"""轻量级工具调用追踪器 - 调试专用工具

专注于工具调用监控的极简实现:
- 零开销设计: DEBUG=false时立即返回, 无性能影响
- 专注核心: 只记录工具调用和LLM执行事件
- 结构化输出: JSON格式便于分析工具处理
- 环境变量控制: 通过DEBUG环境变量统一开关

使用方式:
    from scripts.debug.tool_call_tracker import ToolCallTracker

    callbacks = [ToolCallTracker()] if DEBUG_ENABLED else []
    agent = create_agent(llm, tools, callbacks=callbacks)
"""

from __future__ import annotations

import json
import logging
import os
import pathlib
import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Protocol, override

if TYPE_CHECKING:
    from uuid import UUID

logger = logging.getLogger(__name__)

_TRUE_VALUES = frozenset({"true", "1", "yes"})


def _is_debug_enabled() -> bool:
    return os.getenv("DEBUG", "").lower() in _TRUE_VALUES


class ToolOutputProtocol(Protocol):
    """工具输出协议"""

    @property
    def content(self) -> str | Any: ...


def truncate_content(content: str, max_length: int = 200) -> str:
    """内容截断函数 - 减少日志大小, 保留足够的诊断信息.

    Args:
        content: 要截断的内容
        max_length: 最大保留字符数

    Returns:
        截断后的内容
    """
    if not content:
        return ""
    return content[:max_length] + "..." if len(content) > max_length else content


from langchain_core.callbacks.base import BaseCallbackHandler


