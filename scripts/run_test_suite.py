#!/usr/bin/env python3
"""CI/CD测试套件执行器 - 自主管理架构.

两种执行模式:
- quick (--quick): CI门禁. 测试 + 静态分析(core精选规则) -> 阻断性, 日常CI/提交前审查.
- 完整(默认): 探索工具. 全量规则静态分析 + E2E + Safety -> 非CI流程, 无门禁, 出报告供人工审阅.

CI门禁(quick模式, 阻断):
- 静态分析: Ruff/MyPy/Bandit/Vulture/Dependency/配置治理 全部通过(core精选规则, 正常代码全绿)
- 单元测试: 100%通过
- 集成测试: 100%通过

完整模式(full)非CI流程, 无门禁, exit 0, 仅出全量规则分析报告(改进信号).

所有任务并行执行, 自主管理测试命令和报告生成.
"""

import argparse
import asyncio
import contextlib
import json
import logging
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table

# 配置日志
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# 确保项目根目录在Python路径中
project_root = Path(__file__).parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

console = Console()


def _load_coverage_gate() -> float:
    """从 pyproject.toml 读取覆盖率 CI 门禁(SSOT), 失败回退 80."""
    try:
        import tomllib

        path = Path(__file__).resolve().parent.parent / "pyproject.toml"
        with open(path, "rb") as f:
            data = tomllib.load(f)
        return float(data["tool"]["coverage"]["fail_under"]["min_cover_percentage"])
    except Exception:
        return 80.0


# 导入现有的专业脚本
try:
    from scripts.static_analysis import StaticAnalysisRunner
except ImportError:
    StaticAnalysisRunner = None
    console.print(
        "[yellow]警告: 无法导入 StaticAnalysisRunner，将跳过静态分析[/yellow]"
    )


try:
    from scripts.simple_clean import SimpleCleaner
except ImportError:
    SimpleCleaner = None
    console.print("[yellow]警告: 无法导入 SimpleCleaner，将跳过清理功能[/yellow]")


@dataclass
class TaskResult:
    """任务执行结果"""

    task_name: str
    task_type: str  # "static_analysis" or "test"
    success: bool
    duration: float
    output: str
    error_message: str | None = None
    report_path: str | None = None
    structured_data: dict[str, Any] | None = None
    execution_details: dict[str, Any] | None = None  # 详细执行信息


# 任务元数据: asyncio task name -> (中文显示名, task_type)
# 异常兜底使用此映射归类, 避免 task_type="unknown" 被门禁逻辑跳过(门禁绕过风险)
TASK_METADATA: dict[str, tuple[str, str]] = {
    "ruff": ("Ruff代码分析", "static_analysis"),
    "mypy": ("MyPy类型分析", "static_analysis"),
    "bandit": ("Bandit安全分析", "static_analysis"),
    "safety": ("Safety依赖分析", "static_analysis"),
    "config": ("配置治理检查", "static_analysis"),
    "unit": ("单元测试", "test"),
    "integration": ("集成测试", "test"),
    "combined": ("综合测试", "test"),
    "e2e": ("E2E测试", "test"),
}


class E2ETestResult:
    """E2E测试结果（内部使用）"""

    def __init__(
        self,
        success: bool,
        output: str,
        duration: float,
        error: str | None = None,
        test_details: dict[str, Any] | None = None,
    ):
        self.success = success
        self.output = output
        self.error = error
        self.duration = duration
        self.test_details = test_details or {}


