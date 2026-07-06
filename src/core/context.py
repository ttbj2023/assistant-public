"""用户上下文 ContextVar - 运行时透传用户身份到全局共享组件.

设计约定:
- 通过 contextvars 在异步调用链中透传用户身份
- 在 chat.py 调用 agent 前设置, agent 处理完成后重置
- 专家工具通过 get_user_context() 获取运行时用户上下文
- 仅提供轻量上下文(身份标识/渠道标识), 不暴露数据库/Service

使用方式:
    chat.py:     token = set_user_context(UserContext(...))
    expert tool: ctx = get_user_context()
    chat.py:     reset_user_context(token)
"""

from __future__ import annotations

import logging
from contextvars import ContextVar, Token
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class UserContext:
    """运行时用户上下文."""

    user_id: str
    thread_id: str
    agent_id: str
    request_id: str | None = None
    round_number: int | None = None
    usage_source: str = "main_chat"
    is_openclaw: bool = False
    exported_files: list[dict] = field(default_factory=list)


_user_context: ContextVar[UserContext | None] = ContextVar("user_context", default=None)


def set_user_context(ctx: UserContext) -> Token[UserContext | None]:
    """设置当前异步上下文的用户信息."""
    token = _user_context.set(ctx)
    logger.debug(
        f"UserContext set: {ctx.user_id}/{ctx.thread_id}/{ctx.agent_id} "
        f"openclaw={ctx.is_openclaw}",
    )
    return token


def get_user_context() -> UserContext:
    """获取当前异步上下文的用户信息, 无上下文时 raise."""
    ctx = _user_context.get()
    if ctx is None:
        raise RuntimeError(
            "UserContext 未设置, 请确保在 agent 处理链路中已调用 set_user_context()",
        )
    return ctx


def reset_user_context(token: Token[UserContext | None]) -> None:
    """重置用户上下文."""
    _user_context.reset(token)
    logger.debug("UserContext reset")


def get_user_context_or_none() -> UserContext | None:
    """获取当前上下文, 无则返回 None (不 raise)."""
    return _user_context.get()


def replace_user_context(**updates: object) -> Token[UserContext | None]:
    """基于当前上下文替换部分字段, 返回可 reset 的 token."""
    from dataclasses import replace

    ctx = get_user_context()
    return set_user_context(replace(ctx, **updates))
