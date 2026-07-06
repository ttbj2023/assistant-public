"""文件描述写入器 - 约定路径管理 .desc.md 描述文件.

每个文件 (file_id) 对应一个独立的描述文件 .desc.md, 与主文件一一绑定,
清理时同生共死. 描述文件统一存放在用户级目录, 通过 file_id 约定推导路径,
无需在 DB 中存储路径 (运行时推导, 与物理文件位置解耦).

约定路径:
    相对路径 (相对于 user_base): files/desc/{file_id}.desc.md
    绝对路径: {user_base}/files/desc/{file_id}.desc.md

描述内容定位 (统一为"文件描述", 非原文):
    图片 → AI 视觉画面描述
    文档 → AI 摘要

设计为最佳努力 (best-effort): 写入/删除失败仅记录日志, 不抛异常,
避免描述生成故障影响主流程 (文件存储本身成功即可).
"""

from __future__ import annotations

import logging
from pathlib import Path

from src.core.path_resolver import get_user_path_resolver
from src.files.paths import FILES_DESC

logger = logging.getLogger(__name__)


def desc_relative_path(file_id: str) -> str:
    """返回描述文件相对路径 (相对于 user_base)."""
    return f"{FILES_DESC}/{file_id}.desc.md"


def desc_abs_path(user_id: str, file_id: str) -> Path:
    """返回描述文件绝对路径."""
    resolver = get_user_path_resolver()
    user_base = resolver.get_user_base_path(user_id)
    return user_base / desc_relative_path(file_id)


def write_desc(user_id: str, file_id: str, content: str) -> None:
    """写入描述文件 (最佳努力, 失败仅日志不抛异常)."""
    if not content:
        return
    try:
        path = desc_abs_path(user_id, file_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        logger.debug("📝 描述文件已写入: file_id=%s", file_id)
    except Exception as e:
        logger.warning("⚠️ 描述文件写入失败 (不影响主流程): file_id=%s, %s", file_id, e)


def read_desc(user_id: str, file_id: str) -> str | None:
    """读取描述文件, 不存在或读取失败返回 None."""
    try:
        path = desc_abs_path(user_id, file_id)
        if not path.exists():
            return None
        return path.read_text(encoding="utf-8")
    except Exception as e:
        logger.warning("⚠️ 描述文件读取失败: file_id=%s, %s", file_id, e)
        return None


def delete_desc(user_id: str, file_id: str) -> bool:
    """删除描述文件 (最佳努力), 返回是否删除了文件."""
    try:
        path = desc_abs_path(user_id, file_id)
        if path.exists():
            path.unlink()
            logger.debug("🗑️ 描述文件已删除: file_id=%s", file_id)
            return True
        return False
    except Exception as e:
        logger.warning("⚠️ 描述文件删除失败: file_id=%s, %s", file_id, e)
        return False


__all__ = [
    "delete_desc",
    "desc_abs_path",
    "desc_relative_path",
    "read_desc",
    "write_desc",
]
