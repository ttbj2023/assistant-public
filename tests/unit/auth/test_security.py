"""安全验证工具测试

测试认证系统的安全验证功能,包括ID验证、安全清理和安全装饰器.
"""

import pytest

from src.auth.security import (
    IDValidator,
    SecuritySanitizer,
    generate_safe_filename,
    secure_api_key_validation,
    secure_user_thread_isolation,
    secure_validate_params,
)


class TestIDValidator:
    """ID验证器测试类."""

    @pytest.mark.parametrize(
        "user_id",
        ["user123", "test_user", "user-123", "USER123", "a", "user123_456-789"],
    )
    def test_validate_user_id_success_should_return_same_value(
        self, user_id
    ) -> None:
        """用户ID验证成功: 应返回相同值."""
        result = IDValidator.validate_user_id(user_id)
        assert result == user_id

    @pytest.mark.parametrize(
        "invalid_input,expected_error",
        [
            (None, "用户ID不能为None"),
            ("", "用户ID不能为空"),
            ("   ", "用户ID不能为空"),
            (123, "用户ID必须是字符串"),
            ("user@domain", "用户ID只能包含字母,数字,下划线和连字符"),
            ("user space", "用户ID只能包含字母,数字,下划线和连字符"),
            ("a" * 101, "用户ID长度不能超过100个字符"),
        ],
    )
    def test_validate_user_id_failure_should_raise_value_error(
        self, invalid_input, expected_error
    ) -> None:
        """用户ID验证失败: 应抛出包含提示信息的ValueError."""
        with pytest.raises(ValueError) as exc_info:
            IDValidator.validate_user_id(invalid_input)
        assert expected_error in str(exc_info.value)

    @pytest.mark.parametrize(
        "thread_id",
        ["thread123", "main_thread", "thread-123", "THREAD123", "t", "thread123_456-789"],
    )
    def test_validate_thread_id_success_should_return_same_value(
        self, thread_id
    ) -> None:
        """线程ID验证成功: 应返回相同值."""
        result = IDValidator.validate_thread_id(thread_id)
        assert result == thread_id

    @pytest.mark.parametrize(
        "invalid_input,expected_error",
        [
            (None, "线程ID不能为None"),
            ("", "线程ID不能为空"),
            ("   ", "线程ID不能为空"),
            (123, "线程ID必须是字符串"),
            ("thread@domain", "线程ID只能包含字母,数字,下划线和连字符"),
            ("thread space", "线程ID只能包含字母,数字,下划线和连字符"),
            ("t" * 101, "线程ID长度不能超过100个字符"),
        ],
    )
    def test_validate_thread_id_failure_should_raise_value_error(
        self, invalid_input, expected_error
    ) -> None:
        """线程ID验证失败: 应抛出包含提示信息的ValueError."""
        with pytest.raises(ValueError) as exc_info:
            IDValidator.validate_thread_id(invalid_input)
        assert expected_error in str(exc_info.value)

    @pytest.mark.parametrize(
        "api_key",
        [
            "sk-project-user-hash-random123",
            "sk-project-abc123-def456-xyz789",
            "sk-project-user1-thread2-key3",
        ],
    )
    def test_validate_api_key_format_success_should_return_same_value(
        self, api_key
    ) -> None:
        """API密钥格式验证成功: 应返回相同值."""
        result = IDValidator.validate_api_key_format(api_key)
        assert result == api_key

    @pytest.mark.parametrize(
        "invalid_input,expected_error",
        [
            (None, "API密钥不能为None"),
            ("", "API密钥不能为空"),
            ("   ", "API密钥不能为空"),
            (123, "API密钥必须是字符串"),
            ("invalid-key", 'API密钥必须以 "sk-project-" 开头'),
            ("sk-project-abc", "API密钥格式无效,应包含至少4个部分"),
        ],
    )
    def test_validate_api_key_format_failure_should_raise_value_error(
        self, invalid_input, expected_error
    ) -> None:
        """API密钥格式验证失败: 应抛出包含提示信息的ValueError."""
        with pytest.raises(ValueError) as exc_info:
            IDValidator.validate_api_key_format(invalid_input)
        assert expected_error in str(exc_info.value)


