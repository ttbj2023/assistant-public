"""置顶记忆格式化器测试.

测试新的存储层置顶记忆格式化功能。
"""

import pytest

from src.storage.formatters.pinned_memory_formatter import (
    PinnedMemoryFormatter,
    create_pinned_memory_formatter,
)


class TestPinnedMemoryFormatter:
    """置顶记忆格式化器测试类."""

    def test_create_pinned_memory_formatter(self) -> None:
        """测试创建置顶记忆格式化器."""
        formatter = create_pinned_memory_formatter()
        assert isinstance(formatter, PinnedMemoryFormatter)

    @pytest.mark.asyncio
    async def test_format_pinned_memory_empty_data(self) -> None:
        """测试格式化空置顶记忆."""
        formatter = create_pinned_memory_formatter()
        result = await formatter.format_pinned_memory({})
        assert result == ""

    @pytest.mark.asyncio
    async def test_format_pinned_memory_none_data(self) -> None:
        """测试格式化None置顶记忆."""
        formatter = create_pinned_memory_formatter()
        result = await formatter.format_pinned_memory(None)
        assert result == ""

    @pytest.mark.asyncio
    async def test_format_pinned_memory_invalid_type(self) -> None:
        """测试格式化无效类型置顶记忆."""
        formatter = create_pinned_memory_formatter()
        result = await formatter.format_pinned_memory("invalid")
        assert result == ""

    @pytest.mark.asyncio
    async def test_format_pinned_memory_basic_info_only(self) -> None:
        """测试仅格式化基本信息."""
        formatter = create_pinned_memory_formatter()
        pinned_data = {
            "basic_info": "这是用户的基本信息",
            "preferences": "",
        }

        result = await formatter.format_pinned_memory(pinned_data)

        assert "[Basic Info]" in result
        assert "这是用户的基本信息" in result
        assert "[Preferences]" not in result

    @pytest.mark.asyncio
    async def test_format_pinned_memory_preferences_only(self) -> None:
        """测试仅格式化偏好设置."""
        formatter = create_pinned_memory_formatter()
        pinned_data = {
            "basic_info": "",
            "preferences": "这是用户的偏好设置",
        }

        result = await formatter.format_pinned_memory(pinned_data)

        assert "[Basic Info]" not in result
        assert "[Preferences]" in result
        assert "这是用户的偏好设置" in result

    @pytest.mark.asyncio
    async def test_format_pinned_memory_complete_data(self) -> None:
        """测试格式化完整的置顶记忆."""
        formatter = create_pinned_memory_formatter()
        pinned_data = {
            "basic_info": "姓名：张三\n职业：工程师",
            "preferences": "喜欢简洁的回答\n偏好使用中文交流",
        }

        result = await formatter.format_pinned_memory(pinned_data)

        assert "[Basic Info]" in result
        assert "姓名：张三" in result
        assert "职业：工程师" in result

        assert "[Preferences]" in result
        assert "喜欢简洁的回答" in result
        assert "偏好使用中文交流" in result

        # 检查格式正确性
        sections = result.split("\n\n")
        assert len(sections) == 2
        assert result.count("[") >= 2

    @pytest.mark.asyncio
    async def test_format_pinned_memory_whitespace_handling(self) -> None:
        """测试空格和换行符处理."""
        formatter = create_pinned_memory_formatter()
        pinned_data = {
            "basic_info": "  基本信息前后有空格  ",
            "preferences": "  \n  偏好设置包含换行符  \n  ",
        }

        result = await formatter.format_pinned_memory(pinned_data)

        assert "基本信息前后有空格" in result
        assert "偏好设置包含换行符" in result

        # 检查没有多余空格
        assert "  " not in result.splitlines()[1]  # 基本信息行没有前后空格

    def test_sanitize_pinned_memory_data_valid(self) -> None:
        """测试清理有效数据."""
        formatter = create_pinned_memory_formatter()
        data = {
            "basic_info": "  基本信息  ",
            "preferences": "偏好设置",
        }

        result = formatter.sanitize_pinned_memory_data(data)

        assert result["basic_info"] == "基本信息"
        assert result["preferences"] == "偏好设置"

    def test_sanitize_pinned_memory_data_missing_fields(self) -> None:
        """测试清理缺少字段的数据."""
        formatter = create_pinned_memory_formatter()
        data = {
            "basic_info": "基本信息",
            # 缺少 preferences
        }

        result = formatter.sanitize_pinned_memory_data(data)

        assert result["basic_info"] == "基本信息"
        assert result["preferences"] == ""

    def test_sanitize_pinned_memory_data_wrong_type(self) -> None:
        """测试清理错误类型的数据."""
        formatter = create_pinned_memory_formatter()
        data = "not a dict"

        result = formatter.sanitize_pinned_memory_data(data)

        assert result["basic_info"] == ""
        assert result["preferences"] == ""

    def test_sanitize_pinned_memory_data_wrong_field_types(self) -> None:
        """测试清理字段类型错误的数据."""
        formatter = create_pinned_memory_formatter()
        data = {
            "basic_info": "基本信息",
            "preferences": None,
        }

        result = formatter.sanitize_pinned_memory_data(data)

        assert result["basic_info"] == "基本信息"
        assert result["preferences"] == ""

    @pytest.mark.asyncio
    async def test_format_pinned_memory_non_markdown_template(self) -> None:
        """测试非markdown模板时降级使用markdown."""
        formatter = create_pinned_memory_formatter()
        pinned_data = {
            "basic_info": "用户信息",
        }

        result = await formatter.format_pinned_memory(
            pinned_data,
            format_template="html",
        )

        assert "[Basic Info]" in result

    @pytest.mark.asyncio
    async def test_format_pinned_memory_exception_returns_empty(self) -> None:
        """测试格式化置顶记忆异常时返回空字符串."""
        formatter = create_pinned_memory_formatter()

        class BrokenDict(dict):
            """模拟get方法会抛异常的字典子类."""

            def __init__(self) -> None:
                super().__init__({"basic_info": "x"})

            def get(self, key: str, default: str = "") -> str:  # type: ignore[override]
                raise RuntimeError("模拟异常")

        result = await formatter.format_pinned_memory(BrokenDict())
        assert result == ""

    @pytest.mark.asyncio
    async def test_sanitize_pinned_memory_data_exception_returns_default(self) -> None:
        """测试清理数据异常时返回默认空字段字典."""
        formatter = create_pinned_memory_formatter()

        class BrokenDict(dict):
            """模拟get方法会抛异常的字典子类."""

            def __init__(self) -> None:
                super().__init__({"basic_info": "x"})

            def get(self, key: str, default: str = "") -> str:  # type: ignore[override]
                raise RuntimeError("模拟异常")

        result = formatter.sanitize_pinned_memory_data(BrokenDict())
        assert result == {"basic_info": "", "preferences": ""}
