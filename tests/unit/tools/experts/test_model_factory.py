"""ExpertModelFactory 单元测试 - create_for_tool 的 model_id 覆盖."""

from __future__ import annotations

from unittest.mock import patch

from src.tools.experts.model_factory import ExpertModelFactory


class TestExpertModelFactoryCreateForTool:
    def test_uses_provided_model_id_when_given(self):
        """显式传入 model_id 时应优先使用它 (而非配置中的默认值)."""
        with (
            patch("src.tools.experts.model_factory.create_llm") as mock_create_llm,
            patch(
                "src.config.inference_config.get_config",
            ) as mock_cfg,
        ):
            mock_cfg.return_value.experts.get_model_params.return_value = {}

            ExpertModelFactory.create_for_tool(
                "web_research",
                model_id="custom:override",
            )

            mock_create_llm.assert_called_once_with("custom:override")

    def test_falls_back_to_config_model_id_when_none(self):
        """未传 model_id 时回退到配置中的默认模型."""
        with (
            patch("src.tools.experts.model_factory.create_llm") as mock_create_llm,
            patch(
                "src.config.inference_config.get_config",
            ) as mock_cfg,
        ):
            mock_cfg.return_value.experts.get_model_id.return_value = "config:default"
            mock_cfg.return_value.experts.get_model_params.return_value = {}

            ExpertModelFactory.create_for_tool("web_research")

            mock_create_llm.assert_called_once_with("config:default")

    def test_empty_string_model_id_falls_back_to_config(self):
        """空字符串 model_id 视为未提供, 回退配置."""
        with (
            patch("src.tools.experts.model_factory.create_llm") as mock_create_llm,
            patch(
                "src.config.inference_config.get_config",
            ) as mock_cfg,
        ):
            mock_cfg.return_value.experts.get_model_id.return_value = "config:default"
            mock_cfg.return_value.experts.get_model_params.return_value = {}

            ExpertModelFactory.create_for_tool("geo_navigator", model_id="")

            mock_create_llm.assert_called_once_with("config:default")
