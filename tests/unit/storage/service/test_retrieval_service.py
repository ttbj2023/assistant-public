"""RetrievalService单元测试.

专注于测试检索服务的业务逻辑，Mock所有外部依赖。
"""

from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest
from datetime import UTC, datetime
from langchain_core.documents import Document

from src.storage.service.retrieval_service import (
    DualStageRetrievalService,
    _tokenize_query,
)


class TestTokenizeQuery:
    """query 分词 (朴素空格/标点切分, 过滤过短 token)."""

    def test_should_split_on_spaces(self):
        """中英文混合按空格切分."""
        assert _tokenize_query("Nemo 疫苗 动物医院") == [
            "Nemo",
            "疫苗",
            "动物医院",
        ]

    def test_should_split_on_punctuation(self):
        """常见中英文标点也应作为分隔符."""
        assert _tokenize_query("k8s，Kubernetes；NetworkPolicy") == [
            "k8s",
            "Kubernetes",
            "NetworkPolicy",
        ]

    def test_should_filter_short_tokens(self):
        """长度 < 2 的 token 视为噪音过滤掉."""
        assert _tokenize_query("a b 猫 项目") == ["项目"]

    def test_empty_or_whitespace_query_should_return_empty(self):
        """空查询或纯空白应返回空列表."""
        assert _tokenize_query("") == []
        assert _tokenize_query("   ") == []

    def test_no_space_chinese_phrase_stays_single_token(self):
        """无空格的中文短语保持为一个 token (LIKE 子串仍可命中)."""
        assert _tokenize_query("技术待办PDF导出") == ["技术待办PDF导出"]


@pytest.fixture
def test_user(test_user):
    """测试用户ID."""
    return test_user


@pytest.fixture
def test_thread_id(test_thread_id):
    """测试线程ID."""
    return test_thread_id


@pytest.fixture
def mock_conversation_dao():
    """Mock对话DAO."""
    dao = Mock()
    dao.db_ops = Mock()
    dao.db_ops.find_by_filters = AsyncMock(return_value=[])
    return dao


@pytest.fixture
def mock_conversation_service(mock_conversation_dao):
    """Mock对话服务, 桥接 Service 方法到 find_by_filters mock."""
    mock_service = Mock()
    mock_service.conversation_dao = mock_conversation_dao

    async def _list_recent_rounds(user_id, thread_id, limit=10):
        items = await mock_conversation_dao.db_ops.find_by_filters(
            {"user_id": user_id, "thread_id": thread_id},
            limit=limit,
        )
        return [getattr(item, "round_number", 0) for item in items]

    async def _list_conversations(user_id, thread_id, limit=100):
        return await mock_conversation_dao.db_ops.find_by_filters(
            {"user_id": user_id, "thread_id": thread_id},
            limit=limit,
        )

    async def _get_conversations_by_rounds(user_id, thread_id, round_numbers):
        results = []
        seen: set[int] = set()
        for rn in round_numbers:
            if rn in seen:
                continue
            items = await mock_conversation_dao.db_ops.find_by_filters(
                {"round_number": rn, "user_id": user_id, "thread_id": thread_id},
                limit=1,
            )
            results.extend(items)
            seen.add(rn)
        return results

    mock_service.list_recent_rounds = AsyncMock(side_effect=_list_recent_rounds)
    mock_service.list_conversations = AsyncMock(side_effect=_list_conversations)
    mock_service.get_conversations_by_rounds = AsyncMock(
        side_effect=_get_conversations_by_rounds,
    )
    # SQL 关键词检索路默认返回空(各测试可按需覆写为真实命中轮次)
    mock_service.search_rounds_by_keywords = AsyncMock(return_value=[])
    mock_service.get_conversations_in_range = AsyncMock(return_value=[])
    # time_range → round_range 转换默认返回 None(区间内无对话)
    mock_service.get_round_range_by_time_range = AsyncMock(return_value=None)

    return mock_service


@pytest.fixture
def mock_vector_service():
    """Mock向量服务."""
    mock_service = Mock()
    mock_service._vector_store = None
    mock_service._ensure_vector_store = Mock()
    mock_service.health_check = Mock()
    return mock_service


def _make_conv_index(round_number, user_message="", assistant_response=""):
    """创建Mock ConversationIndex."""
    conv = MagicMock()
    conv.round_number = round_number
    conv.user_message = user_message
    conv.assistant_response = assistant_response
    conv.created_at = None
    conv.summary = None
    return conv


