"""FileRepository 单元测试.

从 tests/unit/storage/service/test_attachment_service.py 迁移.
覆盖 store_image / update_description / 文件扩展名映射 / 大小限制.
"""

from __future__ import annotations

import struct
import zlib
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.files.models import AttachmentDTO
from src.files.repository import FileRepository


@pytest.fixture
def mock_path_resolver():
    """Mock UserDataPathResolver."""
    resolver = MagicMock()
    resolver.get_shared_storage_path.return_value = Path(
        "/tmp/test_data/user1/thread1/shared/files/images",
    )
    resolver.get_thread_base_path.return_value = Path("/tmp/test_data/user1/thread1")
    resolver.base_path = Path("/tmp/test_data")
    resolver.get_user_base_path.return_value = Path("/tmp/test_data/user1")
    return resolver


@pytest.fixture
def repository(mock_path_resolver):
    """创建 FileRepository 实例, 替换 path_resolver."""
    with patch(
        "src.files.repository.UserDataPathResolver",
        return_value=mock_path_resolver,
    ):
        return FileRepository()


@pytest.fixture(autouse=True)
def _mock_quota():
    """自动 mock 配额服务, 避免影响现有测试."""
    with patch("src.files.quota.get_storage_quota_service") as mock_quota_getter:
        mock_quota = AsyncMock()
        mock_quota_getter.return_value = mock_quota
        yield


@pytest.fixture
def sample_image_data():
    """创建测试图片数据(1x1 PNG最小合法图片)."""
    signature = b"\x89PNG\r\n\x1a\n"
    ihdr_data = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    ihdr_crc = zlib.crc32(b"IHDR" + ihdr_data)
    ihdr = (
        struct.pack(">I", 13)
        + b"IHDR"
        + ihdr_data
        + struct.pack(">I", ihdr_crc & 0xFFFFFFFF)
    )

    raw_data = b"\x00\x00\x00\x00"
    compressed = zlib.compress(raw_data)
    idat_crc = zlib.crc32(b"IDAT" + compressed)
    idat = (
        struct.pack(">I", len(compressed))
        + b"IDAT"
        + compressed
        + struct.pack(">I", idat_crc & 0xFFFFFFFF)
    )

    iend_crc = zlib.crc32(b"IEND")
    iend = struct.pack(">I", 0) + b"IEND" + struct.pack(">I", iend_crc & 0xFFFFFFFF)

    return signature + ihdr + idat + iend


@pytest.fixture(autouse=True)
def _mock_attach_db():
    """mock 文件注册表 service + 用户上下文 (store_image 依赖).

    注意: quota_service 通过 src.files.quota 模块持有 create_file_registry_service
    的本地引用, 必须同时 patch 该路径, 否则在并发场景下会访问真实 SQLite 数据库,
    触发 disk I/O error 等偶发失败.
    """
    mock_registry = MagicMock()
    mock_registry.upsert = AsyncMock()
    mock_registry.get = AsyncMock()
    mock_registry.find_by_content_hash = AsyncMock(return_value=None)
    mock_registry.get_total_unique_size = AsyncMock(return_value=0)
    mock_ctx = MagicMock()
    mock_ctx.agent_id = "test-agent"
    mock_ctx.user_id = "user1"
    mock_ctx.thread_id = "thread1"
    with (
        patch("src.core.context.get_user_context", return_value=mock_ctx),
        patch(
            "src.storage.service.file_registry_service.create_file_registry_service",
            new=AsyncMock(return_value=mock_registry),
        ),
        patch(
            "src.files.quota.create_file_registry_service",
            new=AsyncMock(return_value=mock_registry),
        ),
    ):
        yield mock_registry


