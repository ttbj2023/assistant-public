"""BusinessRegistryTool 单元测试 - 工具元数据 + 委派."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from src.tools.external.datapro.business_registry_tool import BusinessRegistryTool

_MOD = "src.tools.external.datapro.business_registry_tool"


@pytest.fixture
def tool() -> BusinessRegistryTool:
    return BusinessRegistryTool()


class TestMetadata:
    def test_name_and_keywords(self, tool):
        assert tool.name == "business_registry"
        assert tool.search_keywords
        assert tool.description
        assert tool.args_schema is not None


class TestDelegation:
    @pytest.mark.asyncio
    async def test_arun_delegates_without_split(self, tool):
        with patch.object(
            tool._client, "execute", new_callable=AsyncMock
        ) as mock_execute:
            mock_execute.return_value = "result"
            result = await tool._arun("华为技术有限公司 工商信息")
            assert result == "result"
            mock_execute.assert_awaited_once()
            assert mock_execute.await_args.kwargs.get("split_fn") is None

    @pytest.mark.asyncio
    async def test_is_available_delegates(self, tool):
        with patch(f"{_MOD}.DataProClient.is_available", return_value=True):
            assert await tool.is_available() is True
