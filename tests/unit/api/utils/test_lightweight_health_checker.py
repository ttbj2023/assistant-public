"""轻量级健康检查器单元测试.

测试 src/api/utils/lightweight_health_checker.py 模块的核心业务逻辑,
Mock所有外部依赖（配置、环境变量、importlib）。
"""

from __future__ import annotations

from unittest.mock import patch

from src.api.utils.lightweight_health_checker import (
    check_dependency_availability,
    check_embedding_model_availability,
    check_model_metadata,
    check_provider_requirements,
    get_inference_model_config,
)


class TestCheckDependencyAvailability:
    """测试依赖可用性检查."""

    def test_check_dependency_availability_should_return_true_when_module_exists(self):
        """测试依赖检查：模块存在时返回True."""
        # Act
        result = check_dependency_availability("os")

        # Assert
        assert result is True

    def test_check_dependency_availability_should_return_false_when_module_missing(
        self,
    ):
        """测试依赖检查：模块不存在时返回False."""
        # Act
        result = check_dependency_availability("definitely_not_a_real_module_xyz123")

        # Assert
        assert result is False


class TestCheckProviderRequirements:
    """测试provider要求检查."""

    def test_check_provider_requirements_should_return_available_for_local(self):
        """测试provider检查：local provider应总是可用."""
        # Act
        result = check_provider_requirements("local")

        # Assert
        assert result["provider"] == "local"
        assert result["dependencies_available"] is True
        assert result["env_vars_available"] is True
        assert result["overall_available"] is True
        assert result["optional"] is True

    def test_check_provider_requirements_should_return_unavailable_when_openai_missing(
        self,
    ):
        """测试provider检查：openai依赖缺失时不可用."""
        # Act
        with (
            patch(
                "src.api.utils.lightweight_health_checker.check_dependency_availability",
                return_value=False,
            ),
            patch("os.getenv", return_value=""),
        ):
            result = check_provider_requirements("openai")

        # Assert
        assert result["provider"] == "openai"
        assert result["dependencies_available"] is False
        assert result["env_vars_available"] is False
        assert "openai" in result["missing_dependencies"]
        assert "OPENAI_API_KEY" in result["missing_env_vars"]
        assert result["overall_available"] is False
        assert result["optional"] is False

    def test_check_provider_requirements_should_return_available_when_openai_present(
        self,
    ):
        """测试provider检查：openai依赖齐全时可用."""
        # Act
        with (
            patch(
                "src.api.utils.lightweight_health_checker.check_dependency_availability",
                return_value=True,
            ),
            patch("os.getenv", return_value="sk-xxx"),
        ):
            result = check_provider_requirements("openai")

        # Assert
        assert result["dependencies_available"] is True
        assert result["env_vars_available"] is True
        assert result["overall_available"] is True

    def test_check_provider_requirements_should_treat_unknown_provider_as_optional(
        self,
    ):
        """测试provider检查：未知provider应按可选处理."""
        # Act
        result = check_provider_requirements("unknown_provider")

        # Assert
        assert result["optional"] is True
        assert result["overall_available"] is True


class TestCheckModelMetadata:
    """测试模型元数据检查."""

    def test_check_model_metadata_should_return_metadata_when_registered(self):
        """测试元数据检查：已注册模型应返回元数据."""

        # Arrange
        class FakeCap:
            def __init__(self, value):
                self.value = value

        class FakeMetadata:
            model_type = type("EnumObj", (), {"value": "llm"})()
            provider = "openai"
            capabilities = (FakeCap("chat"),)
            name = "测试模型"
            description = "测试描述"

        # Act
        with patch(
            "src.inference.llm.definitions.model_metadata_checker.get_model",
            return_value=FakeMetadata(),
        ):
            result = check_model_metadata("openai:gpt-test")

        # Assert
        assert result["available"] is True
        assert result["model_id"] == "openai:gpt-test"
        assert result["name"] == "测试模型"
        assert result["description"] == "测试描述"

    def test_check_model_metadata_should_return_unavailable_when_not_registered(self):
        """测试元数据检查：未注册模型应返回不可用."""
        # Act
        with patch(
            "src.inference.llm.definitions.model_metadata_checker.get_model",
            return_value=None,
        ):
            result = check_model_metadata("unknown:model")

        # Assert
        assert result["available"] is False
        assert "error" in result

    def test_check_model_metadata_should_return_error_on_exception(self):
        """测试元数据检查：异常时应返回错误."""
        # Act
        with patch(
            "src.inference.llm.definitions.model_metadata_checker.get_model",
            side_effect=RuntimeError("boom"),
        ):
            result = check_model_metadata("openai:gpt-test")

        # Assert
        assert result["available"] is False
        assert "boom" in result["error"]


