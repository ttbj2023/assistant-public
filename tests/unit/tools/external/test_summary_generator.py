"""summary_generator 单元测试.

验证 export_document 后台摘要链路: LLM 摘要完成后重写 document_meta.summary
并重组 .desc.md 为 摘要+原文 统一结构.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.tools.external.export_document.summary_generator import (
    update_document_meta_summary,
)


@pytest.mark.asyncio
async def test_update_summary_rewrites_desc_with_original():
    """后台摘要完成: desc 重写为 摘要+分隔符+GFM原文 统一结构."""
    mock_entry = MagicMock()
    mock_entry.document_meta = '{"summary": "临时摘要", "title": "报告"}'

    mock_registry = AsyncMock()
    mock_registry.get.return_value = mock_entry

    with (
        patch(
            "src.storage.service.file_registry_service.create_file_registry_service",
            return_value=mock_registry,
        ),
        patch("src.files.desc_writer.write_desc") as mock_write_desc,
    ):
        await update_document_meta_summary(
            "abc12345",
            "LLM 最终摘要",
            user_id="u1",
            thread_id="t1",
            agent_id="a1",
            gfm_content="# 标题\n\n正文",
        )

    # document_meta.summary 被覆盖
    import json

    meta = json.loads(mock_entry.document_meta)
    assert meta["summary"] == "LLM 最终摘要"

    # desc 重写为 摘要 + 分隔符 + GFM 原文
    from src.files.desc_writer import split_desc

    written = mock_write_desc.call_args.args[2]
    summary, original = split_desc(written)
    assert summary == "LLM 最终摘要"
    assert original == "# 标题\n\n正文"


@pytest.mark.asyncio
async def test_update_summary_also_updates_db_brief():
    """后台摘要完成: DB brief 同步更新为摘要截断, 不再停留首段提取."""
    mock_entry = MagicMock()
    mock_entry.document_meta = '{"summary": "临时摘要"}'
    mock_entry.brief = "PDF导出: report.pdf (1.2MB)"

    mock_registry = AsyncMock()
    mock_registry.get.return_value = mock_entry

    with (
        patch(
            "src.storage.service.file_registry_service.create_file_registry_service",
            return_value=mock_registry,
        ),
        patch("src.files.desc_writer.write_desc"),
    ):
        await update_document_meta_summary(
            "abc12345",
            "季度营收分析与展望",
            user_id="u1",
            thread_id="t1",
            agent_id="a1",
        )

    assert mock_entry.brief == "季度营收分析与展望"
    mock_registry.upsert.assert_awaited_once()


@pytest.mark.asyncio
async def test_update_summary_truncates_long_brief_to_200():
    """超长摘要截断到 200 字符作为 brief."""
    long_summary = "长" * 350

    mock_entry = MagicMock()
    mock_entry.document_meta = '{"summary": ""}'
    mock_entry.brief = "旧brief"

    mock_registry = AsyncMock()
    mock_registry.get.return_value = mock_entry

    with (
        patch(
            "src.storage.service.file_registry_service.create_file_registry_service",
            return_value=mock_registry,
        ),
        patch("src.files.desc_writer.write_desc"),
    ):
        await update_document_meta_summary(
            "abc12345",
            long_summary,
            user_id="u1",
            thread_id="t1",
            agent_id="a1",
        )

    assert mock_entry.brief == long_summary[:200]


@pytest.mark.asyncio
async def test_update_summary_without_gfm_content_writes_summary_only():
    """未传 gfm_content (旧签名兼容): 仅写摘要, 不含原文区."""
    mock_entry = MagicMock()
    mock_entry.document_meta = '{"summary": ""}'

    mock_registry = AsyncMock()
    mock_registry.get.return_value = mock_entry

    with (
        patch(
            "src.storage.service.file_registry_service.create_file_registry_service",
            return_value=mock_registry,
        ),
        patch("src.files.desc_writer.write_desc") as mock_write_desc,
    ):
        await update_document_meta_summary(
            "abc12345",
            "只有摘要",
            user_id="u1",
            thread_id="t1",
            agent_id="a1",
        )

    from src.files.desc_writer import split_desc

    written = mock_write_desc.call_args.args[2]
    summary, original = split_desc(written)
    assert summary == "只有摘要"
    assert original == ""
