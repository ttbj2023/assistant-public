"""健康数据访问对象 - 提供健康数据的CRUD操作.

使用AsyncDatabaseOperations组合模式.
文件级隔离: 每个user_id+thread_id拥有独立的health_data.db, 表内不含user_id/thread_id.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy import update as sa_update
from sqlalchemy.ext.asyncio import async_sessionmaker

from src.storage.dao.database_operations import AsyncDatabaseOperations
from src.storage.models.health_data import (
    DailyHealthSummary,
    ECGRecord,
    FoodProduct,
    MealRecord,
    MedicalReport,
    ShoppingItem,
    WeeklyHealthSummary,
    WeightRecord,
    WorkoutRecord,
    WorkoutSample,
)

logger = logging.getLogger(__name__)


class AsyncHealthDAO:
    """健康数据访问对象类.

    所有方法均不需要 user_id/thread_id 参数, 因为数据库文件本身已经是隔离的.
    """

    def __init__(self, session_factory: async_sessionmaker):
        """初始化HealthDAO."""
        self.session_factory = session_factory

        self._medical_report_ops = AsyncDatabaseOperations(
            session_factory,
            MedicalReport,
        )
        self._daily_summary_ops = AsyncDatabaseOperations(
            session_factory,
            DailyHealthSummary,
        )
        self._weekly_summary_ops = AsyncDatabaseOperations(
            session_factory,
            WeeklyHealthSummary,
        )
        self._shopping_item_ops = AsyncDatabaseOperations(session_factory, ShoppingItem)
        self._food_product_ops = AsyncDatabaseOperations(session_factory, FoodProduct)
        self._workout_record_ops = AsyncDatabaseOperations(
            session_factory,
            WorkoutRecord,
        )
        self._meal_record_ops = AsyncDatabaseOperations(session_factory, MealRecord)
        self._weight_record_ops = AsyncDatabaseOperations(session_factory, WeightRecord)

    # ========== 体重记录管理 ==========

    async def create_weight_record(self, record_data: dict[str, Any]) -> WeightRecord:
        """创建体重记录(原始记录, 允许同一天多条)."""
        logger.info(f"创建体重记录: {record_data.get('weight_kg')}kg")
        return await self._weight_record_ops.create_with_validation(
            required_fields=["weight_kg"],
            **record_data,
        )

    # ========== 体检报告管理 ==========

    async def save_medical_report(self, report_data: dict[str, Any]) -> MedicalReport:
        """保存体检报告."""
        logger.info("保存体检报告")
        return await self._medical_report_ops.create(**report_data)

    async def get_latest_report(self) -> MedicalReport | None:
        """获取最新的体检报告."""
        reports = await self._medical_report_ops.get_latest(
            order_field="report_date",
            limit=1,
        )
        return reports[0] if reports else None

    async def analyze_report_trends(self) -> dict[str, Any]:
        """分析体检报告趋势."""
        async with self.session_factory() as session:
            stmt = (
                select(MedicalReport)
                .order_by(MedicalReport.report_date.desc())
                .limit(10)
            )
            result = await session.execute(stmt)
            reports = result.scalars().all()

            if not reports:
                return {"status": "no_data", "message": "暂无体检报告数据"}

            latest_report = reports[0]
            return {
                "status": "success",
                "total_reports": len(reports),
                "latest_report_date": latest_report.report_date.isoformat(),
                "latest_report_data": latest_report.report_data,
                "report_count": len(reports),
            }

    # ========== 每日健康汇总管理 ==========

    async def upsert_daily_summary(self, data: dict[str, Any]) -> DailyHealthSummary:
        """插入或更新每日健康汇总.

        按 record_date 的唯一约束判断是插入还是更新.
        更新时仅覆盖非None字段(保留已有数据).
        """
        target_date = data["record_date"]

        async with self.session_factory() as session:
            stmt = select(DailyHealthSummary).where(
                DailyHealthSummary.record_date == target_date,
            )
            result = await session.execute(stmt)
            existing = result.scalar_one_or_none()

            if existing:
                update_values = {
                    k: v for k, v in data.items() if v is not None and k != "id"
                }
                if update_values:
                    stmt_u = (
                        sa_update(DailyHealthSummary)
                        .where(DailyHealthSummary.id == existing.id)
                        .values(**update_values)
                        .returning(DailyHealthSummary)
                    )
                    result_u = await session.execute(stmt_u)
                    await session.commit()
                    return result_u.scalar_one()
                return existing
            record = DailyHealthSummary(**data)
            session.add(record)
            await session.commit()
            await session.refresh(record)
            return record

    async def get_daily_summary(self, target_date: date) -> DailyHealthSummary | None:
        """查询单日健康汇总."""
        async with self.session_factory() as session:
            stmt = select(DailyHealthSummary).where(
                DailyHealthSummary.record_date == target_date,
            )
            result = await session.execute(stmt)
            return result.scalar_one_or_none()

    async def get_daily_summaries(
        self,
        start_date: date,
        end_date: date,
    ) -> list[DailyHealthSummary]:
        """查询日期范围内的每日健康汇总."""
        async with self.session_factory() as session:
            stmt = (
                select(DailyHealthSummary)
                .where(
                    DailyHealthSummary.record_date >= start_date,
                    DailyHealthSummary.record_date <= end_date,
                )
                .order_by(DailyHealthSummary.record_date.desc())
            )
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def get_metric_history(
        self,
        metric: str,
        days: int = 30,
    ) -> list[dict[str, Any]]:
        """查询单项指标的日历史."""
        async with self.session_factory() as session:
            cutoff_date = date.today() - timedelta(days=days)
            stmt = (
                select(
                    DailyHealthSummary.record_date,
                    getattr(DailyHealthSummary, metric),
                )
                .where(
                    DailyHealthSummary.record_date >= cutoff_date,
                    getattr(DailyHealthSummary, metric) != None,  # noqa: E711
                )
                .order_by(DailyHealthSummary.record_date.desc())
            )
            result = await session.execute(stmt)
            return [
                {"date": row[0].isoformat(), "value": row[1]} for row in result.all()
            ]

    async def get_latest_daily_summaries(
        self,
        limit: int = 7,
    ) -> list[DailyHealthSummary]:
        """获取最近N天的每日健康汇总."""
        async with self.session_factory() as session:
            stmt = (
                select(DailyHealthSummary)
                .order_by(DailyHealthSummary.record_date.desc())
                .limit(limit)
            )
            result = await session.execute(stmt)
            return list(result.scalars().all())

    # ========== 每周健康趋势管理 ==========

    async def upsert_weekly_summary(self, data: dict[str, Any]) -> WeeklyHealthSummary:
        """插入或更新每周健康趋势.

        按 week_start 的唯一约束判断是插入还是更新.
        """
        week_start = data["week_start"]

        async with self.session_factory() as session:
            stmt = select(WeeklyHealthSummary).where(
                WeeklyHealthSummary.week_start == week_start,
            )
            result = await session.execute(stmt)
            existing = result.scalar_one_or_none()

            if existing:
                update_values = {
                    k: v for k, v in data.items() if v is not None and k != "id"
                }
                if update_values:
                    stmt_u = (
                        sa_update(WeeklyHealthSummary)
                        .where(WeeklyHealthSummary.id == existing.id)
                        .values(**update_values)
                        .returning(WeeklyHealthSummary)
                    )
                    result_u = await session.execute(stmt_u)
                    await session.commit()
                    return result_u.scalar_one()
                return existing
            record = WeeklyHealthSummary(**data)
            session.add(record)
            await session.commit()
            await session.refresh(record)
            return record

    async def get_weekly_summaries(
        self,
        start_week: date | None = None,
        end_week: date | None = None,
        limit: int = 12,
    ) -> list[WeeklyHealthSummary]:
        """查询周维度的健康趋势汇总."""
        async with self.session_factory() as session:
            stmt = select(WeeklyHealthSummary)
            if start_week:
                stmt = stmt.where(WeeklyHealthSummary.week_start >= start_week)
            if end_week:
                stmt = stmt.where(WeeklyHealthSummary.week_start <= end_week)
            stmt = stmt.order_by(WeeklyHealthSummary.week_start.desc()).limit(limit)
            result = await session.execute(stmt)
            return list(result.scalars().all())

    # ========== 购物清单管理 ==========

    async def create_shopping_item(self, item_data: dict[str, Any]) -> ShoppingItem:
        """创建购物清单条目."""
        logger.info(f"创建购物清单条目: {item_data.get('name')}")
        return await self._shopping_item_ops.create_with_validation(
            required_fields=["name", "purchase_date"],
            **item_data,
        )

    async def get_shopping_list(
        self,
        start_date: date,
        end_date: date,
    ) -> list[ShoppingItem]:
        """获取购物清单."""
        async with self.session_factory() as session:
            stmt = (
                select(ShoppingItem)
                .where(
                    ShoppingItem.purchase_date >= start_date,
                    ShoppingItem.purchase_date <= end_date,
                )
                .order_by(ShoppingItem.purchase_date.desc())
            )
            result = await session.execute(stmt)
            return list(result.scalars().all())

    # ========== 食品包装目录管理 ==========

    async def create_food_product(self, product_data: dict[str, Any]) -> FoodProduct:
        """创建食品包装目录条目."""
        logger.info(f"创建食品包装目录: {product_data.get('name')}")
        return await self._food_product_ops.create_with_validation(
            required_fields=["product_id", "name", "nutrition_per_100g"],
            **product_data,
        )

    # ========== 运动记录管理 ==========

    async def create_workout_record(
        self,
        workout_data: dict[str, Any],
    ) -> WorkoutRecord:
        """创建运动记录."""
        logger.info(f"创建运动记录: type={workout_data.get('workout_type')}")
        return await self._workout_record_ops.create_with_validation(
            required_fields=["workout_type", "duration", "start_time"],
            **workout_data,
        )

    async def upsert_workout_record(
        self,
        workout_data: dict[str, Any],
    ) -> WorkoutRecord:
        """插入或更新运动记录.

        按 start_time + workout_type 的唯一约束判断是插入还是更新.
        更新时仅覆盖非None字段(保留已有数据).
        """
        start_time = workout_data["start_time"]
        workout_type = workout_data["workout_type"]

        async with self.session_factory() as session:
            stmt = select(WorkoutRecord).where(
                WorkoutRecord.start_time == start_time,
                WorkoutRecord.workout_type == workout_type,
            )
            result = await session.execute(stmt)
            existing = result.scalar_one_or_none()

            if existing:
                update_values = {
                    k: v for k, v in workout_data.items() if v is not None and k != "id"
                }
                if update_values:
                    stmt_u = (
                        sa_update(WorkoutRecord)
                        .where(WorkoutRecord.id == existing.id)
                        .values(**update_values)
                        .returning(WorkoutRecord)
                    )
                    result_u = await session.execute(stmt_u)
                    await session.commit()
                    return result_u.scalar_one()
                return existing
            record = WorkoutRecord(**workout_data)
            session.add(record)
            await session.commit()
            await session.refresh(record)
            return record

    async def create_workout_sample(self, sample_data: dict[str, Any]) -> None:
        """创建单条运动采样记录."""
        async with self.session_factory() as session:
            record = WorkoutSample(**sample_data)
            session.add(record)
            await session.commit()

    async def create_ecg_record(self, ecg_data: dict[str, Any]) -> None:
        """创建ECG记录."""
        async with self.session_factory() as session:
            record = ECGRecord(**ecg_data)
            session.add(record)
            await session.commit()

    async def get_workout_history(self, days: int = 30) -> list[WorkoutRecord]:
        """获取运动历史记录."""
        async with self.session_factory() as session:
            cutoff_date = datetime.now() - timedelta(days=days)
            stmt = (
                select(WorkoutRecord)
                .where(WorkoutRecord.start_time >= cutoff_date)
                .order_by(WorkoutRecord.start_time.desc())
            )
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def get_weekly_activity_summary(self) -> dict[str, Any]:
        """获取每周活动汇总."""
        async with self.session_factory() as session:
            week_ago = datetime.now() - timedelta(days=7)

            stmt = select(WorkoutRecord).where(WorkoutRecord.start_time >= week_ago)
            result = await session.execute(stmt)
            workouts = list(result.scalars().all())

            if not workouts:
                return {
                    "status": "no_data",
                    "message": "过去7天没有运动记录",
                    "total_workouts": 0,
                    "total_duration": 0,
                    "total_calories": 0.0,
                }

            total_duration = sum(w.duration for w in workouts)
            total_calories = sum(w.calories or 0 for w in workouts)

            workout_types = {}
            for workout in workouts:
                workout_type = workout.workout_type
                if workout_type not in workout_types:
                    workout_types[workout_type] = {"count": 0, "duration": 0}
                workout_types[workout_type]["count"] += 1
                workout_types[workout_type]["duration"] += workout.duration

            return {
                "status": "success",
                "total_workouts": len(workouts),
                "total_duration": total_duration,
                "total_duration_minutes": total_duration,
                "total_calories": total_calories,
                "workout_types": workout_types,
            }

    # ========== 摄入记录管理 ==========

    async def bulk_upsert_daily_summaries(
        self,
        records: list[dict[str, Any]],
    ) -> list[DailyHealthSummary]:
        """批量插入或更新每日健康汇总.

        按 record_date 的唯一约束判断是插入还是更新.
        更新时仅覆盖非None字段(保留已有数据).
        """
        results = []
        for data in records:
            result = await self.upsert_daily_summary(data)
            results.append(result)
        return results

    async def bulk_upsert_weekly_summaries(
        self,
        records: list[dict[str, Any]],
    ) -> list[WeeklyHealthSummary]:
        """批量插入或更新每周健康趋势."""
        results = []
        for data in records:
            result = await self.upsert_weekly_summary(data)
            results.append(result)
        return results

    async def bulk_create_workout_records(
        self,
        records: list[dict[str, Any]],
    ) -> list[WorkoutRecord]:
        """批量创建运动记录."""
        results = []
        for data in records:
            result = await self.create_workout_record(data)
            results.append(result)
        return results

    # ========== 摄入记录管理 ==========

    async def create_meal_record(self, meal_data: dict[str, Any]) -> MealRecord:
        """创建摄入记录."""
        logger.info(f"创建摄入记录: type={meal_data.get('meal_type')}")
        return await self._meal_record_ops.create_with_validation(
            required_fields=["meal_date", "items"],
            **meal_data,
        )

    async def get_daily_intake(self, meal_date: date) -> list[MealRecord]:
        """获取每日摄入记录."""
        return await self._meal_record_ops.find_by_filters({"meal_date": meal_date})

    async def calculate_daily_nutrition(self, meal_date: date) -> dict[str, Any]:
        """计算每日营养成分."""
        meals = await self.get_daily_intake(meal_date)

        if not meals:
            return {
                "status": "no_data",
                "message": "该日期没有摄入记录",
                "date": meal_date.isoformat(),
            }

        total_nutrition: dict[str, Any] = {
            "calories": 0.0,
            "protein": 0.0,
            "carbs": 0.0,
            "fat": 0.0,
            "meal_count": len(meals),
        }

        for meal in meals:
            if meal.total_calories:
                total_nutrition["calories"] += meal.total_calories
            if meal.total_protein:
                total_nutrition["protein"] += meal.total_protein
            if meal.total_carbs:
                total_nutrition["carbs"] += meal.total_carbs
            if meal.total_fat:
                total_nutrition["fat"] += meal.total_fat

        total_nutrition["status"] = "success"
        total_nutrition["date"] = meal_date.isoformat()

        return total_nutrition

    # ========== 数据覆盖统计 ==========

    async def get_data_coverage(self) -> dict[str, Any]:
        """获取各表和各指标的数据覆盖统计.

        返回: {
            tables: {table_name: count},
            daily: {date_range, total, metrics: {metric: count}},
            latest_update: datetime,
            data_sources: {source: count},
            workout_types: {type: count},
        }
        """
        from sqlalchemy import func

        result: dict[str, Any] = {"tables": {}, "daily": {}, "workout_types": {}}

        async with self.session_factory() as session:
            # 各表记录数
            for name, model in [
                ("medical_reports", MedicalReport),
                ("daily_health_summary", DailyHealthSummary),
                ("weekly_health_summary", WeeklyHealthSummary),
                ("shopping_items", ShoppingItem),
                ("workout_records", WorkoutRecord),
                ("meal_records", MealRecord),
                ("weight_records", WeightRecord),
                ("workout_samples", WorkoutSample),
                ("ecg_records", ECGRecord),
            ]:
                count_stmt = select(func.count()).select_from(model)
                count_result = await session.execute(count_stmt)
                result["tables"][name] = count_result.scalar_one() or 0

            # daily数据详细统计
            total_stmt = select(func.count()).select_from(DailyHealthSummary)
            total_result = await session.execute(total_stmt)
            total = total_result.scalar_one()

            min_stmt = select(func.min(DailyHealthSummary.record_date))
            max_stmt = select(func.max(DailyHealthSummary.record_date))
            min_r = await session.execute(min_stmt)
            max_r = await session.execute(max_stmt)
            min_date = min_r.scalar_one_or_none()
            max_date = max_r.scalar_one_or_none()

            result["daily"]["total"] = total
            result["daily"]["date_range"] = {
                "start": min_date.isoformat() if min_date else None,
                "end": max_date.isoformat() if max_date else None,
            }

            # 各指标有值记录数
            metrics_to_check = [
                "steps",
                "active_energy_kcal",
                "basal_energy_kcal",
                "distance_km",
                "stand_hours",
                "body_mass_kg",
                "body_fat_pct",
                "muscle_mass_kg",
                "resting_hr_bpm",
                "hrv_ms",
                "vo2_max",
                "sleep_duration_hours",
                "sleep_efficiency",
                "asleep_minutes",
                "avg_hr_bpm",
                "min_hr_bpm",
                "max_hr_bpm",
                "blood_oxygen_pct",
                "wrist_temperature",
                "rem_sleep_minutes",
                "core_sleep_minutes",
                "respiratory_rate",
                "flights_climbed",
                "sunlight_minutes",
                "apple_exercise_minutes",
                "awake_minutes",
                "deep_sleep_minutes",
                "weight_7d_avg",
                "steps_7d_avg",
                "resting_hr_7d_avg",
                "hrv_7d_avg",
                "sleep_7d_avg",
                "sleep_efficiency_7d_avg",
                "exercise_7d_total",
            ]
            metric_counts = {}
            for metric in metrics_to_check:
                col = getattr(DailyHealthSummary, metric)
                count_stmt = (
                    select(func.count())
                    .select_from(DailyHealthSummary)
                    .where(col != None)  # noqa: E711
                )
                r = await session.execute(count_stmt)
                metric_counts[metric] = r.scalar_one() or 0
            result["daily"]["metrics"] = metric_counts

            # 数据来源分布
            source_stmt = select(DailyHealthSummary.data_source, func.count()).group_by(
                DailyHealthSummary.data_source,
            )
            source_r = await session.execute(source_stmt)
            result["data_sources"] = dict(source_r.all())

            # 最近更新时间
            latest_stmt = select(func.max(DailyHealthSummary.updated_at))
            latest_r = await session.execute(latest_stmt)
            latest = latest_r.scalar_one_or_none()
            result["latest_update"] = latest.isoformat() if latest else None

            # 运动类型分布
            workout_type_stmt = (
                select(WorkoutRecord.workout_type, func.count())
                .group_by(WorkoutRecord.workout_type)
                .order_by(func.count().desc())
            )
            wt_r = await session.execute(workout_type_stmt)
            result["workout_types"] = dict(wt_r.all())

            return result

    async def get_metric_comparison(
        self,
        metric: str,
        period1_start: date,
        period1_end: date,
        period2_start: date,
        period2_end: date,
    ) -> dict[str, Any]:
        """对比两个时段的某项指标.

        返回: {
            metric, period1: {start, end, count, avg, values},
            period2: {start, end, count, avg, values},
            change_pct, direction
        }
        """
        from sqlalchemy import func

        col = getattr(DailyHealthSummary, metric)

        async with self.session_factory() as session:
            # 时段1
            stmt1 = select(
                func.count(),
                func.avg(col),
                func.max(col),
                func.min(col),
            ).where(
                DailyHealthSummary.record_date >= period1_start,
                DailyHealthSummary.record_date <= period1_end,
                col != None,  # noqa: E711
            )
            r1 = await session.execute(stmt1)
            row1 = r1.one()
            count1, avg1, max1, min1 = row1

            # 时段2
            stmt2 = select(
                func.count(),
                func.avg(col),
                func.max(col),
                func.min(col),
            ).where(
                DailyHealthSummary.record_date >= period2_start,
                DailyHealthSummary.record_date <= period2_end,
                col != None,  # noqa: E711
            )
            r2 = await session.execute(stmt2)
            row2 = r2.one()
            count2, avg2, max2, min2 = row2

            avg1 = float(avg1) if avg1 else None
            avg2 = float(avg2) if avg2 else None

            change_pct = None
            direction = "no_data"
            if avg1 is not None and avg2 is not None and avg2 != 0:
                change_pct = round((avg1 - avg2) / abs(avg2) * 100, 1)
                if change_pct > 1:
                    direction = "up"
                elif change_pct < -1:
                    direction = "down"
                else:
                    direction = "stable"

            return {
                "metric": metric,
                "period1": {
                    "start": period1_start.isoformat(),
                    "end": period1_end.isoformat(),
                    "count": count1,
                    "avg": avg1,
                    "max": float(max1) if max1 else None,
                    "min": float(min1) if min1 else None,
                },
                "period2": {
                    "start": period2_start.isoformat(),
                    "end": period2_end.isoformat(),
                    "count": count2,
                    "avg": avg2,
                    "max": float(max2) if max2 else None,
                    "min": float(min2) if min2 else None,
                },
                "change_pct": change_pct,
                "direction": direction,
            }

    async def get_workout_history_filtered(
        self,
        days: int = 30,
        workout_type: str | None = None,
        limit: int = 20,
    ) -> list[WorkoutRecord]:
        """获取运动历史记录, 支持类型筛选."""
        async with self.session_factory() as session:
            cutoff_date = datetime.now() - timedelta(days=days)
            stmt = select(WorkoutRecord).where(WorkoutRecord.start_time >= cutoff_date)
            if workout_type:
                stmt = stmt.where(WorkoutRecord.workout_type == workout_type)
            stmt = stmt.order_by(WorkoutRecord.start_time.desc()).limit(limit)
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def get_workout_stats(
        self,
        days: int = 90,
        workout_type: str | None = None,
    ) -> dict[str, Any]:
        """获取运动统计汇总."""
        from sqlalchemy import func

        async with self.session_factory() as session:
            cutoff_date = datetime.now() - timedelta(days=days)
            base_condition = WorkoutRecord.start_time >= cutoff_date
            if workout_type:
                base_condition &= WorkoutRecord.workout_type == workout_type

            # 总体统计
            total_stmt = select(
                func.count(),
                func.sum(WorkoutRecord.duration),
                func.sum(WorkoutRecord.calories),
            ).where(base_condition)
            total_r = await session.execute(total_stmt)
            total_row = total_r.one()
            total_count = total_row[0] or 0
            total_duration = float(total_row[1]) if total_row[1] else 0.0
            total_calories = float(total_row[2]) if total_row[2] else 0.0

            # 各类型分布
            type_stmt = (
                select(
                    WorkoutRecord.workout_type,
                    func.count(),
                    func.sum(WorkoutRecord.duration),
                )
                .where(base_condition)
                .group_by(WorkoutRecord.workout_type)
                .order_by(func.count().desc())
            )
            type_r = await session.execute(type_stmt)
            type_distribution = {}
            for row in type_r.all():
                type_distribution[row[0]] = {
                    "count": row[1],
                    "duration": float(row[2]) if row[2] else 0.0,
                }

            # 频率(次/周)
            weeks = max(days / 7, 1)
            freq_per_week = round(total_count / weeks, 1)

            return {
                "status": "success",
                "total_count": total_count,
                "total_duration_minutes": round(total_duration, 1),
                "total_calories": round(total_calories, 1),
                "freq_per_week": freq_per_week,
                "type_distribution": type_distribution,
                "days": days,
            }

    # ========== 健康检查 ==========

    async def health_check(self) -> dict[str, bool]:
        """健康检查所有健康数据表."""
        results = {}
        for name, ops in [
            ("medical_reports", self._medical_report_ops),
            ("daily_health_summary", self._daily_summary_ops),
            ("weekly_health_summary", self._weekly_summary_ops),
            ("shopping_items", self._shopping_item_ops),
            ("food_products", self._food_product_ops),
            ("workout_records", self._workout_record_ops),
            ("meal_records", self._meal_record_ops),
            ("weight_records", self._weight_record_ops),
        ]:
            try:
                results[name] = await ops.health_check()
            except Exception as e:
                logger.error("健康检查失败 %s: %s", name, e)
                results[name] = False

        return results
