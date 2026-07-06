"""系统提示词装配器 - 按声明的顺序拼接各命名段.

各层只贡献命名段 (sections dict), 由本模块按唯一权威顺序拼成最终系统提示词.
新增段类型 (如 skills) 只需在顺序表登记 + 某处贡献对应段, 无需改动装配逻辑.
"""

from __future__ import annotations

# 唯一权威顺序: base(身份) -> tools(工具策略) -> skills(预留) -> memory(动态数据)
SYSTEM_PROMPT_SECTION_ORDER: tuple[str, ...] = (
    "base",
    "tools",
    "skills",
    "memory",
)


def assemble_system_prompt(sections: dict[str, str]) -> str:
    """按声明顺序拼接非空段, 段间以空行分隔.

    Args:
        sections: 段名 -> 内容. 未提供或空白的段跳过.

    Returns:
        拼接后的系统提示词; 全部为空时返回空串.

    """
    parts = [
        sections[name].strip()
        for name in SYSTEM_PROMPT_SECTION_ORDER
        if (sections.get(name) or "").strip()
    ]
    return "\n\n".join(parts)
