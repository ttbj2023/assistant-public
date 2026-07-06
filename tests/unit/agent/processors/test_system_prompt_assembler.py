"""系统提示词装配器单元测试.

覆盖:
- 按声明顺序拼接 (base -> tools -> skills -> memory)
- 空白/缺失段跳过
- 空 sections 返回空串
- 未登记段被忽略
- 段内容首尾空白剥离
"""

from __future__ import annotations

from src.agent.processors.system_prompt_assembler import (
    SYSTEM_PROMPT_SECTION_ORDER,
    assemble_system_prompt,
)


def test_should_assemble_all_sections_in_declared_order() -> None:
    """所有段齐全时按声明顺序拼接, 段间空行分隔."""
    sections = {
        "base": "你是助手",
        "tools": "## 工具使用策略\n\n- t1: 提示",
        "skills": "技能段",
        "memory": "<pinned>记忆</pinned>",
    }

    result = assemble_system_prompt(sections)

    assert result == (
        "你是助手\n\n## 工具使用策略\n\n- t1: 提示\n\n技能段\n\n<pinned>记忆</pinned>"
    )


def test_should_skip_missing_or_blank_sections() -> None:
    """缺失段与纯空白段跳过, 不产生多余空行."""
    sections = {
        "base": "你是助手",
        "tools": "   \n  ",  # 纯空白
        "memory": "<pinned>记忆</pinned>",
        # skills 缺失
    }

    result = assemble_system_prompt(sections)

    assert result == "你是助手\n\n<pinned>记忆</pinned>"


def test_should_return_empty_string_when_all_blank() -> None:
    """全部为空时返回空串."""
    assert assemble_system_prompt({}) == ""
    assert assemble_system_prompt({"base": "", "memory": "  "}) == ""


def test_should_ignore_sections_not_in_order_table() -> None:
    """未登记的段名被忽略, 保证顺序表为唯一权威."""
    sections = {
        "base": "你是助手",
        "unknown": "不应出现",
        "custom_section": "也不应出现",
    }

    result = assemble_system_prompt(sections)

    assert result == "你是助手"
    assert "不应出现" not in result


def test_should_strip_section_whitespace() -> None:
    """段内容首尾空白被剥离."""
    sections = {
        "base": "  你是助手  \n",
        "memory": "\n  记忆内容  ",
    }

    result = assemble_system_prompt(sections)

    assert result == "你是助手\n\n记忆内容"


def test_order_table_is_base_tools_skills_memory() -> None:
    """顺序表锁定为 base -> tools -> skills -> memory."""
    assert SYSTEM_PROMPT_SECTION_ORDER == ("base", "tools", "skills", "memory")


def test_should_handle_base_only() -> None:
    """仅有 base 时直接返回 base (向后兼容流式测试的裸 system_prompt 场景)."""
    assert assemble_system_prompt({"base": "You are helpful"}) == "You are helpful"
