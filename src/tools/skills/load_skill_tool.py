"""load_skill工具 - 常驻工具, LLM调用激活skill并返回领域知识(L2概览/L3引用).

作为渐进式披露的核心入口(三级模型, 对齐Anthropic Agent Skills):
- L1(构建期): skills段注入清单(skill名称+描述), 让LLM知道有哪些skill可用
- L2(skill触发): LLM调用本工具(不传reference)加载技能总览(概览+选型+引用索引)
- L3(按需): LLM确定具体子方向后调用本工具(传reference)加载详细引用文档
- 加载后: SkillLoadMiddleware检测到L2调用, 动态注入对应skill的关联工具

数据源由InferenceCoordinator通过set_skill_pool()注入
(SkillBridge单例 + 该agent的可用skill列表).
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any, override

from pydantic import BaseModel, ConfigDict, Field

from src.tools.shared.base_internal_tool import BaseInternalTool

if TYPE_CHECKING:
    from src.tools.skills.skill_bridge import SkillBridge

logger = logging.getLogger(__name__)


class LoadSkillRequest(BaseModel):
    """load_skill请求模型."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"additionalProperties": False},
    )

    skill_name: str = Field(
        description="要加载的技能名称(来自系统提示词'可用技能'清单)",
    )
    reference: str | None = Field(
        default=None,
        description=(
            "可选: 加载技能的特定参考文档(如 'mermaid'). "
            "不传则返回技能总览(L2), 传则返回该参考文档的详细内容(L3). "
            "可用参考文档名在技能总览中列出."
        ),
    )


class LoadSkillTool(BaseInternalTool):
    """加载指定技能的使用说明(领域知识).

    三级渐进式披露:
    - 不传reference: 返回技能总览(L2, 概览+选型+参考文档索引)
    - 传reference: 返回特定参考文档(L3, 单引擎/子主题的详细知识)

    加载L2后, 关联工具会通过SkillLoadMiddleware自动注入后续可用.

    参数:
    - skill_name: 技能名称(来自"可用技能"清单)
    - reference: 可选, 参考文档名(如 'mermaid'), 在技能总览中列出

    示例:
    - load_skill(skill_name="xlsx")
    - load_skill(skill_name="chart_maker")  # L2总览
    - load_skill(skill_name="chart_maker", reference="mermaid")  # L3详细语法
    """

    name: str = "load_skill"
    summary: str = "加载指定技能的使用说明(L2总览或L3参考文档), 渐进式披露入口"
    description: str = """加载指定技能的使用说明(领域知识).

三级渐进式披露:
- 不传reference: 返回技能总览(L2, 概览+选型+参考文档索引)
- 传reference: 返回特定参考文档(L3, 单引擎/子主题的详细知识)

加载L2后, 关联工具会自动注入后续可用.

参数:
- skill_name: 技能名称(来自"可用技能"清单)
- reference: 可选, 参考文档名(如 'mermaid'), 在技能总览中列出

示例:
- load_skill(skill_name="xlsx")
- load_skill(skill_name="chart_maker")  # L2总览
- load_skill(skill_name="chart_maker", reference="mermaid")  # L3详细语法
"""
    args_schema: type[LoadSkillRequest] = LoadSkillRequest

    def __init__(
        self,
        user_id: str = "",
        thread_id: str = "",
        *,
        agent_id: str = "",
        **kwargs: Any,
    ) -> None:
        super().__init__(user_id, thread_id, agent_id=agent_id, **kwargs)
        self._skill_bridge: SkillBridge | None = None
        self._available_skills: list[str] = []

    def set_skill_pool(
        self,
        skill_bridge: SkillBridge,
        available_skills: list[str],
    ) -> None:
        """设置实例级skill数据源, 由InferenceCoordinator组装工具集时调用.

        Args:
            skill_bridge: SkillBridge单例(L2正文来源)
            available_skills: 该agent启用的skill名列表

        """
        self._skill_bridge = skill_bridge
        self._available_skills = available_skills
        logger.info("load_skill工具skill池已设置: %s", available_skills)

    @override
    async def _arun(self, skill_name: str, reference: str | None = None) -> str:
        """加载指定skill的使用说明(L2总览或L3参考文档).

        Args:
            skill_name: 技能名称
            reference: 可选, 参考文档名; None返回L2总览, 非空返回L3引用

        Returns:
            L2/L3知识正文(markdown); 失败返回JSON错误.

        """
        if not self._skill_bridge:
            return json.dumps(
                {"success": False, "message": "skill数据源未配置"},
                ensure_ascii=False,
            )

        if skill_name not in self._available_skills:
            return json.dumps(
                {
                    "success": False,
                    "message": f"技能 '{skill_name}' 不在可用列表",
                    "available_skills": self._available_skills,
                },
                ensure_ascii=False,
            )

        if reference:
            return self._load_l3(skill_name, reference)

        return self._load_l2(skill_name)

    def _load_l2(self, skill_name: str) -> str:
        """加载L2总览(skill的SKILL.md正文)."""
        l2 = self._skill_bridge.get_skill_l2(skill_name)
        if l2 is None:
            return json.dumps(
                {"success": False, "message": f"技能 '{skill_name}' 未加载"},
                ensure_ascii=False,
            )
        logger.info("加载skill L2: %s (%d字符)", skill_name, len(l2))
        return l2

    def _load_l3(self, skill_name: str, reference: str) -> str:
        """加载L3引用文档(skill的references/xxx.md).

        失败时返回可用引用列表, 引导LLM修正.
        """
        content = self._skill_bridge.get_skill_reference(skill_name, reference)
        if content is not None:
            logger.info(
                "加载skill L3引用: %s/%s (%d字符)",
                skill_name,
                reference,
                len(content),
            )
            return content

        available = self._skill_bridge.get_reference_names(skill_name)
        return json.dumps(
            {
                "success": False,
                "message": f"参考文档 '{reference}' 不存在",
                "available_references": available,
            },
            ensure_ascii=False,
        )


__all__ = ["LoadSkillRequest", "LoadSkillTool"]