class CIParallelRunner:
    """CI并行执行器 - 统一架构"""

    def __init__(
        self, quick_mode: bool = False, verbose: bool = False, codex_mode: bool = False
    ):
        self.quick_mode = quick_mode or codex_mode
        self.verbose = verbose
        self.codex_mode = codex_mode
        self.console = Console()
        self.start_time = time.time()
        self.reports_dir = project_root / "reports"
        self.results: list[TaskResult] = []

        # 确保reports目录存在
        self.reports_dir.mkdir(exist_ok=True)

        if self.verbose:
            mode_label = (
                "Codex" if codex_mode else ("快速" if self.quick_mode else "完整")
            )
            self.console.print(f"[blue]🚀 CI并行执行器启动 (模式: {mode_label})[/blue]")

    # ==================== 静态分析工具调用 ====================

    @staticmethod
    def _parse_pytest_summary(stdout: str) -> tuple[int, int, int]:
        """从pytest输出解析通过/失败/跳过数量."""
        passed = failed = skipped = 0
        for line in stdout.split("\n"):
            if "=" in line and (
                "passed" in line
                or "failed" in line
                or "skipped" in line
                or "error" in line
            ):
                parts = line.replace("=", "").split(",")
                for part in parts:
                    part = part.strip()
                    if "passed" in part:
                        with contextlib.suppress(ValueError, IndexError):
                            passed = int(part.split()[0].replace(",", ""))
                    elif "failed" in part:
                        with contextlib.suppress(ValueError, IndexError):
                            failed = int(part.split()[0].replace(",", ""))
                    elif "error" in part:
                        with contextlib.suppress(ValueError, IndexError):
                            failed += int(part.split()[0].replace(",", ""))
                    elif "skipped" in part:
                        with contextlib.suppress(ValueError, IndexError):
                            skipped = int(part.split()[0].replace(",", ""))
        return passed, failed, skipped

    async def run_ruff_analysis(self) -> TaskResult:
        """运行Ruff代码分析 - 委托给StaticAnalysisRunner"""
        start_time = time.time()

        if StaticAnalysisRunner is None:
            return TaskResult(
                task_name="Ruff代码分析",
                task_type="static_analysis",
                success=False,
                duration=time.time() - start_time,
                output="StaticAnalysisRunner 未加载",
                error_message="StaticAnalysisRunner 未加载",
            )

        try:
            runner = StaticAnalysisRunner(
                core_mode=self.quick_mode,
                verbose=self.verbose,
                codex_mode=self.codex_mode,
            )
            result = await runner.run_ruff_analysis()

            # 转换为CI格式的TaskResult
            return TaskResult(
                task_name="Ruff代码分析",
                task_type="static_analysis",
                success=result.success,
                duration=result.duration,
                output=result.output,
                report_path=result.report_path,
                structured_data=result.structured_data,
                execution_details=result.execution_details,
            )

        except Exception as e:
            return TaskResult(
                task_name="Ruff代码分析",
                task_type="static_analysis",
                success=False,
                duration=time.time() - start_time,
                error_message=str(e),
            )

    async def run_mypy_analysis(self) -> TaskResult:
        """运行MyPy类型分析 - 委托给StaticAnalysisRunner"""
        start_time = time.time()

        if StaticAnalysisRunner is None:
            return TaskResult(
                task_name="MyPy类型分析",
                task_type="static_analysis",
                success=False,
                duration=time.time() - start_time,
                output="StaticAnalysisRunner 未加载",
                error_message="StaticAnalysisRunner 未加载",
            )

        try:
            runner = StaticAnalysisRunner(
                core_mode=self.quick_mode,
                verbose=self.verbose,
                codex_mode=self.codex_mode,
            )
            result = await runner.run_mypy_analysis()

            # 转换为CI格式的TaskResult
            return TaskResult(
                task_name="MyPy类型分析",
                task_type="static_analysis",
                success=result.success,
                duration=result.duration,
                output=result.output,
                report_path=result.report_path,
                structured_data=result.structured_data,
                execution_details=result.execution_details,
            )

        except Exception as e:
            return TaskResult(
                task_name="MyPy类型分析",
                task_type="static_analysis",
                success=False,
                duration=time.time() - start_time,
                error_message=str(e),
            )

    async def run_bandit_analysis(self) -> TaskResult:
        """运行Bandit安全分析 - 委托给StaticAnalysisRunner"""
        start_time = time.time()

        if StaticAnalysisRunner is None:
            return TaskResult(
                task_name="Bandit安全分析",
                task_type="static_analysis",
                success=False,
                duration=time.time() - start_time,
                output="StaticAnalysisRunner 未加载",
                error_message="StaticAnalysisRunner 未加载",
            )

        try:
            runner = StaticAnalysisRunner(
                core_mode=self.quick_mode,
                verbose=self.verbose,
                codex_mode=self.codex_mode,
            )
            result = await runner.run_bandit_analysis()

            # 转换为CI格式的TaskResult
            return TaskResult(
                task_name="Bandit安全分析",
                task_type="static_analysis",
                success=result.success,
                duration=result.duration,
                output=result.output,
                report_path=result.report_path,
                structured_data=result.structured_data,
                execution_details=result.execution_details,
            )

        except Exception as e:
            return TaskResult(
                task_name="Bandit安全分析",
                task_type="static_analysis",
                success=False,
                duration=time.time() - start_time,
                error_message=str(e),
            )

    async def run_safety_analysis(self) -> TaskResult:
        """运行Safety依赖安全分析 - 委托给StaticAnalysisRunner"""
        start_time = time.time()

        if StaticAnalysisRunner is None:
            return TaskResult(
                task_name="Safety依赖分析",
                task_type="static_analysis",
                success=False,
                duration=time.time() - start_time,
                output="StaticAnalysisRunner 未加载",
                error_message="StaticAnalysisRunner 未加载",
            )

        try:
            runner = StaticAnalysisRunner(
                core_mode=self.quick_mode,
                verbose=self.verbose,
                codex_mode=self.codex_mode,
            )
            result = await runner.run_safety_analysis()

            # 转换为CI格式的TaskResult
            return TaskResult(
                task_name="Safety依赖分析",
                task_type="static_analysis",
                success=result.success,
                duration=result.duration,
                output=result.output,
                report_path=result.report_path,
                structured_data=result.structured_data,
                execution_details=result.execution_details,
            )

        except Exception as e:
            # Safety检查超时或失败不算作严重错误
            return TaskResult(
                task_name="Safety依赖分析",
                task_type="static_analysis",
                success=True,  # Safety失败不阻塞CI
                duration=time.time() - start_time,
                output=f"Safety检查失败或超时: {e!s}",
                error_message=str(e),
            )

    async def run_config_governance_analysis(self) -> TaskResult:
        """运行配置治理检查 - 委托给StaticAnalysisRunner.

        CI硬门禁: config.yaml结构合法性 + 裸env读取检查, 失败则CI不通过.
        """
        start_time = time.time()

        if StaticAnalysisRunner is None:
            return TaskResult(
                task_name="配置治理检查",
                task_type="static_analysis",
                success=False,
                duration=time.time() - start_time,
                output="StaticAnalysisRunner 未加载",
                error_message="StaticAnalysisRunner 未加载",
            )

        try:
            runner = StaticAnalysisRunner(
                core_mode=self.quick_mode,
                verbose=self.verbose,
                codex_mode=self.codex_mode,
            )
            result = await runner.run_config_governance_analysis()

            return TaskResult(
                task_name="配置治理检查",
                task_type="static_analysis",
                success=result.success,
                duration=result.duration,
                output=result.output,
                report_path=result.report_path,
                structured_data=result.structured_data,
                execution_details=result.execution_details,
            )

        except Exception as e:
            return TaskResult(
                task_name="配置治理检查",
                task_type="static_analysis",
                success=False,
                duration=time.time() - start_time,
                error_message=str(e),
            )

    # ==================== 测试执行 ====================

    async def run_combined_tests_with_coverage(self) -> TaskResult:
        """运行合并的单元+集成测试（默认模式专用）"""
        start_time = time.time()

        try:
            if self.verbose:
                self.console.print("[cyan]🧪 运行综合测试：单元测试 + 集成测试[/cyan]")

            # 构建pytest命令：单元+集成测试一起运行，生成综合覆盖率
            cmd = [
                sys.executable,
                "-m",
                "pytest",
                "tests/unit/",
                "tests/integration/",
                "--cov=src",
                "--cov-report=xml:reports/coverage.xml",
                "--cov-report=html:reports/coverage_html",
                "--cov-report=term-missing",
                "-v",
            ]

            if self.verbose:
                self.console.print(f"[dim]执行命令: {' '.join(cmd)}[/dim]")

            # 在线程池中执行同步命令，添加超时避免卡死
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                None,
                lambda: subprocess.run(
                    cmd,
                    cwd=project_root,
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=1200,  # 20分钟超时，综合测试需要更多时间
                ),
            )

            # 解析pytest输出
            passed_tests, failed_tests, _ = self._parse_pytest_summary(result.stdout)
            total_tests = passed_tests + failed_tests

            # 构建执行详情
            execution_details = {
                "total_tests": total_tests,
                "passed_tests": passed_tests,
                "failed_tests": failed_tests,
                "exit_code": result.returncode,
                "coverage_enabled": True,
                "test_types": ["unit", "integration"],
            }

            # 完整模式要求全部通过
            success = result.returncode == 0
            pass_rate = (passed_tests / max(total_tests, 1)) * 100

            output_summary = f"通过率: {pass_rate:.1f}% ({passed_tests}/{total_tests})"
            if failed_tests > 0:
                output_summary += f", 失败: {failed_tests}"

            return TaskResult(
                task_name="综合测试",
                task_type="test",
                success=success,
                duration=time.time() - start_time,
                output=output_summary,
                report_path="reports/coverage.xml",
                structured_data={
                    "test_type": "combined",
                    "timestamp": time.time(),
                    "success": success,
                    "test_stats": execution_details,
                    "execution_details": execution_details,
                    "raw_output": result.stdout,
                    "raw_stderr": result.stderr,
                },
                execution_details=execution_details,
            )

        except subprocess.TimeoutExpired as e:
            return TaskResult(
                task_name="综合测试",
                task_type="test",
                success=False,
                duration=time.time() - start_time,
                output="执行超时（20分钟）",
                error_message=f"测试执行超时: {e}",
            )
        except Exception as e:
            return TaskResult(
                task_name="综合测试",
                task_type="test",
                success=False,
                duration=time.time() - start_time,
                output="执行失败",
                error_message=str(e),
            )

    async def run_unit_tests_simple(self) -> TaskResult:
        """快速模式专用的简化单元测试"""
        start_time = time.time()
        task_name = "单元测试(Codex)" if self.codex_mode else "单元测试(快速)"

        try:
            if self.verbose:
                self.console.print(f"🧪 运行{task_name}...")

            worker_count = "0" if self.codex_mode else "6"
            test_target = (
                "tests/unit/core/test_path_resolver.py"
                if self.codex_mode
                else "tests/unit/"
            )

            # 直接调用pytest
            cmd = [
                sys.executable,
                "-m",
                "pytest",
                test_target,
                "--ignore=tests/integration/",
                "--ignore=tests/e2e/",
                "-m unit or (not integration and not e2e)",
                "-n",
                worker_count,
                "--tb=short",  # 简化错误输出
                "-q",  # 静默模式
            ]

            if self.verbose:
                self.console.print(f"[dim]执行命令: {' '.join(cmd)}[/dim]")

            def run_pytest() -> subprocess.CompletedProcess[str]:
                return subprocess.run(
                    cmd,
                    cwd=project_root,
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=600,  # 10分钟超时，避免无限等待
                    env={**os.environ, "TEST_PROCESS_PREFIX": "unit"},
                )

            if self.codex_mode:
                result = run_pytest()
            else:
                # 在线程池中执行同步命令，添加超时避免卡死
                loop = asyncio.get_event_loop()
                result = await loop.run_in_executor(None, run_pytest)

            # 解析pytest输出
            passed, failed, skipped = self._parse_pytest_summary(result.stdout)

            # 保存简化报告
            report_data = {
                "report_type": "unit_tests",
                "mode": "codex" if self.codex_mode else "quick",
                "timestamp": time.time(),
                "tests_run": passed + failed + skipped,
                "tests_passed": passed,
                "tests_failed": failed,
                "tests_skipped": skipped,
                "execution_time": time.time() - start_time,
                "exit_code": result.returncode,
                "success": result.returncode == 0,
                "codex_smoke": self.codex_mode,
            }

            # 确保reports/current目录存在
            current_dir = self.reports_dir / "current"
            current_dir.mkdir(exist_ok=True)

            report_path = current_dir / "unit_tests_quick.json"
            with open(report_path, "w", encoding="utf-8") as f:
                json.dump(report_data, f, indent=2, ensure_ascii=False)

            # 保存完整 pytest 输出, 便于排查偶发失败
            full_log_path = current_dir / "unit_tests_quick_full.log"
            with open(full_log_path, "w", encoding="utf-8") as f:
                f.write(result.stdout)
                if result.stderr:
                    f.write("\n\n=== STDERR ===\n")
                    f.write(result.stderr)

            success = result.returncode == 0
            output = f"通过: {passed}/{passed + failed + skipped}"
            if failed > 0:
                output += f", 失败: {failed}"
            if skipped > 0:
                output += f", 跳过: {skipped}"

            return TaskResult(
                task_name=task_name,
                task_type="test",
                success=success,
                duration=time.time() - start_time,
                output=output,
                report_path=str(report_path),
                structured_data=report_data,
                execution_details=report_data,
            )

        except subprocess.TimeoutExpired as e:
            return TaskResult(
                task_name=task_name,
                task_type="test",
                success=False,
                duration=time.time() - start_time,
                output="单元测试执行超时（10分钟）",
                error_message=f"测试执行超时: {e}",
            )
        except Exception as e:
            return TaskResult(
                task_name=task_name,
                task_type="test",
                success=False,
                duration=time.time() - start_time,
                output=f"单元测试执行异常: {e!s}",
                error_message=str(e),
            )

    async def run_integration_tests(self) -> TaskResult:
        """运行集成测试 - 直接调用 pytest，自主管理执行"""
        start_time = time.time()

        try:
            if self.verbose:
                self.console.print("🔗 运行集成测试...")

            # 构建pytest命令
            cmd = [
                sys.executable,
                "-m",
                "pytest",
                "tests/integration/",
                "--ignore=tests/unit/",
                "--ignore=tests/e2e/",
                "-m",
                "integration and not skip",
                "-n",
                "8",  # 集成测试8线程 (实测到-n16零失败, 隔离设计稳健; -n8比-n2快~32%)
                "--timeout=5",  # 5秒超时
                "--timeout-method=thread",
                "-v" if self.verbose else "-q",
            ]

            if self.verbose:
                self.console.print(f"[dim]执行命令: {' '.join(cmd)}[/dim]")

            # 在线程池中执行同步命令
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                None,
                lambda: subprocess.run(
                    cmd,
                    cwd=project_root,
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=600,  # 10分钟超时
                    env={**os.environ, "TEST_PROCESS_PREFIX": "integration"},
                ),
            )

            # 解析pytest输出
            passed, failed, skipped = self._parse_pytest_summary(result.stdout)

            # 构建执行详情
            execution_details = {
                "tests_run": passed + failed + skipped,
                "tests_passed": passed,
                "tests_failed": failed,
                "tests_skipped": skipped,
                "exit_code": result.returncode,
                "threads": 2,
            }

            # 保存测试报告
            current_dir = self.reports_dir / "current"
            current_dir.mkdir(exist_ok=True)

            report_path = current_dir / "integration_tests_report.json"
            with open(report_path, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "report_type": "integration_tests",
                        "mode": "quick" if self.quick_mode else "full",
                        "timestamp": time.time(),
                        **execution_details,
                        "execution_time": time.time() - start_time,
                        "success": result.returncode == 0,
                    },
                    f,
                    indent=2,
                    ensure_ascii=False,
                )

            # 计算通过率
            total_tests = execution_details["tests_run"]
            pass_rate = (passed / max(total_tests, 1)) * 100

            # CI通过标准：集成测试100%通过
            success = result.returncode == 0 and pass_rate >= 100.0

            output = f"通过率: {pass_rate:.1f}% ({passed}/{total_tests})"
            if failed > 0:
                output += f", 失败: {failed}"
            if skipped > 0:
                output += f", 跳过: {skipped}"

            return TaskResult(
                task_name="集成测试",
                task_type="test",
                success=success,
                duration=time.time() - start_time,
                output=output,
                report_path=str(report_path),
                structured_data={
                    "test_type": "integration",
                    "timestamp": time.time(),
                    "success": success,
                    "test_stats": execution_details,
                    "execution_details": execution_details,
                    "raw_output": result.stdout,
                },
                execution_details=execution_details,
            )

        except subprocess.TimeoutExpired as e:
            return TaskResult(
                task_name="集成测试",
                task_type="test",
                success=False,
                duration=time.time() - start_time,
                output="集成测试执行超时（10分钟）",
                error_message=f"测试执行超时: {e}",
            )
        except Exception as e:
            return TaskResult(
                task_name="集成测试",
                task_type="test",
                success=False,
                duration=time.time() - start_time,
                output=f"集成测试执行异常: {e!s}",
                error_message=str(e),
            )

    async def run_e2e_tests(self) -> TaskResult:
        """运行E2E测试 - 仅在完整模式下运行快速模式E2E测试"""
        start_time = time.time()

        try:
            # 快速模式跳过E2E测试
            if self.quick_mode:
                return TaskResult(
                    task_name="E2E测试",
                    task_type="test",
                    success=True,  # 跳过的测试视为成功
                    duration=0,
                    output="快速模式跳过E2E测试",
                    execution_details={
                        "mode": "quick",
                        "skipped": True,
                        "reason": "快速模式不包含E2E测试",
                    },
                )

            # 完整模式运行E2E测试（pytest框架）
            if self.verbose:
                self.console.print("🚀 完整模式运行E2E测试（pytest灰盒测试框架）...")

            # 在线程池中运行E2E测试（固定使用快速模式）
            result = await asyncio.get_event_loop().run_in_executor(
                None, lambda: self._run_e2e_direct()
            )

            # 转换为CI格式的TaskResult
            execution_details = {
                "test_framework": "pytest",
                "test_type": "e2e_graybox",
                "service_managed": True,  # 通过conftest.py fixture管理
                "service_type": "pytest_fixture",
                "ci_mode": "full_with_e2e",  # 标识这是完整模式中的E2E测试
            }

            # 如果有测试详情，添加到执行详情中
            if hasattr(result, "test_details") and result.test_details:
                execution_details.update(result.test_details)

            # 保存E2E测试报告
            e2e_report_path = await self._save_e2e_report(result)

            return TaskResult(
                task_name="E2E测试(快速)",
                task_type="test",
                success=result.success,
                duration=result.duration,
                output=result.output,
                report_path=e2e_report_path,
                execution_details=execution_details,
            )

        except Exception as e:
            return TaskResult(
                task_name="E2E测试(快速)",
                task_type="test",
                success=False,
                duration=time.time() - start_time,
                output=f"E2E测试执行异常: {e!s}",
                error_message=str(e),
                execution_details={
                    "mode": "quick",
                    "service_managed": False,
                    "error": str(e),
                },
            )

    # E2E测试调度策略：
    # - 快速模式：完全跳过E2E测试
    # - 完整模式：使用pytest运行E2E测试（灰盒测试, 阻塞CI）
    # - 服务管理：通过conftest.py的e2e_test_service fixture自动管理

    def _run_e2e_direct(self) -> E2ETestResult:
        """使用pytest运行E2E测试（灰盒测试框架）"""
        try:
            if self.verbose:
                self.console.print("📋 使用pytest运行E2E测试（灰盒测试框架）...")

            # 构建命令：使用pytest运行E2E测试
            cmd = [
                sys.executable,
                "-m",
                "pytest",
                "tests/e2e/",
                "-v",
                "-m",
                "e2e",
                "--tb=short",
                "-p",
                "no:warnings",
            ]

            if self.verbose:
                self.console.print(f"[dim]执行命令: {' '.join(cmd)}[/dim]")

            # 运行E2E测试（pytest框架）
            start_time = time.time()
            result = subprocess.run(
                cmd,
                cwd=project_root,
                capture_output=True,
                text=True,
                check=False,
                timeout=600,  # 10分钟超时
            )
            duration = time.time() - start_time

            success = result.returncode == 0
            output = result.stdout.strip()
            error = result.stderr.strip() if result.stderr else None

            # 解析输出获取测试详情
            test_details = {
                "exit_code": result.returncode,
                "execution_time": duration,
                "mode": "quick",
                "direct_execution": True,
            }

            # 尝试从输出中提取测试统计
            if output:
                for line in output.split("\n"):
                    if "通过率:" in line or "success rate" in line.lower():
                        test_details["summary"] = line.strip()
                    elif "总场景数:" in line or "total scenarios" in line.lower():
                        test_details["total_scenarios"] = line.strip()
                    elif "失败场景:" in line or "failed scenarios" in line.lower():
                        test_details["failed_scenarios"] = line.strip()

            if self.verbose:
                status = "✅ 成功" if success else "❌ 失败"
                self.console.print(f"📊 E2E测试完成: {status}")

            return E2ETestResult(success, output, duration, error, test_details)

        except subprocess.TimeoutExpired:
            return E2ETestResult(
                False,
                "E2E测试执行超时",
                600,
                "Execution timeout after 10 minutes",
                {"timeout": True, "mode": "quick"},
            )

        except Exception as e:
            error_msg = f"E2E测试执行失败: {e!s}"
            return E2ETestResult(
                False, error_msg, 0, error_msg, {"exception": str(e), "mode": "quick"}
            )

    # ==================== 并行执行协调 ====================

    async def run_codex_tasks(self) -> dict[str, Any]:
        """顺序执行Codex sandbox安全任务."""
        task_specs = [
            ("ruff", self.run_ruff_analysis),
            ("mypy", self.run_mypy_analysis),
            ("bandit", self.run_bandit_analysis),
            ("unit", self.run_unit_tests_simple),
        ]

        self.console.print(f"[cyan]📋 顺序启动 {len(task_specs)} 个Codex任务:[/cyan]")
        for task_name, _ in task_specs:
            icon = "🧪" if task_name == "unit" else "🔍"
            self.console.print(f"  {icon} {task_name.title()}")

        start_time = time.time()
        self.results = []
        for task_name, task_func in task_specs:
            try:
                task_result = await task_func()
            except Exception as e:
                self.console.print(f"[red]❌ {task_name.title()} 执行异常: {e}[/red]")
                # 用映射归类 task_type, 确保崩溃任务仍被门禁逻辑检测到
                display_name, task_type = TASK_METADATA.get(
                    task_name, (task_name.title(), "unknown")
                )
                self.results.append(
                    TaskResult(
                        task_name=display_name,
                        task_type=task_type,
                        success=False,
                        duration=0,
                        output="执行异常",
                        error_message=str(e),
                    )
                )
                continue

            self.results.append(task_result)
            status = "✅" if task_result.success else "❌"
            self.console.print(
                f"{status} {task_result.task_name}: {task_result.output}"
            )

        total_time = time.time() - start_time
        summary = self._generate_execution_summary(total_time)
        await self._save_ci_summary_report(summary)
        return summary

    async def run_parallel_tasks(self) -> dict[str, Any]:
        """并行执行所有任务"""
        mode_label = (
            "Codex" if self.codex_mode else ("快速" if self.quick_mode else "完整")
        )
        self.console.print(f"[bold blue]🚀 开始执行 ({mode_label}模式)[/bold blue]")

        if self.codex_mode:
            return await self.run_codex_tasks()

        # CI预构建清理：归档reports目录和根目录文件 - 使用SimpleCleaner
        try:
            self.console.print(
                "[cyan]🗂️  执行预构建清理：归档reports目录和根目录文件...[/cyan]"
            )

            # 使用简化清理工具
            cleaner = SimpleCleaner()

            # 并行执行两个清理任务
            reports_result, root_files_result = await asyncio.gather(
                asyncio.get_event_loop().run_in_executor(
                    None, cleaner.archive_reports_directory
                ),
                asyncio.get_event_loop().run_in_executor(
                    None, cleaner.archive_root_files
                ),
                return_exceptions=True,
            )

            # 检查执行结果
            if isinstance(reports_result, Exception):
                self.console.print(
                    f"[yellow]⚠️ reports目录归档异常: {reports_result}[/yellow]"
                )
            elif isinstance(root_files_result, Exception):
                self.console.print(
                    f"[yellow]⚠️ 根目录文件归档异常: {root_files_result}[/yellow]"
                )
            else:
                self.console.print(
                    "[green]✅ 预构建清理完成（reports + 根目录文件）[/green]"
                )

        except Exception as e:
            self.console.print(f"[yellow]⚠️ 预构建清理失败: {e}[/yellow]")

        # 定义任务列表
        tasks = []

        # 静态分析任务
        if self.quick_mode:
            # 快速模式：静态分析(不含Safety, 不阻塞) + 配置治理硬门禁 + 单元测试 + 集成测试
            static_tasks = [
                asyncio.create_task(self.run_ruff_analysis(), name="ruff"),
                asyncio.create_task(self.run_mypy_analysis(), name="mypy"),
                asyncio.create_task(self.run_bandit_analysis(), name="bandit"),
                asyncio.create_task(
                    self.run_config_governance_analysis(), name="config"
                ),
            ]
            test_tasks = [
                asyncio.create_task(self.run_unit_tests_simple(), name="unit"),
                asyncio.create_task(self.run_integration_tests(), name="integration"),
            ]
        else:
            # 完整模式：所有静态分析 + 配置治理硬门禁 + 综合测试 + 快速模式E2E测试
            static_tasks = [
                asyncio.create_task(self.run_ruff_analysis(), name="ruff"),
                asyncio.create_task(self.run_mypy_analysis(), name="mypy"),
                asyncio.create_task(self.run_bandit_analysis(), name="bandit"),
                asyncio.create_task(self.run_safety_analysis(), name="safety"),
                asyncio.create_task(
                    self.run_config_governance_analysis(), name="config"
                ),
            ]
            test_tasks = [
                asyncio.create_task(
                    self.run_combined_tests_with_coverage(), name="combined"
                ),
                asyncio.create_task(self.run_e2e_tests(), name="e2e"),
            ]

        tasks.extend(static_tasks)
        tasks.extend(test_tasks)

        # 显示任务启动信息
        self.console.print(f"[cyan]📋 启动 {len(tasks)} 个并行任务:[/cyan]")
        for task in static_tasks:
            self.console.print(f"  🔍 {task.get_name().title()}")
        for task in test_tasks:
            self.console.print(f"  🧪 {task.get_name().title()}")

        # 等待所有任务完成
        start_time = time.time()
        completed_tasks = await asyncio.gather(*tasks, return_exceptions=True)
        total_time = time.time() - start_time

        # 收集结果
        self.results = []
        for i, task_result in enumerate(completed_tasks):
            if isinstance(task_result, Exception):
                self.console.print(
                    f"[red]❌ {tasks[i].get_name()} 执行异常: {task_result}[/red]"
                )
                # 用映射归类 task_type, 确保崩溃任务仍被门禁逻辑检测到
                display_name, task_type = TASK_METADATA.get(
                    tasks[i].get_name(),
                    (tasks[i].get_name().title(), "unknown"),
                )
                self.results.append(
                    TaskResult(
                        task_name=display_name,
                        task_type=task_type,
                        success=False,
                        duration=0,
                        output="执行异常",
                        error_message=str(task_result),
                    )
                )
            else:
                self.results.append(task_result)
                status = "✅" if task_result.success else "❌"
                self.console.print(
                    f"{status} {task_result.task_name}: {task_result.output}"
                )

        # 生成执行摘要
        summary = self._generate_execution_summary(total_time)

        # 完整模式：运行简化的智能覆盖率分析
        if not self.quick_mode:
            coverage_analysis = await self._run_simplified_coverage_analysis()
            if coverage_analysis:
                summary["coverage_analysis"] = coverage_analysis

        # 保存整体报告
        await self._save_ci_summary_report(summary)

        return summary

    async def _run_simplified_coverage_analysis(self) -> dict[str, Any] | None:
        """解析 coverage.xml 生成覆盖率摘要报告（完整模式专用）"""

        try:
            if self.verbose:
                self.console.print("[cyan]📊 解析覆盖率报告...[/cyan]")

            coverage_xml = self.reports_dir / "coverage.xml"
            if not coverage_xml.exists():
                if self.verbose:
                    self.console.print(
                        "[yellow]⚠️ coverage.xml 不存在，跳过分析[/yellow]"
                    )
                return None

            import xml.etree.ElementTree as ET

            tree = ET.parse(coverage_xml)
            root = tree.getroot()

            total_lines = int(root.get("lines-valid", 0))
            covered_lines = int(root.get("lines-covered", 0))
            overall_coverage = (
                round((covered_lines / total_lines) * 100, 2) if total_lines > 0 else 0
            )

            module_stats: dict[str, dict[str, Any]] = {}
            for class_elem in root.findall(".//class"):
                filename = class_elem.get("filename", "")
                if not filename:
                    continue
                if not filename.startswith("src/"):
                    filename = f"src/{filename}"

                path_parts = filename.split("/")
                if len(path_parts) < 2:
                    continue
                module_name = path_parts[1]

                lines_elem = class_elem.find("lines")
                if lines_elem is None:
                    continue

                file_lines = sum(1 for _ in lines_elem.findall("line"))
                file_covered = sum(
                    1
                    for line_elem in lines_elem.findall("line")
                    if int(line_elem.get("hits", 0)) > 0
                )
                if file_lines == 0:
                    continue

                if module_name not in module_stats:
                    module_stats[module_name] = {
                        "name": module_name,
                        "total_lines": 0,
                        "covered_lines": 0,
                        "files_count": 0,
                    }

                module_stats[module_name]["total_lines"] += file_lines
                module_stats[module_name]["covered_lines"] += file_covered
                module_stats[module_name]["files_count"] += 1

            for module in module_stats.values():
                if module["total_lines"] > 0:
                    module["percent_covered"] = round(
                        (module["covered_lines"] / module["total_lines"]) * 100, 2
                    )
                else:
                    module["percent_covered"] = 0.0

            target_coverage = _load_coverage_gate()
            success = overall_coverage >= target_coverage
            module_list = sorted(
                module_stats.values(), key=lambda x: x["percent_covered"]
            )

            coverage_analysis = {
                "summary": {
                    "overall_coverage": overall_coverage,
                    "total_lines": total_lines,
                    "covered_lines": covered_lines,
                    "total_modules": len(module_list),
                    "target_coverage": target_coverage,
                    "success": success,
                    "timestamp": time.time(),
                },
                "module_stats": module_list,
            }

            report_path = self.reports_dir / "coverage_analysis.json"
            with open(report_path, "w", encoding="utf-8") as f:
                json.dump(coverage_analysis, f, indent=2, ensure_ascii=False)

            status = "✅ 达标" if success else "❌ 需改进"
            self.console.print(
                f"[{'green' if success else 'red'}]📊 覆盖率: {overall_coverage:.1f}% {status} | 模块: {len(module_list)} | 报告: {report_path}[/{'green' if success else 'red'}]"
            )

            if self.verbose:
                for m in module_list:
                    self.console.print(
                        f"  {m['name']}: {m['percent_covered']:.1f}% ({m['files_count']} files)"
                    )

            return coverage_analysis

        except Exception as e:
            if self.verbose:
                self.console.print(f"[red]❌ 覆盖率分析异常: {e}[/red]")
            return None

    # ==================== 工具方法 ====================

    async def _save_e2e_report(self, test_result: E2ETestResult) -> str | None:
        """优化版本：读取E2E报告并创建精简的CI集成层"""
        import datetime

        report_path = self.reports_dir / "e2e_test_report.json"

        try:
            # 首先尝试读取E2E测试生成的原始报告
            if report_path.exists():
                with open(report_path, encoding="utf-8") as f:
                    e2e_report_data = json.load(f)

                if self.verbose:
                    self.console.print(
                        f"[dim]📄 已读取E2E测试原始报告: {report_path}[/dim]"
                    )

                # 创建精简的CI集成报告，避免重复详细错误信息
                ci_integration = {
                    # CI执行信息
                    "ci_execution": {
                        "timestamp": datetime.datetime.now().isoformat(),
                        "mode": "quick" if self.quick_mode else "full",
                        "duration": round(test_result.duration, 3),
                    },
                    # 测试摘要信息（从原始报告中提取关键数据）
                    "test_summary": {
                        "total_scenarios": e2e_report_data.get("e2e_summary", {}).get(
                            "total_scenarios", 0
                        ),
                        "passed": e2e_report_data.get("e2e_summary", {}).get(
                            "passed", 0
                        ),
                        "failed": e2e_report_data.get("e2e_summary", {}).get(
                            "failed", 0
                        ),
                        "skipped": e2e_report_data.get("e2e_summary", {}).get(
                            "skipped", 0
                        ),
                        "success_rate": e2e_report_data.get("e2e_summary", {}).get(
                            "success_rate", 0.0
                        ),
                        "test_mode": e2e_report_data.get("e2e_summary", {}).get(
                            "test_mode", "unknown"
                        ),
                    },
                    # CI状态信息
                    "ci_status": {
                        "success": test_result.success,
                        "status": getattr(test_result, "status", "UNKNOWN"),
                        "error_type": self._extract_error_type(test_result)
                        if not test_result.success
                        else None,
                        "execution_path": str(report_path),  # 指向原始报告的路径
                    },
                }

                # 保存精简的CI集成报告
                integration_report_path = self.reports_dir / "ci_e2e_integration.json"
                with open(integration_report_path, "w", encoding="utf-8") as f:
                    json.dump(ci_integration, f, indent=2, ensure_ascii=False)

                if self.verbose:
                    self.console.print(
                        f"[dim]📄 精简CI集成报告已保存: {integration_report_path}[/dim]"
                    )

                return str(report_path)  # 返回原始报告路径

            else:
                # 如果原始报告不存在，创建精简的基本报告
                if self.verbose:
                    self.console.print(
                        "[yellow]⚠️ 未找到E2E原始报告，创建精简基本报告[/yellow]"
                    )

                # 创建精简的基本报告结构
                report_data = {
                    "test_type": "e2e",
                    "timestamp": datetime.datetime.now().isoformat(),
                    "mode": "quick" if self.quick_mode else "full",
                    "success": test_result.success,
                    "status": getattr(test_result, "status", "UNKNOWN"),
                    "duration": round(test_result.duration, 3),
                    "error_type": self._extract_error_type(test_result)
                    if not test_result.success
                    else None,
                    "original_report_missing": True,
                }

                with open(report_path, "w", encoding="utf-8") as f:
                    json.dump(report_data, f, indent=2, ensure_ascii=False)

                if self.verbose:
                    self.console.print(
                        f"[dim]📄 E2E精简基本报告已创建: {report_path}[/dim]"
                    )

                return str(report_path)

        except Exception as e:
            self.console.print(f"[red]处理E2E测试报告失败: {e}[/red]")
            return None

    def _extract_error_type(self, test_result: E2ETestResult) -> str | None:
        """提取错误类型，避免重复长错误信息"""
        error = getattr(test_result, "error", None) or getattr(
            test_result, "output", ""
        )

        if not error:
            return None

        # 提取关键错误类型，避免重复完整的堆栈跟踪
        error_lower = error.lower()
        if "connectionerror" in error_lower or "connect error" in error_lower:
            return "CONNECTION_ERROR"
        elif "timeout" in error_lower:
            return "TIMEOUT_ERROR"
        elif "key" in error_lower and "api" in error_lower:
            return "API_KEY_ERROR"
        elif "assertion" in error_lower:
            return "ASSERTION_ERROR"
        elif "validation" in error_lower:
            return "VALIDATION_ERROR"
        else:
            return "UNKNOWN_ERROR"

    async def _save_ci_summary_report(self, summary: dict[str, Any]) -> str | None:
        """保存CI执行摘要报告"""
        report_path = self.reports_dir / "ci_execution_summary.json"

        try:
            with open(report_path, "w", encoding="utf-8") as f:
                json.dump(summary, f, indent=2, ensure_ascii=False)

            self.console.print(f"[green]📄 CI执行摘要已保存: {report_path}[/green]")
            return str(report_path)

        except Exception as e:
            self.console.print(f"[red]保存CI摘要失败: {e}[/red]")
            return None

    def _generate_execution_summary(self, total_time: float) -> dict[str, Any]:
        """生成执行摘要 - 仅包含关键摘要信息"""
        # 统计各类任务结果
        static_results = [r for r in self.results if r.task_type == "static_analysis"]
        test_results = [r for r in self.results if r.task_type == "test"]

        static_success = sum(1 for r in static_results if r.success)
        static_total = len(static_results)

        test_success = sum(1 for r in test_results if r.success)
        test_total = len(test_results)

        # 提取通过率用于展示（从各测试任务的execution_details获取）
        # None 表示未获取到数据(任务崩溃或未执行), 展示层显示 N/A 而非误导性的 100%
        unit_pass_rate: float | None = None
        integration_pass_rate: float | None = None

        for r in test_results:
            stats = r.execution_details or {}
            total = stats.get("tests_run", 0)
            passed_count = stats.get("tests_passed", 0)
            rate = (passed_count / total) * 100 if total > 0 else 0
            if "单元" in r.task_name:
                unit_pass_rate = rate
            elif "集成" in r.task_name:
                integration_pass_rate = rate

        # 配置治理检查是CI硬门禁, 失败则CI不通过
        config_governance_results = [
            r for r in static_results if "配置治理" in r.task_name
        ]
        config_gate_passed = (
            all(r.success for r in config_governance_results)
            if config_governance_results
            else True
        )

        # CI门禁分层:
        # - quick(核心模式): 测试 + 全部静态分析(core精选规则, 正常代码全绿) -> 阻断性门禁
        # - full(完整模式): 非CI流程, 无门禁, 仅出报告供人工审阅 -> 不阻断(exit 0)
        if self.quick_mode:
            ci_passed = (
                bool(test_results)
                and all(r.success for r in test_results)
                and all(r.success for r in static_results)
            )
        else:
            ci_passed = True

        # 生成简化的摘要 - 不包含详细的完整信息
        summary = {
            "execution_mode": "codex"
            if self.codex_mode
            else ("quick" if self.quick_mode else "full"),
            "timestamp": time.time(),
            "total_execution_time": total_time,
            "ci_passed": ci_passed,
            "ci_pass_criteria": {
                "unit_test_pass_rate": unit_pass_rate,
                "integration_test_pass_rate": integration_pass_rate,
                "unit_test_required": 100.0,
                "integration_test_required": 100.0,
            },
            "tasks_summary": {
                "total_tasks": len(self.results),
                "successful_tasks": sum(1 for r in self.results if r.success),
                "failed_tasks": sum(1 for r in self.results if not r.success),
            },
            "static_analysis_summary": {
                "total_tools": static_total,
                "successful_tools": static_success,
                "tool_results": [
                    {
                        "name": r.task_name,
                        "success": r.success,
                        "duration": r.duration,
                        "output_summary": r.output[:100] + "..."
                        if len(r.output) > 100
                        else r.output,
                        "report_path": r.report_path,
                    }
                    for r in static_results
                ],
            },
            "tests_summary": {
                "total_test_suites": test_total,
                "successful_test_suites": test_success,
                "test_results": [
                    {
                        "name": r.task_name,
                        "success": r.success,
                        "duration": r.duration,
                        "output_summary": r.output[:100] + "..."
                        if len(r.output) > 100
                        else r.output,
                        "report_path": r.report_path,
                    }
                    for r in test_results
                ],
            },
        }

        return summary

    async def display_summary(self, summary: dict[str, Any]) -> None:
        """显示执行摘要"""
        self.console.print("\n[bold]📊 CI执行摘要[/bold]\n")

        # 显示CI通过状态 (full模式非CI流程, 不显示门禁判定避免误导)
        if summary.get("execution_mode") == "full":
            self.console.print("[cyan]📊 完整分析报告 (非CI门禁, 供人工审阅)[/cyan]")
        else:
            ci_status = "✅ 通过" if summary["ci_passed"] else "❌ 失败"
            status_color = "green" if summary["ci_passed"] else "red"
            self.console.print(f"[{status_color}]CI状态: {ci_status}[/{status_color}]")

        # 显示通过标准
        criteria = summary["ci_pass_criteria"]
        unit_rate = criteria["unit_test_pass_rate"]
        integration_rate = criteria["integration_test_pass_rate"]
        unit_str = f"{unit_rate:.1f}%" if unit_rate is not None else "N/A(未执行)"
        integration_str = (
            f"{integration_rate:.1f}%"
            if integration_rate is not None
            else "N/A(未执行)"
        )
        self.console.print(
            f"🧪 单元测试通过率: {unit_str} (要求: {criteria['unit_test_required']}%)"
        )
        self.console.print(
            f"🔗 集成测试通过率: {integration_str} (要求: {criteria['integration_test_required']}%)"
        )
        self.console.print("[dim]ℹ️  静态分析仅提示, 配置治理检查为硬门禁[/dim]")

        # 显示任务统计
        tasks_summary = summary["tasks_summary"]
        self.console.print(
            f"\n📋 任务统计: {tasks_summary['successful_tasks']}/{tasks_summary['total_tasks']} 成功"
        )
        self.console.print(f"⏱️  总执行时间: {summary['total_execution_time']:.1f}秒")

        # 显示详细结果表格
        table = Table(show_header=True, header_style="bold magenta")
        table.add_column("任务类型", style="cyan", width=15)
        table.add_column("任务名称", style="blue", width=15)
        table.add_column("状态", width=8)
        table.add_column("耗时", style="yellow", width=10)
        table.add_column("输出摘要", width=40)

        # 静态分析结果
        for tool in summary["static_analysis_summary"]["tool_results"]:
            status = "✅" if tool["success"] else "❌"
            table.add_row(
                "静态分析",
                tool["name"],
                status,
                f"{tool['duration']:.1f}s",
                tool["output_summary"],
            )

        # 测试结果
        for test_suite in summary["tests_summary"]["test_results"]:
            status = "✅" if test_suite["success"] else "❌"
            table.add_row(
                "测试",
                test_suite["name"],
                status,
                f"{test_suite['duration']:.1f}s",
                test_suite["output_summary"],
            )

        self.console.print("\n", table)

        # 显示报告路径
        self.console.print(f"\n📁 详细报告目录: {self.reports_dir}")
        self.console.print("📄 主要报告文件:")
        self.console.print("  • CI摘要: reports/ci_execution_summary.json")

        for tool in summary["static_analysis_summary"]["tool_results"]:
            if tool["report_path"]:
                self.console.print(f"  • {tool['name']}: {tool['report_path']}")

        for test_suite in summary["tests_summary"]["test_results"]:
            if test_suite["report_path"]:
                # 特殊处理E2E测试报告显示
                if "E2E" in test_suite["name"]:
                    self.console.print(
                        f"  • {test_suite['name']}: {test_suite['report_path']}"
                    )
                    # 检查并显示优化后的集成报告
                    ci_integration_report = self.reports_dir / "ci_e2e_integration.json"
                    if ci_integration_report.exists():
                        self.console.print(
                            f"    📊 CI集成报告(精简): {ci_integration_report}"
                        )

                    # 向后兼容：检查旧版本集成报告
                    legacy_integration_report = (
                        self.reports_dir / "ci_e2e_integration_report.json"
                    )
                    if legacy_integration_report.exists():
                        self.console.print(
                            f"    📊 CI集成报告(旧版): {legacy_integration_report}"
                        )
                else:
                    self.console.print(
                        f"  • {test_suite['name']}: {test_suite['report_path']}"
                    )

        if self.codex_mode:
            return

        # CI后构建清理：使用SimpleCleaner（清理htmlcov和过期文件）
        try:
            self.console.print("[cyan]🧹 执行后构建清理...[/cyan]")

            # 使用简化清理工具
            cleaner = SimpleCleaner()

            # 并行执行清理任务
            htmlcov_result, archives_result, test_logs_result = await asyncio.gather(
                asyncio.get_event_loop().run_in_executor(None, cleaner.cleanup_htmlcov),
                asyncio.get_event_loop().run_in_executor(
                    None, cleaner.cleanup_old_archives
                ),
                asyncio.get_event_loop().run_in_executor(
                    None, cleaner.cleanup_test_logs
                ),
                return_exceptions=True,
            )

            # 显示详细的清理结果
            files_cleaned = 0
            size_freed = 0

            # htmlcov清理结果
            if isinstance(htmlcov_result, Exception):
                self.console.print(
                    f"[yellow]⚠️ htmlcov清理异常: {htmlcov_result}[/yellow]"
                )
            elif htmlcov_result.get("cleaned"):
                files_cleaned += 1
                size_freed += htmlcov_result.get("size_freed", 0)
                size_mb = htmlcov_result.get("size_freed", 0) / (1024 * 1024)
                self.console.print(
                    f"[green]✅ htmlcov目录已清理，释放 {size_mb:.1f} MB[/green]"
                )
            else:
                self.console.print("[dim]✓ htmlcov目录无需清理[/dim]")

            # 过期文件清理结果
            if isinstance(archives_result, Exception):
                self.console.print(
                    f"[yellow]⚠️ 过期文件清理异常: {archives_result}[/yellow]"
                )
            elif archives_result.get("cleaned_count", 0) > 0:
                count = archives_result["cleaned_count"]
                size = archives_result.get("cleaned_size", 0)
                files_cleaned += count
                size_freed += size
                size_mb = size / (1024 * 1024)
                self.console.print(
                    f"[green]✅ 清理了 {count} 个过期文件，释放 {size_mb:.1f} MB[/green]"
                )
            else:
                self.console.print("[dim]✓ 没有过期文件需要清理[/dim]")

            # 测试日志清理结果 (test_*.log, 含 .error.log)
            if isinstance(test_logs_result, Exception):
                self.console.print(
                    f"[yellow]⚠️ 测试日志清理异常: {test_logs_result}[/yellow]"
                )
            elif test_logs_result.get("cleaned_count", 0) > 0:
                count = test_logs_result["cleaned_count"]
                size = test_logs_result.get("cleaned_size", 0)
                files_cleaned += count
                size_freed += size
                self.console.print(
                    f"[green]✅ 清理了 {count} 个过期测试日志[/green]"
                )
            else:
                self.console.print("[dim]✓ 没有过期测试日志需要清理[/dim]")

            # 总计清理结果
            if files_cleaned > 0:
                total_mb = size_freed / (1024 * 1024)
                self.console.print(
                    f"[green]🎉 后构建清理完成：共清理 {files_cleaned} 项，释放 {total_mb:.1f} MB[/green]"
                )
            else:
                self.console.print(
                    "[green]✅ 后构建清理完成，没有需要清理的文件[/green]"
                )

        except Exception as e:
            self.console.print(f"[yellow]⚠️ 后构建清理失败: {e}[/yellow]")


