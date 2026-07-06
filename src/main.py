"""主入口."""

from __future__ import annotations

import logging
from pathlib import Path

from dotenv import load_dotenv

# 加载.env文件
project_root = Path(__file__).parent.parent
load_dotenv(project_root / ".env")

logger = logging.getLogger(__name__)


def setup_logging() -> None:
    """配置日志."""
    from src.core.logger_setup import configure_logging

    configure_logging("info")


def main() -> None:
    """主函数."""
    setup_logging()
    logger.info("🚀 启动Personal Agent Assistant系统...")
    logger.info("🎯 简化架构已启动")
    logger.info("✅ 双层记忆 + LangChain v1.0 架构就绪")


if __name__ == "__main__":
    main()
