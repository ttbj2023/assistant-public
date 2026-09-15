"""纯文本文档入库集成测试.

灰盒: 真实 FileRepository.store_document + 真实 file_registry SQLite +
真实物理文件 + 真实下载路由 (HMAC 签名), 不 Mock 内部组件. 验证:
1. 文档落盘 files/documents/ + desc=原文 (read_file 语义)
2. 内容去重: 相同文本复用物理文件
3. 下载路由可取回原文

单元测试 fixture 屏蔽了真实存储协作, 此处验证端到端语义.
"""

from __future__ import annotations

import pytest


@pytest.fixture
def user_context(test_user, test_thread_id):
    """注入 UserContext (store_document 经 get_user_context 取 agent_id)."""
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


class TestDocumentStoreIntegration:
    """纯文本文档入库协作集成测试."""

    @pytest.mark.asyncio
    async def test_integration_document_stored_desc_is_original(
        self, test_user, test_thread_id, user_context
    ):
        """文档入库: 物理文件 + 注册表 document 类型 + desc=原文."""
        from src.files.desc_writer import read_desc
        from src.files.repository import get_file_repository
        from src.storage.service.file_registry_service import (
            create_file_registry_service,
        )

        repo = get_file_repository()
        content = "# 会议纪要\n\n- 议题一\n- 议题二\n"

        dto = await repo.store_document(
            user_id=test_user,
            thread_id=test_thread_id,
            round_number=1,
            filename="会议纪要.md",
            content=content,
        )

        # 注册表: document 类型 + 可被 get 反查
        registry = await create_file_registry_service(test_user)
        entry = await registry.get(dto.file_id)
        assert entry is not None
        assert entry.file_type == "document"
        assert entry.physical_path.startswith(
            f"{test_thread_id}/shared/files/documents/"
        )
        assert entry.document_meta is not None

        # desc = 摘要 + 分隔符 + 原文 (统一结构)
        from src.files.desc_writer import split_desc

        summary, original = split_desc(read_desc(test_user, dto.file_id))
        assert summary, "摘要区应非空"
        assert original == content

        # 物理文件存在且内容一致
        from src.core.path_resolver import resolve_attachment_internal_path

        physical = resolve_attachment_internal_path(
            dto.internal_path, test_user, test_thread_id
        )
        assert physical.exists()
        assert physical.read_text(encoding="utf-8") == content

    @pytest.mark.asyncio
    async def test_integration_duplicate_document_reuses_physical_file(
        self, test_user, test_thread_id, user_context
    ):
        """相同内容文档存两次: 复用物理文件, 写两条注册表."""
        from src.files.repository import get_file_repository

        repo = get_file_repository()
        content = "重复内容" * 10

        dto1 = await repo.store_document(
            user_id=test_user,
            thread_id=test_thread_id,
            round_number=1,
            filename="a.md",
            content=content,
        )
        dto2 = await repo.store_document(
            user_id=test_user,
            thread_id=test_thread_id,
            round_number=2,
            filename="b.md",
            content=content,
        )

        assert dto1.file_id != dto2.file_id
        assert dto1.internal_path == dto2.internal_path, "去重应复用物理文件"

    @pytest.mark.asyncio
    async def test_integration_document_downloadable_via_signed_url(
        self, test_user, test_thread_id, user_context
    ):
        """文档可经签名 URL 下载, 内容为原文."""
        import time as time_mod

        from fastapi.testclient import TestClient

        from src.api.fastapi_app import app
        from src.files.repository import get_file_repository
        from src.files.signed_url import (
            get_signed_url_provider,
            reset_signed_url_provider_for_test,
        )

        reset_signed_url_provider_for_test(secret="test-secret-xxx")
        repo = get_file_repository()
        content = "# 下载测试\n正文"

        dto = await repo.store_document(
            user_id=test_user,
            thread_id=test_thread_id,
            round_number=1,
            filename="下载测试.md",
            content=content,
        )

        provider = get_signed_url_provider()
        expiry = int(time_mod.time()) + 3600
        sig = provider.sign(
            test_user, test_thread_id, "personal-assistant", dto.file_id, expiry
        )
        url = (
            f"/v1/files/dl/{test_user}/{test_thread_id}/personal-assistant/"
            f"{dto.file_id}/{expiry}/{sig}/{dto.filename}"
        )

        client = TestClient(app)
        resp = client.get(url)
        assert resp.status_code == 200
        assert resp.content.decode("utf-8") == content
