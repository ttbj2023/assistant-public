"""查看价格监控规则工具 - list_price_alerts."""

from __future__ import annotations

import logging
from typing import Any, ClassVar, override

from pydantic import BaseModel, ConfigDict

from src.storage.service.price_alert_service import get_price_alert_engine
from src.tools.shared.base_internal_tool import BaseInternalTool

logger = logging.getLogger(__name__)


class ListPriceAlertsRequest(BaseModel):
    """查看价格监控规则请求 (无参数)."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"additionalProperties": False},
    )


class ListPriceAlertsTool(BaseInternalTool):
    """查看当前用户活跃的价格监控规则."""

    name: str = "list_price_alerts"
    search_keywords: ClassVar[list[str]] = [
        "股票",
        "价格",
        "监控",
        "查看监控",
        "价格提醒",
    ]
    description: str = """查看当前用户所有活跃的价格监控规则, 无参数."""
    args_schema: type[ListPriceAlertsRequest] = ListPriceAlertsRequest

    @override
    async def _arun(self, **kwargs: Any) -> str:
        owner = (self.user_id, self.thread_id, self.agent_id)
        try:
            engine = get_price_alert_engine()
            rules = await engine.list_active(owner)
        except Exception as e:
            logger.error("查询价格监控失败: %s", e)
            return f"错误: 查询价格监控失败: {e}"

        if not rules:
            return "当前没有任何活跃的价格监控规则."

        lines = [f"📋 共 {len(rules)} 条价格监控规则:"]
        for r in rules:
            verb = "涨到" if str(r.direction) == "above" else "跌破"
            name = r.stock_name or ""
            head = f"{name}({r.stock_code})" if name else r.stock_code
            method = "微信" if r.delivery_method == "wechat" else "邮件"
            lines.append(
                f"- [{r.rule_id}] {head} {verb} {r.threshold_price} ({method})"
            )
        return "\n".join(lines)


__all__ = ["ListPriceAlertsTool"]
