"""load_skill_tool 单元测试.

覆盖范围:
- L2: _arun返回正文(skill_pool已配置且skill存在)
- L2: 未知skill返回错误
- L2: 无skill_pool返回错误
- L3: reference参数返回引用文档
- L3: 不存在的reference返回可用列表
- set_skill_pool注入
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.config.tools_config import SkillConfig
from src.tools.skills.load_skill_tool import LoadSkillTool
from src.tools.skills.skill_bridge import SkillBridge


def _make_bridge_with_skill(
    tmp_path: Path,
    name: str = "xlsx",
    references: dict[str, str] | None = None,
) -> SkillBridge:
    skill_dir = tmp_path / name
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: 描述\n---\nL2正文内容",
        encoding="utf-8",
    )
    if references:
        refs_dir = skill_dir / "references"
        refs_dir.mkdir()
        for ref_name, ref_content in references.items():
            (refs_dir / f"{ref_name}.md").write_text(ref_content, encoding="utf-8")
    return SkillBridge({
        name: SkillConfig(name=name, source=str(skill_dir), backend="executable")
    })


class TestLoadSkillL2:
    @pytest.mark.asyncio
    async def test_should_return_l2_body_for_valid_skill(self, tmp_path: Path) -> None:
        bridge = _make_bridge_with_skill(tmp_path)
        tool = LoadSkillTool("u1", "t1", agent_id="a1")
        tool.set_skill_pool(bridge, ["xlsx"])
        result = await tool._arun(skill_name="xlsx")
        assert result == "L2正文内容"

    @pytest.mark.asyncio
    async def test_should_return_error_for_unknown_skill(self) -> None:
        bridge = MagicMock()
        tool = LoadSkillTool("u1", "t1", agent_id="a1")
        tool.set_skill_pool(bridge, ["xlsx"])
        result = await tool._arun(skill_name="nope")
        data = json.loads(result)
        assert data["success"] is False
        assert "nope" in data["message"]
        assert data["available_skills"] == ["xlsx"]

    @pytest.mark.asyncio
    async def test_should_return_error_when_no_skill_pool(self) -> None:
        tool = LoadSkillTool("u1", "t1", agent_id="a1")
        # 未调set_skill_pool
        result = await tool._arun(skill_name="xlsx")
        data = json.loads(result)
        assert data["success"] is False
        assert "数据源" in data["message"]


class TestLoadSkillL3:
    @pytest.mark.asyncio
    async def test_should_return_reference_content(self, tmp_path: Path) -> None:
        bridge = _make_bridge_with_skill(
            tmp_path, "chart_maker",
            references={"mermaid": "# Mermaid 语法\n完整内容"},
        )
        tool = LoadSkillTool("u1", "t1", agent_id="a1")
        tool.set_skill_pool(bridge, ["chart_maker"])
        result = await tool._arun(skill_name="chart_maker", reference="mermaid")
        assert "Mermaid" in result
        assert "完整内容" in result

    @pytest.mark.asyncio
    async def test_should_return_error_for_invalid_reference(
        self, tmp_path: Path
    ) -> None:
        bridge = _make_bridge_with_skill(
            tmp_path, "chart_maker",
            references={"mermaid": "# M", "vega_lite": "# V"},
        )
        tool = LoadSkillTool("u1", "t1", agent_id="a1")
        tool.set_skill_pool(bridge, ["chart_maker"])
        result = await tool._arun(skill_name="chart_maker", reference="nope")
        data = json.loads(result)
        assert data["success"] is False
        assert "nope" in data["message"]
        assert sorted(data["available_references"]) == ["mermaid", "vega_lite"]

    @pytest.mark.asyncio
    async def test_l3_without_references_dir_returns_error(
        self, tmp_path: Path
    ) -> None:
        bridge = _make_bridge_with_skill(tmp_path, "xlsx")
        tool = LoadSkillTool("u1", "t1", agent_id="a1")
        tool.set_skill_pool(bridge, ["xlsx"])
        result = await tool._arun(skill_name="xlsx", reference="anything")
        data = json.loads(result)
        assert data["success"] is False
        assert data["available_references"] == []
