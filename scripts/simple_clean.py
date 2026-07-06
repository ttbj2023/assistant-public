#!/usr/bin/env python3
"""简化清理工具 - 替代复杂的ProjectCleanupManager

专注于核心清理功能：
1. 根目录零散文件归档（白名单保护）
2. htmlcov目录清理
3. reports目录归档
4. 过期归档文件删除

代码量减少85%，维护成本大幅降低。
"""

import argparse
import fnmatch
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml
from rich.console import Console

# 确保项目根目录在Python路径中
project_root = Path(__file__).parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

console = Console()


class SimpleCleaner:
    """简化清理工具 - 专注核心功能"""

    def __init__(self, config_path: Path | None = None):
        """初始化清理工具

        Args:
            config_path: 配置文件路径，默认为 config/simple_cleanup_config.yaml
        """
        self.project_root = project_root
        self.config_path = config_path or (
            self.project_root / "config" / "simple_cleanup_config.yaml"
        )
        self.config = self._load_config()

        # 初始化路径
        self.docs_archive_dir = self.project_root / "docs" / "archive"
        self.archive_dir = self.project_root / "archive"
        self.reports_dir = self.project_root / "reports"
        self.reports_archive_dir = self.project_root / "reports" / "archive"

        # 确保归档目录存在
        self.docs_archive_dir.mkdir(parents=True, exist_ok=True)
        self.archive_dir.mkdir(parents=True, exist_ok=True)
        self.reports_archive_dir.mkdir(parents=True, exist_ok=True)

        # 统计信息
        self.stats = {
            "files_archived": 0,
            "directories_archived": 0,
            "files_deleted": 0,
            "directories_deleted": 0,
            "total_size": 0,
        }

    def _load_config(self) -> dict:
        """加载配置文件"""
        default_config = {
            "protected_files": {
                "markdown_files": ["CLAUDE.md", "README.md"],
                "config_files": [
                    "pyproject.toml",
                    "uv.lock",
                    "conftest.py",
                    ".gitignore",
                    "config.yaml",
                    ".env.example",
                    ".env",
                    ".env.test",
                ],
            },
            "ignore_root_files": {
                "=*",
            },
            "archive_rules": {
                "root_markdown_archive": "docs/archive",
                "root_other_archive": "archive",
                "reports_archive": "reports/archive",
            },
            "cleanup_rules": {
                "cleanup_directories": ["htmlcov"],
                "old_archives_days": 3,
                "archive_directories": ["archive", "docs/archive", "reports/archive"],
                "protected_runtime_patterns": ["*.pid"],
            },
            "exclude_directories": [
                "src",
                "scripts",
                "tests",
                "docs",
                "config",
                "data",
                "examples",
                "docker",
                ".git",
                ".claude",
                ".venv",
                ".vscode",
                ".github",
                "logs",
                "temp",
            ],
        }

        if self.config_path.exists():
            try:
                with open(self.config_path, encoding="utf-8") as f:
                    user_config = yaml.safe_load(f) or {}
                # 合并配置
                for key in default_config:
                    if (
                        key in user_config
                        and isinstance(default_config[key], dict)
                        and isinstance(user_config[key], dict)
                    ):
                        default_config[key].update(user_config[key])
            except Exception as e:
                console.print(f"[yellow]⚠️ 配置文件加载失败，使用默认配置: {e}[/yellow]")

        return default_config

    def _get_protected_files(self) -> set[str]:
        """获取受保护的文件列表"""
        protected = set()
        protected.update(self.config["protected_files"]["markdown_files"])
        protected.update(self.config["protected_files"]["config_files"])
        return protected

    def _should_ignore_root_file(self, name: str) -> bool:
        """检查根目录文件是否应被忽略(不归档)"""
        ignore_patterns = self.config.get("ignore_root_files", [])
        if not isinstance(ignore_patterns, (list, set)):
            return False
        for pattern in ignore_patterns:
            if pattern == name:
                return True
            if pattern.endswith("*") and name.startswith(pattern[:-1]):
                return True
        return False

    def _should_exclude_directory(self, path: Path) -> bool:
        """检查目录是否应该排除"""
        return path.name in self.config["exclude_directories"]

    def _is_protected_runtime_file(self, path: Path) -> bool:
        """检查是否为运行时保护文件(cleanup_old_archives 不删除).

        仅保护 *.pid 等"创建后不更新 mtime 但进程可能仍持有"的文件;
        日志不在此列, 靠 cutoff(mtime) 兜底——活跃日志持续写入, mtime 恒新不会进清理候选.
        """
        patterns = self.config.get("cleanup_rules", {}).get(
            "protected_runtime_patterns", []
        )
        return any(fnmatch.fnmatch(path.name, pat) for pat in patterns)

    def archive_root_files(self) -> dict[str, Any]:
        """归档根目录零散文件

        Returns:
            归档结果统计
        """
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        protected_files = self._get_protected_files()
        archived_count = 0
        archived_size = 0

        console.print("[blue]📁 开始归档根目录零散文件...[/blue]")

        # 获取根目录所有文件（不包括目录）
        for item in self.project_root.iterdir():
            if not item.is_file():
                continue

            # 跳过隐藏文件(dotfile): .git / .env / .dockerignore / .python-version 等.
            # Unix dotfile 语义即"配置/元数据", 清理工具不应触碰.
            # worktree 环境下 .git 是普通文件(指向 gitdir), 必须保护以免破坏 git.
            # 例外通过 protected_files 显式列出, 需要清理的 dotfile 后续再加入.
            if item.name.startswith("."):
                continue

            # 跳过受保护的文件
            if item.name in protected_files:
                console.print(f"[dim]  ✓ 跳过保护文件: {item.name}[/dim]")
                continue

            # 跳过应忽略的文件
            if self._should_ignore_root_file(item.name):
                console.print(f"[dim]  ✓ 跳过忽略文件: {item.name}[/dim]")
                continue

            # 处理MD文档
            if item.suffix == ".md":
                archive_path = (
                    self.docs_archive_dir / f"{item.stem}_{timestamp}{item.suffix}"
                )
                console.print(
                    f"[green]  📄 归档MD文档: {item.name} -> docs/archive/[/green]"
                )
            else:
                archive_path = (
                    self.archive_dir / f"{item.stem}_{timestamp}{item.suffix}"
                )
                console.print(f"[green]  📄 归档文件: {item.name} -> archive/[/green]")

            try:
                file_size = item.stat().st_size
                shutil.move(str(item), str(archive_path))
                archived_count += 1
                archived_size += file_size
                self.stats["files_archived"] += 1
                self.stats["total_size"] += file_size
            except Exception as e:
                console.print(f"[red]  ❌ 归档失败 {item.name}: {e}[/red]")

        result = {
            "archived_count": archived_count,
            "archived_size": archived_size,
        }
        console.print(
            f"[green]✅ 根目录文件归档完成，处理了 {archived_count} 个文件[/green]"
        )
        return result

    def cleanup_htmlcov(self) -> dict[str, Any]:
        """清理htmlcov目录

        Returns:
            清理结果统计
        """
        htmlcov_dir = self.project_root / "htmlcov"
        if not htmlcov_dir.exists():
            return {"cleaned": False, "reason": "htmlcov目录不存在"}

        try:
            # 删除整个目录及其内容
            dir_size = sum(
                f.stat().st_size for f in htmlcov_dir.rglob("*") if f.is_file()
            )
            shutil.rmtree(htmlcov_dir)
            self.stats["directories_deleted"] += 1
            self.stats["files_deleted"] += (
                len(list(htmlcov_dir.rglob("*"))) if htmlcov_dir.exists() else 0
            )
            self.stats["total_size"] += dir_size

            console.print(
                f"[green]✅ htmlcov目录已清理，释放 {dir_size:,} bytes[/green]"
            )
            return {"cleaned": True, "size_freed": dir_size}
        except Exception as e:
            console.print(f"[red]❌ 清理htmlcov目录失败: {e}[/red]")
            return {"cleaned": False, "error": str(e)}

    def archive_reports_directory(self) -> dict[str, Any]:
        """归档reports目录

        Returns:
            归档结果统计
        """
        if not self.reports_dir.exists():
            return {"archived": False, "reason": "reports目录不存在"}

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        archive_name = f"reports_{timestamp}"
        archive_path = self.reports_archive_dir / archive_name

        console.print("[blue]📁 开始归档reports目录...[/blue]")

        try:
            # 先移动reports目录内的所有内容到归档位置，而不是移动整个目录
            archive_path.mkdir(parents=True, exist_ok=True)

            # 移动reports目录内的所有文件和子目录
            for item in self.reports_dir.iterdir():
                if item.name != "archive":  # 不移动archive目录本身
                    dest_path = archive_path / item.name
                    if dest_path.exists():
                        # 如果目标已存在，添加时间戳
                        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                        dest_path = (
                            archive_path / f"{item.stem}_{timestamp}{item.suffix}"
                            if item.is_file()
                            else archive_path / f"{item.name}_{timestamp}"
                        )
                    shutil.move(str(item), str(dest_path))

            # 计算归档大小
            archived_size = sum(
                f.stat().st_size for f in archive_path.rglob("*") if f.is_file()
            )
            self.stats["directories_archived"] += 1
            self.stats["total_size"] += archived_size

            console.print(f"[green]✅ reports目录已归档到: {archive_name}/[/green]")
            return {
                "archived": True,
                "archive_path": str(archive_path),
                "size": archived_size,
            }
        except Exception as e:
            console.print(f"[red]❌ 归档reports目录失败: {e}[/red]")
            return {"archived": False, "error": str(e)}

    def cleanup_old_archives(self, days: int | None = None) -> dict[str, Any]:
        """清理过期的归档文件

        Args:
            days: 保留天数，None表示使用配置中的天数

        Returns:
            清理结果统计
        """
        retention_days = days or self.config["cleanup_rules"]["old_archives_days"]
        if retention_days <= 0:
            return {"cleaned_count": 0, "reason": "保留天数必须大于0"}

        cutoff_time = time.time() - (retention_days * 24 * 3600)
        cleaned_count = 0
        cleaned_size = 0
        skipped_runtime = 0

        console.print(f"[blue]🧹 清理 {retention_days} 天前的归档文件...[/blue]")

        # 清理所有归档目录
        for archive_dir_name in self.config["cleanup_rules"]["archive_directories"]:
            archive_dir = self.project_root / archive_dir_name
            if not archive_dir.exists():
                console.print(f"[dim]  跳过不存在的目录: {archive_dir_name}[/dim]")
                continue

            console.print(f"  清理目录: [cyan]{archive_dir_name}[/cyan]")

            # 清理文件
            for item in archive_dir.rglob("*"):
                if not (item.is_file() and item.stat().st_mtime < cutoff_time):
                    continue

                if self._is_protected_runtime_file(item):
                    skipped_runtime += 1
                    continue

                try:
                    file_size = item.stat().st_size
                    item.unlink()
                    cleaned_count += 1
                    cleaned_size += file_size
                    self.stats["files_deleted"] += 1
                    self.stats["total_size"] += file_size
                except Exception as e:
                    console.print(f"[red]    ❌ 删除文件失败 {item.name}: {e}[/red]")

            # 清理空目录
            for item in sorted(archive_dir.rglob("*"), reverse=True):
                if item.is_dir() and not any(item.iterdir()):
                    try:
                        item.rmdir()
                        self.stats["directories_deleted"] += 1
                    except Exception:
                        pass  # 忽略无法删除的目录

        if skipped_runtime > 0:
            console.print(
                f"[dim]    ⏭️ 跳过 {skipped_runtime} 个运行时保护文件 (*.pid)[/dim]"
            )

        result = {
            "cleaned_count": cleaned_count,
            "cleaned_size": cleaned_size,
            "retention_days": retention_days,
        }

        if cleaned_count > 0:
            console.print(
                f"[green]✅ 清理了 {cleaned_count} 个过期文件，释放 {cleaned_size:,} bytes[/green]"
            )
        else:
            console.print("[dim]✓ 没有过期文件需要清理[/dim]")

        return result

    def run_full_cleanup(self) -> dict[str, Any]:
        """执行完整清理流程

        Returns:
            完整清理结果统计
        """
        console.print("[bold blue]🚀 开始完整清理流程...[/bold blue]")
        start_time = time.time()

        results = {}

        # 1. 归档根目录零散文件
        console.print("[cyan]1️⃣ 归档根目录零散文件...[/cyan]")
        results["root_files"] = self.archive_root_files()

        # 2. 清理htmlcov目录
        console.print("[cyan]2️⃣ 清理htmlcov目录...[/cyan]")
        results["htmlcov"] = self.cleanup_htmlcov()

        # 3. 归档reports目录
        console.print("[cyan]3️⃣ 归档reports目录...[/cyan]")
        results["reports"] = self.archive_reports_directory()

        # 4. 清理过期归档文件
        console.print("[cyan]4️⃣ 清理过期归档文件...[/cyan]")
        results["old_archives"] = self.cleanup_old_archives()

        total_time = time.time() - start_time

        # 显示清理摘要
        console.print("\n[bold green]📊 清理摘要[/bold green]")
        console.print(f"⏱️ 总耗时: {total_time:.1f}秒")
        console.print(f"📁 归档文件: {self.stats['files_archived']} 个")
        console.print(f"📂 归档目录: {self.stats['directories_archived']} 个")
        console.print(f"🗑️ 删除文件: {self.stats['files_deleted']} 个")
        console.print(f"🗂️ 删除目录: {self.stats['directories_deleted']} 个")
        console.print(f"💾 处理总量: {self.stats['total_size']:,} bytes")

        results["summary"] = {
            "total_time": total_time,
            "stats": self.stats.copy(),
        }

        console.print("[green]🎉 完整清理流程完成！[/green]")
        return results


