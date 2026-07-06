"""AsyncRequirementMemoryTool单元测试.

验证: 全文重写成功/清空/超限错误回传/query别名映射. Mock service.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from src.tools.internal.async_requirement_memory_tool import (
    AsyncRequirementMemoryTool,
    RequirementMemoryInput,
)


@pytest.fixture
def tool() -> AsyncRequirementMemoryTool:
    return AsyncRequirementMemoryTool(
        user_id="u",
        thread_id="t",
        agent_id="personal-assistant",
    )


def test_query_alias_maps_to_content():
    """query 别名重映射到 content, 且不暴露进 JSON schema."""
    model = RequirementMemoryInput.model_validate({"query": "回复简洁"})
    assert model.content == "回复简洁"
    schema_props = set(RequirementMemoryInput.model_json_schema()["properties"].keys())
    assert "query" not in schema_props
    assert "content" in schema_props


@pytest.mark.asyncio
async def test_arun_set_success(tool):
    """合法内容: 调用 set_content, 返回 success JSON."""
    mock_service = AsyncMock()
    mock_service.set_content = AsyncMock(return_value="a\nb")
    with patch.object(tool, "_get_service", AsyncMock(return_value=mock_service)):
        result = await tool._arun(content="a\nb")
    data = json.loads(result)
    assert data["success"] is True
    assert data["current_requirements"] == "a\nb"
    mock_service.set_content.assert_awaited_once_with("u", "t", "a\nb")


@pytest.mark.asyncio
async def test_arun_clear(tool):
    """空串=清空, 仍成功."""
    mock_service = AsyncMock()
    mock_service.set_content = AsyncMock(return_value="")
    with patch.object(tool, "_get_service", AsyncMock(return_value=mock_service)):
        result = await tool._arun(content="")
    data = json.loads(result)
    assert data["success"] is True
    assert "已清空" in data["current_requirements"]


@pytest.mark.asyncio
async def test_arun_over_limit_returns_error(tool):
    """超限: service 抛 ValueError -> 返回带限额上下文的 error JSON."""
    mock_service = AsyncMock()
    mock_service.set_content = AsyncMock(
        side_effect=ValueError("要求条数 11 超过上限 10"),
    )
    with patch.object(tool, "_get_service", AsyncMock(return_value=mock_service)):
        result = await tool._arun(content="x\n" * 11)
    data = json.loads(result)
    assert data["success"] is False
    assert "限额" in data.get("context", "")


@pytest.mark.asyncio
async def test_arun_via_query_alias(tool):
    """通过 query 别名调用 _arun 应等价于 content."""
    mock_service = AsyncMock()
    mock_service.set_content = AsyncMock(return_value="简洁")
    # 模拟 QueryAliasModel before-validator 已把 query 重映射为 content
    with patch.object(tool, "_get_service", AsyncMock(return_value=mock_service)):
        result = await tool._arun(content="简洁")
    data = json.loads(result)
    assert data["success"] is True
