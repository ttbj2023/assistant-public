"""ExpertModelFactory单元测试 - 验证专家工具模型工厂.

测试范围:
1. 基本创建 - 从ClientManager获取缓存实例
2. bind参数覆盖 - 通过bind()覆盖默认参数
3. 错误处理 - 不支持的provider

Mock策略: Mock create_llm, 避免真实API调用.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.tools.experts.model_factory import ExpertModelFactory


class TestExpertModelFactoryCreate:
    """测试ExpertModelFactory.create基本行为"""

    @patch(
        "src.tools.experts.model_factory.create_llm"
    )
    def test_should_return_cached_instance_without_kwargs(self, mock_get):
        mock_llm = MagicMock()
        mock_get.return_value = mock_llm

        result = ExpertModelFactory.create("deepseek:deepseek-v4-flash")

        mock_get.assert_called_once_with("deepseek:deepseek-v4-flash")
        assert result is mock_llm

    @patch(
        "src.tools.experts.model_factory.create_llm"
    )
    def test_should_return_bound_instance_with_kwargs(self, mock_get):
        mock_llm = MagicMock()
        mock_bound = MagicMock()
        mock_llm.bind = MagicMock(return_value=mock_bound)
        mock_get.return_value = mock_llm

        result = ExpertModelFactory.create(
            "deepseek:deepseek-v4-flash", max_tokens=16384
        )

        mock_get.assert_called_once_with("deepseek:deepseek-v4-flash")
        mock_llm.bind.assert_called_once_with(max_tokens=16384)
        assert result is mock_bound

    def test_should_raise_for_invalid_model_id_format(self):
        with pytest.raises(ValueError, match="模型不存在"):
            ExpertModelFactory.create("no_colon_here")

    @patch(
        "src.tools.experts.model_factory.create_llm"
    )
    def test_should_raise_for_model_not_found(self, mock_get):
        mock_get.side_effect = ValueError("模型不存在: unknown:model")
        with pytest.raises(ValueError, match="模型不存在"):
            ExpertModelFactory.create("unknown:model")


class TestExpertModelFactoryBindParams:
    """测试ExpertModelFactory.create的bind()参数传递"""

    @patch(
        "src.tools.experts.model_factory.create_llm"
    )
    def test_should_pass_single_param(self, mock_get):
        mock_llm = MagicMock()
        mock_bound = MagicMock()
        mock_llm.bind = MagicMock(return_value=mock_bound)
        mock_get.return_value = mock_llm

        ExpertModelFactory.create("local:qwen3.5:9b", num_predict=16384)

        mock_llm.bind.assert_called_once_with(num_predict=16384)

    @patch(
        "src.tools.experts.model_factory.create_llm"
    )
    def test_should_pass_multiple_params(self, mock_get):
        mock_llm = MagicMock()
        mock_bound = MagicMock()
        mock_llm.bind = MagicMock(return_value=mock_bound)
        mock_get.return_value = mock_llm

        ExpertModelFactory.create(
            "deepseek:deepseek-chat",
            temperature=0.5,
            max_tokens=8192,
        )

        mock_llm.bind.assert_called_once_with(
            temperature=0.5,
            max_tokens=8192,
        )

    @patch(
        "src.tools.experts.model_factory.create_llm"
    )
    def test_empty_kwargs_should_not_call_bind(self, mock_get):
        mock_llm = MagicMock()
        mock_get.return_value = mock_llm

        result = ExpertModelFactory.create("openai:gpt-4o")

        mock_llm.bind.assert_not_called()
        assert result is mock_llm
