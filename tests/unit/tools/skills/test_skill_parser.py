"""skill_parser 单元测试.

覆盖范围:
- 正常frontmatter解析(name/description/body)
- 无frontmatter降级
- 缺name降级为父目录名
- 异常YAML降级
- 多余字段忽略
- references/目录扫描(L3)
- source_path正确设置
"""

from __future__ import annotations

from pathlib import Path

from src.tools.skills.skill_parser import ParsedSkill, parse_skill


class TestParseSkill:
    def test_should_parse_normal_frontmatter(self, tmp_path: Path) -> None:
        skill_md = tmp_path / "SKILL.md"
        skill_md.write_text(
            "---\nname: xlsx\ndescription: Excel报表生成\n---\n# Excel 知识\n\n正文内容",
            encoding="utf-8",
        )
        result = parse_skill(skill_md)
        assert result.name == "xlsx"
        assert result.description == "Excel报表生成"
        assert result.body == "# Excel 知识\n\n正文内容"

    def test_should_fallback_when_no_frontmatter(self, tmp_path: Path) -> None:
        skill_md = tmp_path / "SKILL.md"
        skill_md.write_text("# 纯正文无frontmatter", encoding="utf-8")
        result = parse_skill(skill_md)
        # name降级为父目录名
        assert result.name == tmp_path.name
        assert result.description == ""
        assert "纯正文" in result.body

    def test_should_fallback_name_to_parent_dir_when_missing(
        self, tmp_path: Path
    ) -> None:
        skill_md = tmp_path / "SKILL.md"
        skill_md.write_text("---\ndescription: 无name字段\n---\n正文", encoding="utf-8")
        result = parse_skill(skill_md)
        assert result.name == tmp_path.name
        assert result.description == "无name字段"

    def test_should_fallback_on_invalid_yaml(self, tmp_path: Path) -> None:
        skill_md = tmp_path / "SKILL.md"
        # 未闭合的flow sequence触发YAMLError
        skill_md.write_text("---\nname: [unclosed\n---\n正文内容", encoding="utf-8")
        result = parse_skill(skill_md)
        assert result.name == tmp_path.name
        assert "正文内容" in result.body

    def test_should_ignore_extra_fields(self, tmp_path: Path) -> None:
        skill_md = tmp_path / "SKILL.md"
        skill_md.write_text(
            "---\nname: xlsx\ndescription: 描述\nlicense: MIT\nextra: value\n---\n正文",
            encoding="utf-8",
        )
        result = parse_skill(skill_md)
        assert result.name == "xlsx"
        assert result.description == "描述"
        assert result.body == "正文"

    def test_should_return_parsed_skill_instance(self, tmp_path: Path) -> None:
        skill_md = tmp_path / "SKILL.md"
        skill_md.write_text("---\nname: test\n---\nbody", encoding="utf-8")
        result = parse_skill(skill_md)
        assert isinstance(result, ParsedSkill)

    def test_empty_description_when_missing(self, tmp_path: Path) -> None:
        skill_md = tmp_path / "SKILL.md"
        skill_md.write_text("---\nname: test\n---\nbody", encoding="utf-8")
        result = parse_skill(skill_md)
        assert result.description == ""


class TestReferences:
    def test_should_scan_references_directory(self, tmp_path: Path) -> None:
        """有references/目录时, ParsedSkill.references包含.md文件名(无扩展名)."""
        skill_dir = tmp_path / "chart_maker"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(
            "---\nname: chart_maker\ndescription: 图表\n---\n正文",
            encoding="utf-8",
        )
        refs_dir = skill_dir / "references"
        refs_dir.mkdir()
        (refs_dir / "mermaid.md").write_text("# Mermaid", encoding="utf-8")
        (refs_dir / "vega_lite.md").write_text("# Vega-Lite", encoding="utf-8")
        (refs_dir / "readme.txt").write_text("not md", encoding="utf-8")

        result = parse_skill(skill_dir / "SKILL.md")
        assert result.references == ["mermaid", "vega_lite"]

    def test_should_return_empty_references_when_no_dir(self, tmp_path: Path) -> None:
        """无references/目录时, references为空列表."""
        skill_md = tmp_path / "SKILL.md"
        skill_md.write_text("---\nname: xlsx\n---\n正文", encoding="utf-8")
        result = parse_skill(skill_md)
        assert result.references == []

    def test_source_path_should_be_skill_directory(self, tmp_path: Path) -> None:
        """source_path应为SKILL.md所在目录."""
        skill_dir = tmp_path / "my_skill"
        skill_dir.mkdir()
        skill_md = skill_dir / "SKILL.md"
        skill_md.write_text("---\nname: test\n---\n正文", encoding="utf-8")
        result = parse_skill(skill_md)
        assert result.source_path == skill_dir