class TestGetFileExtension:
    """测试_get_file_extension - MIME类型到扩展名映射."""

    def test_get_file_extension_jpeg_returns_jpg(self, repository):
        assert repository._get_file_extension("image/jpeg") == ".jpg"

    def test_get_file_extension_png_returns_png(self, repository):
        assert repository._get_file_extension("image/png") == ".png"

    def test_get_file_extension_gif_returns_gif(self, repository):
        assert repository._get_file_extension("image/gif") == ".gif"

    def test_get_file_extension_webp_returns_webp(self, repository):
        assert repository._get_file_extension("image/webp") == ".webp"

    def test_get_file_extension_bmp_returns_bmp(self, repository):
        assert repository._get_file_extension("image/bmp") == ".bmp"

    def test_get_file_extension_unknown_returns_jpg(self, repository):
        assert repository._get_file_extension("application/pdf") == ".jpg"

    def test_get_file_extension_case_insensitive(self, repository):
        assert repository._get_file_extension("Image/JPEG") == ".jpg"
        assert repository._get_file_extension("IMAGE/PNG") == ".png"


class TestGetMaxFileSize:
    """测试_get_max_file_size - 文件大小限制."""

    def test_get_max_file_size_returns_50mb(self, repository):
        assert repository._get_max_file_size() == 50 * 1024 * 1024


class TestStoreBinaryDocument:
    """测试store_binary_document - 二进制文档 (docx/pdf) 存储与 desc 组装."""

    @pytest.mark.asyncio
    async def test_converted_document_stores_original_bytes_and_md_desc(
        self,
        repository,
        _mock_attach_db,
    ):
        """数字版已转换: 物理存原始二进制, desc=brief+转换md."""
        data = b"\x50\x4b\x03\x04 fake docx"

        with (
            patch.object(Path, "write_bytes"),
            patch("src.files.repository.write_desc") as mock_write_desc,
        ):
            result = await repository.store_binary_document(
                user_id="user1",
                thread_id="thread1",
                round_number=2,
                filename="报告.docx",
                data=data,
                markdown="# 转换后的标题\n正文",
                mode="digital-docx",
            )

        assert isinstance(result, AttachmentDTO)
        assert result.file_type == "document"
        assert result.file_format == "docx"
        assert result.file_size == len(data)
        assert "报告.docx" in result.brief
        assert "docx" in result.brief

        # desc = brief + 分隔符 + 转换 md
        from src.files.desc_writer import split_desc

        written = mock_write_desc.call_args[0][2]
        summary, original = split_desc(written)
        assert original == "# 转换后的标题\n正文"

    @pytest.mark.asyncio
    async def test_deferred_document_brief_mentions_parsing(
        self,
        repository,
        _mock_attach_db,
    ):
        """扫描件延迟形态: brief 含解析中提示与页数, desc 为占位说明."""
        with (
            patch.object(Path, "write_bytes"),
            patch("src.files.repository.write_desc") as mock_write_desc,
        ):
            result = await repository.store_binary_document(
                user_id="user1",
                thread_id="thread1",
                round_number=2,
                filename="扫描件.pdf",
                data=b"pdf-bytes",
                markdown="",
                mode="scan-deferred",
                pages=12,
            )

        assert "解析中" in result.brief
        assert "12" in result.brief
        # desc 已写入 (占位说明, 后台完成后重组)
        mock_write_desc.assert_called_once()

    @pytest.mark.asyncio
    async def test_empty_data_raises(self, repository):
        """空数据抛 ValueError."""
        with pytest.raises(ValueError, match="文档数据不能为空"):
            await repository.store_binary_document(
                user_id="user1",
                thread_id="thread1",
                round_number=1,
                filename="a.pdf",
                data=b"",
            )


