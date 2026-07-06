"""MCP工具桥接器 - 基于fastmcp.Client的MCP协议集成.

核心职责:
1. 从配置的MCP服务器加载工具, 转换为LangChain BaseTool
2. 应用tool_names映射: MCP原始工具名 → 项目工具名
3. Session复用: Client实例常驻, 避免每次调用新建子进程/连接
4. 内置重试: 失败后自动重连+重试(限流感知)
5. 响应格式化器: 可选的后处理器, 将原始MCP响应转为LLM友好格式
"""

from __future__ import annotations

import asyncio
import copy
import json
import logging
import os
import re
from typing import Any

from langchain_core.tools import BaseTool, StructuredTool
from pydantic import BaseModel, Field, create_model

from src.config.tools_config import McpServerConfig

logger = logging.getLogger(__name__)

_MCP_SEMAPHORE = asyncio.Semaphore(5)


def _resolve_env_vars(text: str) -> str:
    """替换字符串中的 ${ENV_VAR} 环境变量占位符."""

    def replacer(match: re.Match[str]) -> str:
        var_name = match.group(1)
        value = os.getenv(var_name, "")
        if not value:
            logger.warning("环境变量 %s 未设置", var_name)
        return value

    return re.sub(r"\$\{(\w+)\}", replacer, text)


def _resolve_dict_env(d: dict[str, str]) -> dict[str, str]:
    """批量解析字典中的环境变量占位符."""
    return {k: _resolve_env_vars(v) for k, v in d.items()}


def _schema_to_pydantic(schema: dict[str, Any], model_name: str) -> type[BaseModel]:
    """将MCP工具的inputSchema(JSON Schema)动态创建为Pydantic BaseModel.

    Args:
        schema: MCP工具的inputSchema
        model_name: 生成的模型名称

    Returns:
        动态创建的Pydantic BaseModel类

    """
    type_mapping = {
        "string": (str, ...),
        "integer": (int, ...),
        "number": (float, ...),
        "boolean": (bool, ...),
        "array": (list, ...),
        "object": (dict, ...),
    }

    properties = schema.get("properties", {})
    required = set(schema.get("required", []))

    field_definitions: dict[str, Any] = {}
    for prop_name, prop_schema in properties.items():
        prop_type = prop_schema.get("type", "string")
        description = prop_schema.get("description", "")
        field_kwargs: dict[str, Any] = {"description": description}
        if "enum" in prop_schema:
            field_kwargs["json_schema_extra"] = {"enum": prop_schema["enum"]}

        if prop_name in required:
            base_type, default = type_mapping.get(prop_type, (str, ...))
            field_definitions[prop_name] = (
                base_type,
                Field(default=default, **field_kwargs),
            )
        else:
            base_type, _ = type_mapping.get(prop_type, (str, ...))
            default = prop_schema.get("default")
            field_type = base_type if default is not None else base_type | None
            field_definitions[prop_name] = (
                field_type,
                Field(default=default, **field_kwargs),
            )

    return create_model(model_name, **field_definitions)


def _schema_with_local_args(
    schema: dict[str, Any],
    tool_name: str,
    server_config: McpServerConfig,
) -> dict[str, Any]:
    """合并项目侧本地参数到MCP工具schema.

    local_args只影响LangChain侧schema和formatter, 调用远端MCP前会剥离.
    """
    local_args = server_config.local_args.get(tool_name)
    if not local_args:
        return schema

    merged = copy.deepcopy(schema)
    merged.setdefault("type", "object")
    properties = merged.setdefault("properties", {})
    if not isinstance(properties, dict):
        properties = {}
        merged["properties"] = properties

    for arg_name, arg_schema in local_args.items():
        properties[arg_name] = arg_schema
    return merged


def _strip_local_args(
    kwargs: dict[str, Any],
    tool_name: str,
    server_config: McpServerConfig,
) -> dict[str, Any]:
    """剥离只供项目侧schema/formatter使用的本地参数."""
    local_arg_names = set(server_config.local_args.get(tool_name, {}))
    if not local_arg_names:
        return kwargs
    return {k: v for k, v in kwargs.items() if k not in local_arg_names}


def _extract_call_result_text(result: Any) -> str:
    """从fastmcp Client.call_tool()返回值中提取文本.

    fastmcp返回CallToolResult对象, 内容在content列表中.

    Args:
        result: CallToolResult或类似结构

    Returns:
        提取的文本内容

    """
    if isinstance(result, str):
        return result

    content = getattr(result, "content", None)
    if content is None and isinstance(result, (list, tuple)):
        content = result

    if isinstance(content, list):
        texts = []
        for item in content:
            if isinstance(item, str):
                texts.append(item)
            elif isinstance(item, dict) and "text" in item:
                texts.append(item["text"])
            elif hasattr(item, "text"):
                texts.append(item.text)
        return "\n".join(texts)

    return str(result) if result else ""


