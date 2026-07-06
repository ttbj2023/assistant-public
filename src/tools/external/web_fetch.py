"""HTTP网页抓取工具 - 使用httpx+trafilatura提取网页正文."""

from __future__ import annotations

import json
import logging
import re
from html.parser import HTMLParser
from typing import Any, ClassVar, override
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel, ConfigDict, Field

from src.tools.shared.base_external_tool import BaseExternalTool
from src.tools.shared.cache import ExpertCache, get_expert_cache

logger = logging.getLogger(__name__)


class _TrafilaturaLevelFilter(logging.Filter):
    """将 trafilatura 内部的 ERROR 日志降级为 WARNING.

    trafilatura 在遇到无法解析的 HTML (如 JS 外壳页面,空文档) 时, 会经由其自身
    logger (`trafilatura.utils` / `trafilatura.core`) 以 ERROR 级别记录日志. 这类
    情况属于库的解析能力限制, 既非工具调用错误也非网络错误, 且本工具已优雅降级
    (返回 failed 状态并回退到 `_fallback_extract`), 因此将这些噪音就地降级为
    WARNING, 避免污染错误统计与告警.

    通过修改 `record.levelno`/`levelname` 实现就地降级, 而非丢弃记录, 保留可观测性.
    """

    @override
    def filter(self, record: logging.LogRecord) -> bool:
        if record.levelno >= logging.ERROR:
            record.levelno = logging.WARNING
            record.levelname = logging.getLevelName(logging.WARNING)
        return True


# trafilatura 内部日志的发起 logger (库内各模块以 `logging.getLogger(__name__)` 命名).
# 必须在发起 logger 本身挂载过滤器: 父 logger (trafilatura) 的 logger 级过滤器不会在
# 日志传播 (propagate) 时被子 logger 的记录触发, 只有发起 logger 自身的过滤器始终生效.
_TRAFILATURA_NOISY_LOGGERS: tuple[str, ...] = (
    "trafilatura",
    "trafilatura.utils",
    "trafilatura.core",
)


def _install_trafilatura_noise_filter() -> None:
    """为 trafilatura 内部 logger 安装 ERROR→WARNING 降级过滤器 (幂等)."""
    level_filter = _TrafilaturaLevelFilter()
    for name in _TRAFILATURA_NOISY_LOGGERS:
        noisy_logger = logging.getLogger(name)
        if not any(
            isinstance(existing, _TrafilaturaLevelFilter)
            for existing in noisy_logger.filters
        ):
            noisy_logger.addFilter(level_filter)


# 模块导入时即挂载, 早于 `_fetch_content` 内的惰性 `import trafilatura` 及任何
# extract 调用; getLogger 会按需创建 logger 对象, trafilatura 后续复用同一实例.
_install_trafilatura_noise_filter()

_ANTI_CRAWL_DOMAINS: frozenset[str] = frozenset({
    "mp.weixin.qq.com",
    "zhihu.com",
    "zhuanlan.zhihu.com",
    "www.zhihu.com",
    "blog.csdn.net",
    "www.csdn.net",
    "medium.com",
    "twitter.com",
    "x.com",
    "www.facebook.com",
    "www.instagram.com",
    "www.reddit.com",
    "weibo.com",
    "www.weibo.com",
})