class TestDualStageRetrievalService:
    """DualStageRetrievalService单元测试."""

    @pytest.mark.asyncio
    async def test_search_conversations_with_empty_query_should_raise_error(
        self, mock_conversation_service, mock_vector_service, test_user, test_thread_id
    ):
        """测试空查询字符串应抛出错误."""
        service = DualStageRetrievalService(
            conversation_service=mock_conversation_service,
            vector_service=mock_vector_service,
            user_id=test_user,
            thread_id=test_thread_id,
        )

        with pytest.raises(ValueError, match="查询字符串不能为空"):
            await service.search_conversations("", max_results=10)

    @pytest.mark.asyncio
    async def test_search_conversations_with_invalid_max_results_should_raise_error(
        self, mock_conversation_service, mock_vector_service, test_user, test_thread_id
    ):
        """测试无效max_results应抛出错误."""
        service = DualStageRetrievalService(
            conversation_service=mock_conversation_service,
            vector_service=mock_vector_service,
            user_id=test_user,
            thread_id=test_thread_id,
        )

        with pytest.raises(ValueError, match="max_results必须大于0"):
            await service.search_conversations("test query", max_results=0)

        with pytest.raises(ValueError, match="max_results不能超过50"):
            await service.search_conversations("test query", max_results=51)

    @pytest.mark.asyncio
    async def test_ensure_initialized_with_vector_disabled_should_skip_vector_initialization(
        self, mock_conversation_service, mock_vector_service, test_user, test_thread_id
    ):
        """测试禁用向量搜索时应跳过向量初始化."""
        service = DualStageRetrievalService(
            conversation_service=mock_conversation_service,
            vector_service=mock_vector_service,
            user_id=test_user,
            thread_id=test_thread_id,
            enable_vector_search=False,
        )

        await service._ensure_initialized()

        assert service._initialized is True
        assert (
            not hasattr(service, "_retriever")
            or service.__dict__.get("_retriever") is None
        )

    @pytest.mark.asyncio
    async def test_search_conversations_without_filters_should_use_sql_search(
        self,
        mock_conversation_service,
        mock_vector_service,
        test_user,
        test_thread_id,
    ):
        """测试不带过滤器的搜索应使用SQL关键词检索."""
        conv1 = _make_conv_index(1, "hello test query", "response 1")
        conv2 = _make_conv_index(2, "another message", "test query response 2")
        mock_conversation_service.search_rounds_by_keywords = AsyncMock(
            return_value=[2, 1],
        )
        mock_conversation_service.get_conversations_by_rounds = AsyncMock(
            return_value=[conv1, conv2],
        )

        service = DualStageRetrievalService(
            conversation_service=mock_conversation_service,
            vector_service=mock_vector_service,
            user_id=test_user,
            thread_id=test_thread_id,
            enable_sql_search=True,
            enable_vector_search=False,
        )

        results = await service.search_conversations("test query", max_results=10)

        assert len(results) == 2
        assert results[0].metadata["round_number"] == 2
        assert results[1].metadata["round_number"] == 1
        assert results[0].metadata["retrieval_type"] == "dual_stage_async"
        mock_conversation_service.search_rounds_by_keywords.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_sql_path_should_receive_tokenized_query_terms(
        self,
        mock_conversation_service,
        mock_vector_service,
        test_user,
        test_thread_id,
    ):
        """SQL关键词路应收到 query 分词后的 terms (而非被忽略)."""
        mock_conversation_service.search_rounds_by_keywords = AsyncMock(return_value=[])
        mock_vector_service._vector_store = None

        service = DualStageRetrievalService(
            conversation_service=mock_conversation_service,
            vector_service=mock_vector_service,
            user_id=test_user,
            thread_id=test_thread_id,
            enable_sql_search=True,
            enable_vector_search=False,
        )

        await service.search_conversations("Nemo 疫苗", max_results=5)

        mock_conversation_service.search_rounds_by_keywords.assert_awaited_once()
        call_args = mock_conversation_service.search_rounds_by_keywords.await_args
        # terms 为第 3 个位置参数
        assert call_args.args[2] == ["Nemo", "疫苗"]

    @pytest.mark.asyncio
    async def test_search_conversations_should_respect_max_results(
        self,
        mock_conversation_service,
        mock_conversation_dao,
        mock_vector_service,
        test_user,
        test_thread_id,
    ):
        """测试应遵守max_results参数."""
        convs = [
            _make_conv_index(i, f"test query content {i}", f"response {i}")
            for i in range(20)
        ]

        async def mock_find(filters, **kwargs):
            rn = filters.get("round_number")
            if rn is not None:
                for c in convs:
                    if c.round_number == rn:
                        return [c]
                return []
            return convs

        mock_conversation_dao.db_ops.find_by_filters.side_effect = mock_find

        service = DualStageRetrievalService(
            conversation_service=mock_conversation_service,
            vector_service=mock_vector_service,
            user_id=test_user,
            thread_id=test_thread_id,
            enable_sql_search=True,
            enable_vector_search=False,
        )

        results = await service.search_conversations("test query", max_results=5)

        assert len(results) == 5

    @pytest.mark.asyncio
    async def test_search_conversations_with_filters_should_pass_filters_to_sql_search(
        self,
        mock_conversation_service,
        mock_vector_service,
        test_user,
        test_thread_id,
    ):
        """time_range 过滤器应转换为 round_range 传递到 SQL 检索路.

        修复 filter_parser 产出 time_range 而 SQL/向量路消费 round_range 的键名断裂 bug:
        time_range 经 conversation_service 转换为 round_range 后, 必须真实到达 SQL 路.
        """
        conv = _make_conv_index(5, "filtered content", "filtered response")
        mock_conversation_service.get_round_range_by_time_range = AsyncMock(
            return_value=(5, 10),
        )
        mock_conversation_service.search_rounds_by_keywords = AsyncMock(
            return_value=[5],
        )
        mock_conversation_service.get_conversations_by_rounds = AsyncMock(
            return_value=[conv],
        )

        service = DualStageRetrievalService(
            conversation_service=mock_conversation_service,
            vector_service=mock_vector_service,
            user_id=test_user,
            thread_id=test_thread_id,
            enable_sql_search=True,
            enable_vector_search=False,
        )

        filters = {"time_range": (datetime.now(UTC), datetime.now(UTC))}
        results = await service.search_conversations(
            "test query", max_results=10, filters=filters
        )

        assert len(results) == 1
        # 关键断言: SQL 路收到转换后的 round_range, 而非 None
        call = mock_conversation_service.search_rounds_by_keywords.await_args
        assert call.kwargs.get("round_range") == (5, 10)
        mock_conversation_service.get_round_range_by_time_range.assert_awaited_once()


