"""健康数据业务服务.

提供健康数据相关的业务逻辑封装, 包括:
- 高层业务: 健康状态分析,报告生成
- 数据查询: 数据覆盖统计,指标趋势,时段对比,运动统计等
数据库文件级隔离, DAO方法无需user_id/thread_id.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from datetime import date
from typing import Any, override

from src.storage.dao.async_health_dao import AsyncHealthDAO
from src.storage.models.health_data import (
    DailyHealthSummary,
    MedicalReport,
)

from .health_check_mixin import ServiceHealthCheckMixin

logger = logging.getLogger(__name__)


class HealthDataService(ServiceHealthCheckMixin):
    """健康数据业务服务.

    封装 AsyncHealthDAO 的所有查询方法, 提供统一的 Service 层接口.
    工具层通过本 Service 访问数据, 不直接操作 DAO.
    """

    def __init__(self, session_factory: Callable[[], Any]) -> None:
        """初始化健康数据服务."""
        super().__init__()
        self.session_factory = session_factory
        self.logger = logging.getLogger(f"{__name__}.HealthDataService")
        self.health_dao = AsyncHealthDAO(session_factory)

    # ========== 高层业务方法 ==========

    async def analyze_health_status(self) -> dict[str, Any]:
        """分析用户健康状态."""
        start_time = time.time()

        try:
            self.logger.info("开始分析健康状态")

            latest_report = await self.health_dao.get_latest_report()
            daily_summaries = await self.health_dao.get_latest_daily_summaries(limit=30)
            activity_summary = await self.health_dao.get_weekly_activity_summary()

            health_score = await self._calculate_health_score(
                latest_report,
                daily_summaries,
                activity_summary,
            )

            recommendations = await self._generate_health_recommendations(
                latest_report,
                daily_summaries,
                activity_summary,
            )

            duration = (time.time() - start_time) * 1000

            result = {
                "status": "success",
                "latest_medical": {
                    "report_date": latest_report.report_date.isoformat()
                    if latest_report
                    else None,
                    "has_data": bool(latest_report),
                },
                "weight_trend": self._analyze_weight_trend_from_daily(daily_summaries),
                "daily_summary_available": len(daily_summaries),
                "activity_summary": activity_summary,
                "health_score": health_score,
                "recommendations": recommendations,
                "analysis_duration_ms": round(duration, 2),
            }

            self.logger.info(
                f"健康状态分析完成, score: {health_score}, {duration:.2f}ms",
            )
            return result

        except Exception as e:
            self.logger.error("健康状态分析失败: %s", e)
            raise RuntimeError(f"健康状态分析失败: {e}") from e

    # ========== 数据查询方法(透传 DAO) ==========

    async def get_data_coverage(self) -> dict[str, Any]:
        """获取各表和各指标的数据覆盖统计."""
        return await self.health_dao.get_data_coverage()

    async def get_daily_summary(self, target_date: date) -> DailyHealthSummary | None:
        """查询单日健康汇总."""
        return await self.health_dao.get_daily_summary(target_date)

    async def get_daily_summaries(
        self,
        start_date: date,
        end_date: date,
    ) -> list[DailyHealthSummary]:
        """查询日期范围内的每日健康汇总."""
        return await self.health_dao.get_daily_summaries(start_date, end_date)

    async def get_metric_history(
        self,
        metric: str,
        days: int = 30,
    ) -> list[dict[str, Any]]:
        """查询单项指标的日历史."""
        return await self.health_dao.get_metric_history(metric, days=days)

    async def get_latest_daily_summaries(
        self,
        limit: int = 7,
    ) -> list[DailyHealthSummary]:
        """获取最近N天的每日健康汇总."""
        return await self.health_dao.get_latest_daily_summaries(limit=limit)

    async def get_weekly_summaries(
        self,
        start_week: date | None = None,
        end_week: date | None = None,
        limit: int = 12,
    ) -> list[Any]:
        """查询周维度的健康趋势汇总."""
        return await self.health_dao.get_weekly_summaries(
            start_week=start_week,
            end_week=end_week,
            limit=limit,
        )

    async def get_weekly_activity_summary(self) -> dict[str, Any]:
        """获取每周活动汇总."""
        return await self.health_dao.get_weekly_activity_summary()

    async def get_latest_report(self) -> MedicalReport | None:
        """获取最新的体检报告."""
        return await self.health_dao.get_latest_report()

    async def get_metric_comparison(
        self,
        metric: str,
        period1_start: date,
        period1_end: date,
        period2_start: date,
        period2_end: date,
    ) -> dict[str, Any]:
        """对比两个时段的某项指标."""
        return await self.health_dao.get_metric_comparison(
            metric,
            period1_start,
            period1_end,
            period2_start,
            period2_end,
        )

    async def get_workout_history_filtered(
        self,
        days: int = 30,
        workout_type: str | None = None,
        limit: int = 20,
    ) -> list[Any]:
        """获取运动历史记录, 支持类型筛选."""
        return await self.health_dao.get_workout_history_filtered(
            days=days,
            workout_type=workout_type,
            limit=limit,
        )

    async def get_workout_stats(
        self,
        days: int = 90,
        workout_type: str | None = None,
    ) -> dict[str, Any]:
        """获取运动统计汇总."""
        return await self.health_dao.get_workout_stats(
            days=days,
            workout_type=workout_type,
        )

    async def get_nutrition_summary(self, target_date: date) -> dict[str, Any]:
        """获取指定日期的营养汇总."""
        return await self.health_dao.calculate_daily_nutrition(target_date)

    async def get_report_detail(self) -> dict[str, Any]:
        """获取最新体检报告详情(含历史趋势)."""
        latest = await self.health_dao.get_latest_report()
        if not latest:
            return {"status": "no_data", "message": "暂无体检报告"}
        trends = await self.health_dao.analyze_report_trends()
        return {
            "status": "success",
            "latest": {
                "report_date": latest.report_date.isoformat(),
                "report_type": latest.report_type,
                "data": latest.report_data,
            },
            "history": trends,
        }

    async def get_shopping_list(self, days: int = 30) -> list[Any]:
        """获取最近N天的购物清单."""
        end_date = date.today()
        start_date = end_date - __import__("datetime").timedelta(days=days - 1)
        return await self.health_dao.get_shopping_list(start_date, end_date)

    # ========== 私有方法 ==========

    async def _calculate_health_score(
        self,
        medical_report: MedicalReport | None,
        daily_summaries: list[DailyHealthSummary],
        activity_summary: dict[str, Any],
    ) -> int:
        """计算健康评分."""
        score = 0

        if medical_report:
            score += 30
        if daily_summaries:
            score += 30
        if activity_summary.get("status") == "success":
            total_workouts = activity_summary.get("total_workouts", 0)
            if total_workouts >= 3:
                score += 40
            elif total_workouts >= 1:
                score += 20

        return min(score, 100)

    async def _generate_health_recommendations(
        self,
        medical_report: MedicalReport | None,
        daily_summaries: list[DailyHealthSummary],
        activity_summary: dict[str, Any],
    ) -> list[str]:
        """生成健康建议."""
        recommendations = []

        if activity_summary.get("status") == "success":
            total_workouts = activity_summary.get("total_workouts", 0)
            if total_workouts == 0:
                recommendations.append("建议开始规律运动,每周至少3次中等强度运动")
            elif total_workouts < 3:
                recommendations.append("建议增加运动频率,目标每周3-5次运动")

        if medical_report:
            recommendations.append("定期体检已记录,建议持续关注关键健康指标")

        if daily_summaries:
            low_activity_days = sum(
                1 for d in daily_summaries if d.steps is not None and d.steps < 1000
            )
            if low_activity_days > len(daily_summaries) * 0.5:
                recommendations.append(
                    f"过去{len(daily_summaries)}天中有{low_activity_days}天步数低于1000, 建议增加日常活动量",
                )

        return recommendations

    def _analyze_weight_trend_from_daily(
        self,
        daily_summaries: list[DailyHealthSummary],
    ) -> dict[str, Any]:
        """从每日汇总分析体重趋势."""
        weight_data = [
            (d.record_date, d.body_mass_kg)
            for d in daily_summaries
            if d.body_mass_kg is not None
        ]

        if not weight_data:
            return {"latest_weight": None, "records_count": 0, "trend": "no_data"}

        latest_weight = weight_data[0][1]
        trend = "stable"
        if len(weight_data) >= 2:
            change = weight_data[0][1] - weight_data[-1][1]
            if change > 1:
                trend = "increasing"
            elif change < -1:
                trend = "decreasing"

        return {
            "latest_weight": latest_weight,
            "records_count": len(weight_data),
            "trend": trend,
        }

    @override
    async def _check_service_health(self) -> dict[str, Any]:
        """检查服务健康状态."""
        try:
            db_health = await self.health_dao.health_check()
            all_healthy = all(db_health.values())
            return {
                "status": "healthy" if all_healthy else "degraded",
                "database_connected": all_healthy,
                "statistics": {
                    "tables_checked": len(db_health),
                    "healthy_tables": sum(1 for v in db_health.values() if v),
                },
                "error": None,
                "additional_info": {"database_health": db_health},
            }
        except Exception as e:
            self.logger.debug("健康数据服务健康检查失败: %s", e)
            return {
                "status": "unhealthy",
                "database_connected": False,
                "statistics": {},
                "error": str(e),
                "additional_info": {},
            }


async def get_health_service(
    user_id: str,
    thread_id: str,
    *,
    agent_id: str,
) -> HealthDataService:
    """创建健康数据服务实例 (底层Engine全局复用).

    Args:
        user_id: 用户ID
        thread_id: 线程ID
        agent_id: Agent ID

    Returns:
        健康数据服务实例

    """
    from src.storage.dao.async_database_manager import (
        create_async_health_data_db_manager,
    )

    db_manager = await create_async_health_data_db_manager(
        user_id,
        thread_id,
        agent_id=agent_id,
    )
    return HealthDataService(db_manager.session_factory)


__all__ = ["HealthDataService", "get_health_service"]
