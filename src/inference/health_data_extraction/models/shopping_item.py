"""购物清单验证模型 - 简单记录用户购买的食材."""

from __future__ import annotations

from typing import ClassVar

from pydantic import BaseModel, Field


class ShoppingListModel(BaseModel):
    """购物清单验证模型 - 只需商品名称, 其余可选."""

    name: str = Field(..., description="商品/食材名称")
    quantity: int | None = Field(None, description="数量")
    notes: str | None = Field(None, description="备注")

    class Config:
        json_schema_extra: ClassVar[dict] = {
            "example": {
                "name": "鲜活淡水鲈鱼",
                "quantity": 1,
                "notes": "400-500g",
            },
        }