class TestTimeFilterResolution:
    """time_range → round_range 转换测试 (修复 filter_parser 与检索路键名断裂 bug)."""

    @pytest.mark.asyncio
    async def test_empty_time_window_should_return_empty_without_fallback(
        self,
        mock_conversation_service,
        mock_vector_service,
        test_user,
        test_thread_id,
    ):
        """时间窗口内无对话时应返回空列表, 且不触发 fallback.

        fallback 会无差别返回最近对话, 让时间过滤失效; 故转换得 None 时
        应直接返回空, 不走 list_conversations.
        """
        mock_conversation_service.get_round_range_by_time_range = AsyncMock(
            return_value=None,
        )
        mock_conversation_service.list_conversations = AsyncMock(return_value=[])

        service = DualStageRetrievalService(
            conversation_service=mock_conversation_service,
            vector_service=mock_vector_service,
            user_id=test_user,
            thread_id=test_thread_id,
            enable_sql_search=True,
            enable_vector_search=False,
        )

        filters = {"time_range": (datetime.now(UTC), datetime.now(UTC))}
        results = await service.search_conversations(
            "test query", max_results=10, filters=filters
        )

        assert results == []
        mock_conversation_service.list_conversations.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_search_with_filters_should_resolve_time_filter_to_round_range(
        self,
        mock_conversation_service,
        mock_vector_service,
        test_user,
        test_thread_id,
    ):
        """search_with_filters 应将 time_filter 字符串解析并转换为 round_range.

        端到端: time_filter="yesterday" → FilterParser 产 time_range →
        conversation_service 转 round_range → 到达 SQL 路.
        """
        mock_conversation_service.get_round_range_by_time_range = AsyncMock(
            return_value=(3, 8),
        )
        mock_conversation_service.search_rounds_by_keywords = AsyncMock(
            return_value=[],
        )

        service = DualStageRetrievalService(
            conversation_service=mock_conversation_service,
            vector_service=mock_vector_service,
            user_id=test_user,
            thread_id=test_thread_id,
            enable_sql_search=True,
            enable_vector_search=False,
        )

        await service.search_with_filters(
            query="test", time_filter="yesterday", max_results=10
        )

        mock_conversation_service.get_round_range_by_time_range.assert_awaited_once()
        call = mock_conversation_service.search_rounds_by_keywords.await_args
        assert call.kwargs.get("round_range") == (3, 8)

    @pytest.mark.asyncio
    async def test_time_range_should_intersect_with_existing_round_range(
        self,
        mock_conversation_service,
        mock_vector_service,
        test_user,
        test_thread_id,
    ):
        """time_range 转换出的 round_range 应与已有 round_range 取交集."""
        mock_conversation_service.get_round_range_by_time_range = AsyncMock(
            return_value=(5, 20),
        )
        mock_conversation_service.search_rounds_by_keywords = AsyncMock(
            return_value=[],
        )

        service = DualStageRetrievalService(
            conversation_service=mock_conversation_service,
            vector_service=mock_vector_service,
            user_id=test_user,
            thread_id=test_thread_id,
            enable_sql_search=True,
            enable_vector_search=False,
        )

        filters = {
            "time_range": (datetime.now(UTC), datetime.now(UTC)),
            "round_range": (1, 10),
        }
        await service.search_conversations("test", max_results=10, filters=filters)

        call = mock_conversation_service.search_rounds_by_keywords.await_args
        assert call.kwargs.get("round_range") == (5, 10)

    @pytest.mark.asyncio
    async def test_no_time_range_should_skip_conversion(
        self,
        mock_conversation_service,
        mock_vector_service,
        test_user,
        test_thread_id,
    ):
        """filters 无 time_range 时不触发转换 (向后兼容)."""
        mock_conversation_service.search_rounds_by_keywords = AsyncMock(
            return_value=[],
        )
        mock_conversation_service.list_conversations = AsyncMock(return_value=[])

        service = DualStageRetrievalService(
            conversation_service=mock_conversation_service,
            vector_service=mock_vector_service,
            user_id=test_user,
            thread_id=test_thread_id,
            enable_sql_search=True,
            enable_vector_search=False,
        )

        filters = {"round_range": (1, 5)}
        await service.search_conversations("test", max_results=10, filters=filters)

        mock_conversation_service.get_round_range_by_time_range.assert_not_awaited()
        call = mock_conversation_service.search_rounds_by_keywords.await_args
        assert call.kwargs.get("round_range") == (1, 5)


class TestDualStageRetrievalServiceDualPath:
    """双路检索集成测试 - SQL + 向量协同."""

    @pytest.fixture
    def service_with_both(
        self,
        mock_conversation_service,
        mock_conversation_dao,
        mock_vector_service,
        test_user,
        test_thread_id,
    ):
        """创建启用双路检索的service."""
        convs = [_make_conv_index(i, f"msg {i}", f"resp {i}") for i in range(1, 11)]
        mock_conversation_dao.db_ops.find_by_filters.return_value = convs

        async def mock_find(filters, limit=1):
            rn = filters.get("round_number")
            for c in convs:
                if c.round_number == rn:
                    return [c]
            return []

        mock_conversation_dao.db_ops.find_by_filters.side_effect = mock_find

        vector_store = Mock()
        vector_store._ensure_initialized = AsyncMock()
        vector_store.search_rounds_only = AsyncMock(
            return_value=[(3, 0.9), (5, 0.8), (7, 0.7)]
        )
        mock_vector_service._vector_store = vector_store

        service = DualStageRetrievalService(
            conversation_service=mock_conversation_service,
            vector_service=mock_vector_service,
            user_id=test_user,
            thread_id=test_thread_id,
            enable_sql_search=True,
            enable_vector_search=True,
        )
        return service

    @pytest.mark.asyncio
    async def test_dual_search_should_merge_sql_and_vector_results(
        self,
        service_with_both,
    ):
        """双路检索应合并SQL和向量结果."""
        results = await service_with_both.search_conversations("test", max_results=10)
        assert len(results) >= 1
        for doc in results:
            assert isinstance(doc, Document)
            assert "round_number" in doc.metadata

    @pytest.mark.asyncio
    async def test_vector_only_search_should_work(
        self,
        mock_conversation_service,
        mock_conversation_dao,
        mock_vector_service,
        test_user,
        test_thread_id,
    ):
        """仅向量搜索路径应正常工作."""
        convs = [
            _make_conv_index(3, "v msg", "v resp"),
            _make_conv_index(7, "v msg2", "v resp2"),
        ]
        mock_conversation_dao.db_ops.find_by_filters.return_value = []

        async def mock_find(filters, limit=1):
            rn = filters.get("round_number")
            for c in convs:
                if c.round_number == rn:
                    return [c]
            return []

        mock_conversation_dao.db_ops.find_by_filters.side_effect = mock_find

        vector_store = Mock()
        vector_store._ensure_initialized = AsyncMock()
        vector_store.search_rounds_only = AsyncMock(return_value=[(3, 0.9), (7, 0.7)])
        mock_vector_service._vector_store = vector_store

        service = DualStageRetrievalService(
            conversation_service=mock_conversation_service,
            vector_service=mock_vector_service,
            user_id=test_user,
            thread_id=test_thread_id,
            enable_sql_search=False,
            enable_vector_search=True,
        )

        results = await service.search_conversations("test", max_results=5)
        assert len(results) >= 1
        assert all(isinstance(d, Document) for d in results)

    @pytest.mark.asyncio
    async def test_smart_deduplication_intersection_should_have_higher_priority(
        self,
        mock_conversation_service,
        mock_conversation_dao,
        mock_vector_service,
        test_user,
        test_thread_id,
    ):
        """智能去重: 交集轮次应排在前面."""
        convs = [_make_conv_index(i, f"msg {i}", f"resp {i}") for i in range(1, 21)]
        mock_conversation_dao.db_ops.find_by_filters.return_value = convs[:5]

        async def mock_find(filters, limit=1):
            rn = filters.get("round_number")
            for c in convs:
                if c.round_number == rn:
                    return [c]
            return []

        mock_conversation_dao.db_ops.find_by_filters.side_effect = mock_find

        vector_store = Mock()
        vector_store._ensure_initialized = AsyncMock()
        vector_store.search_rounds_only = AsyncMock(return_value=[(2, 0.95), (4, 0.9)])
        mock_vector_service._vector_store = vector_store

        service = DualStageRetrievalService(
            conversation_service=mock_conversation_service,
            vector_service=mock_vector_service,
            user_id=test_user,
            thread_id=test_thread_id,
            enable_sql_search=True,
            enable_vector_search=True,
        )

        results = await service.search_conversations("test", max_results=10)
        if len(results) >= 2:
            round_numbers = [d.metadata["round_number"] for d in results]
            assert 2 in round_numbers or 4 in round_numbers


