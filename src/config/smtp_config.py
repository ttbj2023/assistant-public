"""SMTP 邮件发送配置模块 (系统级共享配置).

统一管理 app 进程内所有需要发邮件的组件(scheduled_messenger / 价格监控 /
NotificationService 等)的 SMTP 配置. 发件凭据(username/password)遵循治理规范
不进 config.yaml, 留空时由 resolve_credentials() 回退 credentials_registry 的
.env 变量 (SMTP_USERNAME / SMTP_PASSWORD / SMTP_FROM_ADDRESS).

## config.yaml 示例

    smtp:
      host: "smtp.qq.com"
      port: 465
      use_tls: true
      from_address: ""    # 留空回退 .env SMTP_FROM_ADDRESS
      username: ""        # 留空回退 .env SMTP_USERNAME
      password: ""        # 留空回退 .env SMTP_PASSWORD
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, override

from pydantic import Field

from .base_config import BaseConfig
from .config_loader import get_module_config_sync


class SmtpConfig(BaseConfig):
    """SMTP 系统级配置(config.yaml 顶层 smtp 段).

    username/password/from_address 留空时由 resolve_credentials() 回退
    credentials_registry 的 .env 变量, 密钥不进 config.yaml.
    """

    _module_name = "smtp"

    host: str = Field(default="", description="SMTP 服务器地址")
    port: int = Field(default=465, ge=1, le=65535, description="SMTP 服务器端口")
    use_tls: bool = Field(default=True, description="是否启用 TLS 直连")
    username: str = Field(
        default="",
        description="发件账号; 留空回退 .env SMTP_USERNAME",
    )
    password: str = Field(
        default="",
        description="发件账号密码; 留空回退 .env SMTP_PASSWORD(密钥不进 config.yaml)",
    )
    from_address: str = Field(
        default="",
        description="发件人地址; 留空回退 .env SMTP_FROM_ADDRESS, 再回退 username",
    )

    @classmethod
    @override
    def from_module_config(cls) -> SmtpConfig:
        """从 config.yaml 顶层 smtp.* 块创建配置对象."""
        yaml_config = get_module_config_sync("smtp") or {}
        return cls.from_dict(yaml_config)


@dataclass(frozen=True)
class ResolvedSmtpCredentials:
    """合并 credentials_registry 回退后的最终发信凭据."""

    host: str
    port: int
    use_tls: bool
    username: str
    password: str
    from_address: str


# === 配置获取函数 ===

_cached: SmtpConfig | None = None


def get_config() -> SmtpConfig:
    """获取 SMTP 系统级配置对象(推荐方式)."""
    global _cached
    if _cached is None:
        _cached = SmtpConfig.from_module_config()
    return _cached


def resolve_credentials() -> ResolvedSmtpCredentials:
    """返回合并 credentials_registry 回退后的最终发信凭据.

    回退链: config.yaml 字段 → .env(SMTP_HOST / SMTP_USERNAME / SMTP_PASSWORD /
    SMTP_FROM_ADDRESS). from_address 再回退到 username(多数 SMTP 服务器
    发件人即登录账号).

    设计目的: 让 config.yaml 的所有 SMTP 字段都可留空, 全部走 .env 注入,
    避免 SMTP 服务器地址/账号等半敏感信息进入版本控制.

    Returns:
        解析后的发信凭据

    """
    from .credentials_registry import get_credential

    cfg = get_config()
    host = cfg.host or get_credential("smtp_host")
    username = cfg.username or get_credential("smtp_username")
    password = cfg.password or get_credential("smtp_password")
    from_address = cfg.from_address or get_credential("smtp_from_address") or username
    return ResolvedSmtpCredentials(
        host=host,
        port=cfg.port,
        use_tls=cfg.use_tls,
        username=username,
        password=password,
        from_address=from_address,
    )


def is_configured() -> bool:
    """SMTP 是否配置完整(host + username + password 均非空).

    供工具 is_available() 门控判断邮件渠道可用性.
    """
    creds = resolve_credentials()
    return bool(creds.host and creds.username and creds.password)


def get_default_config() -> dict[str, Any]:
    """获取 SMTP 模块默认配置字典(兜底边界)."""
    return SmtpConfig.get_default_config()


# === 导出接口 ===
__all__ = [
    "ResolvedSmtpCredentials",
    "SmtpConfig",
    "get_config",
    "get_default_config",
    "is_configured",
    "resolve_credentials",
]
