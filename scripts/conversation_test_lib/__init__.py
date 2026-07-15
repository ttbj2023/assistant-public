"""对话测试脚本模块化实现.

原 monolithic 脚本 scripts/conversation_test.py 的核心逻辑被拆分到本包,
入口文件仅保留薄 shim 以保证 CLI 兼容.
"""

from __future__ import annotations

from scripts.conversation_test_lib.client import check_server, run_conversations
from scripts.conversation_test_lib.collectors import (
    collect_db_data,
    collect_prompt_logs,
    collect_server_logs,
    collect_tool_call_logs,
    collect_usage_stats,
)
from scripts.conversation_test_lib.config import (
    ConversationTestConfig,
    build_config,
    parse_args,
)
from scripts.conversation_test_lib.data import get_conversations
from scripts.conversation_test_lib.report import generate_report

__all__ = [
    "ConversationTestConfig",
    "build_config",
    "check_server",
    "collect_db_data",
    "collect_prompt_logs",
    "collect_server_logs",
    "collect_tool_call_logs",
    "collect_usage_stats",
    "generate_report",
    "get_conversations",
    "parse_args",
    "run_conversations",
]
