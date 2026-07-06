"""置顶记忆格式化器.

将置顶记忆格式化逻辑从应用层下沉到存储层,提供高效的格式化接口.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class PinnedMemoryFormatter:
    """置顶记忆格式化器.

    负责在存储层对置顶记忆数据进行格式化处理,包括:
    - 基本信息格式化
    - 口味偏好格式化
    - 完整置顶记忆格式化
    """

    def __init__(self) -> None:
        """初始化置顶记忆格式化器."""
        logger.debug("📍 初始化PinnedMemoryFormatter")

    async def format_pinned_memory(
        self,
        pinned_memory_dict: dict[str, Any],
        format_template: str = "markdown",
    ) -> str:
        """格式化置顶记忆为指定格式.

        Args:
            pinned_memory_dict: 置顶记忆字典
            format_template: 格式化模板,目前仅支持 "markdown"

        Returns:
            格式化后的置顶记忆字符串,无内容时返回空字符串

        """
        try:
            if format_template != "markdown":
                logger.warning(
                    "不支持的格式模板: %s,使用默认markdown",
                    format_template,
                )
                format_template = "markdown"

            if not pinned_memory_dict or not isinstance(pinned_memory_dict, dict):
                logger.debug("置顶记忆数据为空或类型错误")
                return ""

            logger.debug("开始格式化置顶记忆数据")
            sections = []

            # 基本信息
            basic_info = pinned_memory_dict.get("basic_info", "")
            if basic_info and basic_info.strip():
                sections.append(f"[Basic Info]\n{basic_info.strip()}")
                logger.debug("基本信息格式化完成")

            # 口味偏好
            preferences = pinned_memory_dict.get("preferences", "")
            if preferences and preferences.strip():
                sections.append(f"[Preferences]\n{preferences.strip()}")
                logger.debug("口味偏好格式化完成")

            result = "\n\n".join(sections) if sections else ""
            logger.debug(f"置顶记忆格式化完成,输出长度: {len(result)}")
            return result

        except Exception as e:
            logger.error("格式化置顶记忆失败: %s", e)
            return ""

    def sanitize_pinned_memory_data(
        self,
        pinned_memory_dict: dict[str, Any],
    ) -> dict[str, Any]:
        """清理和标准化置顶记忆数据.

        Args:
            pinned_memory_dict: 原始置顶记忆字典

        Returns:
            清理后的置顶记忆字典

        """
        try:
            if not pinned_memory_dict or not isinstance(pinned_memory_dict, dict):
                return {
                    "basic_info": "",
                    "preferences": "",
                }

            # 确保所有字段存在且为字符串类型
            sanitized = {}
            for field in ["basic_info", "preferences"]:
                value = pinned_memory_dict.get(field, "")
                if not isinstance(value, str):
                    value = str(value) if value is not None else ""
                # 清理多余的空白字符
                sanitized[field] = value.strip()

            return sanitized

        except Exception as e:
            logger.error("清理置顶记忆数据失败: %s", e)
            return {
                "basic_info": "",
                "preferences": "",
            }


# 工厂函数
def create_pinned_memory_formatter() -> PinnedMemoryFormatter:
    """创建置顶记忆格式化器实例.

    Returns:
        置顶记忆格式化器实例

    """
    return PinnedMemoryFormatter()


# 导出
__all__ = [
    "PinnedMemoryFormatter",
    "create_pinned_memory_formatter",
]
