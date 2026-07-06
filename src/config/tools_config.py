"""Tools模块配置系统.

配置体系 v2 将工具定义分为两层:
- 内置工具 catalog (`tool_catalog.py`): class_path,默认描述,默认组成员.
- config.yaml overlay: enabled,timeout,config,prompt_hint,MCP,skills 和额外工具定义.

工具配置不再支持通用环境变量覆盖; 部署拓扑和密钥分别走 runtime_env.py
与 credentials_registry/provider_registry.
"""

from __future__ import annotations

import copy
import logging
import os
import re
from typing import Any, ClassVar, Literal, override

from pydantic import BaseModel, Field, field_validator

from .base_config import BaseConfig
from .config_loader import get_module_config_sync
from .tool_catalog import get_builtin_tools_config

logger = logging.getLogger(__name__)


def _deep_merge_dict(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """递归合并两个配置字典.

    override 覆盖 base: dict 子键递归合并, 其余类型(标量/list)整体替换.
    用于工具/skill/group 的 per-item 字段级合并, 使 config 子 dict 也能按字段覆盖,
    而非整体替换丢失默认值.

    Args:
        base: 默认配置(或已合并的底层)
        override: 高优先级覆盖项

    Returns:
        合并后的新字典(不修改入参)

    """
    result = {**base}
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge_dict(result[key], value)
        else:
            result[key] = value
    return result


class InternalToolConfig(BaseModel):
    """内部工具配置"""

    name: str = Field(description="工具名称")
    class_path: str = Field(description="工具类路径")
    enabled: bool = Field(default=True, description="是否启用")
    timeout: float = Field(default=30.0, gt=0, description="工具超时时间(秒)")
    description: str = Field(default="", description="工具描述")
    config: dict[str, Any] = Field(default_factory=dict, description="工具特定配置参数")
    prompt_hint: str = Field(
        default="",
        description="注入系统提示词的策略引导(跨工具协调/使用优先级; 非工具描述)",
    )
    skip_when_capabilities: list[str] = Field(
        default_factory=list,
        description="主对话模型具备这些能力时跳过注入该工具(如 image_input)",
    )

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        """验证工具名称"""
        if not v or not v.strip():
            raise ValueError("工具名称不能为空")
        return v.strip()

    @field_validator("class_path")
    @classmethod
    def validate_class_path(cls, v: str) -> str:
        """验证工具类路径"""
        if not v or not v.strip():
            raise ValueError("工具类路径不能为空")
        return v.strip()


class ToolGroupConfig(BaseModel):
    """工具组配置 - 将多个细粒度子工具打包为一个检索/激活单元.

    两种放置方式:
    - 休眠组(optional_tools引用): 组作为检索单元(一句summary), 命中后整组成员激活注入,
      避免相似子工具分散命中导致的检索混淆.
    - 常驻组(tools引用): 直接整组激活(全量注入), 不走检索, 组仅作配置整洁与机制统一.

    组对主对话模型透明: LLM只感知被注入的具体子工具, 从不调用组名.
    """

    name: str = Field(description="组名称")
    summary: str = Field(
        description="组摘要(一句话, 拼接到search_available_tools描述列表)",
    )
    description: str = Field(
        default="",
        description="组能力描述(2-3行), 同时供 search_available_tools 初筛子串匹配(2.0分)和工具筛选LLM去噪; 为空则回退用summary",
    )
    keywords: list[str] = Field(
        default_factory=list,
        description="检索关键词(组级聚合词, 成员子工具不再各自配检索词)",
    )
    members: list[str] = Field(description="组成员工具名列表")
    prompt_hint: str = Field(
        default="",
        description="注入系统提示词的组级策略引导(非工具描述)",
    )

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        """验证组名称: 非空且以 _group 结尾.

        _group 后缀是命名约定, 保证 display_label 派生可靠, 并使组名与
        普通工具名在命名上严格二分.
        """
        if not v or not v.strip():
            raise ValueError("组名称不能为空")
        v = v.strip()
        if not v.endswith("_group"):
            raise ValueError("组名称必须以 '_group' 结尾")
        return v

    @property
    def display_label(self) -> str:
        """对外展示标签(组名去 _group 后缀).

        工具组对主对话模型透明: 组名仅作内部标识符(配置key/agent.yaml引用/
        middleware映射), 所有面向模型的文本(search可发现清单/prompt_hint前缀)
        统一用本标签替代组名, 避免模型把组名误当作可调用或可加载的实体.
        """
        return self.name.removesuffix("_group")

    @field_validator("members")
    @classmethod
    def validate_members(cls, v: list[str]) -> list[str]:
        """验证成员列表非空"""
        if not v:
            raise ValueError("组成员列表不能为空")
        return v


class McpServerConfig(BaseModel):
    """MCP服务器配置"""

    name: str = Field(description="服务器名称")
    transport: Literal["stdio", "sse", "streamable_http", "websocket"] = Field(
        description="传输协议类型",
    )
    enabled: bool = Field(default=True, description="是否启用")
    timeout: float = Field(default=60.0, gt=0, description="连接超时时间(秒)")
    max_concurrency: int = Field(
        default=0,
        ge=0,
        description="单服务器最大并发调用数(0=不限, 按QPS限制配置避免超限)",
    )

    # HTTP传输参数 (sse, streamable_http, websocket)
    url: str | None = Field(default=None, description="服务器URL")
    headers: dict[str, str] | None = Field(
        default=None,
        description="HTTP请求头(支持${ENV_VAR}环境变量替换)",
    )

    # stdio传输参数
    command: str | None = Field(default=None, description="可执行命令")
    args: list[str] | None = Field(default=None, description="命令参数")
    env: dict[str, str] | None = Field(default=None, description="子进程环境变量")

    # 工具名映射: MCP原始工具名 → 项目工具名(agent.yaml中使用的名称)
    tool_names: dict[str, str] = Field(
        default_factory=dict,
        description="工具名映射: {MCP原始名: 项目工具名}",
    )

    # 响应格式化器: MCP原始工具名 → 格式化器名称(不配置则透传原始响应)
    response_formatters: dict[str, str] = Field(
        default_factory=dict,
        description="响应格式化器: {MCP原始工具名: 格式化器名称}",
    )

    # 工具描述覆盖: MCP原始工具名 → 项目侧工具描述
    tool_descriptions: dict[str, str] = Field(
        default_factory=dict,
        description="工具描述覆盖: {MCP原始工具名: 项目侧工具描述}",
    )

    # 本地虚拟参数: MCP原始工具名 → JSON Schema properties
    # 这些参数只用于项目侧schema和formatter, 调用远端MCP前会剥离.
    local_args: dict[str, dict[str, dict[str, Any]]] = Field(
        default_factory=dict,
        description="本地虚拟参数: {MCP原始工具名: {参数名: JSON Schema属性}}",
    )

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        """验证服务器名称"""
        if not v or not v.strip():
            raise ValueError("服务器名称不能为空")
        return v.strip()

    def resolve_headers(self) -> dict[str, str] | None:
        """解析HTTP请求头中的环境变量占位符

        将 ${ENV_VAR} 格式的占位符替换为实际的环境变量值.
        如果环境变量不存在, 保留原始占位符.

        Returns:
            解析后的请求头字典, 如果原始headers为None则返回None

        """
        if self.headers is None:
            return None

        resolved = {}
        for key, value in self.headers.items():
            resolved[key] = self._resolve_env_vars(value)
        return resolved

    @staticmethod
    def _resolve_env_vars(text: str) -> str:
        """替换字符串中的 ${ENV_VAR} 环境变量占位符"""

        def replacer(match: re.Match[str]) -> str:
            var_name = match.group(1)
            value = os.getenv(var_name, "")
            if not value:
                logger.warning("环境变量 %s 未设置", var_name)
            return value

        return re.sub(r"\$\{(\w+)\}", replacer, text)

    def build_connection(self) -> dict[str, Any]:
        """构建MCP连接配置字典(用于配置验证).

        根据transport类型构建对应的连接配置, 并自动解析环境变量.
        注意: McpBridge直接使用McpServerConfig字段创建Transport,
        此方法主要用于配置完整性验证.

        Returns:
            连接配置字典

        Raises:
            ValueError: 配置不完整时

        """
        conn: dict[str, Any] = {"transport": self.transport}

        if self.transport == "streamable_http":
            if not self.url:
                raise ValueError(f"MCP服务器 {self.name}: streamable_http传输需要url")
            conn["url"] = self.url
            resolved_headers = self.resolve_headers()
            if resolved_headers:
                conn["headers"] = resolved_headers

        elif self.transport == "sse":
            if not self.url:
                raise ValueError(f"MCP服务器 {self.name}: sse传输需要url")
            conn["url"] = self.url
            resolved_headers = self.resolve_headers()
            if resolved_headers:
                conn["headers"] = resolved_headers

        elif self.transport == "stdio":
            if not self.command:
                raise ValueError(f"MCP服务器 {self.name}: stdio传输需要command")
            conn["command"] = self.command
            conn["args"] = self.args if self.args is not None else []
            if self.env:
                conn["env"] = self.env

        elif self.transport == "websocket":
            if not self.url:
                raise ValueError(f"MCP服务器 {self.name}: websocket传输需要url")
            conn["url"] = self.url

        return conn


class SkillConfig(BaseModel):
    """Skill配置 - 与工具/MCP平级的外部能力源.

    skill经审核适配后接入, 提供领域知识(skills段注入)+ 定向能力(关联工具注入).
    """

    name: str = Field(description="skill名称(唯一标识)")
    source: str = Field(description="SKILL.md所在目录(相对项目根)")
    backend: Literal["prompt_only", "executable"] = Field(
        default="prompt_only",
        description="执行后端: prompt_only(纯知识) | executable(需运行时), 仅元信息",
    )
    associated_tools: list[str] = Field(
        default_factory=list,
        description="load_skill激活时注入的关联工具名(从external_tools/internal_tools引用)",
    )
    enabled: bool = Field(default=True, description="是否启用")

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        """验证skill名称"""
        if not v or not v.strip():
            raise ValueError("skill名称不能为空")
        return v.strip()

    @field_validator("source")
    @classmethod
    def validate_source(cls, v: str) -> str:
        """验证skill源目录"""
        if not v or not v.strip():
            raise ValueError("skill source不能为空")
        return v.strip()


class ToolsConfig(BaseConfig):
    """Tools模块主配置类 - 标准配置体系"""

    _module_name = "tools"

    # 内部工具配置管理
    internal_tools: dict[str, InternalToolConfig] = Field(
        default_factory=dict,
        description="内部工具配置管理",
    )

    # 外部工具配置管理(复用InternalToolConfig)
    external_tools: dict[str, InternalToolConfig] = Field(
        default_factory=dict,
        description="外部工具配置管理(无状态全局共享)",
    )

    # MCP服务器配置管理
    mcp_servers: dict[str, McpServerConfig] = Field(
        default_factory=dict,
        description="MCP服务器配置管理",
    )

    # Skill配置管理(与工具/MCP平级的外部能力源)
    skills: dict[str, SkillConfig] = Field(
        default_factory=dict,
        description="Skill配置管理(领域知识+定向能力)",
    )

    # 工具组配置管理(休眠工具打包检索 / 常驻工具整组激活)
    tool_groups: dict[str, ToolGroupConfig] = Field(
        default_factory=dict,
        description="工具组配置(组作为检索单元, 命中后整组激活注入)",
    )

    # 默认配置字典来自内置工具 catalog.
    _default_config: ClassVar[dict[str, Any]] = get_builtin_tools_config()

    @field_validator(
        "internal_tools",
        "external_tools",
        "mcp_servers",
        "skills",
        "tool_groups",
        mode="before",
    )
    @classmethod
    def _normalize_dict_field(cls, value: Any) -> Any:
        """dict 类别字段 None 归一为空 dict.

        双保险: 即使 from_module_config 的合并逻辑未拦截(如直接 from_dict 调用),
        Pydantic 校验阶段也会把 None 安全归一, 避免 ValidationError 崩溃.
        对应历史生产事故: config.yaml 写空键导致全工具丢失.
        """
        if value is None:
            return {}
        return value

    @classmethod
    @override
    def from_module_config(cls) -> ToolsConfig:
        """从内置 catalog + config.yaml overlay 创建配置对象.

        Returns:
            配置对象实例

        """
        # 获取YAML配置
        yaml_config = get_module_config_sync("tools") or {}

        # 使用深拷贝避免浅拷贝污染 ClassVar _default_config 的嵌套字典.
        # 合并规则: 内置 catalog → config.yaml 用户覆盖.
        merged_config = copy.deepcopy(cls.get_default_config())

        # dict 类别(tool_groups/internal_tools/external_tools/mcp_servers/skills)
        # 按 item 合并: 已存在则 deep merge(使 config 子 dict 也能字段级覆盖),
        # 其余类别整体赋值.
        # None 防御: YAML 写 `internal_tools:` 紧跟非缩进内容会被解析为 None,
        # 此时跳过(保留 catalog 默认), 避免 None 整体覆盖导致 Pydantic 崩溃.
        # 历史 bug: 生产 config.yaml 极简版写了空键, 触发 Agent 全工具丢失事故.
        deep_merge_categories = {
            "tool_groups",
            "internal_tools",
            "external_tools",
            "mcp_servers",
            "skills",
        }
        for source in (yaml_config,):
            for key, value in source.items():
                if key in deep_merge_categories:
                    if value is None:
                        # 显式空键(如 `internal_tools:`), 跳过保留默认
                        continue
                    if not isinstance(value, dict):
                        # 类型错误(如写成字符串/列表), 警告并跳过, 不崩溃
                        logger.warning(
                            "配置 tools.%s 应为 dict, 实际 %s; 跳过覆盖保留默认",
                            key,
                            type(value).__name__,
                        )
                        continue
                    bucket = merged_config.setdefault(key, {})
                    for name, item_config in value.items():
                        if name in bucket:
                            bucket[name] = _deep_merge_dict(
                                bucket[name],
                                item_config,
                            )
                        else:
                            bucket[name] = item_config
                else:
                    merged_config[key] = value

        return cls.from_dict(merged_config)

    def get_internal_tool_config(self, tool_name: str) -> InternalToolConfig | None:
        """获取指定内部工具的配置"""
        return self.internal_tools.get(tool_name)

    def get_mcp_server_config(self, server_name: str) -> McpServerConfig | None:
        """获取指定MCP服务器的配置"""
        return self.mcp_servers.get(server_name)

    def get_external_tool_config(self, tool_name: str) -> InternalToolConfig | None:
        """获取指定外部工具的配置"""
        return self.external_tools.get(tool_name)

    def list_enabled_internal_tools(self) -> list[InternalToolConfig]:
        """获取所有启用的内部工具配置"""
        return [config for config in self.internal_tools.values() if config.enabled]

    def list_enabled_external_tools(self) -> list[InternalToolConfig]:
        """获取所有启用的外部工具配置"""
        return [config for config in self.external_tools.values() if config.enabled]

    def list_enabled_mcp_servers(self) -> list[McpServerConfig]:
        """获取所有启用的MCP服务器配置"""
        return [config for config in self.mcp_servers.values() if config.enabled]


# === 配置获取函数 ===


_cached: ToolsConfig | None = None


def get_config() -> ToolsConfig:
    """获取Tools模块配置对象(推荐方式)

    Returns:
        Tools配置对象实例

    """
    global _cached
    if _cached is None:
        _cached = ToolsConfig.from_module_config()
    return _cached


def get_default_config() -> dict[str, Any]:
    """获取Tools模块默认配置字典(兜底边界)

    Returns:
        Tools模块默认配置字典

    """
    return ToolsConfig.get_default_config()


# === 导出接口 ===
__all__ = [
    "InternalToolConfig",
    "McpServerConfig",
    "SkillConfig",
    "ToolsConfig",
    "get_config",
    "get_default_config",
]
