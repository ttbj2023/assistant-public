"""ModelLoader 单元测试.

测试统一对外入口 create_llm / get_llm_factory.
"""

from __future__ import annotations

from unittest.mock import Mock, patch

import pytest

from src.inference.llm.model_loader import (
    create_llm,
)


class TestCreateLlm:
    """测试 create_llm 入口."""

    def test_create_llm_should_delegate_to_factory(self):
        """create_llm 应委托给 LlmFactory.get_llm."""
        mock_llm = Mock()
        mock_factory = Mock()
        mock_factory.get_llm.return_value = mock_llm

        with patch("src.inference.llm.model_loader._factory", return_value=mock_factory):
            result = create_llm("openai:gpt-4")

            assert result is mock_llm
            mock_factory.get_llm.assert_called_once_with(
                "openai:gpt-4",
                agent_config={"streaming": False},
            )

    def test_create_llm_should_pass_construction_params(self):
        """create_llm 应将额外构造级参数透传给工厂."""
        mock_llm = Mock()
        mock_factory = Mock()
        mock_factory.get_llm.return_value = mock_llm

        with patch("src.inference.llm.model_loader._factory", return_value=mock_factory):
            result = create_llm("local:qwen3.5:9b", streaming=True, num_ctx=131072)

            assert result is mock_llm
            mock_factory.get_llm.assert_called_once_with(
                "local:qwen3.5:9b",
                agent_config={"streaming": True, "num_ctx": 131072},
            )

    def test_create_llm_streaming_should_pass_agent_config(self):
        """streaming=True 应转换为 agent_config."""
        mock_llm = Mock()
        mock_factory = Mock()
        mock_factory.get_llm.return_value = mock_llm

        with patch("src.inference.llm.model_loader._factory", return_value=mock_factory):
            result = create_llm("openai:gpt-4", streaming=True)

            assert result is mock_llm
            mock_factory.get_llm.assert_called_once_with(
                "openai:gpt-4",
                agent_config={"streaming": True},
            )

    def test_create_llm_should_propagate_factory_error(self):
        """工厂异常应向上传播."""
        mock_factory = Mock()
        mock_factory.get_llm.side_effect = ValueError("模型不存在")

        with (
            patch("src.inference.llm.model_loader._factory", return_value=mock_factory),
            pytest.raises(ValueError, match="模型不存在"),
        ):
            create_llm("invalid:model")
