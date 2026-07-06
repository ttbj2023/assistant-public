"""健康数据管理工具 - Agent主动查询用户健康数据.

数据来源: 外部设备导入(Health Auto Export)和对话自动提取, 含每日汇总/运动记录/周汇总等.
通过 HealthDataService 访问数据, 不直接操作 DAO.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any, override

from pydantic import BaseModel, ConfigDict, Field

from src.tools.shared.base_internal_tool import BaseInternalTool

logger = logging.getLogger(__name__)

_METRIC_LABELS: dict[str, str] = {
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

# get_trend period=weekly 时可查询的周汇总指标
_WEEKLY_METRIC_LABELS: dict[str, str] = {
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


class HealthDataManagerRequest(BaseModel):
    """健康数据管理工具请求模型."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"additionalProperties": False},
    )

    action: str = Field(
        ...,
        description=(
            "操作类型: "
            "get_overview(健康快照), "
            "get_daily(每日明细), "
            "get_trend(指标趋势), "
            "get_comparison(时段对比), "
            "get_workout(运动记录), "
            "get_meals(饮食记录), "
            "get_report(体检报告), "
            "get_shopping(购物清单)"
        ),
    )
    metric: str | None = Field(
        None,
        description=(
            "指标名(get_trend/get_comparison时必需). "
            "可用指标: steps, body_mass_kg, resting_hr_bpm, hrv_ms, "
            "sleep_duration_hours, sleep_efficiency, apple_exercise_minutes, "
            "weight_7d_avg, steps_7d_avg 等"
        ),
    )
    days: int = Field(
        default=7,
        ge=1,
        le=365,
        description=(
            "查询天数: "
            "get_daily默认7最大90, "
            "get_trend默认30最大365, "
            "get_workout list默认30/stats默认90, "
            "get_meals默认7, "
            "get_shopping默认30"
        ),
    )
    target_date: str | None = Field(
        None,
        description="指定日期(YYYY-MM-DD, get_daily/get_meals查单日时使用)",
    )
    period: str | None = Field(
        None,
        description="时间粒度(get_trend): daily(默认) 或 weekly",
    )
    period_type: str | None = Field(
        None,
        description="时段类型(get_comparison): week或month, 默认week",
    )
    period_offset: int = Field(
        default=0,
        ge=0,
        description="时段偏移(get_comparison): 0=当前, 1=上一期, 2=上两期",
    )
    mode: str | None = Field(
        None,
        description="查询模式(get_workout): list(运动列表) 或 stats(统计汇总), 默认list",
    )
    workout_type: str | None = Field(
        None,
        description="运动类型筛选(get_workout): 户外 步行/太极拳/户外 骑行等",
    )
    limit: int | None = Field(
        None,
        ge=1,
        le=100,
        description="返回记录数上限(get_workout list模式, 默认20)",
    )


