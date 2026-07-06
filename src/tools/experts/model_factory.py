"""专家工具模型工厂 - 基于 LlmFactory 创建 LLM 实例并支持 bind() 参数覆盖.

统一走 model_loader 的缓存实例, 通过 bind() 在调用时覆盖参数.
不再为每个 provider 维护独立的创建逻辑.
"""

from __future__ import annotations

from typing import Any, cast

from langchain_core.language_models import BaseChatModel

from src.inference.llm.model_loader import create_llm


class ExpertModelFactory:
    """专家工具模型工厂 - 基于缓存实例 + bind() 参数覆盖."""

    @staticmethod
    def create(model_id: str, **kwargs: Any) -> BaseChatModel:
        """根据 model_id 获取缓存的 LLM 实例, 通过 bind() 覆盖参数.

        Args:
            model_id: 模型 ID, 格式 "provider:model_name"
            **kwargs: SDK 原生参数名, 直接传给 bind() 覆盖默认值

        Returns:
            LLM 客户端实例 (带参数覆盖)

        """
        llm = create_llm(model_id)
        if kwargs:
            return cast("BaseChatModel", llm.bind(**kwargs))
        return llm

    @staticmethod
    def create_for_tool(tool_name: str) -> BaseChatModel:
        """根据工具名从配置中获取模型ID和专属参数, 创建LLM实例.

        Args:
            tool_name: 工具名称, 如 "web_research", "geo_navigator"

        Returns:
            LLM客户端实例(带配置参数覆盖)

        """
        from src.config.inference_config import get_config as get_inference_config

        inference_config = get_inference_config()
        model_id = inference_config.experts.get_model_id(tool_name)
        params = inference_config.experts.get_model_params(tool_name)
        return ExpertModelFactory.create(model_id, **params)
