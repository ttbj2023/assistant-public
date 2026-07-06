"""OpenClaw inbound 全链 E2E 测试.

独特价值 (集成/单元测试无法覆盖):
- OpenClaw 中间件上下文传播 (request.state.openclaw_context) 是 FastAPI 请求生命周期
  concerns, 集成层需重放中间件; 单元测试 mock 函数级无法验证
- stream_openclaw_response 的拆分+补发触发链路是 HTTP 驱动, 仅 E2E 可验证

拆为两个聚焦测试降低时序脆弱性:
- G1: inbound 解析 + 渠道自动配置 (短响应, 不触发拆分)
- G2: 超长响应拆分 + 补发 spawn (patch send_openclaw_followup 验证协作)
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from langchain_core.messages import AIMessage

from tests.e2e.mock_llm import E2EMockLLM

_OPENCLAW_MARKER = "You are a personal assistant running inside OpenClaw."


def _openclaw_request(
    user_content: str,
    thread_id: str,
    *,
    account_id: str = "bot001",
    channel: str = "openclaw-weixin",
    chat_id: str = "wx_user_abc",
    stream: bool = True,
) -> dict:
    """构造 OpenClaw Gateway 注入风格的请求体.

    system 消息含 OpenClaw 特征标记 + Inbound Context (trusted metadata) JSON 块,
    中间件据此解析 account_id/channel/chat_id 并设置 request.state.openclaw_context.
    """
    inbound_block = (
        "## Inbound Context (trusted metadata)\n"
        f'```json\n{{"account_id": "{account_id}", '
        f'"channel": "{channel}", "chat_id": "{chat_id}"}}\n```'
    )
    system_content = f"{_OPENCLAW_MARKER}\n\n{inbound_block}"
    return {
        "model": "personal-assistant",
        "stream": stream,
        "messages": [
            {"role": "system", "content": system_content},
            {"role": "user", "content": user_content},
        ],
        "user": thread_id,
    }


def _read_channel_config(user_id: str, thread_id: str, agent_id: str) -> list[dict]:
    """直接读 channel_config.db 的 user_channel_configs 表."""
    from src.core.path_resolver import get_user_path_resolver

    resolver = get_user_path_resolver()
    db_path = Path(
        resolver.get_database_path(
            user_id, thread_id, "channel_config", agent_id=agent_id
        )
    )
    if not db_path.exists():
        return []
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute("SELECT * FROM user_channel_configs").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@pytest.mark.e2e
class TestOpenClawInboundE2E:
    """OpenClaw inbound 全链 E2E 测试."""

    async def test_e2e_inbound_context_provisions_channel_config(
        self,
        e2e_client,
        e2e_test_thread_id,
        e2e_api_key,
        e2e_test_user,
    ):
        """G1: OpenClaw 请求 → 中间件解析 inbound → 自动写入 channel_config.

        独特价值: 验证真实中间件上下文传播 (parse_openclaw_inbound → request.state →
                  _auto_provision_openclaw_channel → channel_config.db) 的完整请求生命周期,
                  集成层无法验证中间件 context 传播.
        """
        E2EMockLLM.set_script([AIMessage(content="收到，已处理。", tool_calls=[])])

        response = await e2e_client.post(
            "/v1/chat/completions",
            json=_openclaw_request("你好", e2e_test_thread_id),
            headers={"Authorization": f"Bearer {e2e_api_key}"},
        )

        assert response.status_code == 200

        # 灰盒: 验证 channel_config.db 写入了 wechat 渠道配置
        configs = _read_channel_config(
            e2e_test_user, e2e_test_thread_id, "personal-assistant"
        )
        assert len(configs) >= 1, "OpenClaw 请求应自动写入渠道配置"
        wechat = [c for c in configs if c.get("channel_type") == "wechat"]
        assert len(wechat) >= 1, "应写入 wechat 渠道"

        cfg = json.loads(wechat[0]["config"])
        assert cfg.get("openclaw_account") == "bot001", "account_id 应从 inbound 提取"
        assert cfg.get("target") == "wx_user_abc", "target 应取 chat_id"

    async def test_e2e_long_response_splits_and_spawns_followup(
        self,
        e2e_client,
        e2e_test_thread_id,
        e2e_api_key,
    ):
        """G2: 超长响应 (>2000) → 拆分 → spawn send_openclaw_followup 补发后续段.

        独特价值: 验证真实 stream_openclaw_response 的拆分+补发触发链路 (HTTP 驱动,
                  仅 E2E 可验证). patch send_openclaw_followup 边界排除渠道配置解析的
                  脆弱性, 聚焦拆分+spawn 协作本身.
        """
        from langchain_core.messages import AIMessage

        long_content = "这是超长回复。" * 300  # > 2000 字符触发拆分
        E2EMockLLM.set_script([AIMessage(content=long_content, tool_calls=[])])

        followup_mock = AsyncMock()

        with patch(
            "src.session.openclaw_message_splitter.send_openclaw_followup",
            new=followup_mock,
        ):
            response = await e2e_client.post(
                "/v1/chat/completions",
                json=_openclaw_request("给我一个详细的长回复", e2e_test_thread_id),
                headers={"Authorization": f"Bearer {e2e_api_key}"},
            )
            assert response.status_code == 200

            # drain 后台补发任务 (send_openclaw_followup 由 spawn_background_task 触发)
            from src.utils import async_utils

            tasks = list(async_utils._background_tasks)
            if tasks:
                await asyncio.wait_for(
                    asyncio.gather(*tasks, return_exceptions=True),
                    timeout=5.0,
                )

        # Assert: 补发被触发, 且含拆分余段 (证明发生了 >2000 拆分)
        # parts[0] 随主响应返回, parts[1:] 交补发; 拆分为 2 段时补发收 1 段
        assert followup_mock.await_count == 1, "超长响应应 spawn 一次补发"
        parts_arg = followup_mock.await_args.args[-1]
        assert isinstance(parts_arg, list)
        assert len(parts_arg) >= 1, "拆分后应有补发余段"
        assert parts_arg[0], "补发段内容非空"
