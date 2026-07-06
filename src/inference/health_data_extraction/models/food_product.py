"""食品包装目录验证模型 - 精确转录营养标签, 禁止推断."""

from __future__ import annotations

from typing import ClassVar

from pydantic import BaseModel, Field


class NutritionPer100gModel(BaseModel):
    """每100g营养成分模型 - 精确转录, 所有字段均可选."""

    energy_kj: float | None = Field(
        None,
        ge=0,
        description="能量(kJ, 中国标签原始单位)",
    )
    calories: float | None = Field(None, ge=0, description="热量(kcal)")
    protein: float | None = Field(None, ge=0, description="蛋白质(g)")
    carbs: float | None = Field(None, ge=0, description="碳水化合物(g)")
    fat: float | None = Field(None, ge=0, description="脂肪(g)")
    fiber: float | None = Field(None, ge=0, description="膳食纤维(g)")
    sugar: float | None = Field(None, ge=0, description="糖(g)")
    sodium: float | None = Field(None, ge=0, description="钠(mg)")

    class Config:
        json_schema_extra: ClassVar[dict] = {
            "example": {
                "energy_kj": 158,
                "calories": 37.8,
                "protein": 3.3,
                "carbs": 2.1,
                "fat": 1.8,
                "sodium": 14,
            },
        }


class FoodProductModel(BaseModel):
    """食品包装目录验证模型 - 仅用于有明确包装/营养标签信息的商品."""

    product_id: str = Field(..., description="商品稳定ID(基于商品名_规格生成)")
    name: str = Field(..., description="商品名称")
    brand: str | None = Field(None, description="品牌")
    weight_per_unit: float | None = Field(None, ge=0, description="单位重量(g)")
    ingredients: str | None = Field(None, description="配料表")
    nutrition_per_100g: NutritionPer100gModel | None = Field(
        None,
        description="每100g营养成分(来自包装标签)",
    )
    allergens: list[str] | None = Field(None, description="过敏原列表")

    class Config:
        json_schema_extra: ClassVar[dict] = {
            "example": {
                "product_id": "醇豆浆_400g",
                "name": "醇豆浆",
                "weight_per_unit": 400,
                "nutrition_per_100g": {
                    "energy_kj": 158,
                    "calories": 37.8,
                    "protein": 3.3,
                    "carbs": 2.1,
                    "fat": 1.8,
                    "sodium": 14,
                },
            },
        }
