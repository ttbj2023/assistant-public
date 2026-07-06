"""存储配额服务 — 用户级配额管理与自动清理 (基于 FileRegistry 单表).

核心职责:
- 计算用户文件存储总大小 (按 physical_path 去重, 同内容文件不重复计算)
- 超限时按最早创建时间自动清理
- 清理尊重引用计数 (content_hash 实时查询), 物理文件仅在引用归零后删除

清理原子性 (以 FileEntry 为单位, 三步):
    删 .desc.md (file_id 独有) → 删 FileEntry 记录 → count_by_content_hash
    归零则删物理文件 (单库事务保证一致性, 无跨库悬空).

设计约定:
- 用户级: 每个用户独立 file_registry.db
- 清理触发: 每次新文件保存后检查
- 清理策略: 按 created_at 升序清理, 直到低于目标值
"""

from __future__ import annotations

import logging

from src.config.storage_config import get_config as get_storage_config
from src.core.path_resolver import get_user_path_resolver
from src.files.desc_writer import delete_desc
from src.storage.service.file_registry_service import (
    create_file_registry_service,
)

logger = logging.getLogger(__name__)


class StorageQuotaService:
    """存储配额服务 — 用户级配额管理与自动清理."""

    def __init__(self, user_id: str) -> None:
        self.user_id = user_id

    async def check_and_cleanup(self) -> None:
        """检查配额并在超限时自动清理."""
        config = get_storage_config().file_store

        if not config.quota_check_enabled:
            return

        max_bytes = config.max_user_storage_mb * 1024 * 1024
        target_bytes = config.cleanup_target_mb * 1024 * 1024

        registry = await create_file_registry_service(self.user_id)
        current_size = await registry.get_total_unique_size()

        if current_size <= max_bytes:
            logger.debug(
                "📊 配额检查通过: user=%s, %dMB / %dMB",
                self.user_id,
                current_size // (1024 * 1024),
                config.max_user_storage_mb,
            )
            return

        logger.warning(
            "⚠️ 配额超限: user=%s, %dMB > %dMB, 开始清理到 %dMB",
            self.user_id,
            current_size // (1024 * 1024),
            config.max_user_storage_mb,
            config.cleanup_target_mb,
        )
        await self._cleanup_until_target(registry, target_bytes)

    async def _cleanup_until_target(
        self,
        registry: object,
        target_bytes: int,
    ) -> None:
        """按创建时间从早到晚清理文件直到低于目标值.

        清理流程 (每条 FileEntry 记录):
        1. 删除描述文件 .desc.md (file_id 独有, 安全删)
        2. 删除 FileEntry 记录
        3. 实时查询 content_hash 引用数, 归零则删物理文件

        """
        resolver = get_user_path_resolver()
        user_base = resolver.get_user_base_path(self.user_id)

        entries = await registry.list_ordered_by_created()
        freed = 0

        for entry in entries:
            current_size = await registry.get_total_unique_size()
            if current_size <= target_bytes:
                break

            # 1. 删描述文件 (.desc.md, 与 file_id 一一对应)
            delete_desc(self.user_id, entry.file_id)

            # 2. 删 FileEntry 记录
            await registry.delete(entry.file_id)

            # 3. content_hash 引用归零则删物理文件
            if entry.content_hash:
                ref_count = await registry.count_references(entry.content_hash)
                if ref_count == 0:
                    file_path = user_base / entry.physical_path
                    try:
                        if file_path.exists():
                            file_path.unlink()
                            logger.info(
                                "🗑️ 删除物理文件: %s (%d bytes)",
                                entry.physical_path,
                                entry.file_size,
                            )
                    except OSError as e:
                        logger.warning("⚠️ 删除文件失败: %s, %s", file_path, e)

            freed += entry.file_size or 0

        if freed > 0:
            final_size = await registry.get_total_unique_size()
            logger.info(
                "✅ 清理完成: user=%s, 释放 %dMB, 当前 %dMB",
                self.user_id,
                freed // (1024 * 1024),
                final_size // (1024 * 1024),
            )


def get_storage_quota_service(user_id: str) -> StorageQuotaService:
    """创建用户级配额服务.

    Args:
        user_id: 用户ID

    Returns:
        存储配额服务实例

    """
    return StorageQuotaService(user_id)