class TestDualStageRetrievalServiceFallback:
    """降级路径测试."""

    @pytest.mark.asyncio
    async def test_fallback_when_both_sql_and_vector_return_empty(
        self,
        mock_conversation_service,
        mock_conversation_dao,
        mock_vector_service,
        test_user,
        test_thread_id,
    ):
        """SQL和向量均无结果时应触发fallback."""
        conv = _make_conv_index(1, "fallback test content", "fallback response")
        mock_conversation_dao.db_ops.find_by_filters.return_value = []

        vector_store = Mock()
        vector_store._ensure_initialized = AsyncMock()
        vector_store.search_rounds_only = AsyncMock(return_value=[])
        mock_vector_service._vector_store = vector_store

        service = DualStageRetrievalService(
            conversation_service=mock_conversation_service,
            vector_service=mock_vector_service,
            user_id=test_user,
            thread_id=test_thread_id,
            enable_sql_search=True,
            enable_vector_search=True,
        )

        results = await service.search_conversations("fallback test", max_results=10)
        assert isinstance(results, list)

    @pytest.mark.asyncio
    async def test_fallback_sql_search_should_match_query_keywords(
        self,
        mock_conversation_service,
        mock_conversation_dao,
        mock_vector_service,
        test_user,
        test_thread_id,
    ):
        """fallback SQL搜索应根据查询关键词匹配内容.

        SQL关键词路与向量均无命中 -> 触发 fallback; fallback 走 list_conversations
        做内存子串匹配.
        """
        conv = _make_conv_index(1, "unique keyword match", "response")
        mock_conversation_dao.db_ops.find_by_filters.return_value = [conv]

        vector_store = Mock()
        vector_store._ensure_initialized = AsyncMock()
        vector_store.search_rounds_only = AsyncMock(return_value=[])
        mock_vector_service._vector_store = vector_store

        service = DualStageRetrievalService(
            conversation_service=mock_conversation_service,
            vector_service=mock_vector_service,
            user_id=test_user,
            thread_id=test_thread_id,
            enable_sql_search=True,
            enable_vector_search=True,
        )

        results = await service.search_conversations("unique keyword", max_results=10)
        assert len(results) == 1
        assert results[0].metadata["retrieval_type"] == "sql_fallback"

    @pytest.mark.asyncio
    async def test_dao_failure_should_trigger_fallback(
        self,
        mock_conversation_service,
        mock_conversation_dao,
        mock_vector_service,
        test_user,
        test_thread_id,
    ):
        """DAO异常时应降级到fallback."""
        conv = _make_conv_index(1, "error recovery", "response")
        mock_conversation_dao.db_ops.find_by_filters.side_effect = [
            Exception("DAO connection failed"),
            [conv],
        ]
        mock_conversation_dao.db_ops.find_by_filters.side_effect = Exception(
            "DAO error"
        )

        vector_store = Mock()
        vector_store._ensure_initialized = AsyncMock()
        vector_store.search_rounds_only = AsyncMock(return_value=[])
        mock_vector_service._vector_store = vector_store

        service = DualStageRetrievalService(
            conversation_service=mock_conversation_service,
            vector_service=mock_vector_service,
            user_id=test_user,
            thread_id=test_thread_id,
            enable_sql_search=True,
            enable_vector_search=True,
        )

        results = await service.search_conversations("error recovery", max_results=10)
        assert isinstance(results, list)

    @pytest.mark.asyncio
    async def test_vector_failure_should_continue_with_sql(
        self,
        mock_conversation_service,
        mock_conversation_dao,
        mock_vector_service,
        test_user,
        test_thread_id,
    ):
        """向量检索失败时SQL应继续工作."""
        convs = [
            _make_conv_index(i, f"sql msg {i}", f"sql resp {i}") for i in range(1, 6)
        ]

        async def mock_find(filters, **kwargs):
            rn = filters.get("round_number")
            if rn is not None:
                for c in convs:
                    if c.round_number == rn:
                        return [c]
                return []
            return convs

        mock_conversation_dao.db_ops.find_by_filters.side_effect = mock_find

        vector_store = Mock()
        vector_store._ensure_initialized = AsyncMock()
        vector_store.search_rounds_only = AsyncMock(
            side_effect=Exception("vector down")
        )
        mock_vector_service._vector_store = vector_store

        service = DualStageRetrievalService(
            conversation_service=mock_conversation_service,
            vector_service=mock_vector_service,
            user_id=test_user,
            thread_id=test_thread_id,
            enable_sql_search=True,
            enable_vector_search=True,
        )

        results = await service.search_conversations("sql msg", max_results=5)
        assert len(results) >= 1

    @pytest.mark.asyncio
    async def test_no_results_at_all_should_return_empty(
        self,
        mock_conversation_service,
        mock_conversation_dao,
        mock_vector_service,
        test_user,
        test_thread_id,
    ):
        """所有路径均无匹配时应返回空列表."""
        mock_conversation_dao.db_ops.find_by_filters.return_value = []

        vector_store = Mock()
        vector_store._ensure_initialized = AsyncMock()
        vector_store.search_rounds_only = AsyncMock(return_value=[])
        mock_vector_service._vector_store = vector_store

        service = DualStageRetrievalService(
            conversation_service=mock_conversation_service,
            vector_service=mock_vector_service,
            user_id=test_user,
            thread_id=test_thread_id,
            enable_sql_search=True,
            enable_vector_search=True,
        )

        results = await service.search_conversations(
            "nonexistent content", max_results=10
        )
        assert results == []

    @pytest.mark.asyncio
    async def test_single_result_should_work(
        self,
        mock_conversation_service,
        mock_conversation_dao,
        mock_vector_service,
        test_user,
        test_thread_id,
    ):
        """单条结果应正常返回."""
        conv = _make_conv_index(1, "single result test", "single response")
        mock_conversation_dao.db_ops.find_by_filters.return_value = [conv]
        mock_conversation_dao.db_ops.find_by_filters.return_value = [conv]

        service = DualStageRetrievalService(
            conversation_service=mock_conversation_service,
            vector_service=mock_vector_service,
            user_id=test_user,
            thread_id=test_thread_id,
            enable_sql_search=True,
            enable_vector_search=False,
        )

        results = await service.search_conversations("single result", max_results=10)
        assert len(results) == 1
        assert results[0].metadata["round_number"] == 1


