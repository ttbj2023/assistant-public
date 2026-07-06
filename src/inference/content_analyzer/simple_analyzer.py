"""简化内容分析器 - 通用结构化分析服务.

这个模块实现了一个极简的内容分析器,硬编码了提示词模板,
支持两种分析类型:
1. conversation_index - 对话索引生成(基于历史最佳实践)
2. pinned_memory_update - 置顶记忆更新判断(2字段简化存储)

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
    PinnedMemoryUpdateResult,
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
- summary: 一句话摘要,不超过40字,只描述对话核心内容
- topic: 2-4个关键词,逗号分隔

返回JSON:{{"summary":"摘要","topic":"主题关键词"}}"""

    PINNED_MEMORY_UPDATE_PROMPT = """你是置顶记忆维护助手. 置顶记忆只保存"用户是谁"的稳定信息(身份事实 + 口味偏好), 让助手长期了解这个人. 严禁记录"用户在做什么"——当前项目,公司架构,团队动态,正在用的技术栈,正在考虑的选型, 这些会随项目/工作变化, 属过渡状态, 不归置顶记忆.

注意: 用户对助手"该如何响应/运作的要求"(如"回复简洁""用英文回复")不归置顶记忆, 由 requirement_memory 工具处理, 不要记录.

## 准入判据(同时满足才记)
1. 陈述可得: 用户明确说出口, 非从行为推断
2. 是"谁"不是"做什么": 描述用户的稳定属性/口味, 不是当前工作/项目/团队/基础设施
3. 有惯性: 测试——"这条信息一周不联系, 还该默认成立吗?" 必须明显为"是"

## 用户本轮输入
{user_message}

## 当前TODO列表 (动态任务, 不记入置顶记忆)
{todo_list}

## 当前置顶记忆
{memory_block}

## 两个字段
- basic_info: 用户身份事实(姓名/所在地/职业/技能/宠物/联系方式/长期生理事实如过敏)
- preferences: 口味偏好(喜欢什么: 书/游戏/食物/风格/稳定的工具选型)

## 该记 — "用户是谁"
- "我叫张三, 在杭州做产品经理" — 身份
- "养了一只叫Nemo的美短猫" — 宠物(身份)
- "对海鲜过敏" — 长期生理事实
- "喜欢科幻小说, 最爱三体" — 口味
- "主力Mac, 用Cursor开发" / "我用VSCode写Python" — 用户个人的稳定工具选型(身份)

## 不该记 — "用户在做什么" 或 无持久价值 或 属其他工具
- "我们公司是电商平台, 后端20个Go微服务, 用gRPC, 网关Envoy" — 公司/项目架构(换工作即失效)
- "团队最近在推进GitOps, 用ArgoCD做持续部署" — 团队当前动态
- "用OpenTelemetry替换了Jaeger" — 当前迁移动作
- "API网关配置了限流熔断, 接了Prometheus" — 当前基础设施配置
- "监控用Prometheus+Grafana, 考虑引入PagerDuty" — 当前栈+未定选型
- "团队6个后端2个前端1个SRE" — 团队当前构成
- "偏好简洁直接的回复" — 对助手的要求(归 requirement_memory 工具, 不记入置顶)
- "每天早上6点晨跑3公里" — 行为习惯/作息(归用户建模机制, 非置顶记忆)
- "今早把季度报告交了" — 一次性动作
- "打算开始学吉他" — 未确定意向(属TODO)

## 关键区分: 个人 vs 组织
- 该记: 用户"个人"的稳定工具/技能选型 (我用VScode/Mac, 会Go/Python) —— 这是"用户是谁"
- 不记: "公司/团队/项目"当前用的技术栈与架构 (我们/公司用Envoy, 团队接了Prometheus, 后端20个服务) —— 这是"在做什么"

## 判定原则
- 拿不准是否持久时, 倾向拒绝(误记的噪音污染长期记忆; 漏记可由对话历史补回)
- 涉及"公司/项目/团队/正在做的迁移/正在考虑的选型"语境, 默认拒绝
- 注意: "我用X开发/我会X" 属个人技能工具选型, 不属上述拒绝语境, 该记

## 操作
- 新细节 -> add (field + content; 用中文; 记简洁结论, 不裹时间/过程表述)
- 与现有条目语义重叠 -> 不操作
- 现有条目已不适用 -> delete (content须与某行逐字一致)
- 现有条目需更新 -> change (old_content逐字一致; new_content为替换全文)

返回JSON:
{{
    "has_operations": true/false,
    "operations": [
        {{"action": "add", "field": "basic_info", "content": "..."}},
        {{"action": "delete", "field": "preferences", "content": "原文"}},
        {{"action": "change", "field": "preferences", "old_content": "原文", "new_content": "新全文"}}
    ]
}}"""

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
            "pinned_memory_model": inference_config.content_analyzer.pinned_memory_model,
            "pinned_memory_model_params": inference_config.content_analyzer.pinned_memory_model_params,
            "fallback_model_params": inference_config.content_analyzer.fallback_model_params,
        }

        if config_override:
            self.config.update(config_override)

        self.model_id = self.config.get(
            "model_id", "ark-agent-plan:doubao-seed-2.0-mini"
        )
        self.model_params: dict[str, Any] = self.config.get("model_params", {})
        self.pinned_memory_model: str = self.config.get("pinned_memory_model", "")
        self.pinned_memory_model_params: dict[str, Any] = self.config.get(
            "pinned_memory_model_params",
            {},
        )
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
        if schema_type == "pinned_memory_update":
            ops_data = data.get("operations", [])
            if not isinstance(ops_data, list):
                ops_data = []
            valid_fields = {"basic_info", "preferences"}
            operations = []
            for op in ops_data:
                if not isinstance(op, dict):
                    continue
                action = str(op.get("action", "")).strip().lower()
                if action not in {"add", "delete", "change"}:
                    continue
                field = str(op.get("field", "")).strip().lower()
                if field not in valid_fields:
                    continue
                content = str(op.get("content", "")).strip()
                old_content = str(op.get("old_content", "")).strip()
                new_content = str(op.get("new_content", "")).strip()
                if action == "add" and not content:
                    continue
                if action == "delete" and not content:
                    continue
                if action == "change" and (not old_content or not new_content):
                    continue
                operations.append(
                    {
                        "action": action,
                        "field": field,
                        "content": content,
                        "old_content": old_content,
                        "new_content": new_content,
                    },
                )
            return PinnedMemoryUpdateResult(
                has_operations=bool(operations),
                operations=operations,
            )
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

    async def analyze_pinned_memory_update(
        self,
        user_message: str,
        todo_list: str,
        memory_block: str,
    ) -> PinnedMemoryUpdateResult:
        """分析用户消息并输出置顶记忆操作(增删改, 精确字符串匹配).

        Args:
            user_message: 用户消息
            todo_list: 当前TODO列表(已记录的无需再记入置顶记忆)
            memory_block: 已格式化的当前记忆(无编号)

        Returns:
            置顶记忆操作结果

        """
        if not self.enable_pinned_memory_update:
            logger.warning("置顶记忆分析功能已禁用")
            raise RuntimeError("置顶记忆分析功能已禁用")

        logger.info(f"📊 开始分析置顶记忆更新 - 用户消息长度: {len(user_message)}")

        prompt = self.PINNED_MEMORY_UPDATE_PROMPT.format(
            user_message=user_message,
            todo_list=todo_list or "(无)",
            memory_block=memory_block,
        )

        try:
            response = await self._invoke(
                prompt,
                model_id=self.pinned_memory_model or None,
                model_params=self.pinned_memory_model_params or None,
                fallback_params=self.fallback_model_params,
            )

            json_data = self._extract_json_from_response(
                response.content,
                "pinned_memory_update",
            )
            result = self._validate_result(json_data, "pinned_memory_update")

            logger.info(
                f"✅ 置顶记忆分析完成 - 操作数: {len(result.operations)}",
            )
            return result

        except Exception as e:
            logger.error("❌ 置顶记忆分析失败: %s", e)
            logger.warning("⚠️ 置顶记忆分析降级:返回默认结果,不影响主流程")
            return PinnedMemoryUpdateResult()


# 全局实例,用于复用
_analyzer_instance: SimpleContentAnalyzer | None = None


def get_content_analyzer(
    config_override: dict[str, Any] | None = None,
) -> SimpleContentAnalyzer:
    """获取内容分析器实例(单例模式).

    Args:
        config_override: 可选的配置覆盖,用于测试场景

    Returns:
        内容分析器实例

    """
    global _analyzer_instance
    if _analyzer_instance is None or config_override is not None:
        _analyzer_instance = SimpleContentAnalyzer(config_override)
    return _analyzer_instance


def clear_analyzer_cache() -> None:
    """清空分析器缓存."""
    global _analyzer_instance
    _analyzer_instance = None
    logger.info("🧹 内容分析器缓存已清空")


# 导出主要类和函数
__all__ = ["SimpleContentAnalyzer", "clear_analyzer_cache", "get_content_analyzer"]
