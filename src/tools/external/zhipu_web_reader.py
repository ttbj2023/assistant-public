"""智谱AI网页阅读 - 直接调用 /paas/v4/reader API.

提供两种形态:
- async 函数 zhipu_web_reader(): 供 service.py 等直接调用
- LangChain 工具 ZhipuReaderTool: 供 Agent 自主调用

按量计费, 从 API Key 余额扣除.
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

_API_BASE = "https://open.bigmodel.cn/api/paas/v4"
_MAX_CONTENT_LENGTH = 15000


def _get_retry_params() -> dict[str, Any]:
    """从统一配置获取重试参数."""
    from src.config.retry_config import get_http_retry_params

    return get_http_retry_params()


def _get_api_key() -> str:
    from src.config.credentials_registry import get_credential

    return get_credential("zhipu_api_key")


class ReaderInput(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"additionalProperties": False},
    )

    url: str = Field(description="要阅读的网页URL")


class ZhipuReaderTool(BaseExternalTool):
    """智谱AI网页阅读工具.

    直接调用 /paas/v4/reader API, 将网页转为结构化 markdown/text.
    支持 JS 渲染页面, 比 httpx+trafilatura 抓取更完整.
    """

    name: str = "zhipu_reader"
    summary: str = "智谱AI网页阅读器, 支持JS渲染页面, 返回结构化内容"
    description: str = (
        "使用智谱AI网页阅读器获取网页完整内容, 支持JavaScript渲染的页面.\n"
        "返回markdown格式的网页正文.\n"
        '示例: {"url": "https://example.com/article"}'
    )
    args_schema: type[BaseModel] = ReaderInput

    timeout: float = 30.0

    @override
    async def is_available(self) -> bool:
        return bool(_get_api_key())

    @override
    async def _arun(self, url: str) -> str:
        result = await zhipu_web_reader(url, timeout=self.timeout)
        if "error" in result:
            return json.dumps(result, ensure_ascii=False)
        content = result.get("content", "")
        title = result.get("title", "")
        lines = []
        if title:
            lines.append(f"标题: {title}")
        lines.append(content[:_MAX_CONTENT_LENGTH])
        return "\n\n".join(lines)


async def zhipu_web_reader(
    url: str,
    *,
    return_format: str = "markdown",
    timeout: float = 30.0,
) -> dict[str, Any]:
    """调用智谱 Web Reader API.

    Args:
        url: 要抓取的网页 URL
        return_format: 返回格式 (markdown/text)
        timeout: 请求超时(秒)

    Returns:
        成功: {"content": str, "title": str, "description": str, ...}
        失败: {"error": str, ...}

    """
    api_key = _get_api_key()
    if not api_key:
        return {"error": "ZHIPU_API_KEY 未设置"}

    cache = get_expert_cache()
    cache_key = ExpertCache.make_key("zhipu_reader", url=url)
    cached = await cache.get_fetch(cache_key)
    if cached is not None:
        return json.loads(cached)

    result = await _execute_reader(
        api_key, url, return_format=return_format, timeout=timeout
    )

    if "error" not in result:
        await cache.set_fetch(cache_key, json.dumps(result, ensure_ascii=False))
    return result


async def _check_url_reachable(
    url: str, timeout: float = 10.0
) -> tuple[bool, str, bool]:
    """检查目标 URL 是否可直接访问.

    用于在调用智谱 reader 前过滤已失效页面, 避免对确定不存在的 URL
    发起无意义 API 调用. 对 404/410/协议错误等硬失败直接跳过;
    对 403/CF 验证页/超时等软失败仍让智谱 reader 尝试, 因为智谱可能绕过.

    Returns:
        (是否可达, 不可访问原因, 是否硬失败).
        硬失败为 True 时上层应直接返回错误, 不再调用智谱 API.
    """
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8",
    }

    try:
        async with httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=True,
            headers=headers,
        ) as client:
            # 优先 HEAD, 405 时降级 GET
            try:
                resp = await client.head(url)
                if resp.status_code == 405:
                    resp = await client.get(url)
            except httpx.UnsupportedProtocol:
                return (False, "URL 协议不支持", True)

            # Cloudflare 等 CDN 验证页可能返回 403, 按内容识别但不跳过
            text_preview = (resp.text or "").lower()[:2048]
            if (
                "just a moment" in text_preview
                or "checking your browser" in text_preview
            ):
                return (False, "目标页面被 CDN 人机验证拦截", False)

            if resp.status_code in {404, 410}:
                return (False, f"目标页面不存在 ({resp.status_code})", True)
            if resp.status_code == 403:
                return (False, "目标页面禁止访问 (403)", False)
            if resp.status_code >= 400:
                return (False, f"目标页面返回 {resp.status_code}", False)

            return (True, "", False)
    except httpx.TimeoutException:
        return (False, "目标页面访问超时", False)
    except Exception as e:
        return (False, f"目标页面访问异常: {e}", False)


async def _execute_reader(
    api_key: str,
    url: str,
    *,
    return_format: str,
    timeout: float,
) -> dict[str, Any]:
    """执行网页阅读请求, 含重试逻辑."""
    # 先检查目标 URL 可达性, 仅对确定不存在的页面跳过智谱 API 调用
    reachable, reason, is_hard_fail = await _check_url_reachable(url)
    if not reachable and is_hard_fail:
        logger.warning("智谱阅读跳过不可访问页面: %s, 原因: %s", url, reason)
        return {"error": f"网页阅读失败: {reason}"}

    if not reachable:
        logger.info("智谱阅读尝试可能受拦截的页面: %s, 预检状态: %s", url, reason)

    endpoint = f"{_API_BASE}/reader"
    payload: dict[str, Any] = {
        "url": url,
        "return_format": return_format,
        "retain_images": False,
        "no_cache": False,
        "timeout": int(timeout),
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    last_error: Exception | None = None
    retry = _get_retry_params()
    max_retries = retry["max_retries"]
    # 智谱 reader 返回的 500 通常是对上游页面错误(404/过期/无法访问等)的封装,
    # 重试无法解决, 故从可重试状态码中排除.
    retryable_status = retry["retryable_status"] - {500}
    base_delay = retry["base_delay"]
    rate_limit_delay = retry["rate_limit_delay"]

    for attempt in range(1, max_retries + 1):
        try:
            async with httpx.AsyncClient(timeout=timeout + 10) as client:
                resp = await client.post(endpoint, json=payload, headers=headers)
                resp.raise_for_status()
                data = resp.json()

            reader_result = data.get("reader_result") or {}
            content = reader_result.get("content", "")
            metadata = reader_result.get("metadata") or {}

            if isinstance(content, str) and content.startswith("```"):
                content = _extract_from_code_block(content)

            return {
                "content": content[:_MAX_CONTENT_LENGTH],
                "title": metadata.get("og:title", ""),
                "description": metadata.get("description", ""),
                "url": url,
            }

        except httpx.HTTPStatusError as e:
            last_error = e
            status = e.response.status_code
            if status not in retryable_status or attempt == max_retries:
                logger.warning(
                    "智谱阅读失败(%s/%s, status=%s): %s",
                    attempt,
                    max_retries,
                    status,
                    e,
                )
                break
            delay = rate_limit_delay if status == 429 else base_delay * attempt
            logger.warning(
                "智谱阅读可重试错误(status=%s), %ss后重试(%s/%s)",
                status,
                delay,
                attempt,
                max_retries,
            )
            await asyncio.sleep(delay)

        except Exception as e:
            last_error = e
            # 带异常类型名: 部分 httpx 异常 str() 为空, 仅 %s 会丢失诊断信息
            logger.warning("智谱阅读请求异常: %s: %s", type(e).__name__, e)
            break

    return {"error": f"网页阅读失败: {last_error!s}"}


def _extract_from_code_block(content: str) -> str:
    """提取被代码块包裹的内容.

    智谱 Reader 偶尔将内容包裹在 ``` 代码块中.
    """
    lines = content.split("\n")
    start = 0
    end = len(lines)
    for i, line in enumerate(lines):
        if line.strip().startswith("```") and i == 0:
            start = i + 1
        elif line.strip().startswith("```") and i > start:
            end = i
            break
    return "\n".join(lines[start:end]).strip()


__all__ = ["ZhipuReaderTool", "zhipu_web_reader"]
