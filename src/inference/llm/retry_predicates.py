"""LLM 调用异常判定 - 可重试错误的统一白名单.

主 Agent 和子 Agent(web_research / geo_research)的 ModelRetryMiddleware 共用此模块,
杜绝子 Agent 默认重试所有异常导致重试风暴.

核心原则: 只重试"下次大概率不一样"的错误.
"""

from __future__ import annotations

import asyncio

import httpx
import openai


def is_retryable_llm_exception(exc: Exception) -> bool:
    """判断 LLM 调用异常是否值得重试.

    可重试(瞬时错误, 下次大概率成功):
    - RateLimitError / InternalServerError (服务端瞬时)
    - APIConnectionError(cause=ConnectError/ConnectTimeout) (连接建立阶段)
    - APITimeoutError (send_request 阶段超时, 注意: 不是 asyncio.TimeoutError)

    不重试(确定错误, 重试无意义):
    - SSE 流中途断开 (RemoteProtocolError / ReadError) — 同样请求大概率同样结果
    - asyncio.TimeoutError (总时长超限) — 重试更长更不可能完成
    - 客户端错误 (BadRequestError / AuthenticationError 等)
    - 未知异常 — 保守不重试

    Args:
        exc: 捕获的异常

    Returns:
        True 表示值得重试, False 表示立即失败

    """
    if isinstance(exc, openai.RateLimitError | openai.InternalServerError):
        return True
    # APITimeoutError 是 APIConnectionError 的子类, 必须先检查
    if isinstance(exc, openai.APITimeoutError):
        return True
    if isinstance(exc, openai.APIConnectionError):
        cause = exc.__cause__
        return isinstance(cause, httpx.ConnectError | httpx.ConnectTimeout)
    return False


def format_llm_failure_message(exc: Exception) -> str:
    """LLM 调用失败后返回给 Agent 的用户可见消息.

    用于 ModelRetryMiddleware 的 on_failure 回调,
    在重试耗尽后将异常转为友好的工具消息返回给 LLM.
    """
    if isinstance(exc, TimeoutError | asyncio.TimeoutError):
        return "模型响应超时(可能是输出内容过长), 请尝试简化请求或分步处理."
    if isinstance(exc, openai.APIConnectionError):
        cause = exc.__cause__
        if isinstance(cause, httpx.ConnectError | httpx.ConnectTimeout):
            return "无法连接模型服务, 请检查网络后重试."
        return "模型连接中断, 请稍后重试."
    if isinstance(exc, httpx.RemoteProtocolError | httpx.ReadError):
        return "模型输出被中断(可能是内容过长), 请尝试简化请求."
    if isinstance(exc, openai.RateLimitError):
        return "模型当前负载过高, 请稍后重试."
    if isinstance(exc, openai.InternalServerError):
        return "模型服务暂时不可用, 请稍后重试."
    if isinstance(exc, openai.BadRequestError):
        return "请求格式有误, 请检查输入内容."
    if isinstance(exc, openai.AuthenticationError):
        return "模型认证失败, 请联系管理员."
    return f"模型调用失败({type(exc).__name__}), 请稍后重试."


__all__ = [
    "format_llm_failure_message",
    "is_retryable_llm_exception",
]
