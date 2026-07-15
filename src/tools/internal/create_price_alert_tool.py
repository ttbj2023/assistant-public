"""创建价格监控工具 - create_price_alert.

设定个股价格阈值, 突破时由 PriceAlertEngine 轮询并通过 NotificationService 提醒.
一次性语义: 触发即提醒一次并自动结束 (规则转为 disabled).
市场按代码前缀自动推断: 6 开头=沪市, 0/3 开头=深市.
"""

from __future__ import annotations

import logging
from typing import Any, ClassVar, override

from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.core.notification import resolve_delivery
from src.storage.service.price_alert_service import get_price_alert_engine
from src.tools.shared.base_internal_tool import BaseInternalTool

logger = logging.getLogger(__name__)


def infer_market(code: str) -> int:
    """按代码前缀推断市场: 6→沪市(1), 0/3→深市(0). 其余报错."""
    code = code.strip()
    if code.startswith("6"):
        return 1
    if code.startswith(("0", "3")):
        return 0
    raise ValueError(f"无法识别股票市场(代码需以 6/0/3 开头): {code}; 北交所暂不支持")


class CreatePriceAlertRequest(BaseModel):
    """创建价格监控请求."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"additionalProperties": False},
    )

    stock_code: str = Field(
        ...,
        min_length=6,
        max_length=6,
        description="6位A股代码, 如 600519(贵州茅台) / 000001(平安银行)",
    )
    direction: str = Field(
        ...,
        description="触发方向: above(涨到/向上突破时提醒) | below(跌破时提醒)",
    )
    threshold_price: float = Field(
        ...,
        gt=0,
        description="价格阈值, 到价触发",
    )
    stock_name: str | None = Field(
        None,
        description="股票名称(可选, 用于告警文案展示, 如 '贵州茅台')",
    )
    delivery_method: str = Field(
        "wechat",
        description="提醒方式: wechat(微信, 默认) | email(邮件)",
    )
    email_address: str | None = Field(
        None,
        description="收件邮箱 (delivery_method=email 时必填)",
    )

    @field_validator("direction")
    @classmethod
    def _validate_direction(cls, v: str) -> str:
        if v not in ("above", "below"):
            raise ValueError("direction 必须为 above 或 below")
        return v

    @field_validator("delivery_method")
    @classmethod
    def _validate_method(cls, v: str) -> str:
        if v not in ("wechat", "email"):
            raise ValueError("delivery_method 必须为 wechat 或 email")
        return v


class CreatePriceAlertTool(BaseInternalTool):
    """创建A股个股价格监控, 突破阈值时提醒一次."""

    name: str = "create_price_alert"
    search_keywords: ClassVar[list[str]] = [
        "股票",
        "股价",
        "价格",
        "监控",
        "告警",
        "提醒",
        "突破",
        "到价",
        "跌破",
        "涨到",
        "行情",
    ]
    description: str = """创建A股个股价格监控, 当股价向上突破或向下跌破设定阈值时提醒一次(一次性, 触发后自动结束).

参数:
- stock_code: 6位A股代码(必填), 如 600519 / 000001
- direction: 触发方向(必填), above=涨到该价提醒, below=跌破该价提醒
- threshold_price: 价格阈值(必填)
- stock_name: 股票名称(可选, 用于告警展示)
- delivery_method: 提醒方式, wechat(微信, 默认) | email(邮件)
- email_address: 收件邮箱 (delivery_method=email 时必填)

说明:
- 仅在交易时段(工作日 9:30-11:30 / 13:00-15:00)轮询
- 一次性: 价格触发阈值即提醒一次, 规则自动结束, 不重复提醒
- 创建时若价格已穿越阈值, 下次轮询立即触发
- 需上下双线监控(如某区间)请分别创建 above 和 below 两条规则
- 微信渠道需已配置接收凭证; 邮件需提供 email_address"""
    args_schema: type[CreatePriceAlertRequest] = CreatePriceAlertRequest

    @override
    async def is_available(self) -> bool:
        """有微信渠道或邮件配置其一即注册."""
        if await resolve_delivery(
            self.user_id, self.thread_id, self.agent_id, "wechat"
        ):
            return True
        from src.config.smtp_config import is_configured

        return is_configured()

    @override
    async def _arun(self, **kwargs: Any) -> str:
        try:
            req = CreatePriceAlertRequest(**kwargs)
        except Exception as e:
            return self._format_error(e)

        try:
            market = infer_market(req.stock_code)
        except ValueError as e:
            return self._format_error(e)

        owner = (self.user_id, self.thread_id, self.agent_id)
        fields: dict[str, Any] = {
            "market": market,
            "stock_code": req.stock_code,
            "stock_name": req.stock_name or "",
            "direction": req.direction,
            "threshold_price": req.threshold_price,
            "delivery_method": req.delivery_method,
        }

        if req.delivery_method == "email":
            if not req.email_address:
                return self._format_error(
                    ValueError("email 投递需要 email_address 参数")
                )
            fields["email_address"] = req.email_address
            channel_label = f"邮件({req.email_address})"
        else:
            delivery = await resolve_delivery(
                self.user_id, self.thread_id, self.agent_id, "wechat"
            )
            if delivery is None:
                return (
                    "错误: 未检测到可用的微信接收渠道, 无法创建价格监控. "
                    "请先配置微信接收(target 与 openclaw_account), "
                    "或改用 delivery_method=email 并提供 email_address."
                )
            fields["account_id"] = delivery.account_id
            fields["target"] = delivery.target
            fields["openclaw_channel"] = delivery.openclaw_channel
            channel_label = "微信"

        try:
            engine = get_price_alert_engine()
            rule = await engine.create_rule(owner, **fields)
        except Exception as e:
            logger.error("创建价格监控失败: %s", e)
            return f"错误: 创建价格监控失败: {e}"

        verb = "涨到" if req.direction == "above" else "跌破"
        name_part = (
            f"{req.stock_name}({req.stock_code})" if req.stock_name else req.stock_code
        )
        return (
            f"✅ 价格监控已创建\n"
            f"- 监控ID: {rule.rule_id}\n"
            f"- 股票: {name_part}\n"
            f"- 触发条件: {verb} {req.threshold_price}\n"
            f"- 提醒渠道: {channel_label}\n"
            f"- 仅交易时段监控, 触发即提醒一次并自动结束"
        )


__all__ = ["CreatePriceAlertTool", "infer_market"]
