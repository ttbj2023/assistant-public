"""Skill桥接器 - 与McpBridge平级的外部能力源桥接.

懒加载解析所有启用skill的SKILL.md, 贡献:
- L1清单(系统提示词skills段, 构建期注入)
- L2正文(load_skill无reference参数时返回, 渐进式披露)
- L3引用(load_skill传reference参数时返回, 按需加载单引擎/子主题详细知识)
- 关联工具名(config.associated_tools, InferenceCoordinator创建实例并经SkillLoadMiddleware注入)

三级渐进式披露(对齐Anthropic Agent Skills):
- L1: frontmatter name+description → 始终在系统提示词(轻量, ~100 tokens/skill)
- L2: SKILL.md正文 → load_skill触发时返回(概览+选型+引用索引, <5k tokens)
- L3: references/xxx.md → load_skill(reference="xxx")按需返回(单引擎/子主题完整知识)

与McpBridge区别: MCP是被动协议调用(异步网络), skill是本地知识注入(同步解析).
独立单例(与ToolsManager平级, get_skill_bridge()), 保持skill与工具概念平级.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.config.tools_config import SkillConfig
from src.tools.skills.skill_parser import parse_skill

logger = logging.getLogger(__name__)

# L3引用文件名安全校验: 仅允许字母/数字/下划线/连字符, 防止路径遍历
_REFERENCE_NAME_RE = re.compile(r"^[a-zA-Z0-9_-]+$")


@dataclass
class SkillRecord:
    """已加载的skill记录.

    Attributes:
        name: skill唯一标识
        description: L1触发条件描述(skills段清单)
        body: L2完整领域知识正文(load_skill无reference时返回)
        backend: 执行后端(prompt_only | executable), 仅元信息
        references: 可用的L3引用文档名列表(references/目录扫描结果)
        source_path: SKILL.md所在目录路径, 供L3引用按需读取

    """

    name: str
    description: str
    body: str
    backend: str
    references: list[str]
    source_path: Path


class SkillBridge:
    """Skill桥接器 - 懒加载解析SKILL.md, 管理skill池.

    独立单例(与McpBridge平级). 提供L1清单贡献,L2/L3正文返回和关联工具名查询.
    不创建工具实例(实例创建由InferenceCoordinator负责).
    """

    def __init__(self, skills_config: dict[str, SkillConfig]) -> None:
        self._skills_config = skills_config
        self._skills: dict[str, SkillRecord] = {}
        self._loaded = False

        enabled = [n for n, c in skills_config.items() if c.enabled]
        logger.info(
            "SkillBridge初始化: %d个配置, %d个启用(%s)",
            len(skills_config),
            len(enabled),
            enabled,
        )

    def _ensure_loaded(self) -> None:
        """懒加载: 首次访问时同步解析所有启用skill的SKILL.md.

        SKILL.md为本地文件, 解析为同步IO, 无需异步. 单个skill加载失败不阻断其他.
        """
        if self._loaded:
            return

        for name, config in self._skills_config.items():
            if not config.enabled:
                continue
            try:
                skill_path = Path(config.source) / "SKILL.md"
                parsed = parse_skill(skill_path)
                self._skills[name] = SkillRecord(
                    name=parsed.name,
                    description=parsed.description,
                    body=parsed.body,
                    backend=config.backend,
                    references=parsed.references,
                    source_path=parsed.source_path,
                )
                logger.debug(
                    "skill加载: %s (backend=%s, references=%s)",
                    name,
                    config.backend,
                    parsed.references,
                )
            except Exception as e:
                logger.error("skill %s 加载失败: %s", name, e)

        self._loaded = True
        logger.info(
            "SkillBridge加载完成: %d个skill(%s)",
            len(self._skills),
            list(self._skills.keys()),
        )

    def get_l1_manifest(self, skill_names: list[str]) -> str:
        """生成skills段L1清单文本(skill名称+描述).

        skills段只承载L1清单, 保持轻量(不撑爆系统提示词, 稳定缓存前缀).
        L2正文走load_skill按需返回.

        Args:
            skill_names: 该agent启用的skill名列表

        Returns:
            skills段文本; 无可用skill时返回空串.

        """
        self._ensure_loaded()
        available = [self._skills[n] for n in skill_names if n in self._skills]
        if not available:
            return ""

        lines = ["## 可用技能(Skills)", ""]
        lines.append(
            "以下技能提供专项领域能力, 调用 `load_skill` 工具加载完整使用说明后使用:"
        )
        lines.append("")
        for skill in available:
            lines.append(f"- **{skill.name}**: {skill.description}")
        return "\n".join(lines)

    def get_skill_l2(self, skill_name: str) -> str | None:
        """返回指定skill的L2正文(load_skill无reference参数时调用).

        Args:
            skill_name: skill名

        Returns:
            L2完整正文; skill不存在时返回None.

        """
        self._ensure_loaded()
        skill = self._skills.get(skill_name)
        return skill.body if skill else None

    def get_skill_reference(self, skill_name: str, reference: str) -> str | None:
        """返回指定skill的L3引用文档内容(load_skill传reference参数时调用).

        路径遍历防护: reference必须匹配^[a-zA-Z0-9_-]+$且在references列表中.

        Args:
            skill_name: skill名
            reference: 引用文档名(无扩展名, 如 "mermaid")

        Returns:
            引用文档内容; skill/reference不存在时返回None.

        """
        self._ensure_loaded()
        skill = self._skills.get(skill_name)
        if not skill:
            return None
        if not _REFERENCE_NAME_RE.match(reference):
            logger.warning("skill引用名不合法(路径遍历防护): %s", reference)
            return None
        if reference not in skill.references:
            return None
        ref_path = skill.source_path / "references" / f"{reference}.md"
        if not ref_path.exists():
            return None
        return ref_path.read_text(encoding="utf-8")

    def get_reference_names(self, skill_name: str) -> list[str]:
        """返回指定skill的可用引用文档名列表(L3).

        Args:
            skill_name: skill名

        Returns:
            引用文档名列表; skill不存在时返回空列表.

        """
        self._ensure_loaded()
        skill = self._skills.get(skill_name)
        return list(skill.references) if skill else []

    def get_associated_tool_names(
        self,
        skill_names: list[str],
    ) -> dict[str, list[str]]:
        """返回per-skill关联工具名映射(从config.associated_tools直接读取).

        InferenceCoordinator据此创建工具实例, 构建SkillLoadMiddleware的per-skill工具映射.
        SkillBridge不创建工具实例, 仅提供配置查询.

        Args:
            skill_names: 该agent启用的skill名列表

        Returns:
            {skill_name: [tool_name, ...]}; 无关联工具的skill不包含在结果中.

        """
        self._ensure_loaded()
        result: dict[str, list[str]] = {}
        for name in skill_names:
            config = self._skills_config.get(name)
            if config and config.associated_tools:
                result[name] = list(config.associated_tools)
        return result

    def get_stats(self) -> dict[str, Any]:
        """获取skill统计信息."""
        return {
            "total_skills": len(self._skills),
            "loaded": self._loaded,
            "skills": list(self._skills.keys()),
        }


_skill_bridge: SkillBridge | None = None


def get_skill_bridge() -> SkillBridge:
    """获取全局SkillBridge单例(与get_tools_manager平级).

    skill为本地知识源, 无外部连接需关闭, 不注册生命周期回调.

    Returns:
        SkillBridge单例实例

    """
    global _skill_bridge
    if _skill_bridge is None:
        from src.config.tools_config import get_config

        _skill_bridge = SkillBridge(get_config().skills)
    return _skill_bridge


__all__ = ["SkillBridge", "SkillRecord", "get_skill_bridge"]
