"""Agent模块轻量级健康检查器.

专注于基础设施准备度检查,避免重量级操作,确保200ms内完成.

设计原则:
- 只检查Agent系统是否能够启动(不检查是否完美运行)
- 避免Agent实例化,LLM调用,数据库连接等重量级操作
- 使用文件系统检查,导入路径验证,YAML解析等轻量级操作
- 遵循现有健康检查模式,集成到系统健康监控

检查项目:
- 配置文件可访问性
- 目录结构完整性
- 导入路径可用性
- YAML格式有效性
- 模型元数据可用性
- 核心依赖可用性
"""

from __future__ import annotations

import importlib.util
import logging
import time
from pathlib import Path
from typing import Any

import yaml

from src.inference.llm.definitions.model_metadata_checker import (
    check_model_metadata,
)

logger = logging.getLogger(__name__)


def check_agent_yaml_config(agent_id: str) -> dict[str, Any]:
    """轻量级Agent配置文件检查.

    只检查配置文件存在性,可读性和基本YAML格式,不进行完整验证.

    Args:
        agent_id: Agent标识符

    Returns:
        配置检查结果字典

    """
    try:
        # 转换Agent ID到目录名
        from src.agent.agents_implementations import get_agent_directory

        directory_name = get_agent_directory(agent_id)
        config_path = Path(
            f"src/agent/agents_implementations/{directory_name}/agent.yaml",
        )

        # 检查文件存在性
        if not config_path.exists():
            return {
                "available": False,
                "error": f"Agent配置文件不存在: {config_path}",
                "file_exists": False,
            }

        # 检查文件可读性
        if not config_path.is_file():
            return {
                "available": False,
                "error": f"Agent配置路径不是文件: {config_path}",
                "file_exists": True,
                "is_file": False,
            }

        # 轻量级YAML解析(仅解析格式,不验证内容)
        try:
            with config_path.open("r", encoding="utf-8") as f:
                content = f.read()
                config = yaml.safe_load(content)

        except yaml.YAMLError as e:
            return {
                "available": False,
                "error": f"YAML格式错误: {e}",
                "file_exists": True,
                "is_file": True,
                "yaml_valid": False,
            }
        except Exception as e:
            logger.debug("Agent配置文件读取失败(%s): %s", agent_id, e)
            return {
                "available": False,
                "error": f"文件读取错误: {e}",
                "file_exists": True,
                "is_file": True,
            }

        # 检查必需字段存在性(不验证字段值的有效性)
        required_fields = ["agent_id", "name", "description"]
        missing_fields = [field for field in required_fields if not config.get(field)]

        return {
            "available": len(missing_fields) == 0,
            "file_exists": True,
            "is_file": True,
            "yaml_valid": True,
            "has_required_fields": len(missing_fields) == 0,
            "missing_fields": missing_fields,
            "config_size_bytes": len(content),
            "config_entries_count": len(config) if isinstance(config, dict) else 0,
        }

    except Exception as e:
        logger.error("Agent配置检查失败 %s: %s", agent_id, e)
        return {"available": False, "error": f"配置检查异常: {e}"}


def check_agent_import_path(agent_id: str) -> dict[str, Any]:
    """轻量级Agent导入路径检查.

    使用importlib.util.find_spec检查模块可导入性,不实际导入模块.

    Args:
        agent_id: Agent标识符

    Returns:
        导入路径检查结果字典

    """
    try:
        # 转换Agent ID到目录名(personal-assistant -> personal_assistant)
        from src.agent.agents_implementations import get_agent_directory

        directory_name = get_agent_directory(agent_id)

        # 检查主要的Agent实现模块
        main_module_path = f"src.agent.agents_implementations.{directory_name}.main"
        main_spec = importlib.util.find_spec(main_module_path)

        result = {
            "available": main_spec is not None,
            "main_module_available": main_spec is not None,
            "main_module_path": main_module_path,
            "directory_name": directory_name,
        }

        if main_spec is None:
            result["error"] = f"Agent主模块不可导入: {main_module_path}"
            result["error_details"] = (
                f"ModuleSpec not found for path: {main_module_path}"
            )
        else:
            result["module_origin"] = getattr(main_spec, "origin", "Unknown origin")

        return result

    except Exception as e:
        logger.error("Agent导入路径检查失败 %s: %s", agent_id, e)
        return {
            "available": False,
            "main_module_available": False,
            "error": f"导入路径检查异常: {e}",
            "error_details": str(type(e).__name__),
            "main_module_path": f"src.agent.agents_implementations.{agent_id}.main",
            "directory_name": agent_id.replace("-", "_"),
        }