class TestResolveEmbeddedImages:
    """测试resolve_embedded_images - 内嵌图入库 + md 引用替换."""

    @pytest.mark.asyncio
    async def test_images_stored_and_references_replaced(
        self,
        repository,
        _mock_attach_db,
    ):
        """内嵌图入库为 image 附件, md 引用替换为 file 标记."""
        import base64

        img_b64 = base64.b64encode(b"fake-png").decode()
        markdown = "# 文档\n\n![图](images/image_001.png)\n\n正文 ![图](images/image_002.jpg)"

        stored_dtos = [
            AttachmentDTO(
                file_id="img00001",
                file_type="image",
                internal_path="files/images/a.png",
                filename="a.png",
                brief="《报告.docx》内嵌图",
                file_format="png",
            ),
            AttachmentDTO(
                file_id="img00002",
                file_type="image",
                internal_path="files/images/b.jpg",
                filename="b.jpg",
                brief="《报告.docx》内嵌图",
                file_format="jpg",
            ),
        ]
        repository.store_image = AsyncMock(side_effect=stored_dtos)

        new_md, image_ids = await repository.resolve_embedded_images(
            user_id="user1",
            thread_id="thread1",
            round_number=2,
            markdown=markdown,
            images=[
                {"name": "image_001.png", "content_b64": img_b64},
                {"name": "image_002.jpg", "content_b64": img_b64},
            ],
        )

        assert repository.store_image.await_count == 2
        assert image_ids == ["img00001", "img00002"]
        assert "![图](file:img00001)" in new_md
        assert "![图](file:img00002)" in new_md
        assert "images/image_001.png" not in new_md

        # 内嵌图不生成描述 (brief 为来源占位)
        call = repository.store_image.call_args_list[0]
        assert call.kwargs.get("image_describer") is None

    @pytest.mark.asyncio
    async def test_no_images_returns_markdown_unchanged(self, repository):
        """无内嵌图: markdown 原样返回, 不调 store_image."""
        repository.store_image = AsyncMock()

        new_md, image_ids = await repository.resolve_embedded_images(
            user_id="user1",
            thread_id="thread1",
            round_number=1,
            markdown="# 纯文本",
            images=[],
        )

        assert new_md == "# 纯文本"
        assert image_ids == []
        repository.store_image.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_image_store_failure_skips_reference(
        self,
        repository,
    ):
        """单图入库失败跳过, 引用保留占位文本, 不中断."""
        import base64

        img_b64 = base64.b64encode(b"fake-png").decode()
        repository.store_image = AsyncMock(side_effect=OSError("磁盘满"))

        new_md, image_ids = await repository.resolve_embedded_images(
            user_id="user1",
            thread_id="thread1",
            round_number=1,
            markdown="![图](images/image_001.png)",
            images=[{"name": "image_001.png", "content_b64": img_b64}],
        )

        assert image_ids == []
        # 引用替换为图片占位说明 (不含 file id)
        assert "images/image_001.png" not in new_md


