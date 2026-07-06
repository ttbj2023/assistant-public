"""HealthAutoExportImporter 单元测试.

测试CSV健康数据导入器的业务逻辑: 字段映射, 单位转换, 统计汇总.
Mock外部依赖: pandas CSV读取, HealthDAO数据库操作, 文件系统.
"""

from __future__ import annotations

from datetime import date, datetime
from unittest.mock import AsyncMock, patch

import pandas as pd
import pytest

from src.storage.importers.health_auto_export_importer import (
    KJ_TO_KCAL,
    ImportStats,
    _parse_datetime_str,
    _parse_duration,
    _parse_filename_timestamp,
)


class TestParseDatetimeStr:
    """测试日期时间字符串解析."""

    def test_iso_format_with_timezone(self):
        result = _parse_datetime_str("2024-12-27T07:50:32+0800")
        assert result.year == 2024
        assert result.month == 12
        assert result.day == 27

    def test_iso_format_without_timezone(self):
        result = _parse_datetime_str("2024-12-27T07:50:32")
        assert result.year == 2024
        assert result.hour == 7
        assert result.minute == 50

    def test_space_separated_format(self):
        result = _parse_datetime_str("2024-12-27 07:50:32")
        assert result.year == 2024
        assert result.second == 32

    def test_date_only_format(self):
        result = _parse_datetime_str("2024-12-27")
        assert result.year == 2024
        assert result.month == 12
        assert result.day == 27

    def test_space_hour_minute(self):
        result = _parse_datetime_str("2024-12-27 07:50")
        assert result.hour == 7
        assert result.minute == 50

    def test_invalid_format_raises(self):
        with pytest.raises(ValueError, match="无法解析日期时间"):
            _parse_datetime_str("not-a-date-at-all")


class TestParseDuration:
    """测试时长字符串解析."""

    def test_hhmmss_format(self):
        assert _parse_duration("1:30:00") == 90.0

    def test_mmss_format(self):
        assert _parse_duration("45:30") == 45.5

    def test_numeric_string(self):
        assert _parse_duration("60") == 60.0

    def test_zero_duration(self):
        assert _parse_duration("0:00:00") == 0.0


class TestParseFilenameTimestamp:
    """测试文件名时间戳解析."""

    def test_standard_format(self):
        result = _parse_filename_timestamp("20241227_075032")
        assert result.year == 2024
        assert result.month == 12
        assert result.day == 27
        assert result.hour == 7
        assert result.minute == 50
        assert result.second == 32


class TestImportStats:
    """测试导入统计."""

    def test_total_imported_sums_all_categories(self):
        stats = ImportStats(
            daily_imported=10,
            workout_imported=5,
            workout_samples_imported=100,
            ecg_imported=3,
            weight_records_imported=8,
        )
        assert stats.total_imported == 126

    def test_total_imported_default_zero(self):
        stats = ImportStats()
        assert stats.total_imported == 0

    def test_summary_includes_all_categories(self):
        stats = ImportStats(
            daily_imported=10,
            daily_skipped=2,
            workout_imported=5,
            workout_samples_imported=100,
            workout_samples_files=3,
            ecg_imported=3,
            weight_records_imported=8,
        )
        summary = stats.summary()
        assert "每日汇总: 10 条导入, 2 条跳过" in summary
        assert "运动记录: 5 条导入" in summary
        assert "ECG记录: 3 条导入" in summary
        assert "体重记录: 8 条导入" in summary
        assert "总计导入: 126 条" in summary

    def test_summary_with_errors(self):
        stats = ImportStats(errors=["error1", "error2"])
        summary = stats.summary()
        assert "错误: 2 个" in summary
        assert "- error1" in summary

    def test_summary_limits_errors_to_10(self):
        errors = [f"error{i}" for i in range(15)]
        stats = ImportStats(errors=errors)
        summary = stats.summary()
        assert "错误: 15 个" in summary


