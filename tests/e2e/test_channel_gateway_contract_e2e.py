"""渠道网关对接 E2E 测试 — 核心独特价值.

验证渠道网关 (weixin-gateway) 的对接契约:
- X-Channel* 请求头 → 渠道配置自动发现写入 (定时消息/价格提醒/通知的投递依据)
- file content block 全链路 (已在 test_document_input_e2e 覆盖)

独特价值: 渠道头识别在 HTTP 边界 (中间件链 + 路由), 单元测试无法覆盖
FastAPI 请求头注入与 user_channel_config 物理隔离协作.
"""

from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage

from tests.e2e.mock_llm import E2EMockLLM


@pytest.mark.e2e
class TestChannelGatewayContractE2E:
    """渠道网关请求头契约 E2E 测试."""

    async def test_channel_headers_provision_wechat_config(
        self,
        e2e_client,
        e2e_test_thread_id,
        e2e_api_key,
        e2e_test_user,
    ):
        """带渠道头的请求: 自动写入 wechat 渠道配置 (target/account_id)."""
        E2EMockLLM.set_script([AIMessage(content="好的。", tool_calls=[])])

        response = await e2e_client.post(
            "/v1/chat/completions",
            json={
                "model": "personal-assistant",
                "messages": [{"role": "user", "content": "你好"}],
                "stream": False,
                "user": e2e_test_thread_id,
            },
            headers={
                "Authorization": f"Bearer {e2e_api_key}",
                "X-Channel": "weixin",
                "X-Channel-Account": "59ab20669675-im-bot",
                "X-Chat-Id": "o9cq809Y4lRGYVu00SjmCyK6K45s@im.wechat",
            },
        )

        assert response.status_code == 200

        from src.storage.service.user_channel_config_service import (
            get_user_channel_config_service,
        )

        config_service = await get_user_channel_config_service(
            e2e_test_user, e2e_test_thread_id, "personal-assistant"
        )
        cfg = await config_service.get_config_for_channel("wechat")
        assert cfg is not None, "渠道配置应自动写入"
        assert cfg["target"] == "o9cq809Y4lRGYVu00SjmCyK6K45s@im.wechat"
        assert cfg["account_id"] == "59ab20669675-im-bot"

    async def test_channel_config_self_heals_on_change(
        self,
        e2e_client,
        e2e_test_thread_id,
        e2e_api_key,
        e2e_test_user,
    ):
        """渠道头字段变化时配置自愈更新."""
        E2EMockLLM.set_script([AIMessage(content="好", tool_calls=[])])

        headers = {
            "Authorization": f"Bearer {e2e_api_key}",
            "X-Channel": "weixin",
            "X-Channel-Account": "bot-first",
            "X-Chat-Id": "user-1@im.wechat",
        }
        await e2e_client.post(
            "/v1/chat/completions",
            json={
                "model": "personal-assistant",
                "messages": [{"role": "user", "content": "hi"}],
                "stream": False,
                "user": e2e_test_thread_id,
            },
            headers=headers,
        )

        headers["X-Channel-Account"] = "bot-second"
        await e2e_client.post(
            "/v1/chat/completions",
            json={
                "model": "personal-assistant",
                "messages": [{"role": "user", "content": "again"}],
                "stream": False,
                "user": e2e_test_thread_id,
            },
            headers=headers,
        )

        from src.storage.service.user_channel_config_service import (
            get_user_channel_config_service,
        )

        config_service = await get_user_channel_config_service(
            e2e_test_user, e2e_test_thread_id, "personal-assistant"
        )
        cfg = await config_service.get_config_for_channel("wechat")
        assert cfg["account_id"] == "bot-second", "字段变化应自愈更新"

    async def test_request_without_channel_headers_skips_provisioning(
        self,
        e2e_client,
        e2e_test_thread_id,
        e2e_api_key,
        e2e_test_user,
    ):
        """无渠道头的普通请求: 不写渠道配置."""
        E2EMockLLM.set_script([AIMessage(content="你好。", tool_calls=[])])

        await e2e_client.post(
            "/v1/chat/completions",
            json={
                "model": "personal-assistant",
                "messages": [{"role": "user", "content": "你好"}],
                "stream": False,
                "user": e2e_test_thread_id,
            },
            headers={"Authorization": f"Bearer {e2e_api_key}"},
        )

        from src.storage.service.user_channel_config_service import (
            get_user_channel_config_service,
        )

        config_service = await get_user_channel_config_service(
            e2e_test_user, e2e_test_thread_id, "personal-assistant"
        )
        cfg = await config_service.get_config_for_channel("wechat")
        assert cfg is None, "无渠道头不应写配置"