def check_agent_directory_structure(agent_id: str) -> dict[str, Any]:
    """检查Agent目录结构完整性.

    Args:
        agent_id: Agent标识符

    Returns:
        目录结构检查结果字典

    """
    try:
        # 转换Agent ID到目录名
        from src.agent.agents_implementations import get_agent_directory

        directory_name = get_agent_directory(agent_id)
        agent_dir = Path(f"src/agent/agents_implementations/{directory_name}")

        # 检查Agent目录存在性
        if not agent_dir.exists():
            return {
                "available": False,
                "error": f"Agent目录不存在: {agent_dir}",
                "directory_exists": False,
            }

        # 检查是否为目录
        if not agent_dir.is_dir():
            return {
                "available": False,
                "error": f"Agent路径不是目录: {agent_dir}",
                "directory_exists": True,
                "is_directory": False,
            }

        # 检查关键文件存在性
        config_file = agent_dir / "agent.yaml"
        main_file = agent_dir / "main.py"

        key_files = {"agent.yaml": config_file.exists(), "main.py": main_file.exists()}

        missing_files = [name for name, exists in key_files.items() if not exists]

        return {
            "available": len(missing_files) == 0,
            "directory_exists": True,
            "is_directory": True,
            "key_files": key_files,
            "missing_files": missing_files,
            "directory_path": str(agent_dir),
        }

    except Exception as e:
        logger.error("Agent目录结构检查失败 %s: %s", agent_id, e)
        return {"available": False, "error": f"目录结构检查异常: {e}"}


def check_core_dependencies() -> dict[str, Any]:
    """检查核心依赖可用性.

    Returns:
        核心依赖检查结果字典

    """
    try:
        # 检查关键Python包的可导入性
        dependencies = {
            "yaml": "yaml",
            "pydantic": "pydantic",
            "langchain": "langchain",
            "chromadb": "chromadb",
        }

        dependency_status = {}
        for name, module in dependencies.items():
            try:
                spec = importlib.util.find_spec(module)
                dependency_status[name] = {
                    "available": spec is not None,
                    "module_name": module,
                }
            except Exception as e:
                logger.debug("依赖模块检查失败(%s): %s", module, e)
                dependency_status[name] = {
                    "available": False,
                    "error": str(e),
                    "module_name": module,
                }

        available_count = sum(
            1 for status in dependency_status.values() if status.get("available", False)
        )
        total_count = len(dependencies)

        return {
            "available": available_count == total_count,
            "total_dependencies": total_count,
            "available_dependencies": available_count,
            "dependency_status": dependency_status,
            "availability_ratio": available_count / total_count
            if total_count > 0
            else 0,
        }

    # UNIT_TEST_EXEMPT: 外部防御性异常处理, 内部循环已捕获所有已知异常
    except Exception as e:
        logger.error("核心依赖检查失败: %s", e)
        return {"available": False, "error": f"依赖检查异常: {e}"}


