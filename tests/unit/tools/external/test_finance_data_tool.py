"""FinanceDataTool 单元测试 - 工具元数据 + 委派 + 标的拆分."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from src.tools.external.datapro.finance_data_tool import (
    FinanceDataTool,
    _split_query_if_needed,
)

_MOD = "src.tools.external.datapro.finance_data_tool"


@pytest.fixture
def tool() -> FinanceDataTool:
    return FinanceDataTool()


class TestSplitQuery:
    def test_single_code_no_split(self):
        assert _split_query_if_needed("002594 ROE") == ["002594 ROE"]

    def test_company_name_no_split(self):
        assert _split_query_if_needed("比亚迪 ROE") == ["比亚迪 ROE"]

    def test_three_codes_no_split(self):
        assert len(_split_query_if_needed("002594 000858 600519 ROE")) == 1

    def test_four_codes_split_into_two(self):
        result = _split_query_if_needed("002594 000858 600519 000333 ROE")
        assert len(result) == 2
        assert "002594 000858 600519 ROE" in result
        assert "000333 ROE" in result

    def test_description_preserved_after_split(self):
        result = _split_query_if_needed(
            "002594.SZ 000858.SZ 600519.SH 000333.SZ 000568.SZ 季报"
        )
        assert len(result) == 2
        assert all("季报" in q for q in result)


class TestMetadata:
    def test_name_and_keywords(self, tool):
        assert tool.name == "finance_data"
        assert tool.search_keywords
        assert tool.description
        assert tool.args_schema is not None


class TestDelegation:
    @pytest.mark.asyncio
    async def test_arun_delegates_with_split_fn(self, tool):
        with patch.object(
            tool._client, "execute", new_callable=AsyncMock
        ) as mock_execute:
            mock_execute.return_value = "result"
            result = await tool._arun("比亚迪 ROE")
            assert result == "result"
            mock_execute.assert_awaited_once()
            assert mock_execute.await_args.kwargs["split_fn"] is _split_query_if_needed

    @pytest.mark.asyncio
    async def test_is_available_delegates(self, tool):
        with patch(f"{_MOD}.DataProClient.is_available", return_value=True):
            assert await tool.is_available() is True
