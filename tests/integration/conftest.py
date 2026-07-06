"""集成测试配置文件 - 简化版.

遵循灰盒测试原则，只Mock真正的外部依赖，使用真实的内部组件协作。
"""
from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

import pytest

# test_user fixture 已移至主 conftest.py，支持pytest-xdist并发隔离
# 集成测试直接使用主 conftest.py 的 fixture，避免重复定义


@pytest.fixture(autouse=True, scope="session")
def _force_nullpool_for_tests():
    """测试全程强制 AsyncDatabaseManager 使用 NullPool.

    function-scope 事件循环下, 默认连接池 (pool_size=20) 的 aiosqlite 连接绑定
    创建时的 loop; 即便 engine.dispose() 也可能因 GC 时序残留, 跨测试触发
    Connection.__del__ ResourceWarning. NullPool 用完即弃, 连接在 session 关闭时
    立即释放, 从源头消除跨循环泄漏. 仅测试生效, 不影响生产.
    """
    from unittest.mock import patch

    from sqlalchemy.ext.asyncio import create_async_engine as _orig
    from sqlalchemy.pool import NullPool

    import src.storage.dao.async_database_manager as adm

    def _create_async_engine(url: str, **kwargs: object) -> object:
        # NullPool 不兼容池大小类参数
        for k in ("pool_size", "max_overflow", "pool_timeout", "pool_recycle"):
            kwargs.pop(k, None)
        kwargs["poolclass"] = NullPool
        return _orig(url, **kwargs)

    with patch.object(adm, "create_async_engine", _create_async_engine):
        yield


@pytest.fixture(autouse=True)
async def _reset_db_and_service_state() -> AsyncIterator[None]:
    """每个集成测试前后清理 DB 全局状态, 消除 aiosqlite 连接泄漏.

    function-scope 事件循环下, async_database_manager._db_manager_cache 里的
    engine 绑定当前测试 loop. teardown 必须在 loop 关闭前 await
    close_all_db_managers() 关闭池内 aiosqlite 连接, 否则连接被 GC 时触发
    aiosqlite Connection.__del__ ResourceWarning (pytest 包成
    PytestUnraisableExceptionWarning), 且非确定性怪罪到 GC 时刻正在跑的测试,
    造成偶发失败.

    - _db_cache_lock: setup 重建 asyncio.Lock (延迟绑定当前循环)
    - _db_manager_cache: teardown 经 close_all_db_managers() 正确 dispose engine
    - service_factory._service_cache: 清空 (丢弃绑定旧循环的 Service)
    - cache._global_cache: 置 None (SplittableMemoryCache 单例, 下次访问重建)
    """
    from src.agent.memory.local_memory import cache as cache_mod
    from src.config import reset_config_cache
    from src.config.config_loader import clear_cache as clear_yaml_cache
    from src.storage.dao import async_database_manager as adm
    from src.storage.service.service_factory import clear_vector_cache

    adm._db_cache_lock = asyncio.Lock()
    clear_vector_cache()
    cache_mod._global_cache = None
    clear_yaml_cache()
    reset_config_cache()
    yield
    await adm.close_all_db_managers()
    clear_vector_cache()
    cache_mod._global_cache = None
    clear_yaml_cache()
    reset_config_cache()


@pytest.fixture
def test_thread_id():
    """集成测试线程ID（基于测试名称自动生成）

    格式：integration_{test_function_name}_{random_suffix}
    """
    import inspect
    import uuid

    from tests.utils.test_id_generator import generate_test_thread_id

    # 获取调用测试函数的名称
    frame = inspect.currentframe()
    try:
        # 向上查找调用栈中的测试函数
        caller_frame = frame.f_back
        while caller_frame:
            function_name = caller_frame.f_code.co_name
            if function_name.startswith("test_"):
                # 生成基于测试名的线程ID
                thread_id = generate_test_thread_id("integration", function_name)
                return thread_id
            caller_frame = caller_frame.f_back

        # 如果没找到测试函数，使用默认格式
        return f"integration_unknown_{uuid.uuid4().hex[:8]}"
    finally:
        del frame


@pytest.fixture
def integration_user_paths(test_user, test_thread_id):
    """集成测试用户的完整路径信息.

    返回用户ID对应的所有路径信息，便于集成测试中直接使用。
    """
    try:
        from src.core.path_resolver import get_database_path

        return {
            "user_id": test_user,
            "thread_id": test_thread_id,
            "personalized_data": get_database_path(
                test_user, test_thread_id, "personalized_data", agent_id="test-agent"
            ),
            "conversation_memory": get_database_path(
                test_user, test_thread_id, "conversation_history", agent_id="test-agent"
            ),
            "todo": get_database_path(
                test_user, test_thread_id, "todo", agent_id="test-agent"
            ),
        }
    except ImportError:
        # 如果路径解析器不可用，返回基础用户ID
        return {"user_id": test_user, "thread_id": test_thread_id}


