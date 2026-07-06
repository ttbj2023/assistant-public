"""chat_helpers 后台图片描述生成集成测试.

灰盒: 真实 background_generate_description → 真实 ImageDescriber (响应解析) →
真实 FileRepository.update_description (真实 file_registry SQLite + .desc.md 落盘) 协作,
仅 Mock 外部视觉 LLM 调用 (invoke_with_fallback). 该 fire-and-forget 后台函数此前
任意层级零覆盖, 此处验证完整链路与静默失败契约.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest


async def _seed_file_entry(user_id: str, file_id: str) -> None:
    """预置一条 FileEntry 到真实 file_registry SQLite."""
    from src.storage.models.file_registry import FileEntry
    from src.storage.service.file_registry_service import (
        create_file_registry_service,
    )

    registry = await create_file_registry_service(user_id)
    await registry.upsert(
        FileEntry(
            file_id=file_id,
            file_type="image",
            physical_path="thread/shared/files/images/fake.png",
            desc_path=f"files/desc/{file_id}.desc.md",
            filename="fake.png",
            brief="占位",
            file_format="png",
            file_size=100,
            content_hash=None,
            round_number=1,
            owner_thread_id="thread",
            owner_agent_id="personal-assistant",
        )
    )


@pytest.fixture
def user_context(test_user, test_thread_id):
    """注入 UserContext (update_description 经 get_user_context 取 user_id)."""
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


class TestBackgroundGenerateDescriptionIntegration:
    """后台图片描述生成协作集成测试."""

    @pytest.mark.asyncio
    async def test_integration_success_writes_brief_and_desc_file(
        self, test_user, user_context, tmp_path
    ):
        """视觉 LLM 返回合法 JSON → 真实解析 → 真实 update_description 写 DB + .desc.md.

        协作场景: background_generate_description + ImageDescriber (响应解析真实) +
            FileRepository.update_description (真实 file_registry SQLite + write_desc 落盘)
        Mock 边界: 仅 Mock 外部视觉 LLM (invoke_with_fallback 返回 JSON content),
            ImageDescriber 解析逻辑 / FileRepository / SQLite 均为真实组件
        验证重点:
            1. DB 中 FileEntry.brief 更新为 LLM 返回的概要
            2. .desc.md 文件落盘且内容含详细描述
        业务价值: 多模态对话后台补全描述链路此前零覆盖, 此处为唯一真实协作验证
        """
        from src.files.desc_writer import desc_abs_path
        from src.session.chat_helpers import background_generate_description
        from src.storage.service.file_registry_service import (
            create_file_registry_service,
        )

        file_id = "abc12345"
        await _seed_file_entry(test_user, file_id)

        # 视觉 LLM 边界注入: 返回带 .content 的 message (describer 读 response.content)
        mock_response = SimpleNamespace(
            content='{"brief": "测试概要", "detail": "这是详细画面描述"}',
        )

        # 一张可读的真实图片文件 (describe 会 read_bytes)
        image_path = tmp_path / "fake.png"
        image_path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 50)

        with patch(
            "src.inference.llm.model_loader.invoke_with_fallback",
            new=AsyncMock(return_value=mock_response),
        ):
            await background_generate_description(
                file_id=file_id,
                image_path=image_path,
                mime_type="image/png",
            )

        # Assert: DB brief 更新
        registry = await create_file_registry_service(test_user)
        entry = await registry.get(file_id)
        assert entry is not None
        assert entry.brief == "测试概要"

        # Assert: .desc.md 落盘
        desc_path = desc_abs_path(test_user, file_id)
        assert desc_path.exists(), "详细描述应写入 .desc.md"
        assert "详细画面描述" in desc_path.read_text(encoding="utf-8")

    @pytest.mark.asyncio
    async def test_integration_downstream_failure_swallowed_silently(
        self, test_user, user_context, tmp_path
    ):
        """update_description 抛错时 background_generate_description 静默吞没 (fire-and-forget 契约).

        协作场景: background_generate_description 内部 update_description 失败时,
            fire-and-forget 应捕获异常仅记日志, 不向调用方传播
        Mock 边界: Mock 视觉 LLM + Mock FileRepository.update_description 抛异常
        验证重点: 函数正常返回 (不抛异常), 即使下游存储失败
        业务价值: 后台描述生成是 fire-and-forget, 失败不得影响主对话流程
        """
        from src.session.chat_helpers import background_generate_description

        file_id = "abc12345"
        await _seed_file_entry(test_user, file_id)

        mock_response = SimpleNamespace(
            content='{"brief": "概要", "detail": "描述"}',
        )
        image_path = tmp_path / "fake.png"
        image_path.write_bytes(b"\x89PNG\r\n\x1a\n")

        with (
            patch(
                "src.inference.llm.model_loader.invoke_with_fallback",
                new=AsyncMock(return_value=mock_response),
            ),
            patch(
                "src.files.repository.FileRepository.update_description",
                new=AsyncMock(side_effect=RuntimeError("DB 写入失败")),
            ),
        ):
            # 不应抛异常 (fire-and-forget 契约)
            result = await background_generate_description(
                file_id=file_id,
                image_path=image_path,
                mime_type="image/png",
            )

        assert result is None, "fire-and-forget 函数正常返回 None"
