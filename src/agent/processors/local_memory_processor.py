"""LocalMemoryProcessor - 原生 messages 数组形式的记忆处理器.

历史走原生 messages 数组, 当前轮上下文 (时间/missed_messages/user_input)
走当前 HumanMessage 的 XML 前缀.

LLM 收到的最终结构:
  [SystemMessage(角色 + <pinned_memory>附录)]
  [HumanMessage("[过往对话回顾]")]                          <- 伪对话轮 (索引区)
  [AIMessage("<conversation_index>摘要</conversation_index>")]
  [HumanMessage(轮1原文)] [AIMessage(轮1回复)]             <- 真实历史
  ...
  [HumanMessage(<missed_messages>错过消息</missed_messages>
                <current_context>时间</current_context>
                <user_input>真实输入</user_input>)]        <- 当前轮

首轮对话: 返回空 history + current_content 只含 <first_turn_guidance> + <user_input>.
"""

from __future__ import annotations

import logging
import traceback
from datetime import datetime
from typing import TYPE_CHECKING, Any, override
from zoneinfo import ZoneInfo

from langchain_core.messages import BaseMessage

from src.agent.memory.local_memory.assembler import MemoryAssembler
from src.storage.service import create_conversation_service
from src.storage.service.scheduled_message_service import (
    get_scheduled_message_service,
)

from .base_processor import BaseProcessor, MessageContext

if TYPE_CHECKING:
    from src.config.agent_config import AgentConfig

logger = logging.getLogger(__name__)


