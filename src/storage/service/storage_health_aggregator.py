"""存储层健康检查聚合器.

提供统一的存储层健康检查接口,聚合所有存储服务的健康状态.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from .conversation_service import ConversationService
from .memory_service import MemoryService
from .todo_service import TodoService
from .vector_service import VectorService

logger = logging.getLogger(__name__)

HEALTH_CHECK_TIMEOUT_PER_SERVICE = 30


class StorageHealthAggregator:
    """存储层健康检查聚合器.

    负责聚合所有存储服务的健康状态,提供统一的健康检查接口.
    """

    def __init__(
        self,
        conversation_service: ConversationService,
        todo_service: TodoService,
        memory_service: MemoryService,
        vector_service: VectorService,
    ) -> None:
        """初始化存储健康检查聚合器.

        Args:
            conversation_service: 对话服务
            todo_service: TODO服务
            memory_service: 记忆服务
            vector_service: 向量服务

        """
        self.conversation_service = conversation_service
        self.todo_service = todo_service
        self.memory_service = memory_service
        self.vector_service = vector_service
        self.logger = logging.getLogger(f"{__name__}.StorageHealthAggregator")

    async def check_all_services_health(self) -> dict[str, Any]:
        """检查所有存储服务的健康状态.

        Returns:
            包含所有服务健康状态的字典

        """
        start_time = time.time()

        try:
            self.logger.info("🔍 开始存储层健康检查")

            # 并行检查所有服务的健康状态
            import asyncio

            health_tasks = [
                self.conversation_service.health_check(),
                self.todo_service.health_check(),
                self.memory_service.health_check(),
                self.vector_service.health_check(),
            ]

            # 并行执行健康检查
            health_results = await asyncio.gather(*health_tasks, return_exceptions=True)

            # 处理结果
            services_status: dict[str, Any] = {}
            error_count = 0
            unhealthy_count = 0
            degraded_count = 0

            service_names = [
                "conversation_service",
                "todo_service",
                "memory_service",
                "vector_service",
            ]

            for _i, (service_name, result) in enumerate(
                zip(service_names, health_results, strict=False),
            ):
                if isinstance(result, Exception):
                    services_status[service_name] = {
                        "status": "unhealthy",
                        "error": str(result),
                        "timestamp": time.time(),
                    }
                    error_count += 1
                    unhealthy_count += 1
                    self.logger.error("❌ %s健康检查异常: %s", service_name, result)
                else:
                    services_status[service_name] = result
                    if result.get("status") == "unhealthy":
                        unhealthy_count += 1
                    elif result.get("status") == "degraded":
                        degraded_count += 1

            # 计算整体状态
            if unhealthy_count > 0:
                overall_status = "unhealthy"
            elif degraded_count > 0 or error_count > 0:
                overall_status = "degraded"
            else:
                overall_status = "healthy"

            duration_ms = (time.time() - start_time) * 1000

            # 构建聚合结果
            result = {
                "status": overall_status,
                "timestamp": time.time(),
                "duration_ms": round(duration_ms, 2),
                "services": services_status,
                "summary": {
                    "total_services": len(service_names),
                    "healthy": len([
                        s
                        for s in services_status.values()
                        if s.get("status") == "healthy"
                    ]),
                    "degraded": degraded_count,
                    "unhealthy": unhealthy_count,
                    "errors": error_count,
                },
                "aggregation_info": {
                    "parallel_execution": True,
                    "timeout_per_service": HEALTH_CHECK_TIMEOUT_PER_SERVICE,
                    "services_checked": service_names,
                },
            }

            self.logger.info(
                f"✅ 存储层健康检查完成 - 状态: {overall_status}, "
                f"用时: {duration_ms:.2f}ms, "
                f"健康: {result['summary']['healthy']}, "
                f"降级: {result['summary']['degraded']}, "
                f"异常: {result['summary']['unhealthy'] + result['summary']['errors']}",
            )

            return result

        except Exception as e:
            duration_ms = (time.time() - start_time) * 1000
            error_msg = f"存储层健康检查聚合失败: {e}"
            self.logger.error(f"❌ {error_msg}, {duration_ms:.2f}ms", exc_info=True)

            return {
                "status": "unhealthy",
                "timestamp": time.time(),
                "duration_ms": round(duration_ms, 2),
                "services": {},
                "summary": {
                    "total_services": 0,
                    "healthy": 0,
                    "degraded": 0,
                    "unhealthy": 0,
                    "errors": 1,
                },
                "aggregation_info": {"parallel_execution": False, "error": str(e)},
                "error": str(e),
            }


__all__ = ["StorageHealthAggregator"]
