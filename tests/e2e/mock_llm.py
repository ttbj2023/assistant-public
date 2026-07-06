"""E2E 可编程 Mock LLM.

从 src/inference/mock_providers.py 迁移, 仅服务 E2E 测试.
兼容 LangChain create_agent 的 bind_tools/ainvoke 接口,
支持通过 set_script() 注入预设响应序列触发工具调用.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any, ClassVar

from langchain_core.messages import AIMessage

DEFAULT_LLM_RESPONSE = "这是一个模拟的LLM响应"


class E2EMockLLM:
    """E2E 可编程 Mock LLM.

    兼容 LangChain create_agent 的 bind_tools/ainvoke 接口,
    支持通过 set_script() 注入预设响应序列触发工具调用.

    工作原理:
        1. create_agent 调用 llm.bind_tools(tools) → 返回 self
        2. 图模型节点调用 bound.ainvoke(messages) → 消费 _script 队列
        3. 队列为空时返回默认响应 (tool_calls=[] 终止 agent 循环)

    用法:
        E2EMockLLM.set_script([
            AIMessage(content="", tool_calls=[{"name": "create_todo", ...}]),
            AIMessage(content="已完成", tool_calls=[]),
        ])
        # 执行 HTTP 请求...
        E2EMockLLM.get_last_input()  # 捕获最后一次 ainvoke 输入
    """

    _script: ClassVar[list[AIMessage]] = []
    _last_input: ClassVar[Any] = None

    def __init__(self, default_content: str = DEFAULT_LLM_RESPONSE) -> None:
        self.default_content = default_content

    @classmethod
    def set_script(cls, messages: list[AIMessage]) -> None:
        """注入预设响应序列 (每个测试前调用)."""
        cls._script = list(messages)

    @classmethod
    def clear(cls) -> None:
        """清空脚本队列和输入捕获 (每个测试后调用)."""
        cls._script = []
        cls._last_input = None

    @classmethod
    def get_last_input(cls) -> Any:
        """获取最后一次 ainvoke 的输入消息 (验证 prompt 组装)."""
        return cls._last_input

    def _consume(self) -> AIMessage:
        """消费脚本队列, 为空时返回默认响应."""
        if E2EMockLLM._script:
            return E2EMockLLM._script.pop(0)
        return AIMessage(content=self.default_content, tool_calls=[])

    def bind_tools(
        self,
        _tools: Any,
        *,
        _tool_choice: Any = None,
        **_kwargs: Any,
    ) -> E2EMockLLM:
        """绑定工具列表, 返回自身 (create_agent 要求)."""
        return self

    def bind(self, **_kwargs: Any) -> E2EMockLLM:
        """绑定参数, 返回自身."""
        return self

    async def ainvoke(
        self,
        messages: Any,
        **_kwargs: Any,
    ) -> AIMessage:
        """异步调用, 消费脚本队列."""
        E2EMockLLM._last_input = messages
        return self._consume()

    def invoke(self, messages: Any, **_kwargs: Any) -> AIMessage:
        """同步调用 (内容分析器路径可能使用)."""
        E2EMockLLM._last_input = messages
        return self._consume()

    async def astream(
        self,
        messages: Any,
        **_kwargs: Any,
    ) -> AsyncGenerator[AIMessage, None]:
        """流式调用, yield 单条 AIMessage."""
        E2EMockLLM._last_input = messages
        yield self._consume()

    async def abatch(
        self,
        messages_list: list[Any],
        **_kwargs: Any,
    ) -> list[AIMessage]:
        """批量调用."""
        return [self._consume() for _ in messages_list]


def create_mock_llm(response_content: str | None = None) -> E2EMockLLM:
    """创建LLM Mock对象.

    Args:
        response_content: 默认响应内容 (脚本队列为空时使用)

    Returns:
        E2EMockLLM 实例, 兼容 create_agent 的 bind_tools/ainvoke 接口

    """
    return E2EMockLLM(default_content=response_content or DEFAULT_LLM_RESPONSE)
