"""Skill模块 - 与工具/MCP平级的外部能力源.

提供社区技能包(对齐Anthropic Agent Skills规范)的解析,桥接和三级渐进式披露加载.
skill经审核适配后接入, 提供领域知识(L1/L2/L3) + 定向能力(关联工具注入).

核心组件:
- SkillBridge: skill桥接器, 懒加载解析SKILL.md, 贡献L1清单/L2正文/L3引用/关联工具名
- parse_skill: SKILL.md解析(frontmatter L1 + 正文 L2 + references/目录 L3)
- LoadSkillTool: 常驻工具, LLM调用激活skill返回L2总览或L3引用
- SkillLoadMiddleware: per-skill关联工具动态注入

三级渐进式披露(对齐Anthropic Agent Skills):
- L1: frontmatter name+description → 系统提示词skills段(构建期, 始终可见)
- L2: SKILL.md正文 → load_skill(skill_name)返回(概览+选型+引用索引)
- L3: references/xxx.md → load_skill(skill_name, reference="xxx")按需返回(单引擎/子主题详细知识)

关联工具: load_skill(L2)触发SkillLoadMiddleware注入该skill的associated_tools.

详见 docs/development/skills-integration.md
"""

from __future__ import annotations
