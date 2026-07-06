"""LlmFactory 单元测试.

测试职责: 验证 LLM 实例工厂的核心功能逻辑
测试范围: 实例创建/缓存复用/provider 路由/参数注入
Mock 策略: Mock LLM 构造函数 / 缓存系统 / 元数据查询, 保留工厂业务逻辑
"""

from __future__ import annotations

from unittest.mock import Mock, patch

import pytest

from src.inference.llm.llm_factory import LlmFactory


def _make_chat_metadata(provider: str = "openai", model_id: str = "openai:gpt-4"):
    """构造对话模型 metadata mock."""
    m = Mock()
    m.provider = provider
    m.id = model_id
    m.is_chat_model.return_value = True
    m.is_embedding_model.return_value = False
    m.get_param_defaults.return_value = {"temperature": 0.7, "max_tokens": 4096}
    m.get_allowed_param_names.return_value = {"temperature", "max_tokens"}
    return m


def _make_emb_metadata(provider: str = "local", model_id: str = "local:bge-m3"):
    """构造嵌入模型 metadata mock."""
    m = Mock()
    m.provider = provider
    m.id = model_id
    m.is_chat_model.return_value = False
    m.is_embedding_model.return_value = True
    return m


class TestLlmFactoryLlm:
    """测试 LlmFactory.get_llm."""

    @pytest.fixture
    def mock_cache(self):
        cache = Mock()
        cache.get_llm_client.return_value = None
        cache.cache_llm_client.return_value = None
        return cache

    def test_cache_hit_should_return_cached_client(self, mock_cache):
        """缓存命中应直接返回缓存的客户端."""
        mock_cached = Mock()
        mock_cache.get_llm_client.return_value = mock_cached

        with patch(
            "src.inference.llm.llm_factory.get_client_cache", return_value=mock_cache
        ):
            factory = LlmFactory()
            factory._cache = mock_cache  # type: ignore[assignment]
            result = factory.get_llm("openai:gpt-4")

            assert result is mock_cached
            mock_cache.cache_llm_client.assert_not_called()

    def test_cache_miss_should_create_and_cache_client(self, mock_cache):
        """缓存未命中应创建新实例并写入缓存."""
        mock_llm = Mock()
        metadata = _make_chat_metadata()

        with (
            patch(
                "src.inference.llm.llm_factory.get_client_cache",
                return_value=mock_cache,
            ),
            patch("src.inference.llm.llm_factory.get_model", return_value=metadata),
            patch("src.inference.llm.llm_factory.ChatOpenAI", return_value=mock_llm),
        ):
            factory = LlmFactory()
            factory._cache = mock_cache  # type: ignore[assignment]
            result = factory.get_llm("openai:gpt-4")

            assert result is mock_llm
            mock_cache.cache_llm_client.assert_called_once()
            call_args = mock_cache.cache_llm_client.call_args
            assert call_args[0][0] == "openai:gpt-4"
            assert call_args[0][1] is mock_llm

    def test_unknown_model_should_raise_value_error(self, mock_cache):
        """未知模型应抛出 ValueError."""
        with (
            patch(
                "src.inference.llm.llm_factory.get_client_cache",
                return_value=mock_cache,
            ),
            patch("src.inference.llm.llm_factory.get_model", return_value=None),
        ):
            factory = LlmFactory()
            factory._cache = mock_cache  # type: ignore[assignment]

            with pytest.raises(ValueError, match="模型不存在"):
                factory.get_llm("unknown:model")

    def test_non_chat_model_should_raise_value_error(self, mock_cache):
        """非对话模型应抛出 ValueError."""
        metadata = _make_emb_metadata()

        with (
            patch(
                "src.inference.llm.llm_factory.get_client_cache",
                return_value=mock_cache,
            ),
            patch("src.inference.llm.llm_factory.get_model", return_value=metadata),
        ):
            factory = LlmFactory()
            factory._cache = mock_cache  # type: ignore[assignment]

            with pytest.raises(ValueError, match="不是对话模型"):
                factory.get_llm("local:bge-m3")

    def test_agent_config_should_be_passed_to_cache(self, mock_cache):
        """agent_config 应传递给缓存查找."""
        mock_cache.get_llm_client.return_value = Mock()
        agent_config = {"streaming": True}

        with patch(
            "src.inference.llm.llm_factory.get_client_cache", return_value=mock_cache
        ):
            factory = LlmFactory()
            factory._cache = mock_cache  # type: ignore[assignment]
            factory.get_llm("openai:gpt-4", agent_config=agent_config)

            call_args = mock_cache.get_llm_client.call_args
            assert call_args[0][0] == "openai:gpt-4"
            assert call_args[0][1] is agent_config

    def test_agent_config_should_override_allowed_params(self, mock_cache):
        """agent_config 中模型白名单内的参数应覆盖默认构造参数."""
        metadata = _make_chat_metadata("openai", "openai:gpt-4")
        metadata.get_allowed_param_names.return_value = {"temperature", "max_tokens"}

        with (
            patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"}),
            patch(
                "src.inference.llm.llm_factory.get_client_cache",
                return_value=mock_cache,
            ),
            patch("src.inference.llm.llm_factory.get_model", return_value=metadata),
            patch("src.inference.llm.llm_factory.ChatOpenAI") as mock_cls,
        ):
            mock_cls.return_value = Mock()
            factory = LlmFactory()
            factory._cache = mock_cache  # type: ignore[assignment]
            factory.get_llm(
                "openai:gpt-4",
                agent_config={"streaming": True, "temperature": 0.1, "max_tokens": 512},
            )

            call_kwargs = mock_cls.call_args.kwargs
            assert call_kwargs["temperature"] == 0.1
            assert call_kwargs["max_tokens"] == 512

    def test_agent_config_should_ignore_non_allowed_params(self, mock_cache):
        """agent_config 中不在模型白名单内的参数应被忽略, 避免传给不支持的 SDK."""
        metadata = _make_chat_metadata("openai", "openai:gpt-4")
        metadata.get_allowed_param_names.return_value = {"temperature", "max_tokens"}

        with (
            patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"}),
            patch(
                "src.inference.llm.llm_factory.get_client_cache",
                return_value=mock_cache,
            ),
            patch("src.inference.llm.llm_factory.get_model", return_value=metadata),
            patch("src.inference.llm.llm_factory.ChatOpenAI") as mock_cls,
        ):
            mock_cls.return_value = Mock()
            factory = LlmFactory()
            factory._cache = mock_cache  # type: ignore[assignment]
            factory.get_llm(
                "openai:gpt-4",
                agent_config={"temperature": 0.1, "num_ctx": 131072},
            )

            call_kwargs = mock_cls.call_args.kwargs
            assert call_kwargs["temperature"] == 0.1
            assert "num_ctx" not in call_kwargs

    def test_openai_compatible_provider_should_move_top_k_to_extra_body(
        self,
        mock_cache,
    ):
        """Qwen 等兼容端点的 top_k / repetition_penalty 应被移入 extra_body."""
        metadata = _make_chat_metadata(
            "aliyun-token-plan", "aliyun-token-plan:qwen3.7-max"
        )
        metadata.get_param_defaults.return_value = {
            "temperature": 0.6,
            "top_p": 0.95,
            "top_k": 20,
            "repetition_penalty": 1.05,
            "max_tokens": 32768,
            "extra_body": {"enable_thinking": True, "thinking_budget": None},
        }
        metadata.get_allowed_param_names.return_value = {
            "temperature",
            "top_p",
            "top_k",
            "repetition_penalty",
            "max_tokens",
            "extra_body",
        }

        with (
            patch.dict("os.environ", {"ALIYUN_TOKEN_PLAN_API_KEY": "test-key"}),
            patch(
                "src.inference.llm.llm_factory.get_client_cache",
                return_value=mock_cache,
            ),
            patch("src.inference.llm.llm_factory.get_model", return_value=metadata),
            patch(
                "src.inference.llm.llm_factory.get_provider_config"
            ) as mock_provider_cfg,
            patch("src.inference.llm.llm_factory.ChatOpenAI") as mock_cls,
        ):
            mock_provider_cfg.return_value = Mock(
                requires_auth=True,
                api_key_env="ALIYUN_TOKEN_PLAN_API_KEY",
                get_effective_base_url=lambda: "https://token-plan.example.com/v1",
            )
            mock_cls.return_value = Mock()
            factory = LlmFactory()
            factory._cache = mock_cache  # type: ignore[assignment]
            factory.get_llm("aliyun-token-plan:qwen3.7-max")

            call_kwargs = mock_cls.call_args.kwargs
            assert "top_k" not in call_kwargs
            assert "repetition_penalty" not in call_kwargs
            assert call_kwargs["extra_body"]["top_k"] == 20
            assert call_kwargs["extra_body"]["repetition_penalty"] == 1.05
            assert call_kwargs["extra_body"]["enable_thinking"] is True


