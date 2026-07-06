"""文件存储仓库 - 统一的附件存储编排.

从 src/storage/service/attachment_service.py 拆分而来. 收敛"去重 + 物理存储 +
注册表写入 + 配额检查"的存储编排逻辑到文件管理子系统.

核心设计:
- store_image 通过 ImageDescriberProtocol 依赖注入接收可选的描述生成器,
  避免文件层直接依赖推理层 (files -> inference 反向依赖).
- 调用方 (chat_helpers) 负责决定同步/异步描述策略:
  - 非多模态: 传入 image_describer, 存储时同步生成描述
  - 多模态: 不传入 image_describer, 后台补描述 (调 update_description)
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from pathlib import Path
from typing import Protocol, runtime_checkable

from src.config.storage_config import get_config as get_storage_config
from src.core.path_resolver import UserDataPathResolver
from src.files import generate_file_id
from src.files.desc_writer import write_desc
from src.files.hash_utils import compute_hash
from src.files.models import AttachmentDTO
from src.files.paths import FILES_IMAGES
from src.files.quota import get_storage_quota_service

logger = logging.getLogger(__name__)


@runtime_checkable
class ImageDescriberProtocol(Protocol):
    """图片描述生成器协议 (依赖注入, 解耦 files 与 inference)."""

    async def describe(
        self,
        image_path: Path,
        mime_type: str = "image/jpeg",
    ) -> tuple[str, str]:
        """生成图片描述, 返回 (brief, detail)."""
        ...


class FileRepository:
    """文件存储仓库 - 附件存储编排.

    职责:
    - 图片文件系统存储 (用户-线程共享区域)
    - 文件去重 (用户级 SHA-256 内容哈希)
    - 附件注册表写入 (attachment_registry 表)
    - 存储配额检查与清理触发
    """

    def __init__(self) -> None:
        self.path_resolver = UserDataPathResolver()
        logger.info("📎 文件存储仓库初始化完成")

    def _get_max_file_size(self) -> int:
        """获取最大文件大小限制."""
        return 50 * 1024 * 1024

    async def store_image(
        self,
        user_id: str,
        thread_id: str,
        round_number: int,
        image_data: bytes,
        mime_type: str = "image/jpeg",
        *,
        image_describer: ImageDescriberProtocol | None = None,
    ) -> AttachmentDTO:
        """保存图片文件并可选生成描述.

        集成文件去重: 相同内容的图片在用户级只存一份, 后续保存通过引用计数复用.

        Args:
            user_id: 用户ID
            thread_id: 线程ID
            round_number: 对话轮次号
            image_data: 图片二进制数据
            mime_type: MIME类型 (image/jpeg, image/png等)
            image_describer: 描述生成器, 传入则在未命中去重时同步生成描述;
                None 表示跳过描述 (多模态场景, 后台补全)

        Returns:
            AttachmentDTO 附件信息对象

        Raises:
            ValueError: 当图片数据无效时
            IOError: 当文件保存失败时

        """
        if not image_data:
            raise ValueError("图片数据不能为空")

        if len(image_data) > self._get_max_file_size():
            raise ValueError(
                f"图片文件过大: {len(image_data)} 字节, "
                f"最大允许: {self._get_max_file_size()} 字节",
            )

        storage_config = get_storage_config().file_store
        content_hash = compute_hash(image_data)
        extension = self._get_file_extension(mime_type)
        is_duplicate = False
        physical_path: str = ""
        relative_url: str = ""
        file_size: int = 0
        filename: str = ""
        image_path: Path | None = None

        from src.storage.service.file_registry_service import (
            create_file_registry_service,
        )

        registry = await create_file_registry_service(user_id)

        # 去重检查 (用户级 FileRegistry, 引用计数实时查询, 无需维护 reference_count)
        if storage_config.deduplication_enabled:
            existing = await registry.find_by_content_hash(content_hash)
            if existing:
                existing_abs = (
                    self.path_resolver.base_path / user_id / existing.physical_path
                )
                if existing_abs.exists():
                    is_duplicate = True
                    physical_path = existing.physical_path
                    relative_url = existing.physical_path.split("shared/", 1)[-1]
                    file_size = existing.file_size or 0
                    filename = Path(existing.physical_path).name
                    image_path = existing_abs
                    logger.info(
                        "🔁 图片去重命中: hash=%s.., 复用 %s",
                        content_hash[:8],
                        existing.physical_path,
                    )
                else:
                    logger.warning(
                        "⚠️ 去重命中但物理文件已丢失: hash=%s.., path=%s",
                        content_hash[:8],
                        existing.physical_path,
                    )

        if not is_duplicate:
            timestamp = int(asyncio.get_running_loop().time() * 1000)
            random_suffix = uuid.uuid4().hex[:8]
            filename = f"round_{round_number}_{timestamp}_{random_suffix}{extension}"

            images_dir = self.path_resolver.get_shared_storage_path(
                user_id,
                thread_id,
                FILES_IMAGES,
            )

            image_path = images_dir / filename
            try:
                image_path.write_bytes(image_data)
                logger.info(
                    "💾 图片保存成功: %s/%s/%s (%d 字节)",
                    user_id,
                    thread_id,
                    filename,
                    len(image_data),
                )
            except OSError as e:
                logger.error("❌ 图片保存失败: %s, 错误: %s", image_path, e)
                raise OSError(f"图片保存失败: {e}") from e

            relative_url = f"{FILES_IMAGES}/{filename}"
            physical_path = f"{thread_id}/shared/{FILES_IMAGES}/{filename}"
            file_size = len(image_data)

        # 描述生成: 仅未命中去重且提供了 describer 时同步生成
        if image_describer and image_path and not is_duplicate:
            brief, detail = await image_describer.describe(image_path, mime_type)
        else:
            brief, detail = "", ""

        from src.core.context import get_user_context
        from src.files.desc_writer import desc_relative_path
        from src.storage.models.file_registry import FileEntry

        file_id = generate_file_id()
        ctx = get_user_context()
        await registry.upsert(
            FileEntry(
                file_id=file_id,
                file_type="image",
                physical_path=physical_path,
                desc_path=desc_relative_path(file_id),
                filename=filename,
                brief=brief or f"图片: {filename}",
                file_format=extension.lstrip("."),
                file_size=file_size,
                content_hash=content_hash,
                round_number=round_number,
                owner_thread_id=thread_id,
                owner_agent_id=ctx.agent_id,
            ),
        )

        # 描述外置: 写 .desc.md (Phase 6 后 detail 字段移除, .desc.md 成为唯一描述载体)
        if detail:
            write_desc(user_id, file_id, detail)

        attachment = AttachmentDTO(
            file_id=file_id,
            file_type="image",
            internal_path=relative_url,
            filename=filename,
            brief=brief or f"图片: {filename}",
            detail=detail,
            file_format=extension.lstrip("."),
            file_size=file_size,
            content_hash=content_hash,
            round_number=round_number,
        )

        logger.info("✅ 附件信息生成完成: %s - %s", relative_url, brief)

        if not is_duplicate and storage_config.quota_check_enabled:
            quota_service = get_storage_quota_service(user_id)
            await quota_service.check_and_cleanup()

        return attachment

    async def update_description(
        self,
        file_id: str,
        brief: str,
        detail: str,
    ) -> None:
        """更新文件描述 (brief 写 DB, detail 写 .desc.md).

        供多模态场景后台补全描述使用. user/thread/agent 从 ContextVar 获取.

        Args:
            file_id: 文件ID (8位hex)
            brief: 简短描述
            detail: 详细描述 (写入 .desc.md)

        """
        from src.core.context import get_user_context
        from src.storage.service.file_registry_service import (
            create_file_registry_service,
        )

        ctx = get_user_context()
        registry = await create_file_registry_service(ctx.user_id)
        entry = await registry.get(file_id)
        if entry:
            entry.brief = brief or f"图片: {entry.filename}"
            await registry.upsert(entry)
            if detail:
                write_desc(ctx.user_id, file_id, detail)
            logger.info("🔄 后台更新图片描述: %s - %s", file_id, brief)

    def _get_file_extension(self, mime_type: str) -> str:
        """根据MIME类型获取文件扩展名.

        Args:
            mime_type: MIME类型

        Returns:
            文件扩展名 (包含点号)

        """
        mime_to_ext = {
            "image/jpeg": ".jpg",
            "image/jpg": ".jpg",
            "image/png": ".png",
            "image/gif": ".gif",
            "image/webp": ".webp",
            "image/bmp": ".bmp",
        }
        return mime_to_ext.get(mime_type.lower(), ".jpg")


_repository: FileRepository | None = None


def get_file_repository() -> FileRepository:
    """获取文件存储仓库单例."""
    global _repository
    if _repository is None:
        _repository = FileRepository()
    return _repository


__all__ = [
    "FileRepository",
    "ImageDescriberProtocol",
    "get_file_repository",
]
