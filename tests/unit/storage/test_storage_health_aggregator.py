"""存储健康检查聚合器单元测试.

测试StorageHealthAggregator的功能，包括：
- 并行健康检查执行
- 服务状态聚合逻辑
- 错误处理和超时机制
- 统计信息计算

遵循单元测试设计规范：
- 白盒测试：专注验证聚合器的业务逻辑正确性
- Mock边界：所有服务实例完全Mock，不依赖真实服务
- 测试隔离：不执行真实的健康检查操作
"""

from __future__ import annotations

import asyncio
import time
from typing import Any
from unittest.mock import AsyncMock

import pytest

from src.storage.service.storage_health_aggregator import (
    HEALTH_CHECK_TIMEOUT_PER_SERVICE,
    StorageHealthAggregator,
)


class MockService:
    """Mock服务类，用于测试聚合器."""

    def __init__(self, name: str, status: str = "healthy", delay: float = 0.0):
        """初始化Mock服务.

        Args:
            name: 服务名称
            status: 健康状态 (healthy/degraded/unhealthy)
            delay: 模拟健康检查延迟（秒）
        """
        self.name = name
        self.status = status
        self.delay = delay

    async def health_check(self) -> dict[str, Any]:
        """模拟健康检查."""
        if self.delay > 0:
            await asyncio.sleep(self.delay)

        if self.status == "healthy":
            return {
                "status": "healthy",
                "message": f"{self.name}运行正常",
                "timestamp": time.time(),
                "details": {"service": self.name},
            }
        elif self.status == "degraded":
            return {
                "status": "degraded",
                "message": f"{self.name}运行降级",
                "timestamp": time.time(),
                "details": {"service": self.name, "warning": "性能警告"},
            }
        else:  # unhealthy
            return {
                "status": "unhealthy",
                "message": f"{self.name}运行异常",
                "timestamp": time.time(),
                "details": {"service": self.name, "error": "连接失败"},
            }

    async def health_check_with_exception(self) -> dict[str, Any]:
        """模拟抛出异常的健康检查."""
        raise Exception(f"{self.name}健康检查失败")


