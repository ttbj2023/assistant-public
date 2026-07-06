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

        with patch.object(Path, "write_bytes"):
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
        mock_write_desc.assert_called_once_with("user1", "abc12345", "详细描述")

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
