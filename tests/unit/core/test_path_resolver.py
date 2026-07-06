"""path_resolver.py 模块的单元测试

测试覆盖:
- UserDataPathResolver 单例模式
- 路径解析方法
- 缓存功能
- 测试环境管理
- 错误处理
"""

import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from src.core.path_resolver import (
    UserDataPathResolver,
    get_database_path,
    get_thread_base_path,
    get_user_path_resolver,
    get_vector_path,
)


@pytest.fixture(autouse=True)
def reset_path_resolver_singleton():
    """每个测试前后重置UserDataPathResolver单例"""
    UserDataPathResolver._instance = None
    yield
    UserDataPathResolver._instance = None


class TestUserDataPathResolver:
    """UserDataPathResolver 主要测试类"""

    @patch("src.core.path_resolver.os.getenv")
    def test_singleton_pattern_should_return_same_instance(self, mock_getenv):
        """单例模式: 应返回同一实例"""
        with tempfile.TemporaryDirectory() as temp_dir:
            # 正确设置side_effect，避免创建production目录
            mock_getenv.side_effect = lambda key, default="": (
                temp_dir if key == "BASE_DATA_PATH" else "production"
            )

            # 创建两个实例
            resolver1 = UserDataPathResolver()
            resolver2 = UserDataPathResolver()

            # 验证是同一个实例
            assert resolver1 is resolver2

    @patch("src.core.path_resolver.os.getenv")
    def test_production_mode_initialization_should_set_base_path(self, mock_getenv):
        """生产模式初始化: 应设置正确的base_path"""
        with tempfile.TemporaryDirectory() as temp_dir:
            mock_getenv.side_effect = lambda key, default="": (
                temp_dir if key == "BASE_DATA_PATH" else "production"
            )

            resolver = UserDataPathResolver()

            assert resolver.base_path == Path(temp_dir)

    @patch("src.core.path_resolver.os.getenv")
    def test_test_mode_initialization_should_use_worker_path(self, mock_getenv):
        """测试模式初始化: 应使用worker隔离路径"""
        mock_getenv.side_effect = lambda key, default="": {
            "ENVIRONMENT": "testing",
            "PYTEST_XDIST_WORKER_ID": "gw0",
        }.get(key, default)

        resolver = UserDataPathResolver()

        # 验证base_path是./test_data_worker_{worker_id}（并发安全）
        # 路径包含worker ID以避免pytest-xdist竞态条件
        assert resolver.base_path.match("./test_data_worker_*")
        assert "test_data_worker_" in str(resolver.base_path)

    @patch("src.core.path_resolver.os.getenv")
    def test_test_mode_with_process_prefix_should_use_prefixed_worker_path(
        self, mock_getenv
    ):
        """测试模式带 TEST_PROCESS_PREFIX 时: 应使用带前缀的 worker 隔离路径"""
        mock_getenv.side_effect = lambda key, default="": {
            "ENVIRONMENT": "testing",
            "PYTEST_XDIST_WORKER_ID": "gw0",
            "TEST_PROCESS_PREFIX": "unit",
        }.get(key, default)

        resolver = UserDataPathResolver()

        assert resolver.base_path.match("./test_data_unit_worker_*")
        assert "test_data_unit_worker_" in str(resolver.base_path)

    def test_get_thread_base_path_production_should_create_correct_path(self):
        """生产模式获取线程基础路径: 应创建正确的用户/线程目录"""
        with patch("src.core.path_resolver.os.getenv") as mock_getenv:
            with tempfile.TemporaryDirectory() as temp_dir:
                mock_getenv.side_effect = lambda key, default="": (
                    temp_dir if key == "BASE_DATA_PATH" else "production"
                )

                resolver = UserDataPathResolver()
                thread_path = resolver.get_thread_base_path("alice", "main")

                expected_path = Path(temp_dir) / "alice" / "main"
                assert thread_path == expected_path
                assert thread_path.exists()

    def test_get_database_path_production_should_create_correct_path(self):
        """生产模式获取数据库路径: 应创建正确的数据库文件路径"""
        with patch("src.core.path_resolver.os.getenv") as mock_getenv:
            with tempfile.TemporaryDirectory() as temp_dir:
                mock_getenv.side_effect = lambda key, default="": (
                    temp_dir if key == "BASE_DATA_PATH" else "production"
                )

                resolver = UserDataPathResolver()
                db_path = resolver.get_database_path(
                    "alice", "main", "todo", agent_id="personal-assistant"
                )

                expected_path = (
                    Path(temp_dir)
                    / "alice"
                    / "main"
                    / "personal-assistant"
                    / "database"
                    / "todo.db"
                )
                assert db_path == str(expected_path)
                assert Path(db_path).parent.exists()

    def test_get_vector_path_production_should_create_correct_path(self):
        """生产模式获取向量路径: 应创建正确的向量目录"""
        with patch("src.core.path_resolver.os.getenv") as mock_getenv:
            with tempfile.TemporaryDirectory() as temp_dir:
                mock_getenv.side_effect = lambda key, default="": (
                    temp_dir if key == "BASE_DATA_PATH" else "production"
                )

                resolver = UserDataPathResolver()
                vector_path = resolver.get_vector_path(
                    "alice", "main", agent_id="personal-assistant"
                )

                expected_path = (
                    Path(temp_dir) / "alice" / "main" / "personal-assistant" / "vector"
                )
                assert vector_path == expected_path
                assert vector_path.exists()

    def test_get_shared_storage_path_production_should_create_correct_path(self):
        """生产模式获取共享存储路径: 应创建正确的共享目录"""
        with patch("src.core.path_resolver.os.getenv") as mock_getenv:
            with tempfile.TemporaryDirectory() as temp_dir:
                mock_getenv.side_effect = lambda key, default="": (
                    temp_dir if key == "BASE_DATA_PATH" else "production"
                )

                resolver = UserDataPathResolver()
                cache_path = resolver.get_shared_storage_path("alice", "main", "cache")

                expected_path = Path(temp_dir) / "alice" / "main" / "shared" / "cache"
                assert cache_path == expected_path
                assert cache_path.exists()

    def test_invalid_input_validation_should_raise_on_empty_or_wrong_type(self):
        """输入验证: 空字符串应抛出ValueError, 非字符串应抛出TypeError"""
        with patch("src.core.path_resolver.os.getenv") as mock_getenv:
            mock_getenv.return_value = "testing"

            resolver = UserDataPathResolver()

            # 测试空字符串
            with pytest.raises(ValueError):
                resolver.get_thread_base_path("", "main")

            with pytest.raises(ValueError):
                resolver.get_thread_base_path("alice", "")

            # 测试非字符串类型
            with pytest.raises(TypeError):
                resolver.get_thread_base_path(123, "main")

            with pytest.raises(TypeError):
                resolver.get_thread_base_path("alice", None)


