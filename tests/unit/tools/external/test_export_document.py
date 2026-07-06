"""ExportDocumentTool 外部工具测试.

测试范围:
1. 模板加载 - load_template_config
2. CSS 生成 - generate_css / build_html_document
3. 工具属性 - name, summary, args_schema
4. 服务层 - _validate_filename, _build_unique_filename
5. GFM 解析器 - parse_gfm_structure, format_toc
"""

from __future__ import annotations

import re

import pytest

from src.tools.external.export_document.css_generator import (
    build_html_document,
    generate_css,
)
from src.tools.external.export_document.gfm_parser import (
    parse_gfm_structure,
)
from src.tools.external.export_document.service import (
    _build_unique_filename,
    _validate_filename,
    load_template_config,
)
from src.tools.external.export_document.tool import (
    ExportDocumentInput,
    ExportDocumentTool,
    _resolve_filename,
)

# =============================================================================
# 1. 模板加载测试
# =============================================================================


class TestLoadTemplateConfig:
    """测试 load_template_config."""

    def test_should_load_default_template(self):
        config = load_template_config("default")
        assert "fonts" in config
        assert "colors" in config
        assert "spacing" in config

    def test_should_load_all_builtin_templates(self):
        for name in ["default", "academic", "business", "technical"]:
            config = load_template_config(name)
            assert isinstance(config, dict)
            assert len(config) > 0, f"模板 {name} 应该非空"

    def test_should_fallback_to_default_for_unknown(self):
        config = load_template_config("nonexistent_style")
        assert "fonts" in config

    def test_default_template_should_have_required_fields(self):
        config = load_template_config("default")
        assert "fonts" in config
        assert "font_sizes" in config
        assert "colors" in config
        assert "spacing" in config
        assert "margins" in config
        assert "headings" in config
        assert "tables" in config
        assert "code_blocks" in config
        assert "blockquotes" in config


# =============================================================================
# 2. CSS 生成测试
# =============================================================================


class TestGenerateCss:
    """测试 generate_css."""

    def test_should_generate_non_empty_css(self):
        config = load_template_config("default")
        css = generate_css(config)
        assert len(css) > 100

    def test_should_contain_core_selectors(self):
        config = load_template_config("default")
        css = generate_css(config)
        for selector in [
            "body",
            "h1",
            "h2",
            "h3",
            "p",
            "table",
            "th",
            "td",
            "pre",
            "code",
            "blockquote",
            "hr",
            "img",
        ]:
            assert selector in css, f"CSS 应包含 {selector} 选择器"

    def test_should_contain_page_break_rules(self):
        config = load_template_config("default")
        css = generate_css(config)
        assert "page-break" in css

    def test_should_use_template_colors(self):
        config = load_template_config("default")
        text_color = config["colors"]["text"]
        css = generate_css(config)
        assert text_color in css

    def test_should_handle_empty_config(self):
        css = generate_css({})
        assert len(css) > 0
        assert "body" in css

    def test_different_templates_should_produce_different_css(self):
        default_css = generate_css(load_template_config("default"))
        academic_css = generate_css(load_template_config("academic"))
        business_css = generate_css(load_template_config("business"))
        assert default_css != academic_css
        assert default_css != business_css


class TestBuildHtmlDocument:
    """测试 build_html_document."""

    def test_should_produce_valid_html(self):
        html = build_html_document("<h1>Test</h1>", "h1 { color: red; }")
        assert html.startswith("<!doctype html>")
        assert "<html>" in html
        assert "</html>" in html
        assert "<h1>Test</h1>" in html
        assert "h1 { color: red; }" in html

    def test_should_include_csp_header(self):
        html = build_html_document("<p>Hello</p>", "")
        assert "Content-Security-Policy" in html

    def test_should_include_charset(self):
        html = build_html_document("<p>Hello</p>", "")
        assert "utf-8" in html


# =============================================================================
# 3. 工具属性测试
# =============================================================================


