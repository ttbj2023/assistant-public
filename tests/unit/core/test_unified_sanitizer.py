"""统一安全验证器单元测试."""

import pytest

from src.core.validation.unified_sanitizer import (
    UnifiedSanitizer,
)


class TestUnifiedSanitizer:
    """UnifiedSanitizer单元测试"""

    @pytest.mark.unit
    def test_sanitize_string_basic(self) -> None:
        sanitizer = UnifiedSanitizer()

        safe_text = "Hello, World!"
        result = sanitizer.sanitize_string(safe_text)
        assert result == safe_text

        html_text = "<script>alert('xss')</script>Hello"
        result = sanitizer.sanitize_string(html_text)
        assert "<script>" not in result
        assert "Hello" in result

        html_safe = "<b>Hello</b>"
        result = sanitizer.sanitize_string(html_safe, allow_html=True)
        assert result == html_safe

    @pytest.mark.unit
    def test_sanitize_string_security_patterns(self) -> None:
        sanitizer = UnifiedSanitizer()

        dangerous_patterns = [
            "eval('malicious code')",
            "exec('bad code')",
            "__import__('os')",
            "subprocess.call('rm -rf /')",
            "os.system('rm -rf /')",
        ]

        for pattern in dangerous_patterns:
            with pytest.raises(ValueError):
                sanitizer.sanitize_string(pattern)

    @pytest.mark.unit
    def test_sanitize_json_data(self) -> None:
        sanitizer = UnifiedSanitizer()

        safe_json = {"message": "Hello, World!", "number": 42}
        result = sanitizer.sanitize_json(safe_json)
        assert result == safe_json

        dangerous_json = {
            "user_input": "<script>alert('xss')</script>",
            "code": "function test() { return 1; }",
            "safe_data": "Hello",
        }
        result = sanitizer.sanitize_json(dangerous_json)

        assert "<script>" not in str(result)
        assert "Hello" in str(result)

    @pytest.mark.unit
    def test_sanitize_json_nested_structure(self) -> None:
        sanitizer = UnifiedSanitizer()

        nested_json = {
            "level1": {
                "level2": {"dangerous": "<script>alert(1)</script>", "safe": "Hello"}
            },
            "array": [{"item": "safe content"}, {"item": "<script>evil()</script>"}],
        }

        result = sanitizer.sanitize_json(nested_json)

        result_str = str(result)
        assert "<script>" not in result_str
        assert "Hello" in result_str
        assert "safe content" in result_str

    @pytest.mark.unit
    def test_sanitize_json_strict_mode(self) -> None:
        sanitizer = UnifiedSanitizer()

        dangerous_json = {"code": "<script>alert('xss')</script>"}

        result = sanitizer.sanitize_json(dangerous_json, strict_mode=False)
        assert result is not None

        with pytest.raises(ValueError):
            sanitizer.sanitize_json(dangerous_json, strict_mode=True)

    @pytest.mark.unit
    def test_quick_security_check(self) -> None:
        sanitizer = UnifiedSanitizer()

        safe_content = "This is safe content"
        try:
            sanitizer.quick_security_check(safe_content)
        except ValueError:
            pytest.fail("Safe content should not raise error")

        dangerous_patterns = [
            "<script>alert('xss')</script>",
            "eval('malicious code')",
            "../../etc/passwd",
            "'; DROP TABLE users; --",
        ]

        for pattern in dangerous_patterns:
            with pytest.raises(ValueError):
                sanitizer.quick_security_check(pattern)