class TestGlobalFunctions:
    """全局便捷函数测试类"""

    @patch("src.core.path_resolver.os.getenv")
    def test_get_user_path_resolver_should_return_singleton(self, mock_getenv):
        """全局路径解析器获取: 应返回单例实例"""
        with tempfile.TemporaryDirectory() as temp_dir:
            # 正确设置side_effect，避免创建production目录
            mock_getenv.side_effect = lambda key, default="": (
                temp_dir if key == "BASE_DATA_PATH" else "production"
            )

            resolver1 = get_user_path_resolver()
            resolver2 = get_user_path_resolver()

            assert resolver1 is resolver2
            assert isinstance(resolver1, UserDataPathResolver)

    @patch("src.core.path_resolver.os.getenv")
    def test_convenience_functions_should_resolve_correct_paths(self, mock_getenv):
        """便捷函数: 应解析出正确的路径"""
        with tempfile.TemporaryDirectory() as temp_dir:
            mock_getenv.side_effect = lambda key, default="": (
                temp_dir if key == "BASE_DATA_PATH" else "production"
            )

            user_id = "alice"
            thread_id = "main"
            agent_id = "personal-assistant"

            # 测试便捷函数
            thread_path = get_thread_base_path(user_id, thread_id)
            db_path = get_database_path(user_id, thread_id, "todo", agent_id=agent_id)
            vector_path = get_vector_path(user_id, thread_id, agent_id=agent_id)

            # 验证路径
            expected_thread_path = Path(temp_dir) / user_id / thread_id
            assert thread_path == expected_thread_path

            expected_db_path = (
                Path(temp_dir) / user_id / thread_id / agent_id / "database" / "todo.db"
            )
            assert db_path == str(expected_db_path)

            expected_vector_path = (
                Path(temp_dir) / user_id / thread_id / agent_id / "vector"
            )
            assert vector_path == expected_vector_path


class TestErrorHandling:
    """错误处理测试类"""

    @patch("src.core.path_resolver.os.getenv")
    def test_invalid_database_name_should_raise_value_error(self, mock_getenv):
        """无效数据库名称: 空字符串和空白应抛出ValueError"""
        with tempfile.TemporaryDirectory() as temp_dir:
            # 正确设置side_effect，避免创建production目录
            mock_getenv.side_effect = lambda key, default="": (
                temp_dir if key == "BASE_DATA_PATH" else "production"
            )

            resolver = UserDataPathResolver()

            with pytest.raises(ValueError):
                resolver.get_database_path(
                    "alice", "main", "", agent_id="personal-assistant"
                )

            with pytest.raises(ValueError):
                resolver.get_database_path(
                    "alice", "main", "   ", agent_id="personal-assistant"
                )

    @patch("src.core.path_resolver.os.getenv")
    def test_invalid_storage_type_should_raise_value_error(self, mock_getenv):
        """无效存储类型: 空字符串和空白应抛出ValueError"""
        with tempfile.TemporaryDirectory() as temp_dir:
            # 正确设置side_effect，避免创建production目录
            mock_getenv.side_effect = lambda key, default="": (
                temp_dir if key == "BASE_DATA_PATH" else "production"
            )

            resolver = UserDataPathResolver()

            with pytest.raises(ValueError):
                resolver.get_shared_storage_path("alice", "main", "")

            with pytest.raises(ValueError):
                resolver.get_shared_storage_path("alice", "main", "   ")