class TestHealthAutoExportImporterConvertValue:
    """测试值转换逻辑."""

    def test_float_conversion(self):
        from src.storage.importers.health_auto_export_importer import (
            HealthAutoExportImporter,
        )

        result = HealthAutoExportImporter._convert_value("3.14", "float")
        assert result == pytest.approx(3.14)

    def test_int_conversion(self):
        from src.storage.importers.health_auto_export_importer import (
            HealthAutoExportImporter,
        )

        result = HealthAutoExportImporter._convert_value("42.7", "int")
        assert result == 42

    def test_str_conversion(self):
        from src.storage.importers.health_auto_export_importer import (
            HealthAutoExportImporter,
        )

        result = HealthAutoExportImporter._convert_value("  hello  ", "str")
        assert result == "hello"

    def test_kj_to_kcal_conversion(self):
        from src.storage.importers.health_auto_export_importer import (
            HealthAutoExportImporter,
        )

        result = HealthAutoExportImporter._convert_value("4.184", "kj_to_kcal")
        assert result == pytest.approx(1.0, rel=0.01)

    def test_hours_to_minutes_conversion(self):
        from src.storage.importers.health_auto_export_importer import (
            HealthAutoExportImporter,
        )

        result = HealthAutoExportImporter._convert_value("1.5", "hours_to_minutes")
        assert result == pytest.approx(90.0)

    def test_unknown_type_passes_through(self):
        from src.storage.importers.health_auto_export_importer import (
            HealthAutoExportImporter,
        )

        result = HealthAutoExportImporter._convert_value("value", "unknown_type")
        assert result == "value"


class TestHealthAutoExportImporterMapDailyRow:
    """测试每日汇总行映射."""

    @pytest.fixture
    def importer(self):
        from src.storage.importers.health_auto_export_importer import (
            HealthAutoExportImporter,
        )

        return HealthAutoExportImporter("u1", "t1", agent_id="a1")

    def test_basic_daily_row_mapping(self, importer):
        row = pd.Series({
            "日期/时间": "2024-12-27",
            "步数 (count)": 10000,
            "活动能量 (kJ)": 500.0,
            "体重 (kg)": 70.5,
        })
        result = importer._map_daily_row(row)
        assert result["record_date"] == date(2024, 12, 27)
        assert result["steps"] == 10000
        assert result["active_energy_kcal"] == pytest.approx(
            500.0 * KJ_TO_KCAL, rel=0.01
        )
        assert result["body_mass_kg"] == 70.5

    def test_sleep_stage_aggregation(self, importer):
        row = pd.Series({
            "日期/时间": "2024-12-27",
            "睡眠分析 [深度] (hr)": 2.0,
            "睡眠分析 [快速动眼期] (hr)": 1.5,
            "睡眠分析 [核心] (hr)": 3.0,
            "睡眠分析 [清醒] (hr)": 0.5,
        })
        result = importer._map_daily_row(row)
        assert result["asleep_minutes"] == 390
        assert result["sleep_efficiency"] == pytest.approx(92.9, rel=0.1)

    def test_missing_date_raises(self, importer):
        row = pd.Series({"步数 (count)": 1000})
        with pytest.raises(ValueError, match="缺少日期字段"):
            importer._map_daily_row(row)

    def test_nan_values_skipped(self, importer):
        row = pd.Series({
            "日期/时间": "2024-12-27",
            "步数 (count)": float("nan"),
        })
        result = importer._map_daily_row(row)
        assert "steps" not in result

    def test_data_source_is_apple_health(self, importer):
        row = pd.Series({"日期/时间": "2024-12-27"})
        result = importer._map_daily_row(row)
        assert result["data_source"] == "apple_health"


