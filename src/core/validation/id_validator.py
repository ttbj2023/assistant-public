"""统一的ID验证工具.

从 src/auth/security.py 下沉至 core/validation, 消除 core->auth 的反向依赖.
IDValidator 是纯静态校验 (仅依赖 re 标准库), 不含任何业务/认证逻辑,
适合作为 core 层基础校验工具, 供 core 与 auth 共享.
"""

from __future__ import annotations

import re


class IDValidator:
    """统一的ID验证工具类."""

    # ID格式验证规则
    ID_PATTERN = re.compile(r"^[a-zA-Z0-9_-]+$")
    MIN_ID_LENGTH = 1
    MAX_ID_LENGTH = 100

    @classmethod
    def validate_user_id(cls, user_id: str | int | None) -> str:
        """验证用户ID的有效性."""
        if user_id is None:
            raise ValueError("用户ID不能为None")

        if not isinstance(user_id, str):
            raise ValueError(f"用户ID必须是字符串,当前类型: {type(user_id)}")

        user_id = user_id.strip()
        if not user_id:
            raise ValueError("用户ID不能为空")

        if len(user_id) < cls.MIN_ID_LENGTH:
            raise ValueError(f"用户ID长度不能少于{cls.MIN_ID_LENGTH}个字符")

        if len(user_id) > cls.MAX_ID_LENGTH:
            raise ValueError(f"用户ID长度不能超过{cls.MAX_ID_LENGTH}个字符")

        if not cls.ID_PATTERN.match(user_id):
            raise ValueError("用户ID只能包含字母,数字,下划线和连字符")

        return user_id

    @classmethod
    def validate_thread_id(cls, thread_id: str | int | None) -> str:
        """验证线程ID的有效性."""
        if thread_id is None:
            raise ValueError("线程ID不能为None")

        if not isinstance(thread_id, str):
            raise ValueError(f"线程ID必须是字符串,当前类型: {type(thread_id)}")

        thread_id = thread_id.strip()
        if not thread_id:
            raise ValueError("线程ID不能为空")

        if len(thread_id) < cls.MIN_ID_LENGTH:
            raise ValueError(f"线程ID长度不能少于{cls.MIN_ID_LENGTH}个字符")

        if len(thread_id) > cls.MAX_ID_LENGTH:
            raise ValueError(f"线程ID长度不能超过{cls.MAX_ID_LENGTH}个字符")

        if not cls.ID_PATTERN.match(thread_id):
            raise ValueError("线程ID只能包含字母,数字,下划线和连字符")

        return thread_id

    @classmethod
    def validate_api_key_format(cls, api_key: str | None) -> str:
        """验证API密钥格式."""
        if api_key is None:
            raise ValueError("API密钥不能为None")

        if not isinstance(api_key, str):
            raise ValueError(f"API密钥必须是字符串,当前类型: {type(api_key)}")

        api_key = api_key.strip()
        if not api_key:
            raise ValueError("API密钥不能为空")

        if not api_key.startswith("sk-project-"):
            raise ValueError('API密钥必须以 "sk-project-" 开头')

        parts = api_key.split("-")
        if len(parts) < 4:
            raise ValueError("API密钥格式无效,应包含至少4个部分")

        return api_key


__all__ = ["IDValidator"]
