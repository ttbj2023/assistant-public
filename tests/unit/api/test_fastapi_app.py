"""FastAPI应用模块单元测试.

通过 TestClient 验证路由端点/中间件/文档的 HTTP 栈基础行为.
聚焦 FastAPI 框架层 (路由可达性 / Pydantic 验证 / OpenAPI 文档),
Agent 与存储等外部依赖均 Mock, 正向 chat 链路由集成测试覆盖
(见 tests/integration/memory/test_processor_orchestrator_integration.py).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, Mock, patch

import pytest
from fastapi.testclient import TestClient

# 导入FastAPI应用模块
import src.api.fastapi_app as fastapi_app_module
from src.api.fastapi_app import app


class TestFastAPIApp:
    """FastAPI应用核心功能单元测试."""

    @pytest.fixture(autouse=True)
    def mock_global_auth_manager(self, monkeypatch):
        """自动Mock全局auth_manager，确保测试隔离性"""
        mock_auth = Mock()
        mock_auth.authenticate_request.return_value = ("test_user", "main")
        mock_auth._extract_api_key_from_request.return_value = "test-key"
        monkeypatch.setattr(fastapi_app_module, "auth_manager", mock_auth)

    @pytest.fixture
    def mock_config(self):
        """Mock API配置"""
        mock = Mock()
        mock.cors_origins = ["http://localhost:3000"]
        mock.cors_methods = ["GET", "POST"]
        mock.cors_headers = ["*"]
        return mock

    @pytest.fixture
    def mock_agent_manager(self):
        """Mock Agent管理器"""
        from langchain_core.messages import AIMessage

        mock = Mock()
        # 创建完整的Mock Agent，包括invoke方法
        mock_agent = Mock()
        mock_agent.invoke = Mock(return_value=AIMessage(content="Test response"))
        mock_agent.ainvoke = AsyncMock(return_value=AIMessage(content="Test response"))
        mock_agent.astream = AsyncMock(return_value=AIMessage(content="Test response"))
        mock.get_agent.return_value = mock_agent
        return mock

    @pytest.fixture
    def mock_auth_manager(self):
        """Mock认证管理器"""
        mock = Mock()
        mock.validate_api_key.return_value = Mock(
            valid=True, user_id="test_user", thread_id="main"
        )
        # 添加authenticate_request方法mock
        mock.authenticate_request.return_value = ("test_user", "main")
        # 添加_extract_api_key_from_request方法mock
        mock._extract_api_key_from_request.return_value = "test-key"
        return mock

    @pytest.fixture
    def client(self, mock_config, mock_agent_manager, mock_auth_manager):
        """创建测试客户端"""
        from langchain_core.messages import AIMessage

        from tests.mocks.service_mock_factory import ServiceMockFactory

        # 创建标准Service Mock，防止真实数据库初始化
        services = ServiceMockFactory.create_all_services()

        # 创建Mock Agent，防止真实LLM调用
        mock_agent = Mock()
        mock_agent.invoke = Mock(return_value=AIMessage(content="Test response"))
        mock_agent.ainvoke = AsyncMock(return_value=AIMessage(content="Test response"))
        mock_agent.astream = AsyncMock(return_value=AIMessage(content="Test response"))

        with (
            patch("src.api.fastapi_app.get_config", return_value=mock_config),
            patch(
                "src.api.fastapi_app.get_agent_manager", return_value=mock_agent_manager
            ),
            patch(
                "src.api.fastapi_app.get_auth_manager", return_value=mock_auth_manager
            ),
            patch("src.api.fastapi_app.get_llm_factory", return_value=Mock()),
            # Patch存储服务工厂，防止真实数据库初始化导致超时
            patch(
                "src.storage.service.create_conversation_service",
                return_value=services["conversation"],
            ),
            patch(
                "src.storage.service.create_vector_service",
                return_value=services["vector"],
            ),
            # 关键：patch get_agent函数，防止真实Agent执行
            patch("src.agent.manager.get_agent", return_value=mock_agent),
            patch(
                "src.api.routes.chat.get_agent",
                return_value=AsyncMock(return_value=mock_agent),
            ),
        ):
            return TestClient(app)

    def test_health_endpoint_should_return_healthy_status(self, client):
        """测试健康检查端点：应该返回健康状态"""
        response = client.get("/health")

        assert response.status_code == 200
        data = response.json()
        assert "status" in data

    def test_error_handling_middleware_should_catch_validation_errors(self, client):
        """测试错误处理中间件：应该捕获验证错误"""
        # 发送无效的JSON数据（缺少必需字段）
        headers = {"Authorization": "Bearer test-key"}
        response = client.post(
            "/v1/chat/completions", headers=headers, json={"invalid": "data"}
        )

        # 应该返回验证错误（422）或业务错误
        assert response.status_code in [400, 422]
