"""统一安全验证器 - 合并SimpleSanitizer和JsonSanitizer的最佳功能

使用成熟的标准库(bleach,pydantic)替代133个正则表达式的复杂实现.
专注于实用主义,解决真正需要的安全问题.

设计原则:
- 使用成熟库而非自定义正则表达式
- 高性能:<0.1ms验证开销
- 简单易用:1行代码完成集成
- 向后兼容:不破坏现有代码
"""

from __future__ import annotations

import importlib
import logging
import re
from typing import Any, ClassVar

# 使用成熟的安全库
import bleach

logger = logging.getLogger(__name__)


class UnifiedSanitizer:
    """统一的安全验证器.

    合并SimpleSanitizer和JsonSanitizer的功能,使用标准库和成熟第三方库.
    """

    # 精简的关键安全模式 - 只保留最关键的20个模式
    CRITICAL_PATTERNS: ClassVar[list[str]] = [
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
        r"/etc/passwd",
        r"/proc/",
        # XSS - 中等危险
        r"<script[^>]*>",
        r"javascript:",
        r"vbscript:",
        # SQL注入 - 高危险
        r"(?i)\bunion\b.*\bselect\b",
        r"(?i)\bdrop\s+table\b",
        r"(?i)\bdelete\s+from\b",
        r"(?i)\bor\s+['\"]*\d+['\"]*\s*=\s*['\"]*\d+['\"]*",
    ]

    # 编译正则表达式以提高性能
    COMPILED_PATTERNS: ClassVar[list[re.Pattern[str]]] = [
        re.compile(pattern, re.IGNORECASE) for pattern in CRITICAL_PATTERNS
    ]

    @classmethod
    def sanitize_string(cls, text: str, allow_html: bool = False) -> str:
        """清理字符串内容.

        Args:
            text: 输入文本
            allow_html: 是否允许HTML标签

        Returns:
            清理后的安全文本

        """
        if not isinstance(text, str):
            raise ValueError(f"输入必须是字符串,得到{type(text)}")

        # 1. 使用bleach清理HTML(如果需要)
        if not allow_html:
            text = bleach.clean(text, strip=True)

        # 2. 检查关键安全模式
        for pattern in cls.COMPILED_PATTERNS:
            if pattern.search(text):
                logger.error(f"❌ 检测到危险内容,拒绝处理: {pattern.pattern}")
                raise ValueError(
                    f"输入包含不安全内容,已被安全系统拦截: {pattern.pattern}",
                )

        # 3. 基础清理
        return text.strip()

    @classmethod
    def sanitize_json(cls, data: Any, strict_mode: bool = False) -> Any:
        """清理JSON数据.

        Args:
            data: 要清理的数据
            strict_mode: 严格模式,如果为True则遇到危险内容时抛出异常

        Returns:
            清理后的数据

        """

        def _sanitize_recursive(obj: Any, depth: int = 0) -> Any:
            # 防止过深的嵌套(DoS防护)
            if depth > 10:
                if strict_mode:
                    raise ValueError("数据嵌套层级过深,可能为DoS攻击")
                return "[嵌套层级过深]"

            if isinstance(obj, str):
                if strict_mode:
                    # 严格模式下检测危险内容就抛出异常
                    cls.quick_security_check(obj)
                    return cls.sanitize_string(obj)
                return cls.sanitize_string(obj)
            if isinstance(obj, dict):
                return {
                    key: _sanitize_recursive(value, depth + 1)
                    for key, value in obj.items()
                }
            if isinstance(obj, list):
                return [_sanitize_recursive(item, depth + 1) for item in obj]
            return obj

        return _sanitize_recursive(data)

    @classmethod
    def sanitize(cls, data: Any, strict_mode: bool = False) -> Any:
        """通用清理方法(向后兼容)."""
        return cls.sanitize_json(data, strict_mode)

    @classmethod
    def quick_security_check(cls, data: str) -> None:
        """快速安全检查."""
        for pattern in cls.COMPILED_PATTERNS:
            if pattern.search(data):
                raise ValueError(f"检测到潜在危险内容: {pattern.pattern}")

    @classmethod
    def is_safe_class_path(cls, class_path: str) -> bool:
        """基于白名单前缀的类路径验证.

        Args:
            class_path: 要验证的类路径,如 "src.tools.internal.todo_tool.TodoTool"

        Returns:
            bool: 如果类路径安全返回True,否则返回False

        """
        # 定义允许的前缀白名单
        allowed_prefixes = [
            "src.tools.internal.",
            "src.tools.mcp.",
            "src.tools.external.",
            "src.tools.skills.",
        ]

        # 检查是否以允许的前缀开头
        return any(class_path.startswith(prefix) for prefix in allowed_prefixes)

    @classmethod
    def safe_import(cls, module_path: str, class_name: str) -> type:
        """安全的动态导入实现.

        Args:
            module_path: 模块路径,如 "src.tools.internal.todo_tool"
            class_name: 类名,如 "TodoTool"

        Returns:
            type: 导入的类

        Raises:
            ValueError: 如果导入路径不安全或导入失败

        """
        # 构建完整类路径进行安全检查
        full_class_path = f"{module_path}.{class_name}"

        if not cls.is_safe_class_path(full_class_path):
            raise ValueError(f"不安全的导入路径: {full_class_path}")

        try:
            module = importlib.import_module(module_path)
            return getattr(module, class_name)
        except ImportError as e:
            raise ValueError(f"导入模块失败: {module_path}") from e
        except AttributeError as e:
            raise ValueError(f"类不存在: {class_name} in {module_path}") from e

    @classmethod
    def sanitize_tool_params(cls, params: dict[str, Any]) -> dict[str, Any]:
        """工具参数清理.

        Args:
            params: 要清理的工具参数字典

        Returns:
            dict[str, Any]: 清理后的安全参数

        """
        return cls.sanitize_json(params, strict_mode=False)


# 导出主要接口
__all__ = [
    "UnifiedSanitizer",
]