class McpBridge:
    """MCP工具桥接器 - 基于fastmcp.Client.

    管理MCP服务器连接, 加载工具并转换为LangChain BaseTool.
    Client实例常驻复用, 避免重复创建子进程/连接.
    """

    def __init__(self, mcp_servers: dict[str, McpServerConfig]) -> None:
        self._server_configs = mcp_servers
        self._tools: dict[str, StructuredTool] = {}
        self._clients: list[Any] = []
        self._loaded = False

        self._server_semaphores: dict[str, asyncio.Semaphore] = {}
        for name, config in mcp_servers.items():
            if config.max_concurrency > 0:
                self._server_semaphores[name] = asyncio.Semaphore(
                    config.max_concurrency,
                )
                logger.info(f"MCP服务器 {name} 并发限制: {config.max_concurrency}")

    async def _ensure_loaded(self) -> None:
        """懒加载: 首次访问时从所有MCP服务器加载工具."""
        if self._loaded:
            return

        try:
            from fastmcp.client import Client
            from fastmcp.client.transports import (  # noqa: F401
                SSETransport,
                StdioTransport,
                StreamableHttpTransport,
            )
        except ImportError:
            logger.error("fastmcp未安装, 请运行: pip install fastmcp>=3.0.0")
            self._loaded = True
            return

        enabled_servers = {
            name: config
            for name, config in self._server_configs.items()
            if config.enabled
        }

        if not enabled_servers:
            logger.info("没有启用的MCP服务器")
            self._loaded = True
            return

        for server_name, config in enabled_servers.items():
            try:
                transport = self._create_transport(config)
                if transport is None:
                    continue

                timeout = config.timeout or 60.0
                client = Client(transport, timeout=timeout)
                await client.__aenter__()
                self._clients.append(client)

                mcp_tools = await client.list_tools()
                for mcp_tool in mcp_tools:
                    original_name = mcp_tool.name
                    project_name = self._resolve_project_name(original_name, config)
                    formatter_name = self._resolve_formatter_name(original_name, config)

                    tool = self._convert_tool(
                        client=client,
                        mcp_tool=mcp_tool,
                        project_name=project_name,
                        formatter_name=formatter_name,
                        server_name=server_name,
                        server_config=config,
                    )
                    self._tools[project_name] = tool
                    logger.debug(
                        "MCP工具加载: %s → %s (server: %s)",
                        original_name,
                        project_name,
                        server_name,
                    )

                logger.info(f"MCP服务器 {server_name} 加载完成: {len(mcp_tools)}个工具")

            except Exception as e:
                logger.error("MCP服务器 %s 加载失败: %s", server_name, e)

        self._loaded = True
        logger.info(
            f"MCP桥接器加载完成: {len(self._tools)}个工具 "
            f"来自{len(enabled_servers)}个服务器",
        )

    def _create_transport(self, config: McpServerConfig) -> Any:
        """根据配置创建fastmcp Transport实例."""
        from fastmcp.client.transports import (
            SSETransport,
            StdioTransport,
            StreamableHttpTransport,
        )

        if config.transport == "streamable_http":
            if not config.url:
                logger.error(f"MCP服务器 {config.name}: streamable_http需要url")
                return None
            resolved_url = _resolve_env_vars(config.url)
            headers = config.resolve_headers()
            return StreamableHttpTransport(url=resolved_url, headers=headers or {})

        if config.transport == "sse":
            if not config.url:
                logger.error(f"MCP服务器 {config.name}: sse需要url")
                return None
            resolved_url = _resolve_env_vars(config.url)
            headers = config.resolve_headers()
            return SSETransport(url=resolved_url, headers=headers or {})

        if config.transport == "stdio":
            if not config.command:
                logger.error(f"MCP服务器 {config.name}: stdio需要command")
                return None
            env = _resolve_dict_env(config.env) if config.env else None
            return StdioTransport(
                command=config.command,
                args=config.args or [],
                env=env,
            )

        logger.error(f"不支持的transport类型: {config.transport}")
        return None

    def _resolve_project_name(self, original_name: str, config: McpServerConfig) -> str:
        """将MCP原始工具名映射为项目工具名."""
        return config.tool_names.get(original_name, original_name)

    def _resolve_formatter_name(
        self,
        original_name: str,
        config: McpServerConfig,
    ) -> str | None:
        """获取MCP工具对应的响应格式化器名称."""
        return config.response_formatters.get(original_name)

    def _convert_tool(
        self,
        client: Any,
        mcp_tool: Any,
        project_name: str,
        formatter_name: str | None,
        server_name: str,
        server_config: McpServerConfig,
    ) -> StructuredTool:
        """将MCP Tool转换为LangChain StructuredTool.

        核心设计:
        - 闭包捕获client实例, 实现Session复用
        - 内置重试+重连机制
        - 可选的响应格式化器
        """
        original_tool_name = mcp_tool.name

        input_schema = getattr(mcp_tool, "inputSchema", None) or {}
        input_schema = _schema_with_local_args(
            input_schema,
            original_tool_name,
            server_config,
        )
        args_schema = _schema_to_pydantic(input_schema, f"{project_name}_input")

        description = server_config.tool_descriptions.get(
            original_tool_name,
            getattr(mcp_tool, "description", None) or f"MCP工具: {project_name}",
        )

        formatter = None
        if formatter_name:
            from src.tools.mcp.response_formatters import get_formatter

            formatter = get_formatter(formatter_name)
            if formatter:
                logger.info("MCP工具 %s 启用格式化器: %s", project_name, formatter_name)
            else:
                logger.warning(
                    "MCP工具 %s 格式化器未找到: %s",
                    project_name,
                    formatter_name,
                )

        async def arun(**kwargs: Any) -> str:
            return await _call_with_retry(
                client=client,
                tool_name=original_tool_name,
                kwargs=kwargs,
                formatter=formatter,
                server_name=server_name,
                server_config=server_config,
                bridge=self,
            )

        return StructuredTool(
            name=project_name,
            description=description,
            args_schema=args_schema,
            coroutine=arun,
            response_format="content",
        )

    async def get_tool(self, tool_name: str) -> BaseTool | None:
        """按项目工具名获取MCP工具."""
        await self._ensure_loaded()
        return self._tools.get(tool_name)

    async def get_all_tools(self) -> list[BaseTool]:
        """获取所有已加载的MCP工具."""
        await self._ensure_loaded()
        return list(self._tools.values())

    async def close(self) -> None:
        """关闭所有Client连接."""
        for client in self._clients:
            try:
                await client.__aexit__(None, None, None)
            except Exception as e:
                logger.debug("关闭MCP Client时出错: %s", e)
        self._clients.clear()
        logger.info("MCP桥接器已关闭所有连接")

    async def health_check(self) -> dict[str, Any]:
        """MCP服务器连接健康检查."""
        health: dict[str, Any] = {
            "healthy": True,
            "servers": {},
            "loaded_tools": len(self._tools),
        }

        for name, config in self._server_configs.items():
            if not config.enabled:
                health["servers"][name] = {
                    "status": "disabled",
                    "transport": config.transport,
                }
                continue

            tool_count = sum(
                1 for t_name in config.tool_names.values() if t_name in self._tools
            )
            health["servers"][name] = {
                "status": "configured" if self._loaded else "pending",
                "transport": config.transport,
                "tool_count": tool_count,
            }

        return health

    async def reload(self) -> None:
        """强制重新加载所有MCP工具."""
        logger.info("重新加载MCP工具")
        await self.close()
        self._tools = {}
        self._loaded = False
        await self._ensure_loaded()

    def get_stats(self) -> dict[str, Any]:
        """获取MCP工具统计信息."""
        concurrency_limits = {
            name: sem._value for name, sem in self._server_semaphores.items()
        }
        return {
            "total_tools": len(self._tools),
            "loaded": self._loaded,
            "servers": len(self._server_configs),
            "tool_names": list(self._tools.keys()),
            "active_clients": len(self._clients),
            "concurrency_limits": concurrency_limits,
        }