class TestHealthAutoExportImporterMapWorkoutRow:
    """测试运动记录行映射."""

    @pytest.fixture
    def importer(self):
        from src.storage.importers.health_auto_export_importer import (
            HealthAutoExportImporter,
        )

        return HealthAutoExportImporter("u1", "t1", agent_id="a1")

    def test_basic_workout_row_mapping(self, importer):
        row = pd.Series({
            "Workout Type": "Running",
            "Start": "2024-12-27T07:00:00",
            "End": "2024-12-27T08:00:00",
            "Duration": "1:00:00",
            "距离 (km)": 10.0,
            "活动能量 (kJ)": 500.0,
        })
        result = importer._map_workout_row(row)
        assert result["workout_type"] == "Running"
        assert result["duration"] == 60.0
        assert result["distance"] == 10.0
        assert "calories" in result

    def test_calories_includes_resting_energy(self, importer):
        row = pd.Series({
            "Workout Type": "Walking",
            "Start": "2024-12-27T07:00:00",
            "活动能量 (kJ)": 200.0,
            "静息能量 (kJ)": 100.0,
        })
        result = importer._map_workout_row(row)
        expected_kcal = (200.0 + 100.0) * KJ_TO_KCAL
        assert result["calories"] == pytest.approx(expected_kcal, rel=0.01)

    def test_missing_start_time_raises(self, importer):
        row = pd.Series({"Workout Type": "Running"})
        with pytest.raises(ValueError, match="缺少Start字段"):
            importer._map_workout_row(row)

    def test_missing_workout_type_raises(self, importer):
        row = pd.Series({"Start": "2024-12-27T07:00:00"})
        with pytest.raises(ValueError, match="缺少Workout Type字段"):
            importer._map_workout_row(row)

    def test_default_duration_when_missing(self, importer):
        row = pd.Series({
            "Workout Type": "Yoga",
            "Start": "2024-12-27T07:00:00",
        })
        result = importer._map_workout_row(row)
        assert result["duration"] == 0.0

    def test_underscore_fields_skipped(self, importer):
        row = pd.Series({
            "Workout Type": "Running",
            "Start": "2024-12-27T07:00:00",
            "Duration": "0:30:00",
            "静息能量 (kJ)": 100.0,
        })
        result = importer._map_workout_row(row)
        assert "_resting_energy_kj" not in result


class TestHealthAutoExportImporterParseEcgFile:
    """测试ECG文件解析."""

    @pytest.fixture
    def importer(self):
        from src.storage.importers.health_auto_export_importer import (
            HealthAutoExportImporter,
        )

        return HealthAutoExportImporter("u1", "t1", agent_id="a1")

    def test_parse_single_ecg_record(self, importer, tmp_path):
        content = (
            "开始,2024-12-27 07:50:32 +0800\n"
            "结束,2024-12-27 07:51:02 +0800\n"
            "分类,窦性心律\n"
            "症状,无\n"
        )
        ecg_file = tmp_path / "ECG-test.csv"
        ecg_file.write_text(content, encoding="utf-8")

        records = importer._parse_ecg_file(ecg_file)
        assert len(records) == 1
        assert records[0]["classification"] == "窦性心律"
        assert records[0]["source"] == "apple_health"
        assert "symptoms" not in records[0]

    def test_parse_ecg_with_symptoms(self, importer, tmp_path):
        content = (
            "开始,2024-12-27 07:50:32\n"
            "结束,2024-12-27 07:51:02\n"
            "分类,房颤\n"
            "症状,心悸\n"
            "备注,测试备注\n"
        )
        ecg_file = tmp_path / "ECG-test.csv"
        ecg_file.write_text(content, encoding="utf-8")

        records = importer._parse_ecg_file(ecg_file)
        assert len(records) == 1
        assert records[0]["symptoms"] == "心悸"
        assert records[0]["note"] == "测试备注"

    def test_parse_multiple_ecg_records(self, importer, tmp_path):
        content = (
            "开始,2024-12-27 07:50:32\n"
            "结束,2024-12-27 07:51:02\n"
            "分类,窦性心律\n"
            "症状,无\n"
            "开始,2024-12-28 08:00:00\n"
            "结束,2024-12-28 08:00:30\n"
            "分类,窦性心律\n"
            "症状,无\n"
        )
        ecg_file = tmp_path / "ECG-test.csv"
        ecg_file.write_text(content, encoding="utf-8")

        records = importer._parse_ecg_file(ecg_file)
        assert len(records) == 2

    def test_parse_empty_file_returns_empty(self, importer, tmp_path):
        ecg_file = tmp_path / "ECG-empty.csv"
        ecg_file.write_text("", encoding="utf-8")
        records = importer._parse_ecg_file(ecg_file)
        assert records == []


