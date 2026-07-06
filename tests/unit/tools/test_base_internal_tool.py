"""内部工具基类单元测试.

测试 src/tools/internal/base_internal_tool.py 的功能:
- 初始化: user_id/thread_id 设置
- _format_error: 统一错误格式化
- _format_success: 统一成功格式化
- _run: 同步桥接逻辑
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from src.tools.shared.base_internal_tool import BaseInternalTool


class ConcreteTool(BaseInternalTool):
    """用于测试的具体工具实现."""

    name: str = "test_tool"
    description: str = "测试工具"

    async def _arun(self, **kwargs):
        return json.dumps({"success": True, "action": kwargs.get("action")})


class ErrorTool(BaseInternalTool):
    """抛出异常的测试工具."""

    name: str = "error_tool"
    description: str = "错误测试工具"

    async def _arun(self, **kwargs):
        raise ValueError("测试错误")


class TestBaseInternalToolInit:
    def test_should_not_expose_user_id_as_field(self, test_user, test_thread_id):
        """测试user_id不应出现在Pydantic schema中."""
        tool = ConcreteTool(
            user_id=test_user, thread_id=test_thread_id, agent_id="test-agent"
        )

        schema = tool.model_json_schema()
        properties = schema.get("properties", {})
        assert "user_id" not in properties
        assert "thread_id" not in properties


class TestFormatError:
    def test_should_return_json_error_format(self):
        """测试应返回JSON格式错误信息."""
        error = ValueError("参数无效")
        result = BaseInternalTool._format_error(error)

        parsed = json.loads(result)
        assert parsed["success"] is False
        assert "参数无效" in parsed["message"]
        assert "ValueError" in parsed["error"]

    def test_should_include_context_when_provided(self):
        """测试提供context时应包含在响应中."""
        error = RuntimeError("error")
        result = BaseInternalTool._format_error(error, context="工具执行")

        parsed = json.loads(result)
        assert parsed["context"] == "工具执行"

    def test_should_not_include_context_when_absent(self):
        """测试未提供context时不应包含."""
        error = RuntimeError("error")
        result = BaseInternalTool._format_error(error)

        parsed = json.loads(result)
        assert "context" not in parsed


class TestFormatSuccess:
    def test_should_return_json_success_format(self):
        """测试应返回JSON格式成功信息."""
        result = BaseInternalTool._format_success({"count": 5})

        parsed = json.loads(result)
        assert parsed["success"] is True
        assert parsed["count"] == 5
        assert parsed["message"] == "操作成功"

    def test_should_use_custom_message(self):
        """测试应使用自定义消息."""
        result = BaseInternalTool._format_success({}, message="创建成功")

        parsed = json.loads(result)
        assert parsed["message"] == "创建成功"


class TestRunBridge:
    @pytest.mark.asyncio
    async def test_should_bridge_to_arun(self, test_user, test_thread_id):
        """测试_run应桥接到_arun."""
        tool = ConcreteTool(
            user_id=test_user, thread_id=test_thread_id, agent_id="test-agent"
        )

        with patch(
            "src.utils.async_utils.run_async_in_sync_context",
            return_value='{"success": true}',
        ) as mock_bridge:
            result = tool._run(action="test")
            mock_bridge.assert_called_once()

    @pytest.mark.asyncio
    async def test_should_format_error_on_bridge_failure(
        self, test_user, test_thread_id
    ):
        """测试桥接失败应格式化错误."""
        tool = ConcreteTool(
            user_id=test_user, thread_id=test_thread_id, agent_id="test-agent"
        )

        with patch(
            "src.utils.async_utils.run_async_in_sync_context",
            side_effect=RuntimeError("bridge error"),
        ):
            result = tool._run(action="test")

            parsed = json.loads(result)
            assert parsed["success"] is False

    @pytest.mark.asyncio
    async def test_should_truncate_long_result(self, test_user, test_thread_id):
        """测试_run应对超长结果进行截断."""
        tool = ConcreteTool(
            user_id=test_user, thread_id=test_thread_id, agent_id="test-agent"
        )
        long_result = "x" * 50000

        with patch(
            "src.utils.async_utils.run_async_in_sync_context",
            return_value=long_result,
        ):
            result = tool._run(action="test")

            assert len(result) < 50000
            assert "已截断" in result