@pytest.fixture
def real_todo_tool(test_user, test_thread_id):
    """创建真实的TODO工具实例 - 使用用户隔离.

    灰盒测试原则：使用真实的TODO工具，Mock其外部依赖.
    原 todo_manager 单工具已拆分为 4 个子工具, 这里返回 CreateTodoTool
    (大多数隔离测试以创建任务为起点); 需要 list/update/delete 时由测试
    自行实例化对应子工具(相同的 user_id/thread_id/agent_id).
    """
    from src.tools.internal.create_todo_tool import CreateTodoTool

    return CreateTodoTool(
        user_id=test_user, thread_id=test_thread_id, agent_id="test-agent"
    )


@pytest.fixture
async def integration_db_connection(test_user, test_thread_id):
    """集成测试专用的异步数据库连接fixture.

    提供可靠的异步SQLite连接，解决连接配置问题.
    """
    from pathlib import Path

    import aiosqlite

    from src.core.path_resolver import get_database_path

    # 获取用户数据路径
    db_path_str = get_database_path(
        test_user, test_thread_id, "personalized_data", agent_id="test-agent"
    )
    db_path = Path(db_path_str)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    # 创建异步连接
    conn = await aiosqlite.connect(
        str(db_path),
        isolation_level=None,  # 自动提交模式
        timeout=5.0,
    )

    # 启用WAL模式和外键约束
    await conn.execute("PRAGMA journal_mode=WAL")
    await conn.execute("PRAGMA foreign_keys=ON")
    await conn.execute("PRAGMA synchronous=NORMAL")
    await conn.execute("PRAGMA cache_size=1000")

    try:
        yield conn
    finally:
        # 确保连接关闭
        await conn.close()


@pytest.fixture
async def pinned_memory_db(test_user, test_thread_id):
    """初始化pinned_memory数据库的fixture

    创建pinned_memory数据库的表结构，以便测试可以插入测试数据。
    Service层会自动使用相同的数据库文件。
    """
    import aiosqlite

    from src.core.path_resolver import get_database_path

    # 获取pinned_memory数据库路径
    db_path = get_database_path(
        test_user, test_thread_id, "pinned_memory", agent_id="test-agent"
    )

    # 创建异步连接
    conn = await aiosqlite.connect(
        str(db_path),
        isolation_level=None,
        timeout=5.0,
    )

    # 启用性能优化选项
    await conn.execute("PRAGMA journal_mode=WAL")
    await conn.execute("PRAGMA foreign_keys=ON")
    await conn.execute("PRAGMA synchronous=NORMAL")

    # 创建simple_pinned_memory表
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS simple_pinned_memory (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            thread_id TEXT NOT NULL,
            memory_type TEXT NOT NULL,
            content TEXT NOT NULL,
            priority INTEGER DEFAULT 50,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            access_count INTEGER DEFAULT 0
        )
    """)

    # 创建索引
    await conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_pinned_memory_user_thread
        ON simple_pinned_memory(user_id, thread_id, memory_type)
    """)

    await conn.commit()

    print(f"[DEBUG] pinned_memory数据库: {db_path}")

    try:
        yield conn
    finally:
        await conn.close()


