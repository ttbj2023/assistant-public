"""Assistant项目核心模块.

提供Agent,状态管理,工具调用等核心功能.
"""

# Core imports are available but not exposed at package level
# Import specific modules as needed: from core import constants, path_resolver, etc.

# 从版本管理模块导入版本号
from __future__ import annotations

try:  # noqa: RUF067
    from ._version import __version__
except ImportError:
    __version__ = "1.0.0-dev"

# 导出版本信息
__all__ = ["__version__"]
