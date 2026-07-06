"""置顶记忆审计分析器单测 - 覆盖 parse/build_prompt 纯逻辑."""

from __future__ import annotations

from src.core.types import MemoryOperation
from src.inference.content_analyzer.pinned_memory_audit_analyzer import (
    build_prompt,
    parse_operations,
)


def test_parse_operations_rejects_add() -> None:
    """add 操作必须被拒绝(审计无add, 新信息归1-step)."""
    number_map = {1: {"field": "preferences", "content": "偏好X"}}
    content = '{"operations":[{"action":"add","number":1,"content":"新"},{"action":"delete","number":1,"reason":"r"}]}'
    ops = parse_operations(content, number_map)
    assert len(ops) == 1
    assert ops[0].action == "delete"
    assert isinstance(ops[0], MemoryOperation)


def test_parse_operations_delete_and_change() -> None:
    """delete/change 正确解析为 MemoryOperation 对象, 按编号映射原文."""
    number_map = {
        1: {"field": "basic_info", "content": "A"},
        2: {"field": "preferences", "content": "B"},
    }
    content = (
        '{"operations":['
        '{"action":"delete","number":1,"reason":"噪音"},'
        '{"action":"change","number":2,"new_content":"B提炼","reason":"r"}'
        "]}"
    )
    ops = parse_operations(content, number_map)
    assert len(ops) == 2
    # 返回 MemoryOperation 对象(非裸 dict), 供 apply_operations 属性访问消费
    assert all(isinstance(op, MemoryOperation) for op in ops)
    assert ops[0].action == "delete"
    assert ops[0].field == "basic_info"
    assert ops[0].content == "A"
    assert ops[1].action == "change"
    assert ops[1].field == "preferences"
    assert ops[1].old_content == "B"
    assert ops[1].new_content == "B提炼"


def test_parse_operations_invalid_number_skipped() -> None:
    """无效编号跳过, 不报错."""
    number_map = {1: {"field": "basic_info", "content": "A"}}
    content = (
        '{"operations":['
        '{"action":"delete","number":99,"reason":"r"},'
        '{"action":"delete","number":1,"reason":"r"}'
        "]}"
    )
    ops = parse_operations(content, number_map)
    assert len(ops) == 1


def test_parse_operations_change_empty_new_skipped() -> None:
    """change 的 new_content 为空时跳过."""
    number_map = {1: {"field": "preferences", "content": "A"}}
    content = (
        '{"operations":[{"action":"change","number":1,"new_content":"","reason":"r"}]}'
    )
    ops = parse_operations(content, number_map)
    assert len(ops) == 0


def test_parse_operations_malformed_json_extracts() -> None:
    """JSON 被 markdown 包裹时仍能提取."""
    number_map = {1: {"field": "basic_info", "content": "A"}}
    content = (
        '```json\n{"operations":[{"action":"delete","number":1,"reason":"r"}]}\n```'
    )
    ops = parse_operations(content, number_map)
    assert len(ops) == 1


def test_build_prompt_with_index() -> None:
    """有索引时 prompt 含记忆块和索引."""
    prompt = build_prompt("[1] 测试", "R1: topic - summary")
    assert "[1] 测试" in prompt
    assert "R1: topic - summary" in prompt
    assert "近期对话索引" in prompt


def test_build_prompt_empty_index() -> None:
    """无索引时不报错, 仍含记忆块."""
    prompt = build_prompt("[1] 测试", "")
    assert "[1] 测试" in prompt