class TestExportDocumentToolAttributes:
    """测试 ExportDocumentTool 属性."""

    @pytest.fixture()
    def tool(self):
        return ExportDocumentTool()

    def test_should_have_args_schema(self, tool):
        assert tool.args_schema is not None
        fields = tool.args_schema.model_fields
        assert "content" in fields
        assert "style" in fields
        assert "format" in fields
        assert "filename" in fields

    def test_input_model_should_normalize_format_case_insensitive(self):
        """format字段应兼容大小写变体, 自动归一化为小写."""
        for upper, lower in [("PDF", "pdf"), ("Docx", "docx")]:
            inp = ExportDocumentInput(content="# Test", format=upper, filename="test")
            assert inp.format == lower

    def test_input_model_should_reject_invalid_format(self):
        with pytest.raises(Exception):
            ExportDocumentInput(content="# Test", format="txt", filename="test")

    def test_input_model_should_support_query_alias(self):
        inp = ExportDocumentInput(query="# Test", filename="test")
        assert inp.content == "# Test"

    def test_input_model_should_support_file_name_alias(self):
        """file_name (下划线变体) 应作为 filename 的别名重映射, 容忍 LLM 误传."""
        inp = ExportDocumentInput(content="# Test", file_name="report")
        assert inp.filename == "report"

    def test_input_model_should_prefer_filename_over_file_name_alias(self):
        """同时传 filename 与别名 file_name 时, 保留 filename 丢弃别名."""
        inp = ExportDocumentInput(
            content="# Test", filename="actual", file_name="ignored"
        )
        assert inp.filename == "actual"

    def test_filename_should_be_optional(self):
        """filename 改为可选: 不传也可构造, 默认 None."""
        inp = ExportDocumentInput(content="# Test")
        assert inp.filename is None

    def test_schema_should_only_require_content(self):
        """schema required 只剩 content, filename 移出必填."""
        schema = ExportDocumentInput.model_json_schema()
        assert schema["required"] == ["content"]

    def test_filename_should_be_absent_when_omitted(self):
        """filename 留空时序列化/属性为 None, 不出现在 model_dump."""
        inp = ExportDocumentInput(content="# Test")
        dumped = inp.model_dump()
        assert dumped.get("filename") is None


# =============================================================================
# 3.5 filename 自动生成测试
# =============================================================================


class TestResolveFilename:
    """测试 _resolve_filename 自动生成逻辑."""

    def test_autogen_from_h1_when_filename_missing(self):
        assert _resolve_filename("# 月度报告\n正文", None) == "月度报告"

    def test_autogen_sanitizes_spaces_in_title(self):
        # service._validate_filename 拒绝空格, 故生成名须清洗为下划线
        result = _resolve_filename("# Tech Report 2026\n正文", None)
        assert result == "Tech_Report_2026"
        # 反向验证: 生成名能通过 service 校验
        _validate_filename(result)

    def test_autogen_preserves_chinese(self):
        # \w 在 Unicode 模式下含中文, 无需替换
        result = _resolve_filename("# 技术方案 v2\n正文", None)
        assert result == "技术方案_v2"

    def test_autogen_falls_back_when_no_heading(self):
        assert _resolve_filename("纯正文无标题", None) == "document"

    def test_autogen_falls_back_when_empty_content(self):
        assert _resolve_filename("", None) == "document"

    def test_whitespace_filename_triggers_autogen(self):
        # 仅空白的 filename 视为未提供, 走自动生成
        assert _resolve_filename("# 标题", "   ") == "标题"

    def test_explicit_filename_returned_as_is(self):
        # 显式提供的 filename 原样返回 (交由 service 校验合法性)
        assert _resolve_filename("# 其他", "my_report") == "my_report"

    def test_explicit_filename_only_stripped_not_sanitized(self):
        # 显式 filename 仅去首尾空白, 内部空格不清洗 (保持原行为, 由 service 决定)
        assert _resolve_filename("# x", "  my_report  ") == "my_report"

    def test_autogen_truncates_long_title(self):
        long_title = "标" * 100
        result = _resolve_filename(f"# {long_title}\n正文", None)
        assert len(result) <= 50
        _validate_filename(result)

    def test_autogen_skips_h2_uses_first_h1(self):
        # 只识别 H1 (# ), 不把 H2 (## ) 当文件名来源
        assert _resolve_filename("## 二级标题\n# 一级标题\n正文", None) == "一级标题"

    def test_autogen_result_passes_service_validation(self):
        for content in ["# 报告", "# Q3 总结\n## 细节", "# A/B 测试结果"]:
            result = _resolve_filename(content, None)
            _validate_filename(result)  # 不抛异常即通过