@pytest.fixture
async def db_session(test_user, test_thread_id):
    """集成测试数据库会话fixture

    使用path_resolver提供的统一路径管理，确保与Service层使用相同的数据库文件。
    提供conversation_history数据库的连接和表结构。
    """
    import aiosqlite

    from src.core.path_resolver import get_database_path

    # 直接使用path_resolver获取路径（无需Mock）
    db_path = get_database_path(
        test_user, test_thread_id, "conversation_history", agent_id="test-agent"
    )

    # 创建异步连接
    conn = await aiosqlite.connect(
        str(db_path),
        isolation_level=None,
        timeout=5.0,
    )

    # 启用性能优化选项
    await conn.execute("PRAGMA journal_mode=WAL")
    await conn.execute("PRAGMA foreign_keys=ON")
    await conn.execute("PRAGMA synchronous=NORMAL")
    await conn.execute("PRAGMA cache_size=2000")
    await conn.execute("PRAGMA temp_store=MEMORY")

    # 创建conversation_index表（对话索引表）
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS conversation_index (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            thread_id TEXT NOT NULL,
            agent_id TEXT,
            round_number INTEGER NOT NULL,
            title TEXT DEFAULT '',
            topic TEXT,
            keywords TEXT,
            summary TEXT,
            user_message TEXT NOT NULL,
            assistant_response TEXT NOT NULL,
            message_count INTEGER DEFAULT 1,
            token_usage INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, thread_id, round_number)
        )
    """)

    # 创建conversation_index_group表（老期冻结的语义 run 弧短语表）
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS conversation_index_group (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            thread_id TEXT NOT NULL,
            agent_id TEXT,
            round_start INTEGER NOT NULL,
            round_end INTEGER NOT NULL,
            arc_phrase TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, thread_id, round_start)
        )
    """)

    # 创建simple_pinned_memory表（置顶记忆表 - conversation_history数据库内）
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS simple_pinned_memory (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            thread_id TEXT NOT NULL,
            memory_type TEXT NOT NULL,
            content TEXT NOT NULL,
            priority INTEGER DEFAULT 50,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            access_count INTEGER DEFAULT 0
        )
    """)

    # 创建索引
    await conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_conversation_user_thread
        ON conversation_index(user_id, thread_id)
    """)
    await conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_pinned_memory_user_thread
        ON simple_pinned_memory(user_id, thread_id, memory_type)
    """)

    # 提交DDL语句（确保表结构持久化）
    await conn.commit()

    # 验证表已创建（调试信息）
    cursor = await conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = await cursor.fetchall()
    print(f"[DEBUG] conversation_history数据库表: {[t[0] for t in tables]}")
    print(f"[DEBUG] conversation_history数据库文件: {db_path}")

    try:
        yield conn
    finally:
        await conn.close()


@pytest.fixture(scope="class")
def integration_test_config():
    """集成测试配置.

    配置说明：
    - 使用真实的内部组件进行协作测试
    - 只Mock真正的外部依赖
    - 确保测试间的隔离性和一致性
    - 启用用户隔离功能
    """
    return {
        "test_timeout": 30,  # 测试超时时间(秒)
        "mock_external_deps": True,  # 是否Mock外部依赖
        "use_real_components": True,  # 是否使用真实内部组件
        "cleanup_after_test": True,  # 测试后是否清理
        "enable_user_isolation": True,  # 启用用户隔离功能
    }


def pytest_collection_modifyitems(config, items) -> None:
    """为 tests/integration/ 下未分类的测试自动添加 integration 标记.

    只排除已明确标记为 integration / e2e / skip / skipif 的测试,
    避免仅因 @pytest.mark.asyncio 或 @pytest.mark.parametrize 等插件标记
    而漏标 integration。
    """
    excluded = {"integration", "e2e", "skip", "skipif"}
    for item in items:
        if "tests/integration" in str(item.fspath) and not any(
            m.name in excluded for m in item.iter_markers()
        ):
            item.add_marker(pytest.mark.integration)


# =============================================================================
# Mock Fixtures for Fast Integration Tests
# =============================================================================


@pytest.fixture
def mocked_llm_service():
    """Mock LLM服务fixture

    为API集成测试提供LLM Mock，避免真实的API调用。

    使用UnifiedMockFactory创建，确保与项目mock体系一致。
    """
    from tests.mocks.unified_factory import UnifiedMockFactory

    return UnifiedMockFactory.create_llm(response_content="这是LLM的测试响应")


@pytest.fixture
def mocked_embedding_service():
    """Mock嵌入服务fixture

    为API集成测试提供嵌入服务Mock，避免模型加载。

    使用UnifiedMockFactory创建真实感嵌入向量。
    """
    from tests.mocks.unified_factory import UnifiedMockFactory

    return UnifiedMockFactory.create_embeddings(dimensions=384, realistic=True)


@pytest.fixture
def mocked_vector_store():
    """Mock向量存储fixture

    为API集成测试提供向量存储Mock，避免ChromaDB初始化。

    使用UnifiedMockFactory创建高级向量存储。
    """
    from tests.mocks.unified_factory import UnifiedMockFactory

    return UnifiedMockFactory.create_advanced_vector_store(dimensions=384)


@pytest.fixture
def mocked_agent():
    """Mock Agent fixture

    为API集成测试提供Agent Mock，避免真实的Agent创建。

    注意：Agent mock没有对应的工厂方法，因为Agent类型多样，
    这里使用AsyncMock直接创建。如需更复杂的mock，建议创建AgentMockFactory。
    """
    from unittest.mock import AsyncMock

    mock_agent = AsyncMock()
    mock_agent.process_message.return_value = "测试Agent响应"

    return mock_agent


@pytest.fixture
def mocked_external_services_for_api(
    mocked_llm_service, mocked_embedding_service, mocked_vector_store, mocked_agent
):
    """Mock所有外部服务的综合fixture

    为API集成测试一键应用所有Mock，大幅提升测试速度。

    所有mock都通过UnifiedMockFactory创建，确保一致性和可维护性。

    Returns:
        dict: 包含所有mock服务的字典
    """
    return {
        "llm": mocked_llm_service,
        "embedding": mocked_embedding_service,
        "vector_store": mocked_vector_store,
        "agent": mocked_agent,
    }
