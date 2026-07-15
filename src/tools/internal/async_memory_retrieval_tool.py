"""异步记忆检索工具 - 独立异步实现.

基于异步架构的记忆检索工具,支持对话历史的智能检索.
"""

from __future__ import annotations

import logging
from typing import Any, override

from pydantic import BaseModel, ConfigDict, Field

from src.core.validation.security_decorators import secure_tool_params
from src.storage.service import (
    create_retrieval_service,
)
from src.tools.shared.base_internal_tool import BaseInternalTool

logger = logging.getLogger(__name__)


class MemorySearchRequest(BaseModel):
    """记忆检索参数模型 (Strict模式兼容)."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"additionalProperties": False},
    )

    query: str = Field(
        ...,
        description="搜索关键词或问题, 向量检索跨语言, 专有名词建议保留原文",
    )
    time_filter: str = Field(
        default="",
        description="时间过滤: 'yesterday', 'last_week', '2024-01-15', '2024-01-01_to_2024-01-31'",
    )
    max_results: int = Field(
        default=3,
        ge=1,
        le=50,
        description="返回结果数量, 默认3条, 最多50条",
    )
    round_start: int | None = Field(
        default=None,
        ge=1,
        description="轮次区间起始(包含), 与 round_end 配合限定搜索范围, 用于索引区下钻",
    )
    round_end: int | None = Field(
        default=None,
        ge=1,
        description="轮次区间结束(包含), 与 round_start 配合限定搜索范围",
    )


class AsyncMemoryRetrievalTool(BaseInternalTool):
    """异步对话历史检索工具."""

    name: str = "search_memories"
    description: str = """搜索历史对话记录和记忆内容, 返回概览钩子 [轮X] topic: summary.

向量语义检索支持跨语言, 单一语言通常即可命中.
对于专有名词/技术术语(如 decorator/Kubernetes), 建议保留原文以提升精确匹配.
需要完整原文时, 用返回的轮次号调用 get_round_detail.