class TestArunFilenameAutogen:
    """测试 _arun 在 filename=None 时自动生成并传入 service."""

    @pytest.mark.asyncio
    async def test_arun_autogens_filename_from_content_title(self):
        from unittest.mock import MagicMock, patch

        tool = ExportDocumentTool()
        captured: dict = {}

        async def fake_run(**kwargs):
            captured.update(kwargs)
            return {"success": True, "filename": "月度报告.pdf"}

        mock_ctx = MagicMock()
        mock_ctx.user_id = "u"
        mock_ctx.thread_id = "t"

        with (
            patch(
                "src.tools.external.export_document.service.run_export_document",
                new=fake_run,
            ),
            patch(
                "src.core.context.get_user_context",
                return_value=mock_ctx,
            ),
        ):
            result = await tool._arun(content="# 月度报告\n正文", filename=None)

        # filename 已从内容标题自动生成, 传给 service 的是非空合法名
        assert captured["filename"] == "月度报告"
        assert "月度报告.pdf" in result  # service 返回的 filename 回显
        assert '"success"' in result


# =============================================================================
# 4. 服务层工具函数测试
# =============================================================================


class TestValidateFilename:
    """测试 _validate_filename."""

    def test_should_accept_valid_filename(self):
        _validate_filename("monthly_report")

    def test_should_accept_alphanumeric(self):
        _validate_filename("report-2024_v2")

    def test_should_reject_empty(self):
        with pytest.raises(ValueError, match="不能为空"):
            _validate_filename("")

    def test_should_reject_spaces(self):
        with pytest.raises(ValueError, match="非法字符"):
            _validate_filename("my report")

    def test_should_reject_too_long(self):
        with pytest.raises(ValueError, match="过长"):
            _validate_filename("a" * 201)


class TestBuildUniqueFilename:
    """测试 _build_unique_filename."""

    def test_display_should_have_correct_extension(self):
        _, display = _build_unique_filename("report", "pdf")
        assert display == "report.pdf"

    def test_display_should_have_docx_extension(self):
        _, display = _build_unique_filename("report", "docx")
        assert display == "report.docx"

    def test_unique_should_include_timestamp(self):
        unique, _ = _build_unique_filename("report", "pdf")
        # 格式: report_YYYYMMDD_HHMMSS_xxxxxxxx.pdf
        assert re.match(r"report_\d{8}_\d{6}_[0-9a-f]{8}\.pdf", unique)


# =============================================================================
# 5. GFM 解析器测试
# =============================================================================


class TestParseGfmStructure:
    """测试 parse_gfm_structure."""

    def test_should_extract_toc_from_headings(self):
        gfm = "# 标题一\n## 标题二\n### 标题三\n正文"
        structure = parse_gfm_structure(gfm, "pdf")
        assert len(structure.toc) == 3
        assert structure.toc[0].level == 1
        assert structure.toc[0].title == "标题一"
        assert structure.toc[0].line == 0
        assert structure.toc[1].level == 2
        assert structure.toc[2].level == 3

    def test_empty_content(self):
        structure = parse_gfm_structure("", "pdf")
        assert structure.toc == []

    def test_format_stored(self):
        structure = parse_gfm_structure("# 测试", "pdf")
        assert structure.format == "pdf"

    def test_to_json_dict(self):
        gfm = "# 标题\n正文"
        structure = parse_gfm_structure(gfm, "pdf")
        d = structure.to_json_dict()
        assert "summary" in d
        assert "toc" in d
        assert "format" in d
        assert len(d["toc"]) == 1
        assert d["toc"][0]["level"] == 1
