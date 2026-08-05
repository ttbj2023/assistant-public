"""LangChainVectorStore单元测试.

测试职责: 验证LangChain Chroma向量存储的核心功能逻辑
测试范围: 初始化、延迟初始化、文档操作、搜索、资源管理
Mock策略: Mock Chroma、嵌入模型、配置系统、文件系统，保留业务逻辑
测试价值: 确保向量存储的正确性和资源管理

⚠️ 测试重点:
- 验证初始化和配置读取
- 验证延迟初始化和并发安全
- 验证文档添加和搜索功能
- 验证元数据清理逻辑
- 验证资源管理
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import pytest
from langchain_core.documents import Document

from src.storage.langchain_vector_store import (
    LangChainVectorStore,
)

# ==================== TestLangChainVectorStoreInitialization ====================


class TestLangChainVectorStoreInitialization:
    """测试LangChainVectorStore初始化"""

    def test_init_should_raise_error_without_user_id(self):
        """测试初始化：缺少user_id应抛出异常"""
        with pytest.raises(ValueError):
            LangChainVectorStore(
                collection_name="test",
                user_id="",
                thread_id="thread",
                agent_id="test-agent",
            )

    def test_init_should_raise_error_without_thread_id(self):
        """测试初始化：缺少thread_id应抛出异常"""
        with pytest.raises(ValueError):
            LangChainVectorStore(
                collection_name="test",
                user_id="user",
                thread_id="",
                agent_id="test-agent",
            )


# ==================== TestLangChainVectorStoreResolution ====================


class TestLangChainVectorStoreResolution:
    """测试LangChainVectorStore路径和配置解析"""

    @pytest.fixture
    def store(self):
        """创建存储实例（Mock路径）"""
        with (
            patch("src.storage.langchain_vector_store.get_config"),
            patch(
                "src.storage.langchain_vector_store.get_vector_path"
            ) as mock_get_path,
        ):
            # Mock Path对象
            mock_path = Mock(spec=Path)
            mock_path.mkdir = Mock()
            mock_get_path.return_value = mock_path

            return LangChainVectorStore(
                collection_name="test",
                user_id="user",
                thread_id="thread",
                agent_id="test-agent",
            )

    @patch("src.storage.langchain_vector_store.get_vector_path")
    def test_resolve_persist_directory_should_use_path_resolver(
        self, mock_get_path, store
    ):
        """测试解析持久化目录：应使用path_resolver"""
        # Mock Path对象
        mock_path = Mock(spec=Path)
        mock_path.mkdir = Mock()
        mock_get_path.return_value = mock_path

        result = store._resolve_persist_directory(None)

        assert result == str(mock_path)
        mock_get_path.assert_called_once_with("user", "thread", agent_id="test-agent")


# ==================== TestLangChainVectorStoreExecutor ====================


class TestLangChainVectorStoreExecutor:
    """测试LangChainVectorStore线程池管理"""

    @pytest.fixture
    def store(self):
        """创建存储实例"""
        with patch("src.storage.langchain_vector_store.get_config"):
            with patch("src.storage.langchain_vector_store.get_vector_path"):
                return LangChainVectorStore(
                    collection_name="test",
                    user_id="user",
                    thread_id="thread",
                    agent_id="test-agent",
                )

    def test_get_executor_should_return_singleton(self, store):
        """测试获取执行器：应返回单例实例"""
        executor1 = store._get_executor()
        executor2 = store._get_executor()

        assert executor1 is executor2


# ==================== TestLangChainVectorStoreEmbeddings ====================


class TestLangChainVectorStoreEmbeddings:
    """测试LangChainVectorStore嵌入模型配置"""

    @pytest.fixture
    def store(self):
        """创建存储实例"""
        with patch("src.storage.langchain_vector_store.get_config"):
            with patch("src.storage.langchain_vector_store.get_vector_path"):
                return LangChainVectorStore(
                    collection_name="test",
                    user_id="user",
                    thread_id="thread",
                    embedding_model="local:test-model",
                    agent_id="test-agent",
                )

    def test_validate_and_parse_config_valid(self, store):
        """测试验证配置：有效配置应解析成功"""
        provider, model_id = store._validate_and_parse_embedding_config(
            "local-embedding:bge-m3"
        )

        assert provider == "local-embedding"
        assert model_id == "bge-m3"

    def test_validate_and_parse_config_missing_colon(self, store):
        """测试验证配置：缺少冒号应抛出异常"""
        with pytest.raises(ValueError, match="配置格式错误"):
            store._validate_and_parse_embedding_config("invalid-model")

    def test_validate_and_parse_config_empty_parts(self, store):
        """测试验证配置：空部分应抛出异常"""
        with pytest.raises(ValueError, match="provider和model都不能为空"):
            store._validate_and_parse_embedding_config(":model")


# ==================== TestLangChainVectorStoreMetadataCleaning ====================


class TestLangChainVectorStoreMetadataCleaning:
    """测试LangChainVectorStore元数据清理"""

    @pytest.fixture
    def store(self):
        """创建存储实例"""
        with patch("src.storage.langchain_vector_store.get_config"):
            with patch("src.storage.langchain_vector_store.get_vector_path"):
                return LangChainVectorStore(
                    collection_name="test",
                    user_id="user",
                    thread_id="thread",
                    agent_id="test-agent",
                )

    def test_clean_metadata_should_convert_list_to_string(self, store):
        """测试清理元数据：列表应转换为字符串"""
        metadata = {"tags": ["tag1", "tag2", "tag3"]}

        cleaned = store._clean_metadata(metadata)

        assert cleaned["tags"] == "tag1, tag2, tag3"

    def test_clean_metadata_should_convert_dict_to_json(self, store):
        """测试清理元数据：字典应转换为JSON字符串"""
        metadata = {"data": {"key": "value"}}

        cleaned = store._clean_metadata(metadata)

        import json

        assert json.loads(cleaned["data"]) == {"key": "value"}

    def test_clean_metadata_value_should_convert_other_to_string(self, store):
        """测试清理元数据值：其他类型应转换为字符串"""
        result = store._clean_metadata_value(123.45)

        assert result == 123.45  # 基本类型保留

        result = store._clean_metadata_value(object())
        assert isinstance(result, str)  # 复杂类型转字符串


# ==================== TestLangChainVectorStoreAddDocuments ====================


class TestLangChainVectorStoreAddDocuments:
    """测试LangChainVectorStore添加文档"""

    @pytest.fixture
    def store(self):
        """创建初始化的存储实例"""
        with patch("src.storage.langchain_vector_store.get_config"):
            with patch("src.storage.langchain_vector_store.get_vector_path"):
                store = LangChainVectorStore(
                    collection_name="test",
                    user_id="user",
                    thread_id="thread",
                    agent_id="test-agent",
                )
                # Mock初始化状态
                store._initialized = True
                store._collection = Mock()
                store._embeddings = AsyncMock()
                store._executor = Mock()
                return store

    @pytest.mark.asyncio
    async def test_add_documents_should_raise_error_for_empty_list(self, store):
        """测试添加文档：空列表应抛出异常"""
        with pytest.raises(ValueError):
            await store.add_documents([])

    @pytest.mark.asyncio
    async def test_add_documents_should_raise_error_for_mismatched_ids(self, store):
        """测试添加文档：ID数量不匹配应抛出异常"""
        documents = [Document(page_content="content")]

        with pytest.raises(ValueError):
            await store.add_documents(documents, ids=["id1", "id2"])


# ==================== TestLangChainVectorStoreSimilaritySearch ====================


class TestLangChainVectorStoreSimilaritySearch:
    """测试LangChainVectorStore相似性搜索"""

    @pytest.fixture
    def store(self):
        """创建初始化的存储实例"""
        with patch("src.storage.langchain_vector_store.get_config"):
            with patch("src.storage.langchain_vector_store.get_vector_path"):
                store = LangChainVectorStore(
                    collection_name="test",
                    user_id="user",
                    thread_id="thread",
                    agent_id="test-agent",
                )
                store._initialized = True
                store._collection = Mock()
                store._embeddings = AsyncMock()
                store._executor = Mock()
                return store

    @pytest.mark.asyncio
    async def test_similarity_search_should_raise_error_for_empty_query(self, store):
        """测试相似性搜索：空查询应抛出异常"""
        with pytest.raises(ValueError):
            await store.similarity_search("")

    @pytest.mark.asyncio
    async def test_similarity_search_should_raise_error_for_invalid_max_results(
        self, store
    ):
        """测试相似性搜索：无效max_results应抛出异常"""
        with pytest.raises(ValueError):
            await store.similarity_search("query", max_results=0)


# ==================== TestLangChainVectorStoreDelete ====================


class TestLangChainVectorStoreDelete:
    """测试LangChainVectorStore删除文档"""

    @pytest.fixture
    def store(self):
        """创建初始化的存储实例"""
        with patch("src.storage.langchain_vector_store.get_config"):
            with patch("src.storage.langchain_vector_store.get_vector_path"):
                store = LangChainVectorStore(
                    collection_name="test",
                    user_id="user",
                    thread_id="thread",
                    agent_id="test-agent",
                )
                store._initialized = True
                store._collection = Mock()
                return store

    @pytest.mark.asyncio
    async def test_delete_should_raise_error_for_empty_ids(self, store):
        """测试删除：空ID列表应抛出异常"""
        with pytest.raises(ValueError):
            await store.delete(ids=[])


# ==================== TestLangChainVectorStoreResourceManagement ====================


class TestLangChainVectorStoreResourceManagement:
    """测试LangChainVectorStore资源管理"""

    @pytest.fixture
    def store(self):
        """创建存储实例"""
        with patch("src.storage.langchain_vector_store.get_config"):
            with patch("src.storage.langchain_vector_store.get_vector_path"):
                store = LangChainVectorStore(
                    collection_name="test",
                    user_id="user",
                    thread_id="thread",
                    agent_id="test-agent",
                )
                # 创建mock executor，模拟真实的ThreadPoolExecutor行为
                mock_executor = Mock()
                mock_executor.shutdown = Mock(return_value=None)
                store._executor = mock_executor
                return store

    def test_close_should_shutdown_executor(self, store):
        """测试关闭：应关闭线程池"""
        # 保存executor引用，因为close后会设置为None
        executor = store._executor
        assert executor is not None

        store.close()

        # 验证shutdown被调用（使用保存的引用）
        executor.shutdown.assert_called_once_with(wait=True)
        assert store._executor is None

    def test_del_should_use_non_blocking_shutdown(self, store):
        """析构兜底应非阻塞 shutdown(wait=False), 避免在事件循环/GC 线程中阻塞."""
        executor = store._executor
        assert executor is not None

        store.__del__()

        executor.shutdown.assert_called_once_with(wait=False)


def test_distance_to_similarity_uses_l2_formula():
    """L2 distance → 相似度应用 1/(1+d) 单调递减公式, 落在 (0,1]."""
    from src.storage.langchain_vector_store import distance_to_similarity

    # d=0 (完全相同) → 1.0
    assert distance_to_similarity(0.0) == 1.0
    # 单调递减: d 越大相似度越低
    assert distance_to_similarity(0.5) > distance_to_similarity(1.0)
    assert distance_to_similarity(1.0) == 0.5
    # 大 distance 趋近 0 但非负
    assert 0.0 <= distance_to_similarity(100.0) < 0.02

    def test_get_collection_stats_uninitialized_should_return_error(self, store):
        """测试获取统计：未初始化应返回错误"""
        store._collection = None

        stats = store.get_collection_stats()

        assert "error" in stats


# ==================== TestLangChainVectorStoreExistingRounds ====================


class TestLangChainVectorStoreExistingRounds:
    """测试 get_existing_round_numbers: 向量补偿 diff 的数据源."""

    @pytest.fixture
    def store(self):
        """创建已初始化的存储实例(跳过真实 Chroma 初始化)."""
        with (
            patch("src.storage.langchain_vector_store.get_config"),
            patch("src.storage.langchain_vector_store.get_vector_path"),
        ):
            s = LangChainVectorStore(
                collection_name="test",
                user_id="user",
                thread_id="thread",
                agent_id="agent",
            )
            s._initialized = True
            return s

    @pytest.mark.asyncio
    async def test_should_return_round_numbers_from_metadata(self, store):
        """应从 collection metadatas 提取 round_number 集合."""
        mock_collection = Mock()
        mock_collection.get = Mock(
            return_value={
                "ids": ["x_round_1", "x_round_3"],
                "metadatas": [{"round_number": 1}, {"round_number": 3}],
            },
        )
        store._collection = mock_collection

        result = await store.get_existing_round_numbers()

        assert result == {1, 3}
        mock_collection.get.assert_called_once()

    @pytest.mark.asyncio
    async def test_should_return_empty_set_when_no_documents(self, store):
        """无文档时应返回空集合(首次补偿场景)."""
        mock_collection = Mock()
        mock_collection.get = Mock(return_value={"ids": [], "metadatas": []})
        store._collection = mock_collection

        result = await store.get_existing_round_numbers()

        assert result == set()

    @pytest.mark.asyncio
    async def test_should_skip_metadata_without_round_number(self, store):
        """应跳过缺少 round_number 的 metadata(兼容历史脏数据)."""
        mock_collection = Mock()
        mock_collection.get = Mock(
            return_value={
                "metadatas": [{"round_number": 2}, None, {"other": "x"}],
            },
        )
        store._collection = mock_collection

        result = await store.get_existing_round_numbers()

        assert result == {2}