class TestStoreImage:
    """测试store_image - 图片保存和描述生成."""

    @pytest.mark.asyncio
    async def test_store_image_with_empty_data_raises_value_error(self, repository):
        """空图片数据应抛出ValueError."""
        with pytest.raises(ValueError, match="图片数据不能为空"):
            await repository.store_image(
                user_id="user1",
                thread_id="thread1",
                round_number=1,
                image_data=b"",
            )

    @pytest.mark.asyncio
    async def test_store_image_with_oversized_data_raises_value_error(
        self,
        repository,
        sample_image_data,
    ):
        """超大图片数据应抛出ValueError."""
        oversized_data = b"x" * (50 * 1024 * 1024 + 1)

        with pytest.raises(ValueError, match="图片文件过大"):
            await repository.store_image(
                user_id="user1",
                thread_id="thread1",
                round_number=1,
                image_data=oversized_data,
            )

    @pytest.mark.asyncio
    async def test_store_image_with_describer_returns_attachment(
        self,
        repository,
        sample_image_data,
        _mock_attach_db,
    ):
        """传入 image_describer 时应同步生成描述并返回 AttachmentDTO."""
        mock_describer = MagicMock()
        mock_describer.describe = AsyncMock(
            return_value=("橘猫照片", "测试图片描述"),
        )

        with (
            patch.object(Path, "write_bytes"),
            patch("src.files.repository.write_desc") as mock_write_desc,
        ):
            result = await repository.store_image(
                user_id="user1",
                thread_id="thread1",
                round_number=1,
                image_data=sample_image_data,
                mime_type="image/png",
                image_describer=mock_describer,
            )

            assert isinstance(result, AttachmentDTO)
            assert result.file_type == "image"
            assert result.internal_path.startswith("files/images/")
            assert result.brief == "橘猫照片"
            assert result.file_id is not None
            assert result.file_size == len(sample_image_data)
            mock_describer.describe.assert_awaited_once()

            # desc = 摘要 + 分隔符 + 画面描述 (统一结构)
            from src.files.desc_writer import split_desc

            written = mock_write_desc.call_args[0][2]
            summary, original = split_desc(written)
            assert summary == "橘猫照片"
            assert original == "测试图片描述"

    @pytest.mark.asyncio
    async def test_store_image_generates_correct_filename_pattern(
        self,
        repository,
        sample_image_data,
        _mock_attach_db,
    ):
        """生成的文件名应包含round_number和正确扩展名."""
        mock_describer = MagicMock()
        mock_describer.describe = AsyncMock(return_value=("描述", "描述"))

        with patch.object(Path, "write_bytes"):
            result = await repository.store_image(
                user_id="user1",
                thread_id="thread1",
                round_number=5,
                image_data=sample_image_data,
                mime_type="image/png",
                image_describer=mock_describer,
            )

            assert "round_5_" in result.internal_path
            assert result.internal_path.endswith(".png")

    @pytest.mark.asyncio
    async def test_store_image_write_failure_raises_os_error(
        self,
        repository,
        sample_image_data,
    ):
        """文件写入失败应抛出OSError."""
        with patch.object(Path, "write_bytes", side_effect=OSError("磁盘空间不足")):
            with pytest.raises(OSError, match="图片保存失败"):
                await repository.store_image(
                    user_id="user1",
                    thread_id="thread1",
                    round_number=1,
                    image_data=sample_image_data,
                )

    @pytest.mark.asyncio
    async def test_store_image_without_describer_skips_description(
        self,
        repository,
        sample_image_data,
        _mock_attach_db,
    ):
        """image_describer=None 应跳过描述生成."""
        with patch.object(Path, "write_bytes"):
            result = await repository.store_image(
                user_id="user1",
                thread_id="thread1",
                round_number=1,
                image_data=sample_image_data,
                mime_type="image/png",
                image_describer=None,
            )

            assert isinstance(result, AttachmentDTO)
            assert result.brief.startswith("图片:")


