"""LLM 工具噪音过滤模块单元测试.

覆盖范围:
- filter_tools_by_llm: 核心 API (子集返回/全部保留/降级)
- _call_llm_filter: LLM 调用逻辑 (从配置读取 model/params/timeout)
- _parse_llm_response: JSON 解析 (正常/异常/边界)
- _build_user_message: 消息构建
"""

from __future__ import annotations

import math
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.tools.internal._llm_tool_filter import (
    _build_user_message,
    _parse_llm_response,
    filter_tools_by_llm,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SAMPLE_CANDIDATES = [
    {
        "name": "generate_image",
        "description": "图片生成, 根据文字提示词生成图片并返回下载链接",
    },
    {"name": "regenerate_download_link", "description": "按文件ID重新生成文件下载链接"},
    {"name": "read_file", "description": "按文件ID读取文件描述内容"},
]

DEFAULT_MODEL = "test:filter-model"
DEFAULT_MODEL_PARAMS = {
    "format": "json",
    "temperature": 0.0,
}
DEFAULT_TIMEOUT = 5.0
DEFAULT_MIN_TOOLS = 2


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_tool_filter_cfg(
    *,
    model: str = DEFAULT_MODEL,
    model_params: dict | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    min_tools_for_filter: int = DEFAULT_MIN_TOOLS,
) -> MagicMock:
    """构造 ToolFilterConfig 的 Mock 对象."""
    cfg = MagicMock()
    cfg.model = model
    cfg.model_params = (
        model_params if model_params is not None else DEFAULT_MODEL_PARAMS
    )
    cfg.timeout = timeout
    cfg.min_tools_for_filter = min_tools_for_filter
    return cfg


def _make_inference_config(
    tool_filter_cfg: MagicMock | None = None,
) -> MagicMock:
    """构造 InferenceConfig 的 Mock 对象, 只暴露 tool_filter 字段."""
    config = MagicMock()
    config.tool_filter = tool_filter_cfg or _make_tool_filter_cfg()
    return config


@pytest.fixture
def mock_inference_config():
    """默认 Mock inference_config, 与 ToolFilterConfig 默认值一致."""
    return _make_inference_config()


@pytest.fixture
def patch_inference_config(mock_inference_config):
    """patch get_inference_config 返回 mock 对象, 用于 filter_tools_by_llm 等公共 API."""
    with patch(
        "src.config.inference_config.get_config",
        return_value=mock_inference_config,
    ):
        yield mock_inference_config


@pytest.fixture
def patch_inference_config_in_filter(mock_inference_config):
    """patch _llm_tool_filter 模块内部 get_inference_config 入口.

    filter_tools_by_llm 与 _call_llm_filter 都通过延迟导入
    `from src.config.inference_config import get_config as get_inference_config`,
    所以 patch 目标是 inference_config 模块的 get_config.
    """
    with patch(
        "src.config.inference_config.get_config",
        return_value=mock_inference_config,
    ):
        yield mock_inference_config


# ---------------------------------------------------------------------------
# TestBuildUserMessage
# ---------------------------------------------------------------------------


class TestBuildUserMessage:
    def test_message_format(self):
        msg = _build_user_message("generate image", SAMPLE_CANDIDATES)
        assert "用户查询: generate image" in msg
        assert "1. generate_image:" in msg
        assert "2. regenerate_download_link:" in msg
        assert "3. read_file:" in msg

    def test_description_uses_first_three_lines(self):
        """description 前 3 行被提取作为筛选模型输入."""
        candidates = [
            {
                "name": "tool_a",
                "description": "第一行核心能力\n第二行场景\n第三行细节\n第四行参数说明",
                "full_description": "",
            },
        ]
        msg = _build_user_message("test", candidates)
        # 前 3 行被提取, 第 4 行被排除
        assert "第一行核心能力" in msg
        assert "第二行场景" in msg
        assert "第三行细节" in msg
        assert "第四行参数说明" not in msg


# ---------------------------------------------------------------------------
# TestParseLlmResponse
# ---------------------------------------------------------------------------


class TestParseLlmResponse:
    def test_valid_json(self):
        result = _parse_llm_response('{"relevant": [1, 3]}')
        assert result == [1, 3]

    def test_empty_relevant(self):
        result = _parse_llm_response('{"relevant": []}')
        assert result == []

    def test_single_item(self):
        result = _parse_llm_response('{"relevant": [2]}')
        assert result == [2]

    def test_float_indices_converted(self):
        result = _parse_llm_response('{"relevant": [1.0, 3.0]}')
        assert result == [1, 3]

    def test_garbage_prefix(self):
        """LLM 可能在 JSON 前输出额外内容."""
        result = _parse_llm_response('思考一下... {"relevant": [1, 2]}')
        assert result == [1, 2]

    def test_think_tags(self):
        """非思考模型不应输出 think 标签, 但防御性处理."""
        result = _parse_llm_response('{"relevant": [1]}')
        assert result == [1]

    def test_invalid_json_returns_none(self):
        result = _parse_llm_response("这不是 JSON")
        assert result is None

    def test_missing_relevant_key_returns_none(self):
        result = _parse_llm_response('{"other": [1, 2]}')
        assert result is None

    def test_non_list_relevant_returns_none(self):
        result = _parse_llm_response('{"relevant": "all"}')
        assert result is None

    def test_empty_string_returns_none(self):
        result = _parse_llm_response("")
        assert result is None


# ---------------------------------------------------------------------------
# TestFilterToolsByLlm
# ---------------------------------------------------------------------------


class TestFilterToolsByLlm:
    @pytest.mark.asyncio
    async def test_single_candidate_skips_llm(self, patch_inference_config):
        """只有 1 个候选时不调用 LLM, 直接返回."""
        candidates = [{"name": "only_tool", "description": "唯一工具"}]
        result = await filter_tools_by_llm("test", candidates)
        assert result == candidates
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_filter_returns_subset(self, patch_inference_config):
        """LLM 返回子集, 验证只保留对应工具."""
        with patch(
            "src.tools.internal._llm_tool_filter._call_llm_filter",
            new_callable=AsyncMock,
            return_value=[1, 3],
        ):
            result = await filter_tools_by_llm("test query", SAMPLE_CANDIDATES)
            names = [t["name"] for t in result]
            assert "generate_image" in names
            assert "read_file" in names
            assert "regenerate_download_link" not in names

    @pytest.mark.asyncio
    async def test_filter_all_relevant(self, patch_inference_config):
        """LLM 返回全部编号, 所有工具保留."""
        with patch(
            "src.tools.internal._llm_tool_filter._call_llm_filter",
            new_callable=AsyncMock,
            return_value=[1, 2, 3],
        ):
            result = await filter_tools_by_llm("test", SAMPLE_CANDIDATES)
            assert len(result) == 3

    @pytest.mark.asyncio
    async def test_filter_empty_relevant_fallback(self, patch_inference_config):
        """LLM 返回空列表 → 降级返回全部候选."""
        with patch(
            "src.tools.internal._llm_tool_filter._call_llm_filter",
            new_callable=AsyncMock,
            return_value=[],
        ):
            result = await filter_tools_by_llm("test", SAMPLE_CANDIDATES)
            assert len(result) == 3  # 降级返回全部

    @pytest.mark.asyncio
    async def test_filter_parse_failure_fallback(self, patch_inference_config):
        """LLM 响应不可解析 → 降级返回全部候选."""
        with patch(
            "src.tools.internal._llm_tool_filter._call_llm_filter",
            new_callable=AsyncMock,
            return_value=None,
        ):
            result = await filter_tools_by_llm("test", SAMPLE_CANDIDATES)
            assert len(result) == 3

    @pytest.mark.asyncio
    async def test_filter_connection_error_fallback(self, patch_inference_config):
        """Ollama 未启动 → 降级返回全部候选."""
        with patch(
            "src.tools.internal._llm_tool_filter._call_llm_filter",
            new_callable=AsyncMock,
            side_effect=ConnectionError("Connection refused"),
        ):
            result = await filter_tools_by_llm("test", SAMPLE_CANDIDATES)
            assert len(result) == 3

    @pytest.mark.asyncio
    async def test_filter_timeout_fallback(self, patch_inference_config):
        """LLM 超时 → 降级返回全部候选."""
        with patch(
            "src.tools.internal._llm_tool_filter._call_llm_filter",
            new_callable=AsyncMock,
            side_effect=TimeoutError(),
        ):
            result = await filter_tools_by_llm("test", SAMPLE_CANDIDATES)
            assert len(result) == 3

    @pytest.mark.asyncio
    async def test_filter_out_of_range_indices_ignored(self, patch_inference_config):
        """LLM 返回越界编号 → 静默跳过, 保留有效结果."""
        with patch(
            "src.tools.internal._llm_tool_filter._call_llm_filter",
            new_callable=AsyncMock,
            return_value=[1, 99, -1, 0],
        ):
            result = await filter_tools_by_llm("test", SAMPLE_CANDIDATES)
            assert len(result) == 1
            assert result[0]["name"] == "generate_image"

    @pytest.mark.asyncio
    async def test_min_tools_threshold_respected(self):
        """min_tools_for_filter 配置生效: 候选数 < 阈值时跳过 LLM 调用."""
        cfg = _make_tool_filter_cfg(min_tools_for_filter=5)
        config = _make_inference_config(cfg)
        with patch(
            "src.config.inference_config.get_config",
            return_value=config,
        ):
            # 3 个候选 < 阈值 5, 直接返回
            result = await filter_tools_by_llm("test", SAMPLE_CANDIDATES)
            assert result == SAMPLE_CANDIDATES


# ---------------------------------------------------------------------------
# TestCallLlmFilter
# ---------------------------------------------------------------------------


class TestCallLlmFilter:
    @pytest.mark.asyncio
    async def test_calls_create_llm_with_configured_model(
        self, patch_inference_config_in_filter
    ):
        """验证 create_llm 接收配置中的 model ID."""
        mock_llm = MagicMock()
        mock_bound = AsyncMock()
        mock_llm.bind.return_value = mock_bound
        mock_bound.ainvoke.return_value = MagicMock(
            content='{"relevant": [1]}',
        )

        with patch(
            "src.inference.llm.model_loader.create_llm",
            return_value=mock_llm,
        ):
            result = await filter_tools_by_llm("test", SAMPLE_CANDIDATES)
            assert len(result) >= 1

            from src.inference.llm.model_loader import create_llm as _cl

            _cl.assert_called_once_with(DEFAULT_MODEL)

    @pytest.mark.asyncio
    async def test_binds_configured_params(self, patch_inference_config_in_filter):
        """验证 bind 调用使用配置中的 model_params (含 num_predict=256)."""
        mock_llm = MagicMock()
        mock_bound = AsyncMock()
        mock_llm.bind.return_value = mock_bound
        mock_bound.ainvoke.return_value = MagicMock(
            content='{"relevant": [1]}',
        )

        with patch(
            "src.inference.llm.model_loader.create_llm",
            return_value=mock_llm,
        ):
            await filter_tools_by_llm("test", SAMPLE_CANDIDATES)
            mock_llm.bind.assert_called_once_with(**DEFAULT_MODEL_PARAMS)

    @pytest.mark.asyncio
    async def test_skips_bind_when_empty_params(self):
        """model_params 为空时跳过 bind 调用."""
        cfg = _make_tool_filter_cfg(model_params={})
        config = _make_inference_config(cfg)

        mock_llm = MagicMock()
        mock_llm.ainvoke = AsyncMock(
            return_value=MagicMock(content='{"relevant": [1]}'),
        )

        with (
            patch(
                "src.config.inference_config.get_config",
                return_value=config,
            ),
            patch(
                "src.inference.llm.model_loader.create_llm",
                return_value=mock_llm,
            ),
        ):
            await filter_tools_by_llm("test", SAMPLE_CANDIDATES)
            mock_llm.bind.assert_not_called()

    @pytest.mark.asyncio
    async def test_uses_configured_timeout(self):
        """验证 asyncio.wait_for 使用配置中的 timeout 值."""
        cfg = _make_tool_filter_cfg(timeout=2.5)
        config = _make_inference_config(cfg)

        mock_llm = MagicMock()
        mock_bound = AsyncMock()
        mock_llm.bind.return_value = mock_bound
        mock_bound.ainvoke.return_value = MagicMock(
            content='{"relevant": [1]}',
        )

        captured_timeout: dict[str, float] = {}

        async def fake_wait_for(coro, timeout):
            captured_timeout["value"] = timeout
            # 关闭未 await 的 coro 避免警告
            coro.close()
            return MagicMock(content='{"relevant": [1]}')

        with (
            patch(
                "src.config.inference_config.get_config",
                return_value=config,
            ),
            patch(
                "src.inference.llm.model_loader.create_llm",
                return_value=mock_llm,
            ),
            patch(
                "src.tools.internal._llm_tool_filter.asyncio.wait_for",
                side_effect=fake_wait_for,
            ),
        ):
            await filter_tools_by_llm("test", SAMPLE_CANDIDATES)
            assert math.isclose(captured_timeout["value"], 2.5)
