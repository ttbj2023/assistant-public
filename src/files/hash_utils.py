"""文件内容哈希工具.

提供文件内容 SHA-256 计算用于去重. 替代旧 FileDeduplicationService.compute_hash.
"""

from __future__ import annotations

import hashlib


def compute_hash(data: bytes) -> str:
    """计算文件内容的 SHA-256 哈希.

    Args:
        data: 文件二进制内容

    Returns:
        SHA-256 哈希值 (64位hex字符串)

    """
    return hashlib.sha256(data).hexdigest()


__all__ = ["compute_hash"]