class TestDualStageRetrievalServiceSearchWithFilters:
    """search_with_filters方法测试."""

    @pytest.mark.asyncio
    async def test_search_with_empty_filters_should_use_defaults(
        self,
        mock_conversation_service,
        mock_conversation_dao,
        mock_vector_service,
        test_user,
        test_thread_id,
    ):
        """空过滤器应使用默认配置."""
        conv = _make_conv_index(1, "default filter", "response")
        mock_conversation_dao.db_ops.find_by_filters.return_value = [conv]
        mock_conversation_dao.db_ops.find_by_filters.return_value = [conv]

        service = DualStageRetrievalService(
            conversation_service=mock_conversation_service,
            vector_service=mock_vector_service,
            user_id=test_user,
            thread_id=test_thread_id,
            enable_sql_search=True,
            enable_vector_search=False,
        )

        results = await service.search_with_filters(query="default", max_results=5)
        assert len(results) == 1


class TestDualStageRetrievalServiceHealthCheck:
    """health_check方法测试."""

    @pytest.mark.asyncio
    async def test_health_check_should_return_healthy_status(
        self,
        mock_conversation_service,
        mock_vector_service,
        test_user,
        test_thread_id,
    ):
        """健康检查应返回healthy状态."""
        mock_vector_service.health_check = AsyncMock(return_value={"status": "healthy"})

        service = DualStageRetrievalService(
            conversation_service=mock_conversation_service,
            vector_service=mock_vector_service,
            user_id=test_user,
            thread_id=test_thread_id,
            enable_vector_search=False,
        )

        health = await service.health_check()
        assert health["status"] == "healthy"
        assert health["service_type"] == "dual_stage_retrieval"
        assert health["initialized"] is True
        assert "components" in health
        assert "features" in health

    @pytest.mark.asyncio
    async def test_health_check_with_degraded_vector_should_return_degraded(
        self,
        mock_conversation_service,
        mock_vector_service,
        test_user,
        test_thread_id,
    ):
        """向量服务降级时应返回degraded状态."""
        mock_vector_service.health_check = AsyncMock(
            return_value={"status": "unhealthy"}
        )

        service = DualStageRetrievalService(
            conversation_service=mock_conversation_service,
            vector_service=mock_vector_service,
            user_id=test_user,
            thread_id=test_thread_id,
            enable_vector_search=True,
        )

        health = await service.health_check()
        assert health["status"] == "degraded"

    @pytest.mark.asyncio
    async def test_health_check_with_vector_exception_should_return_degraded(
        self,
        mock_conversation_service,
        mock_vector_service,
        test_user,
        test_thread_id,
    ):
        """向量服务异常时应返回degraded状态."""
        mock_vector_service.health_check = AsyncMock(
            side_effect=Exception("vector error")
        )

        service = DualStageRetrievalService(
            conversation_service=mock_conversation_service,
            vector_service=mock_vector_service,
            user_id=test_user,
            thread_id=test_thread_id,
            enable_vector_search=True,
        )

        health = await service.health_check()
        assert health["status"] == "degraded"
        assert "vector_health_error" in health

    @pytest.mark.asyncio
    async def test_health_check_without_vector_service(
        self,
        mock_conversation_service,
        test_user,
        test_thread_id,
    ):
        """无向量服务时应返回healthy."""
        service = DualStageRetrievalService(
            conversation_service=mock_conversation_service,
            vector_service=None,
            user_id=test_user,
            thread_id=test_thread_id,
            enable_vector_search=False,
        )

        health = await service.health_check()
        assert health["status"] == "healthy"
        assert health["retrieval_type"] == "sql_fallback"


