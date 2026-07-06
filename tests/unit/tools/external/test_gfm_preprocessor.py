"""GFM 预处理器测试.

测试范围:
1. 图表代码块检测与替换
2. Fenced divs / callout 转换
3. Raw HTML 转换
4. 边界情况
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from src.tools.external.export_document.gfm_preprocessor import (
    _convert_callout_divs,
    _convert_raw_html,
    _find_chart_blocks,
    _parse_div_label,
    preprocess_gfm,
)

# =============================================================================
# 1. 图表代码块检测
# =============================================================================


class TestFindChartBlocks:
    """测试图表代码块检测."""

    def test_detect_mermaid(self):
        gfm = "一些文字\n```mermaid\ngraph TD\nA-->B\n```\n后续"
        blocks = list(_find_chart_blocks(gfm))
        assert len(blocks) == 1
        assert blocks[0][0] == "mermaid"
        assert "graph TD" in blocks[0][1]

    def test_detect_vega_lite(self):
        gfm = '```vega-lite\n{"mark": "bar"}\n```'
        blocks = list(_find_chart_blocks(gfm))
        assert len(blocks) == 1
        assert blocks[0][0] == "vega-lite"

    def test_ignore_regular_code(self):
        gfm = "```python\nprint('hello')\n```"
        blocks = list(_find_chart_blocks(gfm))
        assert len(blocks) == 0

    def test_multiple_charts(self):
        gfm = "```mermaid\ngraph TD\nA-->B\n```\n文字\n```vega-lite\n{}\n```"
        blocks = list(_find_chart_blocks(gfm))
        assert len(blocks) == 2

    def test_no_charts(self):
        gfm = "# 标题\n正文内容\n- 列表项"
        blocks = list(_find_chart_blocks(gfm))
        assert len(blocks) == 0


# =============================================================================
# 2. Callout / Fenced Divs 转换
# =============================================================================


class TestConvertCalloutDivs:
    """测试 fenced divs 转引用块."""

    def test_tip_callout(self):
        gfm = "::: tip\n这是一个提示\n:::"
        result = _convert_callout_divs(gfm)
        assert "> **TIP**" in result
        assert "> 这是一个提示" in result

    def test_warning_callout(self):
        gfm = "::: warning\n注意安全\n:::"
        result = _convert_callout_divs(gfm)
        assert "> **WARNING**" in result

    def test_class_syntax(self):
        gfm = "::: {.tip .important}\n重要提示\n:::"
        result = _convert_callout_divs(gfm)
        assert "> **TIP**" in result

    def test_custom_label(self):
        gfm = "::: 自定义标题\n内容\n:::"
        result = _convert_callout_divs(gfm)
        assert "> **自定义标题**" in result

    def test_empty_label(self):
        gfm = ":::\n纯内容\n:::"
        result = _convert_callout_divs(gfm)
        assert "> 纯内容" in result

    def test_no_divs(self):
        gfm = "# 标题\n正文"
        result = _convert_callout_divs(gfm)
        assert result == gfm

    def test_multiline_content(self):
        gfm = "::: note\n第一行\n第二行\n第三行\n:::"
        result = _convert_callout_divs(gfm)
        assert "> 第一行" in result
        assert "> 第二行" in result
        assert "> 第三行" in result


class TestParseDivLabel:
    """测试 div 标签解析."""

    def test_simple_type(self):
        assert _parse_div_label("tip") == "TIP"

    def test_class_syntax(self):
        assert _parse_div_label("{.warning}") == "WARNING"

    def test_multiple_classes(self):
        assert _parse_div_label("{.tip .highlight}") == "TIP"

    def test_custom_text(self):
        assert _parse_div_label("自定义标题") == "自定义标题"

    def test_empty(self):
        assert _parse_div_label("") == ""

    def test_case_insensitive(self):
        assert _parse_div_label("NOTE") == "NOTE"


# =============================================================================
# 3. Raw HTML 转换
# =============================================================================


class TestConvertRawHtml:
    """测试 raw HTML 转 Markdown."""

    def test_mark_to_bold(self):
        assert _convert_raw_html("<mark>重点</mark>") == "**重点**"

    def test_sub_removed(self):
        assert _convert_raw_html("H<sub>2</sub>O") == "H2O"

    def test_sup_removed(self):
        assert _convert_raw_html("x<sup>2</sup>") == "x2"

    def test_br_to_linebreak(self):
        result = _convert_raw_html("第一行<br>第二行")
        assert result == "第一行  \n第二行"

    def test_hr_preserved(self):
        result = _convert_raw_html("<hr>")
        assert result == "---"

    def test_details_to_blockquote(self):
        html = "<details><summary>标题</summary>内容</details>"
        result = _convert_raw_html(html)
        assert "> **标题**" in result
        assert "> 内容" in result

    def test_no_html(self):
        text = "# 标题\n正文内容"
        assert _convert_raw_html(text) == text

    def test_nested_mark(self):
        assert _convert_raw_html("文字 <mark>高亮</mark> 结尾") == "文字 **高亮** 结尾"


# =============================================================================
# 4. 集成: preprocess_gfm
# =============================================================================


class TestPreprocessGfm:
    """测试完整预处理流程."""

    @pytest.mark.asyncio
    async def test_text_only_passthrough(self, tmp_path):
        gfm = "# 标题\n\n正文内容\n\n- 列表项"
        result = await preprocess_gfm(gfm, tmp_path, render_charts=False)
        assert result == gfm

    @pytest.mark.asyncio
    async def test_callout_converted(self, tmp_path):
        gfm = "::: tip\n提示内容\n:::"
        result = await preprocess_gfm(gfm, tmp_path, render_charts=False)
        assert "> **TIP**" in result
        assert ":::" not in result

    @pytest.mark.asyncio
    async def test_html_converted(self, tmp_path):
        gfm = "这是 <mark>重点</mark> 内容"
        result = await preprocess_gfm(gfm, tmp_path, render_charts=False)
        assert "**重点**" in result
        assert "<mark>" not in result

    @pytest.mark.asyncio
    async def test_chart_rendering_disabled(self, tmp_path):
        gfm = "```mermaid\ngraph TD\nA-->B\n```"
        result = await preprocess_gfm(gfm, tmp_path, render_charts=False)
        assert "```mermaid" in result

    @pytest.mark.asyncio
    async def test_chart_block_replaced_with_png(self, tmp_path):
        gfm = "前面\n```mermaid\ngraph TD\nA-->B\n```\n后面"

        async def fake_render(engine, code, output_path):
            output_path.write_bytes(b"fake png")
            return output_path

        with patch(
            "src.tools.external.export_document.gfm_preprocessor._render_single_chart",
            side_effect=fake_render,
        ):
            result = await preprocess_gfm(gfm, tmp_path, render_charts=True)

        assert "```mermaid" not in result
        assert "![" in result
        assert "chart_0" in result
        assert "前面" in result
        assert "后面" in result

    @pytest.mark.asyncio
    async def test_chart_failure_preserves_block(self, tmp_path):
        gfm = "```mermaid\ngraph TD\nA-->B\n```"

        async def fake_render_fail(engine, code, output_path):
            raise RuntimeError("渲染失败")

        with patch(
            "src.tools.external.export_document.gfm_preprocessor._render_single_chart",
            side_effect=fake_render_fail,
        ):
            result = await preprocess_gfm(gfm, tmp_path, render_charts=True)

        assert "```mermaid" in result
