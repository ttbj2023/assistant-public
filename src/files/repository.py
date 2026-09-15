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
import json
import logging
import os
import re
import uuid
from pathlib import Path
from typing import Protocol, runtime_checkable

from src.config.storage_config import get_config as get_storage_config
from src.core.path_resolver import UserDataPathResolver
from src.files import generate_file_id
from src.files.desc_writer import compose_desc, write_desc
from src.files.hash_utils import compute_hash
from src.files.models import AttachmentDTO
from src.files.paths import FILES_DOCUMENTS, FILES_IMAGES
from src.files.quota import get_storage_quota_service

logger = logging.getLogger(__name__)

# file_type → 中文标签 (brief 兜底文案)
_FILE_TYPE_LABELS = {"image": "图片", "document": "文档", "video": "视频"}


def _fallback_brief(file_type: str, filename: str) -> str:
    """构造 brief 兜底文案: "{标签}: {文件名}"."""
    label = _FILE_TYPE_LABELS.get(file_type, "文件")
    return f"{label}: {filename}"


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

        # desc = 摘要(brief) + 分隔符 + 画面描述(detail), 统一结构
        if detail:
            write_desc(user_id, file_id, compose_desc(brief, detail))

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

    async def store_document(
        self,
        user_id: str,
        thread_id: str,
        round_number: int,
        filename: str,
        content: str,
    ) -> AttachmentDTO:
        """保存纯文本文档 (md/txt) 并注册到文件体系.

        与 store_image 同构: SHA-256 用户级去重 → files/documents/ →
        FileEntry(file_type="document") → .desc.md=原文 (read_file 可读全文) → 配额.

        Args:
            user_id: 用户ID
            thread_id: 线程ID
            round_number: 对话轮次号
            filename: 原始文件名 (清洗后作为展示名)
            content: 纯文本内容

        Returns:
            AttachmentDTO 附件信息对象

        Raises:
            ValueError: 当内容为空或超出大小限制时
            IOError: 当文件保存失败时

        """
        if not content:
            raise ValueError("文档内容不能为空")

        content_bytes = content.encode("utf-8")
        if len(content_bytes) > self._get_max_file_size():
            raise ValueError(
                f"文档过大: {len(content_bytes)} 字节, "
                f"最大允许: {self._get_max_file_size()} 字节",
            )

        # 文件名清洗: 去路径分隔/控制字符/路径穿越, 保留 CJK, 截断长度
        safe_name = (
            re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", filename.strip()) or "document.txt"
        )
        safe_name = safe_name.replace("..", "_")
        safe_name = safe_name[-120:] if len(safe_name) > 120 else safe_name
        stem, dot_ext = os.path.splitext(safe_name)
        extension = dot_ext if dot_ext else ".txt"

        brief = self._build_document_brief(safe_name, content)
        return await self._store_document_entry(
            user_id=user_id,
            thread_id=thread_id,
            round_number=round_number,
            safe_name=safe_name,
            stem=stem,
            extension=extension,
            content_bytes=content_bytes,
            brief=brief,
            desc_original=content,
            document_meta=json.dumps(
                {
                    "filename": safe_name,
                    "chars": len(content),
                    "lines": content.count("\n") + 1,
                },
                ensure_ascii=False,
            ),
        )

    @staticmethod
    def _build_document_brief(filename: str, content: str) -> str:
        """构建文档 brief: 文件名 + 首个非空行摘要 (≤80字符)."""
        first_line = next(
            (line.strip() for line in content.splitlines() if line.strip()),
            "",
        )
        # 去掉 markdown 标题符号, 纯摘要更简洁
        first_line = first_line.lstrip("# ").strip()
        summary = first_line[:80]
        if summary:
            return f"{filename}: {summary}"
        return f"文档: {filename}"

    async def store_binary_document(
        self,
        user_id: str,
        thread_id: str,
        round_number: int,
        filename: str,
        data: bytes,
        *,
        markdown: str = "",
        mode: str = "",
        pages: int = 0,
        brief: str | None = None,
    ) -> AttachmentDTO:
        """保存二进制文档 (docx/pdf/pptx) 并注册到文件体系.

        与 store_document 同构, 差异: 物理文件为原始二进制 (保留原扩展名,
        可下载), desc 原文区为转换结果或解析中占位. 数字版调用方传入
        markdown (doc2md 已转换); 扫描件延迟形态传空 markdown + pages,
        后台 OCR 完成后经 update_description 重组.

        Args:
            user_id: 用户ID
            thread_id: 线程ID
            round_number: 对话轮次号
            filename: 原始文件名 (清洗后作为展示名)
            data: 原始二进制内容
            markdown: 转换产出的 markdown (空表示延迟解析中)
            mode: 转换模式 (digital-docx / digital-pdf / scan-deferred / ocr)
            pages: 页数 (扫描件估时用)
            brief: 覆盖自动生成的 brief (降级场景的说明文案)

        Returns:
            AttachmentDTO 附件信息对象

        Raises:
            ValueError: 当内容为空或超出大小限制时
            IOError: 当文件保存失败时

        """
        if not data:
            raise ValueError("文档数据不能为空")

        if len(data) > self._get_max_file_size():
            raise ValueError(
                f"文档过大: {len(data)} 字节, "
                f"最大允许: {self._get_max_file_size()} 字节",
            )

        # 文件名清洗: 与 store_document 一致
        safe_name = (
            re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", filename.strip()) or "document.bin"
        )
        safe_name = safe_name.replace("..", "_")
        safe_name = safe_name[-120:] if len(safe_name) > 120 else safe_name
        stem, dot_ext = os.path.splitext(safe_name)
        extension = dot_ext if dot_ext else ".bin"

        if brief is None:
            if markdown:
                brief = f"{safe_name} ({extension.lstrip('.')}, 已转markdown)"
            else:
                minutes = max(1, -(-pages * 12 // 60))  # 每页约12s, 向上取整
                brief = (
                    f"{safe_name} 扫描版 {pages}页, 内容解析中"
                    f"(预计约{minutes}分钟), 完成后会通知"
                )

        return await self._store_document_entry(
            user_id=user_id,
            thread_id=thread_id,
            round_number=round_number,
            safe_name=safe_name,
            stem=stem,
            extension=extension,
            content_bytes=data,
            brief=brief,
            desc_original=markdown or brief,
            document_meta=json.dumps(
                {"filename": safe_name, "mode": mode, "pages": pages},
                ensure_ascii=False,
            ),
        )

    async def _store_document_entry(
        self,
        *,
        user_id: str,
        thread_id: str,
        round_number: int,
        safe_name: str,
        stem: str,
        extension: str,
        content_bytes: bytes,
        brief: str,
        desc_original: str,
        document_meta: str | None = None,
    ) -> AttachmentDTO:
        """文档落盘 + 注册共享骨架 (store_document / store_binary_document 复用).

        Args:
            desc_original: desc 原文区内容 (纯文本文档=原文, 二进制=转换md或占位)

        """
        storage_config = get_storage_config().file_store
        content_hash = compute_hash(content_bytes)
        is_duplicate = False
        physical_path = ""
        relative_url = ""
        file_size = 0
        disk_filename = ""

        from src.storage.service.file_registry_service import (
            create_file_registry_service,
        )

        registry = await create_file_registry_service(user_id)

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
                    disk_filename = Path(existing.physical_path).name
                    brief = existing.brief or brief
                    logger.info(
                        "🔁 文档去重命中: hash=%s.., 复用 %s",
                        content_hash[:8],
                        existing.physical_path,
                    )

        if not is_duplicate:
            timestamp = int(asyncio.get_running_loop().time() * 1000)
            random_suffix = uuid.uuid4().hex[:8]
            disk_filename = f"{stem}_{timestamp}_{random_suffix}{extension}"

            documents_dir = self.path_resolver.get_shared_storage_path(
                user_id,
                thread_id,
                FILES_DOCUMENTS,
            )

            doc_path = documents_dir / disk_filename
            try:
                doc_path.write_bytes(content_bytes)
                logger.info(
                    "💾 文档保存成功: %s/%s/%s (%d 字节)",
                    user_id,
                    thread_id,
                    disk_filename,
                    len(content_bytes),
                )
            except OSError as e:
                logger.error("❌ 文档保存失败: %s, 错误: %s", doc_path, e)
                raise OSError(f"文档保存失败: {e}") from e

            relative_url = f"{FILES_DOCUMENTS}/{disk_filename}"
            physical_path = f"{thread_id}/shared/{FILES_DOCUMENTS}/{disk_filename}"
            file_size = len(content_bytes)

        from src.core.context import get_user_context
        from src.files.desc_writer import desc_relative_path
        from src.storage.models.file_registry import FileEntry

        file_id = generate_file_id()
        ctx = get_user_context()
        await registry.upsert(
            FileEntry(
                file_id=file_id,
                file_type="document",
                physical_path=physical_path,
                desc_path=desc_relative_path(file_id),
                filename=safe_name,
                brief=brief,
                file_format=extension.lstrip("."),
                file_size=file_size,
                content_hash=content_hash,
                round_number=round_number,
                owner_thread_id=thread_id,
                owner_agent_id=ctx.agent_id,
                document_meta=document_meta,
            ),
        )

        # desc = 摘要(brief) + 分隔符 + 原文/转换md, 统一结构
        if not is_duplicate:
            write_desc(user_id, file_id, compose_desc(brief, desc_original))

        attachment = AttachmentDTO(
            file_id=file_id,
            file_type="document",
            internal_path=relative_url,
            filename=safe_name,
            brief=brief,
            file_format=extension.lstrip("."),
            file_size=file_size,
            content_hash=content_hash,
            round_number=round_number,
            document_meta=document_meta,
        )

        if not is_duplicate and storage_config.quota_check_enabled:
            quota_service = get_storage_quota_service(user_id)
            await quota_service.check_and_cleanup()

        return attachment

    async def update_description(
        self,
        file_id: str,
        brief: str,
        detail: str,
        *,
        desc_summary: str | None = None,
    ) -> None:
        """更新文件描述 (brief 写 DB, detail 组装统一结构写 .desc.md).

        供后台补全描述使用 (图片描述/代码摘要). user/thread/agent 从 ContextVar 获取.

        Args:
            file_id: 文件ID (8位hex)
            brief: 简短描述 (写 DB brief 字段)
            detail: 原文/源码区内容 (写入 .desc.md)
            desc_summary: desc 摘要区覆盖文本; 缺省用 brief
                (代码文件传结构化摘要: 语言/功能/结构)

        """
        from src.core.context import get_user_context
        from src.storage.service.file_registry_service import (
            create_file_registry_service,
        )

        ctx = get_user_context()
        registry = await create_file_registry_service(ctx.user_id)
        entry = await registry.get(file_id)
        if entry:
            entry.brief = brief or _fallback_brief(entry.file_type, entry.filename)
            await registry.upsert(entry)
            if detail:
                write_desc(
                    ctx.user_id,
                    file_id,
                    compose_desc(desc_summary or brief, detail),
                )
            logger.info("🔄 后台更新文件描述: %s - %s", file_id, brief)

    async def resolve_embedded_images(
        self,
        user_id: str,
        thread_id: str,
        round_number: int,
        markdown: str,
        images: list[dict],
    ) -> tuple[str, list[str]]:
        """内嵌图入库并替换 md 引用 — 完整保留文档图片内容.

        每张内嵌图经 store_image 入库 (files/images/, 不生成 VLM 描述),
        md 中 `![图](images/xxx)` 引用替换为 `![图](file:id)`, agent 可经
        analyze_image 按 file_id 查看. 单图失败跳过 (引用替换为占位说明),
        不中断整体.

        Args:
            markdown: 转换产出的 markdown
            images: doc2md 返回的 [{"name", "content_b64"}]

        Returns:
            (替换后的 markdown, 成功入库的 file_id 列表)

        """
        if not images:
            return markdown, []

        import base64

        name_to_id: dict[str, str] = {}
        for img in images:
            name = img.get("name", "")
            try:
                data = base64.b64decode(img.get("content_b64", ""))
                dto = await self.store_image(
                    user_id=user_id,
                    thread_id=thread_id,
                    round_number=round_number,
                    image_data=data,
                    mime_type=self._image_mime_from_name(name),
                )
                name_to_id[name] = dto.file_id
                logger.info("🖼️ 内嵌图入库: %s -> %s", name, dto.file_id)
            except (ValueError, OSError) as e:
                logger.warning("⚠️ 内嵌图入库失败 (跳过): %s, %s", name, e)

        new_md = markdown
        for name, file_id in name_to_id.items():
            new_md = new_md.replace(f"](images/{name})", f"](file:{file_id})")

        # 未入库成功的引用替换为占位说明, 避免悬空路径
        for img in images:
            name = img.get("name", "")
            if name and name not in name_to_id:
                new_md = new_md.replace(f"](images/{name})", "](图片未能提取)")

        return new_md, list(name_to_id.values())

    @staticmethod
    def _image_mime_from_name(name: str) -> str:
        """从文件名扩展名推断图片 MIME (内嵌图入库用)."""
        ext = os.path.splitext(name)[1].lower()
        return {
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".webp": "image/webp",
            ".bmp": "image/bmp",
        }.get(ext, "image/png")

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
