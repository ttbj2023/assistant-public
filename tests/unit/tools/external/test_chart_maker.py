"""图表渲染工具单元测试.

测试三个子工具 (mermaid_chart/vega_chart/markmap_chart) 及共享 service/builder.
Mock Playwright 渲染和文件注册.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from chart_builder import (
    _inject_default_x_label_angle,
    build_markmap_html,
    build_mermaid_html,
    build_vega_lite_html,
)
from pydantic import ValidationError

from src.tools.external.chart_maker.markmap_chart_tool import MarkmapChartTool
from src.tools.external.chart_maker.mermaid_chart_tool import MermaidChartTool
from src.tools.external.chart_maker.models import (
    MarkmapChartInput,
    MermaidChartInput,
    VegaChartInput,
)
from src.tools.external.chart_maker.vega_chart_tool import VegaChartTool

# =============================================================================
# 1. 输入模型测试
# =============================================================================


class TestMermaidChartInput:
    """测试 MermaidChartInput 模型 (无 width/height, mermaid 引擎忽略尺寸)."""

    def test_should_reject_empty_code(self):
        with pytest.raises(ValidationError):
            MermaidChartInput(code="", filename="test")

    def test_should_reject_empty_filename(self):
        with pytest.raises(ValidationError):
            MermaidChartInput(code="test", filename="")

    def test_should_reject_extra_fields(self):
        with pytest.raises(ValidationError):
            MermaidChartInput(  # type: ignore[call-arg]
                code="test",
                filename="test",
                extra_field="value",
            )

    def test_should_reject_width_field(self):
        """mermaid 不接受 width (无此字段)."""
        with pytest.raises(ValidationError):
            MermaidChartInput(  # type: ignore[call-arg]
                code="test",
                width=800,
            )

    def test_should_reject_engine_field(self):
        """拆分后不再有 engine 参数."""
        with pytest.raises(ValidationError):
            MermaidChartInput(  # type: ignore[call-arg]
                code="test",
                engine="mermaid",
            )

    def test_should_make_filename_optional(self):
        inp = MermaidChartInput(code="graph TD\nA-->B")
        assert inp.filename is None

    def test_should_remap_mermaid_code_alias(self):
        inp = MermaidChartInput(mermaid_code="graph TD\nA-->B", filename="flow")
        assert inp.code == "graph TD\nA-->B"

    def test_should_remap_graph_code_alias(self):
        inp = MermaidChartInput(graph_code="graph TD\nA-->B", filename="flow")
        assert inp.code == "graph TD\nA-->B"

    def test_should_remap_query_alias(self):
        inp = MermaidChartInput(query="graph TD\nA-->B", filename="flow")
        assert inp.code == "graph TD\nA-->B"

    def test_should_drop_alias_when_code_present(self):
        """code 已存在时, 别名应被丢弃, 不触发 extra_forbidden."""
        inp = MermaidChartInput(
            code="graph TD\nA-->B", mermaid_code="other", filename="flow"
        )
        assert inp.code == "graph TD\nA-->B"

    def test_should_reject_scale_out_of_range(self):
        with pytest.raises(ValidationError):
            MermaidChartInput(code="test", filename="c", scale=0)
        with pytest.raises(ValidationError):
            MermaidChartInput(code="test", filename="c", scale=7)


class TestVegaChartInput:
    """测试 VegaChartInput 模型 (含 width/height)."""

    _SPEC = '{"mark":"bar","data":{"values":[]}}'

    def test_should_reject_empty_code(self):
        with pytest.raises(ValidationError):
            VegaChartInput(code="", filename="chart")

    def test_should_reject_extra_fields(self):
        with pytest.raises(ValidationError):
            VegaChartInput(  # type: ignore[call-arg]
                code=self._SPEC,
                filename="chart",
                engine="vega_lite",
            )

    def test_should_make_filename_optional(self):
        inp = VegaChartInput(code=self._SPEC)
        assert inp.filename is None

    def test_should_accept_valid_dimensions(self):
        inp = VegaChartInput(
            code=self._SPEC, filename="chart", width=800, height=600, scale=3
        )
        assert inp.width == 800
        assert inp.height == 600

    def test_should_reject_width_below_minimum(self):
        with pytest.raises(ValidationError):
            VegaChartInput(code=self._SPEC, filename="c", width=50)

    def test_should_reject_width_above_maximum(self):
        with pytest.raises(ValidationError):
            VegaChartInput(code=self._SPEC, filename="c", width=5000)

    def test_should_reject_scale_out_of_range(self):
        with pytest.raises(ValidationError):
            VegaChartInput(code=self._SPEC, filename="c", scale=0)
        with pytest.raises(ValidationError):
            VegaChartInput(code=self._SPEC, filename="c", scale=7)

    def test_should_remap_spec_alias(self):
        inp = VegaChartInput(spec=self._SPEC, filename="chart")
        assert inp.code == self._SPEC

    def test_should_remap_json_alias(self):
        inp = VegaChartInput(json=self._SPEC, filename="chart")
        assert inp.code == self._SPEC

    def test_should_drop_alias_when_code_present(self):
        """code 已存在时, 别名应被丢弃, 不触发 extra_forbidden."""
        inp = VegaChartInput(code='{"mark":"bar"}', spec=self._SPEC, filename="chart")
        assert inp.code == '{"mark":"bar"}'


class TestMarkmapChartInput:
    """测试 MarkmapChartInput 模型 (含 width/height)."""

    _MD = "# 项目计划\n## 目标\n- 交付MVP"

    def test_should_reject_empty_code(self):
        with pytest.raises(ValidationError):
            MarkmapChartInput(code="", filename="test")

    def test_should_reject_extra_fields(self):
        with pytest.raises(ValidationError):
            MarkmapChartInput(  # type: ignore[call-arg]
                code=self._MD,
                filename="test",
                engine="markmap",
            )

    def test_should_make_filename_optional(self):
        inp = MarkmapChartInput(code=self._MD)
        assert inp.filename is None

    def test_should_remap_markdown_alias(self):
        inp = MarkmapChartInput(markdown=self._MD, filename="m")
        assert inp.code == self._MD

    def test_should_remap_md_alias(self):
        inp = MarkmapChartInput(md=self._MD, filename="m")
        assert inp.code == self._MD

    def test_should_drop_alias_when_code_present(self):
        """code 已存在时, 别名应被丢弃, 不触发 extra_forbidden."""
        inp = MarkmapChartInput(code=self._MD, markdown="other", filename="m")
        assert inp.code == self._MD


# =============================================================================
# 2. HTML 构建器测试
# =============================================================================


class TestBuildMermaidHtml:
    """测试 build_mermaid_html."""

    def test_should_produce_valid_html(self):
        html = build_mermaid_html("graph TD\nA-->B", title=None)
        assert html.startswith("<!DOCTYPE html>")
        assert "</html>" in html

    def test_should_include_mermaid_code(self):
        code = "graph TD\nA-->B"
        html = build_mermaid_html(code, title=None)
        assert code in html

    def test_should_include_title_when_provided(self):
        html = build_mermaid_html("graph TD\nA-->B", title="测试标题")
        assert "测试标题" in html
        assert 'class="chart-title"' in html

    def test_should_not_include_title_when_none(self):
        html = build_mermaid_html("graph TD\nA-->B", title=None)
        assert 'class="chart-title"' not in html

    def test_should_include_mermaid_js(self):
        html = build_mermaid_html("graph TD\nA-->B", title=None)
        assert "mermaid" in html.lower()

    def test_should_include_sentinel_script(self):
        html = build_mermaid_html("graph TD\nA-->B", title=None)
        assert "__rendered" in html
        assert "__renderError" in html

    def test_should_include_security_level_loose(self):
        html = build_mermaid_html("graph TD\nA-->B", title=None)
        assert "loose" in html

    def test_should_include_mermaid_content_container(self):
        html = build_mermaid_html("graph TD\nA-->B", title=None)
        assert 'class="mermaid-content"' in html

    def test_should_include_mermaid_font_weight(self):
        html = build_mermaid_html("graph TD\nA-->B", title=None)
        assert "font-weight: 500" in html


class TestBuildVegaLiteHtml:
    """测试 build_vega_lite_html."""

    _SIMPLE_SPEC = '{"mark":"bar","data":{"values":[{"a":"A","b":28}]}}'

    def test_should_produce_valid_html(self):
        html = build_vega_lite_html(self._SIMPLE_SPEC, title=None)
        assert html.startswith("<!DOCTYPE html>")
        assert "</html>" in html

    def test_should_include_spec_as_js_object(self):
        html = build_vega_lite_html(self._SIMPLE_SPEC, title=None)
        assert '"mark":"bar"' in html
        assert "var spec" in html

    def test_should_include_vega_libraries(self):
        html = build_vega_lite_html(self._SIMPLE_SPEC, title=None)
        assert "vegaEmbed" in html

    def test_should_include_svg_renderer(self):
        html = build_vega_lite_html(self._SIMPLE_SPEC, title=None)
        assert "svg" in html
        assert "actions: false" in html.replace(
            " ", ""
        ) or "actions:false" in html.replace(" ", "")

    def test_should_include_sentinel_script(self):
        html = build_vega_lite_html(self._SIMPLE_SPEC, title=None)
        assert "__rendered" in html
        assert "__renderError" in html

    def test_should_include_title_when_provided(self):
        html = build_vega_lite_html(self._SIMPLE_SPEC, title="数据图")
        assert "数据图" in html

    def test_should_include_vega_content_container(self):
        html = build_vega_lite_html(self._SIMPLE_SPEC, title=None)
        assert 'class="vega-content"' in html

    def test_should_inject_width_when_provided(self):
        html = build_vega_lite_html(self._SIMPLE_SPEC, title=None, width=800)
        assert "spec.width = 800;" in html

    def test_should_inject_height_when_provided(self):
        html = build_vega_lite_html(self._SIMPLE_SPEC, title=None, height=600)
        assert "spec.height = 600;" in html

    def test_should_not_inject_dimensions_when_none(self):
        html = build_vega_lite_html(self._SIMPLE_SPEC, title=None)
        assert "spec.width" not in html
        assert "spec.height" not in html

    def test_should_inject_default_x_label_angle_into_html(self):
        """nominal X 轴 spec 生成的 HTML 应包含默认 labelAngle=-45."""
        spec = (
            '{"mark":"bar","encoding":{'
            '"x":{"field":"cat","type":"nominal"},'
            '"y":{"field":"val","type":"quantitative"}}}'
        )
        html = build_vega_lite_html(spec, title=None)
        assert '"labelAngle":-45' in html


class TestBuildMarkmapHtml:
    """测试 build_markmap_html."""

    _SIMPLE_MARKDOWN = "# 项目计划\n## 目标\n- 交付MVP"

    def test_should_produce_valid_html(self):
        html = build_markmap_html(self._SIMPLE_MARKDOWN, title=None)
        assert html.startswith("<!DOCTYPE html>")
        assert "</html>" in html

    def test_should_include_markmap_libraries(self):
        html = build_markmap_html(self._SIMPLE_MARKDOWN, title=None)
        assert "markmap.Transformer" in html
        assert "markmap.Markmap.create" in html

    def test_should_include_markdown_as_json_string(self):
        html = build_markmap_html(self._SIMPLE_MARKDOWN, title=None)
        assert '"# 项目计划\\n## 目标\\n- 交付MVP"' in html

    def test_should_include_sentinel_script(self):
        html = build_markmap_html(self._SIMPLE_MARKDOWN, title=None)
        assert "__rendered" in html
        assert "__renderError" in html

    def test_should_include_title_when_provided(self):
        html = build_markmap_html(self._SIMPLE_MARKDOWN, title="思维导图")
        assert "思维导图" in html
        assert 'class="chart-title"' in html

    def test_should_include_markmap_content_container(self):
        html = build_markmap_html(self._SIMPLE_MARKDOWN, title=None)
        assert 'class="markmap-content"' in html
        assert 'id="markmap"' in html

    def test_should_use_default_dimensions(self):
        html = build_markmap_html(self._SIMPLE_MARKDOWN, title=None)
        assert 'width="1200"' in html
        assert 'height="800"' in html
        assert "width: 1200px" in html
        assert "height: 800px" in html

    def test_should_use_custom_dimensions(self):
        html = build_markmap_html(
            self._SIMPLE_MARKDOWN,
            title=None,
            width=1600,
            height=1000,
        )
        assert 'width="1600"' in html
        assert 'height="1000"' in html
        assert "width: 1600px" in html
        assert "height: 1000px" in html


# =============================================================================
# 2b. 默认 X 轴标签角度注入测试
# =============================================================================


class TestInjectDefaultXLabelAngle:
    """测试 _inject_default_x_label_angle 注入逻辑."""

    def test_should_inject_for_nominal_x_axis(self):
        spec = '{"encoding":{"x":{"field":"cat","type":"nominal"}}}'
        result = json.loads(_inject_default_x_label_angle(spec))
        assert result["encoding"]["x"]["axis"]["labelAngle"] == -45

    def test_should_inject_for_ordinal_x_axis(self):
        spec = '{"encoding":{"x":{"field":"cat","type":"ordinal"}}}'
        result = json.loads(_inject_default_x_label_angle(spec))
        assert result["encoding"]["x"]["axis"]["labelAngle"] == -45

    def test_should_not_overwrite_explicit_label_angle(self):
        spec = (
            '{"encoding":{"x":{"field":"cat","type":"nominal",'
            '"axis":{"labelAngle":-30}}}}'
        )
        result = json.loads(_inject_default_x_label_angle(spec))
        assert result["encoding"]["x"]["axis"]["labelAngle"] == -30

    def test_should_inject_into_existing_axis_without_label_angle(self):
        spec = (
            '{"encoding":{"x":{"field":"cat","type":"nominal",'
            '"axis":{"labelFontSize":12}}}}'
        )
        result = json.loads(_inject_default_x_label_angle(spec))
        assert result["encoding"]["x"]["axis"]["labelAngle"] == -45
        assert result["encoding"]["x"]["axis"]["labelFontSize"] == 12

    def test_should_not_inject_for_quantitative_x_axis(self):
        spec = '{"encoding":{"x":{"field":"val","type":"quantitative"}}}'
        result = json.loads(_inject_default_x_label_angle(spec))
        assert "axis" not in result["encoding"]["x"]

    def test_should_not_inject_for_temporal_x_axis(self):
        spec = '{"encoding":{"x":{"field":"date","type":"temporal"}}}'
        result = json.loads(_inject_default_x_label_angle(spec))
        assert "axis" not in result["encoding"]["x"]

    def test_should_skip_when_axis_is_null(self):
        """axis:null 表示显式隐藏轴, 不应注入."""
        spec = '{"encoding":{"x":{"field":"cat","type":"nominal","axis":null}}}'
        result = json.loads(_inject_default_x_label_angle(spec))
        assert result["encoding"]["x"]["axis"] is None

    def test_should_not_touch_y_axis(self):
        """仅干预 X 轴, Y 轴不受影响."""
        spec = (
            '{"encoding":{"x":{"field":"cat","type":"nominal"},'
            '"y":{"field":"val","type":"nominal"}}}'
        )
        result = json.loads(_inject_default_x_label_angle(spec))
        assert result["encoding"]["x"]["axis"]["labelAngle"] == -45
        assert "axis" not in result["encoding"]["y"]

    def test_should_inject_for_layered_spec(self):
        spec = (
            '{"layer":[{"mark":"bar","encoding":{'
            '"x":{"field":"cat","type":"nominal"},'
            '"y":{"field":"val","type":"quantitative"}}}]}'
        )
        result = json.loads(_inject_default_x_label_angle(spec))
        assert result["layer"][0]["encoding"]["x"]["axis"]["labelAngle"] == -45

    def test_should_preserve_explicit_angle_in_layered_spec(self):
        spec = (
            '{"layer":[{"mark":"bar","encoding":{'
            '"x":{"field":"cat","type":"nominal","axis":{"labelAngle":0}}}}]}'
        )
        result = json.loads(_inject_default_x_label_angle(spec))
        assert result["layer"][0]["encoding"]["x"]["axis"]["labelAngle"] == 0

    def test_should_inject_for_both_top_level_and_layer(self):
        spec = (
            '{"encoding":{"x":{"field":"a","type":"nominal"}},'
            '"layer":[{"encoding":{"x":{"field":"b","type":"ordinal"}}}]}'
        )
        result = json.loads(_inject_default_x_label_angle(spec))
        assert result["encoding"]["x"]["axis"]["labelAngle"] == -45
        assert result["layer"][0]["encoding"]["x"]["axis"]["labelAngle"] == -45

    def test_should_return_original_on_invalid_json(self):
        """非法 JSON 原样返回, 交由下游报错."""
        invalid = '{"encoding":{"x":'
        assert _inject_default_x_label_angle(invalid) == invalid

    def test_should_return_original_on_non_object_json(self):
        """JSON 数组等非对象结构原样返回."""
        arr = "[1, 2, 3]"
        assert _inject_default_x_label_angle(arr) == arr

    def test_should_preserve_other_spec_fields(self):
        spec = (
            '{"$schema":"http://x","data":{"values":[1]},'
            '"encoding":{"x":{"field":"cat","type":"nominal"}}}'
        )
        result = json.loads(_inject_default_x_label_angle(spec))
        assert result["$schema"] == "http://x"
        assert result["data"] == {"values": [1]}
        assert result["encoding"]["x"]["axis"]["labelAngle"] == -45


# =============================================================================
# 4. 工具执行测试 (_arun)
# =============================================================================


def _mock_user_context():
    """构造 Mock 用户上下文并 patch get_user_context."""
    ctx = MagicMock()
    ctx.user_id = "test_user"
    ctx.thread_id = "test_thread"
    ctx.agent_id = "test_agent"
    ctx.exported_files = []
    return ctx


class TestMermaidChartToolRun:
    """测试 MermaidChartTool._arun."""

    @pytest.fixture()
    def tool(self):
        return MermaidChartTool()

    @pytest.fixture()
    def mock_context(self):
        ctx = _mock_user_context()
        with patch("src.core.context.get_user_context", return_value=ctx):
            yield ctx

    @pytest.mark.asyncio
    async def test_arun_should_return_success_json(self, tool, mock_context):
        """正常渲染应返回成功JSON, engine 固定为 mermaid."""
        mock_result = {
            "success": True,
            "file_id": "test_id",
            "file_url": "https://example.com/file.png",
            "filename": "test.png",
            "format": "png",
            "size_bytes": 12345,
        }
        with patch(
            "src.tools.external.chart_maker.service.run_chart_maker",
            new_callable=AsyncMock,
            return_value=mock_result,
        ) as mock_run:
            result = await tool._arun(
                code="graph TD\nA-->B",
                filename="test_chart",
            )

        parsed = json.loads(result)
        assert parsed["success"] is True
        assert mock_run.call_args.kwargs["engine"] == "mermaid"
        # mermaid 不传 width/height
        assert mock_run.call_args.kwargs["width"] is None
        assert mock_run.call_args.kwargs["height"] is None

    @pytest.mark.asyncio
    async def test_arun_should_return_error_json_on_exception(self, tool, mock_context):
        """渲染异常应返回错误JSON."""
        with patch(
            "src.tools.external.chart_maker.service.run_chart_maker",
            new_callable=AsyncMock,
            side_effect=RuntimeError("渲染失败"),
        ):
            result = await tool._arun(
                code="graph TD\nA-->B",
                filename="test_chart",
            )

        parsed = json.loads(result)
        assert parsed["success"] is False
        assert "渲染失败" in parsed["message"]


class TestVegaChartToolRun:
    """测试 VegaChartTool._arun."""

    @pytest.fixture()
    def tool(self):
        return VegaChartTool()

    @pytest.fixture()
    def mock_context(self):
        ctx = _mock_user_context()
        with patch("src.core.context.get_user_context", return_value=ctx):
            yield ctx

    @pytest.mark.asyncio
    async def test_arun_should_pass_width_height_scale(self, tool, mock_context):
        """vega_chart 应透传 width/height/scale, engine 固定 vega_lite."""
        mock_result = {
            "success": True,
            "file_id": "x",
            "file_url": "u",
            "filename": "f",
        }
        with patch(
            "src.tools.external.chart_maker.service.run_chart_maker",
            new_callable=AsyncMock,
            return_value=mock_result,
        ) as mock_run:
            await tool._arun(
                code='{"mark":"bar"}',
                filename="chart",
                width=800,
                height=600,
                scale=5,
            )

        kwargs = mock_run.call_args.kwargs
        assert kwargs["engine"] == "vega_lite"
        assert kwargs["width"] == 800
        assert kwargs["height"] == 600
        assert kwargs["scale"] == 5

    @pytest.mark.asyncio
    async def test_arun_should_return_success_json(self, tool, mock_context):
        mock_result = {
            "success": True,
            "file_id": "test_id",
            "file_url": "https://example.com/file.png",
            "filename": "test.png",
        }
        with patch(
            "src.tools.external.chart_maker.service.run_chart_maker",
            new_callable=AsyncMock,
            return_value=mock_result,
        ):
            result = await tool._arun(code='{"mark":"bar"}', filename="test_chart")

        parsed = json.loads(result)
        assert parsed["success"] is True


class TestMarkmapChartToolRun:
    """测试 MarkmapChartTool._arun."""

    @pytest.fixture()
    def tool(self):
        return MarkmapChartTool()

    @pytest.fixture()
    def mock_context(self):
        ctx = _mock_user_context()
        with patch("src.core.context.get_user_context", return_value=ctx):
            yield ctx

    @pytest.mark.asyncio
    async def test_arun_should_pass_width_height(self, tool, mock_context):
        """markmap_chart 应透传 width/height, engine 固定 markmap."""
        mock_result = {
            "success": True,
            "file_id": "x",
            "file_url": "u",
            "filename": "f",
        }
        with patch(
            "src.tools.external.chart_maker.service.run_chart_maker",
            new_callable=AsyncMock,
            return_value=mock_result,
        ) as mock_run:
            await tool._arun(
                code="# 标题\n## 子项",
                filename="mindmap",
                width=1600,
                height=1000,
            )

        kwargs = mock_run.call_args.kwargs
        assert kwargs["engine"] == "markmap"
        assert kwargs["width"] == 1600
        assert kwargs["height"] == 1000

    @pytest.mark.asyncio
    async def test_arun_should_return_error_json_on_exception(self, tool, mock_context):
        with patch(
            "src.tools.external.chart_maker.service.run_chart_maker",
            new_callable=AsyncMock,
            side_effect=RuntimeError("渲染失败"),
        ):
            result = await tool._arun(code="# 标题", filename="test")

        parsed = json.loads(result)
        assert parsed["success"] is False


# =============================================================================
# 5. 服务层测试 (run_chart_maker)
# =============================================================================


class TestRunChartMaker:
    """测试 run_chart_maker 服务函数."""

    @pytest.fixture()
    def mock_context(self):
        ctx = MagicMock()
        ctx.user_id = "test_user"
        ctx.thread_id = "test_thread"
        ctx.agent_id = "test_agent"
        ctx.exported_files = []
        with patch("src.core.context.get_user_context", return_value=ctx):
            yield ctx

    @pytest.fixture()
    def mock_renderer(self, tmp_path):
        """Mock renderer, render_chart 写入 fake PNG."""
        mock_renderer = MagicMock()

        async def fake_render_chart(*, output_path: Path, **kwargs: object) -> Path:
            Path(output_path).write_bytes(b"fake_png_data")
            return Path(output_path)

        mock_renderer.render_chart = AsyncMock(side_effect=fake_render_chart)
        return mock_renderer

    @staticmethod
    def _setup_resolver(mock_resolver_fn: MagicMock, export_dir: Path) -> None:
        mock_resolver = MagicMock()
        mock_resolver.get_shared_storage_path.return_value = export_dir
        mock_resolver_fn.return_value = mock_resolver

    @pytest.mark.asyncio
    async def test_should_return_error_when_render_fails(self, mock_context, tmp_path):
        """渲染失败 (如 tool-runtime 返回错误) 应返回失败结果."""
        from src.tools.external.chart_maker.service import run_chart_maker

        mock_renderer = MagicMock()
        mock_renderer.render_chart = AsyncMock(
            side_effect=RuntimeError("不支持的引擎: invalid"),
        )
        export_dir = tmp_path / "exports" / "charts"
        export_dir.mkdir(parents=True)

        with (
            patch(
                "src.tools.external.chart_maker.service.get_browser_renderer",
                return_value=mock_renderer,
            ),
            patch(
                "src.core.path_resolver.get_user_path_resolver",
            ) as mock_resolver_fn,
        ):
            self._setup_resolver(mock_resolver_fn, export_dir)

            result = await run_chart_maker(
                engine="invalid",
                code="test",
                filename="test",
                title=None,
                user_id="test_user",
                thread_id="test_thread",
            )

        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_should_store_spec_in_desc_for_mermaid(
        self, mock_context, tmp_path, mock_renderer
    ):
        """mermaid 渲染应将原始输入 spec 写入 .desc.md."""
        from src.tools.external.chart_maker.service import run_chart_maker

        export_dir = tmp_path / "exports" / "charts"
        export_dir.mkdir(parents=True)

        mock_register = AsyncMock(
            return_value={
                "success": True,
                "file_id": "test",
                "file_url": "https://example.com/test.png",
                "filename": "test.png",
                "format": "png",
                "size_bytes": 13,
            }
        )

        with (
            patch(
                "src.tools.external.chart_maker.service.get_browser_renderer",
                return_value=mock_renderer,
            ),
            patch(
                "src.core.path_resolver.get_user_path_resolver",
            ) as mock_resolver_fn,
            patch(
                "src.tools.external.chart_maker.service.register_tool_output",
                mock_register,
            ),
            patch("src.files.desc_writer.write_desc") as mock_write_desc,
        ):
            self._setup_resolver(mock_resolver_fn, export_dir)

            result = await run_chart_maker(
                engine="mermaid",
                code="graph TD\nA-->B",
                filename="test_flow",
                title="流程图",
                user_id="test_user",
                thread_id="test_thread",
            )

        assert result["success"] is True
        assert result["engine"] == "mermaid"

        # 验证 render_chart 调用参数 (engine 透传, width/height 为 None)
        call_kwargs = mock_renderer.render_chart.call_args.kwargs
        assert call_kwargs["engine"] == "mermaid"
        assert call_kwargs["scale"] == 3

        # 验证 spec 写入 .desc.md (write_desc(user_id, file_id, spec_json))
        mock_write_desc.assert_called_once()
        assert mock_write_desc.call_args.args[0] == "test_user"
        assert mock_write_desc.call_args.args[1] == "test"
        spec_data = json.loads(mock_write_desc.call_args.args[2])
        assert spec_data["engine"] == "mermaid"
        assert spec_data["code"] == "graph TD\nA-->B"
        assert spec_data["title"] == "流程图"

    @pytest.mark.asyncio
    async def test_should_store_spec_in_desc_for_vega_lite(
        self, mock_context, tmp_path, mock_renderer
    ):
        """vega_lite 渲染应将原始输入 spec 写入 .desc.md."""
        from src.tools.external.chart_maker.service import run_chart_maker

        export_dir = tmp_path / "exports" / "charts"
        export_dir.mkdir(parents=True)

        spec_json = '{"mark":"bar","data":{"values":[{"a":"A","b":28}]}}'

        with (
            patch(
                "src.tools.external.chart_maker.service.get_browser_renderer",
                return_value=mock_renderer,
            ),
            patch(
                "src.core.path_resolver.get_user_path_resolver",
            ) as mock_resolver_fn,
            patch(
                "src.tools.external.chart_maker.service.register_tool_output",
                new_callable=AsyncMock,
                return_value={
                    "success": True,
                    "file_id": "test",
                    "file_url": "https://example.com/chart.png",
                    "filename": "chart.png",
                    "format": "png",
                    "size_bytes": 13,
                },
            ),
            patch("src.files.desc_writer.write_desc") as mock_write_desc,
        ):
            self._setup_resolver(mock_resolver_fn, export_dir)

            result = await run_chart_maker(
                engine="vega_lite",
                code=spec_json,
                filename="bar_chart",
                title=None,
                user_id="test_user",
                thread_id="test_thread",
            )

        assert result["success"] is True
        assert result["engine"] == "vega_lite"

        # 验证 spec 写入 .desc.md
        mock_write_desc.assert_called_once()
        spec_data = json.loads(mock_write_desc.call_args.args[2])
        assert spec_data["engine"] == "vega_lite"
        assert spec_data["width"] is None
        assert spec_data["height"] is None
        assert spec_data["scale"] == 3
        assert json.loads(spec_data["code"])["mark"] == "bar"

    @pytest.mark.asyncio
    async def test_markmap_should_pass_dimensions_and_scale(
        self, mock_context, tmp_path, mock_renderer
    ):
        """markmap 应透传 width/height/scale 到 render_chart, 并存储 Markdown 原始输入."""
        from src.tools.external.chart_maker.service import run_chart_maker

        export_dir = tmp_path / "exports" / "charts"
        export_dir.mkdir(parents=True)

        markdown = "# 项目计划\n## 目标\n- 交付MVP"

        with (
            patch(
                "src.tools.external.chart_maker.service.get_browser_renderer",
                return_value=mock_renderer,
            ),
            patch(
                "src.core.path_resolver.get_user_path_resolver",
            ) as mock_resolver_fn,
            patch(
                "src.tools.external.chart_maker.service.register_tool_output",
                new_callable=AsyncMock,
                return_value={
                    "success": True,
                    "file_id": "test",
                    "file_url": "https://example.com/mindmap.png",
                    "filename": "mindmap.png",
                    "format": "png",
                    "size_bytes": 13,
                },
            ),
            patch("src.files.desc_writer.write_desc") as mock_write_desc,
        ):
            self._setup_resolver(mock_resolver_fn, export_dir)

            result = await run_chart_maker(
                engine="markmap",
                code=markdown,
                filename="project_mindmap",
                title="项目计划",
                user_id="test_user",
                thread_id="test_thread",
                width=1600,
                height=1000,
                scale=4,
            )

        assert result["success"] is True
        assert result["engine"] == "markmap"
        call_kwargs = mock_renderer.render_chart.call_args.kwargs
        assert call_kwargs["engine"] == "markmap"
        assert call_kwargs["width"] == 1600
        assert call_kwargs["height"] == 1000
        assert call_kwargs["scale"] == 4
        assert call_kwargs["title"] == "项目计划"

        # 验证 spec 写入 .desc.md
        mock_write_desc.assert_called_once()
        spec_data = json.loads(mock_write_desc.call_args.args[2])
        assert spec_data["engine"] == "markmap"
        assert spec_data["code"] == markdown
        assert spec_data["title"] == "项目计划"
        assert spec_data["width"] == 1600
        assert spec_data["height"] == 1000
        assert spec_data["scale"] == 4

    @pytest.mark.asyncio
    async def test_vega_lite_should_pass_dimensions_and_scale(
        self, mock_context, tmp_path, mock_renderer
    ):
        """vega_lite 应透传 width/height/scale 到 render_chart."""
        from src.tools.external.chart_maker.service import run_chart_maker

        export_dir = tmp_path / "exports" / "charts"
        export_dir.mkdir(parents=True)

        with (
            patch(
                "src.tools.external.chart_maker.service.get_browser_renderer",
                return_value=mock_renderer,
            ),
            patch(
                "src.core.path_resolver.get_user_path_resolver",
            ) as mock_resolver_fn,
            patch(
                "src.tools.external.chart_maker.service.register_tool_output",
                new_callable=AsyncMock,
                return_value={
                    "success": True,
                    "file_id": "test",
                    "file_url": "https://example.com/chart.png",
                    "filename": "chart.png",
                    "format": "png",
                    "size_bytes": 13,
                },
            ),
        ):
            self._setup_resolver(mock_resolver_fn, export_dir)

            result = await run_chart_maker(
                engine="vega_lite",
                code='{"mark":"bar"}',
                filename="bar_chart",
                title=None,
                user_id="test_user",
                thread_id="test_thread",
                width=800,
                height=600,
                scale=3,
            )

        assert result["success"] is True
        call_kwargs = mock_renderer.render_chart.call_args.kwargs
        assert call_kwargs["engine"] == "vega_lite"
        assert call_kwargs["width"] == 800
        assert call_kwargs["height"] == 600
        assert call_kwargs["scale"] == 3

    @pytest.mark.asyncio
    async def test_mermaid_should_use_default_scale(
        self, mock_context, tmp_path, mock_renderer
    ):
        """mermaid 应透传默认 scale=3 到 render_chart."""
        from src.tools.external.chart_maker.service import run_chart_maker

        export_dir = tmp_path / "exports" / "charts"
        export_dir.mkdir(parents=True)

        with (
            patch(
                "src.tools.external.chart_maker.service.get_browser_renderer",
                return_value=mock_renderer,
            ),
            patch(
                "src.core.path_resolver.get_user_path_resolver",
            ) as mock_resolver_fn,
            patch(
                "src.tools.external.chart_maker.service.register_tool_output",
                new_callable=AsyncMock,
                return_value={
                    "success": True,
                    "file_id": "test",
                    "file_url": "https://example.com/test.png",
                    "filename": "test.png",
                    "format": "png",
                    "size_bytes": 13,
                },
            ),
        ):
            self._setup_resolver(mock_resolver_fn, export_dir)

            result = await run_chart_maker(
                engine="mermaid",
                code="graph TD\nA-->B",
                filename="test_flow",
                title=None,
                user_id="test_user",
                thread_id="test_thread",
            )

        assert result["success"] is True
        call_kwargs = mock_renderer.render_chart.call_args.kwargs
        assert call_kwargs["engine"] == "mermaid"
        assert call_kwargs["scale"] == 3
