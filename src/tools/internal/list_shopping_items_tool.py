"""健康购物清单工具 - list_shopping_items.

查询最近N天的购物清单/食材库存.
"""

from __future__ import annotations

import logging
from typing import Any, override

from langchain_core.tools import BaseTool
from pydantic import BaseModel, ConfigDict, Field

from src.tools.internal.health_data_helpers import HealthDataServiceAccessor
from src.tools.shared.tool_runtime import format_tool_error, sync_runnable

logger = logging.getLogger(__name__)


class ListShoppingItemsRequest(BaseModel):
    """健康购物清单请求模型."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"additionalProperties": False},
    )

    days: int = Field(
        default=30,
        ge=1,
        le=365,
        description="查询最近N天, 默认30, 最大365",
    )


@sync_runnable
class ListShoppingItemsTool(BaseTool):
    """健康购物清单."""

    name: str = "list_shopping_items"
    description: str = (
        "查询购物清单/食材库存. 参数: days(最近N天, 默认30). "
        "返回购买日期/名称/数量/备注, 供饮食建议参考."
    )
    args_schema: type[ListShoppingItemsRequest] = ListShoppingItemsRequest

    def _get_accessor(self) -> HealthDataServiceAccessor:
        if not hasattr(self, "_health_acc"):
            acc = HealthDataServiceAccessor(self.user_id, self.thread_id, self.agent_id)
            object.__setattr__(self, "_health_acc", acc)
        return self._health_acc

    @override
    async def _arun(self, **kwargs: Any) -> str:
        try:
            request = ListShoppingItemsRequest(**kwargs)
            service = await self._get_accessor().get_service()
            days = min(request.days or 30, 365)

            items = await service.get_shopping_list(days=days)

            if not items:
                return f"最近{days}天无购物记录"

            lines = [f"购物清单(最近{days}天, {len(items)}件):"]
            for item in items:
                parts = [f"- {item.purchase_date.strftime('%Y-%m-%d')}"]
                parts.append(item.name)
                if item.quantity is not None:
                    parts.append(f"x{item.quantity}")
                if item.notes:
                    parts.append(f"({item.notes})")
                lines.append(", ".join(parts))

            return "\n".join(lines)
        except Exception as e:
            logger.error("健康购物清单查询失败: %s", e)
            return format_tool_error(e)


__all__ = ["ListShoppingItemsTool"]
