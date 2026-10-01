"""用户级 Microsoft Graph OAuth token 文件存储.

- 路径: data/{user_id}/credentials/graph_msa_tokens.json
- 权限: 文件 0600 / 目录 0700; refresh_token 每次刷新轮换, 半写即整体失效,
  必须 tmp + os.replace 原子回写
- 不进 credentials_registry (进程级全局密钥体系), 本件是用户级凭证,
  随用户数据目录隔离与备份; 不进 SQLite (避免随 db 备份/导出扩散)
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any

from src.core.validation import IDValidator

logger = logging.getLogger(__name__)

_CREDENTIALS_DIRNAME = "credentials"
_TOKEN_FILENAME = "graph_msa_tokens.json"


class GraphTokenStore:
    """单用户的 Graph OAuth token 文件存取."""

    def __init__(self, user_id: str, *, base_path: Path | None = None) -> None:
        """初始化 token 存储路径.

        Args:
            user_id: 用户ID (经 IDValidator 校验, 拒绝路径穿越)
            base_path: 数据根目录 (默认用户数据根, 测试注入临时目录)

        """
        from src.core.path_resolver import get_user_path_resolver

        safe_user_id = IDValidator.validate_user_id(user_id)
        root = (
            base_path if base_path is not None else get_user_path_resolver().base_path
        )
        self._path = root / safe_user_id / _CREDENTIALS_DIRNAME / _TOKEN_FILENAME

    @property
    def path(self) -> Path:
        """token 文件路径."""
        return self._path

    def exists(self) -> bool:
        """token 文件是否存在."""
        return self._path.exists()

    def load(self) -> dict[str, Any] | None:
        """读取 token 字典.

        Returns:
            token 字典; 文件不存在返回 None

        Raises:
            RuntimeError: 文件存在但解析失败 (半写/损坏)

        """
        if not self._path.exists():
            return None
        try:
            with open(self._path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            raise RuntimeError(f"Graph token 文件损坏, 请重新授权: {self._path}") from e
        if not isinstance(data, dict):
            raise RuntimeError(f"Graph token 文件结构非法: {self._path}")
        return data

    async def save(self, tokens: dict[str, Any]) -> None:
        """原子写入 token 字典 (覆盖旧值).

        async 形态满足 MSGraphClient.on_tokens_updated 的 Awaitable 契约
        (refresh_tokens 内 await 调用; 同步形态曾致每轮 token 过期后
        该轮同步失败 — "object NoneType can't be used in 'await'").


        Args:
            tokens: OAuth token 响应 (含 access_token / refresh_token)

        Raises:
            OSError: 写入失败

        """
        await asyncio.to_thread(self._write_tokens, tokens)

    def _write_tokens(self, tokens: dict[str, Any]) -> None:
        """同步原子写 (经 to_thread 在线程池执行, 不阻塞事件循环)."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(self._path.parent, 0o700)
        tmp_path = self._path.with_name(self._path.name + ".tmp")
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(tokens, f, ensure_ascii=False)
        os.chmod(tmp_path, 0o600)
        os.replace(tmp_path, self._path)
        logger.debug("Graph token 已写入: %s", self._path)

    def delete(self) -> bool:
        """删除 token 文件 (解绑).

        Returns:
            文件存在并删除返回 True, 不存在返回 False

        """
        if not self._path.exists():
            return False
        self._path.unlink()
        logger.info("Graph token 已删除: %s", self._path)
        return True


__all__ = ["GraphTokenStore"]
