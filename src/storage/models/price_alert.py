"""价格监控规则数据模型定义.

一次性语义: 触发即结束 (status active→disabled), 无 last_side/count/date 等
长期监控字段. 规则自包含投递参数 (delivery_method + account_id/target/
openclaw_channel 或 email_address), 触发时由 NotificationService 派发,
无需回查渠道配置.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import Field, field_validator
from sqlmodel import Column, DateTime, SQLModel, text
from sqlmodel import Field as SQLField


class AlertDirection(StrEnum):
    """触发方向."""

    ABOVE = "above"  # 涨到/向上突破时触发
    BELOW = "below"  # 跌破时触发


class AlertStatus(StrEnum):
    """规则状态 (一次性: active → disabled)."""

    ACTIVE = "active"
    DISABLED = "disabled"


class PriceAlertRuleBase(SQLModel):
    """价格监控规则基础模型."""

    rule_id: str = Field(
        default_factory=lambda: f"pa_{uuid.uuid4().hex[:8]}",
        description="规则唯一标识",
    )
    market: int = Field(..., description="市场: 0=深圳 1=上海")
    stock_code: str = Field(..., min_length=6, max_length=6, description="6位A股代码")
    stock_name: str = Field(default="", description="股票名称(展示用)")
    direction: AlertDirection = Field(..., description="触发方向: above|below")
    threshold_price: float = Field(..., gt=0, description="价格阈值")
    delivery_method: str = Field(default="wechat", description="投递方式: wechat|email")
    # wechat 投递参数 (delivery_method=wechat 时必填)
    account_id: str = Field(default="", description="OpenClaw bot 账号 ID")
    target: str = Field(default="", description="OpenClaw 收消息人")
    openclaw_channel: str = Field(default="", description="OpenClaw 系统渠道名")
    # email 投递参数 (delivery_method=email 时必填)
    email_address: str = Field(default="", description="收件邮箱")
    status: AlertStatus = Field(default=AlertStatus.ACTIVE, description="规则状态")

    model_config = {"validate_assignment": True, "str_strip_whitespace": True}

    @field_validator("stock_code")
    @classmethod
    def _validate_code(cls, v: str) -> str:
        if not v.isdigit():
            raise ValueError("股票代码必须为6位数字")
        return v

    @field_validator("delivery_method")
    @classmethod
    def _validate_method(cls, v: str) -> str:
        if v not in ("wechat", "email"):
            raise ValueError("delivery_method 必须为 wechat 或 email")
        return v


class PriceAlertRule(PriceAlertRuleBase, table=True):
    """价格监控规则数据表模型."""

    __tablename__ = "price_alerts"
    __table_args__ = {"extend_existing": True}

    id: int | None = SQLField(default=None, primary_key=True, description="记录ID")
    user_id: str = Field(..., description="用户ID")
    thread_id: str = Field(..., description="线程ID")
    agent_id: str = Field(..., description="Agent ID")
    triggered_at: datetime | None = Field(
        default=None, description="触发结束时间 (一次性触发后填充)"
    )
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
        description="更新时间",
    )

    class Config:
        from_attributes = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "rule_id": self.rule_id,
            "market": self.market,
            "stock_code": self.stock_code,
            "stock_name": self.stock_name,
            "direction": self.direction,
            "threshold_price": self.threshold_price,
            "delivery_method": self.delivery_method,
            "account_id": self.account_id,
            "target": self.target,
            "openclaw_channel": self.openclaw_channel,
            "email_address": self.email_address,
            "status": self.status,
            "triggered_at": self.triggered_at.isoformat()
            if self.triggered_at
            else None,
            "user_id": self.user_id,
            "thread_id": self.thread_id,
            "agent_id": self.agent_id,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


__all__ = [
    "AlertDirection",
    "AlertStatus",
    "PriceAlertRule",
    "PriceAlertRuleBase",
]
