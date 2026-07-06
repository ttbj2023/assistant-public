"""Simple 模式长期记忆提取分析器 (Stage 1).

每轮对话后从单次完整交换(用户消息 + 助手回复)中提取跨会话可复用的
偏好与洞察, 输出 add/delete/change 操作. 领域无关的通用 prompt,
适配任意采用 simple 记忆模式的 Agent.

设计要点:
- 单一通用 prompt, 无 per-agent 定制
- 准入判据领域无关(陈述可得/有惯性/跨会话可复用)
- 复用 PinnedMemoryUpdateResult / MemoryOperation 数据结构
- 复用 SimpleContentAnalyzer 的 LLM 调用与 JSON 解析模式
"""

from __future__ import annotations

import json
import logging
from typing import Any

from src.core.types import PinnedMemoryUpdateResult
from src.inference.llm.model_loader import invoke_with_fallback
from src.inference.llm.response_utils import content_to_text

logger = logging.getLogger(__name__)

# simple 模式两字段
_VALID_FIELDS = {"preferences", "insights"}

SIMPLE_MEMORY_UPDATE_PROMPT = """你是长期记忆维护助手. 长期记忆保存两类跨会话可复用的信息, 让助手在未来的对话中更好地服务用户. 严禁记录只对当前任务有意义的一次性内容(那些由对话历史承载).

## 两个字段
- preferences: 用户对该助手领域的稳定偏好/要求(输出风格,排版格式,受众定位,语言,工作方式等)
- insights: 用户在本轮明确表述的可复用经验,方法或自我说明的领域定位

## 准入判据(同时满足才记)
1. 陈述可得或明确认可: 用户明确说出口, 或对助手某做法明确表态认可(非从沉默推断)
2. 有惯性: 测试——"这条信息一周后,换一个项目, 还该默认成立吗?" 必须明显为"是"
3. 跨会话可复用: 对未来的对话有帮助, 不是只对当前这一次任务有意义
4. 单轮可判定: 仅依据本轮对话即可确认, 无需观察其他轮次.
   需要多轮才能确认的模式/倾向, 不属本阶段职责(留给后续周期综合).

## 本轮对话
用户: {user_message}
助手: {assistant_response}

## 当前长期记忆
{memory_block}

## 该记 — 跨会话稳定可复用
- "回复用中文, 重逻辑轻客套" — 偏好(风格)
- "公众号文章用短段落配小标题" — 偏好(格式)
- "我主要写给初级开发者的技术教程" — 偏好(受众)
- "用户认可'问题-原因-行动'三段式结构, 以后多用" — 洞察(可复用框架, 明确认可)
- "以后这类分析都用'问题树'拆解" — 洞察(用户明确指定的方法)

## 不该记 — 一次性/无惯性/需多轮/属对话历史
- "这篇写远程办公" — 当前任务内容(一次性)
- "这次短一点" — 一次性指令(无惯性)
- "第一段改成XXX" — 当前任务的具体编辑(一次性)
- "用户似乎偏好简洁风格" — 从行为推断的倾向(需多轮观察, 留给周期综合, 非本轮职责)
- 拿不准是否稳定时, 倾向拒绝(误记的噪音污染长期记忆; 漏记可由对话历史补回)

## 操作
- 新的可复用信息 -> add (field + content; 用中文; 记简洁结论, 不裹时间/过程表述)
- 与现有条目语义重叠 -> 不操作
- 现有条目已不适用 -> delete (content须与某行逐字一致)
- 现有条目需更新 -> change (old_content逐字一致; new_content为替换全文)

返回JSON:
{{
    "has_operations": true/false,
    "operations": [
        {{"action": "add", "field": "preferences", "content": "..."}},
        {{"action": "delete", "field": "insights", "content": "原文"}},
        {{"action": "change", "field": "preferences", "old_content": "原文", "new_content": "新全文"}}
    ]
}}"""


