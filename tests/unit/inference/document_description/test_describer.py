"""DocDescriber 单元测试.

覆盖文档概要生成器的响应解析与调用链 (mock invoke_with_fallback).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.inference.document_description.describer import DocDescriber


class TestExtractSummaryFromResponse:
    """测试 _extract_summary_from_response - 模型响应解析."""

    def test_extract_from_json_response(self):
        """应从 JSON 响应中提取 brief 和 summary."""
        describer = DocDescriber()
        response = '{"brief": "季度财务报告", "summary": "2026年Q2营收增长15%"}'
        result = describer._extract_summary_from_response(response)

        assert result[0] == "季度财务报告"
        assert "营收增长" in result[1]

    def test_extract_from_json_with_extra_text(self):
        """应从含额外文本的响应中提取 JSON 字段."""
        describer = DocDescriber()
        response = '概要如下:\n{"brief": "b", "summary": "s"}\n完毕'
        result = describer._extract_summary_from_response(response)

        assert result == ("b", "s")

    def test_extract_from_code_block_json(self):
        """应去除 ```json 代码块标记后解析."""
        describer = DocDescriber()
        response = '```json\n{"brief": "会议纪要", "summary": "项目排期确认"}\n```'
        result = describer._extract_summary_from_response(response)

        assert result == ("会议纪要", "项目排期确认")

    def test_extract_from_empty_string(self):
        """空字符串返回空元组."""
        describer = DocDescriber()
        assert describer._extract_summary_from_response("") == ("", "")


class TestDescribe:
    """测试 describe 调用链."""

    @pytest.mark.asyncio
    async def test_describe_calls_model_with_truncated_content(self):
        """超长输入截断到窗口上限后调用模型, 返回 brief/summary."""
        describer = DocDescriber()
        response = MagicMock()
        response.content = '{"brief": "b", "summary": "s"}'

        with patch(
            "src.inference.llm.model_loader.invoke_with_fallback",
            new=AsyncMock(return_value=response),
        ) as mock_invoke:
            brief, summary = await describer.describe(
                "report.md",
                "内容\n" * 20000,  # 超窗口
            )

        assert brief == "b"
        assert summary == "s"
        # 输入被截断
        prompt_text = mock_invoke.call_args[0][0][0].content
        assert len(prompt_text) < 60000

    @pytest.mark.asyncio
    async def test_describe_failure_returns_empty(self):
        """模型调用失败不抛异常, 返回空元组."""
        describer = DocDescriber()

        with patch(
            "src.inference.llm.model_loader.invoke_with_fallback",
            new=AsyncMock(side_effect=RuntimeError("api down")),
        ):
            brief, summary = await describer.describe("report.md", "文档内容")

        assert brief == ""
        assert summary == ""
