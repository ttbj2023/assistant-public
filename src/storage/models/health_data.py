"""健康数据相关的数据模型.

包含体重记录,体检报告,每日健康汇总,周健康趋势,
购物清单,运动记录,摄入记录,运动采样,心电图等模型定义.

隔离方式: 文件级隔离, 每个user_id+thread_id拥有独立的health_data.db.
因此所有表均不包含user_id/thread_id字段.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import JSON, Column, DateTime, Index, UniqueConstraint, text
from sqlmodel import Field, SQLModel


class WeightRecordBase(SQLModel):
    """体重记录基础模型 - 原始体重记录, 允许同一天多条."""

    weight_kg: float = Field(..., description="体重(kg)")
    body_fat_pct: float | None = Field(default=None, description="体脂率(%)")
    muscle_mass_kg: float | None = Field(default=None, description="肌肉量(kg)")
    recorded_at: datetime | None = Field(
        default=None,
        description="称重时间(用户明确给出时间时填写, 模糊时间不填)",
    )
    round_number: int | None = Field(
        default=None,
        description="来源对话轮次(方便Agent定向查询)",
    )
    source: str = Field(
        default="conversation_extraction",
        description="数据来源: conversation_extraction(对话提取) / external_import(外部导入)",
    )


class WeightRecord(WeightRecordBase, table=True):
    """体重记录表模型.

    数据库表名:weight_records
    无唯一约束, 允许同一天多条记录.
    """

    __tablename__ = "weight_records"
    __table_args__ = {"extend_existing": True}

    id: int | None = Field(default=None, primary_key=True, description="记录ID")
    created_at: datetime | None = Field(
        default_factory=datetime.utcnow,
        sa_column=Column(DateTime, server_default=text("CURRENT_TIMESTAMP")),
        description="创建时间",
    )

    class Config:
        """SQLModel配置."""

        from_attributes = True


class MedicalReportBase(SQLModel):
    """体检报告基础模型."""

    report_date: datetime = Field(..., description="报告日期")
    report_type: str | None = Field(
        default=None,
        description="报告类型: routine/specialized",
    )
    round_number: int | None = Field(
        default=None,
        description="来源对话轮次(方便Agent定向查询)",
    )
    source: str = Field(
        default="conversation_extraction",
        description="数据来源: conversation_extraction(对话提取) / external_import(外部导入)",
    )


class MedicalReport(MedicalReportBase, table=True):
    """体检报告表模型.

    数据库表名:medical_reports
    """

    __tablename__ = "medical_reports"
    __table_args__ = {"extend_existing": True}

    id: int | None = Field(default=None, primary_key=True, description="报告ID")
    report_data: dict[str, Any] = Field(
        ...,
        sa_column=Column(JSON),
        description="报告数据(JSON格式扁平键值对)",
    )
    created_at: datetime | None = Field(
        default_factory=datetime.utcnow,
        sa_column=Column(DateTime, server_default=text("CURRENT_TIMESTAMP")),
        description="创建时间",
    )

    class Config:
        """SQLModel配置."""

        from_attributes = True


class DailyHealthSummaryBase(SQLModel):
    """每日健康汇总基础模型 - 统一的日维度宽表.

    融合外部设备导入数据和对话自动提取数据, 每日一行.
    所有字段均可为空, 表示当天该指标无数据.
    """

    steps: int | None = Field(default=None, description="步数")
    active_energy_kcal: float | None = Field(default=None, description="活动能量(kcal)")
    basal_energy_kcal: float | None = Field(
        default=None,
        description="基础代谢能量(kcal)",
    )
    distance_km: float | None = Field(default=None, description="步行+跑步距离(km)")
    stand_hours: int | None = Field(default=None, description="站立小时数")

    body_mass_kg: float | None = Field(default=None, description="体重(kg)")
    body_fat_pct: float | None = Field(default=None, description="体脂率(%)")
    muscle_mass_kg: float | None = Field(default=None, description="肌肉量(kg)")
    resting_hr_bpm: float | None = Field(default=None, description="静息心率(bpm)")
    hrv_ms: float | None = Field(default=None, description="心率变异性SDNN(ms)")
    vo2_max: float | None = Field(default=None, description="最大摄氧量(ml/kg/min)")

    bed_time: datetime | None = Field(default=None, description="入床时间")
    wake_time: datetime | None = Field(default=None, description="起床时间")
    asleep_minutes: int | None = Field(default=None, description="入睡时长(分钟)")
    deep_sleep_minutes: float | None = Field(
        default=None,
        description="深度睡眠时长(分钟)",
    )
    awake_minutes: int | None = Field(default=None, description="清醒时长(分钟)")
    sleep_efficiency: float | None = Field(default=None, description="睡眠效率(%)")
    sleep_duration_hours: float | None = Field(
        default=None,
        description="睡眠时长(小时)",
    )

    weight_7d_avg: float | None = Field(default=None, description="7日体重均值(kg)")
    steps_7d_avg: float | None = Field(default=None, description="7日步数均值")
    resting_hr_7d_avg: float | None = Field(
        default=None,
        description="7日静息心率均值(bpm)",
    )
    hrv_7d_avg: float | None = Field(default=None, description="7日HRV均值(ms)")
    exercise_7d_total: float | None = Field(
        default=None,
        description="7日运动总时长(分钟)",
    )
    sleep_7d_avg: float | None = Field(default=None, description="7日睡眠均值(分钟)")
    sleep_efficiency_7d_avg: float | None = Field(
        default=None,
        description="7日睡眠效率均值(%)",
    )

    apple_exercise_minutes: float | None = Field(
        default=None,
        description="Apple Watch锻炼时间(分钟)",
    )
    stand_minutes: float | None = Field(default=None, description="站立时间(分钟)")
    flights_climbed: float | None = Field(default=None, description="爬楼层数")
    avg_hr_bpm: float | None = Field(default=None, description="日均心率(bpm)")
    min_hr_bpm: float | None = Field(default=None, description="日最低心率(bpm)")
    max_hr_bpm: float | None = Field(default=None, description="日最高心率(bpm)")
    respiratory_rate: float | None = Field(
        default=None,
        description="呼吸频率(次/分钟)",
    )
    blood_oxygen_pct: float | None = Field(default=None, description="血氧饱和度(%)")
    wrist_temperature: float | None = Field(
        default=None,
        description="睡眠手腕温度(degC)",
    )
    walking_speed_kmh: float | None = Field(default=None, description="步行速度(km/h)")
    walking_hr_avg: float | None = Field(
        default=None,
        description="步行时平均心率(bpm)",
    )
    sunlight_minutes: float | None = Field(default=None, description="日照时长(分钟)")
    rem_sleep_minutes: float | None = Field(
        default=None,
        description="REM睡眠时长(分钟)",
    )
    core_sleep_minutes: float | None = Field(
        default=None,
        description="核心睡眠时长(分钟)",
    )

    data_source: str = Field(
        default="conversation_extraction",
        description="数据来源: conversation_extraction(对话提取) / apple_health(外部导入)",
    )


class DailyHealthSummary(DailyHealthSummaryBase, table=True):
    """每日健康汇总表模型.

    数据库表名:daily_health_summary
    唯一约束:record_date
    """

    __tablename__ = "daily_health_summary"
    __table_args__ = (
        UniqueConstraint("record_date", name="uk_record_date"),
        {"extend_existing": True},
    )

    id: int | None = Field(default=None, primary_key=True, description="记录ID")
    record_date: date = Field(..., description="记录日期")
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


class WeeklyHealthSummaryBase(SQLModel):
    """每周健康趋势基础模型 - 预计算的周维度汇总.

    以周一为起始日, 每周一行.
    """

    steps_total: float | None = Field(default=None, description="周总步数")
    steps_daily_avg: float | None = Field(default=None, description="周日均步数")
    active_energy_total: float | None = Field(
        default=None,
        description="周总活动能量(kcal)",
    )
    active_energy_daily_avg: float | None = Field(
        default=None,
        description="周日均活动能量(kcal)",
    )
    basal_energy_total: float | None = Field(
        default=None,
        description="周总基础代谢能量(kcal)",
    )
    distance_total: float | None = Field(default=None, description="周总距离(km)")
    distance_daily_avg: float | None = Field(default=None, description="周日均距离(km)")
    exercise_minutes_total: float | None = Field(
        default=None,
        description="周总运动时长(分钟)",
    )
    stand_hours_total: float | None = Field(default=None, description="周总站立小时数")

    body_mass_avg: float | None = Field(default=None, description="周均体重(kg)")
    resting_hr_avg: float | None = Field(default=None, description="周均静息心率(bpm)")
    hrv_avg: float | None = Field(default=None, description="周均HRV(ms)")
    vo2_max_avg: float | None = Field(default=None, description="周均VO2Max(ml/kg/min)")

    sleep_duration_avg: float | None = Field(
        default=None,
        description="周均睡眠时长(小时)",
    )
    sleep_efficiency_avg: float | None = Field(
        default=None,
        description="周均睡眠效率(%)",
    )

    valid_days: int | None = Field(default=None, description="有效数据天数")
    total_days: int | None = Field(default=None, description="总天数(通常为7)")


class WeeklyHealthSummary(WeeklyHealthSummaryBase, table=True):
    """每周健康趋势表模型.

    数据库表名:weekly_health_summary
    唯一约束:week_start
    """

    __tablename__ = "weekly_health_summary"
    __table_args__ = (
        UniqueConstraint("week_start", name="uk_week_start"),
        {"extend_existing": True},
    )

    id: int | None = Field(default=None, primary_key=True, description="记录ID")
    week_start: date = Field(..., description="周一日期")
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


class FoodProductBase(SQLModel):
    """食品包装目录基础模型 - 有包装信息的商品详细档案.

    用于记录食品的精确营养数据(来自营养标签/配料表), 供 meal_record 提取时查询引用.
    仅在有明确包装信息时录入, 日常饮食描述不归此类.
    """

    product_id: str = Field(
        ...,
        description="商品稳定ID(基于品牌_商品名_规格生成, 同一商品多次购买共享)",
    )
    name: str = Field(..., description="商品名称")
    brand: str | None = Field(default=None, description="品牌")
    weight_per_unit: float | None = Field(default=None, description="单位重量(g)")
    ingredients: str | None = Field(default=None, description="成分列表")
    allergens: list[str] | None = Field(default=None, description="过敏原列表")
    barcode: str | None = Field(default=None, description="条形码")
    round_number: int | None = Field(default=None, description="来源对话轮次")
    source: str = Field(
        default="conversation_extraction",
        description="数据来源: conversation_extraction(对话提取) / external_import(外部导入)",
    )


class FoodProduct(FoodProductBase, table=True):
    """食品包装目录表模型.

    数据库表名:food_products
    用途: meal_record提取时查询精确营养数据, 无匹配时LLM推断.
    """

    __tablename__ = "food_products"
    __table_args__ = {"extend_existing": True}

    id: int | None = Field(default=None, primary_key=True, description="记录ID")
    nutrition_per_100g: dict[str, Any] = Field(
        ...,
        sa_column=Column(JSON),
        description="每100g营养成分(JSON)",
    )
    allergens: list[str] | None = Field(
        default=None,
        sa_column=Column(JSON),
        description="过敏原列表",
    )
    created_at: datetime | None = Field(
        default_factory=datetime.utcnow,
        sa_column=Column(DateTime, server_default=text("CURRENT_TIMESTAMP")),
        description="创建时间",
    )

    class Config:
        """SQLModel配置."""

        from_attributes = True


class ShoppingItemBase(SQLModel):
    """购物清单基础模型 - 简单记录用户购买的食材.

    目的是告诉Agent用户有什么食材可用, 辅助饮食建议.
    不涉及营养计算, 营养数据由 food_products 目录提供.
    """

    name: str = Field(..., description="商品/食材名称")
    quantity: int | None = Field(default=None, description="数量(可选)")
    purchase_date: datetime = Field(..., description="购买日期")
    notes: str | None = Field(default=None, description="备注")
    round_number: int | None = Field(default=None, description="来源对话轮次")
    source: str = Field(
        default="conversation_extraction",
        description="数据来源: conversation_extraction(对话提取) / external_import(外部导入)",
    )


class ShoppingItem(ShoppingItemBase, table=True):
    """购物清单表模型.

    数据库表名:shopping_items
    简化模型: 只记录名称,数量,购买日期, 供Agent了解用户可用食材.
    """

    __tablename__ = "shopping_items"
    __table_args__ = {"extend_existing": True}

    id: int | None = Field(default=None, primary_key=True, description="商品ID")
    created_at: datetime | None = Field(
        default_factory=datetime.utcnow,
        sa_column=Column(DateTime, server_default=text("CURRENT_TIMESTAMP")),
        description="创建时间",
    )

    class Config:
        """SQLModel配置."""

        from_attributes = True


class WorkoutRecordBase(SQLModel):
    """运动记录基础模型."""

    workout_type: str = Field(
        ...,
        description="运动类型: Running/Cycling/Swimming/Weightlifting等",
    )
    duration: float = Field(..., description="持续时间(分钟)")
    distance: float | None = Field(default=None, description="距离(km)")
    calories: float | None = Field(default=None, description="消耗卡路里")
    heart_rate_avg: float | None = Field(default=None, description="平均心率")
    heart_rate_max: float | None = Field(default=None, description="最大心率")
    start_time: datetime = Field(..., description="开始时间")
    end_time: datetime | None = Field(default=None, description="结束时间")
    active_energy_kj: float | None = Field(default=None, description="活动能量(kJ)")
    intensity: float | None = Field(default=None, description="运动强度(kcal/hr·kg)")
    avg_speed_kmh: float | None = Field(default=None, description="平均速度(km/h)")
    max_speed_kmh: float | None = Field(default=None, description="最大速度(km/h)")
    steps: int | None = Field(default=None, description="步数")
    cadence_spm: float | None = Field(default=None, description="步频(spm)")
    temperature: float | None = Field(default=None, description="环境温度(degC)")
    humidity: float | None = Field(default=None, description="湿度(%)")
    location: str | None = Field(default=None, description="位置(室内/室外等)")
    round_number: int | None = Field(
        default=None,
        description="来源对话轮次(方便Agent定向查询)",
    )
    notes: str | None = Field(default=None, description="训练备注")
    source: str = Field(
        default="conversation_extraction",
        description="数据来源: conversation_extraction(对话提取) / external_import(外部导入)",
    )


class WorkoutRecord(WorkoutRecordBase, table=True):
    """运动记录表模型.

    数据库表名:workout_records
    唯一约束:start_time + workout_type
    """

    __tablename__ = "workout_records"
    __table_args__ = (
        UniqueConstraint("start_time", "workout_type", name="uk_start_time_type"),
        {"extend_existing": True},
    )

    id: int | None = Field(default=None, primary_key=True, description="记录ID")
    exercises: list[dict[str, Any]] | None = Field(
        default=None,
        sa_column=Column(JSON),
        description='训练动作明细(JSON): [{"name":"深蹲","sets":4,"reps":12,"weight_kg":60}]',
    )
    created_at: datetime | None = Field(
        default_factory=datetime.utcnow,
        sa_column=Column(DateTime, server_default=text("CURRENT_TIMESTAMP")),
        description="创建时间",
    )

    class Config:
        """SQLModel配置."""

        from_attributes = True


class MealRecordBase(SQLModel):
    """摄入记录基础模型."""

    meal_type: str | None = Field(
        default=None,
        description="餐型标识(可选): breakfast/lunch/dinner/snack/下午茶等, 不强制要求",
    )
    meal_date: date = Field(..., description="用餐日期")
    meal_time: str | None = Field(
        default=None,
        description="具体用餐时间(可选, 如'08:30'), 用户明确给出时填写",
    )
    total_calories: float | None = Field(default=None, description="总卡路里")
    total_protein: float | None = Field(default=None, description="总蛋白质(g)")
    total_carbs: float | None = Field(default=None, description="总碳水化合物(g)")
    total_fat: float | None = Field(default=None, description="总脂肪(g)")
    nutrition_source: str = Field(
        default="estimated",
        description="营养数据来源: estimated(推算)/labeled(包装标注)",
    )
    round_number: int | None = Field(
        default=None,
        description="来源对话轮次(方便Agent定向查询)",
    )
    notes: str | None = Field(default=None, description="备注")
    source: str = Field(
        default="conversation_extraction",
        description="数据来源: conversation_extraction(对话提取) / external_import(外部导入)",
    )


class MealRecord(MealRecordBase, table=True):
    """摄入记录表模型.

    数据库表名:meal_records
    """

    __tablename__ = "meal_records"
    __table_args__ = {"extend_existing": True}

    id: int | None = Field(default=None, primary_key=True, description="记录ID")
    items: list[dict[str, Any]] = Field(
        ...,
        sa_column=Column(JSON),
        description="摄入项列表(JSON)",
    )
    created_at: datetime | None = Field(
        default_factory=datetime.utcnow,
        sa_column=Column(DateTime, server_default=text("CURRENT_TIMESTAMP")),
        description="创建时间",
    )

    class Config:
        """SQLModel配置."""

        from_attributes = True


class WorkoutSample(SQLModel, table=True):
    """运动时间序列采样数据表.

    数据库表名:workout_samples
    存储运动详情CSV中的逐分钟/逐秒采样数据.
    通过 workout_start_time 关联 workout_records.
    """

    __tablename__ = "workout_samples"
    __table_args__ = (
        Index(
            "ix_ws_workout_metric_time",
            "workout_start_time",
            "metric_type",
            "sample_time",
        ),
        {"extend_existing": True},
    )

    id: int | None = Field(default=None, primary_key=True, description="记录ID")
    workout_start_time: datetime = Field(
        ...,
        description="运动开始时间, 关联workout_records.start_time",
    )
    workout_type: str = Field(..., description="运动类型")
    metric_type: str = Field(
        ...,
        description="指标类型: heart_rate/heart_rate_recovery/steps/distance/active_energy/resting_energy",
    )
    sample_time: datetime = Field(..., description="采样时间点")
    value_min: float | None = Field(
        default=None,
        description="最小值(HR/HR恢复: bpm; 其他: NULL)",
    )
    value_max: float | None = Field(
        default=None,
        description="最大值(HR/HR恢复: bpm; 其他: NULL)",
    )
    value_avg: float | None = Field(
        default=None,
        description="平均值(HR: bpm; 步数: count; 距离: km; 能量: kJ)",
    )
    source: str = Field(
        default="apple_health",
        description="数据来源设备",
    )
    created_at: datetime | None = Field(
        default_factory=datetime.utcnow,
        sa_column=Column(DateTime, server_default=text("CURRENT_TIMESTAMP")),
        description="创建时间",
    )

    class Config:
        """SQLModel配置."""

        from_attributes = True


class ECGRecord(SQLModel, table=True):
    """心电图记录表.

    数据库表名:ecg_records
    存储Apple Watch ECG测量结果.
    """

    __tablename__ = "ecg_records"
    __table_args__ = {"extend_existing": True}

    id: int | None = Field(default=None, primary_key=True, description="记录ID")
    start_time: datetime = Field(..., description="测量开始时间")
    end_time: datetime | None = Field(default=None, description="测量结束时间")
    classification: str | None = Field(
        default=None,
        description="心律分类(如: 窦性心律/房颤)",
    )
    symptoms: str | None = Field(default=None, description="用户记录的症状")
    note: str | None = Field(default=None, description="用户备注")
    source: str = Field(
        default="apple_health",
        description="数据来源",
    )
    created_at: datetime | None = Field(
        default_factory=datetime.utcnow,
        sa_column=Column(DateTime, server_default=text("CURRENT_TIMESTAMP")),
        description="创建时间",
    )

    class Config:
        """SQLModel配置."""

        from_attributes = True


__all__ = [
    "DailyHealthSummary",
    "DailyHealthSummaryBase",
    "ECGRecord",
    "FoodProduct",
    "FoodProductBase",
    "MealRecord",
    "MealRecordBase",
    "MedicalReport",
    "MedicalReportBase",
    "ShoppingItem",
    "ShoppingItemBase",
    "WeeklyHealthSummary",
    "WeeklyHealthSummaryBase",
    "WeightRecord",
    "WeightRecordBase",
    "WorkoutRecord",
    "WorkoutRecordBase",
    "WorkoutSample",
]
