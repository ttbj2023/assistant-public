"""健康检查路由的后端功能模块."""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

router = APIRouter()

logger = logging.getLogger(__name__)


class HealthResponse(BaseModel):
    """健康检查响应模型."""

    status: str
    version: str
    uptime_seconds: int
    timestamp: int
    duration_ms: float
    checks: dict[str, Any]


class HealthCheckResponse(BaseModel):
    """健康检查响应模型."""

    status: str
    message: str
    timestamp: float
    duration_ms: float | None = None
    details: dict[str, Any] | None = None


@router.get("/health", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    """健康检查端点.

    Returns:
        HealthResponse: 健康检查结果,包含状态,版本号,运行时间等信息

    Raises:
        HTTPException: 当服务不可用时

    """
    try:
        start_time = time.time()

        # 并行执行所有健康检查
        health_results = await asyncio.gather(
            _check_storage_health(),
            asyncio.to_thread(_check_agent_health),
            return_exceptions=True,
        )

        # 处理存储健康检查结果
        storage_result = (
            health_results[0]
            if not isinstance(health_results[0], Exception)
            else {
                "status": "unhealthy",
                "message": f"存储健康检查异常: {health_results[0]!s}",
            }
        )

        # 处理Agent健康检查结果
        agent_result = (
            health_results[1]
            if not isinstance(health_results[1], Exception)
            else {
                "status": "unhealthy",
                "message": f"Agent健康检查异常: {health_results[1]!s}",
            }
        )

        # 判断整体健康状态
        overall_status = "healthy"
        issues = []

        # 检查存储层状态
        if storage_result.get("status") != "healthy":
            overall_status = (
                "degraded"
                if storage_result.get("status") == "degraded"
                else "unhealthy"
            )
            issues.append(storage_result.get("message", "存储层异常"))

        # 检查Agent系统状态
        if agent_result.get("overall_status") != "healthy":
            if overall_status == "healthy":
                overall_status = (
                    "degraded"
                    if agent_result.get("overall_status") == "degraded"
                    else "unhealthy"
                )
            issues.append(agent_result.get("overall_message", "Agent系统异常"))

        duration_ms = (time.time() - start_time) * 1000

        response_data = {
            "status": overall_status,
            "version": "1.0.0",
            "uptime_seconds": int(time.time() - 0),  # 实际应该记录服务启动时间
            "timestamp": int(time.time()),
            "duration_ms": round(duration_ms, 2),
            "checks": {
                "storage": storage_result,
                "agent": agent_result,
            },
        }

        # 如果有问题,添加到响应中
        if issues:
            response_data["issues"] = issues

        logger.info(
            f"健康检查完成 - 状态: {overall_status}, "
            f"用时: {duration_ms:.2f}ms, "
            f"存储状态: {storage_result.get('status', 'unknown')}, "
            f"Agent状态: {agent_result.get('overall_status', 'unknown')}",
        )

        return HealthResponse(**response_data)

    except Exception as e:
        logger.error("❌ 健康检查端点异常: %s", e, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"健康检查失败: {e!s}",
        ) from e


def _check_agent_health() -> dict[str, Any]:
    """检查Agent系统健康状态.

    Returns:
        包含Agent系统健康状态的字典

    """
    from src.agent.agent_health_checker import lightweight_agent_health_check

    # Agent健康检查是同步操作,可以直接调用
    return lightweight_agent_health_check()


async def _check_storage_health() -> dict[str, Any]:
    """检查存储层健康状态.

    Returns:
        包含存储层健康状态的字典:
        - status: healthy/degraded/unhealthy/error
        - message: 状态描述
        - timestamp: 检查时间戳
        - details: 详细信息
            - storage_enabled: 是否启用存储层
            - services: 服务层健康检查结果 (如果启用)
            - total_services/healthy_services/unhealthy_services: 服务统计

    """
    start_time = time.time()
    temp_user_id = "health_check_user"
    temp_thread_id = "health_check_thread"
    temp_user_path: Path | None = None

    try:
        # 检查存储层是否启用
        from src.config.core_config import get_config

        config = get_config()
        storage_enabled = getattr(config, "enable_storage", True)

        if not storage_enabled:
            duration_ms = (time.time() - start_time) * 1000
            return {
                "status": "degraded",
                "message": "存储层已禁用",
                "timestamp": time.time(),
                "duration_ms": round(duration_ms, 2),
                "details": {
                    "storage_enabled": False,
                    "reason": "config_disabled",
                },
            }

        # 创建临时Service实例进行健康检查
        from src.storage.service import (
            StorageHealthAggregator,
            create_conversation_service,
            create_memory_service,
            create_todo_service,
            create_vector_service,
        )

        # 创建所有Service实例
        conv_service = await create_conversation_service(
            temp_user_id,
            temp_thread_id,
            agent_id="health_check",
        )
        todo_service = await create_todo_service(
            temp_user_id,
            temp_thread_id,
            agent_id="health_check",
        )
        memory_service = await create_memory_service(
            temp_user_id,
            temp_thread_id,
            agent_id="health_check",
        )
        vector_service = create_vector_service(
            temp_user_id,
            temp_thread_id,
            agent_id="health_check",
        )

        # 创建健康检查聚合器
        health_aggregator = StorageHealthAggregator(
            conversation_service=conv_service,
            todo_service=todo_service,
            memory_service=memory_service,
            vector_service=vector_service,
        )

        # 执行聚合健康检查
        storage_health = await health_aggregator.check_all_services_health()

        # 检查用户数据目录是否可写
        from src.core.path_resolver import get_user_path_resolver

        resolver = get_user_path_resolver()
        user_path = resolver.get_thread_base_path(temp_user_id, temp_thread_id)
        path_writable = False
        temp_user_path = None

        try:
            temp_user_path = resolver.base_path / temp_user_id
            temp_user_path.mkdir(parents=True, exist_ok=True)
            test_file = temp_user_path / "health_check_test.tmp"
            test_file.write_text("health_check")
            path_writable = True
            test_file.unlink()
        except Exception as path_error:
            logger.warning("⚠️ 用户路径写入测试失败: %s", path_error)
            path_writable = False

        # 构建返回结果
        return {
            "status": storage_health["status"],
            "message": f"存储层服务检查完成 - {storage_health['summary']['healthy']}/{storage_health['summary']['total_services']} 服务正常",
            "timestamp": time.time(),
            "duration_ms": round((time.time() - start_time) * 1000, 2),
            "details": {
                "storage_enabled": True,
                "check_type": "service_layer",
                "user_path": {
                    "path": str(user_path),
                    "exists": user_path.exists(),
                    "writable": path_writable,
                    "user_path": user_path,
                },
                "aggregator_info": storage_health.get("aggregation_info", {}),
                "total_services": storage_health["summary"]["total_services"],
                "healthy_services": storage_health["summary"]["healthy"],
                "unhealthy_services": storage_health["summary"]["unhealthy"],
                "degraded_services": storage_health["summary"]["degraded"],
            },
        }

    except Exception as e:
        logger.error("❌ 存储层健康检查聚合失败: %s", e, exc_info=True)
        return {
            "status": "error",
            "message": f"存储层健康检查聚合异常: {e!s}",
            "timestamp": time.time(),
            "details": {
                "storage_enabled": True,
                "error": str(e),
                "fallback_used": False,
            },
        }
    # 不再 rmtree: db 文件需持久存在以匹配 _db_manager_cache 缓存的 manager.
    # 删除会导致下次 /health 命中缓存时 conversation_index 表缺失,
    # 触发 _get_conversation_statistics 的 "DB error" WARNING (conversation_service.py:653).
    # temp 目录内容幂等 (每次建同样的空表), 常驻几 KB, 无累积风险.
