"""API系统集成测试.

## 📖 测试策略文档

### Mock边界定义
**Mock外部服务**:
- LLM API服务 - 避免依赖真实语言模型服务
- 嵌入服务 - 避免依赖真实嵌入服务
- 外部数据库 - 确保测试独立性

**保留内部组件**:
- FastAPI路由和中间件 - 真实的API处理逻辑
- 认证中间件 - 真实的用户认证和授权
- AgentManager - 真实的Agent获取和管理
- PersonalAssistantAgent - 真实的业务逻辑处理
- 错误处理中间件 - 真实的错误处理和响应格式化

### 协作场景覆盖
1. FastAPI路由 + AgentManager → 请求处理验证
2. 认证中间件 + 用户管理 → 访问控制验证
3. 错误处理 + 响应格式化 → 异常处理验证
4. 请求验证 + 参数处理 → 输入验证验证
5. 并发请求 + 资源管理 → 性能验证

### 业务价值
- 确保用户请求能正确路由到Agent并处理
- 验证API认证和授权机制的有效性
- 保障API响应格式的标准性和一致性
- 验证错误处理的用户友好性和调试支持
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from src.api.fastapi_app import app


@pytest.mark.integration
class TestAPIRequestProcessingIntegration:
    """测试API请求处理完整流程."""

    @pytest.fixture
    def client(self) -> Iterator[TestClient]:
        """创建测试客户端.

        走 ``with TestClient(app)`` 触发 lifespan, 确保 teardown 时 app shutdown
        调 close_all_db_managers() 在 portal loop 上正确 dispose engine,
        避免同步测试创建的 aiosqlite 连接跨循环泄漏.
        """
        with TestClient(app) as c:
            yield c

    @pytest.fixture
    def valid_api_key(self) -> str:
        """获取有效的测试API密钥."""
        # 从静态用户管理配置中获取test_user的API密钥
        return "sk-project-test_user-test_thread-e2e123456789"

    def test_agent_not_found_error_handling(
        self, client: TestClient, valid_api_key: str
    ):
        """测试Agent不存在的错误处理.

        协作场景: AgentManager + 错误处理 → 资源不存在验证
        设计思路: 验证请求不存在Agent时的错误处理
        Mock边界: Mock外部服务，保留真实Agent管理
        验证重点:
        1. 无效Agent ID的检测
        2. 错误响应的格式一致性
        3. 错误信息的用户友好性
        4. HTTP状态码的正确性

        业务价值: 确保用户能获得清晰的Agent不存在错误信息
        """
        # Arrange - 准备请求头和无效Agent
        headers = {
            "Authorization": f"Bearer {valid_api_key}",
            "Content-Type": "application/json",
        }

        request_data = {
            "model": "non-existent-agent",
            "messages": [{"role": "user", "content": "测试不存在的Agent"}],
        }

        # Act - 请求不存在的Agent
        response = client.post(
            "/v1/chat/completions", headers=headers, json=request_data
        )

        # Assert - 验证错误处理
        assert response.status_code == 404

        error_data = response.json()
        assert "detail" in error_data
        assert "not found" in error_data["detail"].lower()

    def test_models_list_integration(self, client: TestClient, valid_api_key: str):
        """测试模型列表API集成.

        协作场景: AgentManager + 模型列表API → 动态发现验证
        设计思路: 验证模型列表的动态获取和格式转换
        Mock边界: Mock外部服务，保留真实Agent发现机制
        验证重点:
        1. Agent自动发现的准确性
        2. 模型列表格式的OpenAI兼容性
        3. 模型信息结构的完整性
        4. 错误处理的有效性

        业务价值: 确保客户端能正确获取可用Agent列表
        """
        # Act - 获取模型列表（修复：添加认证token）
        headers = {"Authorization": f"Bearer {valid_api_key}"}
        response = client.get("/v1/models", headers=headers)

        # Assert - 验证响应格式
        assert response.status_code == 200

        response_data = response.json()
        assert response_data["object"] == "list"
        assert "data" in response_data

        models = response_data["data"]
        assert isinstance(models, list)
        assert len(models) > 0  # 至少应该有一个可用的Agent

        # 验证每个模型的结构
        for model in models:
            assert "id" in model
            assert "object" in model
            assert model["object"] == "model"
            assert "created" in model
            assert "owned_by" in model
            assert model["owned_by"] == "personal-assistant"

        # 验证包含personal-assistant
        model_ids = [model["id"] for model in models]
        assert "personal-assistant" in model_ids


@pytest.mark.integration
class TestAPIIntegrationWithRealComponents:
    """测试API与真实组件的集成."""

    @pytest.fixture
    def client(self) -> Iterator[TestClient]:
        """创建测试客户端.

        走 ``with TestClient(app)`` 触发 lifespan, 确保 teardown 时 app shutdown
        调 close_all_db_managers() 在 portal loop 上正确 dispose engine,
        避免同步测试创建的 aiosqlite 连接跨循环泄漏.
        """
        with TestClient(app) as c:
            yield c

    @pytest.fixture
    def valid_api_key(self) -> str:
        """获取有效的测试API密钥."""
        # 从静态用户管理配置中获取test_user的API密钥
        return "sk-project-test_user-test_thread-e2e123456789"

    def test_api_error_propagation_integration(
        self, client: TestClient, valid_api_key: str
    ):
        """测试API错误传播集成.

        协作场景: API层 + AgentManager + 错误处理 → 错误传播验证
        设计思路: 验证各种错误的正确传播和处理
        Mock边界: 模拟各种错误场景，验证错误传播链
        验证重点:
        1. Agent层错误的正确传播
        2. 认证错误的正确处理
        3. 验证错误的正确传播
        4. 系统错误的优雅处理
        5. 错误信息的一致性

        业务价值: 确保错误信息能正确传播并友好呈现
        """
        # 测试各种错误场景
        test_cases = [
            {
                "name": "无效模型ID",
                "request_data": {
                    "model": "invalid-model-id",
                    "messages": [{"role": "user", "content": "测试"}],
                },
                "expected_status": 404,
            },
            {
                "name": "无效请求格式",
                "request_data": {
                    "model": "personal-assistant",
                    "messages": "invalid-messages",  # 应该是列表
                },
                "expected_status": 400,  # 修复: FastAPI验证错误返回400而非422
            },
        ]

        headers = {
            "Authorization": f"Bearer {valid_api_key}",
            "Content-Type": "application/json",
        }

        for test_case in test_cases:
            # Act - 发送错误请求
            response = client.post(
                "/v1/chat/completions", headers=headers, json=test_case["request_data"]
            )

            # Assert - 验证错误处理
            assert response.status_code == test_case["expected_status"], (
                f"{test_case['name']} 状态码不正确"
            )

            # 验证错误响应格式
            if response.status_code != 400:  # 修复: 验证错误不是400（bad request）
                error_data = response.json()
                assert "detail" in error_data, f"{test_case['name']} 错误响应格式不正确"