class TestLlmFactoryProviderRouting:
    """测试 _build_llm 的 provider 路由."""

    @pytest.fixture
    def mock_cache(self):
        cache = Mock()
        cache.get_llm_client.return_value = None
        cache.cache_llm_client.return_value = None
        return cache

    def test_openai_provider_should_use_chat_openai(self, mock_cache):
        """openai provider 应使用 ChatOpenAI."""
        metadata = _make_chat_metadata("openai", "openai:gpt-4")

        with (
            patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"}),
            patch(
                "src.inference.llm.llm_factory.get_client_cache",
                return_value=mock_cache,
            ),
            patch("src.inference.llm.llm_factory.get_model", return_value=metadata),
            patch("src.inference.llm.llm_factory.ChatOpenAI") as mock_cls,
        ):
            mock_cls.return_value = Mock()
            factory = LlmFactory()
            factory._cache = mock_cache  # type: ignore[assignment]
            factory.get_llm("openai:gpt-4")
            mock_cls.assert_called_once()

    def test_deepseek_provider_should_use_chat_deepseek(self, mock_cache):
        """deepseek provider 应使用 ChatDeepSeek."""
        metadata = _make_chat_metadata("deepseek", "deepseek:deepseek-v4-flash")

        with (
            patch.dict("os.environ", {"DEEPSEEK_API_KEY": "test-key"}),
            patch(
                "src.inference.llm.llm_factory.get_client_cache",
                return_value=mock_cache,
            ),
            patch("src.inference.llm.llm_factory.get_model", return_value=metadata),
            patch("src.inference.llm.llm_factory.ChatDeepSeek") as mock_cls,
        ):
            mock_cls.return_value = Mock()
            factory = LlmFactory()
            factory._cache = mock_cache  # type: ignore[assignment]
            factory.get_llm("deepseek:deepseek-v4-flash")
            mock_cls.assert_called_once()

    def test_minimax_provider_should_use_chat_anthropic(self, mock_cache):
        """minimax provider 应使用 ChatAnthropic."""
        metadata = _make_chat_metadata("minimax", "minimax:MiniMax-M2.7")

        with (
            patch.dict("os.environ", {"MINIMAX_API_KEY": "test-key"}),
            patch(
                "src.inference.llm.llm_factory.get_client_cache",
                return_value=mock_cache,
            ),
            patch("src.inference.llm.llm_factory.get_model", return_value=metadata),
            patch("src.inference.llm.llm_factory.ChatAnthropic") as mock_cls,
        ):
            mock_cls.return_value = Mock()
            factory = LlmFactory()
            factory._cache = mock_cache  # type: ignore[assignment]
            factory.get_llm("minimax:MiniMax-M2.7")
            mock_cls.assert_called_once()

    def test_scnet_provider_should_use_chat_openai(self, mock_cache):
        """scnet provider 应使用 ChatOpenAI 并指向 scnet.cn 聚合端点."""
        metadata = _make_chat_metadata("scnet", "scnet:GLM-5.2")

        with (
            patch.dict("os.environ", {"SCNET_API_KEY": "test-key"}),
            patch(
                "src.inference.llm.llm_factory.get_client_cache",
                return_value=mock_cache,
            ),
            patch("src.inference.llm.llm_factory.get_model", return_value=metadata),
            patch("src.inference.llm.llm_factory.ChatOpenAI") as mock_cls,
        ):
            mock_cls.return_value = Mock()
            factory = LlmFactory()
            factory._cache = mock_cache  # type: ignore[assignment]
            factory.get_llm("scnet:GLM-5.2")
            mock_cls.assert_called_once()
            call_kwargs = mock_cls.call_args.kwargs
            assert call_kwargs["base_url"] == "https://api.scnet.cn/api/llm/v1"
            assert call_kwargs["model"] == "GLM-5.2"

    def test_gemini_provider_should_use_chat_google_generative_ai(self, mock_cache):
        """gemini provider 应使用 ChatGoogleGenerativeAI."""
        metadata = _make_chat_metadata("gemini", "gemini:test-model")

        with (
            patch.dict("os.environ", {"GEMINI_API_KEY": "test-key"}),
            patch(
                "src.inference.llm.llm_factory.get_client_cache",
                return_value=mock_cache,
            ),
            patch("src.inference.llm.llm_factory.get_model", return_value=metadata),
            patch("langchain_google_genai.ChatGoogleGenerativeAI") as mock_cls,
        ):
            mock_cls.return_value = Mock()
            factory = LlmFactory()
            factory._cache = mock_cache  # type: ignore[assignment]
            factory.get_llm("gemini:test-model")
            mock_cls.assert_called_once()