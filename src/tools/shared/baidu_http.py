"""百度地图 API 共享 HTTP 客户端 - 提供 QPS 限流和重试封装.

供 geo_research 百度地图客户端 (baidu_maps_client) 和 weather_tool 共享使用,
保证两者使用相同的 BAIDU_MAPS_AK 认证,QPS 限流策略 (默认 2) 和 429/5xx 重试策略.

所有 API 通过环境变量 BAIDU_MAPS_AK 认证.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.map.baidu.com"
_QPS_LIMIT = 2

_semaphore: asyncio.Semaphore | None = None


def _get_retry_params() -> dict[str, Any]:
    """从统一配置获取重试参数."""
    from src.config.retry_config import get_http_retry_params

    return get_http_retry_params()


def _get_semaphore() -> asyncio.Semaphore:
    global _semaphore
    if _semaphore is None:
        _semaphore = asyncio.Semaphore(_QPS_LIMIT)
    return _semaphore


def _get_ak() -> str:
    from src.config.credentials_registry import get_credential

    return get_credential("baidu_maps_ak")


async def baidu_get(
    path: str,
    params: dict[str, Any],
    *,
    timeout: float = 10.0,
) -> dict[str, Any]:
    """百度API通用GET请求, 含QPS限流和简单重试."""
    ak = _get_ak()
    if not ak:
        return {"error": "BAIDU_MAPS_AK 环境变量未设置"}

    params["ak"] = ak
    params.setdefault("output", "json")

    sem = _get_semaphore()
    last_error: Exception | None = None

    retry = _get_retry_params()
    max_retries = retry["max_retries"]
    retryable_status = retry["retryable_status"]
    base_delay = retry["base_delay"]
    rate_limit_delay = retry["rate_limit_delay"]

    for attempt in range(1, max_retries + 1):
        async with sem:
            try:
                async with httpx.AsyncClient(timeout=timeout) as client:
                    resp = await client.get(f"{_BASE_URL}{path}", params=params)
                    resp.raise_for_status()
                    return resp.json()
            except httpx.HTTPStatusError as e:
                last_error = e
                status = e.response.status_code
                if status not in retryable_status or attempt == max_retries:
                    logger.error("百度API失败(%s, status=%s): %s", path, status, e)
                    break
                delay = rate_limit_delay if status == 429 else base_delay * attempt
                logger.warning("百度API重试(%s, status=%s, %ss后)", path, status, delay)
                await asyncio.sleep(delay)
            except Exception as e:
                last_error = e
                logger.error("百度API异常(%s): %s", path, e)
                break

    return {"error": f"百度API调用失败: {last_error!s}"}


def check_response(data: dict[str, Any]) -> dict[str, Any]:
    """检查百度API响应状态."""
    status = data.get("status")
    if status in {0, 200}:
        return data
    return {"error": f"百度API返回错误: status={status}, msg={data.get('message', '')}"}