class TestHealthAutoExportImporterImportDaily:
    """测试每日汇总CSV导入."""

    @pytest.fixture
    def importer(self):
        from src.storage.importers.health_auto_export_importer import (
            HealthAutoExportImporter,
        )

        return HealthAutoExportImporter("u1", "t1", agent_id="a1")

    @pytest.mark.asyncio
    async def test_import_daily_empty_csv_returns_zero(self, importer, tmp_path):
        csv_file = tmp_path / "empty.csv"
        csv_file.write_text("日期/时间\n", encoding="utf-8")

        result = await importer.import_daily(csv_file)
        assert result == 0

    @pytest.mark.asyncio
    async def test_import_daily_with_valid_data(self, importer, tmp_path):
        csv_content = "日期/时间,步数 (count)\n2024-12-27,10000\n"
        csv_file = tmp_path / "daily.csv"
        csv_file.write_text(csv_content, encoding="utf-8")

        mock_dao = AsyncMock()
        mock_dao.upsert_daily_summary = AsyncMock()

        with patch.object(importer, "_get_dao", return_value=mock_dao):
            result = await importer.import_daily(csv_file)
        assert result == 1
        assert importer.stats.daily_imported == 1

    @pytest.mark.asyncio
    async def test_import_daily_creates_weight_record(self, importer, tmp_path):
        csv_content = "日期/时间,体重 (kg),体脂百分比 (%)\n2024-12-27,70.5,18.0\n"
        csv_file = tmp_path / "daily.csv"
        csv_file.write_text(csv_content, encoding="utf-8")

        mock_dao = AsyncMock()

        with patch.object(importer, "_get_dao", return_value=mock_dao):
            result = await importer.import_daily(csv_file)

        assert result == 1
        assert importer.stats.weight_records_imported == 1
        mock_dao.create_weight_record.assert_called_once()

    @pytest.mark.asyncio
    async def test_import_daily_skips_invalid_rows(self, importer, tmp_path):
        csv_content = "日期/时间,步数 (count)\nbad-date,10000\n2024-12-27,5000\n"
        csv_file = tmp_path / "daily.csv"
        csv_file.write_text(csv_content, encoding="utf-8")

        mock_dao = AsyncMock()

        with patch.object(importer, "_get_dao", return_value=mock_dao):
            result = await importer.import_daily(csv_file)

        assert result == 1
        assert importer.stats.daily_skipped == 1


class TestHealthAutoExportImporterImportWorkouts:
    """测试运动记录导入."""

    @pytest.fixture
    def importer(self):
        from src.storage.importers.health_auto_export_importer import (
            HealthAutoExportImporter,
        )

        return HealthAutoExportImporter("u1", "t1", agent_id="a1")

    @pytest.mark.asyncio
    async def test_import_workouts_empty_csv(self, importer, tmp_path):
        csv_file = tmp_path / "empty.csv"
        csv_file.write_text("Workout Type\n", encoding="utf-8")
        result = await importer.import_workouts(csv_file)
        assert result == 0

    @pytest.mark.asyncio
    async def test_import_workouts_with_valid_data(self, importer, tmp_path):
        csv_content = (
            "Workout Type,Start,End,Duration\n"
            "Running,2024-12-27T07:00:00,2024-12-27T08:00:00,1:00:00\n"
        )
        csv_file = tmp_path / "workouts.csv"
        csv_file.write_text(csv_content, encoding="utf-8")

        mock_dao = AsyncMock()

        with patch.object(importer, "_get_dao", return_value=mock_dao):
            result = await importer.import_workouts(csv_file)

        assert result == 1
        assert importer.stats.workout_imported == 1


