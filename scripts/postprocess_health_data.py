#!/usr/bin/env python3
"""健康数据后处理脚本 - 计算7日滚动均值和周汇总.

参考 apple-health-processor 的后处理 pipeline:
- 佩戴判定: stand_hours > 0 OR active_energy_kcal > 0
- 7日滚动均值: 排除未佩戴天, min_periods=4
- 周汇总: 只统计已佩戴天, 有效天数 >= 4 才算日均值
- 异常值过滤: 体重日变化 >5kg 排除, 心率/HRV 范围过滤

用法:
    python scripts/postprocess_health_data.py --user-id alice --thread-id main
    python scripts/postprocess_health_data.py --user-id alice --thread-id main --stats-only
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from typing import Any

import pandas as pd

project_root = str(Path(__file__).resolve().parent.parent)
if project_root not in sys.path:
    sys.path.insert(0, project_root)


WORN_CRITERIA = "stand_hours > 0 or active_energy_kcal > 0"

ROLLING_WINDOW = 7
MIN_PERIODS = 4

ANOMALY_RULES: dict[str, dict[str, Any]] = {
    "body_mass_kg": {"max_daily_change": 5.0, "min": 30, "max": 300},
    "steps": {"min": 0, "max": 100000},
    "resting_hr_bpm": {"min": 40, "max": 120},
    "hrv_ms": {"min": 10, "max": 200},
    "apple_exercise_minutes": {"min": 0, "max": 300},
    "sleep_duration_hours": {"min": 0, "max": 24},
    "asleep_minutes": {"min": 0, "max": 1440},
    "sleep_efficiency": {"min": 0, "max": 100},
}


def _notna(val: Any) -> bool:
    """安全的 notna 检查, 兼容标量和 pandas 类型."""
    if val is None:
        return False
    try:
        return bool(pd.notna(val))
    except (TypeError, ValueError):
        return val is not None


def _load_daily_data(dao: Any) -> pd.DataFrame:
    """从数据库加载全部每日汇总数据到 DataFrame (同步封装)."""
    import sqlalchemy as sa

    from src.storage.models.health_data import DailyHealthSummary

    async def _load() -> pd.DataFrame:
        async with dao.session_factory() as session:
            stmt = sa.select(DailyHealthSummary).order_by(
                DailyHealthSummary.record_date
            )
            result = await session.execute(stmt)
            rows = result.scalars().all()

        records = []
        for r in rows:
            d = {
                c.key: getattr(r, c.key)
                for c in sa.inspect(DailyHealthSummary).mapper.column_attrs
            }
            records.append(d)

        return pd.DataFrame(records)

    import asyncio
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor() as pool:
            future = pool.submit(asyncio.run, _load())
            return future.result()
    else:
        return asyncio.run(_load())


def _mark_worn(df: pd.DataFrame) -> pd.Series:
    """标记佩戴状态: stand_hours > 0 OR active_energy_kcal > 0."""
    stand = df.get("stand_hours", pd.Series(0, index=df.index))
    energy = df.get("active_energy_kcal", pd.Series(0, index=df.index))
    stand = stand.fillna(0)
    energy = energy.fillna(0)
    return (stand > 0) | (energy > 0)


def _filter_anomalies(series: pd.Series, rules: dict[str, Any]) -> pd.Series:
    """根据规则过滤异常值."""
    result = series.copy()
    if "min" in rules:
        result = result.where(result >= rules["min"])
    if "max" in rules:
        result = result.where(result <= rules["max"])
    if "max_daily_change" in rules:
        change = result.diff().abs()
        result = result.where(change <= rules["max_daily_change"])
    return result


def compute_rolling_7d(df: pd.DataFrame, is_worn: pd.Series) -> pd.DataFrame:
    """计算7日滚动均值指标.

    排除未佩戴天的数据, 使用 min_periods=4 确保至少有4天有效数据.
    返回只包含7d字段的 DataFrame (与原 df 行对齐).
    """
    enhanced = pd.DataFrame(index=df.index)

    worn_mask = is_worn

    # 体重 7日均值
    weight = df["body_mass_kg"].copy()
    weight = weight.where(worn_mask)
    weight = _filter_anomalies(weight, ANOMALY_RULES["body_mass_kg"])
    enhanced["weight_7d_avg"] = weight.rolling(
        ROLLING_WINDOW, min_periods=MIN_PERIODS
    ).mean()

    # 步数 7日均值
    steps = df["steps"].copy().where(worn_mask)
    steps = _filter_anomalies(steps, ANOMALY_RULES["steps"])
    enhanced["steps_7d_avg"] = steps.rolling(
        ROLLING_WINDOW, min_periods=MIN_PERIODS
    ).mean()

    # 静息心率 7日均值
    hr = df["resting_hr_bpm"].copy().where(worn_mask)
    hr = _filter_anomalies(hr, ANOMALY_RULES["resting_hr_bpm"])
    enhanced["resting_hr_7d_avg"] = hr.rolling(
        ROLLING_WINDOW, min_periods=MIN_PERIODS
    ).mean()

    # HRV 7日均值
    hrv = df["hrv_ms"].copy().where(worn_mask)
    hrv = _filter_anomalies(hrv, ANOMALY_RULES["hrv_ms"])
    enhanced["hrv_7d_avg"] = hrv.rolling(
        ROLLING_WINDOW, min_periods=MIN_PERIODS
    ).mean()

    # 睡眠 7日均值 (分钟)
    asleep = df["asleep_minutes"].copy()
    asleep = _filter_anomalies(asleep, ANOMALY_RULES["asleep_minutes"])
    enhanced["sleep_7d_avg"] = asleep.rolling(
        ROLLING_WINDOW, min_periods=MIN_PERIODS
    ).mean()

    # 睡眠效率 7日均值
    eff = df["sleep_efficiency"].copy()
    eff = _filter_anomalies(eff, ANOMALY_RULES["sleep_efficiency"])
    enhanced["sleep_efficiency_7d_avg"] = eff.rolling(
        ROLLING_WINDOW, min_periods=MIN_PERIODS
    ).mean()

    # 运动时间 7日滚动求和
    exercise = df["apple_exercise_minutes"].copy().where(worn_mask)
    exercise = _filter_anomalies(exercise, ANOMALY_RULES["apple_exercise_minutes"])
    enhanced["exercise_7d_total"] = exercise.rolling(
        ROLLING_WINDOW, min_periods=MIN_PERIODS
    ).sum()

    return enhanced


def compute_weekly(df: pd.DataFrame, is_worn: pd.Series) -> pd.DataFrame:
    """计算周汇总.

    只统计已佩戴天的数据, 有效天数 >= 4 才算日均值.
    以周一为起始日.
    """
    worn_df = df[is_worn].copy()
    if worn_df.empty:
        return pd.DataFrame()

    if not isinstance(worn_df.index, pd.DatetimeIndex):
        worn_df.index = pd.to_datetime(worn_df.index)

    resampled = worn_df.resample("W-MON", label="left", closed="left")

    rows = []
    for week_start, week_data in resampled:
        if week_data.empty:
            continue

        n_days = len(week_data)
        row: dict[str, Any] = {"week_start": week_start.date()}

        # 累加
        row["steps_total"] = week_data["steps"].sum()
        row["active_energy_total"] = week_data["active_energy_kcal"].sum()
        row["basal_energy_total"] = week_data["basal_energy_kcal"].sum()
        row["distance_total"] = week_data["distance_km"].sum()
        row["exercise_minutes_total"] = week_data["apple_exercise_minutes"].sum()
        row["stand_hours_total"] = week_data["stand_hours"].sum()

        # 均值
        bm = week_data["body_mass_kg"].dropna()
        row["body_mass_avg"] = float(bm.mean()) if len(bm) > 0 else None

        rhr = week_data["resting_hr_bpm"].dropna()
        row["resting_hr_avg"] = float(rhr.mean()) if len(rhr) > 0 else None

        hrv = week_data["hrv_ms"].dropna()
        row["hrv_avg"] = float(hrv.mean()) if len(hrv) > 0 else None

        vo2 = week_data["vo2_max"].dropna()
        row["vo2_max_avg"] = float(vo2.mean()) if len(vo2) > 0 else None

        sl = week_data["sleep_duration_hours"].dropna()
        row["sleep_duration_avg"] = float(sl.mean()) if len(sl) > 0 else None

        se = week_data["sleep_efficiency"].dropna()
        row["sleep_efficiency_avg"] = float(se.mean()) if len(se) > 0 else None

        row["valid_days"] = n_days
        row["total_days"] = 7

        # 日均值: 有效天数 >= 4 才计算
        if n_days >= MIN_PERIODS:
            row["steps_daily_avg"] = row["steps_total"] / n_days
            row["active_energy_daily_avg"] = row["active_energy_total"] / n_days
            row["distance_daily_avg"] = row["distance_total"] / n_days
        else:
            row["steps_daily_avg"] = None
            row["active_energy_daily_avg"] = None
            row["distance_daily_avg"] = None

        rows.append(row)

    return pd.DataFrame(rows)


async def _save_rolling_7d(dao: Any, df: pd.DataFrame, rolling: pd.DataFrame) -> int:
    """将7日滚动均值写回数据库."""
    cols = [
        "weight_7d_avg", "steps_7d_avg", "resting_hr_7d_avg", "hrv_7d_avg",
        "sleep_7d_avg", "sleep_efficiency_7d_avg", "exercise_7d_total",
    ]

    updated = 0
    async with dao.session_factory() as session:
        import sqlalchemy as sa

        from src.storage.models.health_data import DailyHealthSummary

        for idx in rolling.index:
            row = rolling.loc[idx]
            update_data = {}
            for col in cols:
                val = row.get(col)
                if pd.notna(val):
                    update_data[col] = float(val)

            if not update_data:
                continue

            record_date = idx.date() if hasattr(idx, "date") else idx
            stmt = (
                sa.update(DailyHealthSummary)
                .where(DailyHealthSummary.record_date == record_date)
                .values(**update_data)
            )
            await session.execute(stmt)
            updated += 1

            if updated % 500 == 0:
                await session.commit()

        await session.commit()

    return updated


async def _save_weekly(dao: Any, weekly_df: pd.DataFrame) -> int:
    """将周汇总写入数据库."""
    if weekly_df.empty:
        return 0

    saved = 0
    for _, row in weekly_df.iterrows():
        data = row.dropna().to_dict()
        if "week_start" not in data:
            continue
        await dao.upsert_weekly_summary(data)
        saved += 1

    return saved


def print_stats(df: pd.DataFrame, is_worn: pd.Series, rolling: pd.DataFrame) -> None:
    """打印数据质量统计报告."""
    total = len(df)
    worn_count = int(is_worn.sum())
    not_worn = total - worn_count

    print("\n" + "=" * 60)
    print("数据质量报告")
    print("=" * 60)

    print(f"\n总天数: {total}")
    print(f"已佩戴: {worn_count} ({worn_count / total * 100:.1f}%)")
    print(f"未佩戴: {not_worn} ({not_worn / total * 100:.1f}%)")

    if not df.empty and "record_date" in df.columns:
        dates = df["record_date"]
        print(f"\n数据范围: {dates.min()} ~ {dates.max()}")

    print("\n指标覆盖率 (已佩戴天):")
    worn_df = df[is_worn]
    worn_total = len(worn_df)
    metrics_display = [
        ("steps", "步数"),
        ("active_energy_kcal", "活动能量"),
        ("resting_hr_bpm", "静息心率"),
        ("hrv_ms", "HRV"),
        ("body_mass_kg", "体重"),
        ("sleep_duration_hours", "睡眠"),
        ("asleep_minutes", "入睡(精确)"),
        ("deep_sleep_minutes", "深睡"),
        ("apple_exercise_minutes", "运动时间"),
    ]
    for col, label in metrics_display:
        if col in worn_df.columns:
            cnt = int(worn_df[col].notna().sum())
            pct = cnt / worn_total * 100 if worn_total > 0 else 0
            print(f"  {label}: {cnt}/{worn_total} ({pct:.1f}%)")

    print("\n7日滚动均值统计:")
    rolling_cols = [
        ("weight_7d_avg", "体重"),
        ("steps_7d_avg", "步数"),
        ("resting_hr_7d_avg", "心率"),
        ("hrv_7d_avg", "HRV"),
        ("sleep_7d_avg", "睡眠"),
        ("sleep_efficiency_7d_avg", "睡眠效率"),
        ("exercise_7d_total", "运动(7日总和)"),
    ]
    for col, label in rolling_cols:
        if col in rolling.columns:
            valid = rolling[col].dropna()
            if len(valid) > 0:
                print(
                    f"  {label}: 有值{len(valid)}天, "
                    f"均值={valid.mean():.1f}, "
                    f"范围={valid.min():.1f}~{valid.max():.1f}"
                )


async def _get_dao(user_id: str, thread_id: str, agent_id: str = "health-assistant") -> Any:
    """创建 AsyncHealthDAO 实例."""
    from src.storage.dao.async_database_manager import (
        create_async_health_data_db_manager,
    )
    from src.storage.dao.async_health_dao import AsyncHealthDAO

    db_manager = await create_async_health_data_db_manager(
        user_id, thread_id, agent_id=agent_id
    )
    return AsyncHealthDAO(db_manager.session_factory)


async def run_postprocess(user_id: str, thread_id: str, agent_id: str) -> None:
    """执行后处理: 7日滚动均值 + 周汇总."""
    print("加载数据库...")
    dao = await _get_dao(user_id, thread_id, agent_id)

    # Step 1: 加载全部 daily 数据
    print("加载每日汇总数据...")
    df = _load_daily_data(dao)
    if df.empty:
        print("无每日汇总数据, 退出")
        return

    df = df.set_index("record_date").sort_index()
    print(f"  {len(df)} 天数据")

    # Step 2: 佩戴判定
    print("佩戴判定...")
    is_worn = _mark_worn(df)
    worn_count = int(is_worn.sum())
    print(f"  已佩戴: {worn_count}/{len(df)} ({worn_count / len(df) * 100:.1f}%)")

    # Step 3: 7日滚动均值
    print("计算7日滚动均值...")
    rolling = compute_rolling_7d(df, is_worn)
    for col in rolling.columns:
        valid = rolling[col].dropna()
        print(f"  {col}: {len(valid)}天有值")

    # Step 4: 周汇总
    print("计算周汇总...")
    weekly_df = compute_weekly(df, is_worn)
    print(f"  {len(weekly_df)} 周")

    # Step 5: 写回数据库
    print("保存7日滚动均值...")
    n_updated = await _save_rolling_7d(dao, df, rolling)
    print(f"  更新 {n_updated} 天")

    print("保存周汇总...")
    n_weekly = await _save_weekly(dao, weekly_df)
    print(f"  保存 {n_weekly} 周")

    # Step 6: 统计报告
    print_stats(df, is_worn, rolling)

    if not weekly_df.empty:
        print("\n周汇总示例 (最近4周):")
        for _, row in weekly_df.tail(4).iterrows():
            parts = [f"周{row['week_start']}:"]
            if _notna(row.get("steps_total")):
                parts.append(f"步数{row['steps_total']:.0f}")
            if _notna(row.get("body_mass_avg")):
                parts.append(f"体重{row['body_mass_avg']:.1f}kg")
            if _notna(row.get("resting_hr_avg")):
                parts.append(f"心率{row['resting_hr_avg']:.0f}")
            if _notna(row.get("sleep_duration_avg")):
                parts.append(f"睡眠{row['sleep_duration_avg']:.1f}h")
            if _notna(row.get("exercise_minutes_total")):
                parts.append(f"运动{row['exercise_minutes_total']:.0f}min")
            valid_d = row.get("valid_days", "?")
            parts.append(f"有效{valid_d}/7天")
            print("  " + ", ".join(parts))

    print("\n后处理完成!")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="健康数据后处理: 7日滚动均值 + 周汇总",
    )
    parser.add_argument("--user-id", required=True, help="用户ID")
    parser.add_argument("--thread-id", default="main", help="线程ID")
    parser.add_argument(
        "--agent-id", default="health-assistant", help="Agent ID"
    )
    parser.add_argument(
        "--stats-only",
        action="store_true",
        help="只打印统计报告, 不写回数据库",
    )
    return parser.parse_args()


async def main() -> None:
    args = _parse_args()

    if args.stats_only:
        dao = await _get_dao(args.user_id, args.thread_id, args.agent_id)

        df = _load_daily_data(dao)
        if df.empty:
            print("无数据")
            return
        df = df.set_index("record_date").sort_index()
        is_worn = _mark_worn(df)
        rolling = compute_rolling_7d(df, is_worn)
        print_stats(df, is_worn, rolling)
        return

    await run_postprocess(args.user_id, args.thread_id, args.agent_id)


if __name__ == "__main__":
    asyncio.run(main())
