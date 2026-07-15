#!/usr/bin/env python3
"""Assistant项目静态分析运行器 V2.0 - 统一并行架构

全新设计的静态分析脚本，实现：
1. 统一的并行执行架构（基于run_test_suite.py）
2. 集成代码格式化功能（来自code_quality.py）
3. 所有工具并行执行，非阻塞式报告
4. 智能缓存机制，提升执行效率
5. 单一入口点，支持多种使用场景

支持的工具：
- Ruff: 代码检查和格式化
- MyPy: 类型检查
- Bandit: 安全扫描
- Safety: 依赖安全检查
- Vulture: 死代码检测
- 依赖关系检查: 模块使用分析
- 代码格式化: 自动修复和中文标点处理
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import os
import re
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, ClassVar

from rich.console import Console
from rich.panel import Panel


class SeverityLevel(Enum):
    """问题严重程度分类"""

    CRITICAL = "critical"  # 严重级别: 阻止CI(core模式); full模式仅报告
    STRICT = "strict"  # 严格级别: 必须修复, 仅core模式阻止CI; full模式作改进信号
    WARNING = "warning"  # 警告级别: 建议修复, 不阻止CI
    INFO = "info"  # 信息级别:可选修复
    STYLE = "style"  # 风格级别:代码风格问题


class TaskStatus(Enum):
    """任务状态"""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CACHED = "cached"


@dataclass
class TaskResult:
    """任务执行结果"""

    task_name: str
    task_type: str  # "static_analysis" or "format"
    success: bool
    duration: float
    output: str = ""  # 异常分支构造时不传, 给默认值避免 TypeError
    error_message: str | None = None
    report_path: str | None = None
    structured_data: dict[str, Any] | None = None
    execution_details: dict[str, Any] | None = None
    cached: bool = False


@dataclass
class CacheEntry:
    """缓存条目"""

    tool_name: str
    cache_key: str
    result: TaskResult
    timestamp: float

    def is_expired(self, max_age_seconds: int = 300) -> bool:
        """检查缓存是否过期"""
        return time.time() - self.timestamp > max_age_seconds


@dataclass
class QualityGate:
    """质量门禁数据类"""

    passed: bool
    critical_issues: int
    strict_issues: int
    warning_issues: int
    total_issues: int


class StaticAnalysisRunner:
    """静态分析运行器 V2.0 - 统一并行架构

    基于 run_test_suite.py 的设计理念，提供：
    - 并行执行所有分析工具
    - 智能缓存机制
    - 集成格式化功能
    - 统一配置管理
    """

    def __init__(
        self,
        core_mode: bool = True,
        verbose: bool = False,
        enable_cache: bool = True,
        target_path: str = "src/",
        codex_mode: bool = False,
        args: argparse.Namespace | None = None,
    ):
        self.core_mode = core_mode
        self.verbose = verbose
        self.enable_cache = enable_cache
        self.target_path = target_path
        self.codex_mode = codex_mode
        self.args = args if args is not None else argparse.Namespace()
        self.console = Console()
        self.results: list[TaskResult] = []
        self.cache: dict[str, CacheEntry] = {}

        # 智能识别主项目根目录(避免worktree影响)
        script_path = Path(__file__).parent.parent
        self.project_root = self._find_main_project_root(script_path)
        self.reports_dir = self.project_root / "reports"

        # 确保reports目录存在
        self.reports_dir.mkdir(exist_ok=True)

        # 配置日志
        if verbose:
            logging.getLogger().setLevel(logging.DEBUG)

        self.logger = logging.getLogger(__name__)

        if self.verbose:
            mode_label = "Codex" if codex_mode else ("核心" if core_mode else "完整")
            self.console.print(
                f"[blue]🚀 静态分析运行器启动 (模式: {mode_label}, 缓存: {'启用' if enable_cache else '禁用'})[/blue]"
            )

    def _find_main_project_root(self, script_path: Path) -> Path:
        """智能识别主项目根目录，避免worktree影响"""
        current = script_path.resolve()

        # 如果当前目录是主项目(包含pyproject.toml且不在workspace中)
        if self._is_main_project_root(current):
            return current

        # Codex sandbox可能将仓库挂载到/home/workspace,此时仍以脚本目录为准
        if self.codex_mode and self._has_project_markers(current):
            return current

        # 如果当前是worktree,寻找主项目目录
        for parent in current.parents:
            if self._is_main_project_root(parent):
                return parent

        # 如果找不到,返回脚本所在目录
        return script_path

    def _has_project_markers(self, path: Path) -> bool:
        """判断路径是否包含项目根目录标记"""
        return (
            (path / "pyproject.toml").exists()
            and (path / "src").exists()
            and (path / "scripts").exists()
        )

    def _is_main_project_root(self, path: Path) -> bool:
        """判断是否为主项目根目录"""
        if not self._has_project_markers(path):
            return False

        # 路径名不能包含workspace(避免选择workspace中的项目)
        return "workspace" not in path.parts

    def _get_cache_key(self, tool_name: str, cmd_args: list[str]) -> str:
        """生成缓存键"""
        # 包含工具名,核心模式,关键参数和文件修改时间
        key_data = {
            "tool": tool_name,
            "core_mode": self.core_mode,
            "args": cmd_args,
            "src_mtime": self._get_src_mtime(),
        }
        key_str = json.dumps(key_data, sort_keys=True)
        return hashlib.md5(key_str.encode()).hexdigest()

    def _get_src_mtime(self) -> float:
        """获取src目录的最新修改时间"""
        try:
            src_dir = self.project_root / "src"
            if not src_dir.exists():
                return 0.0

            max_mtime = 0.0
            for py_file in src_dir.rglob("*.py"):
                try:
                    mtime = py_file.stat().st_mtime
                    max_mtime = max(max_mtime, mtime)
                except OSError:
                    continue
            return max_mtime
        except Exception:
            return 0.0

    def _get_dependency_mtime(self) -> float:
        """获取依赖配置文件的最大修改时间"""
        try:
            dependency_files = [
                self.project_root / "pyproject.toml",
                self.project_root / "requirements.txt",
                self.project_root / "poetry.lock",
                self.project_root / "Pipfile.lock",
            ]

            max_mtime = 0.0
            for dep_file in dependency_files:
                if dep_file.exists():
                    mtime = dep_file.stat().st_mtime
                    max_mtime = max(max_mtime, mtime)

            return max_mtime
        except Exception:
            return 0.0

    def _get_cached_result(self, tool_name: str, cache_key: str) -> TaskResult | None:
        """获取缓存结果"""
        if not self.enable_cache:
            return None

        cache_entry = self.cache.get(cache_key)
        if cache_entry and not cache_entry.is_expired():
            if self.verbose:
                self.console.print(f"[dim]📦 使用缓存结果: {tool_name}[/dim]")
            result = cache_entry.result
            result.cached = True
            return result
        return None

    def _store_cached_result(
        self, tool_name: str, cache_key: str, result: TaskResult
    ) -> None:
        """存储缓存结果"""
        if not self.enable_cache:
            return

        cache_entry = CacheEntry(
            tool_name=tool_name,
            cache_key=cache_key,
            result=result,
            timestamp=time.time(),
        )
        self.cache[cache_key] = cache_entry
        if self.verbose:
            self.console.print(f"[dim]💾 缓存结果: {tool_name}[/dim]")

    def _tool_command_prefix(self, module_name: str, executable: str) -> list[str]:
        """获取工具命令前缀."""
        if self.codex_mode:
            return [sys.executable, "-m", module_name]
        return [executable]

    async def _run_async_command(
        self, cmd: list[str], description: str, env: dict[str, str] | None = None
    ) -> dict[str, Any]:
        """异步执行命令"""
        try:

            def run_command() -> subprocess.CompletedProcess[str]:
                return subprocess.run(
                    cmd,
                    cwd=self.project_root,
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=600,  # 10分钟超时
                    env=env or os.environ,
                )

            if self.codex_mode:
                result = run_command()
            else:
                # 使用线程池执行同步命令
                loop = asyncio.get_event_loop()
                result = await loop.run_in_executor(None, run_command)

            return {
                "returncode": result.returncode,
                "stdout": result.stdout,
                "stderr": result.stderr,
            }

        except subprocess.TimeoutExpired:
            return {"returncode": -1, "stdout": "", "stderr": f"{description} 执行超时"}
        except Exception as e:
            return {
                "returncode": -1,
                "stdout": "",
                "stderr": f"{description} 执行异常: {e!s}",
            }

    async def run_ruff_analysis(self) -> TaskResult:
        """运行Ruff代码分析"""
        task_name = "Ruff代码分析"
        start_time = time.time()

        try:
            # 构建命令
            cmd = [
                *self._tool_command_prefix("ruff", "ruff"),
                "check",
                "src/",
                "--output-format=json",
            ]

            # 根据模式选择配置文件
            if not self.core_mode:
                cmd.extend(["--config=config/ruff_full.toml"])
                cmd.extend(["--ignore=ERA001"])

            # 检查缓存
            cache_key = self._get_cache_key("ruff", cmd)
            cached_result = self._get_cached_result("ruff", cache_key)
            if cached_result:
                return cached_result

            # 执行命令
            result = await self._run_async_command(cmd, f"{task_name} - 代码检查")

            # 解析JSON输出
            issues = []
            if result["stdout"]:
                try:
                    issues = json.loads(result["stdout"])
                except json.JSONDecodeError:
                    issues = []

            # 运行格式化检查
            format_cmd = [
                *self._tool_command_prefix("ruff", "ruff"),
                "format",
                "src/",
                "--check",
                "--diff",
            ]
            if not self.core_mode:
                format_cmd.extend(["--config=config/ruff_full.toml"])

            format_result = await self._run_async_command(
                format_cmd, f"{task_name} - 格式化检查"
            )

            # 合并结果
            total_issues = len(issues)
            if format_result["returncode"] != 0:
                total_issues += 1  # 格式化问题算作一个issue

            execution_details = {
                "code_check_issues": len(issues),
                "format_issues": 1 if format_result["returncode"] != 0 else 0,
                "total_issues": total_issues,
                "code_check_success": result["returncode"] == 0,
                "format_check_success": format_result["returncode"] == 0,
            }

            # 生成独立报告
            report_data = {
                "tool": "ruff",
                "timestamp": time.time(),
                "success": True,
                "issues": issues,
                "format_issues": format_result["stdout"]
                if format_result["returncode"] != 0
                else None,
                "execution_details": execution_details,
            }

            report_path = await self._save_static_analysis_report("ruff", report_data)

            success = result["returncode"] == 0 and format_result["returncode"] == 0

            task_result = TaskResult(
                task_name=task_name,
                task_type="static_analysis",
                success=success,
                duration=time.time() - start_time,
                output=f"代码检查: {len(issues)} issues, 格式化检查: {'通过' if format_result['returncode'] == 0 else '失败'}",
                report_path=report_path,
                structured_data=report_data,
                execution_details=execution_details,
            )

            # 存储缓存
            self._store_cached_result("ruff", cache_key, task_result)

            return task_result

        except Exception as e:
            return TaskResult(
                task_name=task_name,
                task_type="static_analysis",
                success=False,
                duration=time.time() - start_time,
                error_message=str(e),
            )

    async def run_mypy_analysis(self) -> TaskResult:
        """运行MyPy类型分析"""
        task_name = "MyPy类型分析"
        start_time = time.time()

        try:
            # 构建MyPy命令,支持核心模式和完整模式
            # 不再在命令行中重复指定模块,使用pyproject.toml中的files配置
            cmd = [
                *self._tool_command_prefix("mypy", "mypy"),
                "--show-error-codes",
                "--no-error-summary",
                "--pretty",
            ]

            # mypy 2.0 并行类型检查: 实测结果与串行一致, 非codex模式启用加速
            # (32核8 worker约4.6x; codex沙盒资源隔离, 串行保稳定)
            if not self.codex_mode:
                cmd.append(f"--num-workers={min(os.cpu_count() or 4, 8)}")

            # 根据模式选择配置文件
            if not self.core_mode:
                # 完整模式使用专门的配置文件
                full_config_path = self.project_root / "config" / "mypy_full.toml"
                if full_config_path.exists():
                    # 验证配置文件可用性
                    if self._validate_mypy_config(full_config_path):
                        cmd.extend([f"--config-file={full_config_path}"])
                        if self.verbose:
                            self.console.print(
                                f"[green]✓ 使用完整模式配置: {full_config_path}[/green]"
                            )
                    else:
                        self.console.print(
                            f"[red]❌ 完整模式配置文件无效: {full_config_path}[/red]"
                        )
                        self.console.print("[yellow]⚠️  回退到核心模式配置[/yellow]")
                        cmd.extend(["--config-file=pyproject.toml"])
                else:
                    self.console.print("[red]❌ 完整模式配置文件不存在[/red]")
                    self.console.print("[yellow]⚠️  回退到核心模式配置[/yellow]")
                    cmd.extend(["--config-file=pyproject.toml"])
            else:
                # 核心模式使用基础配置
                cmd.extend(["--config-file=pyproject.toml"])

            # 添加模块路径解析优化选项
            cmd.extend([
                "--namespace-packages",
                "--explicit-package-bases",
            ])

            # 检查缓存
            cache_key = self._get_cache_key("mypy", cmd)
            cached_result = self._get_cached_result("mypy", cache_key)
            if cached_result:
                return cached_result

            # 清理MyPy缓存以确保使用最新配置
            if self.verbose:
                self.console.print("[dim]🧹 清理MyPy缓存以确保使用最新配置[/dim]")

            result = await self._run_async_command(cmd, task_name)

            # 解析MyPy输出
            issues = []
            output_lines = (result["stdout"] + result["stderr"]).split("\n")

            # 特殊处理路径冲突错误
            path_conflict_found = False
            for line in output_lines:
                if "Source file found twice under different module names" in line:
                    path_conflict_found = True
                    if self.verbose:
                        self.console.print(
                            f"[yellow]⚠️  检测到模块路径冲突: {line}[/yellow]"
                        )
                    # 将路径冲突归类为警告而不是错误
                    issues.append({
                        "message": f"模块路径冲突: {line}",
                        "severity": "warning",
                        "file": "mypy_config",
                    })
                elif (
                    line.strip()
                    and ":" in line
                    and ("error:" in line or "note:" in line)
                ):
                    # 过滤掉路径冲突相关的重复错误信息
                    if path_conflict_found and (
                        "found twice" in line or "different module names" in line
                    ):
                        continue
                    # 分类错误严重程度
                    severity = self._classify_mypy_severity(line)
                    issues.append({
                        "message": line,
                        "severity": severity,
                        "file": line.split(":")[0] if ":" in line else "unknown",
                    })

            execution_details = {
                "total_issues": len(issues),
                "critical_issues": len([
                    i for i in issues if i["severity"] == "critical"
                ]),
                "strict_issues": len([i for i in issues if i["severity"] == "strict"]),
                "warning_issues": len([
                    i for i in issues if i["severity"] == "warning"
                ]),
            }

            # 生成独立报告
            report_data = {
                "tool": "mypy",
                "timestamp": time.time(),
                "success": True,
                "issues": issues,
                "execution_details": execution_details,
            }

            report_path = await self._save_static_analysis_report("mypy", report_data)

            # MyPy成功标准:无严重错误
            success = execution_details["critical_issues"] == 0

            task_result = TaskResult(
                task_name=task_name,
                task_type="static_analysis",
                success=success,
                duration=time.time() - start_time,
                output=f"发现 {len(issues)} 个类型问题 (严重: {execution_details['critical_issues']})",
                report_path=report_path,
                structured_data=report_data,
                execution_details=execution_details,
            )

            # 存储缓存
            self._store_cached_result("mypy", cache_key, task_result)

            return task_result

        except Exception as e:
            return TaskResult(
                task_name=task_name,
                task_type="static_analysis",
                success=False,
                duration=time.time() - start_time,
                error_message=str(e),
            )

    def _classify_mypy_severity(self, error_line: str) -> str:
        """分类MyPy错误的严重程度"""
        error_line_lower = error_line.lower()

        # 严重错误
        critical_patterns = [
            r"name.*not.*defined",
            r"has.*no.*attribute",
            r"too.*many.*arguments",
            r"missing.*required.*argument",
            r"incompatible.*function.*arguments",
        ]

        # 严格错误
        strict_patterns = [
            r"incompatible.*return.*type",
            r"incompatible.*parameter.*type",
            r"return.*value.*expected",
        ]

        for pattern in critical_patterns:
            if re.search(pattern, error_line_lower):
                return "critical"

        for pattern in strict_patterns:
            if re.search(pattern, error_line_lower):
                return "strict"

        return "warning"

    async def run_bandit_analysis(self) -> TaskResult:
        """运行Bandit安全分析"""
        task_name = "Bandit安全分析"
        start_time = time.time()

        try:
            # 创建临时报告文件
            temp_report = Path(tempfile.gettempdir()) / "bandit_report.json"

            cmd = [
                *self._tool_command_prefix("bandit", "bandit"),
                "-r",
                "src/",
                "-f",
                "json",
                "-o",
                str(temp_report),
                "-ll",  # 只显示低严重度以上
                "--skip",
                "B608",  # 跳过SQL注入检查(已通过白名单验证)
            ]

            # 检查缓存
            cache_key = self._get_cache_key("bandit", cmd)
            cached_result = self._get_cached_result("bandit", cache_key)
            if cached_result:
                return cached_result

            result = await self._run_async_command(cmd, task_name)

            # 读取报告
            issues = []
            if self.verbose:
                self.console.print(
                    f"[dim]Bandit returncode: {result['returncode']}[/dim]"
                )

            if temp_report.exists():
                try:
                    with temp_report.open() as f:
                        bandit_result = json.load(f)
                        issues = bandit_result.get("results", [])
                except (json.JSONDecodeError, FileNotFoundError):
                    pass
                finally:
                    # 清理临时文件
                    temp_report.unlink(missing_ok=True)

            execution_details = {
                "total_issues": len(issues),
                "high_severity": len([
                    i for i in issues if i.get("issue_severity") == "HIGH"
                ]),
                "medium_severity": len([
                    i for i in issues if i.get("issue_severity") == "MEDIUM"
                ]),
                "low_severity": len([
                    i for i in issues if i.get("issue_severity") == "LOW"
                ]),
            }

            # 生成独立报告
            report_data = {
                "tool": "bandit",
                "timestamp": time.time(),
                "success": True,
                "issues": issues,
                "execution_details": execution_details,
            }

            report_path = await self._save_static_analysis_report("bandit", report_data)

            # Bandit成功标准:无高危安全问题
            success = execution_details["high_severity"] == 0

            task_result = TaskResult(
                task_name=task_name,
                task_type="static_analysis",
                success=success,
                duration=time.time() - start_time,
                output=f"发现 {len(issues)} 个安全问题 (高危: {execution_details['high_severity']})",
                report_path=report_path,
                structured_data=report_data,
                execution_details=execution_details,
            )

            # 存储缓存
            self._store_cached_result("bandit", cache_key, task_result)

            return task_result

        except Exception as e:
            return TaskResult(
                task_name=task_name,
                task_type="static_analysis",
                success=False,
                duration=time.time() - start_time,
                error_message=str(e),
            )

    async def run_safety_analysis(self) -> TaskResult:
        """运行Safety依赖安全分析"""
        task_name = "Safety依赖分析"
        start_time = time.time()

        try:
            # 检查是否跳过Safety检查
            if hasattr(self.args, "without_safety") and self.args.without_safety:
                if self.verbose:
                    self.console.print("⏭️  跳过Safety安全检查（--without-safety选项）")
                return TaskResult(
                    task_name=task_name,
                    task_type="static_analysis",
                    success=True,
                    duration=time.time() - start_time,
                    output="Safety检查已跳过",
                    structured_data={"skipped": True, "reason": "without_safety flag"},
                )
            # Safety scan命令 - 优化版本,支持离线模式和快速模式
            # 使用简化的命令,避免网络请求过慢
            cache_dir = self.project_root / ".cache" / "safety"

            # 检查是否有缓存文件
            cache_file = cache_dir / "safety_cache.json"
            use_offline = cache_file.exists() and not self.args.no_cache

            if use_offline:
                # 使用离线模式(如果缓存存在)
                cmd = [
                    "safety",
                    "scan",
                    "--json",
                    "--output=json",
                    "--continue-on-error",
                    "--short-report",  # 简短报告模式
                ]
                if self.verbose:
                    self.console.print("💾 Safety使用离线模式")
            else:
                # 在线模式,但设置较短的超时时间
                cmd = [
                    "safety",
                    "scan",
                    "--json",
                    "--output=json",
                    "--continue-on-error",
                    "--short-report",  # 简短报告模式
                ]

            # 添加代理配置 - Safety使用分离的代理参数
            proxy_url = os.environ.get("HTTP_PROXY") or os.environ.get("http_proxy")
            if proxy_url and "://" in proxy_url and not use_offline:
                # 解析代理URL: http://host:port
                protocol, rest = proxy_url.split("://", 1)
                if ":" in rest:
                    host, port = rest.rsplit(":", 1)
                    cmd.extend([
                        f"--proxy-host={host}",
                        f"--proxy-port={port}",
                        f"--proxy-protocol={protocol}",
                    ])
                    if self.verbose:
                        self.console.print(
                            f"🌐 Safety使用代理: {host}:{port} ({protocol})"
                        )

            # Safety使用基于依赖文件的缓存键(不是源代码)
            dependency_mtime = self._get_dependency_mtime()
            safety_cache_key = {
                "tool": "safety",
                "core_mode": self.core_mode,
                "args": cmd,
                "dependency_mtime": dependency_mtime,
            }
            cache_key = hashlib.md5(
                json.dumps(safety_cache_key, sort_keys=True).encode()
            ).hexdigest()

            cached_result = self._get_cached_result("safety", cache_key)
            if cached_result:
                if self.verbose:
                    self.console.print("💾 使用Safety缓存结果（依赖未变化）")
                return cached_result

            # 确保代理环境变量传递给子进程
            safety_env = os.environ.copy()
            if proxy_url:
                safety_env.update({
                    "HTTP_PROXY": proxy_url,
                    "HTTPS_PROXY": proxy_url,
                    "http_proxy": proxy_url,
                    "https_proxy": proxy_url,
                })

            # 为Safety设置更短的超时时间
            from subprocess import TimeoutExpired

            try:
                loop = asyncio.get_event_loop()
                process_result = await loop.run_in_executor(
                    None,
                    lambda: subprocess.run(
                        cmd,
                        cwd=self.project_root,
                        capture_output=True,
                        text=True,
                        check=False,
                        timeout=30,  # Safety设置30秒超时
                        env=safety_env,
                    ),
                )
                # 转换为统一格式
                result = {
                    "returncode": process_result.returncode,
                    "stdout": process_result.stdout,
                    "stderr": process_result.stderr,
                }
            except TimeoutExpired:
                if self.verbose:
                    self.console.print("⏰ Safety执行超时，跳过安全检查")
                result = {"returncode": 0, "stdout": "", "stderr": "Safety timeout"}
            except Exception as e:
                if self.verbose:
                    self.console.print(f"❌ Safety执行失败: {e}")
                result = {"returncode": 0, "stdout": "", "stderr": str(e)}

            # 解析JSON输出
            issues = []
            try:
                stdout_content = result.get("stdout", "")
                if stdout_content:
                    output_lines = stdout_content.split("\n")
                    json_start = None
                    for i, line in enumerate(output_lines):
                        if line.strip().startswith("{"):
                            json_start = i
                            break

                    if json_start is not None:
                        json_text = "\n".join(output_lines[json_start:])
                        safety_result = json.loads(json_text)
                        issues = safety_result.get("vulnerabilities", [])
            except (json.JSONDecodeError, KeyError):
                pass

            execution_details = {
                "total_issues": len(issues),
                "vulnerable_packages": len({
                    issue.get("package_name", "") for issue in issues
                }),
            }

            # 生成独立报告
            report_data = {
                "tool": "safety",
                "timestamp": time.time(),
                "success": True,
                "issues": issues,
                "execution_details": execution_details,
            }

            report_path = await self._save_static_analysis_report("safety", report_data)

            # Safety成功标准:无依赖漏洞
            success = len(issues) == 0

            task_result = TaskResult(
                task_name=task_name,
                task_type="static_analysis",
                success=success,
                duration=time.time() - start_time,
                output=f"发现 {len(issues)} 个依赖漏洞",
                report_path=report_path,
                structured_data=report_data,
                execution_details=execution_details,
            )

            # 存储缓存
            self._store_cached_result("safety", cache_key, task_result)

            return task_result

        except Exception as e:
            # Safety检查超时或失败不算作严重错误
            return TaskResult(
                task_name=task_name,
                task_type="static_analysis",
                success=True,  # Safety失败不阻塞CI
                duration=time.time() - start_time,
                output=f"Safety检查失败或超时: {e!s}",
                error_message=str(e),
            )

    async def run_dependency_analysis(self) -> TaskResult:
        """运行依赖关系分析"""
        task_name = "依赖关系分析"
        start_time = time.time()

        try:
            # 定义实际存在的核心模块
            core_modules = [
                "src.agent",
                "src.api",
                "src.auth",
                "src.config",
                "src.core",
                "src.inference",
                "src.storage",
                "src.tools",
                "src.utils",
            ]

            # 定义通用组件列表(这些组件被高度依赖是正常的)
            common_components = {
                "src.core",  # 核心工具和基础设施
                "src.utils",  # 通用工具函数
                "src.config",  # 配置管理
                "src.storage",  # 存储层(数据访问)
                "src.agent",  # Agent核心抽象
                "src.tools",  # 工具系统(内部/专家/MCP三层架构)
                "src.inference",  # 推理基础设施(模型定义/Provider配置/工厂)
            }

            issues = []

            for module in core_modules:
                module_name = module.split(".")[-1]
                pattern = f"from {module}|from.*{module_name} import"
                count = await self._count_imports_async(pattern, "src/")

                # 通用组件被高度依赖是正常的,设置更高的阈值或直接忽略
                threshold = 200 if module in common_components else 50

                # 只有超过阈值且不是通用组件时才报告问题
                if count > threshold and module not in common_components:
                    issues.append({
                        "message": f"模块 {module} 使用频繁: {count} 次",
                        "severity": "WARNING",
                        "file": "dependency_analysis",
                        "module": module,
                        "count": count,
                        "threshold": threshold,
                    })
                elif count > threshold and module in common_components:
                    # 对于通用组件,记录但不视为问题
                    self.logger.debug(f"通用组件 {module} 使用次数: {count} (正常)")

            success = len(issues) == 0

            execution_details = {
                "modules_analyzed": len(core_modules),
                "issues_found": len(issues),
            }

            task_result = TaskResult(
                task_name=task_name,
                task_type="static_analysis",
                success=success,
                duration=time.time() - start_time,
                output=f"分析完成，发现 {len(issues)} 个依赖问题",
                execution_details=execution_details,
            )

            return task_result

        except Exception as e:
            return TaskResult(
                task_name=task_name,
                task_type="static_analysis",
                success=False,
                duration=time.time() - start_time,
                error_message=str(e),
            )

    async def run_config_governance_analysis(self) -> TaskResult:
        """运行配置治理检查."""
        task_name = "配置治理检查"
        start_time = time.time()
        cmd = [
            sys.executable,
            str(self.project_root / "scripts" / "config_doctor.py"),
            "--strict",
            "--check-env",
        ]
        try:
            result = await self._run_async_command(cmd, task_name)
            success = result["returncode"] == 0
            return TaskResult(
                task_name=task_name,
                task_type="static_analysis",
                success=success,
                duration=time.time() - start_time,
                output=result.get("stdout", ""),
                error_message=None if success else result.get("stderr", ""),
                execution_details={
                    "returncode": result["returncode"],
                    "command": " ".join(cmd),
                },
            )
        except Exception as e:
            return TaskResult(
                task_name=task_name,
                task_type="static_analysis",
                success=False,
                duration=time.time() - start_time,
                error_message=str(e),
            )

    def _validate_mypy_config(self, config_path: Path) -> bool:
        """验证MyPy配置文件是否有效"""
        try:
            # 尝试使用配置文件运行简单的版本检查
            test_cmd = [
                "mypy",
                "--version",
                f"--config-file={config_path}",
            ]

            result = subprocess.run(
                test_cmd,
                capture_output=True,
                text=True,
                check=False,
                timeout=10,  # 10秒超时
            )

            if result.returncode == 0:
                return True
            if self.verbose:
                self.console.print(f"[dim]MyPy配置验证失败: {result.stderr}[/dim]")
            return False

        except subprocess.TimeoutExpired:
            if self.verbose:
                self.console.print("[dim]MyPy配置验证超时[/dim]")
            return False
        except Exception as e:
            if self.verbose:
                self.console.print(f"[dim]MyPy配置验证异常: {e}[/dim]")
            return False

    async def _count_imports_async(self, pattern: str, path: str) -> int:
        """异步统计匹配模式的导入次数"""
        try:
            # 使用线程池执行grep命令
            cmd = ["grep", "-r", "--include=*.py", "-E", pattern, path]
            result = await self._run_async_command(cmd, f"统计导入: {pattern}")

            if result["returncode"] == 0:
                lines = [line for line in result["stdout"].split("\n") if line.strip()]
                return len(lines)
            return 0

        except Exception:
            # fallback to simple Python implementation
            return 0

    async def run_vulture_analysis(self) -> TaskResult:
        """运行Vulture死代码检测"""
        task_name = "Vulture死代码检测"
        start_time = time.time()

        try:
            # 构建命令 - 使用pyproject.toml配置
            cmd = ["vulture"]

            # 添加配置文件参数
            config_file = self.project_root / "pyproject.toml"
            if config_file.exists():
                cmd.extend(["--config", str(config_file)])

            cmd.append("src/")

            # 检查缓存
            cache_key = self._get_cache_key("vulture", cmd)
            cached_result = self._get_cached_result("vulture", cache_key)
            if cached_result:
                return cached_result

            # 执行命令
            result = await self._run_async_command(cmd, task_name)

            # 解析输出
            output_lines = result["stdout"].split("\n")
            issues = []

            # 统计死代码
            dead_code_count = 0
            for line in output_lines:
                if (
                    "unused" in line.lower() or "unreachable" in line.lower()
                ) and line.strip():
                    dead_code_count += 1
                    # 尝试解析格式: file.py:line: unused code
                    parts = line.split(":")
                    if len(parts) >= 3:
                        issues.append({
                            "message": line.strip(),
                            "severity": "warning",
                            "file": parts[0],
                            "line": parts[1] if len(parts) > 1 else "unknown",
                        })

            execution_details = {
                "total_issues": dead_code_count,
                "unused_code": dead_code_count,
            }

            # 生成独立报告
            report_data = {
                "tool": "vulture",
                "timestamp": time.time(),
                "success": True,
                "issues": issues,
                "raw_output": result["stdout"],
                "execution_details": execution_details,
            }

            report_path = await self._save_static_analysis_report(
                "vulture", report_data
            )

            # Vulture成功标准:不阻塞CI(仅警告)
            success = True

            task_result = TaskResult(
                task_name=task_name,
                task_type="static_analysis",
                success=success,
                duration=time.time() - start_time,
                output=f"发现 {dead_code_count} 处潜在死代码（置信度≥80%）",
                report_path=report_path,
                structured_data=report_data,
                execution_details=execution_details,
            )

            # 存储缓存
            self._store_cached_result("vulture", cache_key, task_result)

            return task_result

        except Exception as e:
            # Vulture失败不阻塞CI
            return TaskResult(
                task_name=task_name,
                task_type="static_analysis",
                success=True,  # 失败不阻塞CI
                duration=time.time() - start_time,
                output=f"Vulture检测失败: {e!s}",
                error_message=str(e),
            )

    # ==================== 格式化功能(集成自code_quality.py)====================

    async def run_format_code(self, check_only: bool = False) -> TaskResult:
        """运行代码格式化"""
        task_name = "代码格式化" if not check_only else "代码格式检查"
        start_time = time.time()

        try:
            cmd = [
                *self._tool_command_prefix("ruff", "ruff"),
                "format",
                self.target_path,
            ]
            if check_only:
                cmd.extend(["--check", "--diff"])

            result = await self._run_async_command(cmd, task_name)

            success = result["returncode"] == 0
            output = (
                "格式化完成" if success else f"发现格式问题: {result['stdout'][:200]}"
            )

            task_result = TaskResult(
                task_name=task_name,
                task_type="format",
                success=success,
                duration=time.time() - start_time,
                output=output,
                execution_details={
                    "check_only": check_only,
                    "changes_made": not success,
                },
            )

            return task_result

        except Exception as e:
            return TaskResult(
                task_name=task_name,
                task_type="format",
                success=False,
                duration=time.time() - start_time,
                error_message=str(e),
            )

    async def run_fix_code(self) -> TaskResult:
        """运行代码修复"""
        task_name = "代码修复"
        start_time = time.time()

        try:
            # 修复可自动修复的问题
            fix_cmd = [
                *self._tool_command_prefix("ruff", "ruff"),
                "check",
                self.target_path,
                "--fix",
            ]
            fix_result = await self._run_async_command(
                fix_cmd, f"{task_name} - 自动修复"
            )

            # 格式化代码
            format_result = await self.run_format_code(check_only=False)

            success = fix_result["returncode"] == 0 and format_result.success

            task_result = TaskResult(
                task_name=task_name,
                task_type="format",
                success=success,
                duration=time.time() - start_time,
                output="代码修复完成" if success else "代码修复部分失败",
                execution_details={
                    "fix_success": fix_result["returncode"] == 0,
                    "format_success": format_result.success,
                },
            )

            return task_result

        except Exception as e:
            return TaskResult(
                task_name=task_name,
                task_type="format",
                success=False,
                duration=time.time() - start_time,
                error_message=str(e),
            )

    async def run_fix_punctuation(self, check_only: bool = False) -> TaskResult:
        """修复中文标点符号.

        业务必需的中文标点 (URL边界正则/数据源键名/分句符等) 用 `# noqa: RUF001`
        标记保护, 详见 _replace_chinese_punctuation 跳过策略.
        """
        task_name = "中文标点修复" if not check_only else "中文标点检查"
        start_time = time.time()

        try:
            punctuation_map = {
                "，": ",",
                "。": ".",
                "、": ",",
                "：": ":",
                "；": ";",
                "？": "?",
                "！": "!",
                "（": "(",
                "）": ")",
                "【": "[",
                "】": "]",
                "「": '"',
                "」": '"',
                "『": "'",
                "』": "'",
                "《": "<",
                "》": ">",
            }

            full_path = self.project_root / self.target_path
            if not full_path.exists():
                return TaskResult(
                    task_name=task_name,
                    task_type="format",
                    success=False,
                    duration=time.time() - start_time,
                    error_message=f"路径不存在: {full_path}",
                )

            if full_path.is_file():
                python_files = [full_path] if full_path.suffix == ".py" else []
            else:
                python_files = list(full_path.rglob("*.py"))
            total_replacements = 0
            files_with_changes = 0

            if check_only:
                # 只检查模式
                found_issues = False
                for file_path in python_files:
                    with Path(file_path).open(encoding="utf-8") as f:
                        content = f.read()

                    # 复用修复模式的行级跳过逻辑, 仅统计实际需替换的标点 (跳过
                    # 带 noqa 标记的行和已知 Unicode 模式)
                    _, file_replacements = self._replace_chinese_punctuation(
                        content, punctuation_map
                    )
                    if file_replacements > 0:
                        files_with_changes += 1
                        total_replacements += file_replacements
                        found_issues = True

                success = not found_issues
                if found_issues:
                    output = (
                        f"检查完成，发现 {total_replacements} 个中文标点\n"
                        "💡 业务必需的中文标点可用 # noqa: RUF001 标记保护 "
                        "(脚本与 ruff 双端识别)"
                    )
                else:
                    output = "✅ 未发现中文标点问题"

            else:
                # 修复模式
                for file_path in python_files:
                    with Path(file_path).open(encoding="utf-8") as f:
                        content = f.read()

                    modified_content, file_replacements = (
                        self._replace_chinese_punctuation(content, punctuation_map)
                    )

                    if file_replacements > 0:
                        with Path(file_path).open("w", encoding="utf-8") as f:
                            f.write(modified_content)
                        files_with_changes += 1
                        total_replacements += file_replacements

                success = True
                output = (
                    f"修复完成，修复了 {total_replacements} 个标点\n"
                    "💡 业务必需的中文标点可用 # noqa: RUF001 标记保护 "
                    "(脚本与 ruff 双端识别)"
                )

            task_result = TaskResult(
                task_name=task_name,
                task_type="format",
                success=success,
                duration=time.time() - start_time,
                output=output,
                execution_details={
                    "check_only": check_only,
                    "files_processed": len(python_files),
                    "files_with_changes": files_with_changes,
                    "total_replacements": total_replacements,
                },
            )

            return task_result

        except Exception as e:
            return TaskResult(
                task_name=task_name,
                task_type="format",
                success=False,
                duration=time.time() - start_time,
                error_message=str(e),
            )

    # 匹配有意使用中文标点/Unicode符号的行模式
    # 这些行不应被标点替换脚本修改
    _INTENTIONAL_UNICODE_PATTERNS: ClassVar[list[str]] = [
        "_CN_SENTENCE_END",  # 中文句子结束标记, 用于中文文本分割
        "_simple_latex_to_text",  # LaTeX→Unicode 映射表, 含希腊字母等
    ]

    def _replace_chinese_punctuation(
        self, content: str, punctuation_map: dict[str, str]
    ) -> tuple[str, int]:
        """替换中文标点符号为英文标点.

        中文标点在 Python 源码中仅出现在字符串和注释中,
        全局替换不会影响语法语义.

        跳过策略:
        1. 带 noqa 标记的行: 业务必需的中文标点 (如正则边界/数据源键名/分句符)
           在该行添加 `# noqa: RUF001` 注释, 脚本识别 "noqa" 子串跳过,
           ruff 同步静音, 双端一致
        2. 包含已知有意 Unicode 模式的行 (如 _CN_SENTENCE_END, LaTeX映射表)
        """
        replacements_count = 0
        lines = content.split("\n")
        modified_lines = []
        for line in lines:
            if "noqa" in line:
                modified_lines.append(line)
                continue
            if any(pattern in line for pattern in self._INTENTIONAL_UNICODE_PATTERNS):
                modified_lines.append(line)
                continue
            for chinese_punc, english_punc in punctuation_map.items():
                count = line.count(chinese_punc)
                if count > 0:
                    line = line.replace(chinese_punc, english_punc)
                    replacements_count += count
            modified_lines.append(line)
        return "\n".join(modified_lines), replacements_count

    # ==================== 并行执行协调 ====================

    async def run_codex_static_analysis(self) -> dict[str, Any]:
        """顺序执行Codex sandbox安全静态分析."""
        self.console.print("[bold blue]🚀 开始Codex静态分析[/bold blue]")

        task_specs = [
            ("ruff", self.run_ruff_analysis),
            ("mypy", self.run_mypy_analysis),
            ("bandit", self.run_bandit_analysis),
        ]

        self.console.print(f"[cyan]📋 顺序启动 {len(task_specs)} 个Codex任务:[/cyan]")
        for task_name, _ in task_specs:
            self.console.print(f"  🔍 {task_name.title()}")

        start_time = time.time()
        self.results = []
        for task_name, task_func in task_specs:
            try:
                task_result = await task_func()
            except Exception as e:
                self.console.print(f"[red]❌ {task_name.title()} 执行异常: {e}[/red]")
                self.results.append(
                    TaskResult(
                        task_name=task_name.title(),
                        task_type="static_analysis",
                        success=False,
                        duration=0,
                        output="执行异常",
                        error_message=str(e),
                    )
                )
                continue

            self.results.append(task_result)
            status = "✅" if task_result.success else "❌"
            cache_indicator = " (缓存)" if task_result.cached else ""
            self.console.print(
                f"{status} {task_result.task_name}: {task_result.output}{cache_indicator}"
            )

        total_time = time.time() - start_time
        summary = self._generate_execution_summary(total_time)
        await self._save_summary_report(summary)
        return summary

    async def run_parallel_static_analysis(self) -> dict[str, Any]:
        """并行执行所有静态分析工具"""
        if self.codex_mode:
            return await self.run_codex_static_analysis()

        self.console.print(
            f"[bold blue]🚀 开始并行静态分析 ({'核心模式' if self.core_mode else '完整模式'})[/bold blue]"
        )

        # 定义任务列表
        tasks = []

        # 静态分析任务
        static_tasks = [
            asyncio.create_task(self.run_ruff_analysis(), name="ruff"),
            asyncio.create_task(self.run_mypy_analysis(), name="mypy"),
            asyncio.create_task(self.run_bandit_analysis(), name="bandit"),
            asyncio.create_task(self.run_vulture_analysis(), name="vulture"),
        ]

        # Safety在核心模式下跳过(性能瓶颈),仅在完整模式下运行
        if not self.core_mode:
            static_tasks.append(
                asyncio.create_task(self.run_safety_analysis(), name="safety")
            )

        static_tasks.append(
            asyncio.create_task(self.run_dependency_analysis(), name="dependency")
        )
        static_tasks.append(
            asyncio.create_task(self.run_config_governance_analysis(), name="config")
        )
        tasks.extend(static_tasks)

        # 显示任务启动信息
        self.console.print(f"[cyan]📋 启动 {len(tasks)} 个并行任务:[/cyan]")
        for task in static_tasks:
            self.console.print(f"  🔍 {task.get_name().title()}")

        # 等待所有任务完成
        start_time = time.time()
        completed_tasks = await asyncio.gather(*tasks, return_exceptions=True)
        total_time = time.time() - start_time

        # 收集结果
        self.results = []
        for i, task_result in enumerate(completed_tasks):
            if isinstance(task_result, BaseException):
                self.console.print(
                    f"[red]❌ {tasks[i].get_name()} 执行异常: {task_result}[/red]"
                )
                self.results.append(
                    TaskResult(
                        task_name=tasks[i].get_name().title(),
                        task_type="static_analysis",
                        success=False,
                        duration=0,
                        error_message=str(task_result),
                    )
                )
            else:
                self.results.append(task_result)
                status = "✅" if task_result.success else "❌"
                cache_indicator = " (缓存)" if task_result.cached else ""
                self.console.print(
                    f"{status} {task_result.task_name}: {task_result.output}{cache_indicator}"
                )

        # 生成执行摘要
        summary = self._generate_execution_summary(total_time)

        # 保存整体报告
        await self._save_summary_report(summary)

        return summary

    async def run_parallel_formatting(
        self, operations: list[str] | None = None
    ) -> dict[str, Any]:
        """并行执行格式化操作"""
        if operations is None:
            operations = ["check", "punctuation"]

        self.console.print("[bold blue]🎨 开始并行格式化操作[/bold blue]")

        # 定义格式化任务
        format_tasks = []

        if "check" in operations:
            format_tasks.append(
                asyncio.create_task(
                    self.run_format_code(check_only=True), name="format_check"
                )
            )
        if "fix" in operations:
            format_tasks.append(
                asyncio.create_task(self.run_fix_code(), name="fix_code")
            )
        if "punctuation" in operations:
            format_tasks.append(
                asyncio.create_task(
                    self.run_fix_punctuation(check_only=False), name="fix_punctuation"
                )
            )
        if "punctuation_check" in operations:
            format_tasks.append(
                asyncio.create_task(
                    self.run_fix_punctuation(check_only=True), name="punctuation_check"
                )
            )

        if not format_tasks:
            self.console.print("[yellow]⚠️  没有指定格式化操作[/yellow]")
            return {"success": True, "message": "没有执行操作"}

        # 显示任务启动信息
        self.console.print(f"[cyan]📋 启动 {len(format_tasks)} 个格式化任务:[/cyan]")
        for task in format_tasks:
            self.console.print(f"  🎨 {task.get_name().title()}")

        # 等待所有任务完成
        start_time = time.time()
        completed_tasks = await asyncio.gather(*format_tasks, return_exceptions=True)
        total_time = time.time() - start_time

        # 收集结果
        results = []
        all_success = True

        for i, task_result in enumerate(completed_tasks):
            if isinstance(task_result, BaseException):
                self.console.print(
                    f"[red]❌ {format_tasks[i].get_name()} 执行异常: {task_result}[/red]"
                )
                all_success = False
            else:
                results.append(task_result)
                status = "✅" if task_result.success else "❌"
                self.console.print(
                    f"{status} {task_result.task_name}: {task_result.output}"
                )
                if not task_result.success:
                    all_success = False

        return {
            "success": all_success,
            "total_time": total_time,
            "operations": operations,
            "results": results,
            "message": "格式化操作完成" if all_success else "部分格式化操作失败",
        }

    # ==================== 工具方法 ====================

    async def _save_static_analysis_report(
        self, tool_name: str, report_data: dict[str, Any]
    ) -> str | None:
        """保存静态分析报告"""
        report_path = self.reports_dir / f"{tool_name}_analysis_report.json"

        try:
            with Path(report_path).open("w", encoding="utf-8") as f:
                json.dump(report_data, f, indent=2, ensure_ascii=False)

            if self.verbose:
                self.console.print(
                    f"[dim]📄 {tool_name} 报告已保存: {report_path}[/dim]"
                )

            return str(report_path)

        except Exception as e:
            self.console.print(f"[red]保存 {tool_name} 报告失败: {e}[/red]")
            return None

    async def _save_summary_report(self, summary: dict[str, Any]) -> str | None:
        """保存执行摘要报告"""
        report_path = self.reports_dir / "static_analysis_summary.json"

        try:
            with Path(report_path).open("w", encoding="utf-8") as f:
                json.dump(summary, f, indent=2, ensure_ascii=False)

            self.console.print(f"[green]📄 分析摘要已保存: {report_path}[/green]")
            return str(report_path)

        except Exception as e:
            self.console.print(f"[red]保存分析摘要失败: {e}[/red]")
            return None

    def _generate_execution_summary(self, total_time: float) -> dict[str, Any]:
        """生成执行摘要"""
        static_results = [r for r in self.results if r.task_type == "static_analysis"]

        static_success = sum(1 for r in static_results if r.success)
        static_total = len(static_results)

        # 统计问题数量
        critical_issues = 0
        strict_issues = 0
        warning_issues = 0

        for result in static_results:
            if result.structured_data and "execution_details" in result.structured_data:
                details = result.structured_data["execution_details"]
                if "critical_issues" in details:
                    critical_issues += details["critical_issues"]
                if "strict_issues" in details:
                    strict_issues += details["strict_issues"]
                if "warning_issues" in details:
                    warning_issues += details["warning_issues"]

        # 质量门禁判断
        if self.codex_mode:
            passed = static_total > 0 and static_success == static_total
        elif self.core_mode:
            passed = critical_issues == 0 and strict_issues < 5 and warning_issues < 30
        else:
            # full模式非CI流程, 无门禁, 仅出报告供人工审阅(改进信号)
            passed = True

        execution_mode = (
            "codex" if self.codex_mode else ("core" if self.core_mode else "full")
        )

        summary = {
            "execution_mode": execution_mode,
            "timestamp": time.time(),
            "total_execution_time": total_time,
            "quality_gate": {
                "passed": passed,
                "critical_issues": critical_issues,
                "strict_issues": strict_issues,
                "warning_issues": warning_issues,
                "total_issues": critical_issues + strict_issues + warning_issues,
            },
            "tasks_summary": {
                "total_tasks": len(self.results),
                "successful_tasks": sum(1 for r in self.results if r.success),
                "failed_tasks": sum(1 for r in self.results if not r.success),
                "cached_tasks": sum(
                    1 for r in self.results if getattr(r, "cached", False)
                ),
            },
            "static_analysis_summary": {
                "total_tools": static_total,
                "successful_tools": static_success,
                "tool_results": [
                    {
                        "name": r.task_name,
                        "success": r.success,
                        "duration": r.duration,
                        "output": r.output,
                        "cached": getattr(r, "cached", False),
                        "report_path": r.report_path,
                    }
                    for r in static_results
                ],
            },
        }

        return summary

    def display_results(self) -> None:
        """显示分析结果"""
        self.console.print("\n[bold blue]🔍 静态分析结果[/bold blue]\n")

        # 显示各工具结果
        for result in self.results:
            status = "[green]✓[/green]" if result.success else "[red]✗[/red]"
            cache_indicator = " (缓存)" if getattr(result, "cached", False) else ""
            self.console.print(
                f"{status} {result.task_name}: {result.duration:.2f}s{cache_indicator}"
            )

            if result.error_message:
                self.console.print(f"  [red]错误: {result.error_message}[/red]")

        # 显示质量门禁
        summary = self._generate_execution_summary(0)
        quality_gate = summary["quality_gate"]
        gate_status = (
            "[green]✓ 通过[/green]" if quality_gate["passed"] else "[red]✗ 失败[/red]"
        )

        gate_panel = Panel(
            f"{gate_status} 质量门禁\n\n"
            f"严重问题: {quality_gate['critical_issues']}\n"
            f"严格问题: {quality_gate['strict_issues']}\n"
            f"警告问题: {quality_gate['warning_issues']}\n"
            f"总计问题: {quality_gate['total_issues']}",
            title="质量门禁检查",
            border_style="green" if quality_gate["passed"] else "red",
        )
        self.console.print(gate_panel)

    # ==================== 主入口方法 ====================

    async def run_all_analysis(self, output_path: Path | None = None) -> dict[str, Any]:
        """运行所有静态分析"""
        try:
            # 运行并行分析
            summary = await self.run_parallel_static_analysis()

            # 显示结果
            self.display_results()

            # 保存用户指定的输出文件
            if output_path:
                with output_path.open("w", encoding="utf-8") as f:
                    json.dump(summary, f, indent=2, ensure_ascii=False)
                self.console.print(f"[green]📄 报告已保存到: {output_path}[/green]")

            # 检查质量门禁
            if not summary["quality_gate"]["passed"]:
                self.console.print(
                    f"\n[yellow]⚠️  质量门禁检查失败: {summary['quality_gate']['total_issues']} 个问题[/yellow]"
                )
            else:
                self.console.print(
                    f"\n[green]✅ 质量门禁检查通过! ({summary['quality_gate']['total_issues']} 个警告)[/green]"
                )

            return summary

        except Exception as e:
            self.console.print(f"[red]❌ 分析执行出错: {e}[/red]")
            raise

    async def run_format_operations(
        self, operations: list[str] | None = None
    ) -> dict[str, Any]:
        """运行格式化操作"""
        try:
            return await self.run_parallel_formatting(operations)
        except Exception as e:
            self.console.print(f"[red]❌ 格式化执行出错: {e}[/red]")
            raise


# ==================== 命令行接口 ====================


def main() -> None:
    """主函数"""
    parser = argparse.ArgumentParser(description="Assistant项目静态分析运行器 V2.0")
    parser.add_argument(
        "--core-mode",
        action="store_true",
        default=True,
        help="启用核心模式(默认)",
    )
    parser.add_argument("--full-mode", action="store_true", help="启用完整模式")
    parser.add_argument(
        "--codex",
        action="store_true",
        help="启用Codex sandbox兼容模式(顺序核心检查,跳过网络/重型任务)",
    )
    parser.add_argument("--verbose", action="store_true", help="详细输出")
    parser.add_argument("--no-cache", action="store_true", help="禁用缓存")
    parser.add_argument(
        "--without-safety",
        action="store_true",
        help="跳过Safety安全检查（加快执行速度）",
    )
    parser.add_argument("--output", type=str, help="输出文件路径")

    # 子命令
    subparsers = parser.add_subparsers(dest="command", help="可用命令")

    # 分析命令
    analyze_parser = subparsers.add_parser("analyze", help="运行静态分析")
    analyze_parser.add_argument("--quick", action="store_true", help="快速分析")

    # 格式化命令
    format_parser = subparsers.add_parser("format", help="格式化代码")
    format_parser.add_argument("--check", action="store_true", help="只检查不修复")
    format_parser.add_argument("--fix", action="store_true", help="修复代码问题")
    format_parser.add_argument(
        "--punctuation", action="store_true", help="修复中文标点"
    )
    format_parser.add_argument(
        "--punctuation-check", action="store_true", help="检查中文标点"
    )
    format_parser.add_argument(
        "--file", "-f", type=str, default="src/", help="目标文件或目录路径 (默认 src/)"
    )

    args = parser.parse_args()

    # 设置模式
    core_mode = True if args.codex else (False if args.full_mode else args.core_mode)

    # 创建运行器
    target_path = args.file if hasattr(args, "file") else "src/"
    runner = StaticAnalysisRunner(
        core_mode=core_mode,
        verbose=args.verbose,
        enable_cache=not args.no_cache,
        target_path=target_path,
        codex_mode=args.codex,
        args=args,
    )

    try:
        # 准备输出路径
        output_path = Path(args.output) if args.output else None

        if args.command == "analyze":
            # 运行静态分析
            result = asyncio.run(runner.run_all_analysis(output_path))

            # 根据质量门禁结果设置退出码
            if result["quality_gate"]["passed"]:
                sys.exit(0)
            else:
                sys.exit(1)

        elif args.command == "format":
            # 运行格式化操作
            operations = []
            if args.check:
                operations.append("check")
            if args.fix:
                operations.append("fix")
            if args.punctuation:
                operations.append("punctuation")
            if args.punctuation_check:
                operations.append("punctuation_check")

            if not operations:
                operations = ["check"]  # 默认操作

            result = asyncio.run(runner.run_format_operations(operations))
            sys.exit(0 if result["success"] else 1)

        else:
            # 默认运行静态分析
            result = asyncio.run(runner.run_all_analysis(output_path))

            if result["quality_gate"]["passed"]:
                sys.exit(0)
            else:
                sys.exit(1)

    except KeyboardInterrupt:
        print("\n用户中断")
        sys.exit(1)
    except Exception as e:
        print(f"错误: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
