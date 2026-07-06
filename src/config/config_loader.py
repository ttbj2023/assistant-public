"""配置加载器 - 专注于 YAML 文件加载

简化版配置加载,只负责:
1. 加载 config.yaml
2. 返回配置字典
3. 支持模块级别的配置缓存

运行时环境和凭据分别由 runtime_env.py / credentials_registry.py 管理.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

# 全局缓存
_config_cache: dict[str, Any] = {}

# 使用模块级 _cached 缓存的配置模块名 (reset_config_cache 据此清理)
# 新增配置模块若采用 _cached 模式, 必须在此注册, 否则测试间缓存不会被清理
_CACHED_MODULE_NAMES: tuple[str, ...] = (
    "app_config",
    "api_config",
    "auth_config",
    "core_config",
    "inference_config",
    "openclaw_config",
    "smtp_config",
    "storage_config",
    "tools_config",
)

# 定义项目根目录
PROJECT_ROOT = Path(__file__).parent.parent.parent


def clear_cache() -> None:
    """清除配置缓存"""
    _config_cache.clear()


def load_base_config_sync() -> dict[str, Any]:
    """同步加载基础配置(供配置类使用,未废弃)

    这个函数专门供配置类使用,不会被废弃装饰器影响.

    Returns:
        从 config.yaml 加载的配置字典

    """
    # 加载config.yaml
    # 刻意设计的固定路径搜索顺序:
    # - 保持部署简单性,避免复杂的环境变量配置
    # - 支持两种标准项目结构:根目录直接放置 vs config子目录
    # - 优先根目录符合开发习惯,config子目录作为标准备选
    config_file = PROJECT_ROOT / "config.yaml"
    if not config_file.exists():
        config_file = PROJECT_ROOT / "config" / "config.yaml"

    yaml_config: dict[str, Any] = {}
    if config_file.exists():
        with Path(config_file).open(encoding="utf-8") as f:
            yaml_config = yaml.safe_load(f) or {}

    return yaml_config


def get_module_config_sync(module_name: str) -> dict[str, Any]:
    """同步获取模块配置(供配置类使用,未废弃)

    这个函数专门供配置类的 from_module_config() 方法使用,
    不会被废弃装饰器影响.

    Args:
        module_name: 模块名称(如 "storage", "api", "memory")

    Returns:
        模块配置字典

    """
    if module_name not in _config_cache:
        base_config = load_base_config_sync()
        _config_cache[module_name] = base_config.get(module_name, {})

    return _config_cache[module_name]


def reset_config_cache() -> None:
    """重置所有配置模块的 Pydantic 实例缓存 (仅用于测试)."""
    from importlib import import_module

    try:
        from .app_config import reset_app_config_cache

        reset_app_config_cache()
    except ImportError:
        pass

    for module_name in _CACHED_MODULE_NAMES:
        mod = import_module(f"src.config.{module_name}")
        mod._cached = None
