"""Provider 级共享校验工具 - 被 LlmFactory / EmbeddingsFactory / 生成服务共用.

合法 provider 列表由调用方传入, 避免本模块反向依赖任一工厂模块.
"""

from __future__ import annotations

from src.inference.llm.definitions.provider_registry import require_api_key_env


def format_error_message(provider: str, error_type: str, detail: str) -> str:
    """统一错误消息格式."""
    return f"[{provider.upper()}] {error_type}: {detail}"


def validate_supported_provider(provider: str, supported: list[str]) -> None:
    """校验 provider 非空且在支持列表内.

    Args:
        provider: provider 名称
        supported: 调用方提供的合法 provider 列表 (如 SUPPORTED_LLM_PROVIDERS)

    Raises:
        ValueError: provider 为空或不支持时

    """
    if not provider:
        raise ValueError(
            format_error_message("SYSTEM", "配置错误", "provider 不能为空"),
        )

    if provider not in supported:
        raise ValueError(
            format_error_message(
                "SYSTEM",
                "配置错误",
                f"不支持的 provider: {provider}",
            ),
        )


def resolve_api_key(env_name: str | None, purpose: str) -> str:
    """读取并校验 API Key, 将 RuntimeError 包装为 ValueError.

    Args:
        env_name: 存放 API Key 的环境变量名
        purpose: 用途说明, 用于错误诊断

    Returns:
        API Key 字符串

    Raises:
        ValueError: 环境变量未设置或为空

    """
    try:
        return require_api_key_env(env_name, purpose=purpose)
    except RuntimeError as e:
        raise ValueError(str(e)) from e