class ToolCallTracker(BaseCallbackHandler):
    """轻量级工具调用追踪器

    极简设计, 专注于核心功能:
    - 工具调用监控(开始, 结束, 错误)
    - LLM调用监控(开始, 结束, 错误)
    - 性能统计和慢查询检测
    - 零开销实现
    """

    def __init__(self, log_file: str | None = None) -> None:
        """初始化工具调用追踪器

        Args:
            log_file: 可选的日志文件路径, 默认使用logs/tool_calls.json
        """
        self.enabled = _is_debug_enabled()

        if not self.enabled:
            # (start_time, tool_name) 缓存, on_tool_error 通过 run_id 回查工具名
            self._tool_timers: dict[UUID, tuple[float, str]] | None = None
            # (start_time, model_name) 缓存, 供 on_chat_model_end/error 回查模型名
            self._llm_timers: dict[UUID, tuple[float, str]] | None = None
            self._log_file = None
            return

        self._tool_timers: dict[UUID, tuple[float, str]] | None = {}
        self._llm_timers: dict[UUID, tuple[float, str]] | None = {}

        if log_file is None:
            pathlib.Path("logs").mkdir(exist_ok=True, parents=True)
            timestamp = datetime.now(UTC).strftime("%Y-%m-%d_%H-%M-%S")
            self._log_file = f"logs/tool_calls_{timestamp}.json"
        else:
            self._log_file = log_file

        self._setup_logger()

        # 启用提示走 stdout, 不经 logger: 其 FileHandler 指向 JSONL 事件文件,
        # 文本提示会写入首行污染 JSONL, 导致 collect_tool_call_logs 需靠
        # suppress(json.JSONDecodeError) 兜底跳过.
        print(f"ToolCallTracker已启用, 日志文件: {self._log_file}")

    def _setup_logger(self) -> None:
        """配置专用日志记录器"""
        handler = logging.FileHandler(self._log_file, mode="a", encoding="utf-8")
        formatter = logging.Formatter("%(message)s")
        handler.setFormatter(formatter)

        logger.handlers.clear()
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)

    def _log_event(
        self,
        event_type: str,
        data: dict[str, str | int | float | bool | list | dict | None],
    ) -> None:
        """记录事件到JSON日志文件

        Args:
            event_type: 事件类型
            data: 事件数据
        """
        if not self.enabled:
            return

        event = {"ts": datetime.now(UTC).isoformat(), "type": event_type, "data": data}

        try:
            logger.info(json.dumps(event, ensure_ascii=False, separators=(",", ":")))
        except Exception as e:
            logger.warning("ToolCallTracker日志记录失败: %s", e)

    @override
    def on_tool_start(
        self,
        serialized: dict[str, Any],
        input_str: str,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        tags: list[str] | None = None,
        _metadata: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        """工具开始调用时触发

        LangChain BaseTool.run() 传入 serialized={"name": self.name, "description": ...}
        同时 kwargs 中也包含 name=self.name
        """
        if not self.enabled or self._tool_timers is None:
            return

        try:
            tool_name = serialized.get("name") or kwargs.get("name") or "unknown_tool"

            # 缓存 (start_time, tool_name), 供 on_tool_end/on_tool_error 回查
            self._tool_timers[run_id] = (time.time(), tool_name)

            tool_description = serialized.get("description", "")

            self._log_event(
                "tool_start",
                {
                    "tool_name": tool_name,
                    "tool_description": tool_description,
                    "input_preview": truncate_content(input_str),
                    "input_length": len(input_str) if input_str else 0,
                    "run_id": str(run_id),
                    "parent_run_id": str(parent_run_id) if parent_run_id else None,
                    "tags": tags or [],
                },
            )

        except Exception as e:
            logger.warning("工具开始回调记录失败: %s", e)

    @override
    def on_tool_end(
        self,
        output: str | ToolOutputProtocol,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        tags: list[str] | None = None,
        **kwargs: Any,
    ) -> None:
        """工具调用完成时触发

        LangChain CallbackManagerForToolRun.on_tool_end() 通过 **kwargs 传递 name=self.name,
        不传 serialized. 因此工具名从 kwargs["name"] 获取.
        """
        if not self.enabled or self._tool_timers is None:
            return

        try:
            cached = self._tool_timers.pop(run_id, None)
            if cached is None:
                return

            start_time, cached_name = cached
            duration = time.time() - start_time
            # kwargs["name"] 优先, 回查 on_tool_start 缓存的工具名
            tool_name = kwargs.get("name") or cached_name

            output_str = self._extract_output_content(output)

            self._log_event(
                "tool_end",
                {
                    "tool_name": tool_name,
                    "duration_ms": int(duration * 1000),
                    "success": self._detect_success(output_str),
                    "output_preview": truncate_content(output_str),
                    "output_length": len(output_str) if output_str else 0,
                    "run_id": str(run_id),
                    "parent_run_id": str(parent_run_id) if parent_run_id else None,
                    "tags": tags or [],
                },
            )

            if duration > 1.0:
                logger.warning("慢工具检测: %s 耗时 %.3fs", tool_name, duration)

        except Exception as e:
            logger.warning("工具结束回调记录失败: %s", e)

    @override
    def on_tool_error(  # type: ignore[override]
        self,
        error: Exception,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        tags: list[str] | None = None,
        **kwargs: Any,
    ) -> None:
        """工具调用出错时触发

        工具名优先从 kwargs["name"] 获取, 回退到 on_tool_start 缓存.
        """
        if not self.enabled or self._tool_timers is None:
            return

        try:
            cached = self._tool_timers.pop(run_id, None)
            if cached is None:
                start_time = time.time()
                cached_name = "unknown_tool"
            else:
                start_time, cached_name = cached

            duration = time.time() - start_time
            # kwargs["name"] 优先, 回查 on_tool_start 缓存的工具名
            tool_name = kwargs.get("name") or cached_name

            self._log_event(
                "tool_error",
                {
                    "tool_name": tool_name,
                    "duration_ms": int(duration * 1000),
                    "success": False,
                    "error_type": type(error).__name__,
                    "error_message": str(error)[:200],
                    "run_id": str(run_id),
                    "parent_run_id": str(parent_run_id) if parent_run_id else None,
                    "tags": tags or [],
                },
            )

            logger.error(
                "工具错误: %s - %s: %s", tool_name, type(error).__name__, error
            )

        except Exception as e:
            logger.warning("工具错误回调记录失败: %s", e)

    @override
    def on_chat_model_start(  # type: ignore[override]
        self,
        serialized: dict[str, str | int | float | bool | list | dict | None],
        messages: list[list[dict[str, str | int | float | bool | list | None]]],
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        tags: list[str] | None = None,
        _metadata: dict[str, str | int | float | bool | list | dict | None]
        | None = None,
        **_kwargs: Any,
    ) -> None:
        """聊天模型开始时触发"""
        if not self.enabled or self._llm_timers is None:
            return

        try:
            model_name = str(serialized.get("name", "unknown_model"))

            # 缓存 (start_time, model_name), 供 on_chat_model_end/error 回查
            self._llm_timers[run_id] = (time.time(), model_name)
            total_messages = (
                sum(len(msg_list) for msg_list in messages) if messages else 0
            )

            self._log_event(
                "llm_start",
                {
                    "model_name": model_name,
                    "total_messages": total_messages,
                    "run_id": str(run_id),
                    "parent_run_id": str(parent_run_id) if parent_run_id else None,
                    "tags": tags or [],
                },
            )

        except Exception as e:
            logger.warning("LLM开始回调记录失败: %s", e)

    @override
    def on_llm_end(
        self,
        response: Any,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        tags: list[str] | None = None,
        **kwargs: Any,
    ) -> None:
        """LLM/ChatModel 完成时触发.

        LangChain 回调体系中 chat model 的结束回调是 on_llm_end (不是 on_chat_model_end).
        BaseChatModel.astream() / generate() 在完成时都调用 run_manager.on_llm_end().
        """
        if not self.enabled or self._llm_timers is None:
            return

        try:
            cached = self._llm_timers.pop(run_id, None)
            if cached is None:
                return

            start_time, cached_model = cached
            duration = time.time() - start_time

            response_content = ""
            # LLMResult.generations[0][0].message.content
            try:
                if hasattr(response, "generations") and response.generations:
                    gen = response.generations[0][0]
                    msg = gen.message if hasattr(gen, "message") else gen
                    response_content = str(getattr(msg, "content", ""))
            except (IndexError, AttributeError):
                response_content = str(response)[:500]

            self._log_event(
                "llm_end",
                {
                    "model_name": cached_model,
                    "duration_ms": int(duration * 1000),
                    "success": True,
                    "content_preview": truncate_content(response_content),
                    "content_length": len(response_content),
                    "run_id": str(run_id),
                    "parent_run_id": str(parent_run_id) if parent_run_id else None,
                    "tags": tags or [],
                },
            )

        except Exception as e:
            logger.warning("LLM结束回调记录失败: %s", e)

    @override
    def on_llm_error(
        self,
        error: BaseException,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        tags: list[str] | None = None,
        **kwargs: Any,
    ) -> None:
        """LLM/ChatModel 出错时触发.

        同 on_llm_end, chat model 的错误回调也是 on_llm_error.
        """
        if not self.enabled or self._llm_timers is None:
            return

        try:
            cached = self._llm_timers.pop(run_id, None)
            if cached is None:
                start_time = time.time()
                cached_model = "unknown_model"
            else:
                start_time, cached_model = cached

            duration = time.time() - start_time

            self._log_event(
                "llm_error",
                {
                    "model_name": cached_model,
                    "duration_ms": int(duration * 1000),
                    "success": False,
                    "error_type": type(error).__name__,
                    "error_message": str(error)[:200],
                    "run_id": str(run_id),
                    "parent_run_id": str(parent_run_id) if parent_run_id else None,
                    "tags": tags or [],
                },
            )

            logger.error("LLM错误: %s: %s", type(error).__name__, error)

        except Exception as e:
            logger.warning("LLM错误回调记录失败: %s", e)

    def _detect_success(self, output_str: str) -> bool:
        """从工具输出中推断操作是否成功.

        检测规则:
        1. JSON格式 → 检查 "success" 字段
        2. 文本前缀 → 检查 "错误:" / "Error:"
        3. 默认 True

        Args:
            output_str: 工具输出的字符串内容

        Returns:
            操作是否成功
        """
        if not output_str:
            return True

        stripped = output_str.strip()

        if stripped.startswith("{"):
            try:
                data = json.loads(stripped)
                if isinstance(data, dict) and "success" in data:
                    return bool(data["success"])
            except (json.JSONDecodeError, ValueError):
                pass

        return not (stripped.startswith("错误:") or stripped.startswith("Error:"))

    def _extract_output_content(self, output: str | ToolOutputProtocol | None) -> str:
        """安全地从工具输出中提取字符串内容.

        Args:
            output: 工具输出, 可能是字符串, ToolMessage对象或其他类型

        Returns:
            提取的字符串内容
        """
        if output is None:
            return ""

        if isinstance(output, str):
            return output

        if hasattr(output, "content"):
            try:
                content = output.content
                if isinstance(content, str):
                    return content
                return str(content)
            except Exception:
                return str(output)

        for attr in ["text", "message", "result"]:
            if hasattr(output, attr):
                try:
                    value = getattr(output, attr)
                    if isinstance(value, str):
                        return value
                    return str(value)
                except Exception:
                    continue

        try:
            return str(output)
        except Exception:
            return ""


def create_tool_call_tracker(log_file: str | None = None) -> ToolCallTracker | list:
    """创建工具调用追踪器或返回空列表

    根据DEBUG环境变量决定是否启用追踪器, 提供统一的集成接口.
    零开销设计: DEBUG=false时立即返回空列表, 无任何实例创建.

    Args:
        log_file: 可选的日志文件路径

    Returns:
        ToolCallTracker实例或空列表
    """
    if not _is_debug_enabled():
        return []
    return ToolCallTracker(log_file)
