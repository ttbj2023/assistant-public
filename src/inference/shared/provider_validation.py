"""Provider 级共享校验工具 - 被 LlmFactory / EmbeddingsFactory 共用.

纯函数模块, 不持有任何业务常量: 合法 provider 列表由调用方传入,
避免本模块反向依赖任一工厂模块.
"""

from __future__ import annotations


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