class HealthDataManagerTool(BaseInternalTool):
    """健康数据管理工具."""

    name: str = "health_data_manager"
    summary: str = "查询用户健康数据, 包括体征/睡眠/运动/饮食/体检报告"
    description: str = """查询用户的健康数据. 数据来源包括外部设备导入和对话中自动提取的记录.

操作(action):
- get_overview: 健康快照. 最新日数据+7日均值+数据新鲜度. 无需参数, 推荐首次查询使用
- get_daily: 每日明细(活动/体征/睡眠/7日均值). 参数: target_date(单日) 或 days(最近N天, 默认7)
- get_trend: 单指标趋势. 参数: metric(必需), days(默认30), period(daily/weekly)
- get_comparison: 时段对比(周/月环比). 参数: metric(必需), period_type(week/month), period_offset
- get_workout: 运动记录. 参数: mode(list/stats), days, workout_type, limit
- get_meals: 饮食记录和营养摄入. 参数: target_date(单日) 或 days(最近N天, 默认7)
- get_report: 体检报告详情. 无需参数, 返回最新报告完整内容
- get_shopping: 购物清单/食材库存. 参数: days(最近N天, 默认30)

可用指标: steps, active_energy_kcal, distance_km, apple_exercise_minutes, body_mass_kg, body_fat_pct, resting_hr_bpm, hrv_ms, vo2_max, sleep_duration_hours, sleep_efficiency, asleep_minutes, deep_sleep_minutes, rem_sleep_minutes, core_sleep_minutes, awake_minutes, weight_7d_avg, steps_7d_avg, resting_hr_7d_avg, hrv_7d_avg, sleep_7d_avg, sleep_efficiency_7d_avg, exercise_7d_total

示例:
趋势: {"action": "get_trend", "metric": "body_mass_kg", "days": 30}
概览: {"action": "get_overview"}"""
    args_schema: type[HealthDataManagerRequest] = HealthDataManagerRequest

    def __init__(self, user_id: str, thread_id: str, **kwargs: Any) -> None:
        super().__init__(user_id, thread_id, **kwargs)
        self._health_service: Any = None

    async def _get_service(self) -> Any:
        if self._health_service is not None:
            return self._health_service

        from ...storage.service.health_service import get_health_service

        service = await get_health_service(
            self.user_id,
            self.thread_id,
            agent_id=self.agent_id,
        )
        self._health_service = service
        return service

    @override
    async def _arun(self, **kwargs: Any) -> str:
        try:
            request = HealthDataManagerRequest(**kwargs)
            action = request.action

            handlers: dict[str, Any] = {
                "get_overview": self._get_overview,
                "get_daily": lambda: self._get_daily(request),
                "get_trend": lambda: self._get_trend(request),
                "get_comparison": lambda: self._get_comparison(request),
                "get_workout": lambda: self._get_workout(request),
                "get_meals": lambda: self._get_meals(request),
                "get_report": self._get_report,
                "get_shopping": lambda: self._get_shopping(request),
            }

            handler = handlers.get(action)
            if not handler:
                return (
                    f"错误: 不支持的操作 '{action}', 可用: {', '.join(handlers.keys())}"
                )
            return await handler()

        except Exception as e:
            logger.error("健康数据管理操作失败: %s", e)
            return self._format_error(e)

    # ========== get_overview: 健康快照 ==========

    async def _get_overview(self) -> str:
        """健康快照: 最新日完整数据 + 7日均值 + 数据新鲜度."""
        service = await self._get_service()

        end_date = date.today()
        start_7d = end_date - timedelta(days=6)

        summaries = await service.get_daily_summaries(start_7d, end_date)
        coverage = await service.get_data_coverage()
        latest_report = await service.get_latest_report()
        activity_summary = await service.get_weekly_activity_summary()

        lines = ["=== 健康快照 ==="]

        # 数据覆盖
        daily_meta = coverage.get("daily", {})
        total_days = daily_meta.get("total", 0)
        date_range = daily_meta.get("date_range", {})
        if total_days > 0:
            lines.append(
                f"数据范围: {date_range.get('start', '?')} ~ {date_range.get('end', '?')} ({total_days}天)",
            )
        lines.append(f"近7天有数据: {len(summaries)}/7天")

        # 最新一天完整数据 (取最近有数据的一天)
        latest_s = summaries[0] if summaries else None
        if latest_s:
            lines.append(f"\n--- {latest_s.record_date} ---")
            lines.extend(self._format_brief_fields(latest_s))

        # 数据新鲜度
        if summaries:
            freshness = []
            for metric_key, label_short in [
                ("body_mass_kg", "体重"),
                ("resting_hr_bpm", "心率"),
                ("sleep_duration_hours", "睡眠"),
                ("steps", "步数"),
            ]:
                last_day = next(
                    (
                        s.record_date
                        for s in summaries
                        if getattr(s, metric_key, None) is not None
                    ),
                    None,
                )
                if last_day:
                    days_ago = (end_date - last_day).days
                    if days_ago > 1:
                        freshness.append(f"{label_short}最新{days_ago}天前")
            if freshness:
                lines.append(f"\n数据新鲜度: {', '.join(freshness)}")

        # 运动摘要
        if activity_summary.get("status") == "success":
            total = activity_summary.get("total_workouts", 0)
            dur = activity_summary.get("total_duration_minutes", 0)
            lines.append(f"\n近期运动: {total}次, 共{dur:.0f}分钟")

        # 体检报告
        if latest_report:
            lines.append(
                f"体检报告: 最新{latest_report.report_date.strftime('%Y-%m-%d')}, {len(latest_report.report_data)}项",
            )

        return "\n".join(lines)

    # ========== get_daily: 每日明细 ==========

    async def _get_daily(self, request: HealthDataManagerRequest) -> str:
        """每日明细: 指定日期或日期范围."""
        service = await self._get_service()

        if request.target_date:
            target = date.fromisoformat(request.target_date)
            summary = await service.get_daily_summary(target)
            if not summary:
                return f"日期 {request.target_date} 无健康数据"
            return self._format_daily_detail(summary)

        days = min(request.days or 7, 90)
        end_date = date.today()
        start_date = end_date - timedelta(days=days - 1)
        summaries = await service.get_daily_summaries(start_date, end_date)

        if not summaries:
            return f"最近{days}天无健康数据"

        lines = [f"每日明细(最近{days}天, {len(summaries)}天有数据):"]
        for s in summaries:
            lines.append(self._format_daily_brief(s))
        return "\n".join(lines)

    # ========== get_trend: 指标趋势 ==========

    async def _get_trend(self, request: HealthDataManagerRequest) -> str:
        """单指标趋势: 日维度或周维度."""
        metric = request.metric
        if not metric:
            available = ", ".join(sorted(_METRIC_LABELS.keys()))
            return f"错误: 请指定metric参数. 可用指标: {available}"

        period = request.period or "daily"

        if period == "weekly":
            return await self._get_trend_weekly(request, metric)

        label = _METRIC_LABELS.get(metric, metric)
        service = await self._get_service()
        days = min(request.days or 30, 365)

        try:
            history = await service.get_metric_history(metric, days=days)
        except AttributeError:
            return f"错误: 不支持的指标 '{metric}'"

        if not history:
            return f"最近{days}天无{label}数据"

        values = [h["value"] for h in history]
        avg = sum(values) / len(values)
        latest = values[0]
        oldest = values[-1]
        change = latest - oldest

        lines = [
            f"{label}趋势(最近{days}天, {len(history)}个数据点):",
            f"- 最新: {latest:.1f} ({history[0]['date']})",
            f"- 均值: {avg:.1f}",
            f"- 变化: {change:+.1f} (从{oldest:.1f}到{latest:.1f})",
            f"- 最大: {max(values):.1f}, 最小: {min(values):.1f}",
        ]

        if len(history) > 5:
            lines.append(
                "- 近期: "
                + ", ".join(f"{h['date'][-5:]}={h['value']:.1f}" for h in history[:7]),
            )

        if len(history) >= 3:
            from datetime import datetime as dt

            dates_parsed = [dt.strptime(h["date"], "%Y-%m-%d").date() for h in history]
            gaps = []
            for i in range(len(dates_parsed) - 1):
                gap_days = (dates_parsed[i] - dates_parsed[i + 1]).days
                if gap_days > 3:
                    gaps.append(
                        f"{dates_parsed[i + 1]}~{dates_parsed[i]}缺{gap_days - 1}天",
                    )
            if gaps:
                lines.append(f"- 断档: {'; '.join(gaps[:3])}")

        return "\n".join(lines)

    async def _get_trend_weekly(
        self,
        request: HealthDataManagerRequest,
        metric: str,
    ) -> str:
        """周维度指标趋势."""
        label = _WEEKLY_METRIC_LABELS.get(metric, metric)
        service = await self._get_service()
        weeks = min(request.days or 12, 52)

        summaries = await service.get_weekly_summaries(limit=weeks)
        if not summaries:
            return f"最近{weeks}周无周汇总数据"

        values = []
        dates = []
        for s in summaries:
            val = getattr(s, metric, None)
            if val is not None:
                values.append(float(val))
                dates.append(str(s.week_start))

        if not values:
            return f"最近{weeks}周无{label}数据"

        avg = sum(values) / len(values)
        latest = values[0]
        oldest = values[-1]
        change = latest - oldest

        lines = [
            f"{label}周趋势(最近{weeks}周, {len(values)}个数据点):",
            f"- 最新: {latest:.1f} (周{dates[0]})",
            f"- 均值: {avg:.1f}",
            f"- 变化: {change:+.1f} (从{oldest:.1f}到{latest:.1f})",
            f"- 最大: {max(values):.1f}, 最小: {min(values):.1f}",
        ]

        if len(values) > 3:
            parts = [
                f"{d[-5:]}={v:.1f}" for d, v in zip(dates[:6], values[:6], strict=False)
            ]
            lines.append("- 近期: " + ", ".join(parts))

        return "\n".join(lines)

    # ========== get_comparison: 时段对比 ==========

    async def _get_comparison(self, request: HealthDataManagerRequest) -> str:
        """时段对比: 周环比/月环比."""
        metric = request.metric
        if not metric:
            return (
                "错误: 请指定metric参数(如 steps, body_mass_kg, sleep_duration_hours)"
            )

        label = _METRIC_LABELS.get(metric, metric)
        service = await self._get_service()

        period_type = request.period_type or "week"
        offset = max(request.period_offset or 0, 0)

        if period_type == "month":
            now = date.today()
            this_month_start = now.replace(day=1)
            if offset == 0:
                p1_start = this_month_start
                p1_end = now
            else:
                p1_start = this_month_start - timedelta(days=1)
                p1_start = p1_start.replace(day=1)
                p1_end = this_month_start - timedelta(days=1)

            p2_offset = offset + 1
            p2_end = p1_start - timedelta(days=1)
            p2_start = p2_end.replace(day=1)

            period_label_1 = f"{'本月' if offset == 0 else f'{offset}个月前'}"
            period_label_2 = f"{p2_offset}个月前"
        else:
            today = date.today()
            weekday = today.weekday()
            this_monday = today - timedelta(days=weekday)

            current_monday = this_monday - timedelta(weeks=offset)
            p1_start = current_monday
            p1_end = min(current_monday + timedelta(days=6), today)

            prev_monday = current_monday - timedelta(weeks=1)
            p2_start = prev_monday
            p2_end = prev_monday + timedelta(days=6)

            period_label_1 = f"{'本周' if offset == 0 else f'{offset}周前'}"
            period_label_2 = f"{offset + 1}周前"

        result = await service.get_metric_comparison(
            metric,
            p1_start,
            p1_end,
            p2_start,
            p2_end,
        )

        lines = [f"{label}时段对比:"]

        p1 = result.get("period1", {})
        p2 = result.get("period2", {})

        if p1.get("count", 0) == 0 and p2.get("count", 0) == 0:
            return f"对比时段内均无{label}数据"

        if p1.get("avg") is not None:
            lines.append(
                f"- {period_label_1}({p1['start']}~{p1['end']}): "
                f"均值{p1['avg']:.1f}, {p1['count']}个数据点",
            )
        else:
            lines.append(f"- {period_label_1}({p1['start']}~{p1['end']}): 无数据")

        if p2.get("avg") is not None:
            lines.append(
                f"- {period_label_2}({p2['start']}~{p2['end']}): "
                f"均值{p2['avg']:.1f}, {p2['count']}个数据点",
            )
        else:
            lines.append(f"- {period_label_2}({p2['start']}~{p2['end']}): 无数据")

        change_pct = result.get("change_pct")
        direction = result.get("direction", "no_data")
        if change_pct is not None:
            arrow = {"up": "↑", "down": "↓", "stable": "→"}.get(direction, "")
            lines.append(f"- 变化: {arrow} {change_pct:+.1f}%")

        return "\n".join(lines)

    # ========== get_workout: 运动记录 ==========

    async def _get_workout(self, request: HealthDataManagerRequest) -> str:
        """运动记录: 列表或统计."""
        mode = request.mode or "list"

        if mode == "stats":
            return await self._get_workout_stats(request)
        return await self._get_workout_list(request)

    async def _get_workout_list(self, request: HealthDataManagerRequest) -> str:
        """运动列表."""
        service = await self._get_service()
        days = min(request.days or 30, 365)
        workout_type = request.workout_type
        limit = request.limit or 20

        records = await service.get_workout_history_filtered(
            days=days,
            workout_type=workout_type,
            limit=limit,
        )

        if not records:
            type_hint = f" {workout_type}" if workout_type else ""
            return f"最近{days}天无{type_hint}运动记录"

        header = f"运动记录(最近{days}天"
        if workout_type:
            header += f", 类型: {workout_type}"
        header += f", {len(records)}条):"

        lines = [header]
        for r in records[:limit]:
            parts = [f"- {r.start_time.strftime('%Y-%m-%d %H:%M')}"]
            parts.append(f"{r.workout_type}")
            parts.append(f"{r.duration:.0f}min")
            if r.distance:
                parts.append(f"{r.distance:.1f}km")
            if r.calories:
                parts.append(f"{r.calories:.0f}kcal")
            if r.heart_rate_avg:
                parts.append(f"心率{r.heart_rate_avg:.0f}")
            lines.append(", ".join(parts))

        return "\n".join(lines)

    async def _get_workout_stats(self, request: HealthDataManagerRequest) -> str:
        """运动统计汇总."""
        service = await self._get_service()
        days = request.days or 90
        workout_type = request.workout_type

        stats = await service.get_workout_stats(days=days, workout_type=workout_type)

        if stats.get("status") != "success" or stats.get("total_count", 0) == 0:
            type_hint = f" {workout_type}" if workout_type else ""
            return f"最近{days}天无{type_hint}运动记录"

        lines = [f"运动统计(最近{days}天):"]
        lines.append(
            f"- 总计: {stats['total_count']}次, {stats['total_duration_minutes']:.0f}分钟",
        )
        lines.append(f"- 频率: {stats['freq_per_week']}次/周")

        type_dist = stats.get("type_distribution", {})
        if type_dist:
            lines.append("- 类型分布:")
            for wtype, info in type_dist.items():
                lines.append(
                    f"  {wtype}: {info['count']}次, {info['duration']:.0f}分钟",
                )

        return "\n".join(lines)

    # ========== get_meals: 饮食记录 ==========

    async def _get_meals(self, request: HealthDataManagerRequest) -> str:
        """饮食记录和营养摄入."""
        service = await self._get_service()

        if request.target_date:
            target = date.fromisoformat(request.target_date)
            nutrition = await service.get_nutrition_summary(target)
            if nutrition.get("status") == "no_data":
                return f"{request.target_date} 无饮食记录"
            return self._format_nutrition_detail(target, nutrition)

        days = min(request.days or 7, 90)
        end_date = date.today()

        lines = [f"饮食记录(最近{days}天):"]
        has_data = False
        for i in range(days):
            d = end_date - timedelta(days=i)
            nutrition = await service.get_nutrition_summary(d)
            if nutrition.get("status") == "no_data":
                continue
            has_data = True
            cal = nutrition.get("calories", 0)
            protein = nutrition.get("protein", 0)
            carbs = nutrition.get("carbs", 0)
            fat = nutrition.get("fat", 0)
            meals = nutrition.get("meal_count", 0)
            lines.append(
                f"- {d}: {meals}餐, "
                f"{cal:.0f}kcal (蛋白{protein:.0f}g 碳水{carbs:.0f}g 脂肪{fat:.0f}g)",
            )

        if not has_data:
            return f"最近{days}天无饮食记录"
        return "\n".join(lines)

    def _format_nutrition_detail(self, target: date, nutrition: dict) -> str:
        """格式化单日营养详情."""
        lines = [f"=== {target} 饮食记录 ==="]
        lines.append(
            f"营养汇总: {nutrition['calories']:.0f}kcal, "
            f"蛋白质{nutrition['protein']:.0f}g, "
            f"碳水{nutrition['carbs']:.0f}g, "
            f"脂肪{nutrition['fat']:.0f}g ({nutrition['meal_count']}餐)",
        )

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

        return "\n".join(lines)

    # ========== get_report: 体检报告 ==========

    async def _get_report(self) -> str:
        """体检报告详情."""
        service = await self._get_service()
        result = await service.get_report_detail()

        if result.get("status") == "no_data":
            return "暂无体检报告"

        latest = result.get("latest", {})
        report_date = latest.get("report_date", "?")
        report_type = latest.get("report_type", "")
        data = latest.get("data", {})

        lines = [f"=== 体检报告 ({report_date}) ==="]
        if report_type:
            lines.append(f"类型: {report_type}")

        if data:
            lines.append(f"\n报告数据({len(data)}项):")
            for key, value in data.items():
                lines.append(f"- {key}: {value}")

        history = result.get("history", {})
        total = history.get("total_reports", 0)
        if total > 1:
            lines.append(f"\n历史报告: 共{total}份")

        return "\n".join(lines)

    # ========== get_shopping: 购物清单 ==========

    async def _get_shopping(self, request: HealthDataManagerRequest) -> str:
        """购物清单/食材库存."""
        service = await self._get_service()
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

    # ========== 格式化方法 ==========

    def _format_brief_fields(self, s: Any) -> list[str]:
        """将单日数据格式化为紧凑的多行文本."""
        lines = []

        activity_parts = []
        if _v(s.steps):
            activity_parts.append(f"步数{s.steps}")
        if _v(s.active_energy_kcal):
            activity_parts.append(f"活动{s.active_energy_kcal:.0f}kcal")
        if _v(s.apple_exercise_minutes):
            activity_parts.append(f"运动{s.apple_exercise_minutes:.0f}min")
        if _v(s.distance_km):
            activity_parts.append(f"{s.distance_km:.1f}km")
        if _v(s.stand_hours):
            activity_parts.append(f"站立{s.stand_hours}h")
        if activity_parts:
            lines.append("活动: " + ", ".join(activity_parts))

        vital_parts = []
        if _v(s.body_mass_kg):
            vital_parts.append(f"体重{s.body_mass_kg:.1f}kg")
            if _v(s.body_fat_pct):
                vital_parts.append(f"体脂{s.body_fat_pct:.1f}%")
            if _v(s.muscle_mass_kg):
                vital_parts.append(f"肌肉{s.muscle_mass_kg:.1f}kg")
        if _v(s.resting_hr_bpm):
            vital_parts.append(f"心率{s.resting_hr_bpm:.0f}")
        if _v(s.hrv_ms):
            vital_parts.append(f"HRV{s.hrv_ms:.0f}")
        if _v(s.vo2_max):
            vital_parts.append(f"VO2{s.vo2_max:.1f}")
        if _v(s.blood_oxygen_pct):
            vital_parts.append(f"血氧{s.blood_oxygen_pct:.0f}%")
        if vital_parts:
            lines.append("体征: " + ", ".join(vital_parts))

        sleep_parts = []
        if _v(s.sleep_duration_hours):
            sleep_parts.append(f"睡眠{s.sleep_duration_hours:.1f}h")
        if _v(s.sleep_efficiency):
            sleep_parts.append(f"效率{s.sleep_efficiency:.0f}%")
        if _v(s.asleep_minutes):
            sleep_parts.append(f"入睡{s.asleep_minutes:.0f}min")
        if _v(s.deep_sleep_minutes):
            sleep_parts.append(f"深睡{s.deep_sleep_minutes:.0f}min")
        if _v(s.rem_sleep_minutes):
            sleep_parts.append(f"REM{s.rem_sleep_minutes:.0f}min")
        if _v(s.core_sleep_minutes):
            sleep_parts.append(f"核心{s.core_sleep_minutes:.0f}min")
        if _v(s.awake_minutes):
            sleep_parts.append(f"清醒{s.awake_minutes}min")
        if s.bed_time is not None:
            sleep_parts.append(f"入睡{s.bed_time.strftime('%H:%M')}")
        if s.wake_time is not None:
            sleep_parts.append(f"起床{s.wake_time.strftime('%H:%M')}")
        if sleep_parts:
            lines.append("睡眠: " + ", ".join(sleep_parts))

        avg_parts = []
        if _v(s.weight_7d_avg):
            avg_parts.append(f"7d体重{s.weight_7d_avg:.1f}kg")
        if _v(s.steps_7d_avg):
            avg_parts.append(f"7d步数{s.steps_7d_avg:.0f}")
        if _v(s.resting_hr_7d_avg):
            avg_parts.append(f"7d心率{s.resting_hr_7d_avg:.0f}")
        if _v(s.hrv_7d_avg):
            avg_parts.append(f"7dHRV{s.hrv_7d_avg:.0f}")
        if _v(s.sleep_7d_avg):
            avg_parts.append(f"7d睡眠{s.sleep_7d_avg:.0f}min")
        if _v(s.sleep_efficiency_7d_avg):
            avg_parts.append(f"7d效率{s.sleep_efficiency_7d_avg:.0f}%")
        if _v(s.exercise_7d_total):
            avg_parts.append(f"7d运动{s.exercise_7d_total:.0f}min")
        if avg_parts:
            lines.append("均值: " + ", ".join(avg_parts))

        return lines

    def _format_daily_detail(self, s: Any) -> str:
        """格式化单日健康汇总详情."""
        lines = [f"=== {s.record_date} 健康日报 ==="]
        lines.extend(self._format_brief_fields(s))
        return "\n".join(lines)

    def _format_daily_brief(self, s: Any) -> str:
        """格式化单日健康汇总简要(多天列表用)."""
        parts = [f"{s.record_date}:"]
        if _v(s.steps):
            parts.append(f"{s.steps}步")
        if _v(s.active_energy_kcal):
            parts.append(f"{s.active_energy_kcal:.0f}kcal")
        if _v(s.body_mass_kg):
            parts.append(f"{s.body_mass_kg:.1f}kg")
        if _v(s.resting_hr_bpm):
            parts.append(f"心率{s.resting_hr_bpm:.0f}")
        if _v(s.sleep_duration_hours):
            parts.append(f"睡眠{s.sleep_duration_hours:.1f}h")
        if _v(s.apple_exercise_minutes):
            parts.append(f"运动{s.apple_exercise_minutes:.0f}min")
        return "- " + ", ".join(parts)


def _v(val: Any) -> bool:
    """检查值是否有效(非None且非零)."""
    return val is not None and val != 0


__all__ = ["HealthDataManagerTool"]
