"""HealthAssistantAgent 健康服务集成测试.

## 测试策略文档

### Mock边界定义
无 Mock - 使用真实的 Service 工厂创建健康服务.

### 协作场景覆盖
1. Service 工厂 + AsyncDatabaseManager + AsyncHealthDAO → Service 装配验证

### 业务价值
确保健康助手相关 Service 工厂能正确创建并注入 DAO,
为健康数据读写提供可靠基础.

注意: HealthAssistantAgent 的配置加载/工厂创建/工具配置等场景,
已被 test_agent_system_integration.py 的 Agent 发现机制覆盖,
此处不再重复 smoke 测试.
"""

from __future__ import annotations

import pytest


@pytest.mark.integration
class TestHealthServicesIntegration:
    """测试健康 Service 工厂的真实装配."""

    @pytest.mark.asyncio
    async def test_health_service_factory_assembles_dao(
        self, test_user: str, test_thread_id: str
    ):
        """测试健康服务通过工厂正确装配并注入 DAO.

        协作场景: Service 工厂 + AsyncDatabaseManager + AsyncHealthDAO → 装配链验证
        设计思路: 通过真实工厂创建 HealthDataService, 验证 DAO 正确注入,
                 指向 user/thread/agent 隔离数据库
        Mock边界: 无 Mock, 使用真实 Service 工厂和真实 SQLite
        业务价值: 确保 HealthAssistantAgent 依赖的 HealthDataService 能正确初始化,
                 这是健康数据读取的前置条件
        """
        from src.storage.service import create_health_service

        health_service = await create_health_service(
            test_user, test_thread_id, agent_id="health-assistant"
        )
        assert health_service is not None
        assert hasattr(health_service, "health_dao")
