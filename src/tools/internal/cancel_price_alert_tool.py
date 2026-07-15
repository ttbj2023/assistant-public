"""取消价格监控工具 - cancel_price_alert."""

from __future__ import annotations

import logging
from typing import Any, ClassVar, override

from pydantic import BaseModel, ConfigDict, Field

from src.storage.service.price_alert_service import get_price_alert_engine
from src.tools.shared.base_internal_tool import BaseInternalTool

logger = logging.getLogger(__name__)


class CancelPriceAlertRequest(BaseModel):
    """取消价格监控请求."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"additionalProperties": False},
    )

    rule_id: str = Field(
        ...,
        description="要取消的价格监控规则ID (由 create_price_alert 返回, 或用 list_price_alerts 查询)",
    )


class CancelPriceAlertTool(BaseInternalTool):
    """取消一条价格监控规则."""

    name: str = "cancel_price_alert"
    search_keywords: ClassVar[list[str]] = [
        "股票",
        "价格",
        "监控",
        "取消监控",
        "停止提醒",
    ]
    description: str = """取消一条价格监控规则, 停止其价格提醒.

参数:
- rule_id: 监控规则ID(必填), 由 create_price_alert 返回或 list_price_alerts 查得"""
    args_schema: type[CancelPriceAlertRequest] = CancelPriceAlertRequest

    @override
    async def _arun(self, **kwargs: Any) -> str:
        try:
            req = CancelPriceAlertRequest(**kwargs)
        except Exception as e:
            return self._format_error(e)

        owner = (self.user_id, self.thread_id, self.agent_id)
        try:
            engine = get_price_alert_engine()
            ok = await engine.disable_rule(req.rule_id, owner)
        except Exception as e:
            logger.error("取消价格监控失败: %s", e)
            return f"错误: 取消价格监控失败: {e}"

        if not ok:
            return f"错误: 规则 {req.rule_id} 不存在或已结束"
        return f"✅ 已取消价格监控规则 {req.rule_id}"


__all__ = ["CancelPriceAlertTool"]
