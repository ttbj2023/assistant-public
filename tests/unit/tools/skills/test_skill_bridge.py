"""skill_bridge 单元测试.

覆盖范围:
- L1清单生成(有skill/无skill/部分匹配)
- L2正文返回(存在/不存在)
- L3引用文档读取(存在/不存在/路径遍历防护)
- 关联工具名查询(associated_tools)
- disabled skill不加载
"""

from __future__ import annotations

from pathlib import Path

from src.config.tools_config import SkillConfig
from src.tools.skills.skill_bridge import SkillBridge


def _make_skill_config(
    name: str,
    source: str,
    backend: str = "prompt_only",
    enabled: bool = True,
    associated_tools: list[str] | None = None,
) -> SkillConfig:
    return SkillConfig(
        name=name,
        source=source,
        backend=backend,
        enabled=enabled,
        associated_tools=associated_tools or [],
    )


def _write_skill(
    tmp_path: Path,
    name: str,
    description: str,
    body: str,
    references: dict[str, str] | None = None,
) -> Path:
    """创建临时skill目录, 可选带references/, 返回skill目录路径."""
    skill_dir = tmp_path / name
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n{body}",
        encoding="utf-8",
    )
    if references:
        refs_dir = skill_dir / "references"
        refs_dir.mkdir()
        for ref_name, ref_content in references.items():
            (refs_dir / f"{ref_name}.md").write_text(ref_content, encoding="utf-8")
    return skill_dir


class TestL1Manifest:
    def test_should_generate_manifest_for_available_skill(self, tmp_path: Path) -> None:
        skill_dir = _write_skill(tmp_path, "xlsx", "Excel生成", "正文")
        bridge = SkillBridge({
            "xlsx": _make_skill_config("xlsx", str(skill_dir), "executable")
        })
        manifest = bridge.get_l1_manifest(["xlsx"])
        assert "xlsx" in manifest
        assert "Excel生成" in manifest
        assert "load_skill" in manifest

    def test_should_return_empty_when_no_matching_skill(self) -> None:
        bridge = SkillBridge({})
        assert bridge.get_l1_manifest(["xlsx"]) == ""

    def test_should_only_include_requested_skills(self, tmp_path: Path) -> None:
        d1 = _write_skill(tmp_path, "xlsx", "Excel", "b1")
        d2 = _write_skill(tmp_path, "docx", "Word", "b2")
        bridge = SkillBridge({
            "xlsx": _make_skill_config("xlsx", str(d1), "executable"),
            "docx": _make_skill_config("docx", str(d2), "executable"),
        })
        manifest = bridge.get_l1_manifest(["xlsx"])
        assert "xlsx" in manifest
        assert "docx" not in manifest


class TestL2Body:
    def test_should_return_l2_body(self, tmp_path: Path) -> None:
        skill_dir = _write_skill(tmp_path, "xlsx", "Excel", "完整正文")
        bridge = SkillBridge({"xlsx": _make_skill_config("xlsx", str(skill_dir))})
        assert bridge.get_skill_l2("xlsx") == "完整正文"

    def test_should_return_none_for_unknown_skill(self) -> None:
        bridge = SkillBridge({})
        assert bridge.get_skill_l2("nope") is None


class TestReferences:
    def test_should_return_reference_content(self, tmp_path: Path) -> None:
        skill_dir = _write_skill(
            tmp_path, "chart", "图表", "正文",
            references={"mermaid": "# Mermaid\n语法"},
        )
        bridge = SkillBridge({"chart": _make_skill_config("chart", str(skill_dir))})
        content = bridge.get_skill_reference("chart", "mermaid")
        assert content is not None
        assert "Mermaid" in content

    def test_should_return_none_for_unknown_reference(self, tmp_path: Path) -> None:
        skill_dir = _write_skill(
            tmp_path, "chart", "图表", "正文",
            references={"mermaid": "# Mermaid"},
        )
        bridge = SkillBridge({"chart": _make_skill_config("chart", str(skill_dir))})
        assert bridge.get_skill_reference("chart", "nope") is None

    def test_should_return_none_for_unknown_skill(self) -> None:
        bridge = SkillBridge({})
        assert bridge.get_skill_reference("nope", "mermaid") is None

    def test_should_reject_path_traversal_reference(self, tmp_path: Path) -> None:
        """reference含路径分隔符/特殊字符时拒绝(路径遍历防护)."""
        skill_dir = _write_skill(tmp_path, "chart", "图表", "正文")
        bridge = SkillBridge({"chart": _make_skill_config("chart", str(skill_dir))})
        assert bridge.get_skill_reference("chart", "../../../etc/passwd") is None
        assert bridge.get_skill_reference("chart", "mermaid/../../../etc") is None

    def test_get_reference_names(self, tmp_path: Path) -> None:
        skill_dir = _write_skill(
            tmp_path, "chart", "图表", "正文",
            references={"mermaid": "# M", "vega_lite": "# V"},
        )
        bridge = SkillBridge({"chart": _make_skill_config("chart", str(skill_dir))})
        assert bridge.get_reference_names("chart") == ["mermaid", "vega_lite"]

    def test_get_reference_names_empty_for_skill_without_refs(
        self, tmp_path: Path
    ) -> None:
        skill_dir = _write_skill(tmp_path, "xlsx", "Excel", "正文")
        bridge = SkillBridge({"xlsx": _make_skill_config("xlsx", str(skill_dir))})
        assert bridge.get_reference_names("xlsx") == []


class TestAssociatedToolNames:
    def test_should_return_associated_tools(self, tmp_path: Path) -> None:
        skill_dir = _write_skill(tmp_path, "chart", "图表", "正文")
        bridge = SkillBridge({
            "chart": _make_skill_config(
                "chart", str(skill_dir),
                associated_tools=["mermaid_chart", "vega_chart"],
            )
        })
        result = bridge.get_associated_tool_names(["chart"])
        assert result == {"chart": ["mermaid_chart", "vega_chart"]}

    def test_should_skip_skills_without_associated_tools(
        self, tmp_path: Path
    ) -> None:
        d1 = _write_skill(tmp_path, "chart", "图表", "正文")
        d2 = _write_skill(tmp_path, "plain", "纯知识", "正文")
        bridge = SkillBridge({
            "chart": _make_skill_config(
                "chart", str(d1), associated_tools=["mermaid_chart"],
            ),
            "plain": _make_skill_config("plain", str(d2)),
        })
        result = bridge.get_associated_tool_names(["chart", "plain"])
        assert result == {"chart": ["mermaid_chart"]}

    def test_should_return_empty_when_no_associated_tools(self, tmp_path: Path) -> None:
        skill_dir = _write_skill(tmp_path, "plain", "纯知识", "正文")
        bridge = SkillBridge({"plain": _make_skill_config("plain", str(skill_dir))})
        assert bridge.get_associated_tool_names(["plain"]) == {}


class TestDisabledSkill:
    def test_disabled_skill_not_loaded(self, tmp_path: Path) -> None:
        d = _write_skill(tmp_path, "xlsx", "Excel", "b")
        bridge = SkillBridge({
            "xlsx": _make_skill_config("xlsx", str(d), enabled=False)
        })
        assert bridge.get_skill_l2("xlsx") is None
        assert bridge.get_l1_manifest(["xlsx"]) == ""
