"""tool_runtime 公共运行时行为单元测试.

测试 src/tools/shared/tool_runtime.py:
- format_tool_error: 统一错误格式化
- format_tool_success: 统一成功格式化
- run_sync: sync→async 桥接 + 截断 + 错误格式化
- sync_runnable: 类装饰器注入 _run
- inject_identity: 运行时身份注入 (绕过 Pydantic Field)
"""

from __future__ import annotations

import json
from unittest.mock import patch

from langchain_core.tools import BaseTool

from src.tools.shared.tool_runtime import (
    format_tool_error,
    format_tool_success,
    inject_identity,
    run_sync,
    sync_runnable,
)


class TestFormatToolError:
    def test_should_return_json_error_format(self):
        """应返回 JSON 格式错误信息."""
        error = ValueError("参数无效")
        result = format_tool_error(error)

        parsed = json.loads(result)
        assert parsed["success"] is False
        assert "参数无效" in parsed["message"]
        assert "ValueError" in parsed["error"]

    def test_should_include_context_when_provided(self):
        """提供 context 时应包含在响应中."""
        error = RuntimeError("error")
        result = format_tool_error(error, context="工具执行")

        parsed = json.loads(result)
        assert parsed["context"] == "工具执行"

    def test_should_not_include_context_when_absent(self):
        """未提供 context 时不应包含."""
        error = RuntimeError("error")
        result = format_tool_error(error)

        parsed = json.loads(result)
        assert "context" not in parsed


class TestFormatToolSuccess:
    def test_should_return_json_success_format(self):
        """应返回 JSON 格式成功信息."""
        result = format_tool_success({"count": 5})

        parsed = json.loads(result)
        assert parsed["success"] is True
        assert parsed["count"] == 5
        assert parsed["message"] == "操作成功"

    def test_should_use_custom_message(self):
        """应使用自定义消息."""
        result = format_tool_success({}, message="创建成功")

        parsed = json.loads(result)
        assert parsed["message"] == "创建成功"


@sync_runnable
class _StubTool(BaseTool):
    """用于测试 run_sync / sync_runnable 的桩工具."""

    name: str = "stub_tool"
    description: str = "测试桩"

    async def _arun(self, **kwargs):
        return json.dumps({"success": True, "action": kwargs.get("action")})


@sync_runnable
class _ErrorStubTool(BaseTool):
    """抛出异常的桩工具."""

    name: str = "error_stub"
    description: str = "错误桩"

    async def _arun(self, **kwargs):
        raise ValueError("测试错误")


class TestRunSync:
    def test_should_bridge_to_arun_and_truncate(self):
        """run_sync 应桥接到 _arun 并截断结果."""
        with patch(
            "src.utils.async_utils.run_async_in_sync_context",
            return_value='{"success": true}',
        ):
            result = run_sync(_StubTool(), action="test")
            assert result == '{"success": true}'

    def test_should_format_error_on_bridge_failure(self):
        """桥接失败应返回 JSON 错误."""
        with patch(
            "src.utils.async_utils.run_async_in_sync_context",
            side_effect=RuntimeError("bridge error"),
        ):
            result = run_sync(_ErrorStubTool(), action="test")

            parsed = json.loads(result)
            assert parsed["success"] is False

    def test_should_truncate_long_result(self):
        """超长结果应被截断."""
        long_result = "x" * 50000
        with patch(
            "src.utils.async_utils.run_async_in_sync_context",
            return_value=long_result,
        ):
            result = run_sync(_StubTool(), action="test")

            assert len(result) < 50000
            assert "已截断" in result


class TestSyncRunnableDecorator:
    def test_should_inject_run_method(self):
        """装饰器应给类注入 _run 方法."""

        @sync_runnable
        class DecoratedTool(BaseTool):
            name: str = "decorated"
            description: str = "装饰工具"

            async def _arun(self, **kwargs):
                return '{"ok": true}'

        tool = DecoratedTool()
        assert hasattr(tool, "_run")

        with patch(
            "src.utils.async_utils.run_async_in_sync_context",
            return_value='{"ok": true}',
        ):
            result = tool._run(action="x")
            assert result == '{"ok": true}'

    def test_should_format_error_on_failure(self):
        """装饰器注入的 _run 失败时应返回 JSON 错误."""

        @sync_runnable
        class FailTool(BaseTool):
            name: str = "fail"
            description: str = "失败工具"

            async def _arun(self, **kwargs):
                raise ValueError("boom")

        tool = FailTool()
        with patch(
            "src.utils.async_utils.run_async_in_sync_context",
            side_effect=ValueError("boom"),
        ):
            result = tool._run()
            parsed = json.loads(result)
            assert parsed["success"] is False


class TestInjectIdentity:
    def test_should_set_identity_attributes(self):
        """inject_identity 应设置 user_id/thread_id/agent_id."""

        @sync_runnable
        class IdentityTool(BaseTool):
            name: str = "id_tool"
            description: str = "身份测试"

            async def _arun(self, **kwargs):
                return self.user_id

        tool = IdentityTool()
        inject_identity(tool, "user123", "thread456", "agent789")

        assert tool.user_id == "user123"
        assert tool.thread_id == "thread456"
        assert tool.agent_id == "agent789"

    def test_identity_should_not_appear_in_schema(self):
        """身份字段不应出现在 Pydantic schema 中."""

        @sync_runnable
        class SchemaTool(BaseTool):
            name: str = "schema_tool"
            description: str = "schema 测试"

            async def _arun(self, **kwargs):
                return ""

        tool = SchemaTool()
        inject_identity(tool, "u", "t", "a")

        schema = tool.model_json_schema()
        properties = schema.get("properties", {})
        assert "user_id" not in properties
        assert "thread_id" not in properties
        assert "agent_id" not in properties
