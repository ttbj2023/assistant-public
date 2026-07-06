"""主入口模块单元测试."""

from __future__ import annotations

import logging
import sys

from src.main import setup_logging


class TestSetupLogging:
    """setup_logging函数测试."""

    def test_setup_logging_should_configure_basic_logging(self):
        """测试setup_logging应该配置基本日志系统."""
        # 清除之前的logger配置
        root_logger = logging.getLogger()
        for handler in root_logger.handlers[:]:
            root_logger.removeHandler(handler)

        # 执行setup_logging
        setup_logging()

        # 验证日志系统配置
        root_logger = logging.getLogger()
        assert len(root_logger.handlers) > 0
        assert isinstance(root_logger.handlers[0], logging.StreamHandler)
        assert root_logger.level == logging.INFO

    def test_setup_logging_should_use_stdout_handler(self):
        """测试setup_logging应该使用stdout作为输出."""
        # 清除之前的logger配置
        root_logger = logging.getLogger()
        for handler in root_logger.handlers[:]:
            root_logger.removeHandler(handler)

        setup_logging()

        # 验证handler输出到stdout
        root_logger = logging.getLogger()
        handler = root_logger.handlers[0]
        assert handler.stream == sys.stdout
