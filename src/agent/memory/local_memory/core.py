"""Conversation Memory Core - 对话记忆核心.

为个人助手系统提供对话记忆管理, 包含6个并行存储操作:
1. SQL数据库对话内容存储
2. 向量数据库语义存储
3. 置顶记忆更新
4. 对话索引生成
5. 轮次号使用确认
6. 缓存更新与溢出处理

基于统一ConversationData数据源, 确保所有操作的数据一致性.
"""

from __future__ import annotations

import asyncio
import logging
import traceback
from typing import TYPE_CHECKING, override

from src.storage.service import (
    create_conversation_service,
    create_vector_service,
)

if TYPE_CHECKING:
    from src.config.agent_config import AgentConfig
    from src.storage.models.conversation import ConversationData

from .index_run_service import IndexRunService
from .pinned_memory_service import PinnedMemoryService

# 缓存内部索引区和对话历史的分隔符 - 使用中性标记, 不与外层XML标签冲突


logger = logging.getLogger(__name__)


def _resolve_index_run_threshold(agent_config: AgentConfig | None) -> float:
    """解析 run 检测相似度阈值(回退 0.45)."""
    if agent_config is not None:
        try:
            val = getattr(agent_config.memory, "index_run_similarity_threshold", None)
            if isinstance(val, (int, float)) and 0.0 <= val <= 1.0:
                return float(val)
        except Exception as e:
            logger.debug("run 阈值配置获取失败, 使用默认值: %s", e)
    return 0.45


def _resolve_index_arc_max_chars(agent_config: AgentConfig | None) -> int:
    """解析弧短语最大字符数(回退 60)."""
    if agent_config is not None:
        try:
            val = getattr(agent_config.memory, "index_arc_max_chars", None)
            if isinstance(val, int) and val > 0:
                return val
        except Exception as e:
            logger.debug("弧短语长度配置获取失败, 使用默认值: %s", e)
    return 60


