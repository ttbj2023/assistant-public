"""公开仓导出脱敏规则的单元测试.

聚焦 should_exclude: 决定哪些跟踪文件不进入公开仓快照. 历史上 skills
目录从 .claude/skills/ (符号链接, 已被 .claude/ 前缀排除) 迁移到真实目录
.agents/skills/ 后, 脱敏规则未同步, 导致 docker-deploy skill 文档 (含
内网部署细节) 进入 staging 触发敏感扫描中止同步.
"""

from __future__ import annotations

import pytest

from scripts.prepare_public_release import should_exclude


class TestShouldExclude:
    """should_exclude 行为: 私人路径排除, 项目文件保留."""

    @pytest.mark.parametrize(
        "rel_path",
        [
            "AGENTS.md",
            "CLAUDE.md",
        ],
    )
    def test_excludes_top_level_private_docs(self, rel_path: str) -> None:
        """顶层私人协作文档应排除."""
        assert should_exclude(rel_path) is True

    @pytest.mark.parametrize(
        "prefix",
        [
            ".claude/",
            ".claude/skills/docker-deploy",
            ".claude/agents/unit-test-analyzer.md",
            ".opencode/",
            ".opencode/config.json",
            ".github/",
            ".github/workflows/sync-public.yml",
        ],
    )
    def test_excludes_private_prefix_dirs(self, prefix: str) -> None:
        """私人/工具配置目录前缀应排除."""
        assert should_exclude(prefix) is True

    def test_excludes_agents_skills_dir(self) -> None:
        """.agents/skills/ 是真实 skill 文档目录 (含 docker-deploy 内网部署细节), 必须排除."""
        assert should_exclude(".agents/skills/docker-deploy/SKILL.md") is True
        assert should_exclude(".agents/skills/test-mocks/SKILL.md") is True
        assert should_exclude(".agents/") is True

    @pytest.mark.parametrize(
        "rel_path",
        [
            "README.md",
            "src/agent/factory.py",
            "scripts/prepare_public_release.py",
            "docs/changelog.md",
            "pyproject.toml",
            "config.yaml",
        ],
    )
    def test_keeps_project_files(self, rel_path: str) -> None:
        """项目正常文件不应排除."""
        assert should_exclude(rel_path) is False
