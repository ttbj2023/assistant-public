"""凭据注册表 - 集中管理工具与第三方服务的 API Key / Secret.

与 provider_registry.py 对称, 同样采用 dataclass 注册表模式:
- provider_registry 管理模型 Provider 的密钥(ARK/DeepSeek/OpenAI 等)
- 本模块管理工具与第三方服务的凭据(百度地图/腾讯地图/智谱/SMTP 等)

设计说明:
- 密钥不入 config.yaml, 仅通过环境变量注入(敏感信息)
- 不缓存: 与历史行为一致, 支持运行时更新环境变量
- 统一入口: 收敛散落的 os.getenv 调用, 消除同一密钥的多处重复读取
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass
class CredentialSpec:
    """凭据规格."""

    name: str
    env_var: str
    description: str = ""


CREDENTIALS: dict[str, CredentialSpec] = {
    "file_signing_secret": CredentialSpec(
        name="file_signing_secret",
        env_var="FILE_SIGNING_SECRET",
        description="文件下载 URL HMAC 签名密钥",
    ),
    "openclaw_gateway_token": CredentialSpec(
        name="openclaw_gateway_token",
        env_var="OPENCLAW_GATEWAY_TOKEN",
        description="OpenClaw Gateway 主动发消息鉴权 token",
    ),
    "baidu_api_key": CredentialSpec(
        name="baidu_api_key",
        env_var="BAIDU_API_KEY",
        description="百度 AppBuilder MCP 搜索服务 API Key",
    ),
    "zhipu_api_key": CredentialSpec(
        name="zhipu_api_key",
        env_var="ZHIPU_API_KEY",
        description="智谱网页阅读(zhipu_web_reader)的 API Key",
    ),
    "ark_agent_plan_api_key": CredentialSpec(
        name="ark_agent_plan_api_key",
        env_var="ARK_AGENT_PLAN_API_KEY",
        description="Ark Agent Plan 订阅密钥(豆包搜索使用, 复用 ark-agent-plan provider)",
    ),
    "baidu_maps_ak": CredentialSpec(
        name="baidu_maps_ak",
        env_var="BAIDU_MAPS_AK",
        description="百度地图服务 Access Key(天气/地理工具)",
    ),
    "tencent_maps_key": CredentialSpec(
        name="tencent_maps_key",
        env_var="TENCENT_MAPS_KEY",
        description="腾讯地图服务 API Key",
    ),
    "tencent_maps_sk": CredentialSpec(
        name="tencent_maps_sk",
        env_var="TENCENT_MAPS_SK",
        description="腾讯地图服务签名密钥(SK 签名认证)",
    ),
    "smtp_host": CredentialSpec(
        name="smtp_host",
        env_var="SMTP_HOST",
        description="SMTP 服务器地址(定时消息邮件渠道)",
    ),
    "smtp_username": CredentialSpec(
        name="smtp_username",
        env_var="SMTP_USERNAME",
        description="SMTP 邮件服务用户名(定时消息邮件渠道)",
    ),
    "smtp_password": CredentialSpec(
        name="smtp_password",
        env_var="SMTP_PASSWORD",
        description="SMTP 邮件服务密码",
    ),
    "smtp_from_address": CredentialSpec(
        name="smtp_from_address",
        env_var="SMTP_FROM_ADDRESS",
        description="SMTP 发件人地址",
    ),
}


def get_credential(name: str) -> str:
    """获取凭据值.

    Args:
        name: 凭据名称(CREDENTIALS 的 key)

    Returns:
        凭据值; 未设置时返回空字符串

    Raises:
        KeyError: 凭据名称未注册

    """
    spec = CREDENTIALS.get(name)
    if spec is None:
        raise KeyError(f"未注册的凭据名称: {name}")
    return os.getenv(spec.env_var, "")


def has_credential(name: str) -> bool:
    """检查凭据是否已配置.

    供工具的 is_available() 使用, 避免在多处重复 os.getenv.

    Args:
        name: 凭据名称(CREDENTIALS 的 key)

    Returns:
        凭据是否已设置(非空)

    """
    return bool(get_credential(name))


def require_credential(name: str) -> str:
    """获取必填凭据值.

    Args:
        name: 凭据名称(CREDENTIALS 的 key)

    Returns:
        凭据值

    Raises:
        RuntimeError: 凭据未配置
        KeyError: 凭据名称未注册

    """
    value = get_credential(name)
    if not value:
        spec = CREDENTIALS[name]
        raise RuntimeError(f"必需环境变量 {spec.env_var} 未设置")
    return value
