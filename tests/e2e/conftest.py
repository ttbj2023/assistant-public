"""E2E测试配置和fixtures.

基于ASGI TestClient的端到端测试框架:
- 进程内直接调用FastAPI app, 无子进程/无网络开销
- E2EMockLLM 可编程 Mock (支持 tool_calls 触发真实工具执行)
- Mock Embedding + Mock ContentAnalyzer
- 每个测试独立 user+thread 数据隔离
- 串行执行 (-n 0) 避免共享 test_data 目录竞态
"""
from __future__ import annotations

import asyncio
import os
import secrets
import shutil
import sqlite3
from contextlib import suppress
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, AsyncIterator, Iterator

import httpx
import pytest
import pytest_asyncio


def pytest_configure(config) -> None:
    """配置E2E测试环境变量和超时."""
    config.option.timeout = 30.0

    os.environ["ENVIRONMENT"] = "testing"
    os.environ["DEBUG"] = "false"
    os.environ["ENABLE_STATIC_USER_MANAGEMENT"] = "false"


# =============================================================================
# Mock LLM 重置
# =============================================================================


@pytest.fixture(autouse=True)
def _reset_e2e_mock() -> Iterator[None]:
    """每个测试前后清空 E2EMockLLM 脚本队列."""
    from tests.e2e.mock_llm import E2EMockLLM

    E2EMockLLM.clear()
    yield
    E2EMockLLM.clear()


# =============================================================================
# 会话级清理
# =============================================================================


@pytest.fixture(scope="session", autouse=True)
def e2e_session_cleanup():
    """E2E测试会话级数据清理.

    在 session 启动和结束时都清理 ./test_data:
    - 启动时清理: 防止上一次 pytest 进程 teardown 不完整(文件被占用导致 rmtree
      部分失败)遗留损坏的 SQLite/ChromaDB 文件, 本次运行开头就报 disk I/O
      或 unable to open database file。
    - 结束时清理: 保持本地整洁。
    """
    test_data_dir = Path("./test_data")
    if test_data_dir.exists():
        shutil.rmtree(test_data_dir, ignore_errors=True)
    yield
    try:
        if test_data_dir.exists():
            shutil.rmtree(test_data_dir, ignore_errors=True)
    except Exception:
        pass


@pytest.fixture(scope="session", autouse=True)
def _mock_content_analyzer_llm():
    """E2E 会话级 Mock 内容分析器 LLM.

    通过 patch invoke_with_fallback 注入 Mock 响应,
    替代原 USE_MOCK_CONTENT_ANALYZER 环境变量, 避免生产代码嵌入测试逻辑.
    """
    from unittest.mock import AsyncMock, Mock, patch

    mock_json = """{
  "summary": "Mock摘要",
  "topic": "Mock主题",
  "title": "Mock主题对话",
  "keywords": ["关键词1", "关键词2"],
  "has_operations": false,
  "operations": []
}"""
    mock_response = Mock()
    mock_response.content = mock_json

    with patch(
        "src.inference.content_analyzer.simple_analyzer.invoke_with_fallback",
        new=AsyncMock(return_value=mock_response),
    ):
        yield


@pytest.fixture(scope="session", autouse=True)
def _mock_main_llm():
    """E2E 会话级 Mock 主对话 LLM.

    通过 patch create_llm 注入 E2EMockLLM,
    替代原 USE_MOCK_LLM 环境变量, 避免生产代码嵌入测试逻辑.
    """
    from unittest.mock import patch

    from tests.e2e.mock_llm import create_mock_llm

    mock_llm = create_mock_llm(
        response_content="这是测试的Mock响应,系统运行正常.",
    )

    with patch(
        "src.agent.processors.inference_coordinator.create_llm",
        return_value=mock_llm,
    ):
        yield


@pytest.fixture(scope="session", autouse=True)
def _mock_embeddings():
    """E2E 会话级 Mock 嵌入模型.

    通过 patch get_embeddings_factory 注入 Mock 嵌入模型,
    替代原 USE_MOCK_EMBEDDINGS 环境变量, 避免生产代码嵌入测试逻辑.
    """
    from unittest.mock import Mock, patch

    from tests.mocks.unified_factory import UnifiedMockFactory

    mock_factory = Mock()
    mock_factory.get_embeddings.return_value = UnifiedMockFactory.create_embeddings(
        dimensions=384, realistic=True
    )

    with patch(
        "src.inference.embeddings.embeddings.get_embeddings_factory",
        return_value=mock_factory,
    ):
        yield


@pytest.fixture(scope="session", autouse=True)
def _force_nullpool_for_tests():
    """测试全程强制 AsyncDatabaseManager 使用 NullPool, 并简化 SQLite PRAGMA.

    后台任务持有的 aiosqlite 连接若未及时 dispose, GC 触发
    Connection.__del__ 在异 loop/已关闭 loop 上关连接, 损坏 SQLite WAL,
    造成偶发 disk I/O error / Event loop is closed。NullPool 用完即弃,
    从源头消除跨循环连接泄漏。

    E2E 中 rapid 创建/关闭多个 SQLite 文件时, 默认 WAL 模式(-wal/-shm 文件)
    与 aiosqlite worker 线程生命周期偶发冲突, 导致 `PRAGMA journal_mode=WAL`
    报 disk I/O 或表创建失败(no such table)。测试只验证业务行为, 无需 WAL,
    故简化为仅保留 busy_timeout, 提升稳定性。仅测试生效。
    """
    from unittest.mock import patch

    from sqlalchemy.ext.asyncio import create_async_engine as _orig
    from sqlalchemy.pool import NullPool

    import src.storage.dao.async_database_manager as adm

    def _create_async_engine(url: str, **kwargs: object) -> object:
        for k in ("pool_size", "max_overflow", "pool_timeout", "pool_recycle"):
            kwargs.pop(k, None)
        kwargs["poolclass"] = NullPool
        return _orig(url, **kwargs)

    def _set_sqlite_pragma_test(dbapi_conn: object, connection_record: object) -> None:
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.close()

    with (
        patch.object(adm, "create_async_engine", _create_async_engine),
        patch.object(adm, "_set_sqlite_pragma", _set_sqlite_pragma_test),
    ):
        yield


