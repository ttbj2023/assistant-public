"""LLM 统一对外入口 - 唯一公共 API.

所有调用方都应该从本模块导入, 不直接依赖 LlmFactory / HttpPool / Cache.
Embeddings 创建入口在 src.inference.embeddings.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import Any, Literal

from langchain_core.language_models.chat_models import BaseChatModel

from .llm_factory import LlmFactory

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _factory() -> LlmFactory:
    return LlmFactory()


def create_llm(
    model_id: str,
    *,
    streaming: bool = False,
    **kwargs: Any,
) -> BaseChatModel:
    """创建或复用 LLM 客户端.

    Args:
        model_id: 模型 ID, 格式为 "provider:model_name"
        streaming: 是否启用流式输出
        **kwargs: 额外构造级参数(如 num_ctx), 按模型元数据白名单覆盖默认值

    Returns:
        BaseChatModel 实例

    """
    agent_config: dict[str, Any] = {"streaming": streaming}
    agent_config.update(kwargs)
    return _factory().get_llm(model_id, agent_config=agent_config)


async def invoke_with_fallback(
    prompt: Any,
    primary_model: str,
    primary_params: dict[str, Any] | None = None,
    *,
    fallback_kind: Literal["text", "vision"] = "text",
    fallback_model: str | None = None,
    fallback_params: dict[str, Any] | None = None,
    usage_tag: str = "tool_llm",
    use_json_mode: bool = True,
    primary_json_log_level: int = logging.DEBUG,
    **invoke_kwargs: Any,
) -> Any:
    """主模型调用失败(瞬时错误白名单)时切换到 fallback 模型再调一次.

    机制: 主模型 1 次 → 白名单异常 → fallback 模型 1 次; fallback 也失败则抛出.
    非白名单异常(内容解析失败/BadRequestError/总时长超限/SSE 断流)不触发 fallback.
    json_mode 按各模型独立取(get_json_mode_config), 兼容跨 provider.
    fallback 配置为空字符串时禁用 fallback, 直接抛主异常(安全阀).

    fallback 模型/参数优先级: 显式传入(fallback_model/fallback_params) > 全局配置
    (inference.fallback). 需要按任务定制 fallback 行为时(如判断类任务要求 fallback
    保留思考, 而全局默认关思考), 由调用方传入覆盖.

    Args:
        prompt: ainvoke 入参(str / HumanMessage / list)
        primary_model: 主模型 id
        primary_params: 主模型 bind 参数(可为空)
        fallback_kind: text | vision, 选全局 fallback 块中的对应模型
        fallback_model: 任务级 fallback 模型覆盖(空=用全局 fallback_kind 对应模型)
        fallback_params: 任务级 fallback bind 参数覆盖(None=用全局对应 params;
            传 dict 含空 dict 则按该 dict bind, 不回退全局)
        usage_tag: usage_source 标签(memory_analyzer/tool_llm/health_extraction/...)
        use_json_mode: 是否启用 json_mode(视觉点传 False)
        primary_json_log_level: 主模型 json_mode 元数据缺失时的日志级别
        **invoke_kwargs: 透传给主/fallback 两个模型 ainvoke 的额外参数(如 max_tokens)

    Returns:
        LLM 响应对象

    """
    from src.config.inference_config import get_config as get_inference_config
    from src.inference.llm.json_mode_config import get_json_mode_config
    from src.inference.llm.retry_predicates import is_retryable_llm_exception
    from src.inference.usage import usage_source

    async def _ainvoke(
        model_id: str,
        bind_params: dict[str, Any] | None,
        json_log_level: int,
    ) -> Any:
        llm = create_llm(model_id)
        if bind_params:
            llm = llm.bind(**bind_params)
        kw = dict(invoke_kwargs)
        if use_json_mode:
            kw.update(get_json_mode_config(model_id, log_level=json_log_level))
        with usage_source(usage_tag):
            return await llm.ainvoke(prompt, **kw)

    try:
        return await _ainvoke(primary_model, primary_params, primary_json_log_level)
    except Exception as exc:
        if not is_retryable_llm_exception(exc):
            raise

        inference_config = get_inference_config()
        fallback_cfg = inference_config.fallback
        if fallback_kind == "text":
            global_model = fallback_cfg.text_model
            global_params = fallback_cfg.text_model_params
        else:
            global_model = fallback_cfg.vision_model
            global_params = fallback_cfg.vision_model_params
        # 任务级覆盖优先于全局默认
        resolved_model = fallback_model or global_model
        resolved_params = global_params if fallback_params is None else fallback_params

        if not resolved_model:
            raise

        logger.warning(
            "主模型 %s 调用失败(%s), 切换 fallback(%s=%s)",
            primary_model,
            type(exc).__name__,
            fallback_kind,
            resolved_model,
        )
        return await _ainvoke(resolved_model, resolved_params or None, logging.WARNING)


def get_llm_factory() -> LlmFactory:
    """暴露工厂实例 (供 fastapi_app lifespan 调用 preload/stats)."""
    return _factory()
