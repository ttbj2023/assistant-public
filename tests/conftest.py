"""pytest配置和共享fixtures

提供全局fixtures和配置,确保所有测试都能使用一致的测试环境和数据。
简化版本，移除了冗余和过时的fixtures。
"""

import os
import shutil
import sys
import tempfile
import uuid
from pathlib import Path

import pytest

from src.config import runtime_env

project_root = Path(__file__).parent.parent
src_path = project_root / "src"
sys.path.insert(0, str(project_root))
sys.path.insert(0, str(src_path))

# =============================================================================
# 基础配置和环境设置
# =============================================================================


def pytest_configure(config) -> None:
    """pytest配置钩子：设置测试环境并清理残留数据目录."""
    os.environ["ENVIRONMENT"] = "testing"
    os.environ.setdefault("TESTING", "true")

    # 清理上次测试残留的目录（比session钩子更早，确保即使异常退出也能清理）
    # 只清理当前进程前缀的目录，避免并行 pytest 进程误删彼此的 test_data 目录
    for pattern in runtime_env.test_data_cleanup_patterns():
        for d in Path(".").glob(pattern):
            if d.is_dir():
                shutil.rmtree(d, ignore_errors=True)


def pytest_sessionstart(session):
    """pytest会话开始钩子 - 为每个worker创建独立的测试数据目录

    pytest-xdist并发测试策略：
    - master进程：创建共享的./test_data目录
    - worker进程：创建worker专属的test_data_worker_{gw}子目录
    - 通过环境变量PYTEST_XDIST_WORKER_ID告知path_resolver使用worker专属目录

    这样可以完全避免多worker同时创建相同目录导致的竞态条件。
    """
    try:
        import os
        from pathlib import Path

        # 检查是否是worker进程
        if hasattr(session.config, "workerinput"):
            # Worker进程：创建worker专属目录
            worker_id = session.config.workerinput.get("workerid", "unknown")
            worker_test_dir = Path(runtime_env.test_data_dir_name(worker_id))

            worker_test_dir.mkdir(parents=True, exist_ok=True)

            # 设置环境变量，让path_resolver知道使用worker专属目录
            os.environ["PYTEST_XDIST_WORKER_ID"] = worker_id

            print(f"✅ Worker {worker_id} 创建专属测试目录: {worker_test_dir}")
        else:
            # Master进程：创建共享基础目录
            test_data_dir = Path(runtime_env.test_data_dir_name(None))
            test_data_dir.mkdir(parents=True, exist_ok=True)
            print(f"✅ Master进程预创建测试目录: {test_data_dir}")
    except Exception as e:
        print(f"⚠️ pytest session钩子创建测试数据目录失败: {e}")


def pytest_sessionfinish(session, exitstatus):
    """pytest会话结束钩子 - 后备清理机制

    即使pytest异常退出(Ctrl+C, 崩溃等)，这个钩子也会执行
    确保测试数据总是被清理

    清理策略：
    - master进程：清理共享的./test_data目录
    - worker进程：清理worker专属的./test_data_worker_*目录
    """
    try:
        import shutil
        from pathlib import Path

        # 检查是否是worker进程
        if hasattr(session.config, "workerinput"):
            # Worker进程：清理worker专属目录
            worker_id = session.config.workerinput.get("workerid", "unknown")
            worker_test_dir = Path(runtime_env.test_data_dir_name(worker_id))

            if worker_test_dir.exists():
                shutil.rmtree(worker_test_dir, ignore_errors=True)
                print(f"✅ Worker {worker_id} 清理测试数据目录: {worker_test_dir}")
        else:
            # Master进程：清理共享目录和当前前缀的所有worker目录
            test_data_dir = Path(runtime_env.test_data_dir_name(None))
            if test_data_dir.exists():
                shutil.rmtree(test_data_dir, ignore_errors=True)
                print(f"✅ Master进程清理测试数据目录: {test_data_dir}")

            # 只清理本进程前缀的 worker 目录，避免误删并行 pytest 进程的数据
            for pattern in runtime_env.test_data_cleanup_patterns():
                for worker_dir in Path(".").glob(pattern):
                    if worker_dir.is_dir():
                        shutil.rmtree(worker_dir, ignore_errors=True)
                        print(f"✅ Master进程清理Worker目录: {worker_dir}")

    except Exception as e:
        # 清理失败不影响pytest退出
        print(f"⚠️ pytest钩子清理失败: {e}")


def pytest_collection_modifyitems(config, items) -> None:
    """修改收集到的测试项，为集成测试自动设置5秒超时，为E2E测试设置30秒超时"""
    for item in items:
        # 如果测试有integration标记，自动添加5秒超时标记
        if item.get_closest_marker("integration"):
            if not item.get_closest_marker("timeout"):
                item.add_marker(pytest.mark.timeout(5))

        # 如果测试有e2e标记，自动添加30秒超时标记
        if item.get_closest_marker("e2e"):
            if not item.get_closest_marker("timeout"):
                item.add_marker(pytest.mark.timeout(30))

        # 如果测试有serial标记，添加xdist_group确保串行执行
        if item.get_closest_marker("serial"):
            item.add_marker(pytest.mark.xdist_group("serial"))


