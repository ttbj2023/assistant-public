"""LLM 工厂 - 业务知识集中层.

Layer 3 工厂, 按 ModelMetadata + provider 路由构造 LLM 实例.
依赖 core 层 (HttpPool / ClientCache) 和 inference.definitions (元数据 / provider 配置).
Embeddings 创建职责已下沉到 src.inference.embeddings.factory, 本模块通过组合方式委托预热.

设计原则:
- 业务知识 (provider 分支 / SDK 选择 / 参数注入) 全部集中在这里
- core 层零业务知识, 只提供基础设施
- 单向依赖: inference → core, 无反向引用
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, cast

from langchain_openai import ChatOpenAI
from pydantic import SecretStr

from src.core.cache import get_client_cache
from src.core.http_pool import get_http_pool
from src.inference.llm.definitions import ModelMetadata, get_provider_config
from src.inference.llm.definitions.model_registry import get_model
from src.inference.shared.provider_validation import (
    format_error_message,
    validate_supported_provider,
)
from src.inference.usage import get_usage_tracking_callback

if TYPE_CHECKING:
    from langchain_core.language_models.chat_models import BaseChatModel

logger = logging.getLogger(__name__)

# Provider SDK 可选导入 (保留原 try/except 链)
try:
    from langchain_deepseek import ChatDeepSeek
except ImportError:
    logger.error("❌ DeepSeek 支持不可用, 请安装: pip install langchain-deepseek")
    ChatDeepSeek = None

try:
    from langchain_anthropic import ChatAnthropic
except ImportError:
    logger.error("❌ Anthropic 兼容支持不可用, 请安装: pip install langchain-anthropic")
    ChatAnthropic = None

try:
    from .chat_ollama_enhanced import ChatOllamaEnhanced as ChatOllama
except ImportError:
    try:
        from langchain_ollama import ChatOllama as ChatOllamaFallback

        ChatOllama = ChatOllamaFallback
    except ImportError:
        logger.error("❌ Ollama 支持不可用, 请安装: pip install langchain-ollama")
        ChatOllama = None

# 配置常量
SUPPORTED_LLM_PROVIDERS = [
    "local",
    "deepseek",
    "openai",
    "gemini",
    "dashscope",
    "doubao",
    "minimax",
    "ark-agent-plan",
    "aliyun-token-plan",
    "scnet",
]

LONG_HTTP_TIMEOUT = 120.0

DEFAULT_LLM_TIMEOUT = LONG_HTTP_TIMEOUT


class LlmFactory:
    """LLM 实例工厂."""

    def __init__(self) -> None:
        self._http_pool = get_http_pool()
        self._cache = get_client_cache()

    def get_llm(
        self,
        model_id: str,
        *,
        agent_config: dict | None = None,
    ) -> BaseChatModel:
        """缓存优先的 LLM 实例获取.

        Args:
            model_id: 模型 ID, 格式为 "provider:model_name"
            agent_config: 可选配置, 当前仅识别 streaming 字段 (其他参数由调用方通过 bind() 应用)

        Returns:
            BaseChatModel 实例

        """
        cached = self._cache.get_llm_client(model_id, agent_config)
        if cached:
            logger.debug("♻️  复用 LLM 客户端: %s", model_id)
            return cast("BaseChatModel", cached)

        logger.info("🔧 创建新的 LLM 客户端: %s (含 Agent 配置)", model_id)
        metadata = get_model(model_id)
        if metadata is None:
            raise ValueError(
                format_error_message("SYSTEM", "模型不存在", model_id),
            )
        if not metadata.is_chat_model():
            raise ValueError(
                format_error_message(
                    "SYSTEM",
                    "模型类型错误",
                    f"{model_id} 不是对话模型",
                ),
            )

        client = self._build_llm(metadata, agent_config)
        self._cache.cache_llm_client(model_id, client, agent_config)
        return client

    @staticmethod
    def _normalize_chatopenai_params(params: dict[str, Any]) -> dict[str, Any]:
        """将 ChatOpenAI 不直接支持的扩展采样参数移入 extra_body.

        langchain_openai.ChatOpenAI 会直接把未知关键字参数透传给 OpenAI 客户端,
        而 OpenAI 客户端在签名层就会拒绝 top_k / repetition_penalty 等非标准字段.
        对于 Qwen / 阿里 Token Plan 等兼容端点, 这些参数需放在 extra_body 中,
        由服务端自行识别.
        """
        normalized = dict(params)
        extra_body = dict(normalized.get("extra_body") or {})
        for key in ("top_k", "repetition_penalty"):
            if key in normalized:
                extra_body[key] = normalized.pop(key)
        if extra_body:
            normalized["extra_body"] = extra_body
        return normalized

    def _build_llm(
        self,
        metadata: ModelMetadata,
        agent_config: dict | None = None,
    ) -> BaseChatModel:
        """构造 LLM 客户端实例 - provider 路由.

        使用 builtin_data 中定义的默认参数构造实例.
        agent_config 中除 streaming 外的允许参数, 按模型元数据白名单覆盖默认构造参数.
        调用方仍可通过 bind() 覆盖生成级参数 (temperature, max_tokens 等).
        """
        provider = metadata.provider
        model_name = metadata.id.split(":", 1)[1]
        params = metadata.get_param_defaults()

        agent_streaming = agent_config.get("streaming") if agent_config else None
        if agent_config:
            allowed_names = metadata.get_allowed_param_names()
            overrides = {
                k: v
                for k, v in agent_config.items()
                if k not in ("streaming", "model") and k in allowed_names
            }
            params.update(overrides)

        # ChatOpenAI 仅原生支持 OpenAI 标准参数; Qwen 等兼容端点的扩展参数
        # (top_k / repetition_penalty) 必须经 extra_body 透传, 否则会触发
        # AsyncCompletions.create() got an unexpected keyword argument 错误.
        if provider in {
            "openai",
            "dashscope",
            "doubao",
            "aliyun-token-plan",
            "ark-agent-plan",
            "scnet",
        }:
            params = self._normalize_chatopenai_params(params)

        effective_timeout = DEFAULT_LLM_TIMEOUT

        validate_supported_provider(provider, SUPPORTED_LLM_PROVIDERS)

        provider_cfg = get_provider_config(provider)
        usage_kwargs = {
            "callbacks": [get_usage_tracking_callback()],
            "metadata": {"model": metadata.id, "provider": provider},
        }
        api_key: str | None = None
        if provider_cfg.requires_auth and provider_cfg.api_key_env:
            try:
                api_key = provider_cfg.require_api_key()
            except RuntimeError as e:
                raise ValueError(
                    format_error_message(
                        provider.upper(),
                        "配置错误",
                        str(e),
                    ),
                ) from e
        base_url = provider_cfg.get_effective_base_url()

        if provider == "local":
            if ChatOllama is None:
                raise ImportError(
                    "langchain-ollama 未安装, 请运行: pip install langchain-ollama",
                )

            ollama_base_url = (base_url or "http://localhost:11434/v1").replace(
                "/v1",
                "",
            )

            return ChatOllama(
                base_url=ollama_base_url,
                model=model_name,
                **usage_kwargs,
                **params,
                **({"streaming": True} if agent_streaming else {}),
            )

        if provider == "deepseek":
            if ChatDeepSeek is None:
                raise ImportError(
                    "langchain-deepseek 未安装, 请运行: pip install langchain-deepseek",
                )

            shared_client = self._http_pool.get(provider)

            return ChatDeepSeek(
                model=model_name,
                api_key=SecretStr(api_key),
                base_url=base_url or "https://api.deepseek.com/v1",
                timeout=effective_timeout,
                http_async_client=shared_client,
                **usage_kwargs,
                **params,
                **({"streaming": True} if agent_streaming else {}),
            )

        if provider == "openai":
            shared_client = self._http_pool.get(provider)

            return ChatOpenAI(
                base_url=base_url or "https://api3.wlai.vip/v1",
                model=model_name,
                api_key=SecretStr(api_key),
                timeout=effective_timeout,
                http_async_client=shared_client,
                **usage_kwargs,
                **params,
                **({"streaming": True} if agent_streaming else {}),
            )

        if provider == "gemini":
            try:
                from langchain_google_genai import ChatGoogleGenerativeAI
            except ImportError:
                raise ImportError(
                    "langchain-google-genai 未安装, 请运行: pip install langchain-google-genai",
                ) from None

            return ChatGoogleGenerativeAI(
                model=model_name,
                google_api_key=SecretStr(api_key),
                base_url=base_url,
                timeout=effective_timeout,
                **usage_kwargs,
                **params,
                **({"streaming": True} if agent_streaming else {}),
            )

        if provider == "dashscope":
            shared_client = self._http_pool.get(provider)

            return ChatOpenAI(
                base_url=base_url
                or "https://dashscope.aliyuncs.com/compatible-mode/v1",
                model=model_name,
                api_key=SecretStr(api_key),
                timeout=effective_timeout,
                http_async_client=shared_client,
                **usage_kwargs,
                **params,
                **({"streaming": True} if agent_streaming else {}),
            )

        if provider == "doubao":
            shared_client = self._http_pool.get(provider)

            return ChatOpenAI(
                base_url=base_url or "https://ark.cn-beijing.volces.com/api/v3",
                model=model_name,
                api_key=SecretStr(api_key),
                timeout=effective_timeout,
                http_async_client=shared_client,
                **usage_kwargs,
                **params,
                **({"streaming": True} if agent_streaming else {}),
            )

        if provider == "minimax":
            if ChatAnthropic is None:
                raise ImportError(
                    "langchain-anthropic 未安装, 请运行: pip install langchain-anthropic",
                )

            return ChatAnthropic(
                model=model_name,
                anthropic_api_url=base_url or "https://api.minimaxi.com/anthropic",
                anthropic_api_key=SecretStr(api_key),
                default_request_timeout=effective_timeout,
                **usage_kwargs,
                **params,
                **({"streaming": True} if agent_streaming else {}),
            )

        if provider == "ark-agent-plan":
            shared_client = self._http_pool.get(provider)

            return ChatOpenAI(
                base_url=base_url or "https://ark.cn-beijing.volces.com/api/plan/v3",
                model=model_name,
                api_key=SecretStr(api_key),
                timeout=effective_timeout,
                http_async_client=shared_client,
                **usage_kwargs,
                **params,
                **({"streaming": True} if agent_streaming else {}),
            )

        if provider == "aliyun-token-plan":
            shared_client = self._http_pool.get(provider)

            return ChatOpenAI(
                base_url=base_url
                or "https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
                model=model_name,
                api_key=SecretStr(api_key),
                timeout=effective_timeout,
                http_async_client=shared_client,
                **usage_kwargs,
                **params,
                **({"streaming": True} if agent_streaming else {}),
            )

        if provider == "scnet":
            shared_client = self._http_pool.get(provider)

            return ChatOpenAI(
                base_url=base_url or "https://api.scnet.cn/api/llm/v1",
                model=model_name,
                api_key=SecretStr(api_key),
                timeout=effective_timeout,
                http_async_client=shared_client,
                **usage_kwargs,
                **params,
                **({"streaming": True} if agent_streaming else {}),
            )

        raise ValueError(
            format_error_message(
                provider.upper(),
                "不支持",
                f"不支持的 LLM provider: {provider}",
            ),
        )

    def stats(self) -> dict[str, Any]:
        """获取工厂统计信息 (主要供 fastapi_app lifespan 输出)."""
        cache_stats = self._cache.get_stats()
        client_counts = self._cache.get_client_count()

        return {
            "total_clients": client_counts["total_clients"],
            "llm_clients": client_counts["llm_clients"],
            "embedding_clients": client_counts["embedding_clients"],
            "llm_hit_rate": cache_stats["llm_clients"]["hit_rate"],
            "embedding_hit_rate": cache_stats["embedding_clients"]["hit_rate"],
            "total_hit_rate": cache_stats["total_hit_rate"],
        }

    def clear_cache(self) -> None:
        """清空客户端缓存 (主要用于测试)."""
        self._cache.clear_all_clients()
        logger.info("🧹 客户端缓存已清空")