class TestCheckEmbeddingModelAvailability:
    """测试嵌入模型可用性检查."""

    def test_check_embedding_should_return_error_when_empty(self):
        """测试嵌入检查：空模型ID应返回错误."""
        # Act
        result = check_embedding_model_availability("")

        # Assert
        assert result["available"] is False
        assert "未配置" in result["error"]

    def test_check_embedding_should_return_error_when_invalid_format(self):
        """测试嵌入检查：错误格式应返回错误."""
        # Act
        result = check_embedding_model_availability("invalid_no_colon")

        # Assert
        assert result["available"] is False
        assert "格式错误" in result["error"]

    def test_check_embedding_should_return_unavailable_when_metadata_missing(self):
        """测试嵌入检查：元数据缺失时应不可用."""
        # Act
        with patch(
            "src.api.utils.lightweight_health_checker.check_model_metadata",
            return_value={"available": False, "model_id": "x:y", "error": "未注册"},
        ):
            result = check_embedding_model_availability("x:y")

        # Assert
        assert result["available"] is False
        assert result["check_type"] == "lightweight"

    def test_check_embedding_should_return_available_for_optional_provider(self):
        """测试嵌入检查：可选provider元数据可用时应可用."""
        # Act
        with (
            patch(
                "src.api.utils.lightweight_health_checker.check_model_metadata",
                return_value={"available": True, "model_id": "local:foo"},
            ),
            patch(
                "src.api.utils.lightweight_health_checker.check_provider_requirements",
                return_value={
                    "optional": True,
                    "overall_available": False,
                },
            ),
        ):
            result = check_embedding_model_availability("local:foo")

        # Assert
        assert result["available"] is True

    def test_check_embedding_should_return_unavailable_when_required_provider_fails(
        self,
    ):
        """测试嵌入检查：必需provider失败时应不可用."""
        # Act
        with (
            patch(
                "src.api.utils.lightweight_health_checker.check_model_metadata",
                return_value={"available": True, "model_id": "openai:foo"},
            ),
            patch(
                "src.api.utils.lightweight_health_checker.check_provider_requirements",
                return_value={
                    "optional": False,
                    "overall_available": False,
                },
            ),
        ):
            result = check_embedding_model_availability("openai:foo")

        # Assert
        assert result["available"] is False


class TestGetInferenceModelConfig:
    """测试推理模型配置获取."""

    def test_get_config_should_return_complete_when_configured(self):
        """测试配置获取：完整配置时应返回available=True."""
        # Arrange
        fake_config = {
            "model": {
                "model_id": "openai:gpt",
                "embedding_model_id": "openai:ada",
            }
        }

        # Act
        with patch(
            "src.api.utils.lightweight_health_checker.get_inference_config",
            return_value=fake_config,
        ):
            result = get_inference_model_config()

        # Assert
        assert result["available"] is True
        assert result["llm_model_id"] == "openai:gpt"
        assert result["embedding_model_id"] == "openai:ada"
        assert result["config_complete"] is True

    def test_get_config_should_return_incomplete_when_missing(self):
        """测试配置获取：缺失模型ID时config_complete=False."""
        # Arrange
        fake_config = {"model": {}}

        # Act
        with patch(
            "src.api.utils.lightweight_health_checker.get_inference_config",
            return_value=fake_config,
        ):
            result = get_inference_model_config()

        # Assert
        assert result["available"] is True
        assert result["config_complete"] is False

    def test_get_config_should_handle_exception(self):
        """测试配置获取：异常时返回available=False."""
        # Act
        with patch(
            "src.api.utils.lightweight_health_checker.get_inference_config",
            side_effect=RuntimeError("config error"),
        ):
            result = get_inference_model_config()

        # Assert
        assert result["available"] is False
        assert "config error" in result["error"]
