"""Health Auto Export CSV 数据导入器.

从 Health Auto Export app 导出的 CSV 文件读取健康数据, 映射字段并写入数据库.
支持5类数据: 每日汇总(含睡眠+体征), 运动记录, 运动详情时间序列, ECG记录, 体重记录.

CSV 列名为中文(如 "步数 (count)", "活动能量 (kJ)"), 需要特殊映射和单位转换.
能量单位: CSV中为kJ, 数据库中为kcal, 需要除以4.184.
睡眠单位: CSV中为小时, 数据库中为分钟, 需要乘以60.

使用方式:
    1. 在 Health Auto Export app 中导出全部数据
    2. 用本导入器将导出目录中的CSV导入 assistant 健康数据库

冲突策略: upsert模式, 按唯一键合并(每日按日期, 运动按开始时间+类型), 仅填充空值.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from src.storage.dao.async_database_manager import create_async_health_data_db_manager
from src.storage.dao.async_health_dao import AsyncHealthDAO

logger = logging.getLogger(__name__)

# kJ -> kcal 转换系数
KJ_TO_KCAL = 1.0 / 4.184

# ===== 每日汇总: 中文CSV列名 -> DB字段名 =====
_DAILY_CSV_TO_DB: dict[str, tuple[str, str]] = {
    "日期/时间": ("record_date", "date"),
    "步数 (count)": ("steps", "int"),
    "活动能量 (kJ)": ("active_energy_kcal", "kj_to_kcal"),
    "静息能量 (kJ)": ("basal_energy_kcal", "kj_to_kcal"),
    "步行 + 跑步距离 (km)": ("distance_km", "float"),
    "Apple锻炼时间 (min)": ("apple_exercise_minutes", "float"),
    "Apple 站立小时 (count)": ("stand_hours", "int"),
    "Apple 站立时间 (min)": ("stand_minutes", "float"),
    "体重 (kg)": ("body_mass_kg", "float"),
    "体脂百分比 (%)": ("body_fat_pct", "float"),
    "去脂体重 (kg)": ("muscle_mass_kg", "float"),
    "静息心率 (count/min)": ("resting_hr_bpm", "float"),
    "心率变异性 (ms)": ("hrv_ms", "float"),
    "最大摄氧量(VO2 Max) (ml/(kg·min))": ("vo2_max", "float"),
    "心率 [平均值] (count/min)": ("avg_hr_bpm", "float"),
    "心率 [最小值] (count/min)": ("min_hr_bpm", "float"),
    "心率 [最大] (count/min)": ("max_hr_bpm", "float"),
    "呼吸频率 (count/min)": ("respiratory_rate", "float"),
    "血氧饱和度 (%)": ("blood_oxygen_pct", "float"),
    "Apple 睡眠手腕温度 (degC)": ("wrist_temperature", "float"),
    "步行速度 (km/hr)": ("walking_speed_kmh", "float"),
    "步行心率均值 (count/min)": ("walking_hr_avg", "float"),
    "日照时长 (min)": ("sunlight_minutes", "float"),
    "攀登楼层数 (count)": ("flights_climbed", "float"),
    "睡眠分析 [Total] (hr)": ("sleep_duration_hours", "float"),
    "睡眠分析 [核心] (hr)": ("core_sleep_minutes", "hours_to_minutes"),
    "睡眠分析 [深度] (hr)": ("deep_sleep_minutes", "hours_to_minutes"),
    "睡眠分析 [快速动眼期] (hr)": ("rem_sleep_minutes", "hours_to_minutes"),
    "睡眠分析 [清醒] (hr)": ("awake_minutes", "hours_to_minutes"),
}

# ===== 运动记录: 中文CSV列名 -> DB字段名 =====
_WORKOUT_CSV_TO_DB: dict[str, tuple[str, str]] = {
    "Workout Type": ("workout_type", "str"),
    "Start": ("start_time", "datetime"),
    "End": ("end_time", "datetime"),
    "Duration": ("duration", "duration_str"),
    "距离 (km)": ("distance", "float"),
    "活动能量 (kJ)": ("active_energy_kj", "float"),
    "静息能量 (kJ)": ("_resting_energy_kj", "float"),
    "平均心率 (count/min)": ("heart_rate_avg", "float"),
    "最大心率 (count/min)": ("heart_rate_max", "float"),
    "强度 (kcal/hr·kg)": ("intensity", "float"),
    "平均速度 (km/小时)": ("avg_speed_kmh", "float"),
    "最大速度 (km/小时)": ("max_speed_kmh", "float"),
    "步数": ("steps", "int"),
    "步频 (spm)": ("cadence_spm", "float"),
    "温度 (degC)": ("temperature", "float"),
    "湿度 (%)": ("humidity", "float"),
    "位置": ("location", "str"),
}

# ===== 运动详情: 文件名中的中文指标名 -> metric_type =====
_METRIC_NAME_MAP: dict[str, str] = {
    "心率": "heart_rate",
    "心率恢复": "heart_rate_recovery",
    "步数": "steps",
    "步行 + 跑步距离": "distance",
    "活动能量": "active_energy",
    "静息能量": "resting_energy",
    "骑行距离": "cycling_distance",
    "攀登楼层数": "flights_climbed",
}

# 运动详情文件名解析正则: {运动类型}-{指标}-{YYYYMMDD_HHMMSS}.csv
_WORKOUT_DETAIL_PATTERN = re.compile(r"^(.+?)-([^-]+)-(\d{8}_\d{6})\.csv$")

# ECG文件名匹配
_ECG_PATTERN = re.compile(r"^ECG-")


def _parse_datetime_str(value: Any) -> datetime:
    """解析日期时间字符串."""
    s = str(value).strip()
    for fmt in (
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d %H:%M:%S%z",
        "%Y-%m-%d %H:%M:%S %z",
    ):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        pass
    try:
        return datetime.strptime(s[:10], "%Y-%m-%d")
    except ValueError:
        raise ValueError(f"无法解析日期时间: {s}") from None


def _parse_duration(duration_str: str) -> float:
    """解析 Duration 字段 (HH:MM:SS) 为分钟数."""
    parts = duration_str.strip().split(":")
    if len(parts) == 3:
        h, m, s = int(parts[0]), int(parts[1]), int(parts[2])
        return h * 60.0 + m + s / 60.0
    if len(parts) == 2:
        m, s = int(parts[0]), int(parts[1])
        return m + s / 60.0
    return float(duration_str)


def _parse_filename_timestamp(ts: str) -> datetime:
    """解析文件名中的时间戳 (YYYYMMDD_HHMMSS) 为 datetime."""
    return datetime.strptime(ts, "%Y%m%d_%H%M%S")


@dataclass
class ImportStats:
    """导入统计信息."""

    daily_imported: int = 0
    daily_skipped: int = 0
    workout_imported: int = 0
    workout_skipped: int = 0
    workout_samples_imported: int = 0
    workout_samples_files: int = 0
    ecg_imported: int = 0
    ecg_skipped: int = 0
    weight_records_imported: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def total_imported(self) -> int:
        return (
            self.daily_imported
            + self.workout_imported
            + self.workout_samples_imported
            + self.ecg_imported
            + self.weight_records_imported
        )

    def summary(self) -> str:
        lines = [
            "=== Health Auto Export 导入报告 ===",
            f"每日汇总: {self.daily_imported} 条导入, {self.daily_skipped} 条跳过",
            f"运动记录: {self.workout_imported} 条导入, {self.workout_skipped} 条跳过",
            f"运动详情: {self.workout_samples_imported} 条采样 ({self.workout_samples_files} 个文件)",
            f"ECG记录: {self.ecg_imported} 条导入, {self.ecg_skipped} 条跳过",
            f"体重记录: {self.weight_records_imported} 条导入",
            f"总计导入: {self.total_imported} 条",
        ]
        if self.errors:
            lines.append(f"错误: {len(self.errors)} 个")
            for err in self.errors[:10]:
                lines.append(f"  - {err}")
        return "\n".join(lines)


class HealthAutoExportImporter:
    """Health Auto Export CSV 数据导入器.

    读取 Health Auto Export app 导出的 CSV 文件并导入健康数据库.
    支持完整数据导入或按类别单独导入.
    """

    def __init__(self, user_id: str, thread_id: str, *, agent_id: str) -> None:
        self.user_id = user_id
        self.thread_id = thread_id
        self.agent_id = agent_id
        self._dao: AsyncHealthDAO | None = None
        self.stats = ImportStats()

    async def _get_dao(self) -> AsyncHealthDAO:
        if self._dao is None:
            db_manager = await create_async_health_data_db_manager(
                self.user_id,
                self.thread_id,
                agent_id=self.agent_id,
            )
            self._dao = AsyncHealthDAO(db_manager.session_factory)
        return self._dao

    # ===== 每日汇总 =====

    async def import_daily(self, csv_path: str | Path) -> int:
        """导入每日健康汇总 CSV (HealthAutoExport-*.csv).

        该文件包含每日活动,体征,睡眠等所有指标.
        同时为有体重数据的日期创建 weight_record.
        """
        df = pd.read_csv(csv_path)
        if df.empty:
            logger.warning("每日汇总CSV为空: %s", csv_path)
            return 0

        dao = await self._get_dao()
        count = 0

        for _, row in df.iterrows():
            try:
                record = self._map_daily_row(row)
                await dao.upsert_daily_summary(record)
                count += 1
                self.stats.daily_imported += 1

                if record.get("body_mass_kg") is not None:
                    try:
                        weight_data = {
                            "weight_kg": record["body_mass_kg"],
                            "body_fat_pct": record.get("body_fat_pct"),
                            "muscle_mass_kg": record.get("muscle_mass_kg"),
                            "recorded_at": datetime.combine(
                                record["record_date"],
                                datetime.min.time(),
                            ),
                            "source": "apple_health",
                        }
                        await dao.create_weight_record(weight_data)
                        self.stats.weight_records_imported += 1
                    except Exception as we:
                        self.stats.errors.append(
                            f"weight_record for {record.get('record_date')}: {we}",
                        )
                        logger.warning("导入weight_record失败: %s", we)
            except Exception as e:
                self.stats.errors.append(f"daily row: {e}")
                self.stats.daily_skipped += 1
                logger.warning("导入daily行失败: %s", e)

        logger.info("每日汇总导入完成: %d/%d", count, len(df))
        return count

    def _map_daily_row(self, row: pd.Series) -> dict[str, Any]:
        """映射每日汇总CSV行到DB字段."""
        record: dict[str, Any] = {"data_source": "apple_health"}

        for csv_col, (db_field, convert_type) in _DAILY_CSV_TO_DB.items():
            if csv_col not in row.index:
                continue
            value = row[csv_col]
            if pd.isna(value):
                continue

            converted = self._convert_value(value, convert_type)
            if converted is None:
                continue

            if db_field == "record_date":
                record[db_field] = date.fromisoformat(str(value).strip()[:10])
            else:
                record[db_field] = converted

        # 从睡眠阶段精确计算 asleep_minutes: deep + rem + core
        # 仅在有阶段细节时才计算, 无阶段时不做近似(近似无意义)
        sleep_parts = []
        for key in ("deep_sleep_minutes", "rem_sleep_minutes", "core_sleep_minutes"):
            if key in record and record[key] is not None and record[key] > 0:
                sleep_parts.append(record[key])
        if sleep_parts:
            record["asleep_minutes"] = int(sum(sleep_parts))

        # 计算 sleep_efficiency: asleep / (asleep + awake) * 100
        # 仅在有阶段数据且 awake 有值时才计算
        asleep = record.get("asleep_minutes")
        awake = record.get("awake_minutes")
        if asleep and asleep > 0 and awake is not None and awake > 0:
            record["sleep_efficiency"] = round(asleep / (asleep + awake) * 100, 1)

        if "record_date" not in record:
            raise ValueError("缺少日期字段")

        return record

    @staticmethod
    def _convert_value(value: Any, convert_type: str) -> Any:
        """根据转换类型转换值."""
        if convert_type == "float":
            return float(value)
        if convert_type == "int":
            return int(float(value))
        if convert_type == "str":
            return str(value).strip()
        if convert_type == "date":
            return date.fromisoformat(str(value).strip()[:10])
        if convert_type == "datetime":
            return _parse_datetime_str(value)
        if convert_type == "kj_to_kcal":
            return round(float(value) * KJ_TO_KCAL, 2)
        if convert_type == "hours_to_minutes":
            return round(float(value) * 60, 2)
        if convert_type == "duration_str":
            return _parse_duration(str(value))
        return value

    # ===== 运动记录 =====

    async def import_workouts(self, csv_path: str | Path) -> int:
        """导入运动记录 CSV (Workouts-*.csv)."""
        df = pd.read_csv(csv_path)
        if df.empty:
            logger.warning("运动记录CSV为空: %s", csv_path)
            return 0

        dao = await self._get_dao()
        count = 0

        for _, row in df.iterrows():
            try:
                record = self._map_workout_row(row)
                await dao.upsert_workout_record(record)
                count += 1
                self.stats.workout_imported += 1
            except Exception as e:
                self.stats.errors.append(f"workout row: {e}")
                self.stats.workout_skipped += 1
                logger.warning("导入workout行失败: %s", e)

        logger.info("运动记录导入完成: %d/%d", count, len(df))
        return count

    def _map_workout_row(self, row: pd.Series) -> dict[str, Any]:
        """映射运动记录CSV行到DB字段."""
        record: dict[str, Any] = {"source": "apple_health"}

        for csv_col, (db_field, convert_type) in _WORKOUT_CSV_TO_DB.items():
            if csv_col not in row.index:
                continue
            value = row[csv_col]
            if pd.isna(value):
                continue

            if db_field.startswith("_"):
                continue

            converted = self._convert_value(value, convert_type)
            if converted is not None:
                record[db_field] = converted

        # 计算卡路里: 活动能量(kJ) + 静息能量(kJ) -> kcal
        active_kj = row.get("活动能量 (kJ)")
        resting_kj = row.get("静息能量 (kJ)")
        if active_kj is not None and pd.notna(active_kj):
            total_kj = float(active_kj)
            if resting_kj is not None and pd.notna(resting_kj):
                total_kj += float(resting_kj)
            record["calories"] = round(total_kj * KJ_TO_KCAL, 2)

        if "start_time" not in record:
            raise ValueError("缺少Start字段")
        if "workout_type" not in record:
            raise ValueError("缺少Workout Type字段")
        if "duration" not in record:
            record["duration"] = 0.0

        return record

    # ===== 运动详情时间序列 =====

    async def import_workout_samples(self, dir_path: str | Path) -> int:
        """导入运动详情时间序列数据.

        扫描目录中所有 {运动类型}-{指标}-{时间}.csv 文件,
        解析文件名和内容, 批量写入 workout_samples 表.
        """
        dir_path = Path(dir_path)
        if not dir_path.is_dir():
            logger.warning("目录不存在: %s", dir_path)
            return 0

        dao = await self._get_dao()
        total_rows = 0
        file_count = 0

        for csv_file in sorted(dir_path.iterdir()):
            if csv_file.suffix.lower() != ".csv":
                continue
            if csv_file.name.startswith("HealthAutoExport-"):
                continue
            if csv_file.name.startswith("Workouts-"):
                continue
            if csv_file.name.startswith("ECG-"):
                continue
            if csv_file.name.startswith("Symptoms-"):
                continue

            match = _WORKOUT_DETAIL_PATTERN.match(csv_file.name)
            if not match:
                continue

            workout_type = match.group(1)
            metric_name_cn = match.group(2)
            timestamp_str = match.group(3)

            metric_type = _METRIC_NAME_MAP.get(metric_name_cn)
            if metric_type is None:
                logger.debug("跳过未知指标类型: %s", metric_name_cn)
                continue

            try:
                workout_start = _parse_filename_timestamp(timestamp_str)
                rows = self._parse_workout_detail_csv(
                    csv_file,
                    workout_start,
                    workout_type,
                    metric_type,
                )
                if rows:
                    for row in rows:
                        await dao.create_workout_sample(row)
                    total_rows += len(rows)
                    file_count += 1
                    self.stats.workout_samples_files += 1
                    self.stats.workout_samples_imported += len(rows)
            except Exception as e:
                self.stats.errors.append(f"workout_sample {csv_file.name}: {e}")
                logger.warning("导入workout_sample失败 %s: %s", csv_file.name, e)

        logger.info("运动详情导入完成: %d 条采样, %d 个文件", total_rows, file_count)
        return total_rows

    def _parse_workout_detail_csv(
        self,
        csv_path: Path,
        workout_start: datetime,
        workout_type: str,
        metric_type: str,
    ) -> list[dict[str, Any]]:
        """解析单个运动详情CSV文件."""
        df = pd.read_csv(csv_path)
        if df.empty:
            return []

        rows: list[dict[str, Any]] = []
        is_hr_type = metric_type in {"heart_rate", "heart_rate_recovery"}

        for _, row in df.iterrows():
            try:
                dt_col = row.iloc[0]
                if pd.isna(dt_col):
                    continue

                sample_time = _parse_datetime_str(dt_col)

                record: dict[str, Any] = {
                    "workout_start_time": workout_start,
                    "workout_type": workout_type,
                    "metric_type": metric_type,
                    "sample_time": sample_time,
                }

                if is_hr_type and len(row) >= 4:
                    min_val = row.iloc[1]
                    max_val = row.iloc[2]
                    avg_val = row.iloc[3]
                    if pd.notna(min_val):
                        record["value_min"] = float(min_val)
                    if pd.notna(max_val):
                        record["value_max"] = float(max_val)
                    if pd.notna(avg_val):
                        record["value_avg"] = float(avg_val)
                elif len(row) >= 2:
                    val = row.iloc[1]
                    if pd.notna(val):
                        record["value_avg"] = float(val)

                source = row.iloc[-1] if len(row) > 2 else None
                if source is not None and pd.notna(source):
                    record["source"] = str(source).strip()

                rows.append(record)
            except Exception as e:
                logger.debug("解析采样行失败: %s", e)
                continue

        return rows

    # ===== ECG 记录 =====

    async def import_ecg(self, csv_path: str | Path) -> int:
        """导入 ECG CSV (ECG-*.csv).

        ECG文件不是标准CSV, 而是key-value格式:
        开始,2024-12-27 07:50:32 +0800
        结束,2024-12-27 07:51:02 +0800
        分类,窦性心律
        症状,无
        (后跟采样数据行)
        """
        csv_path = Path(csv_path)
        if not csv_path.exists():
            logger.warning("ECG文件不存在: %s", csv_path)
            return 0

        dao = await self._get_dao()
        records = self._parse_ecg_file(csv_path)
        count = 0

        for record in records:
            try:
                await dao.create_ecg_record(record)
                count += 1
                self.stats.ecg_imported += 1
            except Exception as e:
                self.stats.errors.append(f"ecg record: {e}")
                self.stats.ecg_skipped += 1
                logger.warning("导入ECG记录失败: %s", e)

        logger.info("ECG记录导入完成: %d/%d", count, len(records))
        return count

    def _parse_ecg_file(self, csv_path: Path) -> list[dict[str, Any]]:
        """解析ECG文件, 提取多条ECG记录."""
        content = csv_path.read_text(encoding="utf-8")
        lines = content.strip().split("\n")

        records: list[dict[str, Any]] = []
        current: dict[str, Any] = {}

        for line in lines:
            line = line.strip()
            if not line:
                continue

            if line.startswith("开始,"):
                if current and "start_time" in current:
                    records.append(current)
                current = {"source": "apple_health"}
                ts_str = line.split(",", 1)[1].strip()
                try:
                    current["start_time"] = _parse_datetime_str(ts_str)
                except ValueError:
                    current["start_time"] = _parse_datetime_str(ts_str[:19])
            elif line.startswith("结束,") and current is not None:
                ts_str = line.split(",", 1)[1].strip()
                try:
                    current["end_time"] = _parse_datetime_str(ts_str)
                except ValueError:
                    current["end_time"] = _parse_datetime_str(ts_str[:19])
            elif line.startswith("分类,") and current is not None:
                current["classification"] = line.split(",", 1)[1].strip()
            elif line.startswith("症状,") and current is not None:
                val = line.split(",", 1)[1].strip()
                if val and val != "无":
                    current["symptoms"] = val
            elif line.startswith("备注,") and current is not None:
                val = line.split(",", 1)[1].strip()
                if val:
                    current["note"] = val

        if current and "start_time" in current:
            records.append(current)

        return records

    # ===== 一键导入 =====

    async def import_all(self, dir_path: str | Path) -> ImportStats:
        """一键导入目录中所有数据.

        自动识别以下文件:
        - HealthAutoExport-*.csv -> 每日汇总
        - Workouts-*.csv -> 运动记录
        - ECG-*.csv -> ECG记录
        - {运动类型}-{指标}-{时间}.csv -> 运动详情
        """
        dir_path = Path(dir_path)
        if not dir_path.is_dir():
            raise ValueError(f"目录不存在: {dir_path}")

        logger.info("开始导入 Health Auto Export 数据: %s", dir_path)

        # 每日汇总
        daily_files = list(dir_path.glob("HealthAutoExport-*.csv"))
        for f in daily_files:
            logger.info("导入每日汇总: %s", f.name)
            await self.import_daily(f)

        # 运动记录
        workout_files = list(dir_path.glob("Workouts-*.csv"))
        for f in workout_files:
            logger.info("导入运动记录: %s", f.name)
            await self.import_workouts(f)

        # 运动详情
        await self.import_workout_samples(dir_path)

        # ECG
        ecg_files = list(dir_path.glob("ECG-*.csv"))
        for f in ecg_files:
            logger.info("导入ECG: %s", f.name)
            await self.import_ecg(f)

        logger.info(self.stats.summary())
        return self.stats
