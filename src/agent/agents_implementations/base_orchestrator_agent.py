"""OrchestratorAgent中间基类 - 提供基于ProcessorOrchestrator的通用Agent实现.

将PersonalAssistantAgent和HealthAssistantAgent中的共享逻辑抽取到此处:
- initialize: 创建并初始化ProcessorOrchestrator
- cleanup: 清理orchestrator资源
- _build_processor_config: 统一构建处理器配置
- process_message/process_message_stream/finalize_conversation: 模板方法+钩子
"""

from __future__ import annotations

import logging
import traceback
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any, cast, override

if TYPE_CHECKING:
    from src.agent.base_agent import ProcessMessageKwargs

from src.agent.base_agent import BaseAgent
from src.agent.processors import ProcessorOrchestrator
from src.config.agent_config import AgentConfig
from src.core.streaming import StreamContent

logger = logging.getLogger(__name__)


class OrchestratorAgent(BaseAgent):
    """基于ProcessorOrchestrator的通用Agent中间基类.

    提供通用的初始化,处理,清理逻辑, 子类只需覆盖后处理钩子.
    """

    def __init__(self, config: AgentConfig) -> None:
        super().__init__(config)
        self._orchestrator: ProcessorOrchestrator | None = None
        self._initialized = False

    @override
    async def initialize(self) -> None:
        """初始化Agent - 创建并初始化ProcessorOrchestrator."""
        if self._initialized:
            return

        try:
            memory_type = self.config.memory.type if self.config.memory else "local"
            if not memory_type:
                memory_type = "local"
                logger.warning(f"Agent {self.id} memory.type为空, 使用默认值 'local'")

            self._orchestrator = ProcessorOrchestrator(None, memory_type)
            await self._orchestrator.initialize()

            self._initialized = True
            logger.info(
                f"{self.__class__.__name__} {self.id} 初始化完成 "
                f"(记忆类型: {memory_type}, 模型: {self.config.model_id})",
            )

        except Exception as e:
            logger.error(f"{self.__class__.__name__} {self.id} 初始化失败: {e}")
            raise

    def _build_processor_config(self, **kwargs: ProcessMessageKwargs) -> dict[str, Any]:
        """构建统一的processor_config.

        Args:
            **kwargs: 扩展参数, 支持 model_id/temperature/max_tokens/metadata

        Returns:
            处理器配置字典

        """
        processor_config: dict[str, Any] = {
            "agent_config": self.config,
            "system_prompt": self.config.system_prompt or "",
            "memory_config": self.config.memory,
            "tools": self.config.tools,
            "optional_tools": self.config.optional_tools,
        }

        if kwargs:
            for key in ("model_id", "temperature", "max_tokens", "metadata"):
                if key in kwargs:
                    processor_config[key] = kwargs[key]
            # 前端透传历史(simple 模式用), 搭载进 processor_config 供处理器读取
            if "chat_messages" in kwargs:
                processor_config["chat_messages"] = kwargs["chat_messages"]

        return processor_config

    def _ensure_initialized(self) -> None:
        """检查Agent是否已初始化."""
        if not self._orchestrator:
            raise RuntimeError(
                f"{self.__class__.__name__} {self.id} 处理器协调器未初始化",
            )

    async def _lazy_initialize(self) -> None:
        """延迟初始化."""
        if not self._initialized:
            await self.initialize()
        self._ensure_initialized()

    @override
    async def process_message(
        self,
        message: str,
        user_id: str,
        thread_id: str,
        **kwargs: ProcessMessageKwargs,
    ) -> str:
        """处理用户消息 - 模板方法."""
        await self._lazy_initialize()

        try:
            logger.info(
                f"{self.__class__.__name__}.process_message 开始处理: {message[:50]}...",
            )

            processor_config = self._build_processor_config(**kwargs)

            image_datas: list[dict[str, Any]] | None = cast(
                "list[dict[str, Any]] | None",
                kwargs.get("image_datas"),
            )
            attachment_infos: list[Any] | None = cast(
                "list[Any] | None",
                kwargs.get("attachment_infos"),
            )
            timezone: str = cast("str", kwargs.get("timezone", "Asia/Shanghai"))

            result, _, conversation_data = await self._orchestrator.process(
                message,
                user_id,
                thread_id,
                processor_config,
                agent_id=self.id,
                image_datas=image_datas,
                attachment_infos=attachment_infos,
                timezone=timezone,
                round_number=kwargs.get("round_number"),
            )

            # 后处理钩子
            await self._post_process_hook(
                result=result,
                conversation_data=conversation_data,
                user_id=user_id,
                thread_id=thread_id,
                attachment_infos=attachment_infos,
                kwargs=kwargs,
            )

            logger.info(
                f"✅ {self.__class__.__name__}.process_message 处理完成, "
                f"返回结果长度: {len(result)}",
            )
            return result

        except Exception as e:
            logger.error(f"{self.__class__.__name__} {self.id} 处理消息失败: {e}")
            logger.debug(
                f"{self.__class__.__name__} {self.id} 异常详情: {traceback.format_exc()}",
            )
            raise

    @override
    async def process_message_stream(  # type: ignore[override]
        self,
        message: str,
        user_id: str,
        thread_id: str,
        **kwargs: ProcessMessageKwargs,
    ) -> AsyncIterator[str | StreamContent]:
        """处理用户消息(流式响应) - 模板方法."""
        await self._lazy_initialize()

        try:
            logger.info(
                f"🌊 {self.__class__.__name__}.process_message_stream 开始处理: {message[:50]}...",
            )

            processor_config = self._build_processor_config(**kwargs)

            image_datas: list[dict[str, Any]] | None = cast(
                "list[dict[str, Any]] | None",
                kwargs.get("image_datas"),
            )
            attachment_infos: list[Any] | None = cast(
                "list[Any] | None",
                kwargs.get("attachment_infos"),
            )
            timezone: str = cast("str", kwargs.get("timezone", "Asia/Shanghai"))

            # 预流式钩子(子类可覆盖用于缓存pending数据)
            self._pre_stream_hook(
                image_datas=image_datas,
                attachment_infos=attachment_infos,
                kwargs=kwargs,
            )

            async for content_chunk in self._orchestrator.process_stream(
                message,
                user_id,
                thread_id,
                processor_config,
                agent_id=self.id,
                image_datas=image_datas,
                attachment_infos=attachment_infos,
                timezone=timezone,
            ):
                yield content_chunk

            logger.info(f"✅ {self.__class__.__name__}.process_message_stream 处理完成")

        except Exception as e:
            logger.error(f"{self.__class__.__name__} {self.id} 流式处理消息失败: {e}")
            logger.debug(
                f"{self.__class__.__name__} {self.id} 异常详情: {traceback.format_exc()}",
            )
            raise

    @override
    async def finalize_conversation(
        self,
        user_input: str,
        response: str,
        user_id: str,
        thread_id: str,
        **kwargs: ProcessMessageKwargs,
    ) -> None:
        """完成对话处理 - 模板方法."""
        await self._lazy_initialize()

        try:
            logger.info(
                f"💾 {self.__class__.__name__}.finalize_conversation 开始处理: {user_input[:50]}...",
            )

            processor_config = self._build_processor_config(**kwargs)
            timezone: str = cast("str", kwargs.get("timezone", "Asia/Shanghai"))

            conversation_data = await self._orchestrator.finalize_conversation(
                user_input,
                response,
                user_id,
                thread_id,
                processor_config,
                self.id,
                attachment_infos=kwargs.get("attachment_infos"),
                timezone=timezone,
                round_number=kwargs.get("round_number"),
            )

            # 后完成钩子
            await self._post_finalize_hook(
                response=response,
                conversation_data=conversation_data,
                user_id=user_id,
                thread_id=thread_id,
                kwargs=kwargs,
            )

            logger.info(f"✅ {self.__class__.__name__}.finalize_conversation 处理完成")

        except Exception as e:
            logger.error(f"{self.__class__.__name__} {self.id} 完成对话处理失败: {e}")
            logger.debug(
                f"{self.__class__.__name__} {self.id} 异常详情: {traceback.format_exc()}",
            )
            raise

    @override
    async def cleanup(self) -> None:
        """清理Agent资源."""
        if not self._initialized:
            return

        try:
            if self._orchestrator and hasattr(self._orchestrator, "cleanup"):
                await self._orchestrator.cleanup()

            self._initialized = False
            self._cleanup_hook()
            logger.info(f"{self.__class__.__name__} {self.id} 清理完成")

        except Exception as e:
            logger.error(f"{self.__class__.__name__} {self.id} 清理失败: {e}")
            raise

    # ==================== 钩子方法(子类覆盖) ====================

    async def _post_process_hook(
        self,
        result: str,
        conversation_data: Any,
        user_id: str,
        thread_id: str,
        attachment_infos: list[Any] | None,
        kwargs: ProcessMessageKwargs,
    ) -> None:
        """process_message后处理钩子, 子类可覆盖."""

    def _pre_stream_hook(
        self,
        image_datas: list[dict[str, Any]] | None,
        attachment_infos: list[Any] | None,
        kwargs: ProcessMessageKwargs,
    ) -> None:
        """process_message_stream前置钩子, 子类可覆盖."""

    async def _post_finalize_hook(
        self,
        response: str,
        conversation_data: Any,
        user_id: str,
        thread_id: str,
        kwargs: ProcessMessageKwargs,
    ) -> None:
        """finalize_conversation后处理钩子, 子类可覆盖."""

    def _cleanup_hook(self) -> None:
        """cleanup时的额外清理钩子, 子类可覆盖."""
