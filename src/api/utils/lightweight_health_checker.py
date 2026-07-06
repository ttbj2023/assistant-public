"""轻量级健康检查工具

提供不加载模型实例的健康检查功能,专注于配置和依赖可用性检查.
"""

from __future__ import annotations

import importlib.util
import logging
from typing import Any

from src.config.inference_config import get_config as get_inference_config
from src.inference.llm.definitions import get_provider_config, is_provider_supported
from src.inference.llm.definitions.model_metadata_checker import (
    check_model_metadata,
)

logger = logging.getLogger(__name__)


def check_dependency_availability(dependency_name: str) -> bool:
    """检查Python依赖是否可用.

    Args:
        dependency_name: 依赖模块名称

    Returns:
        依赖是否可用

    """
    return importlib.util.find_spec(dependency_name) is not None


def check_provider_requirements(provider: str) -> dict[str, Any]:
    """检查特定provider的要求.

    Args:
        provider: provider名称 (local, openai)

    Returns:
        provider要求检查结果

    """
    requirements: dict[str, dict[str, Any]] = {
        "local": {
            "dependencies": [],
            "env_vars": [],
            "optional": True,
            "description": "本地模型,通过外部工具处理",
        },
        "openai": {
            "dependencies": ["openai"],
            "env_vars": ["OPENAI_API_KEY"],
            "optional": False,
            "description": "OpenAI API嵌入模型",
        },
    }

    req = requirements.get(
        provider,
        {"dependencies": [], "env_vars": [], "optional": True},
    )

    # 检查依赖
    missing_deps = []
    for dep in req.get("dependencies", []):
        if not check_dependency_availability(dep):
            missing_deps.append(dep)

    # 检查环境变量
    missing_env_vars = []
    for env_var in req.get("env_vars", []):
        if not _is_registered_env_available(provider, env_var):
            missing_env_vars.append(env_var)

    return {
        "provider": provider,
        "description": req.get("description", ""),
        "dependencies_available": len(missing_deps) == 0,
        "missing_dependencies": missing_deps,
        "env_vars_available": len(missing_env_vars) == 0,
        "missing_env_vars": missing_env_vars,
        "optional": req.get("optional", True),
        "overall_available": len(missing_deps) == 0 and len(missing_env_vars) == 0,
    }


def _is_registered_env_available(provider: str, env_var: str) -> bool:
    """检查已登记 provider 的 API Key 是否可用."""
    if not is_provider_supported(provider):
        return False
    provider_cfg = get_provider_config(provider)
    if provider_cfg.api_key_env != env_var:
        return False
    return bool(provider_cfg.get_api_key())


def check_embedding_model_availability(model_id: str) -> dict[str, Any]:
    """轻量级嵌入模型可用性检查.

    Args:
        model_id: 嵌入模型ID

    Returns:
        嵌入模型可用性检查结果

    """
    if not model_id:
        return {
            "available": False,
            "model_id": model_id,
            "error": "未配置嵌入模型ID",
            "check_type": "lightweight",
        }

    # 解析模型ID
    if ":" not in model_id:
        return {
            "available": False,
            "model_id": model_id,
            "error": "模型ID格式错误,应为 'provider:model_name'",
            "check_type": "lightweight",
        }

    provider = model_id.split(":", 1)[0]

    # 检查模型元数据
    metadata_check = check_model_metadata(model_id)
    if not metadata_check["available"]:
        return {
            **metadata_check,
            "check_type": "lightweight",
            "note": "模型元数据检查失败",
        }

    # 检查provider要求
    provider_check = check_provider_requirements(provider)

    # 判断整体可用性
    # 对于可选provider,如果元数据可用就认为基本可用
    if provider_check["optional"]:
        overall_available = metadata_check["available"]
    else:
        overall_available = (
            metadata_check["available"] and provider_check["overall_available"]
        )

    return {
        "available": overall_available,
        "model_id": model_id,
        "metadata": metadata_check,
        "provider": provider_check,
        "check_type": "lightweight",
        "note": "仅检查配置和依赖,未加载模型实例",
    }


def get_inference_model_config() -> dict[str, Any]:
    """获取推理模型配置.

    Returns:
        推理模型配置信息

    """
    try:
        inference_config = get_inference_config()
        model_config = inference_config.get("model", {})

        llm_model_id = model_config.get("model_id", "")
        embedding_model_id = model_config.get("embedding_model_id", "")

        return {
            "available": True,
            "llm_model_id": llm_model_id,
            "embedding_model_id": embedding_model_id,
            "config_complete": bool(llm_model_id and embedding_model_id),
        }

    except Exception as e:
        logger.debug("推理配置可用性检查失败: %s", e)
        return {
            "available": False,
            "error": f"推理配置获取失败: {e!s}",
            "llm_model_id": "",
            "embedding_model_id": "",
            "config_complete": False,
        }

    try:
        # 获取模型配置
        config_result = get_inference_model_config()

        if not config_result["available"]:
            return {
                "status": "unhealthy",
                "message": "推理配置不可用",
                "details": config_result,
                "check_type": "lightweight",
            }

        # 检查嵌入模型
        embedding_result = check_embedding_model_availability(
            config_result["embedding_model_id"],
        )

        # 智能判断整体健康状态
        embedding_available = embedding_result["available"]
        embedding_configured = bool(config_result.get("embedding_model_id"))

        # 优化状态判断逻辑
        if embedding_available or not embedding_configured:
            status = "healthy"
            message = "推理功能正常" + (
                "(嵌入模型可用)" if embedding_available else "(嵌入模型未配置)"
            )
        else:
            status = "unhealthy"
            message = "推理功能不可用(嵌入模型已配置但不可用)"

        return {
            "status": status,
            "message": message,
            "details": {
                "config": config_result,
                "embedding": embedding_result,
            },
            "check_type": "lightweight",
            "note": "轻量级检查,未加载任何模型实例",
        }

    except Exception as e:
        logger.error("轻量级推理健康检查失败: %s", e)
        return {
            "status": "unhealthy",
            "message": f"推理健康检查失败: {e!s}",
            "details": {"error": str(e)},
            "check_type": "lightweight",
        }