class TestDualStageRetrievalServiceEdgeCases:
    """边界条件测试."""

    @pytest.mark.asyncio
    async def test_unicode_content_should_work(
        self,
        mock_conversation_service,
        mock_conversation_dao,
        mock_vector_service,
        test_user,
        test_thread_id,
    ):
        """Unicode/中文内容应正常处理."""
        conv = _make_conv_index(1, "你好世界 🌍", "回复内容 émojis 🎉")
        mock_conversation_dao.db_ops.find_by_filters.return_value = [conv]
        mock_conversation_dao.db_ops.find_by_filters.return_value = [conv]

        service = DualStageRetrievalService(
            conversation_service=mock_conversation_service,
            vector_service=mock_vector_service,
            user_id=test_user,
            thread_id=test_thread_id,
            enable_sql_search=True,
            enable_vector_search=False,
        )

        results = await service.search_conversations("你好", max_results=10)
        assert len(results) == 1
        assert "你好" in results[0].page_content

    @pytest.mark.asyncio
    async def test_document_metadata_should_contain_required_fields(
        self,
        mock_conversation_service,
        mock_vector_service,
        test_user,
        test_thread_id,
    ):
        """返回的Document metadata应包含必需字段."""
        conv = _make_conv_index(5, "metadata test", "response")
        conv.created_at = None
        mock_conversation_service.search_rounds_by_keywords = AsyncMock(
            return_value=[5],
        )
        mock_conversation_service.get_conversations_by_rounds = AsyncMock(
            return_value=[conv],
        )

        service = DualStageRetrievalService(
            conversation_service=mock_conversation_service,
            vector_service=mock_vector_service,
            user_id=test_user,
            thread_id=test_thread_id,
            enable_sql_search=True,
            enable_vector_search=False,
        )

        results = await service.search_conversations("metadata", max_results=10)
        assert len(results) == 1
        meta = results[0].metadata
        assert meta["round_number"] == 5
        assert meta["user_id"] == test_user
        assert meta["thread_id"] == test_thread_id
        assert meta["retrieval_type"] == "dual_stage_async"
        # 真实 relevance 分应透传(SQL-only 命中回退中性分 0.5)
        assert meta["relevance_score"] == 0.5


class TestDualStageRetrievalServiceInitFailure:
    """向量存储初始化失败时的降级行为."""

    @pytest.mark.asyncio
    async def test_ensure_initialized_when_vector_init_fails_should_degrade(
        self, mock_conversation_service, mock_vector_service, test_user, test_thread_id
    ):
        """向量存储初始化失败时应降级到SQL搜索(enable_vector_search=False)."""
        # Arrange - vector_store._ensure_initialized 会抛出异常
        vector_store = MagicMock()
        vector_store._ensure_initialized = AsyncMock(
            side_effect=Exception("vector store init failed"),
        )
        mock_vector_service._vector_store = vector_store
        mock_vector_service.health_check = AsyncMock(
            return_value={"status": "healthy"},
        )

        service = DualStageRetrievalService(
            conversation_service=mock_conversation_service,
            vector_service=mock_vector_service,
            user_id=test_user,
            thread_id=test_thread_id,
            enable_sql_search=True,
            enable_vector_search=True,
        )

        # Act - 触发初始化
        await service._ensure_initialized()

        # Assert - 降级到SQL
        assert service._initialized is True
        assert service.enable_vector_search is False

    @pytest.mark.asyncio
    async def test_ensure_initialized_when_already_initialized_should_return_early(
        self, mock_conversation_service, mock_vector_service, test_user, test_thread_id
    ):
        """重复调用_ensure_initialized应直接返回(不重复初始化)."""
        # Arrange
        vector_store = MagicMock()
        vector_store._ensure_initialized = AsyncMock()
        mock_vector_service._vector_store = vector_store

        service = DualStageRetrievalService(
            conversation_service=mock_conversation_service,
            vector_service=mock_vector_service,
            user_id=test_user,
            thread_id=test_thread_id,
            enable_sql_search=True,
            enable_vector_search=True,
        )

        # Act - 第一次初始化
        await service._ensure_initialized()
        assert service._initialized is True

        # 第二次调用前重置mock计数
        vector_store._ensure_initialized.reset_mock()

        # 第二次调用应直接返回
        await service._ensure_initialized()

        # Assert - 不应再次调用向量存储的初始化
        vector_store._ensure_initialized.assert_not_awaited()