def lightweight_agent_health_check() -> dict[str, Any]:
    """Agent模块轻量级健康检查主函数.

    执行所有轻量级检查,确保在200ms内完成.

    Returns:
        完整的Agent健康检查结果字典

    """
    start_time = time.time()

    try:
        logger.debug("开始Agent模块轻量级健康检查")

        # 获取可用的Agent列表(轻量级方式)
        try:
            from src.agent.agents_implementations import get_available_agents

            available_agent_ids = get_available_agents()
        except Exception as e:
            logger.error("获取Agent列表失败: %s", e)
            available_agent_ids = ["personal_assistant"]  # 回退到默认Agent

        health_results = {
            "agent_system": {
                "available": True,
                "message": "Agent系统健康检查完成",
                "agents_checked": len(available_agent_ids),
                "agent_ids": available_agent_ids,
            },
            "agents": {},
            "system_checks": {},
            "agents_checked": len(available_agent_ids),
            "healthy_agents_count": 0,
            "unhealthy_agents": [],
            "health_ratio": 0,
        }

        # 并行执行各项检查
        checks_to_perform = [
            ("core_dependencies", check_core_dependencies),
        ]

        # 执行系统级检查
        for check_name, check_func in checks_to_perform:
            try:
                health_results["system_checks"][check_name] = check_func()
            # UNIT_TEST_EXEMPT: 防御性, 当前check_core_dependencies自身已处理异常
            except Exception as e:
                logger.error("系统级检查失败 %s: %s", check_name, e)
                health_results["system_checks"][check_name] = {
                    "available": False,
                    "error": f"检查执行失败: {e}",
                }

        # 检查每个Agent(轻量级检查)
        unhealthy_agents = []
        for agent_id in available_agent_ids:
            try:
                agent_checks = {
                    "directory_structure": check_agent_directory_structure(agent_id),
                    "yaml_config": check_agent_yaml_config(agent_id),
                    "import_path": check_agent_import_path(agent_id),
                }

                # 注意:在API架构下,不需要检查模型元数据
                # 模型可用性由API端点和配置保证,运行时通过错误处理处理模型问题

                # 判断Agent整体健康状态
                # 只有核心检查失败才算不健康:目录结构,YAML配置,导入路径
                core_checks = ["directory_structure", "yaml_config", "import_path"]
                agent_healthy = all(
                    agent_checks.get(check_name, {}).get("available", False)
                    for check_name in core_checks
                )

                if not agent_healthy:
                    unhealthy_agents.append(agent_id)

                health_results["agents"][agent_id] = {
                    "healthy": agent_healthy,
                    "checks": agent_checks,
                    "healthy_core_checks": [
                        name
                        for name in core_checks
                        if agent_checks.get(name, {}).get("available", False)
                    ],
                    "failed_core_checks": [
                        name
                        for name in core_checks
                        if not agent_checks.get(name, {}).get("available", False)
                    ],
                }

            except Exception as e:
                logger.error("Agent %s 健康检查失败: %s", agent_id, e)
                unhealthy_agents.append(agent_id)
                health_results["agents"][agent_id] = {
                    "healthy": False,
                    "error": f"健康检查执行失败: {e}",
                }

        # 计算整体健康状态
        total_agents = len(available_agent_ids)
        healthy_agents = total_agents - len(unhealthy_agents)

        # 判断系统整体状态
        core_deps_healthy = health_results["system_checks"]["core_dependencies"].get(
            "available",
            False,
        )

        if not core_deps_healthy:
            overall_status = "unhealthy"
            overall_message = "核心依赖不可用"
        elif len(unhealthy_agents) == 0:
            overall_status = "healthy"
            overall_message = "所有Agent系统组件健康"
        elif healthy_agents > 0:
            overall_status = "degraded"
            overall_message = f"部分Agent不可用: {unhealthy_agents}"
        else:
            overall_status = "unhealthy"
            overall_message = "所有Agent都不可用"

        execution_time = (time.time() - start_time) * 1000

        health_results.update({
            "overall_status": overall_status,
            "overall_message": overall_message,
            "execution_time_ms": round(execution_time, 2),
            "healthy_agents_count": healthy_agents,
            "unhealthy_agents": unhealthy_agents,
            "health_ratio": healthy_agents / total_agents if total_agents > 0 else 0,
        })

        logger.info(f"Agent健康检查完成: {overall_status} ({execution_time:.2f}ms)")

        return health_results

    # UNIT_TEST_EXEMPT: 最外层防御性异常处理, 内部所有已知操作已被内层捕获
    except Exception as e:
        execution_time = (time.time() - start_time) * 1000
        logger.error("Agent健康检查执行失败: %s", e)

        return {
            "overall_status": "unhealthy",
            "overall_message": f"健康检查执行失败: {e}",
            "execution_time_ms": round(execution_time, 2),
            "agent_system": {"available": False, "error": str(e)},
            "agents": {},
            "system_checks": {},
            "healthy_agents_count": 0,
            "unhealthy_agents": [],
            "health_ratio": 0,
            "agents_checked": 0,
        }


# 导出主要接口
__all__ = [
    "check_agent_directory_structure",
    "check_agent_import_path",
    "check_agent_yaml_config",
    "check_core_dependencies",
    "check_model_metadata",
    "lightweight_agent_health_check",
]
