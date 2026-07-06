"""API 冒烟测试 — 服务存活 + Agent 注册 + 错误边界.

独特价值: 验证真实 FastAPI 路由 + 中间件链的 HTTP 边界行为.
集成测试直接调用 Service 方法, 无法覆盖 HTTP 路由/中间件/认证.
"""

from __future__ import annotations

import pytest


@pytest.mark.e2e
class TestAPISmokeE2E:
    """HTTP 边界冒烟测试."""

    async def test_server_health_and_agent_registry(
        self,
        e2e_client,
    ):
        """验证 /health 存活 + /v1/models Agent 注册表.

        独特价值: 验证真实服务启动 + AgentFactory 从 agent.yaml 加载.
        """
        health = await e2e_client.get("/health")
        assert health.status_code == 200
        assert health.json()["status"] in ("healthy", "ok")

        models = await e2e_client.get("/v1/models")
        assert models.status_code == 200
        model_ids = [m["id"] for m in models.json()["data"]]
        assert "personal-assistant" in model_ids

    async def test_invalid_request_returns_error(
        self,
        e2e_client,
        e2e_test_thread_id,
        e2e_api_key,
    ):
        """验证缺少 messages 字段时返回 4xx.

        独特价值: 验证 FastAPI Pydantic 校验 + 错误响应格式.
        """
        response = await e2e_client.post(
            "/v1/chat/completions",
            json={
                "model": "personal-assistant",
                "user": e2e_test_thread_id,
            },
            headers={"Authorization": f"Bearer {e2e_api_key}"},
        )
        assert response.status_code in (400, 422)
