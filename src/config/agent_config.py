"""Agent配置对象 - Pydantic模型定义

职责边界:
- 定义: AgentConfig/AgentMemoryConfig的Pydantic模型,字段校验,默认值
- 不管理: Agent实现类的路由信息(由AGENT_REGISTRY管理)

配置来源优先级:
1. agent.yaml (用户编辑, 最高优先级)
2. Pydantic Field defaults (兜底默认值)

Agent实现类路由:
- 由 src.agent.agents_implementations.AGENT_REGISTRY 管理
- AgentFactory通过注册表动态加载实现类
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, Field, field_validator

_AGENT_ID_PATTERN = re.compile(r"^[a-z][a-z0-9-]*$")


class AgentMemoryConfig(BaseModel):
    """Agent记忆配置 - 统一使用char字符预算"""

    type: str = Field(
        default="",
        description="记忆类型:空字符串=不开启,local=本地记忆",
    )

    total_char_budget: int = Field(
        default=20000,
        ge=1000,
        description="主历史区字符预算(index 独立后此项即主历史预算)",
    )

    index_char_budget: int = Field(
        default=10000,
        ge=0,
        description="索引区近期全索引字符预算(老期冻结弧短语不占此预算, 线性增长)",
    )

    index_run_similarity_threshold: float = Field(
        default=0.45,
        ge=0.0,
        le=1.0,
        description="索引 run 检测: 相邻轮 summary embedding 余弦相似度阈值"
        "(低于则切新 run 并冻结弧短语; 越低 run 越长/弧越粗)",
    )

    index_arc_max_chars: int = Field(
        default=60,
        ge=8,
        description="冻结弧短语最大字符数(信号密度旋钮; 实测 40 截断半句, 60 为叙事完整起步值)",
    )

    @field_validator("type")
    @classmethod
    def validate_memory_type(cls, v: str) -> str:
        allowed = ["", "local", "simple", "remote", "hybrid"]
        if v not in allowed:
            raise ValueError(f"memory_type must be one of {allowed}")
        return v


class AgentConfig(BaseModel):
    """Agent配置主类

    所有字段默认值为生产级兜底值,agent.yaml中只写需要覆盖的字段.
    """

    agent_id: str = Field(default="personal-assistant", description="Agent ID")
    name: str = Field(default="Personal Agent Assistant", description="Agent名称")
    description: str = Field(
        default="个人助手Agent,支持记忆管理,TODO任务等",
        description="Agent描述",
    )

    model_id: str = Field(
        default="deepseek:deepseek-v4-pro",
        description="LLM模型标识符",
    )

    llm_config: dict[str, Any] | None = Field(
        default=None,
        description="LLM参数覆盖配置(可选,仅限agent.yaml)",
    )

    system_prompt: str = Field(
        default="你是一个AI助手, 根据用户输入提供友好,准确的帮助.",
        description="系统提示词(最小化兜底, 各Agent在自身yaml覆盖)",
    )

    first_turn_prompt: str = Field(
        default="",
        description="首轮对话开场专属提示词(为空则不启用首轮引导, "
        "非空时首轮跳过记忆拼接并注入该提示词)",
    )

    tools: list[str] = Field(
        default_factory=lambda: [
            "todo_manager_group",
            "memory_recall_group",
            "search_available_tools",
        ],
        description="核心工具列表(必定注入)",
    )

    optional_tools: list[str] = Field(
        default_factory=list,
        description="休眠工具列表(通过search_available_tools按需发现, 由中间件动态注入)",
    )

    skills: list[str] = Field(
        default_factory=list,
        description="启用的skill列表(领域知识+定向能力, 通过load_skill按需加载)",
    )

    memory: AgentMemoryConfig = Field(
        default_factory=AgentMemoryConfig,
        description="记忆配置",
    )

    @field_validator("agent_id")
    @classmethod
    def validate_agent_id(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("agent_id不能为空")
        if not _AGENT_ID_PATTERN.match(v):
            raise ValueError(f"agent_id格式无效: '{v}', 需匹配 ^[a-z][a-z0-9-]*$")
        return v

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("name不能为空")
        return v.strip()

    @field_validator("model_id")
    @classmethod
    def validate_model_id(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("model_id不能为空")
        return v.strip()


__all__ = [
    "AgentConfig",
    "AgentMemoryConfig",
]
