"""Gemini URL Context 页面理解模块.

URL Context 本质是带检索和 citation 的轻量页面理解器, 不作为普通 fetch
替代品. 本模块只返回有可靠 URL citation 的结果; 没有 citation 的模型回答
会被标记为未验证, 避免把模型先验误当作网页抓取结果.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from ipaddress import ip_address
from typing import Any
from urllib.parse import urlparse

from src.inference.llm.definitions import get_provider_config
from src.inference.llm.definitions.provider_registry import require_api_key_env
from src.tools.shared.cache import ExpertCache, get_expert_cache

logger = logging.getLogger(__name__)

_URL_RE = re.compile(r"https?://[^\s<>\]})\"'，。；！？、]+", re.IGNORECASE)  # noqa: RUF001
_TRAILING_URL_CHARS = ".,;:!?)]}'\"，。；：！？、"  # noqa: RUF001
_TUNNEL_DOMAINS = frozenset({
    "ngrok.io",
    "ngrok-free.app",
    "pinggy.io",
    "loca.lt",
    "localhost.run",
})
_YOUTUBE_DOMAINS = frozenset({
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "youtu.be",
})
_GOOGLE_WORKSPACE_DOMAINS = frozenset({
    "docs.google.com",
    "drive.google.com",
    "sheets.google.com",
})
_UNSUPPORTED_EXTENSIONS = frozenset({
    ".mp3",
    ".mp4",
    ".m4a",
    ".mov",
    ".avi",
    ".mkv",
    ".webm",
    ".wav",
    ".flac",
})


def extract_supported_urls(text: str, *, max_urls: int = 4) -> list[str]:
    """从文本中提取 URL, 过滤 URL Context 不支持的地址."""
    urls: list[str] = []
    seen: set[str] = set()
    for match in _URL_RE.finditer(text):
        url = match.group(0).rstrip(_TRAILING_URL_CHARS)
        if not _is_supported_url(url):
            continue
        if url in seen:
            continue
        seen.add(url)
        urls.append(url)
        if len(urls) >= max_urls:
            break
    return urls


async def gemini_url_context(
    query: str,
    urls: list[str],
    *,
    language: str = "zh",
    timeout: float = 20.0,
    model_id: str = "gemini:gemini-3.1-flash-lite-preview",
) -> dict[str, Any]:
    """调用 Gemini URL Context 并返回标准化结果."""
    selected_urls = _dedupe_supported_urls(urls)
    if not selected_urls:
        return _error_result("no_supported_url", "没有可供 URL Context 读取的 URL")

    cache = get_expert_cache()
    cache_key = ExpertCache.make_key(
        "url_context",
        query=query,
        urls=selected_urls,
        lang=language,
        model=model_id,
    )
    cached = await cache.get_fetch(cache_key)
    if cached is not None:
        return json.loads(cached)

    try:
        interaction = await asyncio.to_thread(
            _execute_interaction,
            query,
            selected_urls,
            language=language,
            timeout=timeout,
            model_id=model_id,
        )
        result = _parse_url_context_response(interaction)
    except Exception as e:
        logger.warning("Gemini URL Context 失败(降级): %s", e)
        result = _error_result("url_context_failed", str(e))

    result["requested_urls"] = selected_urls
    result["source"] = "url_context"

    if result.get("verified"):
        await cache.set_fetch(cache_key, json.dumps(result, ensure_ascii=False))
    return result


def _execute_interaction(
    query: str,
    urls: list[str],
    *,
    language: str,
    timeout: float,
    model_id: str,
) -> Any:
    """执行同步 SDK 调用, 由上层放到线程中运行."""
    from google import genai
    from google.genai import types

    provider_config = get_provider_config("gemini")
    api_key = require_api_key_env(
        provider_config.api_key_env, purpose="Gemini URL Context"
    )

    base_url = provider_config.get_effective_base_url()
    http_options = None
    if base_url:
        http_options = types.HttpOptions(baseUrl=base_url)

    client = genai.Client(api_key=api_key, http_options=http_options)
    return client.interactions.create(
        api_version="v1beta",
        model=_strip_provider_prefix(model_id),
        input=_build_prompt(query, urls, language),
        tools=[{"type": "url_context"}],
        timeout=timeout,
    )


def _parse_url_context_response(interaction: Any) -> dict[str, Any]:
    """解析 Interactions API 响应, 只把带 URL citation 的文本标记为 verified."""
    text_parts: list[str] = []
    citations: list[dict[str, Any]] = []
    retrievals: list[dict[str, str]] = []

    for step in _get_value(interaction, "steps", []) or []:
        step_type = _get_value(step, "type", "")
        if step_type == "model_output":
            for block in _get_value(step, "content", []) or []:
                if _get_value(block, "type", "") != "text":
                    continue
                text = _get_value(block, "text", "")
                if text:
                    text_parts.append(str(text))
                citations.extend(_extract_url_citations(block))
        elif step_type == "url_context_result":
            retrievals.extend(_extract_retrievals(step))

    answer = "\n".join(part for part in text_parts if part).strip()
    verified = bool(citations) and _has_successful_retrieval(retrievals)
    result = {
        "answer": answer,
        "sources": citations,
        "retrievals": retrievals,
        "citation_count": len(citations),
        "verified": verified,
        "source": "url_context",
    }
    if not verified:
        result["error"] = _unverified_reason(citations, retrievals)
    return result


def _extract_url_citations(block: Any) -> list[dict[str, Any]]:
    citations: list[dict[str, Any]] = []
    seen: set[tuple[str, int | None, int | None]] = set()
    for annotation in _get_value(block, "annotations", []) or []:
        if _get_value(annotation, "type", "") != "url_citation":
            continue
        url = _get_value(annotation, "url", "")
        if not url:
            continue
        start_index = _get_value(annotation, "start_index", None)
        end_index = _get_value(annotation, "end_index", None)
        key = (url, start_index, end_index)
        if key in seen:
            continue
        seen.add(key)
        citations.append({
            "title": _get_value(annotation, "title", "") or url,
            "url": url,
            "start_index": start_index,
            "end_index": end_index,
        })
    return citations


def _extract_retrievals(step: Any) -> list[dict[str, str]]:
    retrievals: list[dict[str, str]] = []
    for item in _get_value(step, "result", []) or []:
        url = _get_value(item, "url", "")
        status = _normalize_status(_get_value(item, "status", ""))
        if url or status:
            retrievals.append({"url": str(url), "status": status})
    return retrievals


def _build_prompt(query: str, urls: list[str], language: str) -> str:
    lang_hint = "请用中文回答." if language == "zh" else "Please respond in English."
    urls_text = "\n".join(f"- {url}" for url in urls)
    return (
        f"用户问题:\n{query}\n\n"
        f"请只基于以下 URL 的可访问内容回答, 并保留可靠 citation. "
        f"如果某个 URL 无法访问或没有可靠引用, 请明确说明未能验证.\n\n"
        f"URL 列表:\n{urls_text}\n\n{lang_hint}"
    )


def _dedupe_supported_urls(urls: list[str]) -> list[str]:
    selected: list[str] = []
    seen: set[str] = set()
    for raw_url in urls:
        url = raw_url.strip().rstrip(_TRAILING_URL_CHARS)
        if not _is_supported_url(url):
            continue
        if url in seen:
            continue
        seen.add(url)
        selected.append(url)
    return selected


def _is_supported_url(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return False

    host = parsed.hostname.lower()
    if host == "localhost" or host.endswith(".localhost"):
        return False
    if _is_private_host(host):
        return False
    if _matches_domain(host, _TUNNEL_DOMAINS):
        return False
    if _matches_domain(host, _YOUTUBE_DOMAINS):
        return False
    if _matches_domain(host, _GOOGLE_WORKSPACE_DOMAINS):
        return False
    return not parsed.path.lower().endswith(tuple(_UNSUPPORTED_EXTENSIONS))


def _is_private_host(host: str) -> bool:
    try:
        address = ip_address(host)
    except ValueError:
        return False
    return (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
    )


def _matches_domain(host: str, domains: frozenset[str]) -> bool:
    return host in domains or any(host.endswith(f".{domain}") for domain in domains)


def _strip_provider_prefix(model_id: str) -> str:
    if model_id.startswith("gemini:"):
        return model_id.split(":", 1)[1]
    return model_id


def _has_successful_retrieval(retrievals: list[dict[str, str]]) -> bool:
    return any(item.get("status") == "success" for item in retrievals)


def _unverified_reason(
    citations: list[dict[str, Any]],
    retrievals: list[dict[str, str]],
) -> str:
    if not citations:
        return "no_url_citation"
    if not _has_successful_retrieval(retrievals):
        return "url_retrieval_failed"
    return "unverified"


def _normalize_status(status: Any) -> str:
    value = getattr(status, "value", status)
    if value is None:
        return ""
    return str(value).lower()


def _get_value(obj: Any, name: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        if name in obj:
            return obj[name]
        camel_name = _to_camel(name)
        return obj.get(camel_name, default)
    if hasattr(obj, name):
        return getattr(obj, name)
    camel_name = _to_camel(name)
    if hasattr(obj, camel_name):
        return getattr(obj, camel_name)
    return default


def _to_camel(name: str) -> str:
    parts = name.split("_")
    return parts[0] + "".join(part.capitalize() for part in parts[1:])


def _error_result(error: str, message: str) -> dict[str, Any]:
    return {
        "answer": "",
        "sources": [],
        "retrievals": [],
        "citation_count": 0,
        "verified": False,
        "error": error,
        "message": message,
        "source": "url_context",
    }