class TestStoreDocument:
    """测试store_document - 纯文本文档保存 (md/txt)."""

    @pytest.mark.asyncio
    async def test_store_document_saves_and_registers(
        self, repository, _mock_attach_db
    ):
        """正常保存: 落盘 documents 目录, 注册 document 类型, desc=原文."""
        with (
            patch.object(Path, "write_bytes") as mock_write,
            patch("src.files.repository.write_desc") as mock_write_desc,
        ):
            result = await repository.store_document(
                user_id="user1",
                thread_id="thread1",
                round_number=2,
                filename="会议纪要.md",
                content="# 标题\n\n第一行正文内容",
            )

        assert isinstance(result, AttachmentDTO)
        assert result.file_type == "document"
        assert result.internal_path.startswith("files/documents/")
        assert result.internal_path.endswith(".md")
        assert result.filename == "会议纪要.md"
        assert result.file_format == "md"
        assert result.file_size > 0
        # brief = 文件名 + 首个非空行摘要 (md 标题即首行)
        assert "标题" in result.brief

        mock_write.assert_called_once()
        mock_write_desc.assert_called_once()
        # desc = 摘要(brief) + 分隔符 + 原文 (统一结构)
        from src.files.desc_writer import split_desc

        written = mock_write_desc.call_args[0][2]
        summary, original = split_desc(written)
        assert summary == result.brief
        assert original == "# 标题\n\n第一行正文内容"

        # 注册表写入 document 类型
        entry = _mock_attach_db.upsert.await_args[0][0]
        assert entry.file_type == "document"
        assert entry.physical_path == f"thread1/shared/{result.internal_path}"
        assert entry.owner_agent_id == "test-agent"

    @pytest.mark.asyncio
    async def test_store_document_no_extension_defaults_txt(
        self, repository, _mock_attach_db
    ):
        """无扩展名文件默认 .txt."""
        with (
            patch.object(Path, "write_bytes"),
            patch("src.files.repository.write_desc"),
        ):
            result = await repository.store_document(
                user_id="user1",
                thread_id="thread1",
                round_number=1,
                filename="README",
                content="hello",
            )
        assert result.file_format == "txt"
        assert result.internal_path.endswith(".txt")

    @pytest.mark.asyncio
    async def test_store_document_filename_sanitized(self, repository, _mock_attach_db):
        """含路径分隔的文件名被清洗, 防路径穿越."""
        with (
            patch.object(Path, "write_bytes"),
            patch("src.files.repository.write_desc"),
        ):
            result = await repository.store_document(
                user_id="user1",
                thread_id="thread1",
                round_number=1,
                filename="../../etc/passwd.md",
                content="x",
            )
        # 分隔符被替换, 磁盘文件名不含路径
        disk_name = result.internal_path.split("/")[-1]
        assert "/" not in disk_name
        assert ".." not in disk_name

    @pytest.mark.asyncio
    async def test_store_document_empty_content_raises(self, repository):
        """空内容应抛出ValueError."""
        with pytest.raises(ValueError, match="文档内容不能为空"):
            await repository.store_document(
                user_id="user1",
                thread_id="thread1",
                round_number=1,
                filename="a.md",
                content="",
            )

    @pytest.mark.asyncio
    async def test_store_document_oversized_raises(self, repository):
        """超大内容应抛出ValueError."""
        with pytest.raises(ValueError, match="文档过大"):
            await repository.store_document(
                user_id="user1",
                thread_id="thread1",
                round_number=1,
                filename="big.md",
                content="x" * (50 * 1024 * 1024 + 1),
            )

    @pytest.mark.asyncio
    async def test_store_document_dedup_reuses_physical_file(
        self, repository, _mock_attach_db, tmp_path
    ):
        """相同内容去重: 复用物理文件, 不重复写盘."""
        # 构造已存在的物理文件 (resolver.base_path = /tmp/test_data)
        existing_rel = "thread1/shared/files/documents/old.md"
        existing_abs = Path("/tmp/test_data/user1") / existing_rel
        existing_abs.parent.mkdir(parents=True, exist_ok=True)
        existing_abs.write_text("相同内容", encoding="utf-8")

        mock_entry = MagicMock()
        mock_entry.physical_path = existing_rel
        mock_entry.file_size = 12
        mock_entry.brief = "旧 brief"
        mock_entry.file_id = "oldid123"
        _mock_attach_db.find_by_content_hash = AsyncMock(return_value=mock_entry)

        try:
            with (
                patch.object(Path, "write_bytes") as mock_write,
                patch("src.files.repository.write_desc") as mock_write_desc,
            ):
                result = await repository.store_document(
                    user_id="user1",
                    thread_id="thread1",
                    round_number=1,
                    filename="新名字.md",
                    content="相同内容",
                )

            mock_write.assert_not_called()
            mock_write_desc.assert_not_called()
            assert result.internal_path == "files/documents/old.md"
            assert "旧 brief" in result.brief
        finally:
            existing_abs.unlink(missing_ok=True)

    @pytest.mark.asyncio
    async def test_store_document_brief_truncated_to_80_chars(
        self, repository, _mock_attach_db
    ):
        """brief 中首行摘要截断到 80 字符."""
        with (
            patch.object(Path, "write_bytes"),
            patch("src.files.repository.write_desc"),
        ):
            result = await repository.store_document(
                user_id="user1",
                thread_id="thread1",
                round_number=1,
                filename="a.md",
                content="字" * 200,
            )
        # brief = "a.md: " + 摘要(≤80)
        assert len(result.brief) <= len("a.md: ") + 80


