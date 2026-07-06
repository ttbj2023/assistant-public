"""存储服务健康检查单元测试.

测试存储服务的健康检查功能，包括：
- ServiceHealthCheckMixin 的通用功能
- 各个服务的健康检查方法
- 错误处理和边界情况
- 并行健康检查功能

遵循单元测试设计规范：
- 白盒测试：专注验证单一功能模块的业务逻辑正确性
- Mock边界：外部依赖完全Mock，内部逻辑保留
- 测试隔离：不依赖外部资源，确保测试稳定性和可重复性
"""

from __future__ import annotations

from typing import Any

import pytest

from src.storage.service.health_check_mixin import ServiceHealthCheckMixin


class MockServiceWithHealthCheck(ServiceHealthCheckMixin):
    """用于测试的Mock服务类."""

    def __init__(self, should_fail: bool = False, fail_type: str = "exception"):
        """初始化Mock服务.

        Args:
            should_fail: 是否模拟失败
            fail_type: 失败类型 (exception, unhealthy, degraded)
        """
        super().__init__()
        self.should_fail = should_fail
        self.fail_type = fail_type
        self.service_name = "mock_service"

    async def _check_service_health(self) -> dict[str, Any]:
        """模拟健康检查实现."""
        if not self.should_fail:
            return {
                "status": "healthy",
                "database_connected": True,
                "statistics": {"test_param": "test_value", "mock_data": True},
            }

        if self.fail_type == "exception":
            raise ValueError("模拟服务异常")
        elif self.fail_type == "unhealthy":
            return {
                "status": "unhealthy",
                "database_connected": False,
                "statistics": {},
                "error": "模拟错误",
            }
        elif self.fail_type == "degraded":
            return {
                "status": "degraded",
                "database_connected": True,
                "statistics": {"warning": "模拟警告"},
                "additional_info": {"degraded_reason": "模拟警告"},
            }

        return {"status": "unknown"}


class TestServiceHealthCheckMixin:
    """ServiceHealthCheckMixin 测试类."""

    @pytest.fixture
    def healthy_service(self) -> MockServiceWithHealthCheck:
        """创建健康的服务实例."""
        return MockServiceWithHealthCheck(should_fail=False)

    @pytest.fixture
    def failing_service(self) -> MockServiceWithHealthCheck:
        """创建失败的服务实例."""
        return MockServiceWithHealthCheck(should_fail=True, fail_type="exception")

    @pytest.fixture
    def unhealthy_service(self) -> MockServiceWithHealthCheck:
        """创建不健康的服务实例."""
        return MockServiceWithHealthCheck(should_fail=True, fail_type="unhealthy")

    @pytest.fixture
    def degraded_service(self) -> MockServiceWithHealthCheck:
        """创建降级的服务实例."""
        return MockServiceWithHealthCheck(should_fail=True, fail_type="degraded")

    @pytest.mark.asyncio
    async def test_health_check_should_return_healthy_status_when_service_operational(
        self, healthy_service: MockServiceWithHealthCheck
    ) -> None:
        """测试健康检查：服务正常运行时应返回healthy状态"""
        # Arrange - 健康服务已准备就绪

        # Act - 执行健康检查
        result = await healthy_service.health_check()

        # Assert - 验证健康状态和响应结构
        assert result["status"] == "healthy"
        assert result["service_name"] == "MockServiceWithHealthCheck"
        assert "last_check" in result
        assert "duration_ms" in result
        assert isinstance(result["duration_ms"], float)
        assert result["database_connected"] is True
        assert result["statistics"]["mock_data"] is True

    @pytest.mark.asyncio
    async def test_health_check_should_return_unhealthy_status_when_service_reports_unhealthy(
        self, unhealthy_service: MockServiceWithHealthCheck
    ) -> None:
        """测试健康检查：服务报告不健康时应返回unhealthy状态"""
        # Arrange - 不健康服务已准备就绪

        # Act - 执行健康检查
        result = await unhealthy_service.health_check()

        # Assert - 验证unhealthy状态和错误信息
        assert result["status"] == "unhealthy"
        assert result["service_name"] == "MockServiceWithHealthCheck"
        assert result["database_connected"] is False
        assert "error" in result
        assert result["error"] == "模拟错误"

    @pytest.mark.asyncio
    async def test_health_check_should_return_degraded_status_when_service_reports_degraded(
        self, degraded_service: MockServiceWithHealthCheck
    ) -> None:
        """测试健康检查：服务报告降级时应返回degraded状态"""
        # Arrange - 降级服务已准备就绪

        # Act - 执行健康检查
        result = await degraded_service.health_check()

        # Assert - 验证degraded状态和警告信息
        assert result["status"] == "degraded"
        assert result["service_name"] == "MockServiceWithHealthCheck"
        assert result["database_connected"] is True
        assert "warning" in result["statistics"]
        assert result["statistics"]["warning"] == "模拟警告"
        # 验证additional_info也被正确包含
        assert "degraded_reason" in result
        assert result["degraded_reason"] == "模拟警告"

    @pytest.mark.asyncio
    async def test_health_check_should_handle_service_exception_gracefully(
        self, failing_service: MockServiceWithHealthCheck
    ) -> None:
        """测试健康检查：服务抛出异常时应优雅处理并返回unhealthy状态"""
        # Arrange - 会抛出异常的服务已准备就绪

        # Act - 执行健康检查
        result = await failing_service.health_check()

        # Assert - 验证异常被正确捕获和转换为unhealthy状态
        assert result["status"] == "unhealthy"
        assert result["service_name"] == "MockServiceWithHealthCheck"
        assert result["database_connected"] is False
        assert "error" in result
        assert "模拟服务异常" in result["error"]

    @pytest.mark.asyncio
    async def test_health_check_response_structure_should_include_standard_fields(
        self, healthy_service: MockServiceWithHealthCheck
    ) -> None:
        """测试健康检查响应结构：应包含所有标准字段"""
        # Arrange - 健康服务已准备就绪

        # Act - 执行健康检查
        result = await healthy_service.health_check()

        # Assert - 验证标准字段结构
        required_fields = [
            "status",
            "service_name",
            "last_check",
            "duration_ms",
            "database_connected",
            "statistics",
            "error",
        ]
        for field in required_fields:
            assert field in result, f"缺少必需字段: {field}"

        # 验证字段类型和值
        assert result["status"] in ["healthy", "degraded", "unhealthy"]
        assert isinstance(result["service_name"], str)
        assert isinstance(result["last_check"], str)
        assert isinstance(result["duration_ms"], (int, float))
        assert isinstance(result["database_connected"], bool)
        assert isinstance(result["statistics"], dict)
        assert result["error"] is None  # 健康状态下error应为None

    @pytest.mark.asyncio
    async def test_multiple_health_checks_should_return_consistent_results(
        self, healthy_service: MockServiceWithHealthCheck
    ) -> None:
        """测试多次健康检查的一致性：多次执行应返回一致的结果"""
        # Arrange - 健康服务已准备就绪

        # Act - 执行多次健康检查
        result1 = await healthy_service.health_check()
        result2 = await healthy_service.health_check()

        # Assert - 验证结果一致性
        assert result1["status"] == result2["status"]
        assert result1["service_name"] == result2["service_name"]
        assert result1["statistics"] == result2["statistics"]
        # 注意：last_check和duration_ms可能会不同，这是正常的
