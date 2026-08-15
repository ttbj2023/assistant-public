"""run_test_suite CI 执行器关键行为单元测试.

覆盖:
- _extract_error_type 错误分类 (回归: pytest-timeout header 不得误判 TIMEOUT_ERROR)
- quick 模式 E2E 不再跳过 (纳入阻断门禁)
- 集成测试加固: 完整输出落盘 + xdist worker 崩溃 (exit 3) 自动重试一次
"""

from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

import pytest

from scripts.run_test_suite import CIParallelRunner, E2ETestResult

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def runner() -> CIParallelRunner:
    return CIParallelRunner(quick_mode=True)


# pytest 实际输出片段: pytest-timeout 插件 header 固定含 "timeout" 字样
PYTEST_STDOUT_WITH_TIMEOUT_HEADER = """\
============================= test session starts ==============================
platform linux -- Python 3.13.12, pytest-9.0.3, pluggy-1.6.0
plugins: cov-7.1.0, asyncio-1.3.0, xdist-3.8.0, timeout-2.4.0
timeout: 30.0s
timeout method: signal
timeout func_only: True
collected 13 items

tests/e2e/test_x.py::test_a PASSED
tests/e2e/test_x.py::test_b FAILED

=========================== short test summary info ============================
FAILED tests/e2e/test_x.py::test_b - TypeError: unexpected keyword argument
========================= 1 failed, 12 passed in 5.27s =========================
"""


class TestExtractErrorType:
    """_extract_error_type 分类准确性."""

    def test_extract_error_type_explicit_timeout_flag_returns_timeout_error(
        self, runner: CIParallelRunner
    ) -> None:
        """子进程真实超时 (test_details.timeout=True) → TIMEOUT_ERROR."""
        result = E2ETestResult(
            False,
            "E2E测试执行超时",
            600,
            "Execution timeout after 10 minutes",
            {"timeout": True},
        )
        assert runner._extract_error_type(result) == "TIMEOUT_ERROR"

    def test_extract_error_type_pytest_header_timeout_returns_test_failure(
        self, runner: CIParallelRunner
    ) -> None:
        """回归: pytest-timeout header 含 'timeout' 字样不得误判 TIMEOUT_ERROR."""
        result = E2ETestResult(
            False, PYTEST_STDOUT_WITH_TIMEOUT_HEADER, 7.8, None, {"exit_code": 1}
        )
        assert runner._extract_error_type(result) == "TEST_FAILURE"

    def test_extract_error_type_connection_error_returns_connection_error(
        self, runner: CIParallelRunner
    ) -> None:
        """stderr 连接错误 → CONNECTION_ERROR (优先于 stdout 的 error 泛化命中)."""
        result = E2ETestResult(False, "", 1.0, "httpx.ConnectError: Connection refused")
        assert runner._extract_error_type(result) == "CONNECTION_ERROR"