class TestUpdateDescription:
    """测试update_description - 后台更新图片描述 (brief 写 DB, detail 写 .desc.md)."""

    @pytest.mark.asyncio
    async def test_update_description_success(self, repository):
        """应成功更新 brief 并写 .desc.md."""
        mock_entry = MagicMock()
        mock_entry.filename = "test.jpg"
        mock_entry.brief = ""
        mock_registry = AsyncMock()
        mock_registry.get.return_value = mock_entry

        with (
            patch(
                "src.storage.service.file_registry_service.create_file_registry_service",
                return_value=mock_registry,
            ),
            patch("src.files.repository.write_desc") as mock_write_desc,
            patch("src.core.context.get_user_context") as mock_ctx,
        ):
            mock_ctx.return_value = MagicMock(user_id="user1")
            await repository.update_description(
                file_id="abc12345",
                brief="新描述",
                detail="详细描述",
            )

        assert mock_entry.brief == "新描述"
        mock_registry.upsert.assert_awaited_once()
        # desc = 摘要(brief) + 分隔符 + 原文 (统一结构)
        from src.files.desc_writer import split_desc

        written = mock_write_desc.call_args[0][2]
        summary, original = split_desc(written)
        assert summary == "新描述"
        assert original == "详细描述"

    @pytest.mark.asyncio
    async def test_desc_summary_overrides_brief_in_desc(self, repository):
        """desc_summary 覆盖 desc 摘要区 (代码文件: DB brief 一句话, desc 结构化摘要)."""
        mock_entry = MagicMock()
        mock_entry.filename = "a.py"
        mock_entry.file_type = "document"
        mock_entry.brief = ""
        mock_registry = AsyncMock()
        mock_registry.get.return_value = mock_entry

        with (
            patch(
                "src.storage.service.file_registry_service.create_file_registry_service",
                return_value=mock_registry,
            ),
            patch("src.files.repository.write_desc") as mock_write_desc,
            patch("src.core.context.get_user_context") as mock_ctx,
        ):
            mock_ctx.return_value = MagicMock(user_id="user1")
            await repository.update_description(
                file_id="abc12345",
                brief="Python 入口脚本",
                detail="print(1)",
                desc_summary="语言: Python\n功能: 入口",
            )

        # DB brief = 一句话
        assert mock_entry.brief == "Python 入口脚本"
        from src.files.desc_writer import split_desc

        written = mock_write_desc.call_args[0][2]
        summary, original = split_desc(written)
        # desc 摘要区 = 结构化摘要, 原文区 = 代码
        assert summary == "语言: Python\n功能: 入口"
        assert original == "print(1)"

    @pytest.mark.asyncio
    async def test_empty_brief_fallback_by_file_type(self, repository):
        """brief 为空时兜底文案按 file_type 生成 (非硬编码"图片")."""
        mock_entry = MagicMock()
        mock_entry.filename = "a.py"
        mock_entry.file_type = "document"
        mock_entry.brief = ""
        mock_registry = AsyncMock()
        mock_registry.get.return_value = mock_entry

        with (
            patch(
                "src.storage.service.file_registry_service.create_file_registry_service",
                return_value=mock_registry,
            ),
            patch("src.files.repository.write_desc"),
            patch("src.core.context.get_user_context") as mock_ctx,
        ):
            mock_ctx.return_value = MagicMock(user_id="user1")
            await repository.update_description(
                file_id="abc12345",
                brief="",
                detail="print(1)",
            )

        assert mock_entry.brief == "文档: a.py"

    @pytest.mark.asyncio
    async def test_update_description_not_in_registry(self, repository):
        """注册表中无记录时应静默跳过."""
        mock_registry = AsyncMock()
        mock_registry.get.return_value = None

        with (
            patch(
                "src.storage.service.file_registry_service.create_file_registry_service",
                return_value=mock_registry,
            ),
            patch("src.core.context.get_user_context") as mock_ctx,
        ):
            mock_ctx.return_value = MagicMock(user_id="user1")
            await repository.update_description(
                file_id="notexist",
                brief="描述",
                detail="详情",
            )

        mock_registry.upsert.assert_not_awaited()