示例: {"query": "项目进度", "time_filter": "last_week", "max_results": 5}
"""
    args_schema: type[MemorySearchRequest] = MemorySearchRequest

    _retrieval_service: Any

    def __init__(self, user_id: str, thread_id: str, **kwargs: Any) -> None:
        """初始化异步记忆检索工具."""
        if not user_id or not user_id.strip():
            raise ValueError("用户ID不能为空")

        if not thread_id or not thread_id.strip():
            raise ValueError("线程ID不能为空")

        super().__init__(user_id, thread_id, **kwargs)

        self._retrieval_service = None

    async def _get_service(self) -> Any:
        """获取检索服务实例(lazy-init + 缓存)."""
        if self._retrieval_service is not None:
            return self._retrieval_service

        service = await create_retrieval_service(
            user_id=self.user_id,
            thread_id=self.thread_id,
            agent_id=self.agent_id,
            enable_sql_search=True,
            max_results=3,
        )
        self._retrieval_service = service
        logger.info(
            f"AsyncMemoryRetrievalTool 初始化完成: {self.user_id}/{self.thread_id} "
            f"(使用检索服务架构)",
        )
        return service

    @override
    def _run(
        self,
        query: str,
        time_filter: str = "",
        max_results: int = 3,
        round_start: int | None = None,
        round_end: int | None = None,
    ) -> str:  # type: ignore[override]
        """同步执行方法 - 在同步环境中安全运行异步操作."""
        try:
            from src.utils.async_utils import run_async_in_sync_context

            return run_async_in_sync_context(
                self._arun,
                query,
                time_filter,
                max_results,
                round_start,
                round_end,
            )

        except Exception as e:
            logger.error("❌ 同步包装器执行失败: %s", e)
            return f"检索失败: {e!s}"

    @override
    @secure_tool_params()
    async def _arun(
        self,
        query: str,
        time_filter: str = "",
        max_results: int = 3,
        round_start: int | None = None,
        round_end: int | None = None,
    ) -> str:
        """异步执行记忆检索 - 基于双路检索架构."""
        try:
            # 验证查询参数
            if not query or not query.strip():
                raise ValueError("查询字符串不能为空")

            # 验证max_results参数
            if max_results <= 0:
                raise ValueError("max_results必须大于0")

            if max_results > 50:
                raise ValueError("max_results不能超过50")

            await self._get_service()

            if self._retrieval_service:
                # 使用检索服务进行统一检索
                logger.info(
                    "🔍 开始服务化检索: query='%s', time_filter='%s', max_results=%s",
                    query,
                    time_filter,
                    max_results,
                )
                try:
                    # 组装 round_range (round_start/round_end 均提供时生效)
                    round_range = None
                    if round_start is not None and round_end is not None:
                        round_range = (round_start, round_end)

                    # 有任何过滤条件(time_filter 或 round_range)时走 search_with_filters
                    has_filters = bool(time_filter and time_filter.strip()) or (
                        round_range is not None
                    )
                    if has_filters:
                        documents = await self._retrieval_service.search_with_filters(
                            query=query,
                            time_filter=time_filter,
                            max_results=max_results,
                            round_range=round_range,
                        )
                    else:
                        documents = await self._retrieval_service.search_conversations(
                            query=query,
                            max_results=max_results,
                        )
                    results = self._format_documents_to_results(documents)
                except Exception as e:
                    logger.warning("⚠️ 服务化检索失败,降级到基础检索: %s", e)
                    documents = await self._retrieval_service.search_conversations(
                        query=query,
                        max_results=max_results,
                    )
                    results = self._format_documents_to_results(documents)
            else:
                raise RuntimeError("检索服务不可用:无法初始化检索服务")

            # 构造返回结果
            import json

            result = {
                "success": True,
                "message": f"检索完成: '{query}'",
                "results": results,
                "total_count": len(results),
            }

            return json.dumps(result, ensure_ascii=False, indent=2)

        except Exception as e:
            logger.error("❌ 异步记忆检索失败: %s", e)
            import json

            return json.dumps(
                {
                    "success": False,
                    "message": f"检索失败: {e!s}",
                    "error": str(e),
                },
                ensure_ascii=False,
            )

    def _format_documents_to_results(
        self,
        documents: list[Any],
    ) -> list[dict[str, Any]]:
        """格式化文档为结果格式."""
        max_content_chars = 2000
        results = []
        for i, doc in enumerate(documents):
            content = doc.page_content or ""
            if len(content) > max_content_chars:
                content = content[:max_content_chars] + "... [已截断]"
            results.append({
                "content": content,
                "timestamp": doc.metadata.get("timestamp", "unknown"),
                "round_number": doc.metadata.get("round_number", i + 1),
                "relevance": doc.metadata.get("relevance_score"),
                "metadata": doc.metadata,
            })
        return results

    async def aget_relevant_documents(self, query: str) -> list[Any]:
        """异步获取相关文档 - 基于检索服务架构.

        Args:
            query: 查询字符串

        Returns:
            相关文档列表

        """
        try:
            await self._get_service()

            if self._retrieval_service:
                try:
                    return await self._retrieval_service.search_conversations(
                        query,
                    )
                except Exception as e:
                    logger.warning("⚠️ 检索服务失败, 返回空结果: %s", e)
                    return []
            else:
                logger.warning("⚠️ 检索服务不可用,返回空结果")
                return []

        except Exception as e:
            logger.error("❌ 异步获取相关文档失败: %s", e)
            return []

    async def ahealth_check(self) -> dict[str, Any]:
        """异步健康检查.

        Returns:
            健康检查结果

        """
        try:
            await self._get_service()

            if self._retrieval_service:
                health = await self._retrieval_service.health_check()
                health.update({
                    "tool_user_id": self.user_id,
                    "tool_thread_id": self.thread_id,
                    "tool_note": "使用检索服务架构",
                })
                return health
            return {
                "overall": False,
                "status": "unhealthy",
                "error": "检索服务未初始化",
                "user_id": self.user_id,
                "thread_id": self.thread_id,
            }

        except Exception as e:
            logger.error("❌ 异步健康检查失败: %s", e)
            return {
                "overall": False,
                "status": "unhealthy",
                "error": str(e),
                "user_id": self.user_id,
                "thread_id": self.thread_id,
            }

    async def aupdate_config(self) -> bool:
        """异步更新配置.

        max_results 现在由 MemorySearchRequest 每次调用时传入,
        不再需要实例级配置更新.

        Returns:
            是否更新成功

        """
        return True


__all__ = [
    "AsyncMemoryRetrievalTool",
    "MemorySearchRequest",
]
