"""Quote Service - A股实时行情查询服务.

独立常驻进程, 封装 pytdx 行情连接 (best-IP 测速 + heartbeat 保活 + 故障转移),
对外提供单只/批量行情查询. 纯查询无状态, 不含业务逻辑 (价格监控的规则存储 /
轮询 / 派发均在 app 侧, 经 NotificationService 发送).

端点:
- GET  /quote?market&code              单只实时行情 (供 query_stock_price 工具)
- POST /quotes                         批量实时行情 (供 app 价格监控轮询引擎)
        body: {"items": [{"market": 1, "code": "600519"}, ...]}
- GET  /health                         健康检查 (TDX 连接统计)
"""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from pytdx_client import TdxClient
from quote_format import normalize_quote
from trading_hours import is_trading_hours

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("quote-service")

_DEFAULT_PORT = 8767


# ───────────────────── 全局组件 (lifespan 管理) ─────────────────────

_tdx: TdxClient | None = None


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """启动: 建立行情连接; 关闭: 释放连接."""
    global _tdx

    _tdx = TdxClient()
    connected = await _tdx.start()
    if not connected:
        logger.warning("启动时未能连接 TDX (将依赖运行时故障转移重连)")

    logger.info("Quote Service 服务就绪")
    try:
        yield
    finally:
        if _tdx is not None:
            await _tdx.close()
        logger.info("Quote Service 服务已关闭")


app = FastAPI(title="Assistant Quote Service", version="1.0.0", lifespan=lifespan)


# ───────────────────── 请求模型 ─────────────────────


class QuoteItem(BaseModel):
    """批量行情查询单项."""

    market: int = Field(..., description="市场: 0=深圳 1=上海")
    code: str = Field(..., min_length=1, max_length=10)

    @field_validator("market")
    @classmethod
    def _validate_market(cls, v: int) -> int:
        if v not in (0, 1):
            raise ValueError("market 必须为 0(深圳) 或 1(上海)")
        return v


class QuotesRequest(BaseModel):
    """批量行情查询请求."""

    model_config = ConfigDict(extra="forbid")

    items: list[QuoteItem] = Field(..., min_length=1, max_length=500)


# ───────────────────── 端点 ─────────────────────


@app.get("/health")
async def health() -> dict[str, Any]:
    if _tdx is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "服务未就绪")
    return {
        "status": "ok",
        "tdx": {
            "connected": _tdx.connected,
            **_tdx.stats.__dict__,
        },
    }


@app.get("/quote")
async def get_quote(
    market: int = Query(..., description="市场: 0=深圳 1=上海"),
    code: str = Query(..., min_length=1, max_length=10, description="股票代码"),
) -> dict[str, Any]:
    """查询单只股票实时行情 (供 query_stock_price 工具消费)."""
    if market not in (0, 1):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "market 必须为 0(深圳) 或 1(上海)"
        )
    if _tdx is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "服务未就绪")

    quotes = await _tdx.get_quotes([(market, code)])
    if quotes is None:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "行情服务取价失败")

    raw = quotes.get((market, code))
    if not raw:
        return {
            "market": market,
            "code": code,
            "status": "not_found",
            "is_trading": is_trading_hours(),
        }
    return normalize_quote(raw, is_trading=is_trading_hours())


@app.post("/quotes")
async def get_quotes_batch(req: QuotesRequest) -> dict[str, Any]:
    """批量查询实时行情 (供 app 价格监控轮询引擎消费).

    复用底层 TdxClient.get_quotes 批量能力 (pytdx get_security_quotes 一次拉多只),
    保留跨规则/跨用户的取价去重优化 (app 侧去重 codes 后一次调用本端点).
    """
    if _tdx is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "服务未就绪")

    codes = [(item.market, item.code) for item in req.items]
    quotes = await _tdx.get_quotes(codes)
    if quotes is None:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "行情服务取价失败")

    trading = is_trading_hours()
    result = [normalize_quote(raw, is_trading=trading) for raw in quotes.values()]
    return {"quotes": result, "is_trading": trading}


if __name__ == "__main__":
    uvicorn.run(
        "app:app",
        host="0.0.0.0",
        port=int(os.getenv("QUOTE_SERVICE_PORT", _DEFAULT_PORT)),
        proxy_headers=True,
        no_server_headers=True,
        log_level=os.getenv("LOG_LEVEL", "info").lower(),
    )
