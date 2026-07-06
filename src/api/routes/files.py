"""文件下载路由 - HMAC 签名验证的无状态下载.

支持两种路径模式:
  - 直连: /v1/files/dl/{user_id}/{thread_id}/{agent_id}/{file_id}/{expiry}/{sig}/{filename}
  - CF Tunnel 路径分发: /{env}/v1/files/dl/... (env 由 FILE_SERVER_BASE_URL 控制)

filename 仅用于客户端推断文件类型, 服务端通过签名 + attachment_registry 反查.
"""

from __future__ import annotations

import logging
import time

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from src.core.path_resolver import resolve_attachment_internal_path
from src.files.signed_url import get_signed_url_provider

logger = logging.getLogger(__name__)

router = APIRouter(tags=["files"])


_DL_PATH_SUFFIX = (
    "{user_id}/{thread_id}/{agent_id}/{file_id}/{expiry}/{sig}/{_filename:path}"
)


@router.get(f"/v1/files/dl/{_DL_PATH_SUFFIX}")
@router.get(f"/{{_env_prefix}}/v1/files/dl/{_DL_PATH_SUFFIX}")
async def download_file(
    user_id: str,
    thread_id: str,
    agent_id: str,
    file_id: str,
    expiry: int,
    sig: str,
    _filename: str,
    _env_prefix: str | None = None,
) -> FileResponse:
    """通过 HMAC 签名下载文件, URL 路径中的 filename 仅用于客户端展示."""
    provider = get_signed_url_provider()

    if expiry > 0 and time.time() > expiry:
        logger.warning(
            "[download] 链接已过期: user=%s file_id=%s expiry=%d",
            user_id,
            file_id,
            expiry,
        )
        raise HTTPException(status_code=410, detail="下载链接已过期")

    if not provider.verify(user_id, thread_id, agent_id, file_id, expiry, sig):
        logger.warning(
            "[download] 签名无效: user=%s thread=%s agent=%s file_id=%s",
            user_id,
            thread_id,
            agent_id,
            file_id,
        )
        raise HTTPException(status_code=401, detail="下载链接签名无效")

    entry = await _lookup_attachment(user_id, thread_id, agent_id, file_id)
    if entry is None:
        logger.warning(
            "[download] 附件不存在: user=%s file_id=%s",
            user_id,
            file_id,
        )
        raise HTTPException(status_code=404, detail="文件不存在或附件记录已清除")

    internal_path = (
        entry.physical_path.split("shared/", 1)[-1]
        if "shared/" in entry.physical_path
        else entry.physical_path
    )
    file_path = resolve_attachment_internal_path(
        internal_path,
        user_id,
        thread_id,
    )
    if not file_path.exists():
        logger.warning(
            "[download] 物理文件已被清理: user=%s file_id=%s path=%s",
            user_id,
            file_id,
            file_path,
        )
        await _cleanup_orphaned_record(user_id, thread_id, agent_id, file_id)
        raise HTTPException(status_code=404, detail="文件已被清理")

    logger.info(
        "[download] 文件下载: %s (%d bytes)",
        entry.filename,
        file_path.stat().st_size,
    )
    return FileResponse(
        path=str(file_path),
        filename=entry.filename,
        media_type=_guess_media_type(entry.filename),
    )


async def _lookup_attachment(
    user_id: str,
    thread_id: str,
    agent_id: str,
    file_id: str,
) -> object | None:
    """从 attachment_registry 反查附件记录."""
    service = await _get_registry_service(user_id, thread_id, agent_id)
    return await service.get(file_id)


async def _cleanup_orphaned_record(
    user_id: str,
    thread_id: str,
    agent_id: str,
    file_id: str,
) -> None:
    """惰性清理: 物理文件已被配额清理删除, 同步清理悬空的注册表记录.

    配额清理 (StorageQuotaService) 删物理文件时不清理 attachment_registry
    (跨库操作代价高), 改为下载时惰性清理: 第一次访问发现文件不存在即删记录,
    避免悬空引用累积.
    """
    try:
        service = await _get_registry_service(user_id, thread_id, agent_id)
        await service.delete(file_id)
    except Exception as e:
        logger.warning("[download] 惰性清理失败 (非致命): %s", e)


async def _get_registry_service(
    user_id: str,
    thread_id: str,  # noqa: ARG001
    agent_id: str,  # noqa: ARG001
) -> object:
    """获取文件注册表服务 (用户级, 复用实例)."""
    from src.storage.service.file_registry_service import (
        create_file_registry_service,
    )

    return await create_file_registry_service(user_id)


def _guess_media_type(filename: str) -> str:
    """根据扩展名推断 MIME 类型."""
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    mime_map = {
        "pdf": "application/pdf",
        "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "doc": "application/msword",
        "txt": "text/plain",
        "html": "text/html",
        "md": "text/markdown",
        "png": "image/png",
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
        "gif": "image/gif",
        "webp": "image/webp",
    }
    return mime_map.get(ext, "application/octet-stream")