class LocalMemoryProcessor(BaseProcessor):
    """本地记忆处理器 - 原生 messages 数组形式."""

    def __init__(self, config: dict[str, Any] | None) -> None:
        """初始化.

        Args:
            config: 应用配置实例

        """
        super().__init__(config)

        from src.core.path_resolver import get_user_path_resolver

        self.path_resolver = get_user_path_resolver()

        logger.info("LocalMemoryProcessor 初始化完成")

    @override
    async def initialize(self) -> None:
        """初始化异步组件."""
        logger.info("LocalMemoryProcessor 异步初始化完成")

    @override
    def get_prompt_hint(self, agent_config: Any = None) -> str:
        """返回 local 记忆系统的格式描述, 注入系统提示词.

        根据 agent_config 条件性组装:
        - 始终: [过往对话回顾] / <conversation_index> / <current_context> / <user_input>
        - 定时消息工具存在: <missed_messages>

        Args:
            agent_config: Agent配置对象

        Returns:
            格式描述文本

        """
        if agent_config is None:
            return ""

        lines = [
            "对话历史中, 标记为 [过往对话回顾] 的轮次, 其AI回复里的 "
            "<conversation_index> 是更早期对话的摘要索引.",
            "",
            "本轮指令在最后一条消息中, 由以下XML标签按需包裹:",
        ]

        # 条件: 定时消息工具存在时注入 <missed_messages> 描述
        all_tools = list(getattr(agent_config, "tools", None) or [])
        all_tools += list(getattr(agent_config, "optional_tools", None) or [])
        has_scheduled = any("scheduled" in t for t in all_tools)
        if has_scheduled:
            lines.append(
                "- <missed_messages>: 因服务重启未能按时送达的定时消息, 需主动告知用户",
            )

        lines.append("- <current_context>: 当前时间等环境信息")

        lines.append(
            "- <user_input>: 用户本次发送的消息, 你的回复应基于此内容",
        )

        return "\n".join(lines)

    async def _create_memory_assembler(
        self,
        agent_config: AgentConfig,
        user_id: str,
        thread_id: str,
        agent_id: str,
    ) -> MemoryAssembler:
        """创建 MemoryAssembler 实例.

        Args:
            agent_config: Agent 配置对象
            user_id: 用户ID
            thread_id: 线程ID
            agent_id: Agent ID

        Returns:
            MemoryAssembler 实例

        """
        return MemoryAssembler(
            agent_id=agent_id,
            agent_config=agent_config,
            user_id=user_id,
            thread_id=thread_id,
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
        """构建消息上下文 (原生 messages 数组路径).

        流程:
        1. 首轮对话: 返回空 history + first_turn_guidance
        2. 非首轮: 调 MemoryAssembler 拿 history_messages + extension + todo,
           拼接 current_content (时间 + TODO + missed_messages + user_input)

        Args:
            user_input: 用户输入
            user_id: 用户ID
            thread_id: 线程ID
            agent_id: Agent ID
            processor_config: 处理器配置
            timezone: 时区

        Returns:
            MessageContext: 含 history_messages / current_content / system_prompt_extension

        """
        if agent_id:
            logger.debug(
                "LocalMemoryProcessor.build_messages_context for agent_id: %s",
                agent_id,
            )

        if not user_id or not thread_id:
            logger.error(
                "缺少 user_id 或 thread_id (agent_id: %s), user_id=%s, thread_id=%s",
                agent_id,
                user_id,
                thread_id,
            )
            raise RuntimeError("记忆组装失败: 缺少必要的 user_id 或 thread_id 参数")

        agent_config: AgentConfig | None = None
        try:
            if processor_config is not None:
                agent_config = processor_config.get("agent_config")
            if not agent_config:
                raise ValueError("LocalMemoryProcessor 需要有效的 agent_config")

            if not agent_id:
                agent_id = getattr(agent_config, "id", None)
            if not agent_id:
                raise ValueError("agent_id 不能为空, 调用方必须显式传递")

            now = datetime.now(ZoneInfo(timezone))
            time_str = now.strftime("%Y-%m-%d %H:%M:%S %Z")

            first_turn_prompt = getattr(agent_config, "first_turn_prompt", "") or ""
            is_first_turn = False
            if first_turn_prompt.strip():
                conv_service = await create_conversation_service(
                    user_id,
                    thread_id,
                    agent_id=agent_id,
                )
                latest_round = await conv_service.get_latest_round_number(
                    user_id,
                    thread_id,
                )
                is_first_turn = latest_round == 0

            history_messages: list[BaseMessage] = []
            system_prompt_extension = ""

            if is_first_turn:
                logger.info("首轮对话, 跳过历史组装, 注入开场专属提示词")
                return MessageContext(
                    history_messages=[],
                    current_content=self._build_first_turn_content(
                        first_turn_prompt.strip(),
                        time_str,
                        user_input,
                    ),
                    system_prompt_extension="",
                )

            memory_assembler = await self._create_memory_assembler(
                agent_config,
                user_id,
                thread_id,
                agent_id=agent_id,
            )
            total_budget = getattr(
                agent_config.memory,
                "total_char_budget",
                None,
            )
            ctx = await memory_assembler.assemble_memory_context(
                user_id,
                thread_id,
                total_budget,
                agent_id=agent_id,
            )
            history_messages = ctx.history_messages
            system_prompt_extension = ctx.system_prompt_extension

            missed_str = await self._get_missed_messages(
                user_id,
                thread_id,
                agent_id,
            )

            current_content = self._build_current_content(
                time_str=time_str,
                missed_str=missed_str,
                user_input=user_input,
            )

            logger.info(
                "messages 上下文组装成功, history=%d messages, "
                "extension=%d chars, current=%d chars",
                len(history_messages),
                len(system_prompt_extension),
                len(current_content),
            )
            return MessageContext(
                history_messages=history_messages,
                current_content=current_content,
                system_prompt_extension=system_prompt_extension,
            )

        except Exception as e:
            logger.error("build_messages_context 失败: %s", e)
            logger.error("agent_id: %s", agent_id)
            logger.error("user_id: %s", user_id)
            logger.error("thread_id: %s", thread_id)
            logger.error(f"完整异常堆栈:\n{traceback.format_exc()}")
            raise RuntimeError(f"记忆上下文组装失败: {e}") from e

    @staticmethod
    def _build_first_turn_content(
        first_turn_prompt: str,
        time_str: str,
        user_input: str,
    ) -> str:
        """首轮对话的 current_content: first_turn_guidance + 时间 + user_input."""
        parts = [
            f"<first_turn_guidance>\n{first_turn_prompt}\n</first_turn_guidance>",
            f"<current_context>\n时间: {time_str}\n</current_context>",
            f"<user_input>\n{user_input.strip()}\n</user_input>",
        ]
        return "\n\n".join(parts)

    @staticmethod
    def _build_current_content(
        time_str: str,
        missed_str: str,
        user_input: str,
    ) -> str:
        """非首轮的 current_content: missed_messages + 时间 + user_input."""
        parts = []
        if missed_str:
            parts.append(f"<missed_messages>\n{missed_str}\n</missed_messages>")
        parts.append(f"<current_context>\n时间: {time_str}\n</current_context>")
        parts.append(f"<user_input>\n{user_input.strip()}\n</user_input>")
        return "\n\n".join(parts)

    async def _get_missed_messages(
        self,
        user_id: str,
        thread_id: str,
        agent_id: str,
    ) -> str:
        """获取因服务重启未发送的定时消息."""
        try:
            service = await get_scheduled_message_service(
                user_id,
                thread_id,
                agent_id,
            )
            return await service.get_and_acknowledge_missed_messages()
        except Exception as e:
            logger.warning("获取 missed 消息失败(非致命): %s", e)
            return ""

    async def get_or_create_conversation_memory(
        self,
        user_id: str,
        thread_id: str,
        agent_config: AgentConfig | None = None,
    ) -> Any:
        """获取或创建对话记忆核心 (供 orchestrator 存储用)."""
        try:
            from src.agent.memory.local_memory import ConversationMemoryCore

            return ConversationMemoryCore(user_id, thread_id, agent_config)
        except Exception as e:
            logger.error("获取对话记忆失败: %s", e)
            raise RuntimeError(f"获取对话记忆失败: {e}") from e

    @override
    async def get_processor_stats(self) -> dict[str, Any]:
        """获取处理器统计信息."""
        return {
            "processor_type": "LocalMemoryProcessor",
            "memory_type": "local",
            "history_format": "messages_array",
            "service_stats": {},
        }

    @override
    async def cleanup(self) -> None:
        """清理资源."""
        logger.info("清理 LocalMemoryProcessor 资源...")