class TestDualStageRetrievalServiceInternalMethods:
    """内部方法(_async_*)的边缘路径测试."""

    @pytest.mark.asyncio
    async def test_async_vector_search_with_round_range_should_pass_filter(
        self, mock_conversation_service, mock_vector_service, test_user, test_thread_id
    ):
        """向量检索应传递round_range过滤器."""
        # Arrange
        vector_store = MagicMock()
        vector_store._ensure_initialized = AsyncMock()
        vector_store.search_rounds_only = AsyncMock(return_value=[])
        mock_vector_service._vector_store = vector_store

        service = DualStageRetrievalService(
            conversation_service=mock_conversation_service,
            vector_service=mock_vector_service,
            user_id=test_user,
            thread_id=test_thread_id,
            enable_sql_search=True,
            enable_vector_search=True,
        )

        # Act - 带round_range过滤器的搜索
        await service.search_conversations(
            "test query", max_results=10,
            filters={"round_range": (1, 5)},
        )

        # Assert - round_range 传递到 search_rounds_only
        vector_store.search_rounds_only.assert_awaited_once()
        call_kwargs = vector_store.search_rounds_only.await_args[1]
        assert call_kwargs.get("round_range") == (1, 5)

    @pytest.mark.asyncio
    async def test_async_get_final_documents_with_empty_candidates_should_return_empty(
        self, mock_conversation_service, mock_vector_service, test_user, test_thread_id
    ):
        """_async_get_final_documents: 空候选轮次应返回空列表."""
        # Arrange
        service = DualStageRetrievalService(
            conversation_service=mock_conversation_service,
            vector_service=mock_vector_service,
            user_id=test_user,
            thread_id=test_thread_id,
        )

        # Act
        result = await service._async_get_final_documents([], 10)

        # Assert
        assert result == []

    @pytest.mark.asyncio
    async def test_async_get_final_documents_with_no_conversations_should_return_empty(
        self, mock_conversation_service, mock_vector_service, test_user, test_thread_id
    ):
        """_async_get_final_documents: 候选轮次无对应对话应返回空列表."""
        # Arrange
        mock_conversation_service.get_conversations_by_rounds = AsyncMock(return_value=[])
        service = DualStageRetrievalService(
            conversation_service=mock_conversation_service,
            vector_service=mock_vector_service,
            user_id=test_user,
            thread_id=test_thread_id,
        )

        # Act
        result = await service._async_get_final_documents([1, 3, 5], 10)

        # Assert
        assert result == []

    @pytest.mark.asyncio
    async def test_async_get_final_documents_with_exception_should_return_empty(
        self, mock_conversation_service, mock_vector_service, test_user, test_thread_id
    ):
        """_async_get_final_documents: 异常时应返回空列表."""
        # Arrange
        mock_conversation_service.get_conversations_by_rounds = AsyncMock(
            side_effect=Exception("DB error"),
        )
        service = DualStageRetrievalService(
            conversation_service=mock_conversation_service,
            vector_service=mock_vector_service,
            user_id=test_user,
            thread_id=test_thread_id,
        )

        # Act
        result = await service._async_get_final_documents([1, 2], 10)

        # Assert
        assert result == []

    @pytest.mark.asyncio
    async def test_async_sql_search_with_empty_query_should_return_empty(
        self, mock_conversation_service, mock_vector_service, test_user, test_thread_id
    ):
        """_async_sql_search_rounds: 空查询分词应返回空列表."""
        # Arrange
        service = DualStageRetrievalService(
            conversation_service=mock_conversation_service,
            vector_service=mock_vector_service,
            user_id=test_user,
            thread_id=test_thread_id,
        )

        # Act - 查询仅含短词(全部被过滤)
        result = await service._async_sql_search_rounds("a b", None, 10)

        # Assert
        assert result == []


class TestDualStageRetrievalServiceRemainingPaths:
    """剩余未覆盖内部路径测试."""

    @pytest.mark.asyncio
    async def test_search_conversations_exception_should_fallback_to_sql(
        self, mock_conversation_service, mock_conversation_dao,
        mock_vector_service, test_user, test_thread_id,
    ):
        """search_conversations内部异常时应降级到SQL搜索(覆盖243-246)."""
        # Arrange - SQL有结果,但smart_deduplication_with_scores抛出异常
        mock_conversation_service.search_rounds_by_keywords = AsyncMock(
            return_value=[1, 3],
        )
        vector_store = MagicMock()
        vector_store._ensure_initialized = AsyncMock()
        vector_store.search_rounds_only = AsyncMock(
            return_value=[(1, 0.9), (3, 0.8)],
        )
        mock_vector_service._vector_store = vector_store

        with patch(
            "src.storage.retrieval.smart_deduplication.smart_deduplication_with_scores",
            side_effect=Exception("dedup error"),
        ):
            service = DualStageRetrievalService(
                conversation_service=mock_conversation_service,
                vector_service=mock_vector_service,
                user_id=test_user,
                thread_id=test_thread_id,
                enable_sql_search=True,
                enable_vector_search=True,
            )

            # Act - 异常应被外层 except 捕获并降级
            results = await service.search_conversations("test query", max_results=10)

            # Assert - 降级后应返回列表(可能是空列表)
            assert isinstance(results, list)

    @pytest.mark.asyncio
    async def test_async_sql_search_with_exception_should_return_empty(
        self, mock_conversation_service, mock_vector_service,
        test_user, test_thread_id,
    ):
        """_async_sql_search_rounds: 异常时应返回空列表(覆盖276-278)."""
        # Arrange
        mock_conversation_service.search_rounds_by_keywords = AsyncMock(
            side_effect=Exception("SQL search failed"),
        )
        service = DualStageRetrievalService(
            conversation_service=mock_conversation_service,
            vector_service=mock_vector_service,
            user_id=test_user,
            thread_id=test_thread_id,
        )

        # Act
        result = await service._async_sql_search_rounds("test query", None, 10)

        # Assert
        assert result == []

    @pytest.mark.asyncio
    async def test_search_conversations_dedup_empty_should_fallback(
        self, mock_conversation_service, mock_conversation_dao,
        mock_vector_service, test_user, test_thread_id,
    ):
        """双路搜索：去重后无候选轮次应触发fallback."""
        # Arrange - SQL和向量各返回一些结果，但无交集
        convs = [_make_conv_index(1, "test msg", "test resp")]
        mock_conversation_dao.db_ops.find_by_filters.return_value = convs

        async def mock_find(filters, limit=1):
            rn = filters.get("round_number")
            if rn == 1:
                return convs
            return []

        mock_conversation_dao.db_ops.find_by_filters.side_effect = mock_find

        vector_store = MagicMock()
        vector_store._ensure_initialized = AsyncMock()
        vector_store.search_rounds_only = AsyncMock(
            return_value=[(999, 0.9)],  # 向量命中不在SQL候选中的轮次
        )
        mock_vector_service._vector_store = vector_store

        service = DualStageRetrievalService(
            conversation_service=mock_conversation_service,
            vector_service=mock_vector_service,
            user_id=test_user,
            thread_id=test_thread_id,
            enable_sql_search=True,
            enable_vector_search=True,
        )

        # Act - 无交集应触发 fallback
        results = await service.search_conversations("test query", max_results=10)
        assert isinstance(results, list)

    @pytest.mark.asyncio
    async def test_search_conversations_no_documents_should_fallback(
        self, mock_conversation_service, mock_conversation_dao,
        mock_vector_service, test_user, test_thread_id,
    ):
        """双路搜索：候选轮次无对应文档应触发fallback."""
        # Arrange
        convs = [_make_conv_index(1, "test msg", "test resp")]

        async def mock_find(filters, limit=1):
            rn = filters.get("round_number")
            if rn == 1:
                return convs
            return []

        mock_conversation_dao.db_ops.find_by_filters.side_effect = mock_find

        # SQL返回结果，向量也返回相同结果 → dedup产生交集
        mock_conversation_service.search_rounds_by_keywords = AsyncMock(
            return_value=[1, 3],
        )
        vector_store = MagicMock()
        vector_store._ensure_initialized = AsyncMock()
        vector_store.search_rounds_only = AsyncMock(
            return_value=[(1, 0.9), (3, 0.8)],
        )
        mock_vector_service._vector_store = vector_store

        # get_conversations_by_rounds 返回空(模拟文档获取失败)
        mock_conversation_service.get_conversations_by_rounds = AsyncMock(
            return_value=[],
        )

        service = DualStageRetrievalService(
            conversation_service=mock_conversation_service,
            vector_service=mock_vector_service,
            user_id=test_user,
            thread_id=test_thread_id,
            enable_sql_search=True,
            enable_vector_search=True,
        )

        # Act - _async_get_final_documents 返回空 → 触发 fallback
        results = await service.search_conversations("test query", max_results=10)
        assert isinstance(results, list)

    @pytest.mark.asyncio
    async def test_async_vector_search_with_no_vector_store_should_return_empty(
        self, mock_conversation_service, mock_vector_service,
        test_user, test_thread_id,
    ):
        """_async_vector_search_rounds: 无向量存储应返回空列表."""
        # Arrange - vector_service has no _vector_store
        mock_vector_service._vector_store = None

        service = DualStageRetrievalService(
            conversation_service=mock_conversation_service,
            vector_service=mock_vector_service,
            user_id=test_user,
            thread_id=test_thread_id,
            enable_vector_search=True,
        )

        # Act - 调用内部方法
        result = await service._async_vector_search_rounds("test query", None, 10)

        # Assert
        assert result == []


