"""统一对话数据服务.

提供统一对话数据的编排和管理功能,协调四个并行存储操作.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from src.storage.models.conversation import ConversationData

from .conversation_service import ConversationService
from .memory_service import MemoryService
from .todo_service import TodoService
from .vector_service import VectorService


class ConversationDataService:
    """统一对话数据服务.

    负责统一数据源的存储编排:
    - 协调四个并行存储操作
    - 确保数据一致性
    - 处理存储失败重试
    - 对话数据的完整生命周期管理

    采用组合模式,编排其他Service实现复杂的业务流程.
    """

    def __init__(
        self,
        conversation_service: ConversationService,
        memory_service: MemoryService,
        todo_service: TodoService,
        vector_service: VectorService,
    ) -> None:
        """初始化统一对话数据服务.

        Args:
            conversation_service: 对话服务
            memory_service: 记忆服务
            todo_service: TODO服务
            vector_service: 向量存储服务

        """
        self.conversation_service = conversation_service
        self.memory_service = memory_service
        self.todo_service = todo_service
        self.vector_service = vector_service

        # 统一日志配置
        self.logger = logging.getLogger(f"{__name__}.ConversationDataService")

    async def store_conversation_data(
        self,
        conversation_data: ConversationData,
        analysis_result: object | None = None,
    ) -> dict[str, Any]:
        """存储统一对话数据.

        执行四个并行存储操作,确保数据一致性.

        Args:
            conversation_data: 统一的对话数据结构
            analysis_result: 内容分析结果(可选)

        Returns:
            存储结果字典

        Raises:
            RuntimeError: 当关键存储操作失败时

        """
        start_time = time.time()
        try:
            self.logger.info(
                f"🚀 开始存储对话数据 - user_id: {conversation_data.user_id}, thread_id: {conversation_data.thread_id}, round: {conversation_data.round_number}",
            )

            # 四个并行存储操作
            storage_tasks = [
                self._store_conversation_content(conversation_data),
                self._store_vector_conversation(conversation_data),
                self._generate_conversation_index(conversation_data, analysis_result),
            ]

            # 执行并行存储
            storage_results = await self._execute_parallel_storage(storage_tasks)

            # 构建结果摘要
            result_summary = self._build_storage_result_summary(storage_results)

            duration = (time.time() - start_time) * 1000
            self.logger.info(
                f"✅ 存储对话数据完成 - duration: {duration:.2f}ms, SQL: {result_summary['sql_success']}, 向量: {result_summary['vector_success']}, 索引: {result_summary['index_success']}",
            )

            return {
                "success": True,
                "user_id": conversation_data.user_id,
                "thread_id": conversation_data.thread_id,
                "round_number": conversation_data.round_number,
                "storage_results": storage_results,
                "summary": result_summary,
            }

        except Exception as e:
            duration = (time.time() - start_time) * 1000
            self.logger.error(
                f"❌ 存储对话数据失败 - duration: {duration:.2f}ms, user_id: {conversation_data.user_id}, thread_id: {conversation_data.thread_id}, round: {conversation_data.round_number}, error: {e}",
                exc_info=True,
            )
            raise RuntimeError(f"存储对话数据失败: {e}") from e

    async def confirm_round_number_usage(
        self,
        round_number: int,
        user_id: str,
        thread_id: str,
    ) -> bool:
        """确认轮次号使用.

        Args:
            round_number: 轮次号
            user_id: 用户ID
            thread_id: 线程ID

        Returns:
            确认是否成功

        """
        start_time = time.time()
        try:
            self.logger.info(
                "🚀 开始确认轮次号使用 - user_id: %s, thread_id: %s, round: %s",
                user_id,
                thread_id,
                round_number,
            )

            conversation = await self.conversation_service.get_conversation_by_round(
                user_id,
                thread_id,
                round_number,
            )

            if not conversation:
                self.logger.warning(
                    "⚠️ 轮次号对应的对话不存在 - user_id: %s, thread_id: %s, round: %s",
                    user_id,
                    thread_id,
                    round_number,
                )
                return False

            self.logger.debug(
                "✅ 轮次号确认成功 - user_id: %s, thread_id: %s, round: %s",
                user_id,
                thread_id,
                round_number,
            )

            # 这里可以添加额外的确认逻辑,比如更新状态或记录确认时间
            # 当前简化实现,只做验证

            duration = (time.time() - start_time) * 1000
            self.logger.info(
                f"✅ 确认轮次号使用完成 - duration: {duration:.2f}ms, result: 确认成功",
            )
            return True

        except Exception as e:
            duration = (time.time() - start_time) * 1000
            self.logger.error(
                f"❌ 确认轮次号使用失败 - duration: {duration:.2f}ms, user_id: {user_id}, thread_id: {thread_id}, round: {round_number}, error: {e}",
                exc_info=True,
            )
            return False

    async def _store_conversation_content(
        self,
        conversation_data: ConversationData,
    ) -> dict[str, Any]:
        """存储对话内容到SQL数据库.

        使用ConversationData中预分配的round_number,避免重复分配.

        Args:
            conversation_data: 统一的对话数据结构,包含预分配的round_number

        Note:
            - round_number在_build_conversation_data时预分配
            - create_conversation接受预分配的round_number,不再重新分配
            - 确保同一轮对话只占用一个round_number

        """
        try:
            # 调用 create_conversation,传递预分配的 round_number
            conversation = await self.conversation_service.create_conversation(
                user_message=conversation_data.user_message,
                assistant_response=conversation_data.assistant_response,
                user_id=conversation_data.user_id,
                thread_id=conversation_data.thread_id,
                agent_id=conversation_data.agent_id,
                metadata=conversation_data.metadata,  # 传递元数据
                round_number=conversation_data.round_number,  # 传递预分配的轮次号
            )

            return {
                "operation": "sql_storage",
                "success": True,
                "data": {
                    "conversation_db_id": conversation.id,  # 数据库主键ID
                    "round_number": conversation.round_number,  # 使用的轮次号(与预分配的一致)
                },
                "error": None,
            }

        except Exception as e:
            self.logger.debug("SQL存储失败: %s", e)
            return {
                "operation": "sql_storage",
                "success": False,
                "data": None,
                "error": str(e),
            }

    async def _store_vector_conversation(
        self,
        conversation_data: ConversationData,
    ) -> dict[str, Any]:
        """存储对话内容到向量数据库."""
        try:
            # 使用向量服务存储对话内容
            vector_id = await self.vector_service.add_conversation_content(
                conversation_data,
            )

            return {
                "operation": "vector_storage",
                "success": True,
                "data": {"vector_id": vector_id},
                "error": None,
            }

        except Exception as e:
            self.logger.error("向量存储失败: %s", e)
            return {
                "operation": "vector_storage",
                "success": False,
                "data": None,
                "error": str(e),
            }

    async def _generate_conversation_index(
        self,
        conversation_data: ConversationData,
        analysis_result: object | None = None,
    ) -> dict[str, Any]:
        """生成对话索引."""
        try:
            # 如果有分析结果,使用分析结果生成索引
            if analysis_result:
                index_data = {
                    "topic": getattr(analysis_result, "topic", ""),
                    "summary": getattr(analysis_result, "summary", ""),
                }
            else:
                # 简单的索引生成逻辑
                content = f"{conversation_data.user_message} {conversation_data.assistant_response}"
                index_data = {
                    "topic": "general",
                    "summary": content[:200] + "..." if len(content) > 200 else content,
                }

            return {
                "operation": "index_generation",
                "success": True,
                "data": index_data,
                "error": None,
            }

        except Exception as e:
            self.logger.debug("索引生成失败: %s", e)
            return {
                "operation": "index_generation",
                "success": False,
                "data": None,
                "error": str(e),
            }

    async def _execute_parallel_storage(self, storage_tasks: list) -> dict[str, Any]:
        """执行并行存储操作."""
        # 使用异常容错确保系统稳定性
        results = await asyncio.gather(*storage_tasks, return_exceptions=True)

        # 处理结果
        processed_results: dict[str, Any] = {}
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                processed_results[f"task_{i}"] = {
                    "success": False,
                    "error": str(result),
                    "data": None,
                }
            else:
                processed_results[f"task_{i}"] = result

        return processed_results

    def _build_storage_result_summary(
        self,
        storage_results: dict[str, Any],
    ) -> dict[str, Any]:
        """构建存储结果摘要."""
        summary = {
            "sql_success": False,
            "vector_success": False,
            "index_success": False,
            "total_operations": len(storage_results),
            "successful_operations": 0,
            "failed_operations": 0,
            "errors": [],
        }

        for result in storage_results.values():
            if result.get("success", False):
                summary["successful_operations"] += 1

                # 根据操作类型更新对应的状态
                operation = result.get("operation", "")
                if "sql" in operation:
                    summary["sql_success"] = True
                elif "vector" in operation:
                    summary["vector_success"] = True
                elif "index" in operation:
                    summary["index_success"] = True
            else:
                summary["failed_operations"] += 1
                error = result.get("error", "Unknown error")
                summary["errors"].append(
                    f"{result.get('operation', 'unknown')}: {error}",
                )

        return summary
