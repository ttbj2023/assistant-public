"""模型用量统计数据模型."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel
from sqlalchemy import Column, DateTime, Index, text
from sqlmodel import Field, SQLModel

from src.core.datetime_utils import now_utc
from src.core.types import UsageAccuracy, UsageRecordCreate, UsageUnitType


class UsageRecordBase(SQLModel):
    """用量记录基础字段."""

    user_id: str = Field(..., description="用户ID")
    thread_id: str = Field(..., description="线程ID")
    agent_id: str = Field(..., description="Agent ID")
    round_number: int | None = Field(default=None, description="对话轮次号")
    request_id: str | None = Field(default=None, description="请求ID")

    operation: str = Field(..., description="操作类型")
    usage_source: str = Field(..., description="调用来源")
    provider: str | None = Field(default=None, description="模型供应商")
    model_id: str | None = Field(default=None, description="完整模型ID")
    run_id: str | None = Field(default=None, description="LangChain run_id")
    parent_run_id: str | None = Field(default=None, description="父级 run_id")
    external_job_id: str | None = Field(default=None, description="外部任务ID")

    unit_type: str = Field(default="token", description="计量单位")
    request_count: int = Field(default=1, description="请求次数")
    input_tokens: int | None = Field(default=None, description="输入token")
    output_tokens: int | None = Field(default=None, description="输出token")
    total_tokens: int | None = Field(default=None, description="总token")
    cache_read_tokens: int | None = Field(default=None, description="缓存读取token")
    cache_creation_tokens: int | None = Field(default=None, description="缓存写入token")
    reasoning_tokens: int | None = Field(default=None, description="推理token")

    accuracy: str = Field(default="unknown", description="统计准确度")
    success: bool = Field(default=True, description="调用是否成功")
    duration_ms: int | None = Field(default=None, description="调用耗时毫秒")
    raw_usage_json: str | None = Field(default=None, description="原始usage JSON")
    metadata_json: str | None = Field(default=None, description="附加元数据 JSON")


class UsageRecord(UsageRecordBase, table=True):
    """模型用量统计表."""

    __tablename__ = "usage_records"
    __table_args__ = (
        Index("idx_usage_thread_created", "thread_id", "created_at"),
        Index("idx_usage_agent_round", "agent_id", "round_number"),
        Index("idx_usage_source_created", "usage_source", "created_at"),
        {"extend_existing": True},
    )

    id: int | None = Field(default=None, primary_key=True, description="记录ID")
    created_at: datetime | None = Field(
        default_factory=now_utc,
        sa_column=Column(DateTime, server_default=text("CURRENT_TIMESTAMP")),
        description="创建时间",
    )

    class Config:
        """SQLModel配置."""

        from_attributes = True


class UsageQuery(BaseModel):
    """用量查询条件."""

    user_id: str
    thread_id: str | None = None
    agent_id: str | None = None
    usage_source: str | None = None
    operation: str | None = None
    start_time: datetime | None = None
    end_time: datetime | None = None
    limit: int = 100
    offset: int = 0


__all__ = [
    "UsageAccuracy",
    "UsageQuery",
    "UsageRecord",
    "UsageRecordBase",
    "UsageRecordCreate",
    "UsageUnitType",
]
