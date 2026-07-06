"""OpenClaw Gateway 配置模块.

管理 Assistant 通过 OpenClaw Gateway 主动发消息所需的连接配置.

OpenClaw Gateway URL 是部署拓扑配置, 可由 OPENCLAW_GATEWAY_URL 覆盖.
Gateway token 是凭据, 只从 credentials_registry 读取 OPENCLAW_GATEWAY_TOKEN.

## config.yaml 示例

    openclaw:
      gateway:
        url: "http://127.0.0.1:18789"
      notification_defaults:
        weixin:
          channel: "openclaw-weixin"

notification_defaults 为通知渠道系统级默认, 统一收敛原散落在
scheduled_messenger.config / price_alert.config 的 openclaw_defaults,
消除来源不一致. key = openclaw_channel_key (如 weixin).
"""

from __future__ import annotations

from typing import Any, override

from pydantic import BaseModel, Field

from .base_config import BaseConfig
from .config_loader import get_module_config_sync


class OpenClawGatewayConfig(BaseModel):
    """OpenClaw Gateway 连接配置."""

    url: str = Field(
        default="http://127.0.0.1:18789",
        description="Gateway 服务地址",
    )


class OpenClawNotificationDefaults(BaseModel):
    """通知渠道系统级默认.

    user channel config 提供 openclaw_channel_key (如 'weixin'), 此处提供
    该 key 对应的 OpenClaw 系统渠道名 (如 'openclaw-weixin').
    """

    channel: str = Field(
        default="", description="OpenClaw 系统渠道名, 如 openclaw-weixin"
    )


class OpenClawConfig(BaseConfig):
    """OpenClaw 模块主配置类."""

    _module_name = "openclaw"

    gateway: OpenClawGatewayConfig = Field(
        default_factory=OpenClawGatewayConfig,
        description="Gateway 连接配置",
    )

    notification_defaults: dict[str, OpenClawNotificationDefaults] = Field(
        default_factory=dict,
        description="通知渠道系统级默认; key=openclaw_channel_key(如 weixin)",
    )

    @classmethod
    @override
    def from_module_config(cls) -> OpenClawConfig:
        """从 config.yaml 顶层 openclaw.* 块创建配置对象.

        Returns:
            配置对象实例

        """
        yaml_config = get_module_config_sync("openclaw") or {}
        return cls.from_dict(yaml_config)


# === 配置获取函数 ===


_cached: OpenClawConfig | None = None


def get_config() -> OpenClawConfig:
    """获取 OpenClaw 模块配置对象(推荐方式).

    Returns:
        OpenClaw 配置对象实例

    """
    global _cached
    if _cached is None:
        _cached = OpenClawConfig.from_module_config()
    return _cached


def get_default_config() -> dict[str, Any]:
    """获取 OpenClaw 模块默认配置字典(兜底边界).

    Returns:
        OpenClaw 模块默认配置字典

    """
    return OpenClawConfig.get_default_config()


# === 导出接口 ===
__all__ = [
    "OpenClawConfig",
    "OpenClawGatewayConfig",
    "OpenClawNotificationDefaults",
    "get_config",
    "get_default_config",
]