@pytest_asyncio.fixture(autouse=True, loop_scope="session")
async def _e2e_reset_state() -> AsyncIterator[None]:
    """每个测试前后: drain 后台任务 + dispose engine + 清缓存, 消除跨测试污染."""
    from src.agent.memory.local_memory import (
        index_run_service,
        pinned_memory_service,
    )
    from src.agent.memory.simple_memory import service as simple_memory_service
    from src.storage.dao import async_database_manager as adm
    from src.storage.service.service_factory import clear_vector_cache
    from src.utils import async_utils

    async def _drain() -> None:
        tasks = [
            *pinned_memory_service.get_bg_tasks(),
            *index_run_service.get_bg_tasks(),
            *simple_memory_service.get_bg_tasks(),
            *set(async_utils._background_tasks),
        ]
        if not tasks:
            return
        try:
            await asyncio.wait_for(
                asyncio.gather(*tasks, return_exceptions=True),
                timeout=5.0,
            )
        except asyncio.TimeoutError:
            for t in tasks:
                if not t.done():
                    t.cancel()
            for t in tasks:
                with suppress(asyncio.CancelledError, Exception):
                    await t

    # setup: 锁重建绑定当前 loop + 清缓存
    adm._db_cache_lock = asyncio.Lock()
    clear_vector_cache()
    yield
    # teardown: 先 drain(让写入收尾, 避免中途击杀损坏文件), 再 dispose, 再清状态
    await _drain()
    await adm.close_all_db_managers()
    clear_vector_cache()
    pinned_memory_service.clear_module_state()
    index_run_service.clear_module_state()
    simple_memory_service.clear_module_state()


# =============================================================================
# ASGI TestClient
# =============================================================================


@pytest.fixture(scope="session")
def e2e_app():
    """session级: 初始化FastAPI app (一次)."""
    from src.api.fastapi_app import app

    return app


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def e2e_client(e2e_app) -> AsyncGenerator[httpx.AsyncClient, None]:
    """session级: ASGI TestClient (无网络开销).

    loop_scope='session' 为该 session-scoped async fixture 提供专属会话级事件循环,
    替代已移除的 deprecated session event_loop fixture.
    """
    transport = httpx.ASGITransport(app=e2e_app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
        timeout=60.0,
    ) as client:
        yield client


# =============================================================================
# 数据隔离
# =============================================================================


@pytest.fixture
def e2e_test_user() -> str:
    """E2E测试用户ID."""
    return "test_user"


@pytest.fixture
def e2e_test_thread_id(request) -> str:
    """E2E测试线程ID (动态生成以隔离数据)."""
    function_name = (
        request.function.__name__ if hasattr(request, "function") else "unknown"
    )
    random_suffix = secrets.token_hex(4)
    return f"test-{function_name}-{random_suffix}"


@pytest.fixture
def e2e_api_key(e2e_test_user: str, e2e_test_thread_id: str) -> str:
    """E2E测试API Key."""
    random_suffix = secrets.token_hex(4)
    return f"sk-project-{e2e_test_user}-{e2e_test_thread_id}-{random_suffix}"


# =============================================================================
# 灰盒 DB 验证
# =============================================================================


@pytest.fixture
def e2e_db_reader(e2e_test_user: str):
    """灰盒 DB 读取器: 读取测试产生的 SQLite 数据用于验证副作用.

    提供:
        read_todos(thread_id, agent_id) -> list[dict]
        read_conversations(thread_id, agent_id) -> list[dict]
    """

    def _get_db_path(thread_id: str, agent_id: str, db_name: str) -> Path:
        from src.core.path_resolver import get_user_path_resolver

        resolver = get_user_path_resolver()
        return Path(
            resolver.get_database_path(
                e2e_test_user, thread_id, db_name, agent_id=agent_id
            )
        )

    def read_todos(thread_id: str, agent_id: str = "personal-assistant") -> list[dict]:
        db_path = _get_db_path(thread_id, agent_id, "todo")
        if not db_path.exists():
            return []
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute("SELECT * FROM todo_items").fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def read_conversations(
        thread_id: str, agent_id: str = "personal-assistant"
    ) -> list[dict]:
        db_path = _get_db_path(thread_id, agent_id, "conversation_history")
        if not db_path.exists():
            return []
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute("SELECT * FROM conversation_index").fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    return SimpleNamespace(read_todos=read_todos, read_conversations=read_conversations)


# =============================================================================
# 断言辅助
# =============================================================================


@pytest.fixture
def e2e_assertions():
    """E2E测试断言辅助."""

    class E2EAssertions:
        @staticmethod
        def assert_valid_chat_response(response: dict) -> None:
            assert "choices" in response
            assert len(response["choices"]) > 0
            assert "message" in response["choices"][0]
            assert response["choices"][0]["message"]["content"]

        @staticmethod
        def assert_openai_format(response: dict) -> None:
            assert response["object"] == "chat.completion"
            assert "id" in response
            assert "created" in response
            assert "model" in response

    return E2EAssertions()
