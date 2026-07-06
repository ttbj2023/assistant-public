"""健康数据提取模块.

将自然语言文本转换为结构化健康数据.
支持6种数据类型: weight_record, meal_record, workout_record,
shopping_list, food_product, medical_report.

架构: DeepSeek V4 Flash (JSON Mode) 单次调用完成检测+分类+转录.
"""

from __future__ import annotations

from .unified_extractor import UnifiedHealthExtractor

__all__ = [
    "UnifiedHealthExtractor",
]