# =============================================================================
# 用户隔离测试fixtures
# =============================================================================


def get_test_user_id() -> str:
    """获取统一测试用的用户ID"""
    return "test_user"


@pytest.fixture(scope="function")
def test_user() -> str:
    """统一测试用户ID（并发安全）

    使用统一的测试用户ID，确保测试数据隔离和一致性。
    单元测试和集成测试都应该使用这个 fixture。

    并发安全：在pytest-xdist环境下，使用worker进程ID作为后缀，
    避免多个worker同时创建相同的用户目录导致的竞态条件。
    """

    base_user = get_test_user_id()

    # 使用进程ID来区分不同worker（pytest-xdist环境）
    # 这比环境变量更可靠
    pid = os.getpid()

    # 如果不是主进程（pytest主进程的PID通常较小），则添加后缀
    # 这里我们简单地对所有进程都添加PID后缀，确保唯一性
    result = f"{base_user}_pid_{pid}"

    # 调试日志
    print(f"🐛 test_user fixture: PID={pid}, returning={result}")

    return result


@pytest.fixture
def test_thread_id(request):
    """基于测试函数名生成线程ID的fixture

    使用统一的线程ID生成逻辑，确保测试的一致性。
    """
    from tests.utils.test_id_generator import generate_test_thread_id

    # 获取测试函数名
    function_name = (
        request.function.__name__ if hasattr(request, "function") else "unknown"
    )

    # 根据测试类型确定类别
    test_path = str(request.node.path)
    if "integration" in test_path:
        test_category = "integration"
    elif "unit" in test_path:
        test_category = "unit"
    else:
        test_category = "test"

    return generate_test_thread_id(test_category, function_name)


@pytest.fixture
def thread_id_factory(test_thread_id):
    """线程ID工厂fixture，用于生成线程ID变体

    用于在同一测试中模拟多个线程的隔离性验证。
    确保pytest-xdist并发安全。

    使用示例:
        def test_thread_isolation(thread_id_factory):
            # 生成3个线程变体
            variants = thread_id_factory(["thread1", "thread2", "thread3"])

            # 使用变体线程ID
            tools1 = await build_tools(user_id, variants["thread1"])
            tools2 = await build_tools(user_id, variants["thread2"])
            tools3 = await build_tools(user_id, variants["thread3"])

    Args:
        test_thread_id: 基础线程ID（来自test_thread_id fixture）

    Returns:
        返回一个函数，该函数接受变体名称列表，返回变体名称到线程ID的映射
    """
    from tests.utils.test_id_generator import generate_thread_variants

    def _create_variants(variant_names: list[str]) -> dict[str, str]:
        """创建线程ID变体

        Args:
            variant_names: 变体名称列表，如 ["thread1", "thread2", "thread3"]

        Returns:
            变体名称到完整线程ID的映射字典
        """
        return generate_thread_variants(test_thread_id, variant_names)

    return _create_variants


def get_random_test_user_id() -> str:
    """获取随机测试用户ID"""
    return f"test_user_{uuid.uuid4().hex[:8]}"


# =============================================================================
# Mock Fixtures
# =============================================================================


@pytest.fixture
def mock_httpx_client():
    """预配置的httpx.AsyncClient Mock.

    消除测试中重复的 __aenter__/__aexit__/post 配置模式.
    测试侧需自行设置 post.return_value 和 patch("httpx.AsyncClient").
    """
    from unittest.mock import AsyncMock

    client = AsyncMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    client.post = AsyncMock()
    return client


# =============================================================================
# 测试隔离和清理
# =============================================================================


@pytest.fixture(scope="session", autouse=True)
def auto_test_cleanup():
    """会话级测试清理

    清理测试数据目录，确保只删除 test_data 相关路径，绝不能删除生产 ./data 目录。
    """
    yield

    try:
        import shutil

        from src.core.path_resolver import UserDataPathResolver

        resolver = UserDataPathResolver()
        test_data_dir = resolver.base_path

        path_name = test_data_dir.name.lower()
        if "test_data" not in path_name:
            print(f"🚫 拒绝清理非测试数据目录: {test_data_dir}")
            return

        if test_data_dir.exists():
            shutil.rmtree(test_data_dir, ignore_errors=True)
            print(f"✅ 清理测试数据目录: {test_data_dir}")
    except Exception as e:
        print(f"⚠️ 清理测试数据目录失败: {e}")


@pytest.fixture(scope="session", autouse=True)
def test_path_cleanup():
    """测试路径清理"""
    # 测试会话开始时
    yield
    # 测试会话结束时清理临时文件
    temp_paths = [
        Path(tempfile.gettempdir()) / "test_data_*",
        Path(tempfile.gettempdir()) / "test_db_*",
    ]

    for pattern in temp_paths:
        for path in Path(tempfile.gettempdir()).glob(pattern.name.split("*")[0] + "*"):
            if path.exists():
                try:
                    if path.is_dir():
                        shutil.rmtree(path)
                    else:
                        path.unlink()
                except PermissionError:
                    pass  # 忽略权限错误
