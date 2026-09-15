"""代码文件扩展名白名单 - 识别上传文件是否为代码文件.

代码文件在 store_document 后额外触发后台 AI 摘要生成
(desc = AI 摘要 + 代码原文). 识别只看扩展名, 简单确定性.
"""

from __future__ import annotations

from pathlib import PurePosixPath

# 常见编程语言源码扩展名 (不含 yaml/json/toml 等配置格式)
CODE_EXTENSIONS: frozenset[str] = frozenset({
    ".py",
    ".pyi",
    ".js",
    ".jsx",
    ".mjs",
    ".cjs",
    ".ts",
    ".tsx",
    ".go",
    ".rs",
    ".java",
    ".kt",
    ".kts",
    ".c",
    ".h",
    ".cpp",
    ".cc",
    ".cxx",
    ".hpp",
    ".hh",
    ".cs",
    ".rb",
    ".php",
    ".swift",
    ".m",
    ".mm",
    ".scala",
    ".sh",
    ".bash",
    ".zsh",
    ".ps1",
    ".sql",
    ".lua",
    ".r",
    ".pl",
    ".vue",
    ".svelte",
    ".dart",
    ".ex",
    ".exs",
    ".erl",
    ".hs",
    ".clj",
    ".groovy",
    ".vb",
})


def is_code_file(filename: str) -> bool:
    """判断文件名是否为代码文件 (按扩展名白名单, 大小写不敏感).

    Args:
        filename: 文件名 (可含路径分隔符)

    Returns:
        是否代码文件
    """
    suffix = PurePosixPath(filename.replace("\\", "/")).suffix.lower()
    return suffix in CODE_EXTENSIONS


__all__ = ["CODE_EXTENSIONS", "is_code_file"]
