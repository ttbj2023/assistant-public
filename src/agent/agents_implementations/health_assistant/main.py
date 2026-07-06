"""Health Assistant Agent具体实现.

基于OrchestratorAgent中间基类的健康管理Agent.
集成后台健康数据自动提取: 对话结束后静默检测和存储用户的健康数据.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any, override

if TYPE_CHECKING:
    from src.agent.base_agent import ProcessMessageKwargs

from src.agent.agents_implementations.base_orchestrator_agent import OrchestratorAgent
from src.config.agent_config import AgentConfig

from .health_data_audit import run_audit, should_audit
from .health_data_background_extractor import HealthDataBackgroundExtractor

logger = logging.getLogger(__name__)


class HealthAssistantAgent(OrchestratorAgent):
    """Health Assistant Agent - 健康管理业务逻辑实现.

    后台自动提取:
    - 对话结束后自动检测用户消息中的健康数据
    - 支持7种数据类型的静默提取和存储
    - 支持文本和图片输入
    """

    def __init__(self, config: AgentConfig) -> None:
        super().__init__(config)
        self._pending_image_datas: list[dict] | None = None
        self._pending_attachment_infos: list[Any] | None = None

    # ==================== 健康数据提取调度 ====================

    def _schedule_health_data_extraction(
        self,
        user_message: str,
        user_id: str,
        thread_id: str,
        attachment_infos: list[Any] | None = None,
        round_number: int | None = None,
    ) -> None:
        """调度后台健康数据提取任务 (fire-and-forget)."""
        extractor = HealthDataBackgroundExtractor(user_id, thread_id, agent_id=self.id)

        async def _background_task() -> None:
            try:
                await extractor.extract_from_conversation(
                    user_message=user_message,
                    attachment_infos=attachment_infos,
                    round_number=round_number,
                )
            except Exception as e:
                logger.warning(
                    "后台健康数据提取任务异常 (%s:%s): %s",
                    user_id,
                    thread_id,
                    e,
                )

        try:
            loop = asyncio.get_running_loop()
            bg_tasks: set[asyncio.Task] = set()
            task = loop.create_task(_background_task())
            bg_tasks.add(task)
            task.add_done_callback(bg_tasks.discard)
            logger.debug("已调度后台健康数据提取任务 (%s:%s)", user_id, thread_id)
        except RuntimeError:
            logger.warning("无法获取事件循环, 跳过后台健康数据提取")

    def _schedule_health_data_audit(
        self,
        user_id: str,
        thread_id: str,
        current_round: int,
        user_message: str | None = None,
        attachment_infos: list[Any] | None = None,
    ) -> None:
        """调度后台健康数据审计+提取任务 (替代该轮的常规提取器)."""

        async def _audit_task() -> None:
            try:
                await run_audit(
                    user_id,
                    thread_id,
                    self.id,
                    current_round,
                    user_message=user_message,
                    attachment_infos=attachment_infos,
                )
            except Exception as e:
                logger.warning(
                    "后台健康数据审计任务异常 (%s:%s): %s",
                    user_id,
                    thread_id,
                    e,
                )

        try:
            loop = asyncio.get_running_loop()
            audit_tasks: set[asyncio.Task] = set()
            task = loop.create_task(_audit_task())
            audit_tasks.add(task)
            task.add_done_callback(audit_tasks.discard)
            logger.debug("已调度后台健康数据审计任务 (%s:%s)", user_id, thread_id)
        except RuntimeError:
            logger.warning("无法获取事件循环, 跳过后台健康数据审计")

    def _dispatch_health_data(
        self,
        conversation_data: Any,
        user_id: str,
        thread_id: str,
        attachment_infos: list[Any] | None,
        kwargs: ProcessMessageKwargs,
    ) -> None:
        """统一的健康数据提取调度逻辑 (常规提取 or 审计)."""
        round_number = kwargs.get("round_number") if "round_number" in kwargs else None
        effective_round = round_number or 0
        is_audit_round = effective_round > 0 and should_audit(
            user_id,
            thread_id,
            self.id,
            effective_round,
        )

        if is_audit_round:
            if conversation_data:
                self._schedule_health_data_audit(
                    user_id=user_id,
                    thread_id=thread_id,
                    current_round=effective_round,
                    user_message=conversation_data.user_message,
                    attachment_infos=attachment_infos,
                )
        elif conversation_data:
            self._schedule_health_data_extraction(
                user_message=conversation_data.user_message,
                user_id=user_id,
                thread_id=thread_id,
                attachment_infos=attachment_infos,
                round_number=round_number,
            )

    # ==================== 钩子覆盖 ====================

    @override
    async def _post_process_hook(
        self,
        result: str,
        conversation_data: Any,
        user_id: str,
        thread_id: str,
        attachment_infos: list[Any] | None,
        kwargs: ProcessMessageKwargs,
    ) -> None:
        """process_message后: 调度健康数据提取."""
        self._dispatch_health_data(
            conversation_data=conversation_data,
            user_id=user_id,
            thread_id=thread_id,
            attachment_infos=attachment_infos,
            kwargs=kwargs,
        )

    @override
    def _pre_stream_hook(
        self,
        image_datas: list[dict[str, Any]] | None,
        attachment_infos: list[Any] | None,
        kwargs: ProcessMessageKwargs,
    ) -> None:
        """流式处理前: 缓存pending数据供finalize使用."""
        self._pending_image_datas = image_datas
        self._pending_attachment_infos = attachment_infos

    @override
    async def _post_finalize_hook(
        self,
        response: str,
        conversation_data: Any,
        user_id: str,
        thread_id: str,
        kwargs: ProcessMessageKwargs,
    ) -> None:
        """finalize_conversation后: 调度健康数据提取并清理pending状态."""
        self._dispatch_health_data(
            conversation_data=conversation_data,
            user_id=user_id,
            thread_id=thread_id,
            attachment_infos=self._pending_attachment_infos,
            kwargs=kwargs,
        )
        self._pending_image_datas = None
        self._pending_attachment_infos = None

    @override
    def _cleanup_hook(self) -> None:
        """清理pending状态."""
        self._pending_image_datas = None
        self._pending_attachment_infos = None


__all__ = ["HealthAssistantAgent"]
