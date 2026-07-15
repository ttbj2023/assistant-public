"""处理器基类 - 定义处理器接口和通用功能."""

from __future__ import annotations

import logging
from abc import ABC
from dataclasses import dataclass, field
from typing import Any

from langchain_core.messages import BaseMessage

logger = logging.getLogger(__name__)


@dataclass
class MessageContext:
    """消息上下文 - 区分历史轮次与当前轮内容.

    LocalMemoryProcessor 通过 build_messages_context() 返回此结构,
    使历史对话以原生 messages 数组形式传递给 LLM.

    Attributes:
        history_messages: 历史轮次对应的 message 列表(含伪对话轮索引区).
        current_content: 当前轮 HumanMessage 的文本内容(含 XML 标签).
        system_prompt_extension: 追加到 system_prompt 尾部的额外内容,
            例如置顶记忆. 为空字符串时不拼接.

    """

    history_messages: list[BaseMessage] = field(default_factory=list)
    current_content: str = ""
    system_prompt_extension: str = ""


class BaseProcessor(ABC):
    """处理器基类 - 定义处理器的通用接口和功能."""

    def __init__(self, config: dict[str, Any] | None) -> None:
        """初始化处理器.

        Args:
            config: 应用配置实例

        """
        self.config = config

    async def build_messages_context(
        self,
        user_input: str,
        user_id: str,
        thread_id: str,
        agent_id: str | None = None,
        processor_config: dict | None = None,
        timezone: str = "Asia/Shanghai",
    ) -> MessageContext:
        """构建消息上下文 (原生 messages 数组路径).

        子类重写本方法, 返回 history_messages + current_content +
        system_prompt_extension (置顶记忆等).

        Returns:
            MessageContext: 含历史消息列表和当前轮内容.

        Raises:
            NotImplementedError: 子类未重写时触发.

        """
        raise NotImplementedError(
            f"{self.__class__.__name__} 未实现 build_messages_context, 请重写",
        )

    async def initialize(self) -> None:
        """初始化处理器.

        子类可以重写此方法进行特定初始化工作.
        """
        logger.debug(f"{self.__class__.__name__} 初始化完成")

    async def cleanup(self) -> None:
        """清理处理器资源.

        子类可以重写此方法进行特定清理工作.
        """
        logger.debug(f"{self.__class__.__name__} 资源清理完成")

    async def get_processor_stats(self) -> dict[str, Any]:
        """获取处理器统计信息.

        Returns:
            处理器统计信息字典

        """
        return {
            "processor_type": self.__class__.__name__,
            "config": self.config is not None,
        }

    def get_prompt_hint(self, agent_config: Any = None) -> str:  # noqa: ARG002
        """返回注入系统提示词的记忆格式描述.

        描述该处理器注入到对话中的XML标签/历史格式等, 供LLM理解上下文结构.
        默认空字符串(不注入), 子类按记忆类型重写.

        Args:
            agent_config: Agent配置, 用于条件判断

        Returns:
            格式描述文本, 空字符串表示不注入

        """
        return ""
