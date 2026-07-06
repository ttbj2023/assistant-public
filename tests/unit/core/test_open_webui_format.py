"""Open WebUI HTML 格式化工具单元测试.

测试 src.core.open_webui_format 模块的所有功能.
"""

from __future__ import annotations

from src.core.open_webui_format import (
    format_tool_call_done,
)


class TestFormatToolCallDone:
    """format_tool_call_done 测试."""

    def test_basic_done(self):
        """测试基本 done 标签生成 - result 在 body 中."""
        result = format_tool_call_done("web_search", "搜索完成", {})

        assert 'type="tool_calls"' in result
        assert 'name="web_search"' in result
        assert 'done="true"' in result
        assert "<summary>Tool Executed</summary>" in result
        assert "搜索完成" in result

    def test_done_result_truncation(self):
        """测试结果超长截断."""
        long_result = "x" * 3000
        result = format_tool_call_done("tool", long_result, {})

        # 结果被截断到 MAX_RESULT_LENGTH, 在 result 属性和 body 中各出现一次
        x_count = result.count("x")
        assert 4000 <= x_count <= 4004

    def test_done_html_escaping_in_body(self):
        """测试结果中的 HTML 字符在 body 中被转义."""
        result = format_tool_call_done("tool", '<script>alert("xss")</script>', {})

        assert "&lt;script&gt;" in result
        assert "alert" in result

    def test_done_result_with_newlines_in_body(self):
        """测试 result 包含换行符时 body 模式正常工作."""
        result_text = "搜索结果:\n1. 小雨 25度\n2. 暴雨预警"
        html = format_tool_call_done("web_search", result_text, {"query": "test"})

        # 属性中不应有换行 (换行在 body 的 JSON 编码中)
        assert "<summary>" in html
        # body 中应包含结果 (json.dumps 将换行转为 \n)
        assert "小雨" in html
        assert "暴雨预警" in html

    def test_done_result_with_quotes_in_body(self):
        """测试 result 包含引号时 body 模式正常工作."""
        result_text = '搜索结果: "小雨" 25度'
        html = format_tool_call_done("web_search", result_text, {"query": "test"})

        # body 中的结果经过 json.dumps 处理引号
        assert "小雨" in html

    def test_done_with_none_arguments(self):
        """测试 None 参数默认为空 dict."""
        result = format_tool_call_done("tool", "ok")

        assert 'arguments="{}"' in result

    def test_done_body_content_extractable(self):
        """测试 body 内容可被 Open WebUI 的 getDetailTextContent 提取."""
        import re

        result_text = "搜索结果: 小雨 25度"
        html_tag = format_tool_call_done("web_search", result_text, {"query": "test"})

        # 模拟 Open WebUI 提取 body: 去掉 <summary> 后的内容
        body_match = re.search(
            r"<summary>.*?</summary>\s*(.*?)\s*</details>",
            html_tag,
            re.DOTALL,
        )
        assert body_match is not None
        body = body_match.group(1)
        # body 是 JSON 编码的字符串, decode 后应包含原始文本
        import json

        decoded = json.loads(body)
        assert "小雨" in decoded