async def _call_with_retry(
    client: Any,
    tool_name: str,
    kwargs: dict[str, Any],
    formatter: Any | None,
    server_name: str,
    server_config: McpServerConfig,
    bridge: McpBridge,
) -> str:
    """带重试和重连的MCP工具调用.

    策略:
    - 最多2次尝试(首次+1次重试)
    - 认证错误(401/403)不重试
    - 限流错误(429)等待3秒后重试
    - 其他错误等待1秒后重试, 同时尝试重连
    - 并发控制: 全局Semaphore + per-server Semaphore限制
    """
    server_sem = bridge._server_semaphores.get(server_name)

    async with _MCP_SEMAPHORE:
        if server_sem is not None:
            async with server_sem:
                return await _do_call_with_retry(
                    client,
                    tool_name,
                    kwargs,
                    formatter,
                    server_name,
                    server_config,
                    bridge,
                )
        else:
            return await _do_call_with_retry(
                client,
                tool_name,
                kwargs,
                formatter,
                server_name,
                server_config,
                bridge,
            )


async def _do_call_with_retry(
    client: Any,
    tool_name: str,
    kwargs: dict[str, Any],
    formatter: Any | None,
    server_name: str,
    server_config: McpServerConfig,
    bridge: McpBridge,
) -> str:
    """带重试和重连的MCP工具调用核心逻辑."""
    from src.config.retry_config import get_retry_config

    mcp_cfg = get_retry_config().mcp
    max_attempts = mcp_cfg.max_retries + 1
    base_delay = mcp_cfg.base_delay
    rate_limit_delay = mcp_cfg.rate_limit_delay

    active_client = client

    for attempt in range(max_attempts):
        try:
            # 过滤None值: MCP server的schema可能要求非None类型,
            # 传None会触发校验错误(如baidu_search的temperature/top_p).
            # 过滤后让server使用自身schema定义的default值.
            remote_kwargs = _strip_local_args(kwargs, tool_name, server_config)
            cleaned_kwargs = {k: v for k, v in remote_kwargs.items() if v is not None}
            from src.core.text_truncation import truncate_tool_result

            result = await active_client.call_tool(tool_name, cleaned_kwargs)
            raw_text = _extract_call_result_text(result)

            if formatter:
                return truncate_tool_result(formatter.safe_format(raw_text, **kwargs))
            return truncate_tool_result(raw_text)

        except Exception as e:
            error_str = str(e).lower()
            logger.warning(
                f"MCP工具调用失败 (attempt={attempt + 1}, "
                f"tool={tool_name}, server={server_name}): {e}",
            )

            if _is_auth_error(error_str):
                return json.dumps(
                    {
                        "error": f"认证失败: {str(e)[:2000]}",
                        "source": tool_name,
                    },
                    ensure_ascii=False,
                )

            if attempt < max_attempts - 1:
                retry_delay = (
                    rate_limit_delay if _is_rate_limit_error(error_str) else base_delay
                )
                logger.info("等待%s秒后重试 %s", retry_delay, tool_name)
                await asyncio.sleep(retry_delay)

                reconnected = await _try_reconnect(
                    bridge,
                    server_name,
                    server_config,
                    old_client=active_client,
                )
                if reconnected is not None:
                    active_client = reconnected
            else:
                return json.dumps(
                    {
                        "error": f"工具调用失败: {e}",
                        "source": tool_name,
                    },
                    ensure_ascii=False,
                )

    return json.dumps(
        {"error": "未知错误", "source": tool_name},
        ensure_ascii=False,
    )


