"""price_alert_service 单元测试.

覆盖纯函数 (is_triggered / format_alert / rule_to_delivery / _dedupe_codes /
get_quote_service_url) 与 PriceAlertEngine.tick 一次性评估编排 (mock 取价/派发/DAO).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.storage.models.price_alert import AlertStatus, PriceAlertRule
from src.storage.service import price_alert_service as mod
from src.storage.service.price_alert_service import (
    PriceAlertEngine,
    _dedupe_codes,
    format_alert,
    get_quote_service_url,
    is_triggered,
    rule_to_delivery,
)

# ───────────────────── 辅助构造 ─────────────────────


def _rule(**overrides) -> PriceAlertRule:
    """构造一条 active 规则 (默认 above 100, wechat 投递完整)."""
    defaults = {
        "market": 1,
        "stock_code": "600519",
        "stock_name": "茅台",
        "direction": "above",
        "threshold_price": 100.0,
        "delivery_method": "wechat",
        "openclaw_channel": "openclaw-weixin",
        "account_id": "bot-1",
        "target": "user-1",
        "user_id": "u",
        "thread_id": "t",
        "agent_id": "a",
    }
    defaults.update(overrides)
    return PriceAlertRule(**defaults)


# ───────────────────── 纯函数 ─────────────────────


class TestIsTriggered:
    def test_above_above_threshold(self):
        assert is_triggered("above", 100, 101) is True

    def test_above_at_threshold_not_triggered(self):
        # 严格大于, 等于不触发
        assert is_triggered("above", 100, 100) is False

    def test_above_below_threshold(self):
        assert is_triggered("above", 100, 99) is False

    def test_below_below_threshold(self):
        assert is_triggered("below", 100, 99) is True

    def test_below_at_threshold_not_triggered(self):
        assert is_triggered("below", 100, 100) is False

    def test_invalid_direction(self):
        assert is_triggered("sideways", 100, 100) is False


class TestFormatAlert:
    def test_with_name_above(self):
        assert (
            format_alert("茅台", "600519", "above", 1500, 1501)
            == "📊 茅台(600519) 当前价 1501.00 已向上突破 1500.00"
        )

    def test_without_name_below(self):
        assert (
            format_alert("", "000001", "below", 10.5, 10.4)
            == "📊 000001 当前价 10.40 已向下突破 10.50"
        )


class TestRuleToDelivery:
    def test_wechat_complete(self):
        d = rule_to_delivery(_rule())
        assert d is not None
        assert d.method == "wechat"
        assert d.openclaw_channel == "openclaw-weixin"
        assert d.account_id == "bot-1"
        assert d.target == "user-1"

    def test_wechat_missing_account_returns_none(self):
        assert rule_to_delivery(_rule(account_id="")) is None

    def test_email_complete(self):
        d = rule_to_delivery(
            _rule(delivery_method="email", email_address="x@y.com")
        )
        assert d is not None
        assert d.method == "email"
        assert d.email_address == "x@y.com"

    def test_email_missing_address_returns_none(self):
        assert rule_to_delivery(
            _rule(delivery_method="email", email_address="")
        ) is None


class TestDedupeCodes:
    def test_dedupes_same_code(self):
        rules = [
            _rule(rule_id="pa_1", stock_code="600519"),
            _rule(rule_id="pa_2", stock_code="600519"),
            _rule(rule_id="pa_3", market=0, stock_code="000001"),
        ]
        codes = _dedupe_codes(rules)
        assert sorted(codes) == [(0, "000001"), (1, "600519")]


class TestGetQuoteServiceUrl:
    def test_env_priority(self, monkeypatch):
        monkeypatch.setenv("QUOTE_SERVICE_BASE_URL", "http://env:8767")
        assert get_quote_service_url() == "http://env:8767"

    def test_env_strips_trailing_slash(self, monkeypatch):
        monkeypatch.setenv("QUOTE_SERVICE_BASE_URL", "http://env:8767/")
        assert get_quote_service_url() == "http://env:8767"

    def test_default_when_no_env_no_config(self, monkeypatch):
        monkeypatch.delenv("QUOTE_SERVICE_BASE_URL", raising=False)
        with patch(
            "src.config.tools_config.get_config",
            side_effect=Exception("no config"),
        ):
            assert get_quote_service_url() == "http://127.0.0.1:8767"


# ───────────────────── Engine.tick 编排 ─────────────────────


def _engine() -> PriceAlertEngine:
    return PriceAlertEngine()


@pytest.fixture(autouse=True)
def _trading_hours(monkeypatch):
    """默认交易时段, 单测可局部覆盖."""
    monkeypatch.setattr(mod, "is_trading_hours", lambda: True)


class TestEngineTick:
    @pytest.mark.asyncio
    async def test_skips_non_trading(self, monkeypatch):
        monkeypatch.setattr(mod, "is_trading_hours", lambda: False)
        e = _engine()
        e._collect_active_rules = AsyncMock(return_value=[])  # 不应被调用
        await e.tick()
        e._collect_active_rules.assert_not_called()
        assert e.stats.last_tick_trading is False

    @pytest.mark.asyncio
    async def test_no_rules_no_fetch(self):
        e = _engine()
        e._collect_active_rules = AsyncMock(return_value=[])
        e._fetch_quotes = AsyncMock()
        await e.tick()
        e._fetch_quotes.assert_not_awaited()
        assert e.stats.last_tick_rules == 0

    @pytest.mark.asyncio
    async def test_triggered_dispatches_and_disables(self):
        e = _engine()
        rule = _rule(direction="above", threshold_price=100.0)
        owner = ("u", "t", "a")
        e._collect_active_rules = AsyncMock(return_value=[(owner, rule)])
        e._fetch_quotes = AsyncMock(return_value={(1, "600519"): 101.0})
        e._dispatch = AsyncMock(return_value=True)
        mock_dao = MagicMock()
        mock_dao.disable = AsyncMock(return_value=True)
        e._get_dao = AsyncMock(return_value=mock_dao)

        await e.tick()

        e._dispatch.assert_awaited_once()
        mock_dao.disable.assert_awaited_once_with(
            rule.rule_id, owner, triggered=True
        )
        assert e.stats.last_tick_triggered == 1
        assert e.stats.total_triggered == 1

    @pytest.mark.asyncio
    async def test_not_triggered_no_dispatch(self):
        e = _engine()
        rule = _rule(direction="above", threshold_price=100.0)
        owner = ("u", "t", "a")
        e._collect_active_rules = AsyncMock(return_value=[(owner, rule)])
        e._fetch_quotes = AsyncMock(return_value={(1, "600519"): 99.0})  # 未触发
        e._dispatch = AsyncMock()
        mock_dao = MagicMock()
        mock_dao.disable = AsyncMock()
        e._get_dao = AsyncMock(return_value=mock_dao)

        await e.tick()

        e._dispatch.assert_not_awaited()
        mock_dao.disable.assert_not_awaited()
        assert e.stats.last_tick_triggered == 0

    @pytest.mark.asyncio
    async def test_dispatch_failure_keeps_active_for_retry(self):
        """派发失败不 disable, 保持 active 等下轮重试."""
        e = _engine()
        rule = _rule(direction="above", threshold_price=100.0)
        owner = ("u", "t", "a")
        e._collect_active_rules = AsyncMock(return_value=[(owner, rule)])
        e._fetch_quotes = AsyncMock(return_value={(1, "600519"): 101.0})
        e._dispatch = AsyncMock(return_value=False)
        mock_dao = MagicMock()
        mock_dao.disable = AsyncMock()
        e._get_dao = AsyncMock(return_value=mock_dao)

        await e.tick()

        mock_dao.disable.assert_not_awaited()
        assert e.stats.last_tick_triggered == 0

    @pytest.mark.asyncio
    async def test_halted_price_skipped(self):
        """停牌(price<=0)跳过."""
        e = _engine()
        rule = _rule(direction="above", threshold_price=100.0)
        owner = ("u", "t", "a")
        e._collect_active_rules = AsyncMock(return_value=[(owner, rule)])
        e._fetch_quotes = AsyncMock(return_value={(1, "600519"): 0})  # 停牌
        e._dispatch = AsyncMock()
        await e.tick()
        e._dispatch.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_missing_quote_skipped(self):
        """取价缺失该股跳过."""
        e = _engine()
        rule = _rule(direction="above", threshold_price=100.0)
        owner = ("u", "t", "a")
        e._collect_active_rules = AsyncMock(return_value=[(owner, rule)])
        e._fetch_quotes = AsyncMock(return_value={})  # 空
        e._dispatch = AsyncMock()
        await e.tick()
        e._dispatch.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_fetch_failure_skips_evaluation(self):
        e = _engine()
        e._collect_active_rules = AsyncMock(return_value=[(("u", "t", "a"), _rule())])
        e._fetch_quotes = AsyncMock(return_value=None)  # 取价失败
        e._dispatch = AsyncMock()
        await e.tick()
        e._dispatch.assert_not_awaited()


class TestEngineCrud:
    @pytest.mark.asyncio
    async def test_create_rule_injects_owner(self):
        e = _engine()
        mock_dao = MagicMock()
        created = _rule()
        mock_dao.create = AsyncMock(return_value=created)
        e._get_dao = AsyncMock(return_value=mock_dao)

        result = await e.create_rule(("u", "t", "a"), market=1, stock_code="600519")

        assert result is created
        # owner 注入到 fields
        call_kwargs = mock_dao.create.call_args.kwargs
        assert call_kwargs["user_id"] == "u"
        assert call_kwargs["thread_id"] == "t"
        assert call_kwargs["agent_id"] == "a"

    @pytest.mark.asyncio
    async def test_list_active_delegates(self):
        e = _engine()
        mock_dao = MagicMock()
        mock_dao.list_active_by_owner = AsyncMock(return_value=[])
        e._get_dao = AsyncMock(return_value=mock_dao)

        await e.list_active(("u", "t", "a"))

        mock_dao.list_active_by_owner.assert_awaited_once_with("u", "t", "a")

    @pytest.mark.asyncio
    async def test_disable_rule_delegates(self):
        e = _engine()
        mock_dao = MagicMock()
        mock_dao.disable = AsyncMock(return_value=True)
        e._get_dao = AsyncMock(return_value=mock_dao)

        ok = await e.disable_rule("pa_xxx", ("u", "t", "a"))

        assert ok is True
        mock_dao.disable.assert_awaited_once_with(
            "pa_xxx", ("u", "t", "a"), triggered=False
        )


class TestModelDefaults:
    def test_new_rule_is_active(self):
        r = _rule()
        assert r.status == AlertStatus.ACTIVE

    def test_new_rule_has_triggered_at_none(self):
        r = _rule()
        assert r.triggered_at is None
