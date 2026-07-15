"""SimpleMemoryProcessor - Simple 模式记忆处理器.

轻量记忆架构, 适配"前端管历史"的对接方式(如 Open WebUI):
- 对话历史: 前端透传(processor_config["chat_messages"]), 本处理器过滤 system
  消息后转为 history_messages, 直接交给 LLM, 不从后端 DB 重组
- 置顶记忆: 统一单一块, 注入 system_prompt_extension 的 <pinned_memory> 标签
- 轮次记录: 由 SimpleMemoryCore 在对话完成后存储(不进 prompt)

与 LocalMemoryProcessor 的区别:
- 不组装索引区/主历史/TODO, 不做冷启动种子化
- system prompt 后端权威(agent.yaml), 忽略前端 system 消息
- 多模态历史保留(image_url 内容块原样透传)
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, override
from zoneinfo import ZoneInfo

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage

from .base_processor import BaseProcessor, MessageContext

logger = logging.getLogger(__name__)


def _convert_content(content: Any) -> str | list[dict[str, Any]]:
    """将消息 content 转为 LangChain 兼容格式.

    str 原样返回; list(OpenAI 内容块) 转为 LangChain 的 dict 列表,
    保留 text / image_url 块(多模态透传).
    """
    if isinstance(content, str):
        return content

    if not isinstance(content, list):
        return str(content)

    blocks: list[dict[str, Any]] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        btype = block.get("type")
        if btype == "text":
            text = block.get("text", "")
            if text:
                blocks.append({"type": "text", "text": text})
        elif btype == "image_url":
            image_url = block.get("image_url")
            if isinstance(image_url, dict) and image_url.get("url"):
                blocks.append({
                    "type": "image_url",
                    "image_url": {"url": image_url["url"]},
                })
    return blocks


def _convert_chat_messages(
    chat_messages: list[dict[str, Any]] | None,
) -> list[BaseMessage]:
    """将前端透传的消息列表转为 LangChain BaseMessage.

    - 过滤 role="system"(后端权威, system 由 agent.yaml 组装)
    - user -> HumanMessage, assistant -> AIMessage
    - 保留多模态内容块(image_url)
    """
    if not chat_messages:
        return []

    result: list[BaseMessage] = []
    for msg in chat_messages:
        if not isinstance(msg, dict):
            continue
        role = str(msg.get("role", "")).strip().lower()
        if role == "system":
            continue  # 后端权威, 忽略前端 system 消息
        content = _convert_content(msg.get("content", ""))
        if role == "user":
            result.append(HumanMessage(content=content))
        elif role == "assistant":
            result.append(AIMessage(content=content))
    return result


class SimpleMemoryProcessor(BaseProcessor):
    """Simple 模式记忆处理器 - 前端历史透传 + 长期记忆注入."""

    def __init__(self, config: dict[str, Any] | None) -> None:
        super().__init__(config)
        logger.info("SimpleMemoryProcessor 初始化完成")

    @override
    async def initialize(self) -> None:
        logger.info("SimpleMemoryProcessor 异步初始化完成")

    @override
    def get_prompt_hint(self, agent_config: Any = None) -> str:
        """返回 simple 记忆系统的格式描述, 注入系统提示词."""
        return (
            "系统提示词中可能包含 <pinned_memory> 标签, 是跨会话积累的用户偏好"
            "与经验洞察, 在相关时遵循其中的稳定偏好.\n"
            "本轮指令在最后一条 <user_input> 标签中."
        )

    @override
    async def build_messages_context(
        self,
        user_input: str,
        user_id: str,
        thread_id: str,
        agent_id: str | None = None,
        processor_config: dict | None = None,
        timezone: str = "Asia/Shanghai",
    ) -> MessageContext:
        """构建消息上下文: 前端历史透传 + 置顶记忆 extension.

        流程:
        1. 从 processor_config["chat_messages"] 取前端透传历史, 过滤 system, 转 BaseMessage
        2. 读置顶记忆单一块, 格式化为 <pinned_memory> extension
        3. current_content = 时间 + user_input

        Args:
            user_input: 当前轮用户输入
            user_id: 用户ID
            thread_id: 线程ID
            agent_id: Agent ID
            processor_config: 处理器配置(含 chat_messages / agent_config)
            timezone: 时区

        Returns:
            MessageContext: 含 history_messages / current_content / system_prompt_extension

        """
        if not user_id or not thread_id:
            logger.error(
                "缺少 user_id 或 thread_id (agent_id: %s)",
                agent_id,
            )
            raise RuntimeError("记忆组装失败: 缺少必要的 user_id 或 thread_id 参数")

        agent_config = None
        if processor_config is not None:
            agent_config = processor_config.get("agent_config")
        if not agent_config:
            raise ValueError("SimpleMemoryProcessor 需要有效的 agent_config")

        if not agent_id:
            agent_id = getattr(agent_config, "agent_id", None) or getattr(
                agent_config, "id", None
            )
        if not agent_id:
            raise ValueError("agent_id 不能为空")

        # 1. 前端历史透传
        chat_messages = None
        if processor_config is not None:
            chat_messages = processor_config.get("chat_messages")
        history_messages = _convert_chat_messages(chat_messages)

        # 2. 长期记忆 extension (统一单一块, 标签 <pinned_memory>)
        system_prompt_extension = ""
        try:
            from src.storage.service import create_pinned_memory_block_service

            block_service = await create_pinned_memory_block_service(
                user_id,
                thread_id,
                agent_id=agent_id,
            )
            content = await block_service.get_formatted(user_id, thread_id)
            if content and content.strip():
                system_prompt_extension = (
                    "以下是你需要长期记住的关键信息:\n"
                    f"<pinned_memory>\n{content}\n</pinned_memory>"
                )
        except Exception as e:
            logger.warning("读取长期记忆失败(非致命): %s", e)

        # 3. current_content: 时间 + user_input
        now = datetime.now(ZoneInfo(timezone))
        time_str = now.strftime("%Y-%m-%d %H:%M:%S %Z")
        current_content = (
            f"<current_context>\n时间: {time_str}\n</current_context>\n\n"
            f"<user_input>\n{user_input.strip()}\n</user_input>"
        )

        logger.info(
            "simple 上下文组装: history=%d messages, extension=%d chars, current=%d chars",
            len(history_messages),
            len(system_prompt_extension),
            len(current_content),
        )

        return MessageContext(
            history_messages=history_messages,
            current_content=current_content,
            system_prompt_extension=system_prompt_extension,
        )

    async def get_or_create_conversation_memory(
        self,
        user_id: str,
        thread_id: str,
        agent_config: Any = None,
    ) -> Any:
        """获取或创建 Simple 模式对话记忆核心(供 orchestrator 存储用)."""
        try:
            from src.agent.memory.simple_memory import SimpleMemoryCore

            return SimpleMemoryCore(user_id, thread_id, agent_config)
        except Exception as e:
            logger.error("获取 Simple 对话记忆失败: %s", e)
            raise RuntimeError(f"获取 Simple 对话记忆失败: {e}") from e

    @override
    async def get_processor_stats(self) -> dict[str, Any]:
        return {
            "processor_type": "SimpleMemoryProcessor",
            "memory_type": "simple",
            "history_format": "frontend_passthrough",
        }

    @override
    async def cleanup(self) -> None:
        logger.info("清理 SimpleMemoryProcessor 资源...")


__all__ = ["SimpleMemoryProcessor"]
