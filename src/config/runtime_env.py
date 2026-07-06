"""运行时环境变量白名单.

本模块只管理部署拓扑,调试/测试开关和少量启动时覆盖项. 业务配置应放在
config.yaml, 密钥应放在 credentials_registry/provider_registry.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Final

logger = logging.getLogger(__name__)

_TRUE_VALUES: Final[set[str]] = {"true", "1", "yes", "on"}
_FALSE_VALUES: Final[set[str]] = {"false", "0", "no", "off", ""}

ALLOWED_RUNTIME_ENV_VARS: Final[frozenset[str]] = frozenset({
    "ENVIRONMENT",
    "DEBUG",
    "BASE_DATA_PATH",
    "PYTEST_XDIST_WORKER_ID",
    "TEST_PROCESS_PREFIX",
    "API_PORT",
    "ENABLE_STATIC_USER_MANAGEMENT",
    "ENABLE_TOOL_CALL_DISPLAY",
    "FILE_SERVER_BASE_URL",
    "FILE_URL_TTL_DAYS",
    "TOOL_RUNTIME_BASE_URL",
    "QUOTE_SERVICE_BASE_URL",
    "OPENCLAW_GATEWAY_URL",
})


def _getenv(name: str) -> str | None:
    """读取已登记的运行时环境变量."""
    if name not in ALLOWED_RUNTIME_ENV_VARS:
        raise KeyError(f"未登记的运行时环境变量: {name}")
    return os.getenv(name)


def get_str(name: str, default: str = "") -> str:
    """读取字符串环境变量."""
    value = _getenv(name)
    return default if value is None else value


def get_optional_str(name: str) -> str | None:
    """读取可选字符串环境变量."""
    value = _getenv(name)
    return value if value not in (None, "") else None


def get_bool(name: str, default: bool = False) -> bool:
    """读取布尔环境变量."""
    value = _getenv(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in _TRUE_VALUES:
        return True
    if normalized in _FALSE_VALUES:
        return False
    logger.warning("布尔环境变量 %s=%r 无法识别, 使用 False", name, value)
    return False


def get_int(name: str, default: int) -> int:
    """读取整数环境变量."""
    value = _getenv(name)
    if value is None or value == "":
        return default
    try:
        return int(value)
    except ValueError as e:
        raise ValueError(f"无效的整数环境变量: {name}={value}") from e


def get_url(name: str, default: str) -> str:
    """读取 URL/服务地址并去掉尾部斜杠."""
    return get_str(name, default).rstrip("/")


def get_environment() -> str:
    """当前运行环境."""
    return get_str("ENVIRONMENT", "development").lower()


def is_test_environment() -> bool:
    """是否测试环境."""
    return get_environment() in {"testing", "test"}


def is_debug_enabled() -> bool:
    """统一 DEBUG 开关."""
    return get_bool("DEBUG", False)


def get_pytest_worker_id() -> str | None:
    """pytest-xdist worker id."""
    return get_optional_str("PYTEST_XDIST_WORKER_ID")


def get_test_process_prefix() -> str:
    """测试进程隔离前缀.

    quick 模式下 run_test_suite 为单元/集成测试子进程分别注入不同前缀,
    避免两个独立 pytest 进程因相同 worker_id 写入同一 test_data 目录。
    默认空字符串, 保持现有命名兼容。
    """
    return get_optional_str("TEST_PROCESS_PREFIX") or ""


def test_data_dir_name(worker_id: str | None) -> str:
    """组装当前测试进程使用的测试数据目录名 (SSOT).

    Args:
        worker_id: pytest-xdist worker id; None 表示 master/非 xdist 进程。

    Returns:
        相对当前工作目录的测试数据目录名。
    """
    prefix = get_test_process_prefix()
    if worker_id:
        return (
            f"./test_data_{prefix}_worker_{worker_id}"
            if prefix
            else f"./test_data_worker_{worker_id}"
        )
    return f"./test_data_{prefix}" if prefix else "./test_data"


def test_data_cleanup_patterns() -> list[str]:
    """返回当前测试进程应清理的目录 pattern 列表.

    只包含本进程前缀的目录, 绝不触碰其他并行 pytest 进程的前缀目录,
    避免并发运行时的误删竞态。
    """
    prefix = get_test_process_prefix()
    if prefix:
        return [f"test_data_{prefix}_worker_*", f"test_data_{prefix}"]
    return ["test_data_worker_*", "test_data"]


def get_base_data_path() -> Path:
    """用户数据根目录."""
    return Path(get_str("BASE_DATA_PATH", "./data"))


def get_api_port_override() -> int | None:
    """API 端口运行时覆盖."""
    value = get_optional_str("API_PORT")
    if value is None:
        return None
    try:
        return int(value)
    except ValueError as e:
        raise ValueError(f"无效的 API_PORT 配置: {value}") from e


def get_static_user_management_override() -> bool | None:
    """静态用户管理运行时覆盖."""
    if get_optional_str("ENABLE_STATIC_USER_MANAGEMENT") is None:
        return None
    return get_bool("ENABLE_STATIC_USER_MANAGEMENT", True)


def get_tool_call_display_override() -> bool | None:
    """工具调用展示运行时覆盖."""
    if get_optional_str("ENABLE_TOOL_CALL_DISPLAY") is None:
        return None
    return get_bool("ENABLE_TOOL_CALL_DISPLAY", True)


def get_file_server_base_url() -> str | None:
    """文件下载对外 URL 覆盖."""
    value = get_optional_str("FILE_SERVER_BASE_URL")
    return value.rstrip("/") if value else None


def get_file_url_ttl_days(default: int = 30) -> int:
    """文件下载链接有效期."""
    return get_int("FILE_URL_TTL_DAYS", default)


def get_tool_runtime_base_url() -> str:
    """tool-runtime 服务地址."""
    return get_url("TOOL_RUNTIME_BASE_URL", "http://127.0.0.1:8766")


def get_quote_service_base_url() -> str | None:
    """quote-service 行情查询服务地址."""
    return get_optional_str("QUOTE_SERVICE_BASE_URL")


def get_openclaw_gateway_url() -> str | None:
    """OpenClaw Gateway 服务地址."""
    value = get_optional_str("OPENCLAW_GATEWAY_URL")
    return value.rstrip("/") if value else None
