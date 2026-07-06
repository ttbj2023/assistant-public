"""StorageQuotaService 单元测试.

测试存储配额服务的核心业务逻辑 (基于 FileRegistry 单表), 覆盖:
- check_and_cleanup: 配额检查与自动清理
- _cleanup_until_target: 按时间排序清理 + 引用归零删物理文件
- get_storage_quota_service: 工厂
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.files.quota import (
    StorageQuotaService,
    get_storage_quota_service,
)


def _make_entry(
    file_id: str = "abc12345",
    content_hash: str = "a" * 64,
    physical_path: str = "thread1/shared/files/images/test.jpg",
    file_size: int = 1024,
    hours_ago: int = 0,
) -> MagicMock:
    """创建 mock FileEntry."""
    entry = MagicMock()
    entry.file_id = file_id
    entry.content_hash = content_hash
    entry.physical_path = physical_path
    entry.file_size = file_size
    entry.created_at = datetime.utcnow() - timedelta(hours=hours_ago)
    return entry


def _mock_registry(
    total_size: int = 0,
    entries: list | None = None,
    ref_count: int = 0,
) -> AsyncMock:
    """创建 mock FileRegistryService."""
    registry = AsyncMock()
    registry.get_total_unique_size = AsyncMock(return_value=total_size)
    registry.list_ordered_by_created = AsyncMock(return_value=entries or [])
    registry.delete = AsyncMock()
    registry.count_references = AsyncMock(return_value=ref_count)
    return registry


def _mock_config(max_mb=500, target_mb=400, quota=True):
    """创建 mock FileStoreConfig."""
    config = MagicMock()
    config.max_user_storage_mb = max_mb
    config.cleanup_target_mb = target_mb
    config.quota_check_enabled = quota
    return config


class TestCheckAndCleanup:
    """测试 check_and_cleanup - 配额检查与清理触发."""

    @pytest.mark.asyncio
    async def test_under_limit_no_cleanup(self):
        """未超限时不应触发清理."""
        service = StorageQuotaService("user1")
        mock_registry = _mock_registry(total_size=100 * 1024 * 1024)

        with (
            patch("src.files.quota.get_storage_config") as mock_get_config,
            patch(
                "src.files.quota.create_file_registry_service",
                return_value=mock_registry,
            ),
        ):
            mock_get_config.return_value.file_store = _mock_config()
            await service.check_and_cleanup()

        mock_registry.list_ordered_by_created.assert_not_called()

    @pytest.mark.asyncio
    async def test_over_limit_triggers_cleanup(self):
        """超限时应触发清理."""
        service = StorageQuotaService("user1")
        old_entry = _make_entry(file_size=200 * 1024 * 1024, hours_ago=24)
        mock_registry = _mock_registry(
            total_size=600 * 1024 * 1024,
            entries=[old_entry],
            ref_count=0,
        )

        with (
            patch("src.files.quota.get_storage_config") as mock_get_config,
            patch(
                "src.files.quota.create_file_registry_service",
                return_value=mock_registry,
            ),
            patch("src.files.quota.delete_desc"),
            patch("src.files.quota.get_user_path_resolver") as mock_resolver,
        ):
            mock_get_config.return_value.file_store = _mock_config()
            resolver = MagicMock()
            resolver.get_user_base_path.return_value = Path("/tmp/test")
            mock_resolver.return_value = resolver

            with patch.object(Path, "exists", return_value=False):
                await service.check_and_cleanup()

        mock_registry.delete.assert_awaited()

    @pytest.mark.asyncio
    async def test_quota_disabled_skips_check(self):
        """配额检查禁用时应跳过."""
        service = StorageQuotaService("user1")
        mock_registry = _mock_registry()

        with (
            patch("src.files.quota.get_storage_config") as mock_get_config,
            patch(
                "src.files.quota.create_file_registry_service",
                return_value=mock_registry,
            ),
        ):
            mock_get_config.return_value.file_store = _mock_config(quota=False)
            await service.check_and_cleanup()

        mock_registry.get_total_unique_size.assert_not_called()


class TestCleanupUntilTarget:
    """测试 _cleanup_until_target - 清理策略."""

    @pytest.mark.asyncio
    async def test_cleanup_stops_at_target(self):
        """清理应在达到目标大小时停止."""
        service = StorageQuotaService("user1")
        entry1 = _make_entry(
            file_id="e1",
            content_hash="a" * 64,
            file_size=100 * 1024 * 1024,
            hours_ago=48,
        )
        entry2 = _make_entry(
            file_id="e2",
            content_hash="b" * 64,
            file_size=100 * 1024 * 1024,
            hours_ago=24,
        )
        entry3 = _make_entry(
            file_id="e3",
            content_hash="c" * 64,
            file_size=100 * 1024 * 1024,
            hours_ago=1,
        )

        mock_registry = _mock_registry(entries=[entry1, entry2, entry3])
        # 循环: e1 -> 300>150 删; e2 -> 200>150 删; e3 -> 100<=150 停; 最终日志
        mock_registry.get_total_unique_size = AsyncMock(
            side_effect=[
                300 * 1024 * 1024,
                200 * 1024 * 1024,
                100 * 1024 * 1024,
                100 * 1024 * 1024,
            ],
        )

        with (
            patch("src.files.quota.delete_desc"),
            patch("src.files.quota.get_user_path_resolver") as mock_resolver,
        ):
            resolver = MagicMock()
            resolver.get_user_base_path.return_value = Path("/tmp/test")
            mock_resolver.return_value = resolver
            with patch.object(Path, "exists", return_value=False):
                await service._cleanup_until_target(mock_registry, 150 * 1024 * 1024)

        assert mock_registry.delete.call_count == 2

    @pytest.mark.asyncio
    async def test_cleanup_deletes_physical_file_when_ref_zero(self):
        """引用归零时应删除物理文件."""
        service = StorageQuotaService("user1")
        entry = _make_entry(
            physical_path="thread1/shared/files/images/old.jpg",
            file_size=100 * 1024 * 1024,
            hours_ago=48,
        )
        mock_registry = _mock_registry(entries=[entry], ref_count=0)
        mock_registry.get_total_unique_size = AsyncMock(
            side_effect=[100 * 1024 * 1024, 0],
        )

        tmp_path = Path("/tmp/test")
        with (
            patch("src.files.quota.delete_desc"),
            patch("src.files.quota.get_user_path_resolver") as mock_resolver,
        ):
            resolver = MagicMock()
            resolver.get_user_base_path.return_value = tmp_path
            mock_resolver.return_value = resolver
            with (
                patch.object(Path, "exists", return_value=True),
                patch.object(Path, "unlink") as mock_unlink,
            ):
                await service._cleanup_until_target(mock_registry, 50 * 1024 * 1024)

        mock_unlink.assert_called_once()

    @pytest.mark.asyncio
    async def test_cleanup_skips_physical_file_when_ref_nonzero(self):
        """引用未归零时不应删除物理文件."""
        service = StorageQuotaService("user1")
        entry = _make_entry(file_size=100 * 1024 * 1024, hours_ago=48)
        mock_registry = _mock_registry(entries=[entry], ref_count=1)  # 仍有引用
        mock_registry.get_total_unique_size = AsyncMock(
            side_effect=[100 * 1024 * 1024, 0],
        )

        with (
            patch("src.files.quota.delete_desc"),
            patch("src.files.quota.get_user_path_resolver") as mock_resolver,
        ):
            resolver = MagicMock()
            resolver.get_user_base_path.return_value = Path("/tmp/test")
            mock_resolver.return_value = resolver
            with patch.object(Path, "unlink") as mock_unlink:
                await service._cleanup_until_target(mock_registry, 50 * 1024 * 1024)

        mock_unlink.assert_not_called()


class TestGetService:
    """测试 get_storage_quota_service."""

    def test_different_users_return_different_instances(self):
        """不同用户应返回不同实例."""
        s1 = get_storage_quota_service("user1")
        s2 = get_storage_quota_service("user2")
        assert s1 is not s2
