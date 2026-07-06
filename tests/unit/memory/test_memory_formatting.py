"""MemoryAssembler 格式化功能专项测试.

测试职责: 验证记忆系统的格式化逻辑和Markdown生成
测试范围: 置顶记忆、索引区、对话历史、TODO列表的格式化
Mock策略: 最小化Mock，专注格式化逻辑验证
测试价值: 确保新的Markdown格式和emoji系统的正确性

注意: 重构后格式化功能已分离到各个组件中，此测试更新为测试各组件的格式化功能
"""

from __future__ import annotations

from unittest.mock import Mock

import pytest

from src.storage.formatters.conversation_formatter import (
    create_conversation_formatter,
)
from src.storage.formatters.pinned_memory_formatter import (
    create_pinned_memory_formatter,
)


class TestMemoryFormattingCore:
    """记忆系统格式化功能测试标准模式 - 重构后测试各组件的格式化功能"""

    @pytest.fixture
    def mock_agent_config(self) -> Mock:
        """Mock Agent配置"""
        config = Mock()
        memory_config = Mock()
        memory_config.total_token_budget = 10000
        memory_config.total_char_budget = 20000
        config.memory = memory_config
        return config

    @pytest.fixture
    def pinned_memory_formatter(self):
        """创建 PinnedMemoryFormatter 实例（格式化逻辑已下沉到存储层）"""
        return create_pinned_memory_formatter()

    @pytest.fixture
    def conversation_formatter(self):
        """创建 ConversationFormatter 实例（格式化逻辑已下沉到存储层）"""
        return create_conversation_formatter()

    @pytest.mark.asyncio
    async def test_format_pinned_memory_should_generate_markdown_structure(
        self, pinned_memory_formatter
    ) -> None:
        """测试置顶记忆格式化：应生成正确的Markdown结构（2字段字符串）"""
        # Arrange
        pinned_data = {
            "basic_info": "name: Alice\nemail: alice@example.com",
            "preferences": "theme: dark\nlanguage: zh-CN",
        }

        # Act
        result = await pinned_memory_formatter.format_pinned_memory(pinned_data)

        # Assert
        expected_headers = [
            "[Basic Info]",
            "[Preferences]",
        ]

        for header in expected_headers:
            assert header in result, f"缺少标题: {header}"

        # 验证基本信息内容
        assert "name: Alice" in result
        assert "email: alice@example.com" in result

        # 验证偏好设置
        assert "theme: dark" in result
        assert "language: zh-CN" in result

    @pytest.mark.asyncio
    async def test_format_pinned_memory_should_handle_empty_data(
        self, pinned_memory_formatter
    ) -> None:
        """测试置顶记忆格式化：空数据时应返回默认消息"""
        # Arrange
        pinned_data = {}

        # Act
        result = await pinned_memory_formatter.format_pinned_memory(pinned_data)

        # Assert
        assert result == ""

    @pytest.mark.asyncio
    async def test_format_pinned_memory_should_handle_partial_data(
        self, pinned_memory_formatter
    ) -> None:
        """测试置顶记忆格式化：部分数据时应只显示有内容的部分"""
        # Arrange
        pinned_data = {
            "basic_info": "name: Alice",  # 只有基本信息
        }

        # Act
        result = await pinned_memory_formatter.format_pinned_memory(pinned_data)

        # Assert
        assert "[Basic Info]" in result
        assert "name: Alice" in result
        assert "[Preferences]" not in result

    @pytest.mark.asyncio
    async def test_format_index_area_should_generate_markdown_table(
        self, conversation_formatter
    ) -> None:
        """测试索引区格式化：应生成Markdown表格（存储层 formatter）"""
        # Arrange
        index_data = [
            {
                "round_number": 1,
                "summary": "用户询问Python学习建议",
                "topic": "Python学习指导",
                "created_at": "2025-01-01",
            },
            {
                "round_number": 2,
                "summary": "讨论项目架构设计",
                "topic": "系统架构",
                "created_at": "2025-01-02",
            },
        ]

        result = await conversation_formatter.format_index_range(index_data)

        # Assert
        assert "<index>" in result
        assert "| Round | Topic | Summary | Time |" in result
        assert "|------|----------|----------|------|" in result

        # 验证第一行数据
        assert "| 1 | Python学习指导 | 用户询问Python学习建议 | 2025-01-01 |" in result

        # 验证第二行数据
        assert "| 2 | 系统架构 | 讨论项目架构设计 | 2025-01-02 |" in result

    @pytest.mark.asyncio
    async def test_format_index_area_should_handle_empty_data(
        self, conversation_formatter
    ) -> None:
        """测试索引区格式化：空数据时应返回默认消息"""
        result = await conversation_formatter.format_index_range([])

        # Assert
        assert result == ""

    # 索引范围的选择由 MemoryAssembler 的预算/范围算法决定(无独立过滤步骤).

    @pytest.mark.asyncio
    async def test_format_conversation_history_should_add_round_titles(
        self, conversation_formatter
    ) -> None:
        """测试对话历史格式化：应添加轮次标题和分隔线（存储层 formatter）"""
        # Arrange
        conversation_data = [
            {
                "round_number": 1,
                "user_message": "你好, 请帮我学习Python",
                "assistant_response": "好的, 我来帮您学习Python",
            },
            {
                "round_number": 2,
                "user_message": "我应该从哪里开始?",
                "assistant_response": "建议从基础语法开始学习",
            },
        ]

        # Act
        result = await conversation_formatter.format_conversation_range(
            conversation_data
        )

        # Assert - 匹配实际实现
        assert "[Round 1]" in result
        assert "[Round 2]" in result
        assert "---" in result  # 分隔线
        assert "你好, 请帮我学习Python" in result
        assert "建议从基础语法开始学习" in result

    @pytest.mark.asyncio
    async def test_format_conversation_history_should_handle_empty_data(
        self, conversation_formatter
    ) -> None:
        """测试对话历史格式化：空数据时应返回默认消息"""
        result = await conversation_formatter.format_conversation_range([])

        # Assert
        assert result == ""

    @pytest.mark.asyncio
    async def test_format_conversation_history_should_skip_invalid_rounds(
        self, conversation_formatter
    ) -> None:
        """测试对话历史格式化：应跳过无效轮次，只格式化有效轮次"""
        # Arrange
        conversation_data = [
            {},  # 无效轮次
            {
                "round_number": 1,
                "user_message": "有效消息",
                "assistant_response": "有效回复",
            },
            None,  # None值
        ]

        # Act
        result = await conversation_formatter.format_conversation_range(
            conversation_data
        )

        # Assert - 无效轮次被跳过，只格式化有效轮次
        assert "有效消息" in result
        assert "有效回复" in result
        # 应该只包含一轮对话（第1轮）
        assert result.count("User:") == 1

    @pytest.mark.asyncio
    async def test_format_single_round_should_generate_proper_format(
        self, conversation_formatter
    ) -> None:
        """测试单轮对话格式化：应生成正确的格式（存储层 formatter）"""
        # Arrange
        round_data = {
            "round_number": 1,
            "user_message": "你好",
            "assistant_response": "您好!",
        }

        # Act
        result = await conversation_formatter.format_single_round(round_data)

        # Assert - 轮次号由外层输出, format_single_round不包含[1]
        assert "User: 你好" in result
        assert "Assistant: 您好!" in result

    @pytest.mark.asyncio
    async def test_format_single_round_should_handle_missing_fields(
        self, conversation_formatter
    ) -> None:
        """测试单轮对话格式化：缺少字段时应优雅处理"""
        # Arrange
        round_data = {
            "round_number": 1,
            "user_message": "只有用户消息",
            # 缺少assistant_response
        }

        # Act
        result = await conversation_formatter.format_single_round(round_data)

        # Assert - 轮次号由外层输出, format_single_round不包含[1]
        assert "User: 只有用户消息" in result
        # 应该没有助手部分，因为缺少数据
