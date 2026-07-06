#!/usr/bin/env python3
"""Health Auto Export 数据导入脚本.

从 Health Auto Export app 导出的 CSV 目录导入健康数据到 assistant 健康数据库.

前置条件:
    1. 在 Health Auto Export app 中导出全部数据到一个目录
    2. 确保 user_id/thread_id/agent_id 对应的配置正确

使用示例:
    # 完整导入
    python scripts/import_health_auto_export.py \\
        --dir /path/to/HealthAutoExport_20260527220150 \\
        --user-id alice --thread-id main --agent-id health-assistant

    # 仅导入每日汇总和运动记录(跳过运动详情)
    python scripts/import_health_auto_export.py \\
        --dir /path/to/exports \\
        --user-id alice --thread-id main \\
        --daily --workouts --ecg

    # 预览模式 (不写入数据库)
    python scripts/import_health_auto_export.py \\
        --dir /path/to/exports \\
        --user-id alice --thread-id main \\
        --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

# 将项目根目录加入 sys.path
project_root = str(Path(__file__).resolve().parent.parent)
if project_root not in sys.path:
    sys.path.insert(0, project_root)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="从 Health Auto Export 导出目录导入健康数据",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--dir",
        required=True,
        help="Health Auto Export 导出目录路径",
    )
    parser.add_argument(
        "--user-id",
        required=True,
        help="目标用户ID",
    )
    parser.add_argument(
        "--thread-id",
        default="main",
        help="目标线程ID (默认: main)",
    )
    parser.add_argument(
        "--agent-id",
        default="health-assistant",
        help="目标Agent ID (默认: health-assistant)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="预览模式, 只显示文件信息不导入",
    )
    # 按类别导入
    select_group = parser.add_argument_group("选择性导入")
    select_group.add_argument(
        "--daily",
        action="store_true",
        help="仅导入每日汇总(含睡眠+体重)",
    )
    select_group.add_argument(
        "--workouts",
        action="store_true",
        help="仅导入运动记录",
    )
    select_group.add_argument(
        "--samples",
        action="store_true",
        help="仅导入运动详情时间序列",
    )
    select_group.add_argument(
        "--ecg",
        action="store_true",
        help="仅导入ECG记录",
    )
    return parser.parse_args()


def _scan_directory(dir_path: Path) -> dict[str, list[Path]]:
    """扫描导出目录, 分类文件."""
    files: dict[str, list[Path]] = {
        "daily": [],
        "workouts": [],
        "ecg": [],
        "samples": [],
        "other": [],
    }

    for f in sorted(dir_path.iterdir()):
        if not f.is_file():
            continue
        if f.suffix.lower() != ".csv":
            continue
        name = f.name
        if name.startswith("HealthAutoExport-"):
            files["daily"].append(f)
        elif name.startswith("Workouts-"):
            files["workouts"].append(f)
        elif name.startswith("ECG-"):
            files["ecg"].append(f)
        elif name.startswith("Symptoms-"):
            files["other"].append(f)
        else:
            files["samples"].append(f)

    return files


def _dry_run(dir_path: Path) -> None:
    """预览模式: 扫描并显示文件信息."""
    files = _scan_directory(dir_path)
    print(f"导出目录: {dir_path}")
    print()

    total_csv = sum(len(v) for v in files.values())
    print(f"共发现 {total_csv} 个 CSV 文件:")
    print()

    for category, paths in files.items():
        if not paths:
            continue
        print(f"  [{category}] {len(paths)} 个文件:")
        for p in paths[:5]:
            size_kb = p.stat().st_size / 1024
            print(f"    {p.name} ({size_kb:.1f}KB)")
        if len(paths) > 5:
            print(f"    ... 以及其他 {len(paths) - 5} 个文件")
        print()

    # 统计运动详情类型
    if files["samples"]:
        types: dict[str, int] = {}
        for p in files["samples"]:
            name = p.stem
            parts = name.rsplit("-", 2)
            if len(parts) >= 2:
                key = parts[-2] if len(parts) == 3 else parts[-1]
                types[key] = types.get(key, 0) + 1
        print("  运动详情指标分布:")
        for k, v in sorted(types.items(), key=lambda x: -x[1]):
            print(f"    {k}: {v} 个文件")


async def _import_data(args: argparse.Namespace) -> None:
    """执行实际导入."""
    from src.storage.importers.health_auto_export_importer import (
        HealthAutoExportImporter,
    )

    dir_path = Path(args.dir)
    importer = HealthAutoExportImporter(
        args.user_id,
        args.thread_id,
        agent_id=args.agent_id,
    )

    selective = args.daily or args.workouts or args.samples or args.ecg

    if selective:
        if args.daily:
            daily_files = list(dir_path.glob("HealthAutoExport-*.csv"))
            for f in daily_files:
                print(f"导入每日汇总: {f.name}")
                await importer.import_daily(f)

        if args.workouts:
            workout_files = list(dir_path.glob("Workouts-*.csv"))
            for f in workout_files:
                print(f"导入运动记录: {f.name}")
                await importer.import_workouts(f)

        if args.samples:
            print("导入运动详情...")
            await importer.import_workout_samples(dir_path)

        if args.ecg:
            ecg_files = list(dir_path.glob("ECG-*.csv"))
            for f in ecg_files:
                print(f"导入ECG: {f.name}")
                await importer.import_ecg(f)
    else:
        print(f"开始全量导入: {dir_path}")
        await importer.import_all(dir_path)

    print()
    print(importer.stats.summary())


async def main() -> None:
    args = _parse_args()
    dir_path = Path(args.dir)

    if not dir_path.is_dir():
        print(f"错误: 目录不存在: {dir_path}")
        sys.exit(1)

    if args.dry_run:
        _dry_run(dir_path)
    else:
        await _import_data(args)


if __name__ == "__main__":
    asyncio.run(main())
