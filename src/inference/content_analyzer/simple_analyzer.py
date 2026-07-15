"""简化内容分析器 - 通用结构化分析服务.

这个模块实现了一个极简的内容分析器,硬编码了提示词模板,
支持对话索引生成(conversation_index).

设计理念:
- 极简化架构,移除不必要的抽象层
- 硬编码提示词,避免YAML配置复杂性
- 统一LLM调用流程,复用现有inference模块
- 直接报错处理,移除复杂回退机制

架构位置:
- 位于src/inference/content_analyzer/,作为推理模块
- 可被memory等多个模块复用
- 遵循项目统一配置系统
"""

from __future__ import annotations

import json
import logging
from typing import Any

from src.core.types import (
    ConversationIndexResult,
)
from src.inference.llm.model_loader import invoke_with_fallback
from src.inference.llm.response_utils import content_to_text

logger = logging.getLogger(__name__)


class SimpleContentAnalyzer:
    """简化内容分析器.

    统一处理所有类型的内容分析任务,通过硬编码的提示词模板
    和JSON Schema实现结构化输出.

    架构说明:
    - 属于inference模块,提供通用结构化分析能力
    - 可被memory,agent等多个模块复用
    - 遵循项目统一配置系统
    """

    # 硬编码的提示词模板(优化长度适配端点限制)
    CONVERSATION_INDEX_PROMPT = """分析对话生成JSON索引.语言必须与用户输入一致.

对话:
用户:{user_message}
助手:{assistant_response}

要求:
- summary: 一句话摘要,不超过40字. 直接陈述对话讨论的实质内容, 不用"用户说/助手问/讨论了"等对话叙述句式. 如: 说"南京天气及穿衣建议", 不说"用户询问天气".
- topic: 2-4个关键词,逗号分隔

返回JSON:{{"summary":"摘要","topic":"主题关键词"}}"""

    def __init__(self, config_override: dict[str, Any] | None = None) -> None:
        """初始化内容分析器.

        Args:
            config_override: 可选的配置覆盖,用于测试或特殊场景

        """
        from src.config.inference_config import get_config as get_inference_config

        inference_config = get_inference_config()

        self.config: dict[str, Any] = {
            "model_id": inference_config.content_analyzer.model,
            "model_params": inference_config.content_analyzer.model_params,
            "fallback_model_params": inference_config.content_analyzer.fallback_model_params,
        }

        if config_override:
            self.config.update(config_override)

        self.model_id = self.config.get(
            "model_id", "ark-agent-plan:doubao-seed-2.0-mini"
        )
        self.model_params: dict[str, Any] = self.config.get("model_params", {})
        self.fallback_model_params: dict[str, Any] | None = self.config.get(
            "fallback_model_params",
        )

        self.enable_conversation_index = self.config.get(
            "enable_conversation_index",
            True,
        )
        self.enable_pinned_memory_update = self.config.get(
            "enable_pinned_memory_update",
            True,
        )

        logger.info(
            f"📊 初始化SimpleContentAnalyzer,模型: {self.model_id}",
        )

    async def _invoke(
        self,
        prompt: str | list,
        model_id: str | None = None,
        model_params: dict[str, Any] | None = None,
        fallback_params: dict[str, Any] | None = None,
    ) -> Any:
        """调用LLM并返回标准化响应.

        Args:
            prompt: 提示词 (str 或 HumanMessage list)
            model_id: 指定模型ID, None 使用主模型
            model_params: 指定模型专属参数, None 使用主模型参数
            fallback_params: fallback bind 参数覆盖(None=用全局默认)

        Returns:
            LLM响应

        """
        target_model = model_id or self.model_id
        params = model_params or self.model_params
        from src.inference.usage import usage_source

        with usage_source("memory_analyzer"):
            response = await invoke_with_fallback(
                prompt,
                target_model,
                params,
                fallback_kind="text",
                fallback_params=fallback_params,
                usage_tag="memory_analyzer",
            )
        return self._normalize_response(response)

    @staticmethod
    def _normalize_response(response: Any) -> Any:
        """将多模态list响应标准化为纯文本content.

        gemini 系模型(含 Gemma thinking 块,gemini-3 text 块)经原生 SDK
        返回 content 为 list 格式, 需要提取文本部分以保证下游 JSON 解析兼容.
        """
        response.content = content_to_text(response.content)
        return response

    def _extract_json_from_response(
        self,
        content: str,
        _schema_type: str,
    ) -> dict[str, Any]:
        """从响应中提取JSON数据,支持多种格式.

        Args:
            content: LLM响应内容
            schema_type: Schema类型

        Returns:
            解析后的JSON数据

        """
        try:
            return json.loads(content.strip())
        except json.JSONDecodeError:
            try:
                start = content.find("{")
                end = content.rfind("}") + 1

                if start >= 0 and end > start:
                    json_str = content[start:end]
                    return json.loads(json_str)

                raise ValueError("响应中未找到有效JSON")
            except (json.JSONDecodeError, ValueError) as e:
                logger.debug("JSON解析失败: %s, 原始内容: %s", e, content[:200])
                raise ValueError(f"JSON解析失败: {e}") from e

    def _validate_result(self, data: dict[str, Any], schema_type: str) -> Any:
        """验证并转换结果为对应的Pydantic模型.

        Args:
            data: 解析后的JSON数据
            schema_type: Schema类型

        Returns:
            验证后的Pydantic模型实例

        """
        if schema_type == "conversation_index":
            return ConversationIndexResult.model_validate(data)

        raise ValueError(f"不支持的Schema类型: {schema_type}")

    async def analyze_conversation_index(
        self,
        user_message: str,
        assistant_response: str,
    ) -> ConversationIndexResult:
        """分析对话生成索引.

        Args:
            user_message: 用户消息
            assistant_response: 助手回复

        Returns:
            对话索引结果

        """
        if not self.enable_conversation_index:
            logger.warning("对话索引分析功能已禁用")
            raise RuntimeError("对话索引分析功能已禁用")

        logger.info(f"📊 开始分析对话索引 - 用户消息长度: {len(user_message)}")

        # 构建提示词
        prompt = self.CONVERSATION_INDEX_PROMPT.format(
            user_message=user_message,
            assistant_response=assistant_response,
        )

        try:
            response = await self._invoke(prompt)

            logger.debug(
                "LLM原始响应: 长度=%d, 前200字符: %s",
                len(response.content),
                response.content[:200],
            )

            # 解析响应
            json_data = self._extract_json_from_response(
                response.content,
                "conversation_index",
            )

            logger.debug("解析后的JSON数据: %s", json_data)

            result = self._validate_result(json_data, "conversation_index")

            logger.info(f"✅ 对话索引生成完成 - 主题: {result.topic}")
            return result

        except Exception as e:
            logger.error("❌ 对话索引生成失败: %s", e)
            raise RuntimeError(f"对话索引生成失败: {e}") from e


_analyzer_instance: SimpleContentAnalyzer | None = None


def get_content_analyzer(
    config_override: dict[str, Any] | None = None,
) -> SimpleContentAnalyzer:
    """获取内容分析器实例(单例模式)."""
    global _analyzer_instance
    if _analyzer_instance is None or config_override is not None:
        _analyzer_instance = SimpleContentAnalyzer(config_override)
    return _analyzer_instance


def clear_analyzer_cache() -> None:
    """清空分析器缓存."""
    global _analyzer_instance
    _analyzer_instance = None


__all__ = ["SimpleContentAnalyzer", "clear_analyzer_cache", "get_content_analyzer"]