def _load_test_env() -> None:
    """加载测试环境变量配置

    CI脚本专用：显式加载.env.test，确保测试环境变量正确设置
    不影响手动测试使用.env文件
    """
    env_test_path = project_root / ".env.test"

    if env_test_path.exists():
        # 使用override=True确保测试环境变量覆盖现有值
        loaded = load_dotenv(env_test_path, override=True)

        if loaded:
            logger.info(f"✅ 已加载测试环境配置: {env_test_path}")

            # 验证关键环境变量
            env = os.environ.get("ENVIRONMENT")
            testing = os.environ.get("TESTING")

            if env == "testing":
                logger.info(f"   ✓ ENVIRONMENT={env}")
            else:
                logger.warning(f"   ⚠️ ENVIRONMENT={env} (期望: testing)")

            if testing == "true":
                logger.info(f"   ✓ TESTING={testing}")
            else:
                logger.warning(f"   ⚠️ TESTING={testing} (期望: true)")
        else:
            logger.warning(f"⚠️ .env.test文件存在但加载失败: {env_test_path}")
    else:
        logger.warning(f"⚠️ .env.test文件不存在: {env_test_path}")
        logger.warning("   CI测试可能使用错误的环境变量")


def main() -> None:
    """主函数"""
    # 步骤1: 加载测试环境变量（必须在所有其他操作之前）
    _load_test_env()

    parser = argparse.ArgumentParser(description="CI/CD测试套件执行器")
    parser.add_argument(
        "--quick", action="store_true", help="快速模式（单元测试+静态分析）"
    )
    parser.add_argument(
        "--codex",
        action="store_true",
        help="启用Codex sandbox兼容模式(顺序核心检查,禁用xdist)",
    )
    parser.add_argument("--verbose", action="store_true", help="详细输出")

    args = parser.parse_args()

    try:
        # 创建并行执行器
        runner = CIParallelRunner(
            quick_mode=args.quick,
            verbose=args.verbose,
            codex_mode=args.codex,
        )

        # 并行执行所有任务
        summary = asyncio.run(runner.run_parallel_tasks())

        # 显示摘要
        asyncio.run(runner.display_summary(summary))

        # 根据CI通过标准设置退出码
        exit_code = 0 if summary["ci_passed"] else 1

        if args.verbose:
            console.print(f"\n[dim]CI执行完成,退出码: {exit_code}[/dim]")

        sys.exit(exit_code)

    except KeyboardInterrupt:
        console.print("\n[yellow]⏹️  CI执行被用户中断[/yellow]")
        sys.exit(130)
    except Exception as e:
        console.print(f"\n[red]❌ CI执行出错: {e}[/red]")
        if args.verbose:
            import traceback

            console.print(f"[red]{traceback.format_exc()}[/red]")
        sys.exit(1)


if __name__ == "__main__":
    main()
