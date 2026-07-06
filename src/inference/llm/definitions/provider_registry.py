"""Provider配置管理模块.

提供独立的Provider配置管理,避免循环依赖问题.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any


@dataclass
class ProviderConfig:
    """Provider配置信息."""

    # 基础配置
    name: str
    base_url: str | None = None
    base_url_env: str | None = None  # 环境变量名
    api_key_env: str | None = None
    requires_auth: bool = True

    # 描述信息
    description: str = ""

    def get_effective_base_url(self) -> str | None:
        """获取有效的base_url,优先使用环境变量."""
        if self.base_url_env:
            env_value = os.getenv(self.base_url_env)
            if env_value:
                return env_value
        return self.base_url

    def get_api_key(self) -> str:
        """读取 Provider API Key. 未配置或无需鉴权时返回空字符串."""
        if not self.api_key_env:
            return ""
        return os.getenv(self.api_key_env, "")

    def require_api_key(self) -> str:
        """读取必需 Provider API Key."""
        api_key = self.get_api_key()
        if not api_key:
            raise RuntimeError(f"需要设置 {self.api_key_env} 环境变量")
        return api_key

    def to_dict(self) -> dict[str, Any]:
        """转换为字典格式."""
        config = {
            "name": self.name,
            "requires_auth": self.requires_auth,
            "description": self.description,
        }
        if self.base_url:
            config["base_url"] = self.base_url
        if self.base_url_env:
            config["base_url_env"] = self.base_url_env
        if self.api_key_env:
            config["api_key_env"] = self.api_key_env
        return config


# Provider配置注册表
PROVIDER_CONFIGS = {
    "local": ProviderConfig(
        name="local",
        base_url="http://localhost:11434/v1",
        base_url_env="LOCAL_BASE_URL",
        api_key_env=None,
        requires_auth=False,
        description="本地Ollama模型服务(ChatOllama, 原生工具调用支持)",
    ),
    "local-embedding": ProviderConfig(
        name="local-embedding",
        base_url="http://localhost:11434/v1",
        base_url_env="LOCAL_EMBEDDING_BASE_URL",
        api_key_env=None,
        requires_auth=False,
        description="本地嵌入服务(Ollama bge-m3, 纯CPU)",
    ),
    "openai": ProviderConfig(
        name="openai",
        base_url="https://api3.wlai.vip/v1",
        base_url_env="OPENAI_BASE_URL",
        api_key_env="OPENAI_API_KEY",
        requires_auth=True,
        description="OpenAI兼容服务(包括api3.wlai.vip)",
    ),
    "deepseek": ProviderConfig(
        name="deepseek",
        base_url="https://api.deepseek.com/v1",
        base_url_env="DEEPSEEK_BASE_URL",
        api_key_env="DEEPSEEK_API_KEY",
        requires_auth=True,
        description="DeepSeek官方服务",
    ),
    "gemini": ProviderConfig(
        name="gemini",
        base_url=None,
        base_url_env="GEMINI_BASE_URL",
        api_key_env="GEMINI_API_KEY",
        requires_auth=True,
        description="Google Gemini服务(原生SDK langchain-google-genai, 端点经GEMINI_BASE_URL配置)",
    ),
    "minimax": ProviderConfig(
        name="minimax",
        base_url="https://api.minimaxi.com/anthropic",
        base_url_env="MINIMAX_BASE_URL",
        api_key_env="MINIMAX_API_KEY",
        requires_auth=True,
        description="MiniMax AI服务(M2.7, Anthropic兼容端点)",
    ),
    "dashscope": ProviderConfig(
        name="dashscope",
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        base_url_env="DASHSCOPE_BASE_URL",
        api_key_env="DASHSCOPE_API_KEY",
        requires_auth=True,
        description="阿里云百炼DashScope服务(OpenAI兼容端点, Qwen系列模型)",
    ),
    "doubao": ProviderConfig(
        name="doubao",
        base_url="https://ark.cn-beijing.volces.com/api/v3",
        base_url_env="DOUBAO_BASE_URL",
        api_key_env="ARK_API_KEY",
        requires_auth=True,
        description="字节跳动火山引擎Ark服务(OpenAI兼容端点, 豆包系列模型)",
    ),
    "ark-agent-plan": ProviderConfig(
        name="ark-agent-plan",
        base_url="https://ark.cn-beijing.volces.com/api/plan/v3",
        base_url_env="ARK_AGENT_PLAN_BASE_URL",
        api_key_env="ARK_AGENT_PLAN_API_KEY",
        requires_auth=True,
        description="火山引擎Agent Plan订阅(OpenAI兼容端点, 多模型统一订阅)",
    ),
    "aliyun-token-plan": ProviderConfig(
        name="aliyun-token-plan",
        base_url="https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
        base_url_env="ALIYUN_TOKEN_PLAN_BASE_URL",
        api_key_env="ALIYUN_TOKEN_PLAN_API_KEY",
        requires_auth=True,
        description="阿里云百炼Token Plan订阅(OpenAI兼容端点, Qwen3.7系列/DeepSeek三方模型)",
    ),
    "scnet": ProviderConfig(
        name="scnet",
        base_url="https://api.scnet.cn/api/llm/v1",
        base_url_env="SCNET_BASE_URL",
        api_key_env="SCNET_API_KEY",
        requires_auth=True,
        description="超算互联网(scnet.cn)多模型聚合订阅(OpenAI兼容端点, Kimi/MiniMax/MiMo/GLM等)",
    ),
}


def get_provider_config(provider_name: str) -> ProviderConfig:
    """获取Provider配置对象."""
    config = PROVIDER_CONFIGS.get(provider_name.lower())
    if not config:
        raise ValueError(f"不支持的Provider: {provider_name}")
    return config


def is_provider_supported(provider_name: str) -> bool:
    """检查Provider是否支持."""
    return provider_name.lower() in PROVIDER_CONFIGS


def register_provider(provider_name: str, config: ProviderConfig) -> None:
    """注册新的Provider配置."""
    PROVIDER_CONFIGS[provider_name.lower()] = config


def list_providers() -> list[str]:
    """列出所有可用的Provider."""
    return list(PROVIDER_CONFIGS.keys())


def require_api_key_env(env_name: str | None, *, purpose: str = "Provider") -> str:
    """读取动态 API Key 环境变量名.

    用于模型元数据已经给出 env_name, 但调用点不需要额外 ProviderConfig 的场景.
    """
    if not env_name:
        raise RuntimeError(f"{purpose} 缺少 API Key 环境变量配置")
    api_key = os.getenv(env_name, "")
    if not api_key:
        raise RuntimeError(f"需要设置 {env_name} 环境变量")
    return api_key
