"""IndexArcAnalyzer 与索引分组 formatter 单元测试."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.inference.content_analyzer.index_arc_analyzer import (
    IndexArcAnalyzer,
    format_run_entries,
    parse_arc_phrase,
)
from src.storage.formatters.conversation_formatter import create_conversation_formatter


class TestParseArcPhrase:
    """弧短语解析与截断."""

    def test_parse_normal(self) -> None:
        assert (
            parse_arc_phrase('{"arc_phrase": "建立健康档案与体检"}', 40)
            == "建立健康档案与体检"
        )

    def test_parse_truncates_over_max(self) -> None:
        phrase = parse_arc_phrase('{"arc_phrase": "' + "x" * 60 + '"}', 40)
        assert len(phrase) == 40

    def test_parse_empty_raises(self) -> None:
        with pytest.raises(ValueError):
            parse_arc_phrase('{"arc_phrase": ""}', 40)

    def test_parse_extracts_from_wrapped_text(self) -> None:
        result = parse_arc_phrase('noise {"arc_phrase": "ok"} trailing', 40)
        assert result == "ok"


class TestFormatRunEntries:
    def test_formats_round_topic_summary(self) -> None:
        entries = [
            {"round": 1, "topic": "健康", "summary": "体检"},
            {"round": 2, "topic": "健康", "summary": "指标"},
        ]
        out = format_run_entries(entries)
        assert "R1: 健康 - 体检" in out
        assert "R2: 健康 - 指标" in out


class TestIndexArcAnalyzerDistill:
    @pytest.mark.asyncio
    async def test_distill_calls_llm_and_parses(self) -> None:
        analyzer = IndexArcAnalyzer(model_id="fake:model", max_chars=40)

        fake_resp = MagicMock()
        fake_resp.content = '{"arc_phrase": "饮食记录系统搭建"}'

        with patch(
            "src.inference.content_analyzer.index_arc_analyzer.invoke_with_fallback",
            new=AsyncMock(return_value=fake_resp),
        ) as mock_invoke:
            arc = await analyzer.distill(
                3,
                5,
                [
                    {"round": 3, "topic": "饮食", "summary": "记录"},
                    {"round": 4, "topic": "饮食", "summary": "卡路里"},
                ],
            )

        assert arc == "饮食记录系统搭建"
        mock_invoke.assert_awaited_once()
        assert mock_invoke.call_args[0][1] == "fake:model"
        assert mock_invoke.call_args.kwargs.get("fallback_kind") == "text"
        assert mock_invoke.call_args.kwargs.get("usage_tag") == "memory_analyzer"

    @pytest.mark.asyncio
    async def test_prompt_targets_max_chars_minus_margin(self) -> None:
        """prompt 字数目标 = max_chars - 10(留余量), 硬截断仍 max_chars."""
        analyzer = IndexArcAnalyzer(model_id="fake:model", max_chars=60)

        captured_prompt = {}
        fake_resp = MagicMock()
        fake_resp.content = '{"arc_phrase": "弧"}'

        async def fake_invoke(prompt, *args, **kwargs):
            captured_prompt["text"] = prompt[0].content
            return fake_resp

        with patch(
            "src.inference.content_analyzer.index_arc_analyzer.invoke_with_fallback",
            new=fake_invoke,
        ):
            await analyzer.distill(1, 2, [{"round": 1, "topic": "t", "summary": "s"}])

        # prompt 要求 50 字(60-10 余量), 不是 60
        assert "不超过50字" in captured_prompt["text"]
        assert "不超过60字" not in captured_prompt["text"]


class TestFormatIndexGroups:
    """老期冻结弧短语表 formatter."""

    @pytest.mark.asyncio
    async def test_empty_returns_empty(self) -> None:
        formatter = create_conversation_formatter()
        assert await formatter.format_index_groups([]) == ""

    @pytest.mark.asyncio
    async def test_renders_timeline_table(self) -> None:
        formatter = create_conversation_formatter()
        out = await formatter.format_index_groups([
            {"round_start": 1, "round_end": 15, "arc_phrase": "健康档案建立"},
            {"round_start": 16, "round_end": 16, "arc_phrase": "项目启动"},
        ])
        assert "<timeline>" in out
        assert "| 1-15 | 健康档案建立 |" in out
        assert "| 16 | 项目启动 |" in out
