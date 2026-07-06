"""Prompt捕获和存储工具 - 调试专用

用于在调试模式下捕获传入LangChain之前的完整prompt内容,
包含用户输入, 记忆上下文, 系统提示等所有信息, 便于分析和调试.

使用环境变量控制:
- DEBUG=true: 启用调试模式, 自动开启prompt捕获
- PROMPT_CAPTURE_DIR: 自定义存储目录 (默认: logs/prompts)

记忆系统以 messages 数组形式传递历史, prompt 结构为
"system_prompt + history_messages 数组 + 当前 HumanMessage". 本捕获器同步
记录 history_messages 数组 (简化为 {type, content} 结构), 便于报告分析器
正确识别对话历史/索引区.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from langchain_core.messages import BaseMessage

_TRUE_VALUES = frozenset({"true", "1", "yes"})


def _is_debug_enabled() -> bool:
    return os.getenv("DEBUG", "").lower() in _TRUE_VALUES


logger = logging.getLogger(__name__)


def _serialize_history_messages(
    messages: list[BaseMessage] | None,
) -> tuple[list[dict[str, str]], int]:
    """把 BaseMessage 列表序列化为 JSON 友好的简化结构.

    调试场景只需要 type + content, 丢弃 tool_calls / id / additional_kwargs 等
    LangChain 内部字段 (体积可控, 报告易读). 多模态 content (list) 转为占位字符串.

    Returns:
        (序列化后的列表, 所有 content 长度之和)

    """
    if not messages:
        return [], 0
    serialized: list[dict[str, str]] = []
    total_length = 0
    for msg in messages:
        msg_type = getattr(msg, "type", "unknown")
        raw_content = getattr(msg, "content", "")
        if isinstance(raw_content, str):
            content_str = raw_content
        else:
            # 多模态/list content: 转字符串占位, 避免序列化图片 base64 等大对象
            content_str = f"[非文本 content: {type(raw_content).__name__}]"
        serialized.append({"type": msg_type, "content": content_str})
        total_length += len(content_str)
    return serialized, total_length


class PromptCapture:
    """Prompt捕获和存储工具

    在调试模式下捕获传入LangChain之前的完整prompt内容,
    并将其保存为JSON文件以便后续分析.
    """

    def __init__(
        self,
        enabled: bool | None = None,
        storage_dir: str | None = None,
    ) -> None:
        """初始化Prompt捕获器

        Args:
            enabled: 是否启用捕获, None时根据DEBUG环境变量判断
            storage_dir: 存储目录, None时使用默认路径或环境变量
        """
        if enabled is not None:
            self.enabled = enabled
        else:
            self.enabled = _is_debug_enabled()

        if storage_dir is not None:
            self.storage_dir = Path(storage_dir)
        else:
            self.storage_dir = Path(os.getenv("PROMPT_CAPTURE_DIR", "logs/prompts"))

        if self.enabled:
            try:
                self.storage_dir.mkdir(parents=True, exist_ok=True)
            except Exception:
                self.enabled = False

    def capture_prompt(
        self,
        user_content: str,
        system_prompt: str,
        user_id: str,
        thread_id: str,
        agent_id: str,
        metadata: dict[str, Any] | None = None,
        history_messages: list[BaseMessage] | None = None,
    ) -> str | None:
        """捕获完整的prompt内容

        Args:
            user_content: 当前轮 HumanMessage 的文本内容 (含 XML 标签)
            system_prompt: 系统提示 (已含 <pinned_memory> 附录)
            user_id: 用户ID
            thread_id: 线程ID
            agent_id: Agent ID
            metadata: 额外的元数据信息
            history_messages: 历史轮次 message 列表. 序列化为 [{type, content}, ...] 存档.

        Returns:
            保存的文件路径, 如果未启用捕获则返回None

        """
        if not self.enabled:
            return None

        timestamp = datetime.now().isoformat()
        safe_timestamp = timestamp.replace(":", "-").replace(".", "-")[:19]
        filename = f"prompt_{safe_timestamp}_{user_id}_{thread_id}.json"
        filepath = self.storage_dir / filename

        serialized_history, history_total_length = _serialize_history_messages(
            history_messages,
        )

        prompt_data = {
            "timestamp": timestamp,
            "user_id": user_id,
            "thread_id": thread_id,
            "agent_id": agent_id,
            "system_prompt": system_prompt,
            "user_content": user_content,
            "history_messages": serialized_history,
            "metadata": metadata or {},
            "capture_info": {
                "total_user_content_length": len(user_content),
                "system_prompt_length": len(system_prompt),
                "history_messages_count": len(serialized_history),
                "history_total_length": history_total_length,
            },
        }

        try:
            with Path(filepath).open("w", encoding="utf-8") as f:
                json.dump(prompt_data, f, ensure_ascii=False, indent=2)

            logger.debug("已保存prompt到: %s", filepath)
            return str(filepath)

        except Exception as e:
            logger.debug("保存prompt时出错: %s", e)
            return None

    def is_enabled(self) -> bool:
        """检查是否启用了捕获功能"""
        return self.enabled

    def get_storage_dir(self) -> Path:
        """获取存储目录路径"""
        return self.storage_dir

    def get_captured_files(self, limit: int = 100) -> list[str]:
        """获取已捕获的文件列表

        Args:
            limit: 返回的最大文件数量

        Returns:
            按修改时间排序的文件路径列表

        """
        if not self.enabled or not self.storage_dir.exists():
            return []

        try:
            files = list(self.storage_dir.glob("prompt_*.json"))
            files.sort(key=lambda x: x.stat().st_mtime, reverse=True)
            return [str(f) for f in files[:limit]]

        except Exception:
            return []


def get_prompt_capture() -> PromptCapture:
    """获取全局Prompt捕获实例"""
    return PromptCapture()


def capture_prompt(
    user_content: str,
    system_prompt: str,
    user_id: str,
    thread_id: str,
    agent_id: str,
    metadata: dict[str, Any] | None = None,
    history_messages: list[BaseMessage] | None = None,
) -> str | None:
    """便捷函数: 捕获prompt内容

    Args:
        user_content: 当前轮用户输入内容
        system_prompt: 系统提示
        user_id: 用户ID
        thread_id: 线程ID
        agent_id: Agent ID
        metadata: 额外的元数据信息
        history_messages: 历史轮次 message 列表

    Returns:
        保存的文件路径, 如果未启用捕获则返回None

    """
    capture = get_prompt_capture()
    return capture.capture_prompt(
        user_content=user_content,
        system_prompt=system_prompt,
        user_id=user_id,
        thread_id=thread_id,
        agent_id=agent_id,
        metadata=metadata,
        history_messages=history_messages,
    )
