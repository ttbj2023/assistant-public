"""DataPro 共享运行时单元测试 - DataProClient 编排 + 响应预处理纯函数.

测试范围:
1. preprocess_response / merge_results / _extract_result_text / _get_datapro_api_key 纯函数
2. DataProClient.is_available 环境变量检查
3. DataProClient._call_datapro (mock fastmcp.Client)
4. DataProClient._light_process (mock ExpertModelFactory)
5. DataProClient.execute 编排 (缓存命中/未命中/拆分/失败)

Mock策略: mock fastmcp.Client/ExpertModelFactory/get_semantic_cache, 避免真实调用.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.tools.external.datapro._base import (
    DataProClient,
    _extract_result_text,
    merge_results,
    preprocess_response,
)

_BASE = "src.tools.external.datapro._base"


@pytest.fixture
def client() -> DataProClient:
    return DataProClient(cache_collection="test_cache", llm_prompt="prompt")


# =============================================================================
# 纯函数测试
# =============================================================================


class TestExtractResultText:
    def test_string_passthrough(self):
        assert _extract_result_text("raw text") == "raw text"

    def test_content_list_with_text_attr(self):
        result = MagicMock()
        result.content = [MagicMock(text="chunk1"), MagicMock(text="chunk2")]
        assert _extract_result_text(result) == "chunk1\nchunk2"

    def test_content_list_with_dict(self):
        result = MagicMock()
        result.content = [{"type": "text", "text": "hello"}]
        assert _extract_result_text(result) == "hello"

    def test_empty_returns_empty(self):
        assert _extract_result_text(None) == ""


class TestPreprocess:
    """preprocess_response 结构驱动信息密度提取."""

    def test_finance_strips_null_and_unwraps_list(self):
        data = {
            "items": [
                {"证券代码": "002594", "table": {"ROE": [1.65], "空字段": [None]}}
            ]
        }
        result = preprocess_response(json.dumps(data))
        assert "002594" in result
        assert "1.65" in result
        assert "None" not in result

    def test_enterprise_name_from_list_subitem(self):
        data = {
            "items": [
                {"企业风险信息": [{"企业名称": "腾讯", "风险类型描述": "行政处罚"}]}
            ]
        }
        result = preprocess_response(json.dumps(data))
        assert "腾讯" in result
        assert "行政处罚" in result

    def test_json_string_field_parsed_as_list(self):
        change = json.dumps([
            {"change_item": "投资人变更", "change_time": "2020-01-01"}
        ])
        data = {"items": [{"公司名称": "华为", "工商变更记录(JSON字符串)": change}]}
        result = preprocess_response(json.dumps(data))
        assert "工商变更记录" in result
        assert "投资人变更" in result

    def test_non_json_passthrough(self):
        assert preprocess_response("纯文本非JSON") == "纯文本非JSON"

    def test_strips_internal_keys(self):
        data = {
            "items": [{"公司名称": "华为", "公司ID": 123, "归属省份首字母小写": "h"}]
        }
        result = preprocess_response(json.dumps(data))
        assert "华为" in result
        assert "123" not in result
        assert "首字母" not in result

    def test_finance_indicator_dict_full_expansion(self):
        indicators = {f"指标{i}": [float(i)] for i in range(15)}
        data = {"items": [{"证券代码": "002594", "table": indicators}]}
        result = preprocess_response(json.dumps(data))
        for i in range(15):
            assert f"指标{i}" in result


class TestMergeResults:
    def test_single_batch_returns_directly(self):
        merged = merge_results(["data"], "q", multi_batch=False)
        assert merged["result"] == "data"
        assert "error" not in merged

    def test_multi_batch_concatenates(self):
        merged = merge_results(["batch1", "batch2"], "q", multi_batch=True)
        assert "batch1" in merged["result"]
        assert "batch2" in merged["result"]
        assert "分批合并" in merged["result"]

    def test_all_failed_returns_error(self):
        merged = merge_results(["[查询失败: x]"], "q", multi_batch=False)
        assert merged.get("error") == "all_batches_failed"


# =============================================================================
# is_available 测试
# =============================================================================


class TestIsAvailable:
    def test_available_when_key_set(self, client):
        with patch(f"{_BASE}.get_credential", return_value="fake-key"):
            assert client.is_available() is True

    def test_unavailable_when_key_missing(self, client):
        with patch(f"{_BASE}.get_credential", return_value=""):
            assert client.is_available() is False


# =============================================================================
# _call_datapro 测试 (mock fastmcp.Client)
# =============================================================================


class TestCallDatapro:
    @pytest.mark.asyncio
    @patch(f"{_BASE}.get_credential")
    @patch("fastmcp.client.transports.StreamableHttpTransport")
    @patch("fastmcp.client.Client")
    async def test_should_call_tool_and_format(
        self, mock_client_cls, _mock_transport, mock_get_credential, client
    ):
        mock_get_credential.return_value = "fake-key"
        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None
        mock_result = MagicMock(
            content=[MagicMock(text='{"code":0,"msg":"success","items":[]}')]
        )
        mock_client.call_tool.return_value = mock_result
        mock_client_cls.return_value = mock_client

        result = await client._call_datapro("比亚迪 ROE")

        mock_client.call_tool.assert_awaited_once()
        assert "success" in result or "items" in result

    @pytest.mark.asyncio
    async def test_should_return_error_when_key_missing(self, client):
        with patch(f"{_BASE}.get_credential", return_value=""):
            result = await client._call_datapro("query")
            assert "配置缺失" in result


# =============================================================================
# _light_process 测试 (mock ExpertModelFactory)
# =============================================================================


class TestLightProcess:
    @pytest.mark.asyncio
    @patch(f"{_BASE}.ExpertModelFactory")
    async def test_should_return_processed_text(self, mock_factory, client):
        mock_llm = MagicMock()
        mock_llm.ainvoke = AsyncMock(return_value=MagicMock(content="整理后结果"))
        mock_factory.create_for_tool.return_value = mock_llm

        result = await client._light_process("raw data", "比亚迪 ROE")
        assert result == "整理后结果"

    @pytest.mark.asyncio
    @patch(f"{_BASE}.ExpertModelFactory")
    async def test_should_return_none_on_failure(self, mock_factory, client):
        mock_factory.create_for_tool.side_effect = RuntimeError("llm down")

        result = await client._light_process("raw data", "比亚迪 ROE")
        assert result is None


# =============================================================================
# execute 编排测试 (mock _call_datapro + _light_process + cache)
# =============================================================================

_LARGE_TEXT = "DATAPRO_RAW_" + "x" * 5000


class TestExecute:
    @pytest.mark.asyncio
    @patch.object(DataProClient, "_call_datapro", new_callable=AsyncMock)
    @patch.object(DataProClient, "_light_process", new_callable=AsyncMock)
    @patch(f"{_BASE}.get_semantic_cache")
    async def test_large_data_triggers_llm_and_writes_cache(
        self, mock_cache_fn, mock_light, mock_call, client
    ):
        mock_cache = AsyncMock()
        mock_cache.get.return_value = None
        mock_cache_fn.return_value = mock_cache
        mock_call.return_value = _LARGE_TEXT
        mock_light.return_value = "processed result"

        result = await client.execute("finance_data", "华为技术有限公司 工商信息")

        assert result == "processed result"
        mock_light.assert_awaited_once()
        mock_cache.put.assert_awaited_once()

    @pytest.mark.asyncio
    @patch.object(DataProClient, "_call_datapro", new_callable=AsyncMock)
    @patch.object(DataProClient, "_light_process", new_callable=AsyncMock)
    @patch(f"{_BASE}.get_semantic_cache")
    async def test_small_data_skips_llm(
        self, mock_cache_fn, mock_light, mock_call, client
    ):
        mock_cache = AsyncMock()
        mock_cache.get.return_value = None
        mock_cache_fn.return_value = mock_cache
        mock_call.return_value = "small result"

        result = await client.execute("finance_data", "比亚迪 ROE")

        assert "small result" in result
        mock_light.assert_not_awaited()

    @pytest.mark.asyncio
    @patch.object(DataProClient, "_call_datapro", new_callable=AsyncMock)
    @patch.object(DataProClient, "_light_process", new_callable=AsyncMock)
    @patch(f"{_BASE}.get_semantic_cache")
    async def test_cache_hit_skips_call(
        self, mock_cache_fn, mock_light, mock_call, client
    ):
        cached = json.dumps({"result": "cached result"}, ensure_ascii=False)
        mock_cache = AsyncMock()
        mock_cache.get.return_value = cached
        mock_cache_fn.return_value = mock_cache

        result = await client.execute("finance_data", "比亚迪 ROE")

        assert result == "cached result"
        mock_call.assert_not_awaited()
        mock_light.assert_not_awaited()

    @pytest.mark.asyncio
    @patch.object(DataProClient, "_call_datapro", new_callable=AsyncMock)
    @patch.object(DataProClient, "_light_process", new_callable=AsyncMock)
    @patch(f"{_BASE}.get_semantic_cache")
    async def test_llm_failure_fallback_to_preprocessed(
        self, mock_cache_fn, mock_light, mock_call, client
    ):
        mock_cache = AsyncMock()
        mock_cache.get.return_value = None
        mock_cache_fn.return_value = mock_cache
        mock_call.return_value = _LARGE_TEXT
        mock_light.return_value = None

        result = await client.execute("finance_data", "华为技术有限公司 工商信息")

        assert "DATAPRO_RAW_" in result
        mock_light.assert_awaited_once()

    @pytest.mark.asyncio
    @patch.object(DataProClient, "_call_datapro", new_callable=AsyncMock)
    @patch.object(DataProClient, "_light_process", new_callable=AsyncMock)
    @patch(f"{_BASE}.get_semantic_cache")
    async def test_multi_batch_via_split_fn(
        self, mock_cache_fn, mock_light, mock_call, client
    ):
        mock_cache = AsyncMock()
        mock_cache.get.return_value = None
        mock_cache_fn.return_value = mock_cache
        mock_call.side_effect = ["batch1", "batch2"]

        def split_fn(_q: str) -> list[str]:
            return ["q1", "q2"]

        result = await client.execute("finance_data", "q", split_fn=split_fn)

        assert mock_call.await_count == 2
        assert "batch1" in result and "batch2" in result
        mock_light.assert_not_awaited()
