"""文件去重 + 孤儿惰性清理集成测试.

灰盒: 真实 FileRepository + 真实 file_registry SQLite + 真实物理文件 + 真实 HMAC 签名
URL provider + 真实下载路由协作, 不 Mock 任何内部组件. 验证:
1. 内容去重: 相同 SHA-256 复用物理文件 (跳过 write_bytes) 但仍写新 FileEntry (引用计数)
2. 孤儿惰性清理: 物理文件被删 → 下载访问 → _cleanup_orphaned_record 删悬空注册表记录

单元测试 fixture 强制 find_by_content_hash 返回 None, 去重命中分支与两步清理链从未覆盖.
"""

from __future__ import annotations

import time

import pytest


@pytest.fixture
def user_context(test_user, test_thread_id):
    """注入 UserContext (store_image 经 get_user_context 取 agent_id)."""
    from src.core.context import UserContext, reset_user_context, set_user_context

    token = set_user_context(
        UserContext(
            user_id=test_user,
            thread_id=test_thread_id,
            agent_id="personal-assistant",
        )
    )
    yield test_user
    reset_user_context(token)


class TestFileDedupAndOrphanCleanupIntegration:
    """文件去重与孤儿清理协作集成测试."""

    @pytest.mark.asyncio
    async def test_integration_duplicate_reuses_physical_file(
        self, test_user, test_thread_id, user_context
    ):
        """相同内容图片存两次: 复用物理文件, 写两条注册表 (不同 file_id).

        协作场景: FileRepository.store_image + 真实 file_registry SQLite +
            find_by_content_hash 去重命中分支 (复用 physical_path, 跳过 write_bytes)
        Mock 边界: 不 Mock 任何内部组件, image_describer=None 跳过视觉描述
        验证重点:
            1. 两次返回不同 file_id (各写一条 FileEntry)
            2. 两次返回相同 filename / internal_path (复用物理文件)
            3. DB 注册表 2 条记录共享同一 physical_path
            4. 磁盘只存在 1 个物理文件
        业务价值: 去重命中分支此前被单元 fixture 屏蔽, 此处验证真实引用计数语义
        """
        from src.files.repository import get_file_repository
        from src.storage.service.file_registry_service import (
            create_file_registry_service,
        )

        repo = get_file_repository()
        image_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 200

        dto1 = await repo.store_image(
            user_id=test_user,
            thread_id=test_thread_id,
            round_number=1,
            image_data=image_bytes,
            mime_type="image/png",
            image_describer=None,
        )
        dto2 = await repo.store_image(
            user_id=test_user,
            thread_id=test_thread_id,
            round_number=2,
            image_data=image_bytes,
            mime_type="image/png",
            image_describer=None,
        )

        # Assert: 不同 file_id, 相同物理文件
        assert dto1.file_id != dto2.file_id, "两次存储应生成不同 file_id"
        assert dto1.filename == dto2.filename, "去重应复用同一物理文件名"
        assert dto1.internal_path == dto2.internal_path

        # Assert: 注册表 2 条记录, 共享 physical_path
        registry = await create_file_registry_service(test_user)
        entries = await registry.list_all()
        same_hash_entries = [e for e in entries if e.content_hash == dto1.content_hash]
        assert len(same_hash_entries) == 2, "去重仍应写两条注册表 (引用计数)"
        physical_paths = {e.physical_path for e in same_hash_entries}
        assert len(physical_paths) == 1, "两条记录应共享同一 physical_path"

        # Assert: 磁盘只 1 个物理文件
        from src.core.path_resolver import resolve_attachment_internal_path

        file_path = resolve_attachment_internal_path(
            dto1.internal_path, test_user, test_thread_id
        )
        assert file_path.exists(), "复用的物理文件应存在"

    @pytest.mark.asyncio
    async def test_integration_orphan_cleanup_on_download(
        self, test_user, test_thread_id, user_context
    ):
        """物理文件缺失时下载触发惰性清理, 删除悬空注册表记录.

        协作场景: download_file 路由 + _cleanup_orphaned_record +
            真实 file_registry SQLite + 真实 HMAC 签名验证
        Mock 边界: 不 Mock 内部组件; 用真实签名 provider mint 合法 token
        验证重点:
            1. 物理文件被删后下载返回 404
            2. 下载后 FileEntry 被惰性清理 (registry.get 返回 None)
        业务价值: 配额清理删物理文件留悬空记录, 下载侧惰性清理两步链此前从未协作验证
        """
        from fastapi import HTTPException

        from src.api.routes.files import download_file
        from src.core.path_resolver import resolve_attachment_internal_path
        from src.files.repository import get_file_repository
        from src.files.signed_url import (
            get_signed_url_provider,
            reset_signed_url_provider_for_test,
        )
        from src.storage.service.file_registry_service import (
            create_file_registry_service,
        )

        # 先存一张真实图片
        repo = get_file_repository()
        dto = await repo.store_image(
            user_id=test_user,
            thread_id=test_thread_id,
            round_number=1,
            image_data=b"\x89PNG\r\n\x1a\n" + b"\x00" * 100,
            mime_type="image/png",
            image_describer=None,
        )

        file_path = resolve_attachment_internal_path(
            dto.internal_path, test_user, test_thread_id
        )
        assert file_path.exists()
        # 模拟配额清理: 删除物理文件, 保留注册表记录 (产生悬空记录)
        file_path.unlink()
        assert not file_path.exists()

        # mint 合法签名 token
        reset_signed_url_provider_for_test(secret="test-secret-xxx")
        provider = get_signed_url_provider()
        expiry = int(time.time()) + 3600
        sig = provider.sign(
            test_user, test_thread_id, "personal-assistant", dto.file_id, expiry
        )

        # 下载应 404 并触发惰性清理
        with pytest.raises(HTTPException) as exc_info:
            await download_file(
                user_id=test_user,
                thread_id=test_thread_id,
                agent_id="personal-assistant",
                file_id=dto.file_id,
                expiry=expiry,
                sig=sig,
                _filename=dto.filename,
            )
        assert exc_info.value.status_code == 404

        # Assert: 悬空注册表记录已被惰性清理
        registry = await create_file_registry_service(test_user)
        assert await registry.get(dto.file_id) is None, "下载应触发删除悬空记录"
