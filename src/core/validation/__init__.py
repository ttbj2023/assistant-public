"""简化的核心验证模块.

使用成熟标准库替代复杂的自定义验证实现.
基于bleach,pydantic和简化正则表达式的实用主义验证方案.
"""

from __future__ import annotations

from typing import Any

# 导入ID验证工具 (从 auth 下沉, 供 core/auth 共享)
from .id_validator import IDValidator

# 导入安全装饰器
from .security_decorators import secure_tool_params

# 导入统一验证器
from .unified_sanitizer import UnifiedSanitizer


# 简化的安全检查函数
def sanitize_json(data: Any, strict_mode: bool = False) -> Any:  # noqa: RUF067
    """安全的JSON数据清理."""
    return UnifiedSanitizer.sanitize_json(data, strict_mode)


__all__ = [
    # ID验证工具
    "IDValidator",
    # 统一验证器
    "UnifiedSanitizer",
    # 简化函数
    "sanitize_json",
    # 安全装饰器
    "secure_tool_params",
]