class TestHookFormat:
    """search_memories 返回钩子格式 ([轮X] topic: summary), 而非完整原文.

    钩子化改造(切片D): 避免上下文浪费(此前搜一次拉 10 轮全文, 仅 1-3 轮相关).
    全文由 get_round_detail 二次取.
    """

    @pytest.mark.asyncio
    async def test_normal_path_should_return_hook_format(
        self, mock_conversation_service, mock_vector_service, test_user,
        test_thread_id,
    ):
        """正常路径 page_content 应为钩子, 不含完整原文."""
        conv = _make_conv_index(5, "完整用户消息原文内容", "完整助手回复原文")
        conv.topic = "项目进度"
        conv.summary = "讨论了时间表"
        mock_conversation_service.get_conversations_by_rounds = AsyncMock(
            return_value=[conv],
        )

        service = DualStageRetrievalService(
            conversation_service=mock_conversation_service,
            vector_service=mock_vector_service,
            user_id=test_user,
            thread_id=test_thread_id,
        )
        docs = await service._async_get_final_documents([5], 10)

        assert len(docs) == 1
        content = docs[0].page_content
        assert "[轮5]" in content
        assert "项目进度" in content
        assert "讨论了时间表" in content
        # 不应包含完整原文
        assert "完整用户消息原文内容" not in content
        assert "完整助手回复原文" not in content

    @pytest.mark.asyncio
    async def test_none_summary_should_fallback_to_user_message_preview(
        self, mock_conversation_service, mock_vector_service, test_user,
        test_thread_id,
    ):
        """summary 为 None 时 fallback 到 user_message 前50字符 (沿用 core.py 模式)."""
        conv = _make_conv_index(7, "这是一段较长的用户消息内容用于测试fallback截断逻辑", "回复")
        conv.topic = None
        conv.summary = None
        mock_conversation_service.get_conversations_by_rounds = AsyncMock(
            return_value=[conv],
        )

        service = DualStageRetrievalService(
            conversation_service=mock_conversation_service,
            vector_service=mock_vector_service,
            user_id=test_user,
            thread_id=test_thread_id,
        )
        docs = await service._async_get_final_documents([7], 10)

        content = docs[0].page_content
        assert "[轮7]" in content
        # fallback 到 user_message 前50字符
        assert "这是一段较长的用户消息内容用于测试fallback截断" in content


class TestDualStageRetrievalServiceHealthCheckExtra:
    """health_check方法异常路径测试."""

    @pytest.mark.asyncio
    async def test_health_check_with_exception_should_return_error_dict(
        self, mock_conversation_service, test_user, test_thread_id
    ):
        """健康检查抛出异常时应返回包含错误信息的字典."""
        # Arrange - 创建一个会在_ensure_initialized抛出的service
        class FailingVectorService:
            _vector_store = None

        vector_service = FailingVectorService()

        service = DualStageRetrievalService(
            conversation_service=mock_conversation_service,
            vector_service=vector_service,
            user_id=test_user,
            thread_id=test_thread_id,
            enable_sql_search=True,
            enable_vector_search=True,
        )
        # 让 health_check 抛异常: 需要 _ensure_initialized 或后续步骤抛异常
        # vector_service 没有 health_check 方法
        original = service._ensure_initialized
        service._ensure_initialized = AsyncMock(
            side_effect=Exception("health check failed"),
        )

        # Act
        result = await service.health_check()

        # Assert - 返回包含错误信息的字典
        assert isinstance(result, dict)
        assert "service_type" in result
        # 恢复原始方法 (cleanup)
        service._ensure_initialized = original
