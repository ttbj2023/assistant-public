"""用量落库适配器 - 实现 inference.UsageSink 端口, 桥接 storage.UsageService.

依赖方向: storage 提供 UsageSink 适配器 (正向), inference 依赖端口抽象;
二者经组合根 (fastapi_app lifespan) 在运行时组装, 加载期无相互依赖.
"""

from __future__ import annotations

import logging

from src.core.types import UsageRecordCreate

logger = logging.getLogger(__name__)


class StorageUsageSink:
    """用量落库适配器 - 实现 inference.UsageSink 端口.

    将 inference 产出的用量记录经 UsageService 写入 usage 表.
    """

    async def record(self, data: UsageRecordCreate) -> None:
        from .service_factory import create_usage_service

        service = await create_usage_service(data.user_id)
        await service.record_usage(data)


__all__ = ["StorageUsageSink"]
