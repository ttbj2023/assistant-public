"""code_extensions 单元测试.

验证代码文件扩展名白名单识别.
"""

from __future__ import annotations

import pytest

from src.files.code_extensions import is_code_file


class TestIsCodeFile:
    """测试代码文件扩展名识别."""

    @pytest.mark.parametrize(
        "filename",
        [
            "main.py",
            "app.js",
            "index.ts",
            "component.tsx",
            "main.go",
            "lib.rs",
            "Main.java",
            "Program.cs",
            "script.sh",
            "query.sql",
            "App.vue",
            "Gemfile.rb",
        ],
    )
    def test_common_code_extensions_match(self, filename: str):
        assert is_code_file(filename) is True

    @pytest.mark.parametrize(
        "filename",
        [
            "README.md",
            "notes.txt",
            "config.yaml",
            "config.json",
            "pyproject.toml",
            "data.csv",
            "image.png",
            "noext",
        ],
    )
    def test_non_code_extensions_do_not_match(self, filename: str):
        assert is_code_file(filename) is False

    def test_case_insensitive(self):
        assert is_code_file("MAIN.PY") is True

    def test_plain_name_python_shebang_treated_by_extension_only(self):
        """识别只看扩展名, 不看内容."""
        assert is_code_file("not_python.txt") is False
