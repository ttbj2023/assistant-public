"""DomainDataDispatcher 单元测试.

测试领域数据调度器的核心行为:
- fire-and-forget 调度
- attachment 缓存 (流式跨 hook)
- 失败隔离
- 事件循环缺失时的容错
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from src.agent.domain_data.base_domain_data import BaseDomainData
from src.agent.domain_data.domain_data_dispatcher import DomainDataDispatcher


class _FakeDomainData(BaseDomainData):
    """测试用的 fake domain_data."""

    def __init__(
        self,
        user_id: str,
        thread_id: str,
        agent_id: str,
        agent_config: Any = None,
    ) -> None:
        super().__init__(user_id, thread_id, agent_id, agent_config)
        self.calls: list[tuple] = []
        self.raise_on_round = False

    async def on_conversation_round(
        self,
        conversation_data: Any | None,
        attachment_infos: list[Any] | None = None,
        round_number: int | None = None,
    ) -> None:
        if self.raise_on_round:
            raise RuntimeError("fake error")
        self.calls.append((conversation_data, attachment_infos, round_number))


@pytest.fixture
def fake_domain_data_class():
    """返回一个每次创建新实例的 fake class."""
    instances: list[_FakeDomainData] = []

    def _factory(user_id, thread_id, agent_id, agent_config=None):
        instance = _FakeDomainData(user_id, thread_id, agent_id, agent_config)
        instances.append(instance)
        return instance

    _factory.instances = instances
    return _factory


class TestDispatcherInit:
    """测试调度器初始化."""

    def test_has_domain_data_false_when_empty(self):
        """空名称列表时 has_domain_data 为 False."""
        dispatcher = DomainDataDispatcher([], "agent-1")
        assert not dispatcher.has_domain_data

    def test_has_domain_data_true_when_loaded(self, fake_domain_data_class):
        """成功加载类时 has_domain_data 为 True."""
        with patch(
            "src.agent.domain_data.domain_data_dispatcher.get_domain_data_class",
            return_value=fake_domain_data_class,
        ):
            dispatcher = DomainDataDispatcher(["fake"], "agent-1")
        assert dispatcher.has_domain_data

    def test_unknown_name_skipped_with_warning(self, caplog):
        """未注册名称应跳过并记录警告."""
        with patch(
            "src.agent.domain_data.domain_data_dispatcher.get_domain_data_class",
            side_effect=KeyError("not found"),
        ):
            dispatcher = DomainDataDispatcher(["unknown"], "agent-1")
        assert not dispatcher.has_domain_data
        assert any("加载领域数据" in r.message for r in caplog.records)


class TestDispatch:
    """测试 fire-and-forget 调度."""

    @pytest.mark.asyncio
    async def test_dispatch_creates_instance_and_calls_on_conversation_round(
        self, fake_domain_data_class
    ):
        """dispatch 应创建实例并调用 on_conversation_round."""
        conv = MagicMock()
        conv.user_message = "msg"

        with patch(
            "src.agent.domain_data.domain_data_dispatcher.get_domain_data_class",
            return_value=fake_domain_data_class,
        ):
            dispatcher = DomainDataDispatcher(["fake"], "agent-1")
            dispatcher.dispatch(
                conversation_data=conv,
                user_id="u",
                thread_id="t",
                attachment_infos=None,
                round_number=5,
            )
            await dispatcher.drain()

        assert len(fake_domain_data_class.instances) == 1
        instance = fake_domain_data_class.instances[0]
        assert instance.calls == [(conv, None, 5)]

    @pytest.mark.asyncio
    async def test_dispatch_skips_when_no_classes(self):
        """无 domain_data 时 dispatch 为空操作."""
        dispatcher = DomainDataDispatcher([], "agent-1")
        dispatcher.dispatch(
            conversation_data=MagicMock(),
            user_id="u",
            thread_id="t",
            attachment_infos=None,
            round_number=1,
        )
        # 无异常即通过

    @pytest.mark.asyncio
    async def test_dispatch_uses_cached_attachments_when_none_passed(
        self, fake_domain_data_class
    ):
        """流式场景: attachment_infos=None 时使用缓存的 attachments."""
        cached = [{"id": "img1"}]

        with patch(
            "src.agent.domain_data.domain_data_dispatcher.get_domain_data_class",
            return_value=fake_domain_data_class,
        ):
            dispatcher = DomainDataDispatcher(["fake"], "agent-1")
            dispatcher.cache_attachments(cached)
            dispatcher.dispatch(
                conversation_data=MagicMock(),
                user_id="u",
                thread_id="t",
                attachment_infos=None,
                round_number=1,
            )
            await dispatcher.drain()

        instance = fake_domain_data_class.instances[0]
        assert instance.calls[0][1] == cached

    @pytest.mark.asyncio
    async def test_dispatch_prefers_direct_attachments_over_cached(
        self, fake_domain_data_class
    ):
        """直接传入的 attachment_infos 优先于缓存."""
        direct = [{"id": "direct"}]
        cached = [{"id": "cached"}]

        with patch(
            "src.agent.domain_data.domain_data_dispatcher.get_domain_data_class",
            return_value=fake_domain_data_class,
        ):
            dispatcher = DomainDataDispatcher(["fake"], "agent-1")
            dispatcher.cache_attachments(cached)
            dispatcher.dispatch(
                conversation_data=MagicMock(),
                user_id="u",
                thread_id="t",
                attachment_infos=direct,
                round_number=1,
            )
            await dispatcher.drain()

        instance = fake_domain_data_class.instances[0]
        assert instance.calls[0][1] == direct

    @pytest.mark.asyncio
    async def test_dispatch_failure_isolation(self):
        """一个 domain_data 异常不影响其他."""
        instances: list[_FakeDomainData] = []

        def _good_factory(u, t, a, cfg=None):
            instance = _FakeDomainData(u, t, a, cfg)
            instances.append(instance)
            return instance

        def _bad_factory(u, t, a, cfg=None):
            instance = _FakeDomainData(u, t, a, cfg)
            instance.raise_on_round = True
            instances.append(instance)
            return instance

        class_map = {"good": _good_factory, "bad": _bad_factory}

        def _get_cls(name):
            return class_map[name]

        with patch(
            "src.agent.domain_data.domain_data_dispatcher.get_domain_data_class",
            side_effect=_get_cls,
        ):
            dispatcher = DomainDataDispatcher(["bad", "good"], "agent-1")
            dispatcher.dispatch(
                conversation_data=MagicMock(),
                user_id="u",
                thread_id="t",
                attachment_infos=None,
                round_number=1,
            )
            await dispatcher.drain()

        assert len(instances) == 2
        good_instance = next(i for i in instances if not i.raise_on_round)
        assert len(good_instance.calls) == 1


class TestAttachmentCache:
    """测试 attachment 缓存."""

    def test_cache_and_clear(self):
        """缓存后可清除."""
        dispatcher = DomainDataDispatcher([], "agent-1")
        attachments = [{"id": 1}]

        dispatcher.cache_attachments(attachments)
        assert dispatcher._pending_attachments == attachments

        dispatcher.clear_cached_attachments()
        assert dispatcher._pending_attachments is None
