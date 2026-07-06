"""Service健康检查混入类.

为存储层Service提供统一的健康检查接口和返回格式.
"""

from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)


class ServiceHealthCheckMixin(ABC):
    """Service健康检查混入类.

    提供统一的健康检查接口和标准化的返回格式.
    所有Service类都应该混入此类以实现健康检查功能.
    """

    def __init__(self) -> None:
        """初始化健康检查混入."""
        self._service_name = self.__class__.__name__
        self._last_health_check: datetime | None = None

    async def health_check(self) -> dict[str, Any]:
        """统一健康检查接口.

        子类应该重写 `_check_service_health()` 方法来实现具体的健康检查逻辑.

        Returns:
            标准格式的健康检查结果

        """
        start_time = time.time()

        try:
            # 调用子类实现的具体健康检查逻辑
            service_health = await self._check_service_health()

            duration_ms = (time.time() - start_time) * 1000
            self._last_health_check = datetime.now(UTC)

            # 构建标准返回格式
            result = {
                "status": service_health.get("status", "healthy"),
                "service_name": self._service_name,
                "last_check": self._last_health_check.isoformat(),
                "duration_ms": round(duration_ms, 2),
                "database_connected": service_health.get("database_connected", True),
                "statistics": service_health.get("statistics", {}),
                "error": service_health.get("error"),
                **service_health.get("additional_info", {}),
            }

            logger.debug(
                f"✅ {self._service_name}健康检查完成 - {result['status']}, {duration_ms:.2f}ms",
            )
            return result

        except Exception as e:
            duration_ms = (time.time() - start_time) * 1000
            error_msg = f"{self._service_name}健康检查失败: {e}"
            logger.error(f"❌ {error_msg}, {duration_ms:.2f}ms", exc_info=True)

            return {
                "status": "unhealthy",
                "service_name": self._service_name,
                "last_check": datetime.now(UTC).isoformat(),
                "duration_ms": round(duration_ms, 2),
                "database_connected": False,
                "statistics": {},
                "error": str(e),
                "additional_info": {},
            }

    @abstractmethod
    async def _check_service_health(self) -> dict[str, Any]:
        """检查服务健康状态.

        子类必须实现此方法来提供具体的健康检查逻辑.

        Returns:
            包含健康状态信息的字典,应包含:
            - status: "healthy" | "degraded" | "unhealthy"
            - database_connected: bool
            - statistics: dict[str, Any]
            - error: Optional[str]
            - additional_info: dict[str, Any]

        """
        # 子类实现具体逻辑
        return {
            "status": "healthy",
            "database_connected": True,
            "statistics": {},
            "error": None,
            "additional_info": {},
        }

    def _build_statistics(self, **stats: Any) -> dict[str, Any]:
        """构建统计信息字典.

        Args:
            **stats: 统计数据键值对

        Returns:
            格式化的统计信息字典

        """
        return {"timestamp": datetime.now(UTC).isoformat(), **stats}


__all__ = ["ServiceHealthCheckMixin"]
