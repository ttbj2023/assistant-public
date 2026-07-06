"""兼容层: 环境变量工具函数.

配置体系 v2 不再支持通用"环境变量覆盖任意配置字段". 新代码应优先使用
runtime_env.py 或 credentials_registry.py. 本模块保留少量历史 helper, 用于旧
测试和过渡期调用.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from . import runtime_env

logger = logging.getLogger(__name__)


def get_auth_env_config() -> dict[str, Any]:
    """获取认证模块允许的运行时覆盖."""
    override = runtime_env.get_static_user_management_override()
    if override is None:
        return {}
    return {
        "auth": {
            "user_management": {
                "enable_static_user_management": override,
            },
        },
    }


def get_api_env_config() -> dict[str, Any]:
    """获取 API 模块允许的运行时覆盖."""
    api_config: dict[str, Any] = {}

    port = runtime_env.get_api_port_override()
    if port is not None:
        api_config["port"] = port

    tool_display = runtime_env.get_tool_call_display_override()
    if tool_display is not None:
        api_config.setdefault("tool_call_display", {})["enable"] = tool_display

    file_base_url = runtime_env.get_file_server_base_url()
    if file_base_url is not None:
        api_config["file_server_base_url"] = file_base_url

    ttl_days = os.getenv("FILE_URL_TTL_DAYS")
    if ttl_days is not None:
        api_config["file_url_ttl_days"] = runtime_env.get_file_url_ttl_days()

    return {"api": api_config} if api_config else {}


def get_all_env_config() -> dict[str, Any]:
    """返回允许的历史 env overlay.

    仅 auth/api 的少量运行时字段会出现在这里. 不要扩展为通用嵌套环境变量
    映射; 新字段应进入 runtime_env.py 的具名 helper.
    """
    env_config: dict[str, Any] = {}
    for get_fn in (get_auth_env_config, get_api_env_config):
        section_config = get_fn()
        for section, config in section_config.items():
            if section in env_config and isinstance(env_config[section], dict):
                env_config[section].update(config)
            else:
                env_config[section] = config
    return env_config


def get_env_var(key: str, default: Any = None, var_type: type = str) -> Any:
    """获取单个环境变量并进行基础类型转换.

    仅作为低层工具和测试辅助保留, 不代表该变量可参与应用配置覆盖.
    """
    env_value = os.getenv(key)
    if env_value is None:
        return default

    if var_type is bool:
        return env_value.lower() in {"true", "1", "yes"}
    if var_type is int:
        try:
            return int(env_value)
        except ValueError as e:
            logger.error(
                "❌ 环境变量类型转换失败: %s=%s, 期望类型: int",
                key,
                env_value,
            )
            raise ValueError(f"无效的环境变量配置: {key}={env_value}") from e
    return env_value
