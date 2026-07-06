"""增强版ChatOllama - 支持bind()覆盖options参数.

langchain-ollama的ChatOllama._chat_params()将构造参数放在嵌套的options字典中,
而bind()传入的kwargs落在顶层, 不会合并进options_dict.
本模块子类化ChatOllama, 修复这个问题, 使bind()行为与ChatOpenAI/ChatAnthropic一致.
"""

from __future__ import annotations

from typing import Any, override

from langchain_core.messages import BaseMessage
from langchain_ollama import ChatOllama

_OLLAMA_OPTION_KEYS = frozenset({
    "temperature",
    "top_p",
    "top_k",
    "num_predict",
    "num_ctx",
    "repeat_penalty",
    "repeat_last_n",
    "seed",
    "mirostat",
    "mirostat_eta",
    "mirostat_tau",
    "num_gpu",
    "num_thread",
    "tfs_z",
    "stop",
})


class ChatOllamaEnhanced(ChatOllama):
    """增强版ChatOllama, 支持bind()覆盖options参数.

    用法与ChatOllama完全相同, 但bind()传入的options参数会正确覆盖构造时的值.
    """

    @override
    def _chat_params(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        options_overrides = {
            k: kwargs.pop(k) for k in list(kwargs) if k in _OLLAMA_OPTION_KEYS
        }

        result = super()._chat_params(messages, stop, **kwargs)

        if options_overrides:
            result["options"] = {**result["options"], **options_overrides}

        return result