_BROWSER_HEADERS: dict[str, str] = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,image/apng,*/*;q=0.8"
    ),
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Cache-Control": "max-age=0",
}

_CLIENT_ERROR_STATUS: frozenset[int] = frozenset({400, 401, 403, 404, 405, 410, 429})


class WebFetchInput(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"additionalProperties": False},
    )

    url: str = Field(description="要抓取的网页URL")


class WebFetchTool(BaseExternalTool):
    """HTTP网页抓取工具 - 提取网页正文内容."""

    name: str = "fetch_webpage"
    summary: str = "抓取网页全文正文, 返回纯文本内容"
    search_keywords: ClassVar[list[str]] = ["抓取", "网页内容", "读取网页", "URL"]
    description: str = (
        "抓取网页全文内容, 提取正文文本返回. 适合普通网页.\n\n"
        '示例: {"url": "https://example.com/article"}'
    )
    args_schema: type[BaseModel] = WebFetchInput

    timeout: float = 10.0
    max_content_length: int = 50000

    @override
    async def _arun(self, url: str) -> str:
        cache = get_expert_cache()
        cache_key = ExpertCache.make_key("fetch", url=url)

        cached = await cache.get_fetch(cache_key)
        if cached is not None:
            return cached

        skipped = self._check_anti_crawl(url)
        if skipped is not None:
            return skipped

        result = await self._execute(url)

        try:
            parsed = json.loads(result)
            if parsed.get("status") == "success":
                await cache.set_fetch(cache_key, result)
        except (json.JSONDecodeError, KeyError):
            pass

        return result

    @staticmethod
    def _check_anti_crawl(url: str) -> str | None:
        domain = _extract_domain(url)
        if not domain:
            return None
        if domain in _ANTI_CRAWL_DOMAINS or any(
            domain.endswith(f".{d}") for d in _ANTI_CRAWL_DOMAINS
        ):
            logger.info("反爬域名跳过: %s (%s)", domain, url)
            return json.dumps(
                {
                    "url": url,
                    "content": "",
                    "status": "skipped",
                    "error": f"该站点({domain})需要JS渲染或存在反爬机制, 请使用 zhipu_reader 获取",
                    "source": "fetch_webpage",
                },
                ensure_ascii=False,
            )
        return None

    async def _execute(self, url: str) -> str:
        try:
            content = await self._fetch_content(url)
            if content is None:
                return json.dumps(
                    {
                        "url": url,
                        "content": "",
                        "status": "failed",
                        "error": "无法获取网页内容",
                        "source": "fetch_webpage",
                    },
                    ensure_ascii=False,
                )

            word_count = len(content.split())
            if word_count > self.max_content_length:
                content = content[: self.max_content_length]

            return json.dumps(
                {
                    "url": url,
                    "content": content,
                    "status": "success",
                    "word_count": word_count,
                    "source": "fetch_webpage",
                },
                ensure_ascii=False,
            )

        except httpx.TimeoutException as e:
            logger.warning(
                "请求超时(%ss): %s - %s", self.timeout, url, type(e).__name__
            )
            return json.dumps(
                {
                    "url": url,
                    "content": "",
                    "status": "failed",
                    "error": f"请求超时({self.timeout}s)",
                    "source": "fetch_webpage",
                },
                ensure_ascii=False,
            )
        except httpx.HTTPStatusError as e:
            status_code = e.response.status_code
            logger.warning(
                "HTTP错误: %s → %d",
                url,
                status_code,
            )
            return json.dumps(
                {
                    "url": url,
                    "content": "",
                    "status": "failed",
                    "error": f"HTTP {status_code}",
                    "source": "fetch_webpage",
                },
                ensure_ascii=False,
            )
        except httpx.ConnectError as e:
            logger.warning("连接失败: %s - %s", url, e)
            return json.dumps(
                {
                    "url": url,
                    "content": "",
                    "status": "failed",
                    "error": f"连接失败: {e}",
                    "source": "fetch_webpage",
                },
                ensure_ascii=False,
            )
        except Exception as e:
            logger.exception("HTTP抓取意外失败 %s: %s", url, e)
            return json.dumps(
                {
                    "url": url,
                    "content": "",
                    "status": "failed",
                    "error": str(e),
                    "source": "fetch_webpage",
                },
                ensure_ascii=False,
            )

    async def _fetch_content(self, url: str) -> str | None:
        async with httpx.AsyncClient(
            timeout=self.timeout,
            follow_redirects=True,
            headers=_BROWSER_HEADERS,
        ) as client:
            response = await client.get(url)
            if response.status_code in _CLIENT_ERROR_STATUS:
                logger.warning(
                    "客户端错误: %s → %d",
                    url,
                    response.status_code,
                )
                return None
            if response.status_code >= 500:
                logger.warning(
                    "服务器错误: %s → %d",
                    url,
                    response.status_code,
                )
                return None
            response.raise_for_status()

        html = response.text

        try:
            import trafilatura

            extracted = trafilatura.extract(
                html,
                include_comments=False,
                include_tables=False,
                no_fallback=False,
            )
            if extracted:
                return extracted.strip()
        except Exception as e:
            logger.warning("trafilatura提取失败, 降级处理: %s", e)

        return self._fallback_extract(html)

    @staticmethod
    def _fallback_extract(html: str) -> str | None:
        """降级提取: 简单的HTML标签剥离."""

        class TextExtractor(HTMLParser):
            def __init__(self) -> None:
                super().__init__()
                self.texts: list[str] = []
                self._skip = False

            @override
            def handle_starttag(self, tag: str, _attrs: Any) -> None:
                if tag in {
                    "script",
                    "style",
                    "noscript",
                    "nav",
                    "footer",
                    "header",
                }:
                    self._skip = True

            @override
            def handle_endtag(self, tag: str) -> None:
                if tag in {
                    "script",
                    "style",
                    "noscript",
                    "nav",
                    "footer",
                    "header",
                }:
                    self._skip = False

            @override
            def handle_data(self, data: str) -> None:
                if not self._skip:
                    self.texts.append(data)

        try:
            extractor = TextExtractor()
            extractor.feed(html)
            raw = " ".join(extractor.texts)
            raw = re.sub(r"\s+", " ", raw).strip()
            return raw if len(raw) > 50 else None
        except Exception as e:
            logger.debug("HTML文本提取失败: %s", e)
            return None


def _extract_domain(url: str) -> str:
    """从URL中提取主机名用于反爬匹配."""
    try:
        parsed = urlparse(url)
        return parsed.hostname or ""
    except Exception:
        return ""
