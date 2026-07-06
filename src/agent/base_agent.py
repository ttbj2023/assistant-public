"""Agent抽象基类 - 极简设计.

只定义核心接口,不包含具体实现,符合抽象基类设计原则.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any, NotRequired, TypedDict, override

if TYPE_CHECKING:
    from src.config.agent_config import AgentConfig

logger = logging.getLogger(__name__)


class ProcessMessageKwargs(TypedDict, total=False):
    """process_message方法的扩展参数类型定义."""

    model_id: NotRequired[str]
    temperature: NotRequired[float]
    max_tokens: NotRequired[int]
    metadata: NotRequired[dict[str, Any]]
    image_datas: NotRequired[list[dict[str, Any]]]
    attachment_infos: NotRequired[list[Any]]
    round_number: NotRequired[int]
    timezone: NotRequired[str]
    chat_messages: NotRequired[list[dict[str, Any]]]


class BaseAgent(ABC):
    """极简Agent抽象基类 - 只定义接口,不包含具体实现.

    设计原则:
    - 只定义核心接口,不包含具体业务逻辑
    - 保持接口简洁,职责单一
    - 为子类提供最大的实现自由度
    - 调试等横切功能通过装饰器实现

    Attributes:
        _config: Agent配置对象,类型安全的配置数据

    """

    def __init__(self, agent_config: AgentConfig) -> None:
        """初始化Agent抽象基类.

        Args:
            agent_config: Agent配置对象

        """
        self._config = agent_config

    @property
    def config(self) -> AgentConfig:
        """获取Agent配置对象.

        Returns:
            Agent配置对象的只读访问

        """
        return self._config

    @property
    def id(self) -> str:
        """获取Agent ID.

        Returns:
            Agent唯一标识符

        """
        return self._config.agent_id

    @abstractmethod
    async def process_message(
        self,
        message: str,
        user_id: str,
        thread_id: str,
        **kwargs: ProcessMessageKwargs,
    ) -> str:
        """处理用户消息 - 核心业务接口(非流式).

        子类必须实现此方法来定义具体的消息处理逻辑.

        Args:
            message: 用户输入消息
            user_id: 用户ID(用于数据隔离)
            thread_id: 对话线程ID
            **kwargs: 扩展参数(预留接口,用于未来扩展)

        Returns:
            Agent响应消息(完整字符串)

        Raises:
            Exception: 处理失败时抛出具体异常

        """

    @abstractmethod
    async def process_message_stream(
        self,
        message: str,
        user_id: str,
        thread_id: str,
        **kwargs: ProcessMessageKwargs,
    ) -> AsyncIterator[str]:
        """处理用户消息 - 流式响应接口.

        子类必须实现此方法来支持流式响应.

        Args:
            message: 用户输入消息
            user_id: 用户ID(用于数据隔离)
            thread_id: 对话线程ID
            **kwargs: 扩展参数(预留接口,用于未来扩展)

        Yields:
            Agent响应消息的内容片段

        Raises:
            Exception: 处理失败时抛出具体异常

        Note:
            - 流式响应不自动触发记忆存储
            - 必须在流结束后调用 finalize_conversation() 完成记忆存储

        """

    @abstractmethod
    async def finalize_conversation(
        self,
        user_input: str,
        response: str,
        user_id: str,
        thread_id: str,
        **kwargs: ProcessMessageKwargs,
    ) -> None:
        """完成对话处理 - 记忆存储(流式响应专用).

        在流式响应结束后调用此方法来存储对话数据到记忆系统.

        Args:
            user_input: 用户输入消息
            response: Agent完整响应(所有内容片段拼接后的结果)
            user_id: 用户ID(用于数据隔离)
            thread_id: 对话线程ID
            **kwargs: 扩展参数(预留接口,用于未来扩展)

        Raises:
            Exception: 存储失败时抛出具体异常

        Note:
            - 此方法主要用于流式响应场景
            - 非流式响应的process_message()应自动完成记忆存储
            - 子类应确保此方法的幂等性(多次调用不影响结果)

        """

    @abstractmethod
    async def initialize(self) -> None:
        """初始化Agent资源.

        子类必须实现此方法来初始化所需的资源和依赖.

        Raises:
            Exception: 初始化失败时抛出具体异常

        """

    @abstractmethod
    async def cleanup(self) -> None:
        """清理Agent资源.

        子类必须实现此方法来清理占用的资源.

        Raises:
            Exception: 清理失败时抛出具体异常

        """

    @override
    def __repr__(self) -> str:
        """字符串表示.

        Returns:
            Agent的字符串表示

        """
        return f"{self.__class__.__name__}(id='{self.id}', name='{self._config.name}')"
