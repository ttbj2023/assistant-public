"""logger_setup 日志配置的单元测试.

覆盖错误日志分离能力: build_log_config 应产出独立的 error 文件 handler
(level=WARNING), 且 configure_logging 后 WARNING+ 消息进入 error.log, 普通
INFO 不进入.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from src.core.logger_setup import build_log_config, configure_logging


class TestBuildLogConfig:
    """build_log_config 返回结构验证 (纯单元, 无副作用)."""

    def test_error_file_handler_exists_with_warning_level(self, tmp_path: Path) -> None:
        """build_log_config 应包含 file_error handler, level=WARNING."""
        main_log = tmp_path / "server_8000.log"
        config = build_log_config(
            log_level="info",
            log_file=main_log,
            max_bytes=1024,
            backup_count=3,
        )

        handlers = config["handlers"]
        assert "file_error" in handlers, "缺少 file_error handler"
        error_handler = handlers["file_error"]
        assert error_handler["class"] == "logging.handlers.RotatingFileHandler"
        assert error_handler["level"] == "WARNING"

    def test_error_handler_filename_derived_from_main(self, tmp_path: Path) -> None:
        """error 日志文件名应派生自主日志名 (插入 .error)."""
        main_log = tmp_path / "server_8000.log"
        config = build_log_config("info", main_log, 1024, 3)

        error_filename = config["handlers"]["file_error"]["filename"]
        assert error_filename != str(main_log), "error 文件名不能等于主日志"
        assert "error" in Path(error_filename).stem, "error 文件名应含 error 标识"
        assert Path(error_filename).parent == tmp_path

    def test_error_handler_attached_to_root(self, tmp_path: Path) -> None:
        """root logger 应挂载 file_error handler."""
        main_log = tmp_path / "server_8000.log"
        config = build_log_config("info", main_log, 1024, 3)
        assert "file_error" in config["root"]["handlers"]


class TestErrorLogIntegration:
    """configure_logging 后 error.log 实际分离 WARNING+ 消息."""

    @pytest.fixture
    def isolated_logging(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> Iterator[Path]:
        """隔离: 日志重定向到 tmp, 测试后恢复全局 logging 配置."""
        from src.core import logger_setup

        def fake_ensure(port: int = 0) -> Path:
            if port:
                return tmp_path / f"server_{port}.log"
            return tmp_path / f"test_{os.getpid()}.log"

        monkeypatch.setattr(logger_setup, "ensure_log_dir", fake_ensure)

        root = logging.getLogger()
        old_handlers = root.handlers[:]
        old_level = root.level

        configure_logging("info", port=8000)
        yield tmp_path

        for h in root.handlers[:]:
            root.removeHandler(h)
            h.close()
        for h in old_handlers:
            root.addHandler(h)
        root.setLevel(old_level)

    def test_warning_goes_to_error_log(self, isolated_logging: Path) -> None:
        """WARNING 消息应写入 error.log."""
        logger = logging.getLogger("test.error_separation")
        logger.warning("一条警告消息")

        for h in logging.getLogger().handlers:
            h.flush()

        error_log = isolated_logging / "server_8000.error.log"
        assert error_log.exists(), "error.log 未生成"
        assert "一条警告消息" in error_log.read_text(encoding="utf-8")

    def test_info_not_in_error_log(self, isolated_logging: Path) -> None:
        """INFO 消息不应写入 error.log."""
        logger = logging.getLogger("test.error_separation")
        logger.info("普通信息消息")

        for h in logging.getLogger().handlers:
            h.flush()

        error_log = isolated_logging / "server_8000.error.log"
        if error_log.exists():
            assert "普通信息消息" not in error_log.read_text(encoding="utf-8")

    def test_warning_also_in_main_log(self, isolated_logging: Path) -> None:
        """WARNING 消息也应写入主日志 (全级别)."""
        logger = logging.getLogger("test.error_separation")
        logger.warning("双写警告")

        for h in logging.getLogger().handlers:
            h.flush()

        main_log = isolated_logging / "server_8000.log"
        assert main_log.exists()
        assert "双写警告" in main_log.read_text(encoding="utf-8")
