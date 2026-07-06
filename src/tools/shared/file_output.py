"""工具文件输出注册 - 统一的签名URL/附件注册/去重/配额.

提取自 export_document/service.py, 供所有需要文件输出的工具共享.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def validate_filename(filename: str) -> None:
    """验证文件名合法性和长度."""
    if not filename or not filename.strip():
        raise ValueError("文件名不能为空")
    safe = re.sub(r"[^\w\-.]", "_", filename.strip())
    if safe != filename.strip():
        raise ValueError("文件名包含非法字符, 请使用字母/数字/下划线/连字符")
    if len(filename) > 200:
        raise ValueError("文件名过长, 最大200字符")


def build_unique_filename(filename: str, fmt: str) -> tuple[str, str]:
    """生成唯一磁盘文件名.

    Returns:
        (磁盘文件名含时间戳, 展示文件名)
    """
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    short_hash = os.urandom(4).hex()
    unique_filename = f"{filename}_{timestamp}_{short_hash}.{fmt}"
    display_filename = f"{filename}.{fmt}"
    return unique_filename, display_filename


async def register_tool_output(
    output_path: Path,
    display_filename: str,
    output_filename: str,
    output_format: str,
    file_type: str,
    content: str,
    summary: str | None,
    user_id: str,
    thread_id: str,
    *,
    document_meta: str | None = None,
    brief: str | None = None,
) -> dict[str, Any]:
    """注册工具输出文件: 去重 → 附件注册 → HMAC签名URL → exported_files → 配额.

    Args:
        output_path: 输出文件磁盘路径
        display_filename: 展示文件名 (如 report.pdf)
        output_filename: 磁盘文件名 (含时间戳)
        output_format: 文件格式 (pdf/png/docx等)
        file_type: 文件类型 (document/image)
        content: 供对话历史标记的内容描述
        summary: 一句话摘要
        user_id: 用户ID
        thread_id: 会话ID
        document_meta: 文档结构化元数据JSON (摘要+目录信息)
        brief: 对话历史一句话标签; 优先于 summary 推导, 缺省走 summary 或自动拼合

    Returns:
        包含 file_id, file_url, filename, format, size_bytes 的结果字典.
    """
    from src.config.storage_config import get_config as get_storage_config
    from src.core.context import get_user_context
    from src.files import generate_file_id
    from src.files.hash_utils import compute_hash
    from src.files.quota import get_storage_quota_service
    from src.files.signed_url import get_signed_url_provider

    storage_config = get_storage_config().file_store
    file_data = await asyncio.to_thread(output_path.read_bytes)
    content_hash = compute_hash(file_data)
    is_duplicate = False
    relative_path: str
    physical_path: str = ""

    from src.storage.service.file_registry_service import (
        create_file_registry_service,
    )

    registry = await create_file_registry_service(user_id)

    # 去重检查 (用户级 FileRegistry, 引用计数实时查询)
    if storage_config.deduplication_enabled:
        existing = await registry.find_by_content_hash(content_hash)
        if existing:
            is_duplicate = True
            with contextlib.suppress(OSError):
                await asyncio.to_thread(output_path.unlink)
            from src.core.path_resolver import get_user_path_resolver

            resolver = get_user_path_resolver()
            output_path = resolver.base_path / user_id / existing.physical_path
            relative_path = existing.physical_path.split("shared/", 1)[-1]
            physical_path = existing.physical_path
            file_size = existing.file_size or 0
            logger.info(
                "去重命中: hash=%s.., 复用 %s",
                content_hash[:8],
                existing.physical_path,
            )

    if not is_duplicate:
        from src.core.path_resolver import get_user_path_resolver

        resolver = get_user_path_resolver()
        user_base = resolver.get_user_base_path(user_id)
        thread_shared = resolver.get_thread_base_path(user_id, thread_id) / "shared"
        relative_path = output_path.relative_to(thread_shared).as_posix()
        physical_path = output_path.relative_to(user_base).as_posix()
        file_size = len(file_data)

    file_id = generate_file_id()
    brief = (
        brief or summary or _compose_brief(output_format, display_filename, file_size)
    )

    # 文件注册 (用户级 FileRegistry)
    from src.files.desc_writer import desc_relative_path
    from src.storage.models.file_registry import FileEntry

    ctx = get_user_context()
    await registry.upsert(
        FileEntry(
            file_id=file_id,
            file_type=file_type,
            physical_path=physical_path,
            desc_path=desc_relative_path(file_id),
            filename=output_filename,
            brief=brief,
            file_format=output_format,
            file_size=file_size,
            content_hash=content_hash,
            round_number=ctx.round_number or 0,
            owner_thread_id=thread_id,
            owner_agent_id=ctx.agent_id,
            document_meta=document_meta,
        ),
    )

    # 描述外置: 文档摘要写 .desc.md (summary 即文档的"描述")
    if summary:
        from src.files.desc_writer import write_desc

        write_desc(user_id, file_id, summary)
    token = get_signed_url_provider().compose_token(
        ctx.user_id,
        ctx.thread_id,
        ctx.agent_id,
        file_id,
    )
    base_url = _get_file_server_base_url()
    url = (
        f"{base_url}/"
        f"{ctx.user_id}/{ctx.thread_id}/{ctx.agent_id}/{token}/{display_filename}"
    )
    ctx.exported_files.append({
        "url": url,
        "file_id": file_id,
        "brief": brief,
        "internal_path": relative_path,
        "filename": output_filename,
        "detail": content,
        "size_bytes": file_size,
        "format": output_format,
        "content_hash": content_hash,
        "file_type": file_type,
        "document_meta": document_meta,
    })

    # 配额检查
    if not is_duplicate and storage_config.quota_check_enabled:
        quota_service = get_storage_quota_service(user_id)
        await quota_service.check_and_cleanup()

    return {
        "success": True,
        "message": f"文件已生成: [file: {file_id}] {display_filename}",
        "file_id": file_id,
        "filename": output_filename,
        "format": output_format,
        "size_bytes": file_size,
    }


def _get_file_server_base_url() -> str:
    """从统一配置获取文件服务公网URL."""
    from src.config.api_config import get_config as get_api_config

    return get_api_config().get_file_server_url()


def _compose_brief(fmt: str, filename: str, size_bytes: int) -> str:
    """自动拼合文件概要."""
    size_str = _format_size(size_bytes)
    return f"{fmt.upper()}导出: {filename} ({size_str})"


def _format_size(size_bytes: int) -> str:
    """格式化文件大小."""
    if size_bytes < 1024:
        return f"{size_bytes}B"
    if size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f}KB"
    return f"{size_bytes / (1024 * 1024):.1f}MB"


__all__ = [
    "build_unique_filename",
    "register_tool_output",
    "validate_filename",
]
