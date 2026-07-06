"""工具发现中间件 - 基于LangChain v1.0中间件的动态工具注入.

核心机制:
1. Agent启动时只加载核心工具 + search_available_tools
2. 当Agent调用search_available_tools搜索工具时, 中间件检测到调用结果
3. 中间件从休眠工具池中激活匹配的工具, 通过request.override注入后续模型调用
4. 已激活的工具在会话内永久保留

设计约定:
- 使用awrap_model_call拦截模型调用, 动态修改工具列表
- 使用aafter_model检测search_available_tools的调用结果, 提取匹配的工具名
- 休眠工具池在中间件初始化时创建, 会话内不变
"""

from __future__ import annotations

from src.tools.middleware._skill_load import SkillLoadMiddleware
from src.tools.middleware._tool_discovery import ToolDiscoveryMiddleware

__all__ = ["SkillLoadMiddleware", "ToolDiscoveryMiddleware"]
