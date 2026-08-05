"""run_test_suite CI 执行器关键行为单元测试.

覆盖:
- _extract_error_type 错误分类 (回归: pytest-timeout header 不得误判 TIMEOUT_ERROR)
- quick 模式 E2E 不再跳过 (纳入阻断门禁)
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from scripts.run_test_suite import CIParallelRunner, E2ETestResult


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
