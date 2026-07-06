"""外部工具基类 - 无状态全局共享的外部服务封装.

设计约定:
- 无用户隔离, 构造函数不要求 user_id/thread_id/agent_id
- 接受 **kwargs 传递配置参数(timeout, api_key_env等)
- _run 统一桥接到 _arun, 子类只需实现 _arun()
- 错误返回统一使用 _format_error() 返回 JSON
- is_available 默认 True, 子类可重写检查环境变量

适用场景:
- 调用外部 API/网络服务(搜索, 网页阅读, HTTP抓取等)
- 无状态, 全局单例即可
- 通过 tools_config.py 配置驱动注册到 agent.yaml

MCP 工具是外部工具的一种接入方式:
- 直接封装: BaseExternalTool 子类, 通过 class_path 动态导入
- MCP 接入: 通过 McpBridge 管理, 由 config.yaml mcp_servers 配置
"""

from __future__ import annotations

import json
import logging
from typing import Any, ClassVar, override

from langchain_core.tools import BaseTool

logger = logging.getLogger(__name__)


class BaseExternalTool(BaseTool):
    """外部工具基类.

    子类需要:
    - 设置 name, description, summary, args_schema 等类属性
    - 实现 _arun() 方法处理业务逻辑
    - 可选: 重写 is_available() 实现注册时的可用性检查
    """

    name: str = ""
    description: str = ""
    summary: str = ""
    search_keywords: ClassVar[list[str]] = []

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)

    @override
    def _run(self, **kwargs: Any) -> str:
        """同步桥接 - 统一传递 callable + kwargs 到异步上下文."""
        try:
            from src.core.text_truncation import truncate_tool_result
            from src.utils.async_utils import run_async_in_sync_context

            result = run_async_in_sync_context(self._arun, **kwargs)
            return truncate_tool_result(result)
        except Exception as e:
            logger.error(f"{self.name} 工具同步执行失败: %s", e)
            return self._format_error(e)

    async def is_available(self) -> bool:
        """可用性检查.

        子类重写此方法实现注册时的决策逻辑.
        返回 False 时工具不会被注册.
        """
        return True

    @staticmethod
    def _format_error(error: Exception, context: str = "") -> str:
        """统一错误格式化为 JSON.

        Args:
            error: 异常对象
            context: 额外上下文信息

        Returns:
            JSON 格式的错误信息字符串

        """
        result = {
            "success": False,
            "message": f"操作失败: {error!s}",
            "error": f"{type(error).__name__}: {error!s}",
        }
        if context:
            result["context"] = context
        return json.dumps(result, ensure_ascii=False)

    @staticmethod
    def _format_success(data: dict[str, Any], message: str = "操作成功") -> str:
        """统一成功格式化为 JSON.

        Args:
            data: 返回数据字典
            message: 成功消息

        Returns:
            JSON 格式的成功信息字符串

        """
        result = {"success": True, "message": message, **data}
        return json.dumps(result, ensure_ascii=False, indent=2)


__all__ = ["BaseExternalTool"]
