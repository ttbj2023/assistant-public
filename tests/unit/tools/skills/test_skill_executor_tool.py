"""SkillExecutorTool 单元测试.

覆盖范围:
- _arun成功 + 产物回收(Mock httpx + register_tool_output)
- _arun成功无产物
- _arun服务异常
- _build_message消息构造
- _normalize_timeout
"""

from __future__ import annotations

import base64
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.tools.skills.skill_executor_tool import (
    DEFAULT_TIMEOUT,
    MAX_TIMEOUT,
    SkillExecutorTool,
)


def _make_mock_response(data: dict) -> MagicMock:
    resp = MagicMock()
    resp.json.return_value = data
    resp.raise_for_status = MagicMock()
    return resp


def _make_mock_client(resp: MagicMock) -> AsyncMock:
    client = AsyncMock()
    client.post.return_value = resp
    client.__aenter__.return_value = client
    client.__aexit__.return_value = None
    return client


def _mock_ctx() -> MagicMock:
    ctx = MagicMock()
    ctx.user_id = "u1"
    ctx.thread_id = "t1"
    return ctx


class TestArunSuccess:
    @pytest.mark.asyncio
    async def test_success_with_outputs_registers_file(self, tmp_path: Path) -> None:
        tool = SkillExecutorTool()
        resp = _make_mock_response({
            "success": True,
            "stdout": "done",
            "stderr": "",
            "exit_code": 0,
            "timed_out": False,
            "duration_ms": 100,
            "created_files": [
                {
                    "filename": "report.xlsx",
                    "content_b64": base64.b64encode(b"fake-xlsx").decode(),
                }
            ],
        })
        client = _make_mock_client(resp)
        with (
            patch(
                "src.tools.skills.skill_executor_tool.httpx.AsyncClient",
                return_value=client,
            ),
            patch(
                "src.core.context.get_user_context_or_none",
                return_value=_mock_ctx(),
            ),
            patch("src.core.path_resolver.get_user_path_resolver") as mock_resolver,
            patch(
                "src.tools.shared.file_output.register_tool_output",
                new_callable=AsyncMock,
            ) as mock_reg,
        ):
            mock_resolver.return_value.get_shared_storage_path.return_value = tmp_path
            mock_reg.return_value = {
                "success": True,
                "file_id": "abc12345",
                "filename": "report.xlsx",
            }
            result = await tool._arun(code="import openpyxl")

        data = json.loads(result)
        assert data["success"] is True
        assert data["created_files"][0]["file_id"] == "abc12345"
        # 落盘文件存在
        assert any(tmp_path.glob("report_*.xlsx"))
        mock_reg.assert_called_once()

    @pytest.mark.asyncio
    async def test_success_no_outputs(self) -> None:
        tool = SkillExecutorTool()
        resp = _make_mock_response({
            "success": True,
            "stdout": "ok",
            "stderr": "",
            "exit_code": 0,
            "timed_out": False,
            "duration_ms": 50,
            "created_files": [],
        })
        client = _make_mock_client(resp)
        with patch(
            "src.tools.skills.skill_executor_tool.httpx.AsyncClient",
            return_value=client,
        ):
            result = await tool._arun(code="print(1)")
        data = json.loads(result)
        assert data["success"] is True
        assert data["created_files"] == []


class TestArunFailure:
    @pytest.mark.asyncio
    async def test_service_error_returns_formatted_error(self) -> None:
        tool = SkillExecutorTool()
        with patch(
            "src.tools.skills.skill_executor_tool.httpx.AsyncClient"
        ) as mock_cls:
            mock_cls.return_value.__aenter__ = AsyncMock(
                side_effect=ConnectionError("refused"),
            )
            result = await tool._arun(code="print(1)")
        data = json.loads(result)
        assert data["success"] is False

    @pytest.mark.asyncio
    async def test_failed_execution_no_collection(self) -> None:
        """执行失败(exit_code!=0)时不回收产物."""
        tool = SkillExecutorTool()
        resp = _make_mock_response({
            "success": False,
            "stdout": "",
            "stderr": "boom",
            "exit_code": 1,
            "timed_out": False,
            "duration_ms": 10,
            "created_files": [
                {
                    "filename": "x.xlsx",
                    "content_b64": base64.b64encode(b"x").decode(),
                }
            ],
        })
        client = _make_mock_client(resp)
        with (
            patch(
                "src.tools.skills.skill_executor_tool.httpx.AsyncClient",
                return_value=client,
            ),
            patch(
                "src.tools.shared.file_output.register_tool_output",
                new_callable=AsyncMock,
            ) as mock_reg,
        ):
            result = await tool._arun(code="raise Exception")
        data = json.loads(result)
        assert data["success"] is False
        assert data["created_files"] == []
        mock_reg.assert_not_called()


