"""统一认证安全验证工具

整合所有认证相关的安全验证功能,包括ID验证,安全清理和安全装饰器.
与现有安全组件保持兼容,专注于轻量级静态用户认证体系的安全需求.
"""

from __future__ import annotations

import logging
import re
from functools import wraps
from typing import TYPE_CHECKING, Any, ClassVar

if TYPE_CHECKING:
    from collections.abc import Callable

# 使用成熟的安全库
try:
    import bleach
except ImportError:
    bleach = None

# IDValidator 已下沉至 core/validation (消除 core->auth 反向依赖), 此处 re-export 保持向后兼容
from src.core.validation import IDValidator

logger = logging.getLogger(__name__)


class SecuritySanitizer:
    """统一的安全清理器."""

    # 关键安全模式 - 只保留最关键的检测
    CRITICAL_PATTERNS: ClassVar = [
        # 代码执行 - 最高危险
        r"eval\s*\(",
        r"exec\s*\(",
        r"__import__",
        r"subprocess\.",
        r"os\.system",
        # 路径遍历 - 高危险
        r"\.\.\/",
        r"%2e%2e%2f",
        r"\.\.\\",
        "/etc/passwd",
        "/proc/",
        # 脚本注入
        r"<script[^>]*>",
        r"javascript:",
        r"on\w+\s*=",
        # SQL注入
        r"union\s+select",
        r"drop\s+table",
        r"delete\s+from",
        r"insert\s+into",
    ]

    _compiled_patterns: ClassVar = [
        re.compile(pattern, re.IGNORECASE) for pattern in CRITICAL_PATTERNS
    ]

    @classmethod
    def sanitize_string(
        cls,
        value: str | float | bool | None,
        max_length: int = 1000,
    ) -> str:
        """清理字符串值."""
        if value is None:
            return ""

        if not isinstance(value, str):
            value = str(value)

        # 限制长度
        if len(value) > max_length:
            value = value[:max_length]

        # 使用bleach清理HTML(如果可用)
        if bleach:
            from contextlib import suppress

            with suppress(Exception):
                value = bleach.clean(value, strip=True)

        # 检查危险模式
        for pattern in cls._compiled_patterns:
            if pattern.search(value):
                logger.warning(f"检测到潜在危险内容: {pattern.pattern}")
                # 移除危险内容
                value = pattern.sub("", value)

        return value.strip()

    @classmethod
    def validate_and_sanitize_user_input(
        cls,
        **kwargs: str | float | bool | None,
    ) -> dict[str, str]:
        """验证和清理用户输入."""
        sanitized = {}
        for key, value in kwargs.items():
            if key.endswith("_id"):
                # ID字段使用严格验证
                if key == "user_id":
                    sanitized[key] = IDValidator.validate_user_id(value)
                elif key == "thread_id":
                    sanitized[key] = IDValidator.validate_thread_id(value)
                elif key == "api_key":
                    sanitized[key] = IDValidator.validate_api_key_format(value)
                else:
                    # 其他ID字段使用基础验证
                    sanitized[key] = cls.sanitize_string(value, max_length=100)
            else:
                # 普通字段使用清理
                sanitized[key] = cls.sanitize_string(value)

        return sanitized


def secure_validate_params(
    user_id_param: str = "user_id",
    thread_id_param: str = "thread_id",
    api_key_param: str = "api_key",
    sanitize_all: bool = False,
) -> Callable:
    """安全验证装饰器工厂函数.

    Args:
        user_id_param: 用户ID参数名
        thread_id_param: 线程ID参数名
        api_key_param: API密钥参数名
        sanitize_all: 是否清理所有参数

    Returns:
        装饰器函数

    """

    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                # 验证关键ID参数
                if user_id_param in kwargs:
                    kwargs[user_id_param] = IDValidator.validate_user_id(
                        kwargs[user_id_param],
                    )

                if thread_id_param in kwargs:
                    kwargs[thread_id_param] = IDValidator.validate_thread_id(
                        kwargs[thread_id_param],
                    )

                if api_key_param in kwargs:
                    kwargs[api_key_param] = IDValidator.validate_api_key_format(
                        kwargs[api_key_param],
                    )

                # 可选: 清理所有参数
                if sanitize_all:
                    kwargs = SecuritySanitizer.validate_and_sanitize_user_input(
                        **kwargs,
                    )

                return func(*args, **kwargs)

            except ValueError as e:
                logger.error("参数验证失败: %s", e)
                raise  # 重新抛出异常,让上层处理

        return wrapper

    return decorator


def secure_api_key_validation(api_key: str) -> bool:
    """快速API密钥安全验证."""
    try:
        IDValidator.validate_api_key_format(api_key)
        return True
    except ValueError:
        return False


def secure_user_thread_isolation(user_id: str, thread_id: str) -> bool:
    """验证用户和线程隔离安全性."""
    try:
        validated_user_id = IDValidator.validate_user_id(user_id)
        validated_thread_id = IDValidator.validate_thread_id(thread_id)

        # 额外的隔离检查:确保用户和线程ID不相同
        if validated_user_id == validated_thread_id:
            logger.warning("用户ID和线程ID不应该相同: %s", validated_user_id)
            return False

        return True
    except ValueError as e:
        logger.error("用户线程隔离验证失败: %s", e)
        return False


def generate_safe_filename(user_id: str, thread_id: str, suffix: str = "") -> str:
    """生成安全的文件名用于数据隔离."""
    try:
        safe_user_id = IDValidator.validate_user_id(user_id)
        safe_thread_id = IDValidator.validate_thread_id(thread_id)

        # 清理后缀
        if suffix:
            safe_suffix = SecuritySanitizer.sanitize_string(suffix, max_length=50)
            return f"{safe_user_id}_{safe_thread_id}_{safe_suffix}"
        return f"{safe_user_id}_{safe_thread_id}"
    except ValueError:
        # 如果验证失败,返回安全的默认值
        import uuid

        return f"safe_{uuid.uuid4().hex[:16]}"


# 导出主要类和函数
__all__ = [
    "IDValidator",
    "SecuritySanitizer",
    "generate_safe_filename",
    "secure_api_key_validation",
    "secure_user_thread_isolation",
    "secure_validate_params",
]
