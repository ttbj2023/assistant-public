"""健康数据工具共享逻辑 - 组合替代继承.

Service 访问器: HealthDataServiceAccessor (带缓存)
指标标签: METRIC_LABELS / WEEKLY_METRIC_LABELS
纯函数: has_value / first_day_of_month_n_ago / 格式化函数组
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

from src.auth.auth_manager import get_auth_manager
from src.core.datetime_utils import now_utc, to_user_tz

logger = logging.getLogger(__name__)

METRIC_LABELS: dict[str, str] = {
    # 活动
    "steps": "步数",
    "active_energy_kcal": "活动能量(kcal)",
    "basal_energy_kcal": "基础代谢(kcal)",
    "distance_km": "距离(km)",
    "apple_exercise_minutes": "运动时间(分钟)",
    "stand_hours": "站立小时数",
    # 体征
    "body_mass_kg": "体重(kg)",
    "body_fat_pct": "体脂率(%)",
    "muscle_mass_kg": "肌肉量(kg)",
    "resting_hr_bpm": "静息心率(bpm)",
    "hrv_ms": "HRV(ms)",
    "vo2_max": "VO2Max(ml/kg/min)",
    "avg_hr_bpm": "日均心率(bpm)",
    "min_hr_bpm": "日最低心率(bpm)",
    "max_hr_bpm": "日最高心率(bpm)",
    "blood_oxygen_pct": "血氧(%)",
    "wrist_temperature": "手腕温度(°C)",
    "respiratory_rate": "呼吸频率(次/分)",
    # 睡眠
    "sleep_duration_hours": "睡眠时长(小时)",
    "sleep_efficiency": "睡眠效率(%)",
    "asleep_minutes": "入睡时长(分钟)",
    "deep_sleep_minutes": "深睡时长(分钟)",
    "rem_sleep_minutes": "REM睡眠(分钟)",
    "core_sleep_minutes": "核心睡眠(分钟)",
    "awake_minutes": "清醒时长(分钟)",
    "flights_climbed": "爬楼层数",
    "sunlight_minutes": "日照(分钟)",
    # 7日滚动均值
    "weight_7d_avg": "7日体重均值(kg)",
    "steps_7d_avg": "7日步数均值",
    "resting_hr_7d_avg": "7日心率均值(bpm)",
    "hrv_7d_avg": "7日HRV均值(ms)",
    "sleep_7d_avg": "7日睡眠均值(分钟)",
    "sleep_efficiency_7d_avg": "7日睡眠效率均值(%)",
    "exercise_7d_total": "7日运动总和(分钟)",
}

#  period=weekly 时可查询的周汇总指标
WEEKLY_METRIC_LABELS: dict[str, str] = {
    "steps_total": "周总步数",
    "steps_daily_avg": "周日均步数",
    "active_energy_total": "周总活动能量(kcal)",
    "distance_total": "周总距离(km)",
    "exercise_minutes_total": "周总运动(分钟)",
    "body_mass_avg": "周均体重(kg)",
    "resting_hr_avg": "周均心率(bpm)",
    "hrv_avg": "周均HRV(ms)",
    "vo2_max_avg": "周均VO2Max",
    "sleep_duration_avg": "周均睡眠(小时)",
    "sleep_efficiency_avg": "周均睡眠效率(%)",
    "stand_hours_total": "周总站立小时",
}


def has_value(val: Any) -> bool:
    """检查值是否有效(非None且非零)."""
    return val is not None and val != 0


def first_day_of_month_n_ago(ref: date, months: int) -> date:
    """返回 ref 所在月份往前第 months 个月的 1 号.

    用整数月运算实现, 避免 timedelta(days=1) 只退一天导致 offset>1 时
    始终落在上个月的 bug.
    """
    total = ref.year * 12 + (ref.month - 1) - months
    return date(total // 12, total % 12 + 1, 1)


def user_today(user_id: str) -> date:
    """返回用户本地时区的当前日期.

    统一健康数据子系统的"今天"基准, 避免工具/审计按服务器时区取 date.today()
    导致跨时区用户在日期边界附近数据错位.
    """
    tz = get_auth_manager().get_user_timezone(user_id)
    return to_user_tz(now_utc(), tz).date()


def format_brief_fields(s: Any) -> list[str]:
    """将单日数据格式化为紧凑的多行文本."""
    lines = []

    activity_parts = []
    if has_value(s.steps):
        activity_parts.append(f"步数{s.steps}")
    if has_value(s.active_energy_kcal):
        activity_parts.append(f"活动{s.active_energy_kcal:.0f}kcal")
    if has_value(s.apple_exercise_minutes):
        activity_parts.append(f"运动{s.apple_exercise_minutes:.0f}min")
    if has_value(s.distance_km):
        activity_parts.append(f"{s.distance_km:.1f}km")
    if has_value(s.stand_hours):
        activity_parts.append(f"站立{s.stand_hours}h")
    if activity_parts:
        lines.append("活动: " + ", ".join(activity_parts))

    vital_parts = []
    if has_value(s.body_mass_kg):
        vital_parts.append(f"体重{s.body_mass_kg:.1f}kg")
        if has_value(s.body_fat_pct):
            vital_parts.append(f"体脂{s.body_fat_pct:.1f}%")
        if has_value(s.muscle_mass_kg):
            vital_parts.append(f"肌肉{s.muscle_mass_kg:.1f}kg")
    if has_value(s.resting_hr_bpm):
        vital_parts.append(f"心率{s.resting_hr_bpm:.0f}")
    if has_value(s.hrv_ms):
        vital_parts.append(f"HRV{s.hrv_ms:.0f}")
    if has_value(s.vo2_max):
        vital_parts.append(f"VO2{s.vo2_max:.1f}")
    if has_value(s.blood_oxygen_pct):
        vital_parts.append(f"血氧{s.blood_oxygen_pct:.0f}%")
    if vital_parts:
        lines.append("体征: " + ", ".join(vital_parts))

    sleep_parts = []
    if has_value(s.sleep_duration_hours):
        sleep_parts.append(f"睡眠{s.sleep_duration_hours:.1f}h")
    if has_value(s.sleep_efficiency):
        sleep_parts.append(f"效率{s.sleep_efficiency:.0f}%")
    if has_value(s.asleep_minutes):
        sleep_parts.append(f"入睡{s.asleep_minutes:.0f}min")
    if has_value(s.deep_sleep_minutes):
        sleep_parts.append(f"深睡{s.deep_sleep_minutes:.0f}min")
    if has_value(s.rem_sleep_minutes):
        sleep_parts.append(f"REM{s.rem_sleep_minutes:.0f}min")
    if has_value(s.core_sleep_minutes):
        sleep_parts.append(f"核心{s.core_sleep_minutes:.0f}min")
    if has_value(s.awake_minutes):
        sleep_parts.append(f"清醒{s.awake_minutes}min")
    if s.bed_time is not None:
        sleep_parts.append(f"入睡{s.bed_time.strftime('%H:%M')}")
    if s.wake_time is not None:
        sleep_parts.append(f"起床{s.wake_time.strftime('%H:%M')}")
    if sleep_parts:
        lines.append("睡眠: " + ", ".join(sleep_parts))

    avg_parts = []
    if has_value(s.weight_7d_avg):
        avg_parts.append(f"7d体重{s.weight_7d_avg:.1f}kg")
    if has_value(s.steps_7d_avg):
        avg_parts.append(f"7d步数{s.steps_7d_avg:.0f}")
    if has_value(s.resting_hr_7d_avg):
        avg_parts.append(f"7d心率{s.resting_hr_7d_avg:.0f}")
    if has_value(s.hrv_7d_avg):
        avg_parts.append(f"7dHRV{s.hrv_7d_avg:.0f}")
    if has_value(s.sleep_7d_avg):
        avg_parts.append(f"7d睡眠{s.sleep_7d_avg:.0f}min")
    if has_value(s.sleep_efficiency_7d_avg):
        avg_parts.append(f"7d效率{s.sleep_efficiency_7d_avg:.0f}%")
    if has_value(s.exercise_7d_total):
        avg_parts.append(f"7d运动{s.exercise_7d_total:.0f}min")
    if avg_parts:
        lines.append("均值: " + ", ".join(avg_parts))

    return lines


def format_daily_detail(s: Any) -> str:
    """格式化单日健康汇总详情."""
    lines = [f"=== {s.record_date} 健康日报 ==="]
    lines.extend(format_brief_fields(s))
    return "\n".join(lines)


def format_daily_brief(s: Any) -> str:
    """格式化单日健康汇总简要(多天列表用)."""
    parts = [f"{s.record_date}:"]
    if has_value(s.steps):
        parts.append(f"{s.steps}步")
    if has_value(s.active_energy_kcal):
        parts.append(f"{s.active_energy_kcal:.0f}kcal")
    if has_value(s.body_mass_kg):
        parts.append(f"{s.body_mass_kg:.1f}kg")
    if has_value(s.resting_hr_bpm):
        parts.append(f"心率{s.resting_hr_bpm:.0f}")
    if has_value(s.sleep_duration_hours):
        parts.append(f"睡眠{s.sleep_duration_hours:.1f}h")
    if has_value(s.apple_exercise_minutes):
        parts.append(f"运动{s.apple_exercise_minutes:.0f}min")
    return "- " + ", ".join(parts)


def format_nutrition_detail(target: date, nutrition: dict) -> str:
    """格式化单日营养详情. 营养全0时隐藏汇总行, 避免误导."""
    lines = [f"=== {target} 饮食记录 ==="]
    cal = nutrition.get("calories", 0) or 0
    protein = nutrition.get("protein", 0) or 0
    carbs = nutrition.get("carbs", 0) or 0
    fat = nutrition.get("fat", 0) or 0
    if cal or protein or carbs or fat:
        lines.append(
            f"营养汇总: {cal:.0f}kcal, "
            f"蛋白质{protein:.0f}g, 碳水{carbs:.0f}g, 脂肪{fat:.0f}g "
            f"({nutrition['meal_count']}餐)",
        )
    else:
        lines.append(f"共 {nutrition['meal_count']} 餐(营养数据暂未录入)")

    meals = nutrition.get("meals", [])
    if meals:
        lines.append("\n各餐详情:")
        for meal in meals:
            meal_type = meal.get("meal_type", "未分类")
            meal_time = meal.get("meal_time", "")
            items = meal.get("items", [])
            header = f"- [{meal_type}]"
            if meal_time:
                header += f" {meal_time}"
            lines.append(header)
            for item in items:
                name = item.get("name", "?")
                qty = item.get("quantity", "")
                parts = [f"  {name}"]
                if qty:
                    parts[0] += f" x{qty}"
                if item.get("calories"):
                    parts.append(f"{item['calories']:.0f}kcal")
                lines.append(", ".join(parts))

    return "\n".join(lines) if lines else ""


class HealthDataServiceAccessor:
    """健康数据 Service 访问器 (组合, 带缓存)."""

    def __init__(self, user_id: str, thread_id: str, agent_id: str) -> None:
        self._user_id = user_id
        self._thread_id = thread_id
        self._agent_id = agent_id
        self._service: Any = None

    async def get_service(self) -> Any:
        """获取 HealthDataService 实例 (带缓存)."""
        if self._service is not None:
            return self._service
        from src.storage.service.health_service import get_health_service

        service = await get_health_service(
            self._user_id,
            self._thread_id,
            agent_id=self._agent_id,
        )
        self._service = service
        return service


__all__ = [
    "METRIC_LABELS",
    "WEEKLY_METRIC_LABELS",
    "HealthDataServiceAccessor",
    "first_day_of_month_n_ago",
    "format_brief_fields",
    "format_daily_brief",
    "format_daily_detail",
    "format_nutrition_detail",
    "has_value",
    "user_today",
]