def main() -> None:
    """主函数"""
    parser = argparse.ArgumentParser(description="简化清理工具 - 专注核心清理功能")
    parser.add_argument("--config", type=Path, help="指定配置文件路径")
    parser.add_argument(
        "--dry-run", "-n", action="store_true", help="预览模式，不实际执行"
    )

    subparsers = parser.add_subparsers(dest="command", help="可用命令")

    # 单独命令
    subparsers.add_parser("root-files", help="归档根目录零散文件")
    subparsers.add_parser("htmlcov", help="清理htmlcov目录")
    subparsers.add_parser("reports", help="归档reports目录")
    subparsers.add_parser("old-archives", help="清理过期归档文件")
    subparsers.add_parser("full", help="执行完整清理流程")

    args = parser.parse_args()

    try:
        # 创建清理工具实例
        cleaner = SimpleCleaner(config_path=args.config)

        # 预览模式
        if args.dry_run:
            console.print("[yellow]⚠️ 预览模式 - 不会实际执行清理操作[/yellow]")
            # TODO: 实现预览模式逻辑
            return

        # 执行相应操作
        if args.command == "root-files" or args.command is None:
            cleaner.archive_root_files()
        elif args.command == "htmlcov":
            cleaner.cleanup_htmlcov()
        elif args.command == "reports":
            cleaner.archive_reports_directory()
        elif args.command == "old-archives":
            cleaner.cleanup_old_archives()
        elif args.command == "full":
            cleaner.run_full_cleanup()
        else:
            parser.print_help()

    except KeyboardInterrupt:
        console.print("\n[yellow]⏹️ 清理操作被用户中断[/yellow]")
    except Exception as e:
        console.print(f"\n[red]❌ 清理操作失败: {e}[/red]")
        sys.exit(1)


if __name__ == "__main__":
    main()