class TestQuickModeE2E:
    """quick 模式 E2E 纳入阻断门禁."""

    async def test_run_e2e_tests_quick_mode_executes_instead_of_skipping(
        self, runner: CIParallelRunner, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """quick 模式不再提前返回跳过, 而是真正执行 pytest 子流程."""
        called = []

        def fake_direct() -> E2ETestResult:
            called.append(True)
            return E2ETestResult(True, "13 passed", 8.0, None, {})

        monkeypatch.setattr(runner, "_run_e2e_direct", fake_direct)
        monkeypatch.setattr(runner, "_save_e2e_report", AsyncMock(return_value=None))

        result = await runner.run_e2e_tests()

        assert called, "quick 模式应执行 _run_e2e_direct"
        assert result.success is True
        assert result.execution_details
        assert result.execution_details.get("skipped") is not True


@pytest.fixture
def integration_runner(
    runner: CIParallelRunner, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> CIParallelRunner:
    """隔离 reports 目录的集成测试 runner."""
    monkeypatch.setattr(runner, "reports_dir", tmp_path)
    return runner


def _fake_pytest_process(
    returncode: int, stdout: str
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        args=[], returncode=returncode, stdout=stdout, stderr=""
    )


class TestIntegrationHardening:
    """集成测试加固: 完整日志落盘 + xdist worker 崩溃重试.

    背景: quick 门禁曾出现 exit_code=3 (xdist worker 崩溃) 导致 7 用例丢失,
    但集成测试未落盘原始输出, 事后无法定位.
    """

    async def test_run_integration_saves_full_pytest_log(
        self,
        integration_runner: CIParallelRunner,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """完整 pytest 输出落盘 integration_pytest_full.log (仿 unit/e2e 已有机制)."""
        monkeypatch.setattr(
            "scripts.run_test_suite.subprocess.run",
            lambda *a, **k: _fake_pytest_process(0, "103 passed in 5.94s"),
        )
        monkeypatch.setattr(
            integration_runner, "_parse_pytest_summary", lambda s: (103, 0, 0)
        )

        result = await integration_runner.run_integration_tests()

        assert result.success is True
        log = tmp_path / "current" / "integration_pytest_full.log"
        assert log.exists()
        assert "103 passed" in log.read_text(encoding="utf-8")

    async def test_run_integration_retries_once_on_xdist_worker_crash(
        self,
        integration_runner: CIParallelRunner,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """exit_code=3 (worker 崩溃, 基础设施故障) 保留崩溃现场后自动重试一次."""
        attempts: list[int] = []

        def fake_run(*a: object, **k: object) -> subprocess.CompletedProcess[str]:
            attempts.append(len(attempts))
            if len(attempts) == 1:
                return _fake_pytest_process(3, "worker 'gw0' crashed")
            return _fake_pytest_process(0, "103 passed")

        monkeypatch.setattr("scripts.run_test_suite.subprocess.run", fake_run)
        monkeypatch.setattr(
            integration_runner, "_parse_pytest_summary", lambda s: (103, 0, 0)
        )

        result = await integration_runner.run_integration_tests()

        assert len(attempts) == 2, "worker 崩溃应重试一次"
        assert result.success is True
        # 崩溃现场保留, 不被重试覆盖
        crash_log = tmp_path / "current" / "integration_pytest_crash.log"
        assert crash_log.exists()
        assert "crashed" in crash_log.read_text(encoding="utf-8")

    async def test_run_integration_no_retry_on_real_test_failure(
        self, integration_runner: CIParallelRunner, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """exit_code=1 (真实测试失败) 不重试, 避免掩盖问题与浪费时间."""
        attempts: list[int] = []

        def fake_run(*a: object, **k: object) -> subprocess.CompletedProcess[str]:
            attempts.append(len(attempts))
            return _fake_pytest_process(1, "1 failed, 102 passed")

        monkeypatch.setattr("scripts.run_test_suite.subprocess.run", fake_run)
        monkeypatch.setattr(
            integration_runner, "_parse_pytest_summary", lambda s: (102, 1, 0)
        )

        result = await integration_runner.run_integration_tests()

        assert len(attempts) == 1, "真实测试失败不得重试"
        assert result.success is False

    async def test_run_integration_retry_exhausted_still_fails(
        self, integration_runner: CIParallelRunner, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """重试后仍 exit_code=3 (确定性崩溃) 则判定失败, CI 仍阻断."""
        attempts: list[int] = []

        def fake_run(*a: object, **k: object) -> subprocess.CompletedProcess[str]:
            attempts.append(len(attempts))
            return _fake_pytest_process(3, "worker crashed again")

        monkeypatch.setattr("scripts.run_test_suite.subprocess.run", fake_run)
        monkeypatch.setattr(
            integration_runner, "_parse_pytest_summary", lambda s: (0, 0, 0)
        )

        result = await integration_runner.run_integration_tests()

        assert len(attempts) == 2, "最多重试一次"
        assert result.success is False
