"""SimpleCleaner.cleanup_test_logs 的单元测试.

覆盖开发环境 test_{pid}.log 堆积治理: 按 mtime 清理过期测试日志, 保留
server_*.log / *.pid / prompts 等运行时产物.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from scripts.simple_clean import SimpleCleaner


@pytest.fixture
def cleaner_in_tmp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleCleaner:
    """以 tmp_path 为 project_root 构造 SimpleCleaner (隔离真实项目)."""
    monkeypatch.setattr("scripts.simple_clean.project_root", tmp_path)
    return SimpleCleaner()


def _make_old_file(path: Path, age_days: float, content: str = "x") -> None:
    """创建文件并回溯 mtime 到 age_days 天前."""
    path.write_text(content, encoding="utf-8")
    old_time = time.time() - age_days * 86400
    os.utime(path, (old_time, old_time))


class TestCleanupTestLogs:
    """cleanup_test_logs 行为."""

    def test_deletes_old_test_logs_keeps_recent(
        self, cleaner_in_tmp: SimpleCleaner, tmp_path: Path
    ) -> None:
        """过期的 test_*.log 应删除, 近期保留."""
        logs = tmp_path / "logs"
        logs.mkdir()
        old_log = logs / "test_111.log"
        _make_old_file(old_log, age_days=5)
        new_log = logs / "test_222.log"
        new_log.write_text("recent", encoding="utf-8")

        result = cleaner_in_tmp.cleanup_test_logs(days=3)

        assert not old_log.exists(), "过期 test log 应被删除"
        assert new_log.exists(), "近期 test log 应保留"
        assert result["cleaned_count"] == 1

    def test_keeps_server_logs_and_pids(
        self, cleaner_in_tmp: SimpleCleaner, tmp_path: Path
    ) -> None:
        """server_*.log 和 *.pid 即使过期也不删."""
        logs = tmp_path / "logs"
        logs.mkdir()
        server_log = logs / "server_8000.log"
        _make_old_file(server_log, age_days=10)
        pid_file = logs / ".dev_server_8000.pid"
        _make_old_file(pid_file, age_days=10)

        result = cleaner_in_tmp.cleanup_test_logs(days=3)

        assert server_log.exists(), "server log 不应被清理"
        assert pid_file.exists(), "pid 文件不应被清理"
        assert result["cleaned_count"] == 0

    def test_deletes_old_test_error_logs(
        self, cleaner_in_tmp: SimpleCleaner, tmp_path: Path
    ) -> None:
        """过期的 test_*.error.log 也应清理."""
        logs = tmp_path / "logs"
        logs.mkdir()
        old_err = logs / "test_111.error.log"
        _make_old_file(old_err, age_days=5)

        result = cleaner_in_tmp.cleanup_test_logs(days=3)

        assert not old_err.exists()
        assert result["cleaned_count"] == 1

    def test_zero_days_skips_cleanup(
        self, cleaner_in_tmp: SimpleCleaner, tmp_path: Path
    ) -> None:
        """days=0 应跳过清理."""
        logs = tmp_path / "logs"
        logs.mkdir()
        old_log = logs / "test_111.log"
        _make_old_file(old_log, age_days=5)

        result = cleaner_in_tmp.cleanup_test_logs(days=0)

        assert old_log.exists()
        assert result["cleaned_count"] == 0

    def test_missing_logs_dir_safe(
        self, cleaner_in_tmp: SimpleCleaner, tmp_path: Path
    ) -> None:
        """logs 目录不存在时应安全返回."""
        result = cleaner_in_tmp.cleanup_test_logs(days=3)

        assert result["cleaned_count"] == 0
