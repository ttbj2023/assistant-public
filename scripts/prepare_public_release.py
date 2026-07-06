#!/usr/bin/env python3
"""公开仓 baseline 导出脚本.

从当前私有仓的 HEAD 快照, 导出一份干净的、可公开的代码副本到目标目录.
首次 baseline 与 sync workflow 共用本脚本的核心导出逻辑.

规则:
  - 基于 git ls-files (自动遵循 .gitignore, 排除 data/.venv 等未跟踪内容)
  - 黑名单: AGENTS.md / CLAUDE.md / .claude/ / .opencode/ (私人配置)
  - 脱敏: README / docs/README.md 去私人引用 (CLAUDE.md/AGENTS.md) / 精简 .gitignore
  - 新增: LICENSE (MIT)

用法:
  python scripts/prepare_public_release.py                          # 首次 baseline (含 git init)
  python scripts/prepare_public_release.py --target PATH --no-git-init  # workflow 复用
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

# === 排除规则 ===

EXCLUDE_PATHS: frozenset[str] = frozenset({
    "AGENTS.md",
    "CLAUDE.md",
})

EXCLUDE_PREFIXES: tuple[str, ...] = (
    ".claude/",
    ".opencode/",
    ".github/",
)

# .gitignore 中明确私人的行 (整行匹配, 含注释则保留以维持可读性)
GITIGNORE_PRIVATE_LINES: frozenset[str] = frozenset({
    ".start_dev.sh",
    ".claude/settings.local.json",
    ".claude/scheduled_tasks.lock",
    ".claude/worktrees/",
    "HANDOFF.md",
})

# === 导出产物 (目标目录中新建/覆盖的文件) ===

LICENSE_CONTENT = """MIT License

Copyright (c) 2026 ttbj2023

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""

def should_exclude(rel_path: str) -> bool:
    """判断相对路径是否应排除."""
    if rel_path in EXCLUDE_PATHS:
        return True
    return rel_path.startswith(EXCLUDE_PREFIXES)


def get_tracked_files(source: Path) -> list[str]:
    """获取 git 跟踪文件列表 (相对路径)."""
    result = subprocess.run(
        ["git", "ls-files"],
        cwd=source,
        capture_output=True,
        text=True,
        check=True,
    )
    return [line for line in result.stdout.splitlines() if line]


def copy_tracked_file(source: Path, target: Path, rel_path: str) -> None:
    """拷贝单个跟踪文件, 保留权限, 创建父目录."""
    src_file = source / rel_path
    dst_file = target / rel_path
    dst_file.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src_file, dst_file)


def write_text(target: Path, rel_path: str, content: str) -> None:
    """写入文本文件."""
    dst_file = target / rel_path
    dst_file.parent.mkdir(parents=True, exist_ok=True)
    dst_file.write_text(content, encoding="utf-8")


def sanitize_gitignore(source: Path) -> str:
    """从源 .gitignore 生成精简版 (去掉私人段)."""
    original = (source / ".gitignore").read_text(encoding="utf-8")
    sanitized_lines: list[str] = []
    for line in original.splitlines():
        normalized = line.strip().lstrip("/")
        if normalized in GITIGNORE_PRIVATE_LINES:
            continue
        sanitized_lines.append(line)
    return "\n".join(sanitized_lines) + "\n"


def _strip_private_md_refs(content: str) -> str:
    """去除含 CLAUDE.md/AGENTS.md 的行及其紧跟的空行 (脱敏)."""
    new_lines: list[str] = []
    skip_next_blank = False
    for line in content.splitlines():
        if "CLAUDE.md" in line or "AGENTS.md" in line:
            skip_next_blank = True
            continue
        if skip_next_blank and line.strip() == "":
            skip_next_blank = False
            continue
        skip_next_blank = False
        new_lines.append(line)
    return "\n".join(new_lines) + "\n"


def fix_readme(source: Path) -> str | None:
    """导出源仓 README.md 并脱敏 (去除私人 md 引用)."""
    readme = source / "README.md"
    if not readme.exists():
        return None
    return _strip_private_md_refs(readme.read_text(encoding="utf-8"))


def fix_docs_readme(source: Path) -> str | None:
    """改造 docs/README.md: 去掉 CLAUDE.md/AGENTS.md 引用.

    若文件不存在返回 None.
    """
    docs_readme = source / "docs" / "README.md"
    if not docs_readme.exists():
        return None
    return _strip_private_md_refs(docs_readme.read_text(encoding="utf-8"))


def export_snapshot(source: Path, target: Path) -> list[str]:
    """导出快照到目标目录, 返回已拷贝的相对路径列表."""
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)

    tracked = get_tracked_files(source)
    copied: list[str] = []
    for rel_path in tracked:
        if should_exclude(rel_path):
            continue
        copy_tracked_file(source, target, rel_path)
        copied.append(rel_path)

    # README 脱敏: 源仓 README 已随 git ls-files 拷贝, 覆盖为去除私人引用的版本
    fixed_readme = fix_readme(source)
    if fixed_readme is not None:
        write_text(target, "README.md", fixed_readme)

    # 新增 LICENSE
    write_text(target, "LICENSE", LICENSE_CONTENT)

    # 精简 .gitignore
    write_text(target, ".gitignore", sanitize_gitignore(source))

    # 改造 docs/README.md
    fixed_docs = fix_docs_readme(source)
    if fixed_docs is not None:
        write_text(target, "docs/README.md", fixed_docs)

    return copied


def git_init_commit(target: Path, message: str) -> None:
    """在目标目录 init git 并创建单 commit."""
    subprocess.run(["git", "init", "-q"], cwd=target, check=True)
    subprocess.run(
        ["git", "config", "user.name", "ttbj2023"], cwd=target, check=True
    )
    subprocess.run(
        ["git", "config", "user.email", "ttbj2023@users.noreply.github.com"],
        cwd=target,
        check=True,
    )
    subprocess.run(
        ["git", "checkout", "-q", "-b", "main"], cwd=target, check=True
    )
    subprocess.run(["git", "add", "-A"], cwd=target, check=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", message], cwd=target, check=True
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="导出公开仓 baseline 快照"
    )
    parser.add_argument(
        "--source",
        type=Path,
        default=Path.cwd(),
        help="源仓库根目录 (默认: 当前目录)",
    )
    parser.add_argument(
        "--target",
        type=Path,
        default=Path("/tmp/opencode/assistant-public-release"),
        help="目标目录 (默认: /tmp/opencode/assistant-public-release)",
    )
    parser.add_argument(
        "--no-git-init",
        action="store_true",
        help="只导出文件, 不执行 git init/commit (workflow 复用)",
    )
    parser.add_argument(
        "--commit-message",
        default="Initial public release v1.9.0",
        help="baseline commit message",
    )
    args = parser.parse_args()

    source: Path = args.source.resolve()
    target: Path = args.target.resolve()

    if not (source / ".git").is_dir():
        print(f"错误: {source} 不是 git 仓库", file=sys.stderr)
        return 1

    print(f"源仓库: {source}")
    print(f"目标目录: {target}")

    copied = export_snapshot(source, target)
    print(f"已导出 {len(copied)} 个文件")

    excluded_sample = [
        p for p in get_tracked_files(source) if should_exclude(p)
    ]
    if excluded_sample:
        print(f"已排除 {len(excluded_sample)} 个私人文件: {excluded_sample}")

    if not args.no_git_init:
        git_init_commit(target, args.commit_message)
        print(f"已完成 git init + commit: {args.commit_message}")

    print("\n导出完成. 产物结构:")
    subprocess.run(
        ["find", str(target), "-maxdepth", "1"], check=False
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
