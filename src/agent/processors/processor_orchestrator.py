"""处理器总协调器 - 协调记忆和推理组件."""

from __future__ import annotations

import logging
import traceback
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, NamedTuple
from zoneinfo import ZoneInfo

from src.core.context import get_user_context_or_none
from src.core.streaming import StreamContent
from src.storage.models.conversation import ConversationData
from src.storage.service import (
    create_conversation_data_service,
    create_conversation_service,
)
from src.utils.message_formatting import format_user_message_with_attachments

if TYPE_CHECKING:
    from .base_processor import BaseProcessor

from .inference_coordinator import InferenceCoordinator
from .local_memory_processor import LocalMemoryProcessor
from .simple_memory_processor import SimpleMemoryProcessor

logger = logging.getLogger(__name__)


class _RequestContext(NamedTuple):
    """process / process_stream 共用的请求上下文准备结果(读取侧)."""

    agent_config: Any
    system_prompt: str
    user_content: str
    prompt_sections: dict[str, str]
    llm_config: dict[str, Any]
    history_messages: list[Any] | None


class ProcessorOrchestrator:
    """处理器总协调器 - 选择合适的记忆处理器,协调推理和记忆.

    职责:
    1. 根据配置选择合适的记忆处理器
    2. 协调记忆处理器和推理协调器
    3. 提供统一的处理接口
    4. 管理处理器生命周期
    """

    def __init__(
        self,
        config: dict[str, Any] | None,
        memory_type: str = "local",
    ) -> None:
        """初始化处理器总协调器.

        Args:
            config: 应用配置实例
            memory_type: 记忆处理器类型,默认为"local"

        """
        self.config = config
        self.memory_type = memory_type

        # 选择并创建记忆处理器
        self.memory_processor = self._create_memory_processor(memory_type)

        # 创建推理协调器
        self.inference_coordinator = InferenceCoordinator(config)

        logger.info("🚀 处理器总协调器初始化完成,记忆类型: %s", memory_type)

    def _create_memory_processor(self, memory_type: str) -> BaseProcessor:
        """根据类型创建记忆处理器.

        Args:
            memory_type: 记忆处理器类型

        Returns:
            对应的记忆处理器实例

        """
        processors = {
            "local": LocalMemoryProcessor,
            "simple": SimpleMemoryProcessor,
        }

        if memory_type not in processors:
            logger.warning(
                "未知的记忆类型 '%s',使用默认的 'local' 处理器",
                memory_type,
            )
            memory_type = "local"

        processor_class = processors[memory_type]
        return processor_class(self.config)

    async def initialize(self) -> None:
        """初始化所有组件 - 顺序初始化避免资源竞争."""
        try:
            logger.info(
                "🔄 开始顺序初始化处理器总协调器,记忆类型: %s",
                self.memory_type,
            )

            # 1. 首先初始化记忆处理器
            await self.memory_processor.initialize()
            logger.info("✅ 记忆处理器初始化完成")

            # 2. 推理协调器不需要额外初始化

            logger.info("✅ 处理器总协调器顺序初始化完成")
        except Exception as e:
            logger.error("❌ 处理器总协调器初始化失败: %s", e)
            logger.error("❌ 记忆类型: %s", self.memory_type)
            logger.error("❌ 错误类型: %s", type(e).__name__)
            logger.error("❌ 错误堆栈: %s", traceback.format_exc())
            raise RuntimeError(f"处理器总协调器初始化失败: {e}") from e

    async def process(
        self,
        user_input: str,
        user_id: str,
        thread_id: str,
        processor_config: dict[str, Any] | None = None,
        debug_session_id: str | None = None,
        agent_id: str | None = None,
        image_datas: list[dict[str, Any]] | None = None,
        attachment_infos: list[Any] | None = None,
        timezone: str = "Asia/Shanghai",
        round_number: int | None = None,
    ) -> tuple[str, dict[str, Any] | None, ConversationData | None]:
        """执行AI推理协调(由Agent调用).

        Args:
            user_input: 用户输入(已验证)
            user_id: 用户ID(已验证)
            thread_id: 线程ID(已验证)
            processor_config: 处理器配置参数(从Agent传入)
            debug_session_id: 调试会话ID
            agent_id: Agent ID
            image_datas: 图片数据列表 [{"data": bytes, "mime_type": str}]
            attachment_infos: 附件描述列表(用于非视觉模型降级)

        Returns:
            tuple[str, dict | None, ConversationData | None]: (AI响应内容, 推理统计信息, 对话数据)

        """
        logger.info(
            "🔍 [ENTER] ProcessorOrchestrator.process - user_id=%s, thread_id=%s",
            user_id,
            thread_id,
        )

        # 调试信息
        logger.debug("🔍 [DEBUG] 记忆处理器类型: %s", type(self.memory_processor))
        logger.debug("🔍 [DEBUG] 推理协调器类型: %s", type(self.inference_coordinator))
        logger.debug(
            "ProcessorOrchestrator.process被调用: user_id=%s, thread_id=%s, agent_id=%s",
            user_id,
            thread_id,
            agent_id,
        )
        logger.debug("用户输入长度: %s 字符", len(user_input))
        logger.debug("processor_config类型: %s", type(processor_config))

        logger.info(
            "🤖 处理器总协调器开始处理请求: %s... (用户ID: %s, 线程ID: %s)",
            user_input[:50],
            user_id,
            thread_id,
        )
        logger.debug("📋 ProcessorOrchestrator.process - 开始处理")
        logger.debug("📋 processor_config类型: %s", type(processor_config))
        logger.debug("📋 processor_config内容: %s", processor_config)
        logger.debug("📋 debug_session_id: %s", debug_session_id)
        logger.debug("📋 agent_id: %s", agent_id)

        conversation_data: ConversationData | None = None

        try:
            logger.debug(
                "ProcessorOrchestrator.process try块开始: %s:%s",
                user_id,
                thread_id,
            )

            ctx = await self._prepare_request_context(
                user_input=user_input,
                user_id=user_id,
                thread_id=thread_id,
                agent_id=agent_id,
                processor_config=processor_config,
                timezone=timezone,
            )

            logger.info("🚀 准备调用推理协调器: %s:%s", user_id, thread_id)
            (
                response_content,
                inference_stats,
            ) = await self.inference_coordinator.process_with_agent(
                user_content=ctx.user_content,
                system_prompt=ctx.system_prompt,
                llm_config=ctx.llm_config,
                user_id=user_id,
                thread_id=thread_id,
                agent_id=agent_id,
                agent_config=ctx.agent_config,
                image_datas=image_datas,
                attachment_infos=attachment_infos,
                history_messages=ctx.history_messages,
                prompt_sections=ctx.prompt_sections,
            )
            logger.info("✅ 推理协调器调用成功: %s:%s", user_id, thread_id)

            # 4. 更新对话记忆(如果记忆处理器支持)
            logger.info("🔍 开始更新对话记忆: %s:%s", user_id, thread_id)
            logger.info(
                "🔍 检查记忆处理器类型: %s",
                type(self.memory_processor).__name__,
            )
            if hasattr(self.memory_processor, "get_or_create_conversation_memory"):
                try:
                    logger.info("📋 获取对话记忆实例: %s:%s", user_id, thread_id)
                    agent_config = ctx.agent_config
                    get_conv_mem = (
                        self.memory_processor.get_or_create_conversation_memory
                    )
                    conversation_memory = await get_conv_mem(
                        user_id,
                        thread_id,
                        agent_config,
                    )
                    logger.info(
                        "✅ 对话记忆实例获取成功: %s",
                        type(conversation_memory).__name__,
                    )

                    # 如果有记忆上下文,更新对话
                    if hasattr(conversation_memory, "add_conversation_round"):
                        logger.info("💾 开始存储对话轮次: %s:%s", user_id, thread_id)

                        # 构建ConversationData
                        conversation_data = await self._build_conversation_data(
                            user_input=user_input,
                            response_content=response_content,
                            user_id=user_id,
                            thread_id=thread_id,
                            agent_id=self._resolve_agent_id(
                                agent_config,
                                agent_id,
                                "process_with_agent",
                            ),
                            attachment_infos=attachment_infos,
                            timezone=timezone,
                            round_number=round_number,
                        )

                        await conversation_memory.add_conversation_round(
                            conversation_data,
                        )

                        # 确认轮次号使用并清理预留记录
                        await self._confirm_round_number(
                            conversation_data, user_id, thread_id
                        )

                        logger.info("✅ 已成功更新对话记忆: %s:%s", user_id, thread_id)
                    else:
                        logger.warning(
                            "⚠️ 对话记忆实例缺少add_conversation_round方法: %s",
                            type(conversation_memory).__name__,
                        )

                except Exception as memory_error:
                    logger.error("❌ 更新对话记忆失败: %s", memory_error)
                    logger.error("❌ 错误类型: %s", type(memory_error).__name__)
                    logger.error("❌ 用户ID: %s, 线程ID: %s", user_id, thread_id)
                    if "conversation_memory" in dir():
                        logger.error("❌ 对话记忆类型: %s", type(conversation_memory))
                    logger.error("❌ 错误堆栈: %s", traceback.format_exc())
                    # 存储失败是严重错误, 向上传播原始异常, 由顶层统一包装一次
                    raise
            else:
                logger.warning(
                    "⚠️ 记忆处理器不支持get_or_create_conversation_memory方法: %s",
                    type(self.memory_processor).__name__,
                )

            # 5. 构建完整的统计信息
            memory_stats = {}
            if hasattr(self.memory_processor, "get_processor_stats"):
                try:
                    memory_stats = await self.memory_processor.get_processor_stats()
                except Exception as stats_error:
                    logger.warning("⚠️ 获取记忆处理器统计失败: %s", stats_error)

            stats = {
                "orchestrator_stats": {
                    "memory_type": self.memory_type,
                    "processing_success": True,
                },
                "memory_stats": memory_stats,
                "inference_stats": inference_stats,
            }

            logger.info("✅ 处理器总协调器完成处理")
            return response_content, stats, conversation_data

        except Exception as e:
            logger.error("❌ 处理器总协调器失败: %s", e)

            # 重新抛出异常,中间件会统一处理
            raise RuntimeError(f"处理器总协调器失败: {e}") from e

    async def get_processor_stats(self) -> dict[str, Any]:
        """获取所有处理器的统计信息."""
        try:
            memory_stats = {}
            if hasattr(self.memory_processor, "get_processor_stats"):
                memory_stats = await self.memory_processor.get_processor_stats()

            return {
                "orchestrator_type": "ProcessorOrchestrator",
                "memory_type": self.memory_type,
                "memory_processor_stats": memory_stats,
                "inference_coordinator_available": self.inference_coordinator
                is not None,
            }

        except Exception as e:
            logger.error("获取处理器统计失败: %s", e)
            return {"error": str(e)}

    async def cleanup(self) -> None:
        """清理所有组件资源."""
        logger.info("🧹 清理处理器总协调器资源...")

        try:
            # 清理记忆处理器
            if hasattr(self.memory_processor, "cleanup"):
                await self.memory_processor.cleanup()

            logger.info("✅ 处理器总协调器资源清理完成")

        except Exception as e:
            logger.error("❌ 清理处理器总协调器资源失败: %s", e)

    @staticmethod
    def _resolve_agent_id(agent_config: Any, agent_id: str | None, context: str) -> str:
        """解析并验证 agent_id.

        agent_id 是业务必须字段,不允许漏传.按优先级从 agent_config 或显式参数中获取.

        Args:
            agent_config: Agent配置对象,可能包含 agent_id 属性
            agent_id: 显式传入的 agent_id
            context: 调用上下文描述,用于错误信息

        Returns:
            解析后的 agent_id

        Raises:
            ValueError: agent_id 无法解析时

        """
        if agent_config and hasattr(agent_config, "agent_id") and agent_config.agent_id:
            return agent_config.agent_id
        if agent_id:
            return agent_id
        raise ValueError(
            f"agent_id 不能为空 (上下文: {context}). "
            f"调用方必须显式传递 agent_id 或通过 agent_config 提供",
        )

    async def _allocate_round_number_simple(
        self,
        user_id: str,
        thread_id: str,
        agent_id: str,
    ) -> int:
        """简化的轮次号分配,基于用户洞察的串行处理特性.

        Args:
            user_id: 用户ID
            thread_id: 线程ID

        Returns:
            int: 分配的轮次号

        """
        conv_service = await create_conversation_service(
            user_id,
            thread_id,
            agent_id=agent_id,
        )

        round_number = await conv_service.allocate_round_number(user_id, thread_id)
        logger.debug(
            "分配轮次号成功: %s:%s -> %s",
            user_id,
            thread_id,
            round_number,
        )
        return round_number

    async def _build_conversation_data(
        self,
        user_input: str,
        response_content: str,
        user_id: str,
        thread_id: str,
        agent_id: str | None = None,
        attachment_infos: list[Any] | None = None,
        timezone: str = "Asia/Shanghai",
        round_number: int | None = None,
    ) -> ConversationData:
        """简化的ConversationData构建,基于用户洞察的统一数据源架构.

        Args:
            user_input: 用户输入
            response_content: 助手回复
            user_id: 用户ID
            thread_id: 线程ID
            agent_id: Agent ID
            attachment_infos: 附件信息列表(图片占位符会嵌入user_message)
            timezone: 用户时区

        Returns:
            ConversationData: 构建好的统一对话数据

        """
        resolved_agent_id = self._resolve_agent_id(
            None,
            agent_id,
            "_build_conversation_data",
        )

        # 1. 使用预分配轮次号, 未传入时保持原有自动分配行为
        if round_number is None:
            round_number = await self._allocate_round_number_simple(
                user_id,
                thread_id,
                agent_id=resolved_agent_id,
            )

        # 2. 统一时间源: 一次获取 UTC 时间, 派生用户时区前缀
        now_utc = datetime.now(UTC)
        now_user_tz = now_utc.astimezone(ZoneInfo(timezone))
        time_prefix = f"[{now_user_tz.strftime('%Y-%m-%d %H:%M:%S %Z')}] "

        # 3. 将附件信息嵌入用户消息(图片以占位符形式保留)
        formatted_message = user_input
        attachments = attachment_infos or []
        if attachments:
            formatted_message = format_user_message_with_attachments(
                user_text=user_input,
                attachments=attachments,
            )

        # 4. 拼入时间前缀
        formatted_message = time_prefix + formatted_message

        # 4.5 为 assistant_response 追加缺失的附件标记
        response_for_storage = _append_exported_file_markers(response_content)

        # 5. 构建 ConversationData (attachments 已是死数据: 不持久化, 生产零读取,
        #    LLM 仅通过 [file: id] 文本标记 + attachment_registry 检索文件)
        conversation_data = ConversationData(
            user_id=user_id,
            thread_id=thread_id,
            user_message=formatted_message,
            assistant_response=response_for_storage,
            round_number=round_number,
            timestamp=now_utc,
            agent_id=resolved_agent_id,
        )

        # 检测轮次号异常跳跃(轻量级监控)
        await self._detect_round_number_anomaly(
            user_id,
            thread_id,
            round_number,
            agent_id=resolved_agent_id,
        )

        logger.debug(
            "构建ConversationData完成: %s:%s -> round %s",
            user_id,
            thread_id,
            round_number,
        )
        return conversation_data

    async def _detect_round_number_anomaly(
        self,
        user_id: str,
        thread_id: str,
        new_round: int,
        agent_id: str,
    ) -> bool:
        """检测轮次号异常跳跃.

        Args:
            user_id: 用户ID
            thread_id: 线程ID
            new_round: 新分配的轮次号

        Returns:
            bool: 是否检测到异常

        """
        try:
            conv_service = await create_conversation_service(
                user_id,
                thread_id,
                agent_id=agent_id,
            )
            latest_round = await conv_service.get_latest_round_number(
                user_id,
                thread_id,
            )

            # 如果跳跃超过10,记录警告
            if new_round - latest_round > 10:
                logger.warning(
                    "检测到轮次号异常跳跃: %s:%s %s -> %s (跳跃: %s)",
                    user_id,
                    thread_id,
                    latest_round,
                    new_round,
                    new_round - latest_round,
                )
                return True

            return False
        except Exception as e:
            # 检测失败不影响主流程
            logger.debug("轮次号跳跃检测异常, 跳过检测: %s", e)
            return False

    async def process_stream(
        self,
        user_input: str,
        user_id: str,
        thread_id: str,
        processor_config: dict[str, Any] | None = None,
        agent_id: str | None = None,
        image_datas: list[dict[str, Any]] | None = None,
        attachment_infos: list[Any] | None = None,
        timezone: str = "Asia/Shanghai",
    ) -> AsyncIterator[str | StreamContent]:
        """执行AI推理协调(流式响应).

        Args:
            user_input: 用户输入(已验证)
            user_id: 用户ID(已验证)
            thread_id: 线程ID(已验证)
            processor_config: 处理器配置参数(从Agent传入)
            agent_id: Agent ID
            image_datas: 图片数据列表 [{"data": bytes, "mime_type": str}]

        Yields:
            AI响应内容片段

        Raises:
            ValueError: 处理器配置未传递
            RuntimeError: 处理失败时抛出异常

        Note:
            - 流式响应不触发记忆存储
            - 必须在流结束后调用 finalize_conversation() 完成记忆存储

        """
        logger.info(
            "🌊 [ENTER] ProcessorOrchestrator.process_stream - user_id=%s, thread_id=%s",
            user_id,
            thread_id,
        )

        logger.info(
            "🌊 处理器总协调器开始流式处理: %s... (用户ID: %s, 线程ID: %s)",
            user_input[:50],
            user_id,
            thread_id,
        )

        try:
            ctx = await self._prepare_request_context(
                user_input=user_input,
                user_id=user_id,
                thread_id=thread_id,
                agent_id=agent_id,
                processor_config=processor_config,
                timezone=timezone,
            )

            # 4. 调用推理协调器的流式处理
            logger.info("🚀 准备调用推理协调器流式处理: %s:%s", user_id, thread_id)
            async for (
                content_chunk
            ) in self.inference_coordinator.process_with_agent_stream(
                user_content=ctx.user_content,
                system_prompt=ctx.system_prompt,
                llm_config=ctx.llm_config,
                user_id=user_id,
                thread_id=thread_id,
                agent_id=agent_id,
                agent_config=ctx.agent_config,
                image_datas=image_datas,
                attachment_infos=attachment_infos,
                history_messages=ctx.history_messages,
                prompt_sections=ctx.prompt_sections,
            ):
                yield content_chunk

            logger.info("✅ 推理协调器流式调用成功: %s:%s", user_id, thread_id)

            # 注意:记忆存储在流结束后由 finalize_conversation() 处理

        except Exception as e:
            logger.error("❌ 处理器总协调器流式处理失败: %s", e)
            raise RuntimeError(f"处理器总协调器流式处理失败: {e}") from e

    async def finalize_conversation(
        self,
        user_input: str,
        response_content: str,
        user_id: str,
        thread_id: str,
        processor_config: dict[str, Any] | None = None,
        agent_id: str | None = None,
        attachment_infos: list[Any] | None = None,
        timezone: str = "Asia/Shanghai",
        round_number: int | None = None,  # noqa: ARG002
    ) -> ConversationData | None:
        """完成对话处理 - 记忆存储(流式响应专用).

        在流式响应结束后调用此方法来存储对话数据到记忆系统.

        Returns:
            ConversationData: 构建的对话数据(含统一时间戳)

        Args:
            user_input: 用户输入
            response_content: 完整的AI响应(所有内容片段拼接后的结果)
            user_id: 用户ID
            thread_id: 线程ID
            processor_config: 处理器配置参数
            agent_id: Agent ID

        Raises:
            RuntimeError: 存储失败时抛出异常

        Note:
            - 此方法主要用于流式响应场景
            - 非流式响应的 process() 方法会自动调用记忆存储
            - 确保此方法的幂等性(多次调用不影响结果)

        """
        logger.info(
            "💾 [ENTER] ProcessorOrchestrator.finalize_conversation - user_id=%s, thread_id=%s",
            user_id,
            thread_id,
        )

        try:
            # 1. 获取对话记忆实例
            if not hasattr(self.memory_processor, "get_or_create_conversation_memory"):
                logger.warning(
                    "⚠️ 记忆处理器不支持get_or_create_conversation_memory方法: %s",
                    type(self.memory_processor).__name__,
                )
                return None

            logger.info("📋 获取对话记忆实例: %s:%s", user_id, thread_id)
            agent_config = (
                processor_config.get("agent_config") if processor_config else None
            )
            get_conv_mem = self.memory_processor.get_or_create_conversation_memory
            conversation_memory = await get_conv_mem(user_id, thread_id, agent_config)
            logger.info(
                "✅ 对话记忆实例获取成功: %s",
                type(conversation_memory).__name__,
            )

            # 2. 检查是否支持记忆存储
            if not hasattr(conversation_memory, "add_conversation_round"):
                logger.warning(
                    "⚠️ 对话记忆实例缺少add_conversation_round方法: %s",
                    type(conversation_memory).__name__,
                )
                return None
            logger.info("💾 开始存储对话轮次: %s:%s", user_id, thread_id)
            conversation_data = await self._build_conversation_data(
                user_input=user_input,
                response_content=response_content,
                user_id=user_id,
                thread_id=thread_id,
                agent_id=self._resolve_agent_id(
                    agent_config,
                    agent_id,
                    "finalize_stream",
                ),
                attachment_infos=attachment_infos,
                timezone=timezone,
            )

            # 4. 存储对话轮次
            await conversation_memory.add_conversation_round(conversation_data)

            # 5. 确认轮次号使用并清理预留记录
            await self._confirm_round_number(conversation_data, user_id, thread_id)

            logger.info("✅ 已成功更新对话记忆: %s:%s", user_id, thread_id)

            return conversation_data

        except Exception as e:
            logger.error("❌ 完成对话处理失败: %s", e)
            logger.error("❌ 错误类型: %s", type(e).__name__)
            logger.error("❌ 错误堆栈: %s", traceback.format_exc())
            raise RuntimeError(f"完成对话处理失败: {e}") from e

    async def _prepare_request_context(
        self,
        *,
        user_input: str,
        user_id: str,
        thread_id: str,
        agent_id: str | None,
        processor_config: dict[str, Any] | None,
        timezone: str,
    ) -> _RequestContext:
        """构建消息上下文/记忆段/llm_config, process 与 process_stream 共用读取侧.

        仅覆盖上下文准备(读取), 不涉及记忆写入; process 内联写与 process_stream
        延迟到 finalize_conversation 写的差异保留在各自方法中.

        Raises:
            ValueError: processor_config 未传递
        """
        if processor_config is None:
            logger.error("❌ 处理器配置未传递")
            raise ValueError("处理器配置未传递,请通过processor_config参数传递配置")

        agent_config = processor_config.get("agent_config")
        system_prompt = processor_config.get("system_prompt", "")
        msg_ctx = await self.memory_processor.build_messages_context(
            user_input=user_input,
            user_id=user_id,
            thread_id=thread_id,
            agent_id=agent_id,
            processor_config=processor_config,
            timezone=timezone,
        )

        # 构建记忆段 (hint + extension), 不再过早合并进 base, 由装配器统一排序
        memory_hint = self.memory_processor.get_prompt_hint(agent_config)
        memory_parts: list[str] = []
        if memory_hint:
            memory_parts.append(memory_hint)
        if msg_ctx.system_prompt_extension:
            memory_parts.append(msg_ctx.system_prompt_extension)
        prompt_sections: dict[str, str] = {}
        if memory_parts:
            prompt_sections["memory"] = "\n\n".join(memory_parts)

        user_content = msg_ctx.current_content
        logger.debug(
            "📝 最终用户输入长度: %s 字符, 历史消息数: %s",
            len(user_content),
            len(msg_ctx.history_messages),
        )

        # 构建 LLM 配置: 优先 agent_config.model_id, 支持 llm_config 覆盖
        if agent_config:
            llm_config: dict[str, Any] = {"model": agent_config.model_id}
            if hasattr(agent_config, "llm_config") and agent_config.llm_config:
                llm_config.update(agent_config.llm_config)
        else:
            llm_config = processor_config.get("llm_config", {})

        return _RequestContext(
            agent_config=agent_config,
            system_prompt=system_prompt,
            user_content=user_content,
            prompt_sections=prompt_sections,
            llm_config=llm_config,
            history_messages=msg_ctx.history_messages or None,
        )

    async def _confirm_round_number(
        self,
        conversation_data: ConversationData,
        user_id: str,
        thread_id: str,
    ) -> None:
        """确认轮次号使用并清理预留记录; 失败仅告警, 不阻断主流程."""
        try:
            conv_data_service = await create_conversation_data_service(
                user_id,
                thread_id,
                agent_id=conversation_data.agent_id,
            )
            await conv_data_service.confirm_round_number_usage(
                conversation_data.round_number,
                user_id,
                thread_id,
            )
            logger.debug("已确认轮次号使用: %s", conversation_data.round_number)
        except Exception as confirm_error:
            logger.warning("轮次号确认失败,但不影响主流程: %s", confirm_error)


def _append_exported_file_markers(response_content: str) -> str:
    """将 exported_files 中尚未出现的附件标记追加到响应末尾.

    保证 LLM 在后续轮次能从对话历史中看到 [file: file_id] 标记,
    即使本轮 LLM 原始输出没有写出真实 URL 或标记.

    格式: [file: file_id] brief
    """
    ctx = get_user_context_or_none()
    if not ctx or not ctx.exported_files:
        return response_content

    result = response_content
    lines_to_append: list[str] = []
    for file_info in ctx.exported_files:
        file_id = file_info.get("file_id", "")
        if not file_id or f"[file: {file_id}]" in result:
            continue
        brief = file_info.get("brief", file_info.get("filename", "file"))
        lines_to_append.append(f"[file: {file_id}] {brief}")

    if not lines_to_append:
        return result

    if result and not result.endswith("\n"):
        result += "\n"
    return result + "\n".join(lines_to_append)
