"""API模块配置系统.

常规字段来自 config.yaml + Pydantic 默认值. 仅 API_PORT,
FILE_SERVER_BASE_URL,FILE_URL_TTL_DAYS,ENABLE_TOOL_CALL_DISPLAY 属于显式
runtime env 覆盖.
"""

from __future__ import annotations

from typing import Any, ClassVar, override

from pydantic import BaseModel, Field, field_validator

from . import runtime_env
from .base_config import BaseConfig
from .config_loader import get_module_config_sync


class DocumentationConfig(BaseModel):
    """文档配置"""

    enable_swagger_ui: bool = Field(default=True, description="启用Swagger UI")
    enable_redoc: bool = Field(default=True, description="启用ReDoc")
    docs_url: str = Field(default="/docs", description="Swagger文档URL")
    redoc_url: str = Field(default="/redoc", description="ReDoc文档URL")
    openapi_url: str = Field(default="/openapi.json", description="OpenAPI schema URL")


class ToolCallDisplayConfig(BaseModel):
    """工具调用显示配置 - 控制流式响应中工具调用的展示格式"""

    enable: bool = Field(
        default=True,
        description="启用工具调用显示 (Open WebUI <details> 标签格式)",
    )


class APIConfig(BaseConfig):
    """API模块主配置类"""

    _module_name = "api"

    # 基础API配置
    host: str = Field(default="127.0.0.1", description="API服务主机地址")
    port: int = Field(default=8000, ge=1, le=65535, description="API服务端口")
    file_server_base_url: str | None = Field(
        default=None,
        description="文件下载对外URL (含完整路径前缀), 为空时回退到 http://host:port/v1/files/dl",
    )
    file_url_ttl_days: int = Field(
        default=30,
        ge=0,
        description="文件下载 URL 默认有效期天数, 0 表示永久",
    )

    # CORS配置
    cors_origins: list[str] = Field(default=["*"], description="CORS允许的源")
    cors_methods: list[str] = Field(
        default=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        description="CORS允许的方法",
    )
    cors_headers: list[str] = Field(default=["*"], description="CORS允许的请求头")
    cors_allow_credentials: bool = Field(default=True, description="CORS允许凭证")

    # 嵌套配置对象
    docs: DocumentationConfig = Field(
        default_factory=DocumentationConfig,
        description="文档配置",
    )
    tool_call_display: ToolCallDisplayConfig = Field(
        default_factory=ToolCallDisplayConfig,
        description="工具调用显示配置",
    )

    # 默认配置字典
    _default_config: ClassVar[dict[str, Any]] = {
        "host": "127.0.0.1",
        "port": 8000,
        "cors_origins": ["*"],
        "cors_methods": ["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        "cors_headers": ["*"],
        "cors_allow_credentials": True,
    }

    @classmethod
    @override
    def from_module_config(cls) -> APIConfig:
        """从 config.yaml 创建配置对象, 并应用显式 runtime env 覆盖.

        Returns:
            配置对象实例

        """
        # 获取YAML配置
        yaml_config = get_module_config_sync("api") or {}

        # 合并配置: 显式 runtime env 覆盖少量部署字段
        merged_config = yaml_config.copy()
        port = runtime_env.get_api_port_override()
        if port is not None:
            merged_config["port"] = port
        file_base_url = runtime_env.get_file_server_base_url()
        if file_base_url is not None:
            merged_config["file_server_base_url"] = file_base_url
        if runtime_env.get_optional_str("FILE_URL_TTL_DAYS") is not None:
            merged_config["file_url_ttl_days"] = runtime_env.get_file_url_ttl_days()
        tool_display = runtime_env.get_tool_call_display_override()
        if tool_display is not None:
            merged_config.setdefault("tool_call_display", {})["enable"] = tool_display

        return cls.from_dict(merged_config)

    @field_validator("host")
    @classmethod
    def validate_host(cls, v: str) -> str:
        """验证主机地址"""
        if not v.strip():
            raise ValueError("host不能为空")
        return v.strip()

    def get_server_url(self) -> str:
        """获取服务器URL (监听地址)"""
        return f"http://{self.host}:{self.port}"

    def get_file_server_url(self) -> str:
        """获取文件下载对外URL, 优先使用 file_server_base_url, 回退到监听地址"""
        if self.file_server_base_url:
            return self.file_server_base_url.rstrip("/")
        return f"{self.get_server_url()}/v1/files/dl"


# === 配置获取函数 ===


_cached: APIConfig | None = None


def get_config() -> APIConfig:
    """获取API模块配置对象(推荐方式)

    Returns:
        API配置对象实例

    """
    global _cached
    if _cached is None:
        _cached = APIConfig.from_module_config()
    return _cached


def get_default_config() -> dict[str, Any]:
    """获取API模块默认配置字典(兜底边界)

    Returns:
        API模块默认配置字典

    """
    return APIConfig.get_default_config()


# === 向后兼容接口 ===
# 保持现有接口可用,避免破坏现有代码
DEFAULT_CONFIG = get_default_config()

# === 导出接口 ===
__all__ = [
    "DEFAULT_CONFIG",
    "APIConfig",
    "DocumentationConfig",
    "ToolCallDisplayConfig",
    "get_config",
    "get_default_config",
]
