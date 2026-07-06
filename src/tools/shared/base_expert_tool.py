"""专家工具基类 - 全局共享的专业化工具设计模式.

设计约定:
- 全局共享实例, 不区分用户/线程
- 内部启动独立Agent编排, 与主对话模型解耦
- 通过 mcp_bridge 访问外部MCP工具
- _run 统一桥接到 _arun
- 通过 get_user_context() 获取运行时用户上下文(身份/渠道/文件列表)
- 不得访问数据库/Service 层

适用场景:
- 无数据库访问, 不需要用户隔离
- 内部编排子Agent完成复杂任务
- 全局共享, 一次创建多次复用

运行时上下文(src.core.context.UserContext):
- user_id/thread_id/agent_id: 身份标识
- is_openclaw: 渠道标识(影响输出格式)
- exported_files: 工具产出的文件列表(供对话历史存储追加附件标记)

扩展新专家工具时:
- 继承 BaseExpertTool
- 设置 name, description, summary, args_schema
- 设置 model_id, timeout 等配置
- 实现 _arun() 方法
- 在 experts/__init__.py 的 EXPERT_TOOL_NAMES 和 create_expert_tools() 中注册

description 字段规范:
- 前 3 行: 自然描述工具的核心能力, 供工具筛选模型做降噪判断
  第 1 行: 一句话核心能力
  第 2 行: 主要使用场景或能力范围
  第 3 行: 补充说明或关键细节
- 后续行: 操作说明/参数/示例, 供主模型使用工具时参考
- summary 字段面向主模型(注入 search_available_tools 描述)
- search_keywords 字段面向关键词匹配算法, 与 description 分离
"""

from __future__ import annotations

import json
import logging
from typing import Any, ClassVar, override

from langchain_core.tools import BaseTool

logger = logging.getLogger(__name__)


class BaseExpertTool(BaseTool):
    """专家工具基类.

    子类需要:
    - 设置 name, description, summary, args_schema 等类属性
    - 设置 model_id, timeout 等配置属性
    - 实现 _arun() 方法处理业务逻辑
    """

    name: str = ""
    description: str = ""
    summary: str = ""
    search_keywords: ClassVar[list[str]] = []

    model_id: str = ""
    timeout: float = 120.0
    mcp_bridge: Any = None

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
        """工具可用性检查.

        子类可重写此方法实现注册时的决策逻辑.
        返回False时工具不会被注册.
        """
        return True

    @staticmethod
    def _format_error(error: Exception, context: str = "") -> str:
        """统一错误格式化为 JSON."""
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
        """统一成功格式化为 JSON."""
        result = {"success": True, "message": message, **data}
        return json.dumps(result, ensure_ascii=False, indent=2)


__all__ = ["BaseExpertTool"]
