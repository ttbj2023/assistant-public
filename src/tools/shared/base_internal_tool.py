"""内部工具基类 - 统一内部工具的设计模式.

设计约定:
- user_id/thread_id 不声明为 Pydantic Field, 通过 object.__setattr__ 设置
  避免暴露给 LLM 的 tool schema, 与 args_schema 中的业务参数分离
- 数据访问通过 _get_service() lazy-init, 不在 __init__ 中创建重量级资源
- _run 统一桥接到 _arun, 传递 callable + kwargs (不传 coroutine)
- 错误返回统一使用 _format_error() 返回 JSON
- 附件相关工具的参数命名使用 attachment_id 而非 file_id:
  工具名/对话标记均使用"附件"语义, file_id 会导致 LLM 猜错参数名
"""

from __future__ import annotations

import json
import logging
from typing import Any, ClassVar, override

from langchain_core.tools import BaseTool

logger = logging.getLogger(__name__)


class BaseInternalTool(BaseTool):
    """内部工具基类.

    子类需要:
    - 设置 name, description, args_schema 等类属性
    - 实现 _arun() 方法处理业务逻辑
    - 可选: 实现 _get_service() 提供带缓存的 Service/DAO 访问

    description 字段规范:
    - 前 3 行: 自然描述工具的核心能力, 供工具筛选模型做降噪判断
      第 1 行: 一句话核心能力
      第 2 行: 主要使用场景或能力范围
      第 3 行: 补充说明或关键细节
    - 后续行: 操作说明/参数/示例, 供主模型使用工具时参考
    - summary 字段面向 search_available_tools 的简短描述;
      对于被工具组统一管理的子工具, 应保留默认空值, 由工具组 summary 接管
    - search_keywords 字段面向关键词匹配算法, 与 description 分离
    """

    name: str = ""
    description: str = ""
    summary: str = ""
    search_keywords: ClassVar[list[str]] = []

    def __init__(
        self,
        user_id: str,
        thread_id: str,
        *,
        agent_id: str,
        **kwargs: Any,
    ) -> None:
        """初始化内部工具.

        Args:
            user_id: 用户ID
            thread_id: 线程ID
            agent_id: Agent ID
            **kwargs: 传递给 BaseTool 的额外参数

        """
        super().__init__(**kwargs)
        object.__setattr__(self, "user_id", user_id)
        object.__setattr__(self, "thread_id", thread_id)
        object.__setattr__(self, "agent_id", agent_id)

    @override
    def _run(self, **kwargs: Any) -> str:
        """同步桥接 - 统一传递 callable + kwargs 到异步上下文.

        子类通常不需要重写此方法.
        """
        try:
            from ...core.text_truncation import truncate_tool_result
            from ...utils.async_utils import run_async_in_sync_context

            result = run_async_in_sync_context(self._arun, **kwargs)
            return truncate_tool_result(result)
        except Exception as e:
            logger.error(f"{self.name} 工具同步执行失败: {e}")
            return self._format_error(e)

    async def is_available(self) -> bool:
        """可选工具的可用性检查.

        子类重写此方法实现注册时的决策逻辑.
        返回False时工具不会被注册(核心工具不需要重写).
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


__all__ = ["BaseInternalTool"]
