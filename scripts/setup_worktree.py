#!/usr/bin/env python3
"""worktree 环境初始化工具.

为当前 git worktree 软链主仓库的 gitignored 必需配置文件, 解决 worktree 缺
config.yaml / .env / static_users.yaml 导致主流程跑不了、集成测试全 401 的问题.

设计原则:
- 幂等: 可重复执行, 已存在且正确的软链 skip, 断链自动重建
- 非破坏: 目标已是普通文件/目录(非软链)时跳过, 不覆盖用户手写
- 相对软链: 用 os.path.relpath 生成相对路径, 整树搬家不断
- 主仓库安全: 在主仓库运行时友好退出, 不报错

用法:
    cd /path/to/worktree
    python scripts/setup_worktree.py

白名单维护: 修改下方 WORKTREE_LINKS 列表即可新增/删除软链项.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from rich.console import Console

console = Console()

# worktree 必需的 gitignored 配置文件白名单.
# 这些文件在主仓库各环境独立维护(.gitignore 忽略), worktree 不会自动继承.
# config.yaml 已纳入版本控制, worktree 自动继承, 无需软链.
# 新增项直接在此列表添加; .vscode/ 等个人 IDE 配置不纳入, 留给开发者自行处理.
WORKTREE_LINKS: list[str] = [
    ".env",
    "src/auth/static_users.yaml",
]


def is_worktree(project_root: Path) -> bool:
    """检测指定目录是否为 git worktree (而非主仓库).

    worktree 的 .git 是普通文件, 内容以 "gitdir:" 开头指向 worktree gitdir;
    主仓库的 .git 是目录.

    Args:
        project_root: 待检测目录

    Returns:
        True 表示是 worktree; False 表示是主仓库或非 git 仓库

    """
    git_path = project_root / ".git"
    if not git_path.is_file():
        return False
    try:
        content = git_path.read_text(encoding="utf-8").strip()
    except OSError:
        return False
    return content.startswith("gitdir:")


def resolve_main_repo_root(project_root: Path) -> Path | None:
    """通过 git 命令定位主仓库 root.

    用 `git rev-parse --git-common-dir` 拿到共享的 .git 路径(主仓库的 .git),
    取其父目录即得主仓库 root.

    Args:
        project_root: 当前 worktree 路径

    Returns:
        主仓库 root Path; 失败返回 None

    """
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--git-common-dir"],
            cwd=project_root,
            capture_output=True,
            text=True,
            check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None

    common_dir = Path(result.stdout.strip())
    if not common_dir.is_absolute():
        common_dir = (project_root / common_dir).resolve()

    # common_dir 形如 /xxx/.git, 父目录即主仓库 root
    if common_dir.name != ".git":
        return None
    return common_dir.parent


def _classify_target(dst: Path, expected_src: Path) -> str:
    """分类 worktree 目标路径的当前状态.

    Returns:
        "missing": 不存在(含断链)
        "valid_link": 已是软链且指向 expected_src
        "wrong_link": 是软链但指向别处
        "regular": 是普通文件/目录(非软链)

    """
    if not dst.is_symlink():
        if dst.exists():
            return "regular"
        return "missing"

    # 是软链; 用 lstat 不解析, 拿到链接自身的目标字符串
    link_target_str = os.readlink(dst)
    link_target_path = (dst.parent / link_target_str).resolve()

    if link_target_path == expected_src.resolve():
        return "valid_link"
    return "wrong_link"


def sync_link(rel: str, main_root: Path, worktree_root: Path) -> str:
    """为单个白名单项创建/修复软链.

    Args:
        rel: 相对路径(如 ".env" 或 "src/auth/static_users.yaml")
        main_root: 主仓库 root
        worktree_root: 当前 worktree root

    Returns:
        操作结果描述: "created" / "valid_link" / "rebuilt" / "skipped_regular"
        / "skipped_no_source"

    """
    src = main_root / rel
    dst = worktree_root / rel

    if not src.exists():
        console.print(
            f"[yellow]⚠️  主仓库缺 {rel}, 跳过[/yellow]",
        )
        return "skipped_no_source"

    state = _classify_target(dst, src)

    if state == "valid_link":
        console.print(f"[dim]✓ 已存在 {rel}[/dim]")
        return "valid_link"

    if state == "regular":
        console.print(
            f"[yellow]⏭  已存在(非软链) {rel}, 跳过避免覆盖[/yellow]",
        )
        return "skipped_regular"

    if state == "wrong_link":
        console.print(
            f"[yellow]🔧 {rel} 软链指向错误, 重建[/yellow]",
        )
        dst.unlink()

    # state in ("missing", "wrong_link") → (re)create
    dst.parent.mkdir(parents=True, exist_ok=True)
    rel_src = os.path.relpath(src, dst.parent)
    dst.symlink_to(rel_src)
    console.print(f"[green]✅ {rel} → {rel_src}[/green]")
    return "created" if state == "missing" else "rebuilt"


def main() -> int:
    """脚本入口.

    Returns:
        0 表示成功(含主仓库无操作场景); 1 表示致命错误

    """
    worktree_root = Path.cwd()

    if not is_worktree(worktree_root):
        console.print(
            "[yellow]ℹ️  当前目录不是 git worktree "
            "(主仓库的 .git 是目录, 无需同步配置).[/yellow]",
        )
        console.print(
            "[dim]请在 worktree 目录下运行本脚本: "
            "cd /path/to/worktree && python scripts/setup_worktree.py[/dim]",
        )
        return 0

    main_root = resolve_main_repo_root(worktree_root)
    if main_root is None or not main_root.exists():
        console.print("[red]❌ 无法定位主仓库 root, 检查 git 是否可用[/red]")
        return 1

    console.print(
        f"[blue]🚀 初始化 worktree 配置软链[/blue]\n"
        f"  worktree: [cyan]{worktree_root}[/cyan]\n"
        f"  主仓库:   [cyan]{main_root}[/cyan]\n",
    )

    results: dict[str, int] = {}
    for rel in WORKTREE_LINKS:
        outcome = sync_link(rel, main_root, worktree_root)
        results[outcome] = results.get(outcome, 0) + 1

    # 汇总
    console.print("\n[bold]📊 汇总[/bold]")
    if results.get("created"):
        console.print(f"  [green]新建: {results['created']}[/green]")
    if results.get("rebuilt"):
        console.print(f"  [yellow]重建: {results['rebuilt']}[/yellow]")
    if results.get("valid_link"):
        console.print(f"  [dim]已存在: {results['valid_link']}[/dim]")
    if results.get("skipped_regular"):
        console.print(
            f"  [yellow]跳过(非软链): {results['skipped_regular']}[/yellow]",
        )
    if results.get("skipped_no_source"):
        console.print(
            f"  [yellow]跳过(主仓库缺源): {results['skipped_no_source']}[/yellow]",
        )

    if results.get("created") or results.get("rebuilt"):
        console.print(
            "\n[bold green]✅ 配置软链就绪[/bold green]\n"
            "[dim]下一步: python scripts/run_test_suite.py --quick[/dim]",
        )
    else:
        console.print("\n[bold]✅ 无需变更[/bold]")

    return 0


if __name__ == "__main__":
    sys.exit(main())