class TestSecuritySanitizer:
    """安全清理器测试类."""

    @pytest.mark.parametrize(
        "input_val,expected",
        [
            ("normal text", "normal text"),
            ("", ""),
            (None, ""),
            (123, "123"),
            ("   spaced text   ", "spaced text"),
            ("<script>alert('xss')</script>", "alert('xss')"),
        ],
    )
    def test_sanitize_string_basic_should_clean_correctly(
        self, input_val, expected
    ) -> None:
        """基础字符串清理: 应正确清理各种输入."""
        result = SecuritySanitizer.sanitize_string(input_val)
        assert result == expected

    def test_sanitize_string_length_limit(self) -> None:
        """测试字符串长度限制."""
        long_text = "a" * 1000
        result = SecuritySanitizer.sanitize_string(long_text, max_length=100)

        assert len(result) == 100
        assert result == "a" * 100

    def test_validate_and_sanitize_user_input(self) -> None:
        """测试验证和清理用户输入."""
        input_data = {
            "user_id": "test_user",
            "thread_id": "main_thread",
            "description": "<script>alert('xss')</script>",
            "api_key": "sk-project-test-main-123456",
            "normal_field": "normal value",
        }

        result = SecuritySanitizer.validate_and_sanitize_user_input(**input_data)

        # ID字段应该保持不变（通过验证）
        assert result["user_id"] == "test_user"
        assert result["thread_id"] == "main_thread"
        assert result["api_key"] == "sk-project-test-main-123456"

        # 普通字段应该被清理
        assert result["description"] == "alert('xss')"
        assert result["normal_field"] == "normal value"

    @pytest.mark.parametrize(
        "dangerous_input",
        [
            "eval(some_code)",
            "exec(command)",
            "__import__('os')",
            "subprocess.call",
            "os.system('rm -rf /')",
            "../../../etc/passwd",
            "%2e%2e%2fetc%2fpasswd",
            "..\\windows\\system32",
            "/etc/passwd",
            "/proc/version",
            "<script>alert('xss')</script>",
            "javascript:void(0)",
            "onclick=alert('xss')",
            "union select * from users",
            "drop table users",
            "delete from users",
            "insert into users values",
        ],
    )
    def test_critical_patterns_detection_should_identify_threats(
        self, dangerous_input
    ) -> None:
        """危险模式检测: 应识别所有危险输入."""
        contains_dangerous = any(
            pattern.search(dangerous_input)
            for pattern in SecuritySanitizer._compiled_patterns
        )
        assert contains_dangerous, f"危险输入未被检测: {dangerous_input}"


class TestSecurityDecorators:
    """安全装饰器测试类."""

    def test_secure_validate_params_success(self) -> None:
        """测试安全参数验证装饰器成功."""

        @secure_validate_params()
        def test_function(user_id, thread_id, api_key) -> str:
            return f"User: {user_id}, Thread: {thread_id}"

        result = test_function(
            user_id="test_user",
            thread_id="main_thread",
            api_key="sk-project-test-main-123456",
        )

        assert result == "User: test_user, Thread: main_thread"

    def test_secure_validate_params_failure(self) -> None:
        """测试安全参数验证装饰器失败."""

        @secure_validate_params()
        def test_function(user_id, thread_id) -> str:
            return "success"

        # 测试无效用户ID
        with pytest.raises(ValueError):
            test_function(user_id="invalid@user", thread_id="valid_thread")

        # 测试无效线程ID
        with pytest.raises(ValueError):
            test_function(user_id="valid_user", thread_id="invalid thread")

    def test_secure_validate_params_custom_params(self) -> None:
        """测试自定义参数名验证."""

        @secure_validate_params(
            user_id_param="user", thread_id_param="thread", api_key_param="key"
        )
        def test_function(user, thread, key) -> str:
            return f"User: {user}, Thread: {thread}"

        result = test_function(
            user="test_user", thread="main_thread", key="sk-project-test-main-123456"
        )

        assert result == "User: test_user, Thread: main_thread"


class TestSecurityUtilities:
    """安全工具函数测试类."""

    def test_secure_api_key_validation(self) -> None:
        """测试API密钥安全验证."""
        valid_keys = ["sk-project-user-hash-random123", "sk-project-abc-def-123456"]

        invalid_keys = ["invalid-key", "sk-project", "", None]

        for key in valid_keys:
            assert secure_api_key_validation(key) is True

        for key in invalid_keys:
            assert secure_api_key_validation(key) is False

    def test_secure_user_thread_isolation_success(self) -> None:
        """测试用户线程隔离验证成功."""
        result = secure_user_thread_isolation("user123", "thread456")
        assert result is True

    def test_secure_user_thread_isolation_failure(self) -> None:
        """测试用户线程隔离验证失败."""
        result = secure_user_thread_isolation("same_id", "same_id")
        assert result is False

        result = secure_user_thread_isolation("invalid@user", "thread")
        assert result is False

    def test_generate_safe_filename(self) -> None:
        """测试生成安全文件名."""
        # 测试基本用法
        filename = generate_safe_filename("user123", "thread456")
        assert filename == "user123_thread456"

        # 测试带后缀
        filename = generate_safe_filename("user123", "thread456", "backup")
        assert "user123" in filename
        assert "thread456" in filename
        assert "backup" in filename

        # 测试无效输入（应该返回安全的默认值）
        filename = generate_safe_filename("invalid@user", "thread", "test")
        assert filename.startswith("safe_")
        assert len(filename) == 4 + 17  # "safe_" + 17位hex
