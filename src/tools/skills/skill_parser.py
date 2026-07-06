"""Skill解析器 - 解析 SKILL.md 的 frontmatter(L1), 正文(L2)和引用目录(L3).

对齐 Anthropic Agent Skills 规范, SKILL.md 极简:
YAML frontmatter(name/description) + markdown正文(领域知识) + references/(可选).
容错: 审核过的skill, 多余字段忽略, 格式异常降级.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

# frontmatter: 文件开头 --- ... --- 包裹YAML, 之后是markdown正文
_FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?(.*)\Z", re.DOTALL)


@dataclass
class ParsedSkill:
    """解析后的skill内容.

    Attributes:
        name: skill唯一标识(L1, 来自frontmatter)
        description: 触发条件描述(L1, 来自frontmatter)
        body: 完整领域知识正文(L2, markdown)
        references: 可用引用文档名列表(L3, references/目录下的.md文件名, 无扩展名)
        source_path: SKILL.md所在目录路径, 供L3引用文件按需读取

    """

    name: str
    description: str
    body: str
    references: list[str]
    source_path: Path


def parse_skill(skill_path: Path) -> ParsedSkill:
    """解析SKILL.md: frontmatter(name/description) + markdown正文(L2) + references/目录(L3).

    容错策略(审核过的skill, 不需鲁棒解析):
    - 无frontmatter: name取父目录名, body取全文
    - frontmatter缺name: 降级为父目录名
    - frontmatter格式异常: 降级, body保留
    - 多余字段忽略

    Args:
        skill_path: SKILL.md文件路径

    Returns:
        解析后的ParsedSkill

    Raises:
        FileNotFoundError: 文件不存在时由read_text抛出

    """
    text = skill_path.read_text(encoding="utf-8")
    source_path = skill_path.parent
    fallback_name = source_path.name
    references = _scan_references(source_path)

    match = _FRONTMATTER_RE.match(text)
    if not match:
        logger.debug("skill %s 无frontmatter, 降级全文为body", fallback_name)
        return ParsedSkill(
            name=fallback_name,
            description="",
            body=text.strip(),
            references=references,
            source_path=source_path,
        )

    frontmatter_text, body = match.groups()

    try:
        meta = yaml.safe_load(frontmatter_text) or {}
    except yaml.YAMLError as e:
        logger.warning("skill %s frontmatter解析失败, 降级: %s", fallback_name, e)
        return ParsedSkill(
            name=fallback_name,
            description="",
            body=body.strip(),
            references=references,
            source_path=source_path,
        )

    if not isinstance(meta, dict):
        logger.warning("skill %s frontmatter非字典, 降级", fallback_name)
        return ParsedSkill(
            name=fallback_name,
            description="",
            body=body.strip(),
            references=references,
            source_path=source_path,
        )

    name = str(meta.get("name", "")).strip() or fallback_name
    description = str(meta.get("description", "")).strip()

    return ParsedSkill(
        name=name,
        description=description,
        body=body.strip(),
        references=references,
        source_path=source_path,
    )


def _scan_references(skill_dir: Path) -> list[str]:
    """扫描 references/ 目录, 返回可用的引用文档名(.md文件名, 无扩展名).

    L3渐进式披露: SKILL.md正文引用这些文档, LLM按需通过
    load_skill(skill, reference="xxx")加载特定引用.

    Args:
        skill_dir: skill所在目录(SKILL.md的父目录)

    Returns:
        引用文档名列表(按字母排序); 无references/目录时返回空列表

    """
    refs_dir = skill_dir / "references"
    if not refs_dir.is_dir():
        return []
    return sorted(f.stem for f in refs_dir.iterdir() if f.suffix == ".md")


__all__ = ["ParsedSkill", "parse_skill"]
