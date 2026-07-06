"""豆包(Custom版)网络搜索 - 直接调用 feedcoopapi web_search 接口.

提供两种形态:
- async 函数 doubao_web_search(): 供 service.py 等直接调用
- LangChain 工具 DoubaoSearchTool: 供 Agent 自主调用

计费: 走 Agent Plan, 复用 ARK_AGENT_PLAN_API_KEY, 500 次/月免费额度 + AFP 抵扣.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, override

import httpx
from pydantic import BaseModel, ConfigDict, Field

from src.tools.shared.base_external_tool import BaseExternalTool
from src.tools.shared.cache import ExpertCache, get_expert_cache

logger = logging.getLogger(__name__)

_API_BASE = "https://open.feedcoopapi.com/search_api/web_search"
_MAX_CONTENT_LENGTH = 400
_MAX_DISPLAY_RESULTS = 5


def _get_retry_params() -> dict[str, Any]:
    """从统一配置获取重试参数(避免每次调用都读配置)."""
    from src.config.retry_config import get_http_retry_params

    return get_http_retry_params()


def _get_api_key() -> str:
    from src.config.credentials_registry import get_credential

    return get_credential("ark_agent_plan_api_key")


class SearchInput(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"additionalProperties": False},
    )

    query: str = Field(description="搜索查询内容, 不超过100字符")
    count: int = Field(default=5, ge=1, le=50, description="返回结果数量")


class DoubaoSearchTool(BaseExternalTool):
    """豆包网络搜索工具.

    直接调用 feedcoopapi web_search 接口, 走 Agent Plan 计费.
    """

    name: str = "doubao_search"
    summary: str = "豆包网络搜索, 返回搜索结果列表"
    description: str = (
        "使用豆包搜索引擎进行网络搜索, 返回相关网页标题,链接和摘要.\n"
        '示例: {"query": "2025年AI大模型最新进展", "count": 5}'
    )
    args_schema: type[BaseModel] = SearchInput

    timeout: float = 30.0

    @override
    async def is_available(self) -> bool:
        return bool(_get_api_key())

    @override
    async def _arun(self, query: str, count: int = 5) -> str:
        result = await doubao_web_search(query, count=count, timeout=self.timeout)
        if "error" in result:
            return json.dumps(result, ensure_ascii=False)
        return _format_search_results(result)


async def doubao_web_search(
    query: str,
    *,
    count: int = 5,
    timeout: float = 30.0,
) -> dict[str, Any]:
    """调用豆包 web_search API.

    Args:
        query: 搜索查询 (<=100字符, 超长截断)
        count: 返回结果数 (1-50)
        timeout: 请求超时(秒)

    Returns:
        成功: {"search_results": list, ...}
        失败: {"error": str, ...}

    """
    api_key = _get_api_key()
    if not api_key:
        return {"error": "ARK_AGENT_PLAN_API_KEY 未设置"}

    cache = get_expert_cache()
    cache_key = ExpertCache.make_key("doubao_search", query=query, count=count)
    cached = await cache.get_search(cache_key)
    if cached is not None:
        return json.loads(cached)

    result = await _execute_search(api_key, query, count=count, timeout=timeout)

    if "error" not in result:
        await cache.set_search(cache_key, json.dumps(result, ensure_ascii=False))
    return result


async def _execute_search(
    api_key: str,
    query: str,
    *,
    count: int,
    timeout: float,
) -> dict[str, Any]:
    """执行搜索请求, 含重试逻辑."""
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "Query": query[:100],
        "SearchType": "web",
        "Count": count,
        "NeedSummary": True,
        "Filter": {"NeedUrl": True},
    }

    retry = _get_retry_params()
    max_retries = retry["max_retries"]
    retryable_status = retry["retryable_status"]
    base_delay = retry["base_delay"]
    rate_limit_delay = retry["rate_limit_delay"]

    last_error: Exception | None = None
    for attempt in range(1, max_retries + 1):
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(_API_BASE, json=payload, headers=headers)
                resp.raise_for_status()
                data = resp.json()

            # 豆包把业务错误放在 ResponseMetadata.Error
            err = (data.get("ResponseMetadata") or {}).get("Error")
            if err:
                msg = err.get("Message") or json.dumps(err, ensure_ascii=False)
                logger.error("豆包搜索业务错误: %s", msg)
                return {"error": f"豆包搜索错误: {msg}"}

            results = []
            for item in (data.get("Result") or {}).get("WebResults") or []:
                results.append({
                    "title": item.get("Title", ""),
                    "link": item.get("Url", ""),
                    "content": item.get("Summary") or item.get("Snippet") or "",
                    "publish_date": (item.get("PublishTime") or "")[:10],
                    "refer": item.get("SiteName", ""),
                    "auth_info": item.get("AuthInfoDes", ""),
                })

            return {"search_results": results}

        except httpx.HTTPStatusError as e:
            last_error = e
            status = e.response.status_code
            if status not in retryable_status or attempt == max_retries:
                logger.error(
                    "豆包搜索失败(%s/%s, status=%s): %s",
                    attempt,
                    max_retries,
                    status,
                    e,
                )
                break
            delay = rate_limit_delay if status == 429 else base_delay * attempt
            logger.warning(
                "豆包搜索可重试错误(status=%s), %ss后重试(%s/%s)",
                status,
                delay,
                attempt,
                max_retries,
            )
            await asyncio.sleep(delay)

        except Exception as e:
            last_error = e
            logger.error("豆包搜索请求异常: %s", e)
            break

    return {"error": f"搜索失败: {last_error!s}"}


def _format_search_results(result: dict[str, Any]) -> str:
    """格式化搜索结果为 LLM 友好的文本."""
    results = result.get("search_results", [])
    if not results:
        return "搜索完成, 但未找到相关结果."

    lines = [f"找到 {len(results)} 个相关结果", ""]

    for i, item in enumerate(results[:_MAX_DISPLAY_RESULTS], 1):
        title = item.get("title", "无标题")
        link = item.get("link", "")
        content = item.get("content", "")
        date = item.get("publish_date", "")
        auth = item.get("auth_info", "")

        lines.append(f"{i}. {title}")
        if link:
            lines.append(f"   链接: {link}")
        if date:
            lines.append(f"   日期: {date}")
        if auth:
            lines.append(f"   权威度: {auth}")
        if content:
            preview = (
                content[:_MAX_CONTENT_LENGTH] + "..."
                if len(content) > _MAX_CONTENT_LENGTH
                else content
            )
            lines.append(f"   摘要: {preview}")

    if len(results) > _MAX_DISPLAY_RESULTS:
        remaining = len(results) - _MAX_DISPLAY_RESULTS
        lines.append(f"\n... 还有 {remaining} 个结果未显示")

    return "\n".join(lines)


__all__ = ["DoubaoSearchTool", "doubao_web_search"]