class SimpleMemoryAnalyzer:
    """Simple 模式长期记忆提取分析器 (Stage 1).

    从单次完整交换提取跨会话可复用的偏好与洞察, 输出 operations.
    """

    def __init__(self, config_override: dict[str, Any] | None = None) -> None:
        """初始化分析器.

        Args:
            config_override: 可选的配置覆盖, 用于测试或特殊场景

        """
        from src.config.inference_config import get_config as get_inference_config

        inference_config = get_inference_config()

        self.config: dict[str, Any] = {
            "model_id": inference_config.content_analyzer.model,
            "model_params": inference_config.content_analyzer.model_params,
            "pinned_memory_model": inference_config.content_analyzer.pinned_memory_model,
            "pinned_memory_model_params": (
                inference_config.content_analyzer.pinned_memory_model_params
            ),
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
            "pinned_memory_model_params", {}
        )
        self.fallback_model_params: dict[str, Any] | None = self.config.get(
            "fallback_model_params",
        )

        logger.info("📊 初始化SimpleMemoryAnalyzer, 模型: %s", self.model_id)

    async def _invoke(
        self,
        prompt: str,
        model_id: str | None = None,
        model_params: dict[str, Any] | None = None,
        fallback_params: dict[str, Any] | None = None,
    ) -> Any:
        """调用LLM并返回标准化响应."""
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
        response.content = content_to_text(response.content)
        return response

    @staticmethod
    def _extract_json_from_response(content: str) -> dict[str, Any]:
        """从响应中提取JSON数据, 支持多种格式."""
        try:
            return json.loads(content.strip())
        except json.JSONDecodeError:
            try:
                start = content.find("{")
                end = content.rfind("}") + 1
                if start >= 0 and end > start:
                    return json.loads(content[start:end])
                raise ValueError("响应中未找到有效JSON")
            except (json.JSONDecodeError, ValueError) as e:
                logger.debug("JSON解析失败: %s, 原始内容: %s", e, content[:200])
                raise ValueError(f"JSON解析失败: {e}") from e

    @staticmethod
    def _validate_result(data: dict[str, Any]) -> PinnedMemoryUpdateResult:
        """验证并转换结果为 PinnedMemoryUpdateResult."""
        ops_data = data.get("operations", [])
        if not isinstance(ops_data, list):
            ops_data = []
        operations = []
        for op in ops_data:
            if not isinstance(op, dict):
                continue
            action = str(op.get("action", "")).strip().lower()
            if action not in {"add", "delete", "change"}:
                continue
            field = str(op.get("field", "")).strip().lower()
            if field not in _VALID_FIELDS:
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
            operations.append({
                "action": action,
                "field": field,
                "content": content,
                "old_content": old_content,
                "new_content": new_content,
            })
        return PinnedMemoryUpdateResult(
            has_operations=bool(operations),
            operations=operations,
        )

    async def analyze_memory_update(
        self,
        user_message: str,
        assistant_response: str,
        memory_block: str,
    ) -> PinnedMemoryUpdateResult:
        """分析单次完整交换, 输出长期记忆操作(增删改, 精确字符串匹配).

        Args:
            user_message: 本轮用户消息
            assistant_response: 本轮助手回复
            memory_block: 已格式化的当前记忆(无编号)

        Returns:
            长期记忆操作结果; 失败时返回空结果(不影响主流程)

        """
        logger.info(
            "📊 开始分析长期记忆更新 - 用户消息长度: %d, 助手回复长度: %d",
            len(user_message),
            len(assistant_response),
        )

        prompt = SIMPLE_MEMORY_UPDATE_PROMPT.format(
            user_message=user_message,
            assistant_response=assistant_response,
            memory_block=memory_block,
        )

        try:
            response = await self._invoke(
                prompt,
                model_id=self.pinned_memory_model or None,
                model_params=self.pinned_memory_model_params or None,
                fallback_params=self.fallback_model_params,
            )
            json_data = self._extract_json_from_response(response.content)
            result = self._validate_result(json_data)
            logger.info("✅ 长期记忆分析完成 - 操作数: %d", len(result.operations))
            return result
        except Exception as e:
            logger.error("❌ 长期记忆分析失败: %s", e)
            logger.warning("⚠️ 长期记忆分析降级: 返回默认结果, 不影响主流程")
            return PinnedMemoryUpdateResult()


__all__ = ["SIMPLE_MEMORY_UPDATE_PROMPT", "SimpleMemoryAnalyzer"]