class TestHealthAutoExportImporterParseWorkoutDetailCsv:
    """测试运动详情CSV解析."""

    @pytest.fixture
    def importer(self):
        from src.storage.importers.health_auto_export_importer import (
            HealthAutoExportImporter,
        )

        return HealthAutoExportImporter("u1", "t1", agent_id="a1")

    def test_parse_heart_rate_csv(self, importer, tmp_path):
        csv_content = (
            "时间,最小心率,最大心率,平均心率,来源\n"
            "2024-12-27 07:01:00,80,120,100,Apple Watch\n"
        )
        csv_file = tmp_path / "test.csv"
        csv_file.write_text(csv_content, encoding="utf-8")

        rows = importer._parse_workout_detail_csv(
            csv_file, datetime(2024, 12, 27, 7, 0, 0), "Running", "heart_rate"
        )
        assert len(rows) == 1
        assert rows[0]["metric_type"] == "heart_rate"
        assert rows[0]["value_min"] == 80
        assert rows[0]["value_max"] == 120
        assert rows[0]["value_avg"] == 100

    def test_parse_steps_csv(self, importer, tmp_path):
        csv_content = "时间,步数\n2024-12-27 07:01:00,1500\n"
        csv_file = tmp_path / "test.csv"
        csv_file.write_text(csv_content, encoding="utf-8")

        rows = importer._parse_workout_detail_csv(
            csv_file, datetime(2024, 12, 27, 7, 0, 0), "Walking", "steps"
        )
        assert len(rows) == 1
        assert rows[0]["value_avg"] == 1500.0

    def test_parse_empty_csv_returns_empty(self, importer, tmp_path):
        csv_file = tmp_path / "empty.csv"
        csv_file.write_text("时间\n", encoding="utf-8")

        rows = importer._parse_workout_detail_csv(
            csv_file, datetime(2024, 12, 27, 7, 0, 0), "Running", "heart_rate"
        )
        assert rows == []


class TestHealthAutoExportImporterImportEcg:
    """测试ECG导入."""

    @pytest.fixture
    def importer(self):
        from src.storage.importers.health_auto_export_importer import (
            HealthAutoExportImporter,
        )

        return HealthAutoExportImporter("u1", "t1", agent_id="a1")

    @pytest.mark.asyncio
    async def test_import_ecg_nonexistent_file(self, importer):
        result = await importer.import_ecg("/nonexistent/path.csv")
        assert result == 0

    @pytest.mark.asyncio
    async def test_import_ecg_valid_file(self, importer, tmp_path):
        content = (
            "开始,2024-12-27 07:50:32\n"
            "结束,2024-12-27 07:51:02\n"
            "分类,窦性心律\n"
            "症状,无\n"
        )
        ecg_file = tmp_path / "ECG-test.csv"
        ecg_file.write_text(content, encoding="utf-8")

        mock_dao = AsyncMock()

        with patch.object(importer, "_get_dao", return_value=mock_dao):
            result = await importer.import_ecg(ecg_file)

        assert result == 1
        assert importer.stats.ecg_imported == 1


class TestHealthAutoExportImporterImportWorkoutSamples:
    """测试运动详情目录导入."""

    @pytest.fixture
    def importer(self):
        from src.storage.importers.health_auto_export_importer import (
            HealthAutoExportImporter,
        )

        return HealthAutoExportImporter("u1", "t1", agent_id="a1")

    @pytest.mark.asyncio
    async def test_nonexistent_directory_returns_zero(self, importer):
        result = await importer.import_workout_samples("/nonexistent/dir")
        assert result == 0

    @pytest.mark.asyncio
    async def test_skips_non_matching_files(self, importer, tmp_path):
        (tmp_path / "HealthAutoExport-export.csv").write_text("x", encoding="utf-8")
        (tmp_path / "Workouts-export.csv").write_text("x", encoding="utf-8")
        (tmp_path / "ECG-test.csv").write_text("x", encoding="utf-8")
        (tmp_path / "Symptoms-test.csv").write_text("x", encoding="utf-8")
        (tmp_path / "random.txt").write_text("x", encoding="utf-8")

        mock_dao = AsyncMock()

        with patch.object(importer, "_get_dao", return_value=mock_dao):
            result = await importer.import_workout_samples(tmp_path)

        assert result == 0


class TestHealthAutoExportImporterImportAll:
    """测试一键导入."""

    @pytest.fixture
    def importer(self):
        from src.storage.importers.health_auto_export_importer import (
            HealthAutoExportImporter,
        )

        return HealthAutoExportImporter("u1", "t1", agent_id="a1")

    @pytest.mark.asyncio
    async def test_import_all_nonexistent_dir_raises(self, importer):
        with pytest.raises(ValueError, match="目录不存在"):
            await importer.import_all("/nonexistent/dir")

    @pytest.mark.asyncio
    async def test_import_all_empty_dir(self, importer, tmp_path):
        mock_dao = AsyncMock()

        with patch.object(importer, "_get_dao", return_value=mock_dao):
            stats = await importer.import_all(tmp_path)

        assert stats.total_imported == 0
