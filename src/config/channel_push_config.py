"""渠道推送 Gateway 配置模块.

管理 Assistant 经渠道网关 (如 weixin-gateway) 主动推送消息所需的连接配置.
覆盖场景: 定时消息 / 价格提醒 / 通知.

Gateway URL 是部署拓扑配置, 可由 CHANNEL_GATEWAY_URL 覆盖.
Gateway token 是凭据, 只从 credentials_registry 读取 CHANNEL_GATEWAY_TOKEN.

## config.yaml 示例

    channel_push:
      gateway:
        url: "http://127.0.0.1:18789"
"""

from __future__ import annotations

from typing import Any, override

from pydantic import BaseModel, Field

from .base_config import BaseConfig
from .config_loader import get_module_config_sync


class ChannelGatewayConfig(BaseModel):
    """渠道网关连接配置."""

    url: str = Field(
        default="http://127.0.0.1:18789",
        description="渠道网关服务地址",
    )


class ChannelPushConfig(BaseConfig):
    """渠道推送模块主配置类."""

    _module_name = "channel_push"

    gateway: ChannelGatewayConfig = Field(
        default_factory=ChannelGatewayConfig,
        description="Gateway 连接配置",
    )

    @classmethod
    @override
    def from_module_config(cls) -> ChannelPushConfig:
        """从 config.yaml 顶层 channel_push.* 块创建配置对象.

        Returns:
            配置对象实例

        """
        yaml_config = get_module_config_sync("channel_push") or {}
        return cls.from_dict(yaml_config)


# === 配置获取函数 ===

_cached: ChannelPushConfig | None = None


def get_config() -> ChannelPushConfig:
    """获取渠道推送模块配置对象(推荐方式).

    Returns:
        渠道推送配置对象实例

    """
    global _cached
    if _cached is None:
        _cached = ChannelPushConfig.from_module_config()
    return _cached


def get_default_config() -> dict[str, Any]:
    """获取渠道推送模块默认配置字典(兜底边界)."""
    return ChannelPushConfig.get_default_config()


# === 导出接口 ===

__all__ = [
    "ChannelGatewayConfig",
    "ChannelPushConfig",
    "get_config",
    "get_default_config",
]
