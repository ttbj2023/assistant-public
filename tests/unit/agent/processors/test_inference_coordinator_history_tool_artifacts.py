"""InferenceCoordinator 历史工具调用渲染标记清洗单元测试.

覆盖 _strip_tool_artifacts_in_history:
- <details type="tool_calls"> 标签剥离 (闭合/未闭合/属性值含 >)
- DeepSeek DSML 原生标记剥离
- AIMessage 清洗, HumanMessage 不受影响
- 纯文本历史零改动, list content 清洗
"""

from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from src.agent.processors.inference_coordinator import InferenceCoordinator


@pytest.fixture
def coordinator() -> InferenceCoordinator:
    return InferenceCoordinator({"llm": {"model": "test"}})


# ========== _strip_tool_artifacts_in_history ==========


class TestStripToolArtifacts:
    def test_strips_closed_details_tag(self, coordinator):
        """闭合的 <details type="tool_calls"> 标签被剥离, 保留文本前缀."""
        msg = AIMessage(
            content=(
                "回复\n\n"
                '<details type="tool_calls" name="x" done="true">\n'
                "<summary>Tool Executed</summary>\n"
                "result\n"
                "</details>"
            ),
        )
        result = coordinator._strip_tool_artifacts_in_history([msg])
        assert result[0].content == "回复"

    def test_strips_unclosed_details_with_gt_in_args_and_dsml(self, coordinator):
        """未闭合 details 标签 (arguments 内含未转义 >) + DSML 标记全量剥离."""
        msg = AIMessage(
            content=(
                "好的，推送到草稿箱。\n\n"
                '<details type="tool_calls" name="wechat_publish" '
                'arguments="{&quot;content&quot;: &quot;> 引用文本&quot;}" '
                'result="缺少 appId">'
                "</\uff5c\uff5cDSML\uff5c\uff5cinvoke>\n"
                "</\uff5c\uff5cDSML\uff5c\uff5ctool_calls>"
            ),
        )
        result = coordinator._strip_tool_artifacts_in_history([msg])
        assert result[0].content == "好的，推送到草稿箱。"

    def test_human_message_not_affected(self, coordinator):
        """HumanMessage 中的 <details> 标签不受影响."""
        msg = HumanMessage(content='<details type="tool_calls">不应被清洗</details>')
        result = coordinator._strip_tool_artifacts_in_history([msg])
        assert result[0].content == '<details type="tool_calls">不应被清洗</details>'

    def test_clean_history_zero_changes(self, coordinator):
        """无标签的纯文本历史零改动, 返回原对象."""
        msgs = [AIMessage(content="正常回复"), HumanMessage(content="用户消息")]
        result = coordinator._strip_tool_artifacts_in_history(msgs)
        assert result is msgs

    def test_list_content_text_block_cleaned(self, coordinator):
        """多模态 list content 中的 text block 被清洗."""
        msg = AIMessage(
            content=[
                {"type": "text", "text": "回复"},
                {
                    "type": "text",
                    "text": '<details type="tool_calls" done="true">'
                    "<summary>x</summary>r</details>",
                },
            ],
        )
        result = coordinator._strip_tool_artifacts_in_history([msg])
        assert isinstance(result[0].content, list)
        texts = [b["text"] for b in result[0].content if b.get("type") == "text"]
        assert all("<details" not in t for t in texts)
        assert "回复" in texts[0]

    def test_none_and_empty_return_as_is(self, coordinator):
        """None / 空列表原样返回."""
        assert coordinator._strip_tool_artifacts_in_history(None) is None
        assert coordinator._strip_tool_artifacts_in_history([]) == []
