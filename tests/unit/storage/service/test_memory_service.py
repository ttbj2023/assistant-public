"""MemoryService单元测试.

测试记忆服务的业务逻辑，包括验证、格式化、CRUD操作等。
遵循单元测试设计规范：Mock外部依赖，测试业务逻辑。
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, Mock

import pytest

from src.storage.models.simple_pinned_memory import (
    SimplePinnedMemory,
    SimplePinnedMemoryType,
)
from src.storage.service.memory_service import MemoryService


@pytest.fixture
def memory_session_factory():
    """创建 MemoryService 专用 session factory Mock.

    支持:
    - async with factory() as session
    - async with session.begin() as transaction
    - session.execute(...) 自定义返回值
    """
    session = AsyncMock()
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    session.flush = AsyncMock()
    session.execute = AsyncMock()

    class _BeginContext:
        async def __aenter__(self):
            return Mock()

        async def __aexit__(self, *args):
            return None

    session.begin = Mock(return_value=_BeginContext())

    class _AsyncSessionContext:
        def __init__(self, session_mock):
            self._session = session_mock

        async def __aenter__(self):
            return self._session

        async def __aexit__(self, *args):
            return None

    def _factory():
        return _AsyncSessionContext(session)

    _factory._session = session
    return _factory


@pytest.fixture
def healthy_session_factory(memory_session_factory):
    """配置 session factory 使其 execute 返回健康检查所需的标量."""

    def _execute_side_effect(sql_stmt, *args, **kwargs):
        sql_text = str(sql_stmt)
        result = Mock()

        if "GROUP BY" in sql_text:
            result.fetchall.return_value = [
                ("basic_info", 3),
                ("preferences", 2),
            ]
        else:
            scalar_value = {
                "COUNT(*)": 5,
                "MAX(updated_at)": datetime(2026, 1, 1, tzinfo=UTC),
                "COUNT(DISTINCT user_id)": 2,
                "COUNT(DISTINCT thread_id)": 3,
            }
            found_value = None
            for key, value in scalar_value.items():
                if key in sql_text:
                    found_value = value
                    break
            result.scalar.return_value = found_value

        return result

    memory_session_factory._session.execute = AsyncMock(side_effect=_execute_side_effect)
    return memory_session_factory


class TestMemoryServiceUpdateMemory:
    """测试update_memory方法 - 核心业务逻辑."""

    @pytest.mark.asyncio
    async def test_update_memory_empty_content_should_raise_value_error(
        self, mock_session_factory, test_user
    ):
        """测试更新记忆：空内容应抛出ValueError."""
        # Arrange
        service = MemoryService(mock_session_factory)

        # Act & Assert
        with pytest.raises(ValueError, match="记忆内容验证失败"):
            await service.update_memory(
                memory_type=SimplePinnedMemoryType.BASIC_INFO,
                content="",
                user_id=test_user,
                thread_id="test_thread_id",
            )

    @pytest.mark.asyncio
    async def test_update_memory_whitespace_only_should_raise_value_error(
        self, mock_session_factory, test_user
    ):
        """测试更新记忆：纯空格内容应抛出ValueError."""
        # Arrange
        service = MemoryService(mock_session_factory)

        # Act & Assert
        with pytest.raises(ValueError, match="记忆内容验证失败"):
            await service.update_memory(
                memory_type=SimplePinnedMemoryType.BASIC_INFO,
                content="   \n\t  ",
                user_id=test_user,
                thread_id="test_thread_id",
            )

    @pytest.mark.asyncio
    async def test_update_memory_exceeds_max_length_should_raise_value_error(
        self, mock_session_factory, test_user
    ):
        """测试更新记忆：超长内容应抛出ValueError."""
        # Arrange
        service = MemoryService(mock_session_factory)

        # Act & Assert
        with pytest.raises(ValueError, match="记忆内容验证失败"):
            await service.update_memory(
                memory_type=SimplePinnedMemoryType.BASIC_INFO,
                content="a" * 2001,  # 超过2000字符限制
                user_id=test_user,
                thread_id="test_thread_id",
            )


class TestMemoryServiceValidation:
    """测试_validate_memory_content私有方法."""

    def test_validate_empty_content_should_return_error(self):
        """测试验证：空内容应返回错误."""
        # Arrange
        service = MemoryService(Mock())

        # Act
        result = service._validate_memory_content(SimplePinnedMemoryType.BASIC_INFO, "")

        # Assert
        assert result["valid"] is False
        assert "记忆内容不能为空" in result["errors"]

    def test_validate_whitespace_only_should_return_error(self):
        """测试验证：纯空格应返回错误."""
        # Arrange
        service = MemoryService(Mock())

        # Act
        result = service._validate_memory_content(
            SimplePinnedMemoryType.BASIC_INFO, "   \n\t  "
        )

        # Assert
        assert result["valid"] is False
        assert len(result["errors"]) > 0

    def test_validate_basic_info_2000_chars_should_pass(self):
        """测试验证：基本信息2000字符应通过."""
        # Arrange
        service = MemoryService(Mock())

        # Act
        result = service._validate_memory_content(
            SimplePinnedMemoryType.BASIC_INFO, "a" * 2000
        )

        # Assert
        assert result["valid"] is True
        assert len(result["errors"]) == 0

    def test_validate_basic_info_2001_chars_should_fail(self):
        """测试验证：基本信息2001字符应失败."""
        # Arrange
        service = MemoryService(Mock())

        # Act
        result = service._validate_memory_content(
            SimplePinnedMemoryType.BASIC_INFO, "a" * 2001
        )

        # Assert
        assert result["valid"] is False
        # 检查错误消息中包含"基本信息内容过长"
        assert any("基本信息内容过长" in err for err in result["errors"])

    def test_validate_preferences_1000_chars_should_pass(self):
        """测试验证：偏好信息1000字符应通过."""
        # Arrange
        service = MemoryService(Mock())

        # Act
        result = service._validate_memory_content(
            SimplePinnedMemoryType.PREFERENCES, "a" * 1000
        )

        # Assert
        assert result["valid"] is True


class TestMemoryServiceFormatting:
    """测试_format_memory_content私有方法."""

    def test_format_should_strip_whitespace(self):
        """测试格式化：应去除首尾空白."""
        # Arrange
        service = MemoryService(Mock())

        # Act
        result = service._format_memory_content(
            SimplePinnedMemoryType.BASIC_INFO, "  测试内容  "
        )

        # Assert
        assert result == "测试内容"

    def test_format_should_normalize_line_breaks(self):
        """测试格式化：应规范化换行符."""
        # Arrange
        service = MemoryService(Mock())

        # Act
        result = service._format_memory_content(
            SimplePinnedMemoryType.BASIC_INFO, "行1\r\n行2\r行3"
        )

        # Assert
        assert result == "行1\n行2\n行3"

    def test_format_basic_info_should_preserve_paragraphs(self):
        """测试格式化：基本信息应保留段落."""
        # Arrange
        service = MemoryService(Mock())

        # Act
        result = service._format_memory_content(
            SimplePinnedMemoryType.BASIC_INFO, "段落1\n\n段落2\n\n\n段落3"
        )

        # Assert
        assert result == "段落1\n\n段落2\n\n段落3"

    def test_format_preferences_should_split_by_lines(self):
        """测试格式化：偏好信息应按行分割."""
        # Arrange
        service = MemoryService(Mock())

        # Act
        result = service._format_memory_content(
            SimplePinnedMemoryType.PREFERENCES, "偏好1\n\n偏好2\n偏好3"
        )

        # Assert
        assert result == "偏好1\n偏好2\n偏好3"


class TestMemoryServiceQueries:
    """测试查询方法."""

    @pytest.mark.asyncio
    async def test_get_pinned_memory_as_dict_should_return_2_fields(
        self,
        mock_memory_dao,
        mock_session_factory,
        create_multiple_pinned_memories,
        test_user,
    ):
        """测试获取记忆字典：应返回2个字段."""
        # Arrange
        mock_memory_dao.get_all_memories = AsyncMock(
            return_value=create_multiple_pinned_memories()
        )
        service = MemoryService(mock_session_factory)
        service.memory_dao = mock_memory_dao

        # Act
        result = await service.get_pinned_memory_as_dict(test_user, "test_thread_id")

        # Assert
        assert "basic_info" in result
        assert "preferences" in result
        assert result["basic_info"] == "基本信息内容"
        assert result["preferences"] == "偏好设置内容"

    @pytest.mark.asyncio
    async def test_get_pinned_memory_as_dict_empty_memories_should_return_empty_strings(
        self, mock_memory_dao, mock_session_factory, test_user
    ):
        """测试获取记忆字典：无记忆应返回空字符串."""
        # Arrange
        mock_memory_dao.get_all_memories = AsyncMock(return_value=[])
        service = MemoryService(mock_session_factory)
        service.memory_dao = mock_memory_dao

        # Act
        result = await service.get_pinned_memory_as_dict(test_user, "test_thread_id")

        # Assert
        assert result == {
            "basic_info": "",
            "preferences": "",
        }

    @pytest.mark.asyncio
    async def test_get_memory_by_type_should_return_memory(
        self, mock_memory_dao, mock_session_factory, test_user
    ):
        """测试根据类型获取记忆：应返回对应记忆."""
        # Arrange
        mock_memory = SimplePinnedMemory(
            id=1,
            user_id=test_user,
            thread_id="test_thread_id",
            memory_type=SimplePinnedMemoryType.BASIC_INFO,
            content="测试内容",
        )
        mock_memory_dao.get_memory_by_type = AsyncMock(return_value=mock_memory)
        service = MemoryService(mock_session_factory)
        service.memory_dao = mock_memory_dao

        # Act
        result = await service.get_memory_by_type(
            test_user, "test_thread_id", SimplePinnedMemoryType.BASIC_INFO
        )

        # Assert
        assert result is not None
        assert result.id == 1
        assert result.content == "测试内容"

    @pytest.mark.asyncio
    async def test_format_pinned_memory_dict_should_call_formatter(
        self, mock_memory_formatter, mock_session_factory, test_user
    ):
        """测试格式化记忆字典：应调用格式化器."""
        # Arrange
        service = MemoryService(mock_session_factory)
        service.pinned_memory_formatter = mock_memory_formatter

        test_dict = {
            "basic_info": "测试信息",
            "preferences": "测试偏好",
        }

        # Act
        result = await service.format_pinned_memory_dict(test_dict)

        # Assert
        assert result is not None
        mock_memory_formatter.sanitize_pinned_memory_data.assert_called_once()
        mock_memory_formatter.format_pinned_memory.assert_called_once()


class TestMemoryServiceUpdateMemory:
    """测试 update_memory 成功路径与异常路径."""

    @pytest.mark.asyncio
    async def test_update_memory_should_upsert_and_return_memory(
        self, mock_memory_dao, memory_session_factory, test_user
    ):
        """测试更新记忆: 有效内容应调用 upsert 并返回记忆."""
        # Arrange
        service = MemoryService(memory_session_factory)
        service.memory_dao = mock_memory_dao

        # Act
        result = await service.update_memory(
            memory_type=SimplePinnedMemoryType.BASIC_INFO,
            content="  有效内容  ",
            user_id=test_user,
            thread_id="test_thread_id",
        )

        # Assert
        assert result is not None
        assert result.id == 1
        mock_memory_dao.upsert_memory.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_update_memory_dao_exception_should_raise(
        self, mock_memory_dao, memory_session_factory, test_user
    ):
        """测试更新记忆: DAO 异常应向上抛出."""
        # Arrange
        service = MemoryService(memory_session_factory)
        service.memory_dao = mock_memory_dao
        mock_memory_dao.upsert_memory = AsyncMock(side_effect=RuntimeError("DB 失败"))

        # Act & Assert
        with pytest.raises(RuntimeError, match="DB 失败"):
            await service.update_memory(
                memory_type=SimplePinnedMemoryType.BASIC_INFO,
                content="内容",
                user_id=test_user,
                thread_id="test_thread_id",
            )


class TestMemoryServiceDeleteMemory:
    """测试 delete_memory."""

    @pytest.mark.asyncio
    async def test_delete_memory_should_return_success(
        self, mock_memory_dao, memory_session_factory, test_user
    ):
        """测试删除记忆: DAO 返回 True 时应返回 True."""
        # Arrange
        service = MemoryService(memory_session_factory)
        service.memory_dao = mock_memory_dao
        mock_memory_dao.delete_memory = AsyncMock(return_value=True)

        # Act
        result = await service.delete_memory(
            test_user,
            "test_thread_id",
            SimplePinnedMemoryType.PREFERENCES,
        )

        # Assert
        assert result is True
        mock_memory_dao.delete_memory.assert_awaited_once_with(
            test_user,
            "test_thread_id",
            SimplePinnedMemoryType.PREFERENCES,
        )

    @pytest.mark.asyncio
    async def test_delete_memory_exception_should_raise(
        self, mock_memory_dao, memory_session_factory, test_user
    ):
        """测试删除记忆: DAO 异常应向上抛出."""
        # Arrange
        service = MemoryService(memory_session_factory)
        service.memory_dao = mock_memory_dao
        mock_memory_dao.delete_memory = AsyncMock(side_effect=RuntimeError("DB 失败"))

        # Act & Assert
        with pytest.raises(RuntimeError, match="DB 失败"):
            await service.delete_memory(
                test_user,
                "test_thread_id",
                SimplePinnedMemoryType.PREFERENCES,
            )


class TestMemoryServiceQueryExceptionPaths:
    """测试查询方法的异常路径."""

    @pytest.mark.asyncio
    async def test_get_memory_by_type_exception_should_raise(
        self, mock_memory_dao, memory_session_factory, test_user
    ):
        """测试根据类型获取记忆: DAO 异常应向上抛出."""
        # Arrange
        service = MemoryService(memory_session_factory)
        service.memory_dao = mock_memory_dao
        mock_memory_dao.get_memory_by_type = AsyncMock(side_effect=RuntimeError("DB 失败"))

        # Act & Assert
        with pytest.raises(RuntimeError, match="DB 失败"):
            await service.get_memory_by_type(
                test_user, "test_thread_id", SimplePinnedMemoryType.BASIC_INFO
            )

    @pytest.mark.asyncio
    async def test_get_all_memories_exception_should_raise(
        self, mock_memory_dao, memory_session_factory, test_user
    ):
        """测试获取所有记忆: DAO 异常应向上抛出."""
        # Arrange
        service = MemoryService(memory_session_factory)
        service.memory_dao = mock_memory_dao
        mock_memory_dao.get_all_memories = AsyncMock(side_effect=RuntimeError("DB 失败"))

        # Act & Assert
        with pytest.raises(RuntimeError, match="DB 失败"):
            await service.get_all_memories(test_user, "test_thread_id")


class TestMemoryServiceHealthCheck:
    """测试健康检查与统计逻辑."""

    @pytest.mark.asyncio
    async def test_health_check_should_return_healthy(
        self, healthy_session_factory, test_user
    ):
        """测试健康检查: 数据库正常时应返回 healthy."""
        # Arrange
        service = MemoryService(healthy_session_factory)

        # Act
        result = await service.health_check()

        # Assert
        assert result["status"] == "healthy"
        assert result["database_connected"] is True
        assert result["formatter_accessible"] is True
        assert result["statistics"]["total_memories"] == 5

    @pytest.mark.asyncio
    async def test_health_check_connection_error_should_return_unhealthy(
        self, memory_session_factory, test_user
    ):
        """测试健康检查: 数据库连接异常时应返回 unhealthy."""
        # Arrange
        memory_session_factory._session.execute = AsyncMock(
            side_effect=RuntimeError("connection lost")
        )
        service = MemoryService(memory_session_factory)

        # Act
        result = await service.health_check()

        # Assert
        assert result["status"] == "unhealthy"
        assert result["database_connected"] is False

    @pytest.mark.asyncio
    async def test_get_memory_statistics_exception_should_return_defaults(
        self, memory_session_factory, test_user
    ):
        """测试记忆统计: SQL 异常时应返回默认值."""
        # Arrange
        memory_session_factory._session.execute = AsyncMock(
            side_effect=RuntimeError("SQL 失败")
        )
        service = MemoryService(memory_session_factory)

        # Act
        result = await service._get_memory_statistics()

        # Assert
        assert result["total_memories"] == 0
        assert result["total_users"] == 0
        assert result["total_threads"] == 0
