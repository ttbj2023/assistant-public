"""MCP响应格式化器基类 - 定义MCP工具响应的标准化后处理接口.

职责:
- 将MCP原始响应转为LLM友好的格式化文本
- 压缩token消耗, 提取关键字段, 生成引用标记
- 格式化失败时降级返回原始响应

设计原则:
- 纯函数: 无状态, 输入原始响应输出文本
- 不崩溃: 格式化异常时返回原始数据
- 配置驱动: 通过config.yaml的response_formatters映射到具体实现类
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any

logger = logging.getLogger(__name__)


class BaseMcpResponseFormatter(ABC):
    """MCP响应格式化器基类.

    子类需要实现 format() 方法, 将MCP原始响应转为LLM友好的文本.

    输入raw_response可能是:
    - str: 从fastmcp CallToolResult提取的纯文本(当前主要路径)
    - tuple: (content_list, artifact) 格式(旧版兼容)
    - 其他: 直接str()降级
    """

    @abstractmethod
    def format(self, raw_response: Any, **kwargs: Any) -> str:
        """将MCP原始响应转为LLM友好的格式化文本.

        Args:
            raw_response: MCP工具返回的原始响应
                当前主要格式: str (从fastmcp提取的文本)
                旧版兼容格式: (content_list, artifact) 元组
            **kwargs: 额外上下文(如原始查询参数)

        Returns:
            格式化后的文本字符串

        """

    def safe_format(self, raw_response: Any, **kwargs: Any) -> str:
        """安全格式化: 失败时降级返回原始数据.

        Args:
            raw_response: MCP原始响应
            **kwargs: 额外上下文

        Returns:
            格式化文本或降级的原始字符串

        """
        try:
            return self.format(raw_response, **kwargs)
        except Exception as e:
            logger.warning("MCP响应格式化失败, 降级返回原始数据: %s", e)
            return self._fallback(raw_response)

    @staticmethod
    def _fallback(raw_response: Any) -> str:
        """降级处理: 将原始响应转为字符串."""
        if isinstance(raw_response, str):
            return raw_response
        if isinstance(raw_response, tuple):
            content_list, _artifact = raw_response
            if isinstance(content_list, list) and content_list:
                first = content_list[0]
                if isinstance(first, dict) and "text" in first:
                    return first["text"]
            return str(content_list)
        return str(raw_response)

    @staticmethod
    def extract_text(raw_response: Any) -> str:
        """从MCP响应中提取原始文本字段.

        Args:
            raw_response: MCP原始响应

        Returns:
            提取的文本内容

        """
        if isinstance(raw_response, str):
            return raw_response
        if isinstance(raw_response, tuple):
            content_list, _artifact = raw_response
            if isinstance(content_list, list) and content_list:
                first = content_list[0]
                if isinstance(first, dict) and "text" in first:
                    return first["text"]
        return ""


__all__ = ["BaseMcpResponseFormatter"]