class TestStorageHealthAggregator:
    """StorageHealthAggregator 单元测试类."""

    @pytest.fixture
    def mock_conversation_service(self) -> MockService:
        """创建Mock对话服务."""
        return MockService("conversation_service", "healthy")

    @pytest.fixture
    def mock_todo_service(self) -> MockService:
        """创建Mock TODO服务."""
        return MockService("todo_service", "healthy")

    @pytest.fixture
    def mock_memory_service(self) -> MockService:
        """创建Mock记忆服务."""
        return MockService("memory_service", "healthy")

    @pytest.fixture
    def mock_vector_service(self) -> MockService:
        """创建Mock向量服务."""
        return MockService("vector_service", "healthy")

    @pytest.fixture
    def health_aggregator(
        self,
        mock_conversation_service: MockService,
        mock_todo_service: MockService,
        mock_memory_service: MockService,
        mock_vector_service: MockService,
    ) -> StorageHealthAggregator:
        """创建StorageHealthAggregator实例."""
        return StorageHealthAggregator(
            conversation_service=mock_conversation_service,
            todo_service=mock_todo_service,
            memory_service=mock_memory_service,
            vector_service=mock_vector_service,
        )

    @pytest.mark.asyncio
    async def test_check_all_services_health_should_return_healthy_when_all_services_healthy(
        self, health_aggregator: StorageHealthAggregator
    ) -> None:
        """测试聚合健康检查：所有服务健康时应返回overall healthy状态"""
        # Arrange - 所有Mock服务已设置为健康状态

        # Act - 执行聚合健康检查
        result = await health_aggregator.check_all_services_health()

        # Assert - 验证整体健康状态和统计信息
        assert result["status"] == "healthy"
        assert result["summary"]["total_services"] == 4
        assert result["summary"]["healthy"] == 4
        assert result["summary"]["unhealthy"] == 0
        assert result["summary"]["degraded"] == 0
        assert result["summary"]["errors"] == 0
        assert result["aggregation_info"]["parallel_execution"] is True
        assert len(result["services"]) == 4

        # 验证每个服务的状态
        for service_name, service_result in result["services"].items():
            assert service_result["status"] == "healthy"
            assert "运行正常" in service_result["message"]

    @pytest.mark.asyncio
    async def test_check_all_services_health_should_return_degraded_when_some_services_degraded(
        self, health_aggregator: StorageHealthAggregator
    ) -> None:
        """测试聚合健康检查：部分服务降级时应返回overall degraded状态"""
        # Arrange - 设置一个服务为降级状态
        health_aggregator.memory_service.status = "degraded"

        # Act - 执行聚合健康检查
        result = await health_aggregator.check_all_services_health()

        # Assert - 验证整体降级状态和统计信息
        assert result["status"] == "degraded"
        assert result["summary"]["total_services"] == 4
        assert result["summary"]["healthy"] == 3
        assert result["summary"]["degraded"] == 1
        assert result["summary"]["unhealthy"] == 0

    @pytest.mark.asyncio
    async def test_check_all_services_health_should_return_unhealthy_when_any_service_unhealthy(
        self, health_aggregator: StorageHealthAggregator
    ) -> None:
        """测试聚合健康检查：任一服务不健康时应返回overall unhealthy状态"""
        # Arrange - 设置一个服务为不健康状态
        health_aggregator.todo_service.status = "unhealthy"

        # Act - 执行聚合健康检查
        result = await health_aggregator.check_all_services_health()

        # Assert - 验证整体不健康状态和统计信息
        assert result["status"] == "unhealthy"
        assert result["summary"]["total_services"] == 4
        assert result["summary"]["healthy"] == 3
        assert result["summary"]["unhealthy"] == 1
        assert result["summary"]["degraded"] == 0

    @pytest.mark.asyncio
    async def test_check_all_services_health_should_handle_service_exceptions_gracefully(
        self, health_aggregator: StorageHealthAggregator
    ) -> None:
        """测试聚合健康检查：服务抛出异常时应优雅处理并计入错误统计"""
        # Arrange - 设置一个服务抛出异常
        health_aggregator.conversation_service.health_check = AsyncMock(
            side_effect=Exception("数据库连接失败")
        )

        # Act - 执行聚合健康检查
        result = await health_aggregator.check_all_services_health()

        # Assert - 验证异常被正确处理
        assert result["status"] == "unhealthy"  # 异常导致整体状态为unhealthy
        assert result["summary"]["errors"] == 1
        assert result["summary"]["healthy"] == 3
        assert result["summary"]["unhealthy"] == 1  # 异常被标记为unhealthy

        # 验证异常服务的信息
        conversation_result = result["services"]["conversation_service"]
        assert conversation_result["status"] == "unhealthy"
        assert "数据库连接失败" in conversation_result["error"]

    @pytest.mark.asyncio
    async def test_check_all_services_health_should_execute_checks_in_parallel(
        self, health_aggregator: StorageHealthAggregator
    ) -> None:
        """测试聚合健康检查：应并行执行所有服务的健康检查"""
        # Arrange - 为所有服务添加轻微延迟
        for service in [
            health_aggregator.conversation_service,
            health_aggregator.todo_service,
            health_aggregator.memory_service,
            health_aggregator.vector_service,
        ]:
            service.delay = 0.1

        # Act - 记录开始时间并执行聚合健康检查
        start_time = time.time()
        result = await health_aggregator.check_all_services_health()
        duration = time.time() - start_time

        # Assert - 验证并行执行（总时间应小于串行执行时间）
        # 串行执行需要0.4秒，并行执行应该接近0.1秒
        assert duration < 0.2, f"并行执行时间过长: {duration:.3f}秒"
        assert result["aggregation_info"]["parallel_execution"] is True

    @pytest.mark.asyncio
    async def test_check_all_services_health_should_include_aggregation_metadata(
        self, health_aggregator: StorageHealthAggregator
    ) -> None:
        """测试聚合健康检查：应包含聚合元数据和统计信息"""
        # Arrange - 聚合器已准备就绪

        # Act - 执行聚合健康检查
        result = await health_aggregator.check_all_services_health()

        # Assert - 验证聚合元数据
        assert "aggregation_info" in result
        aggregation_info = result["aggregation_info"]
        assert aggregation_info["parallel_execution"] is True
        assert aggregation_info["timeout_per_service"] == HEALTH_CHECK_TIMEOUT_PER_SERVICE
        assert len(aggregation_info["services_checked"]) == 4
        assert "conversation_service" in aggregation_info["services_checked"]
        assert "todo_service" in aggregation_info["services_checked"]
        assert "memory_service" in aggregation_info["services_checked"]
        assert "vector_service" in aggregation_info["services_checked"]

    @pytest.mark.asyncio
    async def test_check_all_services_health_should_handle_multiple_service_failures(
        self, health_aggregator: StorageHealthAggregator
    ) -> None:
        """测试聚合健康检查：多个服务失败时应正确统计和返回unhealthy状态"""
        # Arrange - 设置多个服务失败
        health_aggregator.todo_service.status = "unhealthy"
        health_aggregator.memory_service.health_check = AsyncMock(
            side_effect=Exception("记忆服务异常")
        )
        health_aggregator.vector_service.status = "degraded"

        # Act - 执行聚合健康检查
        result = await health_aggregator.check_all_services_health()

        # Assert - 验证多种失败状态的正确统计
        assert result["status"] == "unhealthy"  # 有unhealthy服务
        assert result["summary"]["total_services"] == 4
        assert result["summary"]["healthy"] == 1  # conversation
        assert result["summary"]["degraded"] == 1  # vector
        assert result["summary"]["unhealthy"] == 2  # todo + memory exception
        assert result["summary"]["errors"] == 1  # memory exception
