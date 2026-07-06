"""Storage模块配置系统 - 简化版Pydantic配置对象

当前仅支持SQLite数据库和ChromaDB向量存储, 所有路径由path_resolver统一管理.

## 配置类结构

### StorageConfig (主配置类)
- `file_store`: 文件存储配额与去重配置 (FileStoreConfig)

### FileStoreConfig (文件存储配置)
- `max_user_storage_mb`: 用户最大文件存储空间
- `cleanup_target_mb`: 超限清理目标空间
- `deduplication_enabled`: 启用文件内容去重
- `quota_check_enabled`: 启用用户配额检查与自动清理
"""

from __future__ import annotations

import logging
from typing import Any, override

from pydantic import BaseModel, Field

from .base_config import BaseConfig
from .config_loader import get_module_config_sync

logger = logging.getLogger(__name__)


class FileStoreConfig(BaseModel):
    """文件存储配额与去重配置."""

    max_user_storage_mb: int = Field(
        default=500,
        description="用户最大文件存储空间(MB)",
    )
    cleanup_target_mb: int = Field(
        default=400,
        description="超限清理目标空间(MB)",
    )
    deduplication_enabled: bool = Field(
        default=True,
        description="启用文件内容去重",
    )
    quota_check_enabled: bool = Field(
        default=True,
        description="启用用户配额检查与自动清理",
    )


class StorageConfig(BaseConfig):
    """Storage模块主配置类"""

    _module_name = "storage"

    # 嵌套配置对象
    file_store: FileStoreConfig = Field(
        default_factory=FileStoreConfig,
        description="文件存储配额与去重配置",
    )

    @classmethod
    @override
    def from_module_config(cls) -> StorageConfig:
        """从 config.yaml 创建配置对象.

        Returns:
            配置对象实例

        """
        # 获取YAML配置
        yaml_config = get_module_config_sync("storage") or {}

        return cls.from_dict(yaml_config)


# === 配置获取函数 ===


_cached: StorageConfig | None = None


def get_config() -> StorageConfig:
    """获取Storage模块配置对象(推荐方式)

    Returns:
        Storage配置对象实例

    """
    global _cached
    if _cached is None:
        _cached = StorageConfig.from_module_config()
    return _cached


def get_default_config() -> dict[str, Any]:
    """获取Storage模块默认配置字典(兜底边界)

    Returns:
        Storage模块默认配置字典

    """
    return StorageConfig.get_default_config()


# === 导出接口 ===
__all__ = [
    "FileStoreConfig",
    # 配置类
    "StorageConfig",
    # 配置获取函数
    "get_config",  # 配置对象接口
    "get_default_config",  # 默认配置字典
]
