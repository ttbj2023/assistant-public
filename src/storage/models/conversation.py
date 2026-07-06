"""对话系统相关的数据模型.

包含对话索引,置顶记忆等模型定义.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel
from sqlalchemy import Column, DateTime, UniqueConstraint, text
from sqlmodel import Field, SQLModel


class ConversationData(BaseModel):
    """统一对话数据结构 - 四个并行操作的数据源.

    来自Agent本轮对话完成后的返回数据,所有并行操作都基于此统一数据源.

    Attributes:
        user_id: 用户ID
        thread_id: 线程ID
        user_message: 用户消息
        assistant_response: 助手回复
        round_number: 轮次号(业务唯一标识,由 ConversationService 自动分配)
        timestamp: 对话时间戳
        agent_id: Agent ID(必填,不允许漏传)
        metadata: 额外的元数据字典

    Note:
        - round_number 是业务层面的唯一标识,与 user_id + thread_id + agent_id 组成复合唯一键
        - 数据库主键 id 是技术层面的自增ID,仅用于数据库内部
        - 不再使用 conversation_id 字段,完全依赖 round_number 进行业务标识
        - user_message 中已包含附件描述,格式: "文本内容 [img: files/images/xxx.jpg - 描述]"

    """

    user_id: str
    thread_id: str
    user_message: str
    assistant_response: str
    round_number: int
    timestamp: datetime
    agent_id: str
    metadata: dict[str, Any] = Field(default_factory=dict)

    class Config:
        """Pydantic配置."""

        from_attributes = True


class ConversationIndexBase(SQLModel):
    """对话索引基础模型."""

    round_number: int = Field(..., description="对话轮次号")
    topic: str | None = Field(default=None, description="对话主题")
    summary: str | None = Field(default=None, description="对话摘要")
    user_message: str = Field(..., description="用户消息")
    assistant_response: str = Field(..., description="助手回复")
    message_count: int = Field(default=1, description="消息数量")
    token_usage: int = Field(default=0, description="Token使用量")


class ConversationIndex(ConversationIndexBase, table=True):
    """对话索引表模型 - Agent物理隔离版本.

    数据库表名:conversation_index
    唯一约束:user_id + thread_id + round_number(业务唯一标识)

    Note:
        - 移除了 conversation_id 字段,使用 round_number 作为业务唯一标识
        - id 是数据库主键,仅用于技术层面,不参与业务逻辑
        - Agent物理隔离:每个agent拥有独立的数据库文件,不再需要agent_id过滤
        - agent_id 字段保留用于数据溯源和日志追踪,不参与查询过滤

    """

    __tablename__ = "conversation_index"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "thread_id",
            "round_number",
            name="uk_user_thread_round",
        ),
        {"extend_existing": True},
    )

    id: int | None = Field(
        default=None,
        primary_key=True,
        description="索引ID(数据库主键)",
    )
    user_id: str = Field(..., description="用户ID")
    thread_id: str = Field(..., description="线程ID")
    agent_id: str = Field(..., description="Agent ID(数据溯源字段)")
    created_at: datetime | None = Field(
        default_factory=datetime.utcnow,
        sa_column=Column(DateTime, server_default=text("CURRENT_TIMESTAMP")),
        description="创建时间",
    )
    updated_at: datetime | None = Field(
        default_factory=datetime.utcnow,
        sa_column=Column(
            DateTime,
            server_default=text("CURRENT_TIMESTAMP"),
            onupdate=text("CURRENT_TIMESTAMP"),
        ),
        description="最后更新时间",
    )

    class Config:
        """SQLModel配置."""

        from_attributes = True


class ConversationIndexGroupBase(SQLModel):
    """对话索引分组基础模型 - 老期冻结的语义 run 弧短语."""

    round_start: int = Field(..., description="run 起始轮次(包含)")
    round_end: int = Field(..., description="run 结束轮次(包含)")
    arc_phrase: str = Field(
        ...,
        description="run 弧短语(LLM 从各轮 summary 蒸馏, 冻结; 兼检索钩子与时间连续性)",
    )


class ConversationIndexGroup(ConversationIndexGroupBase, table=True):
    """对话索引分组表模型 - 老期冻结的语义 run.

    数据库表名:conversation_index_group
    唯一约束:user_id + thread_id + round_start(一个 run 起点唯一)

    Note:
        - 每个 group 对应一个已闭合的语义 run(连续同主题轮次, embedding 相似度判定)
        - 弧短语一次性生成后冻结, 永不再压缩(避免重复摘要损耗)
        - 与 conversation_index 同库, Agent 物理隔离; agent_id 仅溯源不过滤
        - 老期读 group, 近期读 fine(按 round_number), 天然无重叠, 故 fine 无需 hidden 标记

    """

    __tablename__ = "conversation_index_group"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "thread_id",
            "round_start",
            name="uk_user_thread_group_start",
        ),
        {"extend_existing": True},
    )

    id: int | None = Field(
        default=None,
        primary_key=True,
        description="分组ID(数据库主键)",
    )
    user_id: str = Field(..., description="用户ID")
    thread_id: str = Field(..., description="线程ID")
    agent_id: str = Field(..., description="Agent ID(数据溯源字段)")
    created_at: datetime | None = Field(
        default_factory=datetime.utcnow,
        sa_column=Column(DateTime, server_default=text("CURRENT_TIMESTAMP")),
        description="创建时间",
    )
    updated_at: datetime | None = Field(
        default_factory=datetime.utcnow,
        sa_column=Column(
            DateTime,
            server_default=text("CURRENT_TIMESTAMP"),
            onupdate=text("CURRENT_TIMESTAMP"),
        ),
        description="最后更新时间",
    )

    class Config:
        """SQLModel配置."""

        from_attributes = True


__all__ = [
    "ConversationData",
    "ConversationIndex",
    "ConversationIndexBase",
    "ConversationIndexGroup",
    "ConversationIndexGroupBase",
]
