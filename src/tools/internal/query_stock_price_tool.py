"""实时行情查询工具 - query_stock_price.

查询A股个股实时行情, 仅查价不创建监控. 行情数据由 quote-service 提供,
经 GET /quote 端点返回标准化字段, 本工具负责格式化为人类可读文案.

市场按代码前缀自动推断: 6 开头=沪市, 0/3 开头=深市.
"""

from __future__ import annotations

import logging
from typing import Any, ClassVar, override

import httpx
from pydantic import BaseModel, ConfigDict, Field

from src.storage.service.price_alert_service import get_quote_service_url
from src.tools.internal.create_price_alert_tool import infer_market
from src.tools.shared.base_internal_tool import BaseInternalTool

logger = logging.getLogger(__name__)

_CONNECT_TIMEOUT = 3.0
_READ_TIMEOUT = 10.0


class QueryStockPriceRequest(BaseModel):
    """实时行情查询请求."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"additionalProperties": False},
    )

    stock_code: str = Field(
        ...,
        min_length=6,
        max_length=6,
        description="6位A股代码, 如 600519(贵州茅台) / 000001(平安银行)",
    )


class QueryStockPriceTool(BaseInternalTool):
    """查询A股个股实时行情."""

    name: str = "query_stock_price"
    summary: str = "查询A股个股实时行情(现价/涨跌幅/开盘最高最低/成交量/五档)"
    search_keywords: ClassVar[list[str]] = [
        "股票",
        "股价",
        "现价",
        "行情",
        "多少钱",
        "涨跌",
        "报价",
        "盘口",
    ]
    description: str = """查询A股个股实时行情, 返回现价/涨跌幅/今开/最高/最低/昨收/成交量额/五档买卖盘.

参数:
- stock_code: 6位A股代码(必填), 如 600519 / 000001

说明:
- 仅查价, 不创建监控; 如需到价提醒请改用 create_price_alert
- 交易时段(工作日 9:30-11:30 / 13:00-15:00)返回实时价
- 非交易时段返回最后收盘快照, 文案会标注已收盘
- 停牌返回明确提示, 不会误报价格
- 沪深市场按代码前缀自动推断(6开头=沪, 0/3开头=深)"""
    args_schema: type[QueryStockPriceRequest] = QueryStockPriceRequest

    @override
    async def is_available(self) -> bool:
        """只读查询工具, 无条件可用 (不受微信/邮件渠道配置限制).

        与 create_price_alert 不同: 查价无需投递渠道, 故始终注册,
        也使未配置渠道的用户能唤醒 stock_watch_group 先查行情.
        """
        return True

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
    ) -> httpx.Response:
        """调用 quote-service 行情端点."""
        timeout = httpx.Timeout(
            _READ_TIMEOUT,
            connect=_CONNECT_TIMEOUT,
            read=_READ_TIMEOUT,
            write=_CONNECT_TIMEOUT,
            pool=_CONNECT_TIMEOUT,
        )
        async with httpx.AsyncClient(
            base_url=get_quote_service_url(), timeout=timeout
        ) as client:
            resp = await client.request(method, path, params=params)
            resp.raise_for_status()
            return resp

    @override
    async def _arun(self, **kwargs: Any) -> str:
        try:
            req = QueryStockPriceRequest(**kwargs)
        except Exception as e:
            return self._format_error(e)

        try:
            market = infer_market(req.stock_code)
        except ValueError as e:
            return self._format_error(e)

        try:
            resp = await self._request(
                "GET",
                "/quote",
                params={"market": market, "code": req.stock_code},
            )
        except Exception as e:
            logger.error("查询实时行情失败: %s", e)
            return f"错误: 查询实时行情失败(行情服务不可达): {e}"

        return format_quote_text(resp.json(), req.stock_code)


# ───────────────────── 文案格式化 (纯函数, 便于单测) ─────────────────────


def format_quote_text(data: dict[str, Any], stock_code: str) -> str:
    """将 /quote 响应格式化为人类可读文案.

    Args:
        data: /quote 端点返回的标准化报价 dict
        stock_code: 请求的股票代码 (data 可能无 code 字段时回退使用)
    """
    status = data.get("status", "ok")
    if status == "halted":
        return f"⏸ {stock_code} 当前停牌或无行情数据."
    if status == "not_found":
        return f"未取到 {stock_code} 的行情数据."

    code = data.get("code") or stock_code
    is_trading = data.get("is_trading", True)
    price = data.get("price")

    lines = [f"📈 {code}"]

    # 现价 + 涨跌幅
    last_close = data.get("last_close")
    if price is not None:
        change_str = ""
        if last_close and last_close > 0:
            change_pct = (price - last_close) / last_close * 100
            sign = "+" if change_pct >= 0 else ""
            change_str = f"  涨跌幅 {sign}{change_pct:.2f}%"
        lines.append(f"现价 {price:.2f}{change_str}")
        if not is_trading:
            lines.append("(已收盘, 显示最后成交快照)")
    else:
        lines.append("现价 未知")

    # OHLC
    ohlc_parts = []
    for label, key in (
        ("今开", "open"),
        ("最高", "high"),
        ("最低", "low"),
        ("昨收", "last_close"),
    ):
        val = data.get(key)
        if val is not None:
            ohlc_parts.append(f"{label} {val:.2f}")
    if ohlc_parts:
        lines.append("  ".join(ohlc_parts))

    # 成交量 / 成交额
    flow_parts = []
    vol = data.get("vol")
    if vol is not None:
        flow_parts.append(f"成交量 {_format_volume(vol)}")
    amount = data.get("amount")
    if amount is not None:
        flow_parts.append(f"成交额 {_format_amount(amount)}")
    if flow_parts:
        lines.append("  ".join(flow_parts))

    # 五档买卖盘
    bid_prices = [p for p in (data.get("bid_prices") or []) if p is not None]
    ask_prices = [p for p in (data.get("ask_prices") or []) if p is not None]
    if bid_prices:
        lines.append("买盘 " + "/".join(f"{p:.2f}" for p in bid_prices))
    if ask_prices:
        lines.append("卖盘 " + "/".join(f"{p:.2f}" for p in ask_prices))

    return "\n".join(lines)


def _format_volume(vol: float) -> str:
    """成交量格式化 (pytdx vol 单位为手, 按实测可调整)."""
    if vol >= 1e4:
        return f"{vol / 1e4:.2f}万手"
    return f"{vol:.0f}手"


def _format_amount(amount: float) -> str:
    """成交额格式化 (pytdx amount 单位为元)."""
    if amount >= 1e8:
        return f"{amount / 1e8:.2f}亿"
    if amount >= 1e4:
        return f"{amount / 1e4:.2f}万"
    return f"{amount:.0f}"


__all__ = ["QueryStockPriceTool", "format_quote_text"]
