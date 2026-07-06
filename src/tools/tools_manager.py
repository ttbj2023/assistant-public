"""极简工具管理器 - 基于真实业务需求设计

提供两个核心接口:
1. create_tools() - Agent模块创建工具集
2. health_check() - API模块健康检查

三类工具架构:
- 内部工具: 按用户-线程隔离(需要数据安全)
- 外部工具: 无状态全局共享(直接封装或MCP接入)
- 专家工具: 全局共享(直接Python调用, 不经过MCP)
"""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.tools import BaseTool

from src.config.tools_config import get_config
from src.core.lifecycle import register_resource
from src.tools.experts import EXPERT_TOOL_NAMES, create_expert_tools
from src.tools.mcp.mcp_tool_manager import McpBridge

logger = logging.getLogger(__name__)


class ToolsManager:
    """极简工具管理接口 - 专注真实业务需求"""

    def __init__(self) -> None:
        self._internal_cache: dict[str, dict[str, BaseTool]] = {}

        self._tools_config = get_config()

        self._mcp_bridge = McpBridge(self._tools_config.mcp_servers)

        self._expert_tools_cache: dict[str, BaseTool] = {}

        self._external_tools_cache: dict[str, BaseTool] = {}

    async def create_tools(
        self,
        tool_names: list[str],
        user_id: str,
        thread_id: str,
        *,
        agent_id: str,
    ) -> list[BaseTool]:
        """创建工具集 - Agent模块的核心需求

        Args:
            tool_names: 工具名称列表
            user_id: 用户ID
            thread_id: 线程ID
            agent_id: Agent ID, 用于三级隔离(user/thread/agent), 必填

        Returns:
            创建的工具实例列表

        """
        logger.info(
            "创建工具集: %s, 用户: %s, 线程: %s, Agent: %s",
            tool_names,
            user_id,
            thread_id,
            agent_id,
        )

        tools = []
        cache_key = f"{user_id}:{thread_id}:{agent_id}"

        # 确保内部工具缓存槽存在
        if cache_key not in self._internal_cache:
            self._internal_cache[cache_key] = {}

        for tool_name in tool_names:
            try:
                tool = await self._get_or_create_tool(
                    tool_name,
                    user_id,
                    thread_id,
                    cache_key,
                    agent_id=agent_id,
                )
                if not tool:
                    logger.warning("工具创建失败: %s", tool_name)
                    continue

                if hasattr(tool, "is_available") and not await tool.is_available():
                    logger.info("工具不可用, 跳过: %s", tool_name)
                    continue

                tools.append(tool)
                logger.debug("成功创建工具: %s", tool_name)
            except Exception as e:
                logger.error("创建工具 %s 时出错: %s", tool_name, e)
                continue

        logger.info(f"成功创建 {len(tools)}/{len(tool_names)} 个工具")
        return tools

    async def create_dormant_tools(
        self,
        tool_names: list[str],
        user_id: str,
        thread_id: str,
        *,
        agent_id: str,
    ) -> list[BaseTool]:
        """创建休眠工具池 - 供ToolDiscoveryMiddleware按需激活.

        休眠工具不会注入到Agent的初始工具列表中, 而是通过
        search_available_tools工具搜索后发现, 再由中间件动态注入.

        Args:
            tool_names: 休眠工具名称列表
            user_id: 用户ID
            thread_id: 线程ID
            agent_id: Agent ID

        Returns:
            休眠工具实例列表

        """
        if not tool_names:
            return []

        logger.info(
            "创建休眠工具池: %s, 用户: %s, Agent: %s",
            tool_names,
            user_id,
            agent_id,
        )

        cache_key = f"{user_id}:{thread_id}:{agent_id}"
        if cache_key not in self._internal_cache:
            self._internal_cache[cache_key] = {}

        tools = []
        for tool_name in tool_names:
            try:
                tool = await self._get_or_create_tool(
                    tool_name,
                    user_id,
                    thread_id,
                    cache_key,
                    agent_id=agent_id,
                )
                if not tool:
                    logger.warning("休眠工具创建失败: %s", tool_name)
                    continue

                if hasattr(tool, "is_available") and not await tool.is_available():
                    logger.info("休眠工具不可用, 跳过: %s", tool_name)
                    continue

                tools.append(tool)
                logger.debug("休眠工具已创建: %s", tool_name)
            except Exception as e:
                logger.error("创建休眠工具 %s 时出错: %s", tool_name, e)
                continue

        logger.info(f"休眠工具池: {len(tools)}/{len(tool_names)} 个已创建")
        return tools

    async def health_check(self) -> dict[str, Any]:
        """工具系统健康检查 - API模块的核心需求

        Returns:
            包含各工具健康状态的字典

        """
        logger.info("执行工具系统健康检查")

        health_status = {
            "healthy": True,
            "tools": {},
            "cache_stats": {
                "internal_cache_entries": sum(
                    len(tools) for tools in self._internal_cache.values()
                ),
                "mcp_tools_loaded": len(self._mcp_bridge._tools),
                "total_user_sessions": len(self._internal_cache),
            },
        }

        # 检查内部工具
        internal_tools = self._tools_config.list_enabled_internal_tools()
        for tool in internal_tools:
            tool_name = tool.name
            try:
                config = self._tools_config.get_internal_tool_config(tool_name)
                if config and config.enabled:
                    health_status["tools"][tool_name] = {
                        "healthy": True,
                        "type": "internal",
                        "enabled": True,
                        "timeout": config.timeout,
                    }
                else:
                    health_status["tools"][tool_name] = {
                        "healthy": False,
                        "type": "internal",
                        "enabled": False,
                        "error": "Tool disabled or not configured",
                    }
                    health_status["healthy"] = False
            except Exception as e:
                logger.debug("内部工具健康检查失败(%s): %s", tool_name, e)
                health_status["tools"][tool_name] = {
                    "healthy": False,
                    "type": "internal",
                    "error": str(e),
                }
                health_status["healthy"] = False

        # 检查MCP服务器
        mcp_health = await self._mcp_bridge.health_check()
        for server_name, server_status in mcp_health.get("servers", {}).items():
            health_status["tools"][f"mcp:{server_name}"] = {
                "healthy": server_status.get("status") != "error",
                "type": "mcp",
                **server_status,
            }
            if server_status.get("status") == "error":
                health_status["healthy"] = False

        logger.info(
            f"健康检查完成, 整体状态: {'健康' if health_status['healthy'] else '异常'}",
        )
        return health_status

    async def _get_or_create_tool(
        self,
        tool_name: str,
        user_id: str,
        thread_id: str,
        cache_key: str,
        *,
        agent_id: str,
    ) -> BaseTool | None:
        """获取或创建工具实例(应用智能缓存策略)

        Args:
            tool_name: 工具名称
            user_id: 用户ID
            thread_id: 线程ID
            cache_key: 缓存键(user_id:thread_id:agent_id)
            agent_id: Agent ID

        """
        if self._is_internal_tool(tool_name):
            # 内部工具: 检查缓存
            internal_cache = self._internal_cache[cache_key]
            if tool_name in internal_cache:
                logger.debug("复用内部工具缓存: %s", tool_name)
                return internal_cache[tool_name]

            # 创建新的内部工具
            tool = await self._create_internal_tool(
                tool_name,
                user_id,
                thread_id,
                agent_id=agent_id,
            )
            if tool:
                internal_cache[tool_name] = tool
                logger.debug("创建并缓存内部工具: %s", tool_name)
            return tool

        if self._is_expert_tool(tool_name):
            if tool_name in self._expert_tools_cache:
                logger.debug("复用专家工具缓存: %s", tool_name)
                return self._expert_tools_cache[tool_name]

            from src.config.inference_config import get_config

            experts_config = get_config().experts
            model_id = experts_config.get_model_id(tool_name)

            tools = create_expert_tools(
                [tool_name],
                mcp_bridge=self._mcp_bridge,
                model_id=model_id,
            )
            if tools:
                self._expert_tools_cache[tool_name] = tools[0]
                logger.debug("创建并缓存专家工具: %s", tool_name)
                return tools[0]
            logger.warning("专家工具创建失败: %s", tool_name)
            return None

        if self._is_external_tool(tool_name):
            if tool_name in self._external_tools_cache:
                logger.debug("复用外部工具缓存: %s", tool_name)
                return self._external_tools_cache[tool_name]

            tool = self._create_external_tool(tool_name)
            if tool:
                self._external_tools_cache[tool_name] = tool
                logger.debug("创建并缓存外部工具: %s", tool_name)
            return tool

        # MCP工具: 全局共享, 由McpBridge管理缓存
        tool = await self._mcp_bridge.get_tool(tool_name)
        if tool:
            logger.debug("获取MCP工具: %s", tool_name)
        else:
            logger.warning("MCP工具未找到: %s", tool_name)
        return tool

    def _is_internal_tool(self, tool_name: str) -> bool:
        """判断是否为内部工具"""
        internal_tools = self._tools_config.list_enabled_internal_tools()
        return any(tool.name == tool_name for tool in internal_tools)

    @staticmethod
    def _is_expert_tool(tool_name: str) -> bool:
        """判断是否为专家工具"""
        return tool_name in EXPERT_TOOL_NAMES

    def _is_external_tool(self, tool_name: str) -> bool:
        """判断是否为外部工具"""
        external_tools = self._tools_config.list_enabled_external_tools()
        return any(tool.name == tool_name for tool in external_tools)

    def _create_external_tool(self, tool_name: str) -> BaseTool | None:
        """创建外部工具实例(全局共享, 无需用户隔离)"""
        try:
            tool_config = self._tools_config.get_external_tool_config(tool_name)
            if not tool_config or not tool_config.enabled:
                logger.warning("外部工具未启用: %s", tool_name)
                return None

            class_path = tool_config.class_path
            if not class_path:
                logger.error("外部工具缺少类路径: %s", tool_name)
                return None

            from src.core.validation.unified_sanitizer import UnifiedSanitizer

            if not UnifiedSanitizer.is_safe_class_path(class_path):
                logger.error("不安全的类路径: %s", class_path)
                return None

            module_path, class_name = class_path.rsplit(".", 1)
            tool_class = UnifiedSanitizer.safe_import(module_path, class_name)

            tool_config_dict = tool_config.config or {}
            tool = tool_class(**tool_config_dict)

            logger.debug("成功创建外部工具: %s", tool_name)
            return tool

        except (ImportError, AttributeError, TypeError):
            raise
        except Exception as e:
            logger.error("创建外部工具失败 %s: %s", tool_name, e)
            return None

    async def _create_internal_tool(
        self,
        tool_name: str,
        user_id: str,
        thread_id: str,
        *,
        agent_id: str,
    ) -> BaseTool | None:
        """创建内部工具实例"""
        try:
            # 获取工具配置
            tool_config = self._tools_config.get_internal_tool_config(tool_name)
            if not tool_config or not tool_config.enabled:
                logger.warning("内部工具未启用: %s", tool_name)
                return None

            # 安全验证类路径
            class_path = tool_config.class_path
            if not class_path:
                logger.error("内部工具缺少类路径: %s", tool_name)
                return None

            # 动态导入和实例化
            from src.core.validation.unified_sanitizer import UnifiedSanitizer

            if not UnifiedSanitizer.is_safe_class_path(class_path):
                logger.error("不安全的类路径: %s", class_path)
                return None

            module_path, class_name = class_path.rsplit(".", 1)
            tool_class = UnifiedSanitizer.safe_import(module_path, class_name)

            # 创建工具实例, 传递agent_id实现三级隔离
            tool_config_dict = tool_config.config or {}
            tool = tool_class(user_id, thread_id, agent_id=agent_id, **tool_config_dict)

            logger.debug("成功创建内部工具: %s", tool_name)
            return tool

        except (ImportError, AttributeError, TypeError):
            raise
        except Exception as e:
            logger.error("创建内部工具失败 %s: %s", tool_name, e)
            return None

    def get_cache_stats(self) -> dict[str, Any]:
        """获取缓存统计信息(用于调试和监控)"""
        return {
            "internal_tools": {
                "user_sessions": len(self._internal_cache),
                "total_tools": sum(
                    len(tools) for tools in self._internal_cache.values()
                ),
                "tools_by_session": {
                    session: list(tools.keys())
                    for session, tools in self._internal_cache.items()
                },
            },
            "mcp_tools": self._mcp_bridge.get_stats(),
        }

    async def clear_cache(self) -> None:
        """清理所有缓存"""
        self._internal_cache.clear()
        await self._mcp_bridge.reload()
        logger.info("工具缓存已清理")

    async def close(self) -> None:
        """关闭工具管理器, 释放MCP连接并清理所有缓存(应用关闭时调用).

        与clear_cache()的区别: close()面向优雅关闭, 不触发MCP reload,
        直接关闭Client连接(含stdio子进程), 并清空全部三类工具缓存.
        """
        try:
            await self._mcp_bridge.close()
        except Exception as e:
            logger.warning("关闭MCP桥接器异常(非致命): %s", e)
        self._internal_cache.clear()
        self._expert_tools_cache.clear()
        self._external_tools_cache.clear()
        logger.info("ToolsManager已关闭")

    def get_tool_stats(self) -> dict[str, Any]:
        """获取工具统计信息 - 兼容API接口

        Returns:
            包含工具统计信息的字典

        """
        # 获取内部工具数量
        internal_tools_count = len(self._tools_config.list_enabled_internal_tools())

        # 获取MCP工具数量
        mcp_tools_count = len(self._mcp_bridge._tools)

        # 计算总工具数
        total_tools = internal_tools_count + mcp_tools_count

        return {
            "internal_tools": internal_tools_count,
            "mcp_tools": mcp_tools_count,
            "total_tools": total_tools,
            "active_connections": 0,
            "cache_stats": {
                "internal_cache_entries": sum(
                    len(tools) for tools in self._internal_cache.values()
                ),
                "mcp_tools_loaded": mcp_tools_count,
                "total_user_sessions": len(self._internal_cache),
            },
        }


# 全局实例(单例模式)
_tools_manager: ToolsManager | None = None


def get_tools_manager() -> ToolsManager:
    """获取工具管理器实例(单例)"""
    global _tools_manager
    if _tools_manager is None:
        _tools_manager = ToolsManager()
        register_resource("tools_manager", _tools_manager.close)
    return _tools_manager
