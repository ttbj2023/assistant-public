"""轻量 LLM 综合辅助 - 供 grounding fallback 复用.

用非 Gemini 的轻量模型(默认 experts.default_model)基于检索/抓取到的
原始资料综合成最终回答, 复刻 Grounding 的"检索+生成"闭环.
Gemini 故障期 default_model 仍健康, 保证 fallback 链可用.
"""

from __future__ import annotations

import asyncio
import logging

from langchain_core.messages import HumanMessage, SystemMessage

from src.inference.llm.response_utils import content_to_text
from src.tools.experts.model_factory import ExpertModelFactory

logger = logging.getLogger(__name__)


async def synthesize_with_llm(
    system_prompt: str,
    query: str,
    context: str,
    *,
    language: str = "zh",
    timeout: float = 60.0,
) -> str:
    """基于上下文用轻量 LLM 综合回答.

    Args:
        system_prompt: 系统提示词, 控制综合风格
        query: 用户原始查询
        context: 检索/抓取得到的原始资料文本
        language: 回答语言 "zh"/"en"
        timeout: 单次 LLM 调用超时(秒)

    Returns:
        综合后的回答文本.

    Raises:
        Exception: LLM 调用失败或超时, 由上层 fallback 捕获并降级为错误返回.
    """
    from src.config.inference_config import get_config as get_inference_config

    cfg = get_inference_config().experts
    llm = ExpertModelFactory.create(cfg.default_model, **cfg.default_model_params)

    lang_hint = "请用中文回答." if language == "zh" else "Please respond in English."
    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(
            content=f"用户问题:\n{query}\n\n参考资料:\n{context}\n\n{lang_hint}"
        ),
    ]
    response = await asyncio.wait_for(llm.ainvoke(messages), timeout=timeout)
    return content_to_text(response.content)