async def _try_reconnect(
    bridge: McpBridge,
    server_name: str,
    config: McpServerConfig,
    *,
    old_client: Any | None = None,
) -> Any | None:
    """尝试创建新的Client连接, 同时关闭旧的失效连接.

    Returns:
        新的Client实例, 失败返回None

    """
    try:
        from fastmcp.client import Client

        transport = bridge._create_transport(config)
        if transport is None:
            return None

        timeout = config.timeout or 60.0
        new_client = Client(transport, timeout=timeout)
        await new_client.__aenter__()
        bridge._clients.append(new_client)
        logger.info("MCP重连成功: %s", server_name)

        if old_client is not None:
            try:
                await old_client.__aexit__(None, None, None)
            except Exception as e:
                logger.debug("关闭失效MCP Client连接异常(已忽略): %s", e)
            if old_client in bridge._clients:
                bridge._clients.remove(old_client)
                logger.debug("已关闭并移除失效的MCP Client连接")

        return new_client
    except Exception as e:
        logger.error("MCP重连失败 (%s): %s", server_name, e)
        return None


def _is_auth_error(error_str: str) -> bool:
    """判断是否为认证错误(不应重试)."""
    auth_keywords = [
        "401",
        "403",
        "unauthorized",
        "forbidden",
        "认证",
        "apikey",
        "api_key",
    ]
    return any(kw in error_str for kw in auth_keywords)


def _is_rate_limit_error(error_str: str) -> bool:
    """判断是否为限流错误(需要更长等待)."""
    rate_keywords = [
        "429",
        "rate limit",
        "速率限制",
        "请求频率",
    ]
    return any(kw in error_str for kw in rate_keywords)
