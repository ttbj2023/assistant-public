"""PythonExecutorTool 单元测试."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from src.tools.external.python_executor_tool import PythonExecutorTool


@pytest.fixture
def tool() -> PythonExecutorTool:
    return PythonExecutorTool(
        base_url="http://127.0.0.1:8766",
    )


@pytest.mark.asyncio
async def test_arun_should_return_execution_result(tool: PythonExecutorTool) -> None:
    """远端执行成功时应返回标准执行结果."""
    response = httpx.Response(
        200,
        json={
            "success": True,
            "stdout": "2\n",
            "stderr": "",
            "exit_code": 0,
            "timed_out": False,
            "duration_ms": 12,
        },
        request=httpx.Request("POST", "http://tool-runtime/execute"),
    )

    mock_client = AsyncMock()
    mock_client.__aenter__.return_value = mock_client
    mock_client.__aexit__.return_value = None
    mock_client.post.return_value = response

    with patch("src.tools.external.python_executor_tool.httpx.AsyncClient") as cls:
        cls.return_value = mock_client
        result_text = await tool._arun("print(1 + 1)")

    result = json.loads(result_text)
    assert result["success"] is True
    assert result["stdout"] == "2\n"
    assert result["exit_code"] == 0
    mock_client.post.assert_awaited_once()


@pytest.mark.asyncio
async def test_arun_should_return_nonzero_exit(tool: PythonExecutorTool) -> None:
    """执行失败不应抛异常, 应返回exit_code和stderr."""
    response = httpx.Response(
        200,
        json={
            "success": False,
            "stdout": "",
            "stderr": "boom",
            "exit_code": 1,
            "timed_out": False,
            "duration_ms": 7,
        },
        request=httpx.Request("POST", "http://tool-runtime/execute"),
    )

    mock_client = AsyncMock()
    mock_client.__aenter__.return_value = mock_client
    mock_client.__aexit__.return_value = None
    mock_client.post.return_value = response

    with patch("src.tools.external.python_executor_tool.httpx.AsyncClient") as cls:
        cls.return_value = mock_client
        result_text = await tool._arun("raise SystemExit(1)")

    result = json.loads(result_text)
    assert result["success"] is False
    assert result["stderr"] == "boom"
    assert result["exit_code"] == 1


@pytest.mark.asyncio
async def test_arun_should_format_connection_error(tool: PythonExecutorTool) -> None:
    """连接沙箱失败时应返回统一错误JSON."""
    mock_client = AsyncMock()
    mock_client.__aenter__.return_value = mock_client
    mock_client.__aexit__.return_value = None
    mock_client.post.side_effect = httpx.ConnectError("no socket")

    with patch("src.tools.external.python_executor_tool.httpx.AsyncClient") as cls:
        cls.return_value = mock_client
        result_text = await tool._arun("print('x')")

    result = json.loads(result_text)
    assert result["success"] is False
    assert "ConnectError" in result["error"]


@pytest.mark.asyncio
async def test_arun_should_reject_large_code(tool: PythonExecutorTool) -> None:
    """代码长度超过限制时应拒绝请求."""
    tool.max_code_chars = 3
    result_text = await tool._arun("print(1)")
    result = json.loads(result_text)
    assert result["success"] is False
    assert "代码过长" in result["message"]


def test_normalize_timeout_should_cap_to_max(tool: PythonExecutorTool) -> None:
    """超时时间应被限制到最大值."""
    assert tool._normalize_timeout(999) == pytest.approx(tool.max_timeout_seconds)


def test_truncate_should_mark_truncated() -> None:
    """截断输出时应标记truncated."""
    value, truncated = PythonExecutorTool._truncate("abcdef", 3)
    assert value == "abc"
    assert truncated is True


def test_base_url_should_read_tool_runtime_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """base_url 应由 TOOL_RUNTIME_BASE_URL 环境变量驱动(与 skill_executor 一致)."""
    monkeypatch.setenv("TOOL_RUNTIME_BASE_URL", "http://tool-runtime:8766")
    tool = PythonExecutorTool()
    assert tool.base_url == "http://tool-runtime:8766"


def test_base_url_should_fallback_to_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """未设置环境变量时回退到本地默认地址."""
    monkeypatch.delenv("TOOL_RUNTIME_BASE_URL", raising=False)
    tool = PythonExecutorTool()
    assert tool.base_url == "http://127.0.0.1:8766"