class ConversationMemoryCore:
    """对话记忆核心 - 管理6个并行存储操作.

    负责协调对话完成后的所有数据持久化操作,使用统一数据源确保一致性.
    提供容错机制,单个操作失败不影响整体流程.
    """

    def __init__(
        self,
        user_id: str,
        thread_id: str,
        agent_config: AgentConfig | None = None,
    ) -> None:
        """初始化对话记忆核心.

        Args:
            user_id: 用户ID
            thread_id: 线程ID
            agent_config: Agent配置对象

        """
        self.user_id = user_id
        self.thread_id = thread_id
        self.agent_config = agent_config
        if not agent_config or not agent_config.agent_id:
            raise ValueError(
                "ConversationMemoryCore 初始化失败: agent_id 不能为空, agent_config 必须包含 agent_id 属性",
            )
        self.agent_id: str = agent_config.agent_id

        # 置顶记忆子系统: 主模型覆写 (fire-and-forget)
        self._pinned_svc = PinnedMemoryService(
            self.user_id,
            self.thread_id,
            self.agent_id,
            agent_config=agent_config,
        )

        # 索引 run 检测子系统: 语义连续性判定 + 弧短语冻结 (fire-and-forget)
        sim_threshold = _resolve_index_run_threshold(agent_config)
        arc_max_chars = _resolve_index_arc_max_chars(agent_config)
        self._index_run_svc = IndexRunService(
            self.user_id,
            self.thread_id,
            self.agent_id,
            similarity_threshold=sim_threshold,
            arc_max_chars=arc_max_chars,
        )

        # 加载嵌入模型配置,避免重复读取
        self._embeddings_enabled = self._load_embeddings_config()

    def _load_embeddings_config(self) -> bool:
        """加载嵌入模型配置.

        Returns:
            embeddings.enabled 配置值,默认为 True

        """
        try:
            from src.config.inference_config import get_config

            inference_config = get_config()
            enabled = inference_config.embeddings.enabled
            logger.debug("📋 嵌入模型配置: enabled=%s", enabled)
            return enabled
        except Exception as e:
            logger.warning("⚠️ 读取嵌入模型配置失败,使用默认值(True): %s", e)
            return True

    async def add_conversation_round(self, conversation_data: ConversationData) -> None:
        """添加对话轮次并执行存储操作(对话完成后的统一触发点).

        主路径(同步 await, 4 个并行操作, 需在下一轮前落库):
        1. SQL数据库对话内容存储
        2. 向量数据库语义存储
        3. 对话索引生成
        4. 缓存更新与溢出处理

        后台路径(fire-and-forget, 不阻塞主流程, 读写缓存解耦):
        - 置顶记忆覆写(每轮): 主模型全文覆写单一块, _pinned_lock 串行化

        使用统一ConversationData数据源确保所有操作的数据一致性.
        提供容错机制,单个操作失败不影响整体流程.

        Args:
            conversation_data: 统一的对话数据结构,包含对话轮次的所有必要信息

        """
        logger.debug(
            f"💚 ConversationMemoryCore.add_conversation_round 被调用: {self.user_id}:{self.thread_id}",
        )

        try:
            # 置顶记忆处理 (fire-and-forget 覆写)
            messages_snapshot = conversation_data.metadata.get("_messages_snapshot")
            self._pinned_svc.on_conversation_round(
                conversation_data,
                messages_snapshot=messages_snapshot,
            )

            logger.debug(
                f"🔄 开始并行存储操作: {self.user_id}:{self.thread_id}, 轮次: {conversation_data.round_number}",
            )

            # 3. 三个无依赖并行操作(都使用相同 conversation_data), 需在下一轮前落库
            additional_tasks = [
                self._store_conversation_content(conversation_data),  # SQL对话内容存储
                self._store_vector_conversation(conversation_data),  # 向量存储
                self._update_conversation_cache(
                    conversation_data,
                ),  # 缓存更新
            ]

            # 并行执行所有操作,使用异常容错确保系统可用性
            results = await asyncio.gather(*additional_tasks, return_exceptions=True)

            # 检查并记录结果
            task_names = [
                "对话内容存储",
                "向量存储",
                "缓存更新",
            ]
            for i, result in enumerate(results):
                if isinstance(result, Exception):
                    logger.warning(f"{task_names[i]}失败(不影响主流程): {result}")
                else:
                    logger.debug(
                        f"{task_names[i]}完成: {self.user_id}:{self.thread_id}",
                    )

            # 索引生成: 依赖基础内容行已落库(任务1), 必须在并行存储完成后串行执行.
            # update_conversation_index 只 UPDATE 不 INSERT, 若并行竞态下行不存在
            # 则 topic/summary 丢失. 串行化消除竞态.
            try:
                await self._generate_conversation_index(conversation_data)
            except Exception as e:
                logger.warning(f"索引生成失败(不影响主流程): {e}")

            # 索引 run 检测(fire-and-forget): 在索引生成完成后触发, 此时本轮
            # topic+summary 已落库, run 检测可读本轮 summary 做 embedding 判连续性
            self._index_run_svc.on_conversation_round(conversation_data)

            logger.debug(
                f"✅ 已添加对话轮次并完成所有存储操作: {self.user_id}:{self.thread_id}, 轮次: {conversation_data.round_number}",
            )

        except Exception as e:
            logger.error(f"❌ 添加对话轮次时发生未预期错误: {type(e).__name__}: {e}")
            logger.error(f"❌ 错误详情: {e!s}")
            logger.error(f"❌ 用户ID: {self.user_id}, 线程ID: {self.thread_id}")
            logger.error(f"❌ 错误堆栈: {traceback.format_exc()}")
            # 4 个存储子任务已由 gather(return_exceptions=True) 隔离,
            # 能到达此处的均为编排层意外错误, 向上传播由调用方统一处理
            raise

    # ==================== 辅助方法 ====================

    @override
    def __str__(self) -> str:
        """字符串表示."""
        return f"ConversationMemoryCore(user_id={self.user_id}, thread_id={self.thread_id}, agent_id={self.agent_id})"

    @override
    def __repr__(self) -> str:
        """详细字符串表示."""
        return (
            f"ConversationMemoryCore(user_id='{self.user_id}', "
            f"thread_id='{self.thread_id}', "
            f"agent_id='{self.agent_id}')"
        )

    # ==================== 6个并行存储操作方法 ====================

    async def _store_conversation_content(
        self,
        conversation_data: ConversationData,
    ) -> None:
        """第一个并行操作:SQL数据库对话内容存储.

        将对话轮次的原始数据持久化到SQL数据库中,包括用户输入,助手回复,
        轮次号,时间戳,交互类型等信息.

        Args:
            conversation_data: 包含对话轮次完整信息的统一数据源

        """
        logger.debug(
            f"💾 开始对话内容存储: {conversation_data.user_id}:{conversation_data.thread_id}:{conversation_data.round_number}",
        )

        try:
            conv_service = await create_conversation_service(
                conversation_data.user_id,
                conversation_data.thread_id,
                agent_id=self.agent_id,
            )

            await conv_service.create_conversation(
                user_message=conversation_data.user_message,
                assistant_response=conversation_data.assistant_response,
                user_id=conversation_data.user_id,
                thread_id=conversation_data.thread_id,
                agent_id=conversation_data.agent_id,
                metadata=conversation_data.metadata,
                round_number=conversation_data.round_number,
            )

            logger.debug(
                f"✅ 对话内容存储完成: {conversation_data.user_id}:{conversation_data.thread_id}:{conversation_data.round_number}",
            )

        except Exception as e:
            logger.error("❌ 对话内容存储失败: %s", e)
            raise

    async def _store_vector_conversation(
        self,
        conversation_data: ConversationData,
    ) -> None:
        """第二个并行操作:向量数据库语义存储.

        将对话内容转换为向量表示并存储到向量数据库中,支持语义检索.
        包含用户输入和助手回复的组合内容,以及相关元数据.

        注意:如果 inference.embeddings.enabled=false,将跳过向量存储操作

        Args:
            conversation_data: 包含对话轮次完整信息的统一数据源

        """
        # 检查嵌入模型配置,如果禁用则跳过向量存储
        if not self._embeddings_enabled:
            logger.debug(
                f"📋 嵌入模型已禁用,跳过向量存储: {conversation_data.user_id}:{conversation_data.thread_id}:{conversation_data.round_number}",
            )
            return

        logger.debug(
            f"🔍 开始向量存储: {conversation_data.user_id}:{conversation_data.thread_id}:{conversation_data.round_number}",
        )

        try:
            # 1. 创建向量存储服务
            vector_service = create_vector_service(
                conversation_data.user_id,
                conversation_data.thread_id,
                agent_id=self.agent_id,
            )

            # 2. 添加对话内容到向量存储
            doc_id = await vector_service.add_conversation_content(conversation_data)

            logger.debug("✅ 向量存储完成: %s", doc_id)

        except Exception as e:
            logger.error("❌ 向量存储失败: %s", e)
            raise

    async def _generate_conversation_index(
        self,
        conversation_data: ConversationData,
    ) -> None:
        """第四个并行操作:智能分析并生成对话索引.

        使用内容分析器分析对话内容,生成主题,关键词,摘要等索引信息,
        并将索引信息持久化到数据库中.

        索引生成失败会被捕获并记录,不影响主流程.

        Args:
            conversation_data: 包含对话轮次完整信息的统一数据源

        """
        logger.debug(
            f"🧠 开始索引生成: {conversation_data.user_id}:{conversation_data.thread_id}:{conversation_data.round_number}",
        )

        try:
            # 1. 调用 SimpleContentAnalyzer 进行智能分析
            from src.inference.content_analyzer.simple_analyzer import (
                get_content_analyzer,
            )

            analyzer = get_content_analyzer()
            logger.debug(f"获取分析器成功: {analyzer.model_id}")
            logger.info(
                f"🔍 开始调用索引分析器,用户消息长度: {len(conversation_data.user_message)}",
            )
            logger.info(f"🔍 用户消息预览: {conversation_data.user_message[:100]}...")
            logger.info(
                f"🔍 助手回复预览: {conversation_data.assistant_response[:100]}...",
            )

            logger.debug("即将调用索引分析器")
            if hasattr(analyzer, "enable_conversation_index"):
                logger.debug(
                    f"分析器配置: enable_conversation_index = {analyzer.enable_conversation_index}",
                )
            if hasattr(analyzer, "model_id"):
                logger.debug(f"分析器模型: {analyzer.model_id}")

            index_result = await analyzer.analyze_conversation_index(
                conversation_data.user_message,
                conversation_data.assistant_response,
            )
            used_model = getattr(analyzer, "model_id", "unknown")
            logger.debug("索引分析调用成功返回")

            logger.info(
                f"索引分析完成 - 主题: {getattr(index_result, 'topic', '未知')}, 模型: {used_model}",
            )
            logger.debug("索引结果完整信息: %s", index_result)

            # 2. 索引元数据独立写入(只 UPDATE topic/summary, 不碰基础内容)
            conv_service = await create_conversation_service(
                conversation_data.user_id,
                conversation_data.thread_id,
                agent_id=self.agent_id,
            )

            topic = index_result.topic or "对话"
            summary = (
                index_result.summary
                or f"用户: {conversation_data.user_message[:50]}..."
            )

            updated = await conv_service.update_conversation_index(
                conversation_data.user_id,
                conversation_data.thread_id,
                conversation_data.round_number,
                topic=topic,
                summary=summary,
            )
            if not updated:
                logger.warning(
                    "索引元数据未写入: 基础内容行不存在(round=%s), "
                    "可能对话内容存储失败",
                    conversation_data.round_number,
                )

            logger.debug(
                f"索引生成完成: {conversation_data.user_id}:{conversation_data.thread_id}:{conversation_data.round_number}, 主题: {index_result.topic}",
            )

        except Exception as e:
            logger.error("索引生成失败: %s", e)
            logger.error(f"错误堆栈: {traceback.format_exc()}")
            logger.warning("索引生成失败但不影响主流程")

    async def _update_conversation_cache(
        self,
        conversation_data: ConversationData,
    ) -> None:
        """并行操作: 滚动更新主历史缓存(有界窗口).

        缓存以滚动有界窗口维护: 旧窗口 + 本轮 -> 裁到 total_char_budget 以内.
        缓存未初始化(冷启动由读路径种子化)时跳过.

        Args:
            conversation_data: 包含对话轮次完整信息的统一数据源

        """
        try:
            from src.storage.models.conversation import ConversationIndex

            from .cache import get_conversation, set_conversation
            from .history_budget import (
                resolve_total_char_budget,
                select_main_history_suffix,
            )

            cached = get_conversation(
                self.user_id,
                self.thread_id,
                agent_id=self.agent_id,
            )
            if not isinstance(cached, list):
                return

            new_index = ConversationIndex(
                round_number=conversation_data.round_number,
                user_message=conversation_data.user_message,
                assistant_response=conversation_data.assistant_response,
            )
            budget = resolve_total_char_budget(self.agent_config)
            bounded = select_main_history_suffix([*cached, new_index], budget)
            set_conversation(
                self.user_id,
                self.thread_id,
                bounded,
                agent_id=self.agent_id,
            )
            logger.debug(
                "缓存滚动: %s:%s round %d, 窗口 %d 轮",
                self.user_id,
                self.thread_id,
                conversation_data.round_number,
                len(bounded),
            )

        except Exception as e:
            logger.warning("缓存更新失败(不影响主流程): %s", e)
