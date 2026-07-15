"""对话测试配置与 CLI 参数解析."""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from scripts.conversation_test_lib.data import CONVERSATIONS_FULL, CONVERSATIONS_QUICK

DEFAULT_API_BASE = "http://localhost:8011"
DEFAULT_API_KEY = "sk-project-jack-main-789xyz012uvw345"
DEFAULT_TIMEOUT = 300
DEFAULT_USER_ID = "jack"
DEFAULT_THREAD_ID = "main"
DEFAULT_AGENT_ID = "personal-assistant"
DEFAULT_LOGS_DIR = Path("logs")


@dataclass
class ConversationTestConfig:
    """对话测试运行时配置.

    将原脚本中的全局变量收敛为显式对象, 便于测试注入与多 Agent 切换.
    """

    api_base: str = DEFAULT_API_BASE
    api_key: str = DEFAULT_API_KEY
    model: str = DEFAULT_AGENT_ID
    timeout: int = DEFAULT_TIMEOUT
    user_id: str = DEFAULT_USER_ID
    thread_id: str = DEFAULT_THREAD_ID
    agent_id: str = DEFAULT_AGENT_ID
    data_dir: Path = field(default_factory=Path)
    logs_dir: Path = field(default_factory=lambda: Path(DEFAULT_LOGS_DIR))
    conversations: list[dict[str, Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        """补全 data_dir 默认值, 仅在构造时未传入时使用."""
        if not self.data_dir or str(self.data_dir) == ".":
            self.data_dir = Path(
                f"data/{self.user_id}/{self.thread_id}/{self.agent_id}"
            )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """解析命令行参数, 与原脚本保持兼容."""
    parser = argparse.ArgumentParser(
        description=(
            "Personal Assistant 对话测试脚本 (默认精简版, --all 切换到完整版)"
        ),
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="使用完整版对话 (默认精简版)",
    )
    parser.add_argument(
        "--agent",
        default=DEFAULT_AGENT_ID,
        metavar="ID",
        help="目标 Agent ID (默认 personal-assistant)",
    )
    parser.add_argument(
        "--start-round",
        type=int,
        default=1,
        metavar="N",
        help="从第 N 轮开始执行 (默认 1)",
    )
    parser.add_argument(
        "--max-rounds",
        type=int,
        default=0,
        metavar="N",
        help="最多执行 N 轮 (0=不限)",
    )
    parser.add_argument(
        "--server-log",
        type=Path,
        action="append",
        default=[],
        metavar="PATH",
        help="额外扫描的服务日志路径 (可多次指定)",
    )
    return parser.parse_args(argv)


def build_config(args: argparse.Namespace) -> ConversationTestConfig:
    """根据 CLI 参数构建运行时配置."""
    agent_id = args.agent
    conversations = CONVERSATIONS_FULL if args.all else CONVERSATIONS_QUICK
    return ConversationTestConfig(
        model=agent_id,
        agent_id=agent_id,
        conversations=conversations,
    )