class TestNormalizeTimeout:
    def test_default_timeout(self) -> None:
        tool = SkillExecutorTool()
        assert tool._normalize_timeout(None) == DEFAULT_TIMEOUT

    def test_clamped_to_max(self) -> None:
        tool = SkillExecutorTool()
        assert tool._normalize_timeout(999.0) == MAX_TIMEOUT

    def test_zero_raises(self) -> None:
        tool = SkillExecutorTool()
        with pytest.raises(ValueError, match="大于0"):
            tool._normalize_timeout(0)


class TestBuildMessage:
    def test_success_with_files(self) -> None:
        msg = SkillExecutorTool._build_message(
            True, False, 0, [{"file_id": "a1b2c3d4"}]
        )
        assert "a1b2c3d4" in msg

    def test_success_no_files(self) -> None:
        msg = SkillExecutorTool._build_message(True, False, 0, [])
        assert "无产物" in msg

    def test_timed_out(self) -> None:
        msg = SkillExecutorTool._build_message(False, True, None, [])
        assert "超时" in msg

    def test_failed(self) -> None:
        msg = SkillExecutorTool._build_message(False, False, 1, [])
        assert "失败" in msg


class TestTitleAndDescWrite:
    """title 透传 brief + code 写入 .desc.md."""

    @pytest.mark.asyncio
    async def test_title_propagates_to_brief_and_writes_desc(
        self, tmp_path: Path
    ) -> None:
        """title 应透传到 brief, code 写入 .desc.md."""
        tool = SkillExecutorTool()
        resp = _make_mock_response({
            "success": True,
            "stdout": "done",
            "stderr": "",
            "exit_code": 0,
            "timed_out": False,
            "duration_ms": 100,
            "created_files": [
                {
                    "filename": "report.xlsx",
                    "content_b64": base64.b64encode(b"fake").decode(),
                }
            ],
        })
        client = _make_mock_client(resp)
        with (
            patch(
                "src.tools.skills.skill_executor_tool.httpx.AsyncClient",
                return_value=client,
            ),
            patch(
                "src.core.context.get_user_context_or_none",
                return_value=_mock_ctx(),
            ),
            patch("src.core.path_resolver.get_user_path_resolver") as mock_resolver,
            patch(
                "src.tools.shared.file_output.register_tool_output",
                new_callable=AsyncMock,
            ) as mock_reg,
            patch("src.files.desc_writer.write_desc") as mock_write_desc,
        ):
            mock_resolver.return_value.get_shared_storage_path.return_value = tmp_path
            mock_reg.return_value = {
                "success": True,
                "file_id": "abc12345",
                "filename": "report.xlsx",
            }
            result = await tool._arun(
                code="import openpyxl", title="季度销售报表"
            )

        data = json.loads(result)
        assert data["success"] is True
        # brief 透传 title
        assert mock_reg.call_args.kwargs["brief"] == "季度销售报表"
        # code 写入 .desc.md (write_desc(user_id, file_id, source_code))
        mock_write_desc.assert_called_once()
        assert mock_write_desc.call_args.args[0] == "u1"
        assert mock_write_desc.call_args.args[1] == "abc12345"
        assert mock_write_desc.call_args.args[2] == "import openpyxl"

    @pytest.mark.asyncio
    async def test_no_title_brief_is_none(self, tmp_path: Path) -> None:
        """不传 title 时 brief=None (走 _compose_brief 兜底)."""
        tool = SkillExecutorTool()
        resp = _make_mock_response({
            "success": True,
            "stdout": "done",
            "stderr": "",
            "exit_code": 0,
            "timed_out": False,
            "duration_ms": 100,
            "created_files": [
                {
                    "filename": "report.xlsx",
                    "content_b64": base64.b64encode(b"fake2").decode(),
                }
            ],
        })
        client = _make_mock_client(resp)
        with (
            patch(
                "src.tools.skills.skill_executor_tool.httpx.AsyncClient",
                return_value=client,
            ),
            patch(
                "src.core.context.get_user_context_or_none",
                return_value=_mock_ctx(),
            ),
            patch("src.core.path_resolver.get_user_path_resolver") as mock_resolver,
            patch(
                "src.tools.shared.file_output.register_tool_output",
                new_callable=AsyncMock,
            ) as mock_reg,
            patch("src.files.desc_writer.write_desc"),
        ):
            mock_resolver.return_value.get_shared_storage_path.return_value = tmp_path
            mock_reg.return_value = {
                "success": True,
                "file_id": "deadbeef",
                "filename": "report.xlsx",
            }
            await tool._arun(code="import openpyxl")

        assert mock_reg.call_args.kwargs["brief"] is None
