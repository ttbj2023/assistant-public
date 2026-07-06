"""invoke_with_fallback 单元测试.

覆盖:
- 主模型成功时不触发 fallback
- 白名单瞬时错误触发 fallback
- 非白名单异常直接抛出
- fallback 模型为空时禁用 fallback
- fallback 调用也失败时异常向上传播
- vision 类型使用 vision fallback 模型
- use_json_mode=False 时不注入 json 配置
- invoke_kwargs 透传给主/fallback 两个模型
- 主/fallback 各自按 model_id 取 json_mode 配置
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from src.inference.llm.model_loader import invoke_with_fallback


class _FakeLLM:
    """测试用假 LLM, 记录 bind/ainvoke 调用."""

    def __init__(self, response: Any | None = None, exc: Exception | None = None) -> None:
        self.response = response
        self.exc = exc
        self.bind_calls: list[dict[str, Any]] = []
        self.ainvoke_calls: list[tuple[Any, dict[str, Any]]] = []

    def bind(self, **kwargs: Any) -> _FakeLLM:
        self.bind_calls.append(kwargs)
        return self

    async def ainvoke(self, prompt: Any, **kwargs: Any) -> Any:
        self.ainvoke_calls.append((prompt, kwargs))
        if self.exc:
            raise self.exc
        return self.response


@pytest.mark.asyncio
def _noop_context():
    """返回一个什么都不做的上下文管理器, 且不会吞异常."""
    from contextlib import nullcontext

    return nullcontext()


class TestInvokeWithFallback:
    """invoke_with_fallback 测试类."""

    @patch("src.inference.usage.usage_source", return_value=_noop_context())
    @patch("src.inference.llm.json_mode_config.get_json_mode_config")
    @patch("src.inference.llm.model_loader.create_llm")
    async def test_primary_success_no_fallback(
        self,
        mock_create_llm: MagicMock,
        mock_json_config: MagicMock,
        mock_usage_source: MagicMock,
    ) -> None:
        """主模型成功时只调用一次, 不触发 fallback."""
        primary = _FakeLLM(response=MagicMock(content='{"x": 1}'))
        mock_create_llm.return_value = primary
        mock_json_config.return_value = {"response_format": {"type": "json_object"}}

        result = await invoke_with_fallback("prompt", "primary:model")

        assert result is primary.response
        assert mock_create_llm.call_count == 1
        assert mock_create_llm.call_args[0][0] == "primary:model"
        assert len(primary.ainvoke_calls) == 1
        assert primary.ainvoke_calls[0][1] == {
            "response_format": {"type": "json_object"},
        }

    @patch("src.inference.llm.retry_predicates.is_retryable_llm_exception")
    @patch("src.inference.usage.usage_source", return_value=_noop_context())
    @patch("src.inference.llm.json_mode_config.get_json_mode_config")
    @patch("src.inference.llm.model_loader.create_llm")
    @patch("src.config.inference_config.get_config")
    async def test_retryable_error_triggers_fallback(
        self,
        mock_get_config: MagicMock,
        mock_create_llm: MagicMock,
        mock_json_config: MagicMock,
        mock_usage_source: MagicMock,
        mock_is_retryable: MagicMock,
    ) -> None:
        """白名单瞬时错误触发 fallback 模型调用."""
        primary = _FakeLLM(exc=RuntimeError("rate limit"))
        fallback = _FakeLLM(response=MagicMock(content='{"x": 2}'))
        mock_create_llm.side_effect = [primary, fallback]
        mock_json_config.return_value = {"response_format": {"type": "json_object"}}
        mock_is_retryable.return_value = True
        mock_get_config.return_value.fallback.text_model = "fallback:text"
        mock_get_config.return_value.fallback.text_model_params = {"temperature": 0.5}

        result = await invoke_with_fallback("prompt", "primary:model")

        assert result is fallback.response
        assert mock_create_llm.call_count == 2
        assert mock_create_llm.call_args_list[0][0][0] == "primary:model"
        assert mock_create_llm.call_args_list[1][0][0] == "fallback:text"
        assert fallback.bind_calls == [{"temperature": 0.5}]
        assert len(fallback.ainvoke_calls) == 1

    @patch("src.inference.llm.retry_predicates.is_retryable_llm_exception")
    @patch("src.inference.usage.usage_source", return_value=_noop_context())
    @patch("src.inference.llm.json_mode_config.get_json_mode_config")
    @patch("src.inference.llm.model_loader.create_llm")
    async def test_non_retryable_error_propagates(
        self,
        mock_create_llm: MagicMock,
        mock_json_config: MagicMock,
        mock_usage_source: MagicMock,
        mock_is_retryable: MagicMock,
    ) -> None:
        """非白名单异常不触发 fallback, 直接抛出."""
        primary = _FakeLLM(exc=ValueError("bad request"))
        mock_create_llm.return_value = primary
        mock_json_config.return_value = {}
        mock_is_retryable.return_value = False

        with pytest.raises(ValueError, match="bad request"):
            await invoke_with_fallback("prompt", "primary:model")

        assert mock_create_llm.call_count == 1

    @patch("src.inference.llm.retry_predicates.is_retryable_llm_exception")
    @patch("src.inference.usage.usage_source", return_value=_noop_context())
    @patch("src.inference.llm.json_mode_config.get_json_mode_config")
    @patch("src.inference.llm.model_loader.create_llm")
    @patch("src.config.inference_config.get_config")
    async def test_empty_fallback_model_disables_fallback(
        self,
        mock_get_config: MagicMock,
        mock_create_llm: MagicMock,
        mock_json_config: MagicMock,
        mock_usage_source: MagicMock,
        mock_is_retryable: MagicMock,
    ) -> None:
        """fallback 模型为空字符串时不切换, 直接抛主异常."""
        primary = _FakeLLM(exc=RuntimeError("rate limit"))
        mock_create_llm.return_value = primary
        mock_json_config.return_value = {}
        mock_is_retryable.return_value = True
        mock_get_config.return_value.fallback.text_model = ""
        mock_get_config.return_value.fallback.text_model_params = {}

        with pytest.raises(RuntimeError, match="rate limit"):
            await invoke_with_fallback("prompt", "primary:model")

        assert mock_create_llm.call_count == 1

    @patch("src.inference.llm.retry_predicates.is_retryable_llm_exception")
    @patch("src.inference.usage.usage_source", return_value=_noop_context())
    @patch("src.inference.llm.json_mode_config.get_json_mode_config")
    @patch("src.inference.llm.model_loader.create_llm")
    @patch("src.config.inference_config.get_config")
    async def test_fallback_failure_propagates(
        self,
        mock_get_config: MagicMock,
        mock_create_llm: MagicMock,
        mock_json_config: MagicMock,
        mock_usage_source: MagicMock,
        mock_is_retryable: MagicMock,
    ) -> None:
        """fallback 模型也失败时, 异常向上传播."""
        primary = _FakeLLM(exc=RuntimeError("primary failed"))
        fallback = _FakeLLM(exc=RuntimeError("fallback failed"))
        mock_create_llm.side_effect = [primary, fallback]
        mock_json_config.return_value = {}
        mock_is_retryable.return_value = True
        mock_get_config.return_value.fallback.text_model = "fallback:text"
        mock_get_config.return_value.fallback.text_model_params = {}

        with pytest.raises(RuntimeError, match="fallback failed"):
            await invoke_with_fallback("prompt", "primary:model")

    @patch("src.inference.llm.retry_predicates.is_retryable_llm_exception")
    @patch("src.inference.usage.usage_source", return_value=_noop_context())
    @patch("src.inference.llm.json_mode_config.get_json_mode_config")
    @patch("src.inference.llm.model_loader.create_llm")
    @patch("src.config.inference_config.get_config")
    async def test_vision_kind_uses_vision_model(
        self,
        mock_get_config: MagicMock,
        mock_create_llm: MagicMock,
        mock_json_config: MagicMock,
        mock_usage_source: MagicMock,
        mock_is_retryable: MagicMock,
    ) -> None:
        """fallback_kind='vision' 时使用 vision fallback 模型."""
        primary = _FakeLLM(exc=RuntimeError("primary failed"))
        fallback = _FakeLLM(response=MagicMock(content="vision result"))
        mock_create_llm.side_effect = [primary, fallback]
        mock_json_config.return_value = {}
        mock_is_retryable.return_value = True
        mock_get_config.return_value.fallback.vision_model = "fallback:vision"
        mock_get_config.return_value.fallback.vision_model_params = {}

        result = await invoke_with_fallback(
            "prompt",
            "primary:model",
            fallback_kind="vision",
        )

        assert result is fallback.response
        assert mock_create_llm.call_args_list[1][0][0] == "fallback:vision"

    @patch("src.inference.usage.usage_source", return_value=_noop_context())
    @patch("src.inference.llm.model_loader.create_llm")
    async def test_json_mode_disabled(
        self,
        mock_create_llm: MagicMock,
        mock_usage_source: MagicMock,
    ) -> None:
        """use_json_mode=False 时不注入 json_mode 配置."""
        primary = _FakeLLM(response=MagicMock(content="plain text"))
        mock_create_llm.return_value = primary

        await invoke_with_fallback(
            "prompt",
            "primary:model",
            use_json_mode=False,
        )

        assert primary.ainvoke_calls[0][1] == {}

    @patch("src.inference.usage.usage_source", return_value=_noop_context())
    @patch("src.inference.llm.json_mode_config.get_json_mode_config")
    @patch("src.inference.llm.model_loader.create_llm")
    async def test_invoke_kwargs_forwarded(
        self,
        mock_create_llm: MagicMock,
        mock_json_config: MagicMock,
        mock_usage_source: MagicMock,
    ) -> None:
        """额外 ainvoke 参数(如 max_tokens)透传给主模型."""
        primary = _FakeLLM(response=MagicMock(content="ok"))
        mock_create_llm.return_value = primary
        mock_json_config.return_value = {"response_format": {"type": "json_object"}}

        await invoke_with_fallback(
            "prompt",
            "primary:model",
            max_tokens=8192,
        )

        assert primary.ainvoke_calls[0][1] == {
            "response_format": {"type": "json_object"},
            "max_tokens": 8192,
        }

    @patch("src.inference.llm.retry_predicates.is_retryable_llm_exception")
    @patch("src.inference.usage.usage_source", return_value=_noop_context())
    @patch("src.inference.llm.json_mode_config.get_json_mode_config")
    @patch("src.inference.llm.model_loader.create_llm")
    @patch("src.config.inference_config.get_config")
    async def test_json_mode_per_model(
        self,
        mock_get_config: MagicMock,
        mock_create_llm: MagicMock,
        mock_json_config: MagicMock,
        mock_usage_source: MagicMock,
        mock_is_retryable: MagicMock,
    ) -> None:
        """主/fallback 模型各自独立取 json_mode 配置."""
        primary = _FakeLLM(exc=RuntimeError("primary failed"))
        fallback = _FakeLLM(response=MagicMock(content="fallback ok"))
        mock_create_llm.side_effect = [primary, fallback]
        mock_json_config.side_effect = [
            {"response_format": {"type": "json_object"}},
            {"format": "json"},
        ]
        mock_is_retryable.return_value = True
        mock_get_config.return_value.fallback.text_model = "fallback:text"
        mock_get_config.return_value.fallback.text_model_params = {}

        await invoke_with_fallback("prompt", "primary:model")

        assert mock_json_config.call_args_list[0][0][0] == "primary:model"
        assert mock_json_config.call_args_list[1][0][0] == "fallback:text"
        assert primary.ainvoke_calls[0][1] == {"response_format": {"type": "json_object"}}
        assert fallback.ainvoke_calls[0][1] == {"format": "json"}
