"""价格监控子工具单元测试.

覆盖 create/list/cancel/query 四子工具. Mock: resolve_delivery (渠道解析) /
get_price_alert_engine (引擎 CRUD) / query 工具的 HTTP _request.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.notification import DeliverySpec
from src.storage.models.price_alert import PriceAlertRule
from src.tools.internal.cancel_price_alert_tool import CancelPriceAlertTool
from src.tools.internal.create_price_alert_tool import (
    CreatePriceAlertTool,
    infer_market,
)
from src.tools.internal.list_price_alerts_tool import ListPriceAlertsTool
from src.tools.internal.query_stock_price_tool import (
    QueryStockPriceTool,
    format_quote_text,
)

DELIVERY = DeliverySpec(
    method="wechat",
    openclaw_channel="openclaw-weixin",
    account_id="acc1",
    target="tgt1",
)


def _mock_response(payload):
    resp = MagicMock()
    resp.json.return_value = payload
    resp.raise_for_status.return_value = None
    return resp


def _rule(**overrides) -> PriceAlertRule:
    defaults = {
        "market": 1,
        "stock_code": "600519",
        "stock_name": "",
        "direction": "above",
        "threshold_price": 100.0,
        "delivery_method": "wechat",
        "openclaw_channel": "openclaw-weixin",
        "account_id": "acc1",
        "target": "tgt1",
        "user_id": "u1",
        "thread_id": "t1",
        "agent_id": "a1",
    }
    defaults.update(overrides)
    return PriceAlertRule(**defaults)


@pytest.fixture
def create_tool():
    return CreatePriceAlertTool(user_id="u1", thread_id="t1", agent_id="a1")


@pytest.fixture
def list_tool():
    return ListPriceAlertsTool(user_id="u1", thread_id="t1", agent_id="a1")


@pytest.fixture
def cancel_tool():
    return CancelPriceAlertTool(user_id="u1", thread_id="t1", agent_id="a1")


# ========== infer_market ==========


class TestInferMarket:
    def test_sh(self):
        assert infer_market("600519") == 1
        assert infer_market("688981") == 1

    def test_sz(self):
        assert infer_market("000001") == 0
        assert infer_market("300750") == 0

    def test_bj_unsupported(self):
        with pytest.raises(ValueError, match="北交所"):
            infer_market("830879")


# ========== CreatePriceAlertTool ==========


class TestCreatePriceAlert:
    @pytest.mark.asyncio
    async def test_create_above(self, create_tool):
        mock_engine = MagicMock()
        mock_engine.create_rule = AsyncMock(
            return_value=_rule(
                rule_id="pa_abc123", stock_name="贵州茅台", threshold_price=1300.0
            )
        )
        with (
            patch(
                "src.tools.internal.create_price_alert_tool.resolve_delivery",
                new=AsyncMock(return_value=DELIVERY),
            ),
            patch(
                "src.tools.internal.create_price_alert_tool.get_price_alert_engine",
                return_value=mock_engine,
            ),
        ):
            result = await create_tool._arun(
                stock_code="600519",
                direction="above",
                threshold_price=1300.0,
                stock_name="贵州茅台",
            )
        assert "已创建" in result
        assert "pa_abc123" in result
        assert "涨到 1300" in result
        # create_rule(owner, **fields): owner 位置, fields 关键字
        kwargs = mock_engine.create_rule.call_args.kwargs
        assert kwargs["market"] == 1
        assert kwargs["openclaw_channel"] == "openclaw-weixin"
        assert kwargs["account_id"] == "acc1"
        assert kwargs["target"] == "tgt1"

    @pytest.mark.asyncio
    async def test_create_below_sz(self, create_tool):
        mock_engine = MagicMock()
        mock_engine.create_rule = AsyncMock(return_value=_rule(rule_id="pa_x"))
        with (
            patch(
                "src.tools.internal.create_price_alert_tool.resolve_delivery",
                new=AsyncMock(return_value=DELIVERY),
            ),
            patch(
                "src.tools.internal.create_price_alert_tool.get_price_alert_engine",
                return_value=mock_engine,
            ),
        ):
            result = await create_tool._arun(
                stock_code="000001", direction="below", threshold_price=10.0
            )
        assert "跌破 10" in result
        kwargs = mock_engine.create_rule.call_args.kwargs
        assert kwargs["market"] == 0
        assert kwargs["stock_name"] == ""

    @pytest.mark.asyncio
    async def test_no_wechat_delivery(self, create_tool):
        with patch(
            "src.tools.internal.create_price_alert_tool.resolve_delivery",
            new=AsyncMock(return_value=None),
        ):
            result = await create_tool._arun(
                stock_code="600519", direction="above", threshold_price=1300.0
            )
        assert "未检测到可用的微信接收渠道" in result

    @pytest.mark.asyncio
    async def test_invalid_direction(self, create_tool):
        result = await create_tool._arun(
            stock_code="600519", direction="sideways", threshold_price=1300.0
        )
        assert "操作失败" in result
        assert "direction" in result

    @pytest.mark.asyncio
    async def test_unsupported_market(self, create_tool):
        result = await create_tool._arun(
            stock_code="830879", direction="above", threshold_price=5.0
        )
        assert "北交所" in result

    @pytest.mark.asyncio
    async def test_engine_failure(self, create_tool):
        mock_engine = MagicMock()
        mock_engine.create_rule = AsyncMock(side_effect=RuntimeError("db down"))
        with (
            patch(
                "src.tools.internal.create_price_alert_tool.resolve_delivery",
                new=AsyncMock(return_value=DELIVERY),
            ),
            patch(
                "src.tools.internal.create_price_alert_tool.get_price_alert_engine",
                return_value=mock_engine,
            ),
        ):
            result = await create_tool._arun(
                stock_code="600519", direction="above", threshold_price=1300.0
            )
        assert "创建价格监控失败" in result

    @pytest.mark.asyncio
    async def test_create_email(self, create_tool):
        mock_engine = MagicMock()
        mock_engine.create_rule = AsyncMock(return_value=_rule(rule_id="pa_eml"))
        with patch(
            "src.tools.internal.create_price_alert_tool.get_price_alert_engine",
            return_value=mock_engine,
        ):
            result = await create_tool._arun(
                stock_code="600519",
                direction="below",
                threshold_price=1500.0,
                stock_name="贵州茅台",
                delivery_method="email",
                email_address="dev@test.com",
            )
        assert "已创建" in result
        assert "邮件(dev@test.com)" in result
        kwargs = mock_engine.create_rule.call_args.kwargs
        assert kwargs["delivery_method"] == "email"
        assert kwargs["email_address"] == "dev@test.com"
        assert "account_id" not in kwargs

    @pytest.mark.asyncio
    async def test_create_email_without_address(self, create_tool):
        result = await create_tool._arun(
            stock_code="600519",
            direction="below",
            threshold_price=1500.0,
            delivery_method="email",
        )
        assert "操作失败" in result


# ========== ListPriceAlertsTool ==========


class TestListPriceAlerts:
    @pytest.mark.asyncio
    async def test_list_empty(self, list_tool):
        mock_engine = MagicMock()
        mock_engine.list_active = AsyncMock(return_value=[])
        with patch(
            "src.tools.internal.list_price_alerts_tool.get_price_alert_engine",
            return_value=mock_engine,
        ):
            result = await list_tool._arun()
        assert "没有任何活跃" in result

    @pytest.mark.asyncio
    async def test_list_with_rules(self, list_tool):
        mock_engine = MagicMock()
        mock_engine.list_active = AsyncMock(
            return_value=[
                _rule(
                    rule_id="pa_1",
                    stock_name="贵州茅台",
                    direction="below",
                    threshold_price=1500.0,
                ),
                _rule(
                    rule_id="pa_2",
                    market=0,
                    stock_code="000001",
                    stock_name="",
                    direction="above",
                    threshold_price=11.0,
                ),
            ]
        )
        with patch(
            "src.tools.internal.list_price_alerts_tool.get_price_alert_engine",
            return_value=mock_engine,
        ):
            result = await list_tool._arun()
        assert "共 2 条" in result
        assert "贵州茅台(600519)" in result
        assert "跌破 1500" in result
        assert "000001" in result
        assert "涨到 11" in result

    @pytest.mark.asyncio
    async def test_list_engine_failure(self, list_tool):
        mock_engine = MagicMock()
        mock_engine.list_active = AsyncMock(side_effect=RuntimeError("db down"))
        with patch(
            "src.tools.internal.list_price_alerts_tool.get_price_alert_engine",
            return_value=mock_engine,
        ):
            result = await list_tool._arun()
        assert "查询价格监控失败" in result


# ========== CancelPriceAlertTool ==========


class TestCancelPriceAlert:
    @pytest.mark.asyncio
    async def test_cancel_success(self, cancel_tool):
        mock_engine = MagicMock()
        mock_engine.disable_rule = AsyncMock(return_value=True)
        with patch(
            "src.tools.internal.cancel_price_alert_tool.get_price_alert_engine",
            return_value=mock_engine,
        ):
            result = await cancel_tool._arun(rule_id="pa_1")
        assert "已取消" in result
        assert "pa_1" in result

    @pytest.mark.asyncio
    async def test_cancel_missing_rule_id(self, cancel_tool):
        result = await cancel_tool._arun()
        assert "操作失败" in result
        assert "rule_id" in result

    @pytest.mark.asyncio
    async def test_cancel_not_found(self, cancel_tool):
        mock_engine = MagicMock()
        mock_engine.disable_rule = AsyncMock(return_value=False)
        with patch(
            "src.tools.internal.cancel_price_alert_tool.get_price_alert_engine",
            return_value=mock_engine,
        ):
            result = await cancel_tool._arun(rule_id="pa_x")
        assert "不存在或已结束" in result


# ========== is_available ==========


class TestIsAvailable:
    @pytest.mark.asyncio
    async def test_available_with_wechat(self):
        t = CreatePriceAlertTool(user_id="u1", thread_id="t1", agent_id="a1")
        with patch(
            "src.tools.internal.create_price_alert_tool.resolve_delivery",
            new=AsyncMock(return_value=DELIVERY),
        ):
            assert await t.is_available() is True

    @pytest.mark.asyncio
    async def test_unavailable_without_any_channel(self):
        t = CreatePriceAlertTool(user_id="u1", thread_id="t1", agent_id="a1")
        with (
            patch(
                "src.tools.internal.create_price_alert_tool.resolve_delivery",
                new=AsyncMock(return_value=None),
            ),
            patch("src.config.smtp_config.is_configured", return_value=False),
        ):
            assert await t.is_available() is False

    @pytest.mark.asyncio
    async def test_available_with_email_only(self):
        """无微信但 SMTP 已配置也可用."""
        t = CreatePriceAlertTool(user_id="u1", thread_id="t1", agent_id="a1")
        with (
            patch(
                "src.tools.internal.create_price_alert_tool.resolve_delivery",
                new=AsyncMock(return_value=None),
            ),
            patch("src.config.smtp_config.is_configured", return_value=True),
        ):
            assert await t.is_available() is True


# ========== QueryStockPriceTool: format_quote_text 纯函数 ==========


def _quote_ok(**overrides):
    base = {
        "market": 1,
        "code": "600519",
        "status": "ok",
        "is_trading": True,
        "price": 1685.20,
        "last_close": 1664.80,
        "open": 1670.0,
        "high": 1690.0,
        "low": 1668.0,
        "vol": 23000.0,
        "amount": 3.86e9,
        "bid_prices": [1685.0, 1684.99, 1684.98, None, None],
        "ask_prices": [1685.20, 1685.50, 1686.0, None, None],
        "bid_vols": [5, 10, 3, None, None],
        "ask_vols": [2, 8, 15, None, None],
    }
    base.update(overrides)
    return base


class TestFormatQuoteText:
    def test_normal_trading(self):
        text = format_quote_text(_quote_ok(), "600519")
        assert "📈 600519" in text
        assert "现价 1685.20" in text
        assert "涨跌幅 +1.23%" in text
        assert "今开 1670.00" in text
        assert "最高 1690.00" in text
        assert "昨收 1664.80" in text
        assert "成交量 2.30万手" in text
        assert "成交额 38.60亿" in text
        assert "买盘 1685.00/1684.99/1684.98" in text
        assert "卖盘 1685.20/1685.50/1686.00" in text
        assert "已收盘" not in text

    def test_after_hours_marker(self):
        text = format_quote_text(_quote_ok(is_trading=False), "600519")
        assert "已收盘" in text

    def test_no_last_close_omits_change(self):
        text = format_quote_text(_quote_ok(last_close=None), "600519")
        assert "现价 1685.20" in text
        assert "涨跌幅" not in text

    def test_price_none(self):
        text = format_quote_text(_quote_ok(price=None), "600519")
        assert "现价 未知" in text
        assert "已收盘" not in text

    def test_halted(self):
        text = format_quote_text({"status": "halted"}, "600519")
        assert "停牌" in text

    def test_not_found(self):
        text = format_quote_text({"status": "not_found"}, "600519")
        assert "未取到" in text

    def test_small_volume_amount(self):
        text = format_quote_text(_quote_ok(vol=500.0, amount=5000.0), "600519")
        assert "成交量 500手" in text
        assert "成交额 5000" in text


# ========== QueryStockPriceTool: _arun / is_available ==========


@pytest.fixture
def query_tool():
    t = QueryStockPriceTool(user_id="u1", thread_id="t1", agent_id="a1")
    t._request = AsyncMock()
    return t


class TestQueryStockPrice:
    @pytest.mark.asyncio
    async def test_query_sh_market_inference(self, query_tool):
        query_tool._request.return_value = _mock_response(_quote_ok())
        result = await query_tool._arun(stock_code="600519")
        assert "现价 1685.20" in result
        call = query_tool._request.call_args
        assert call.args[0] == "GET"
        assert call.args[1] == "/quote"
        assert call.kwargs["params"] == {"market": 1, "code": "600519"}

    @pytest.mark.asyncio
    async def test_query_sz_market_inference(self, query_tool):
        query_tool._request.return_value = _mock_response(
            _quote_ok(market=0, code="000001")
        )
        await query_tool._arun(stock_code="000001")
        assert query_tool._request.call_args.kwargs["params"]["market"] == 0

    @pytest.mark.asyncio
    async def test_query_halted(self, query_tool):
        query_tool._request.return_value = _mock_response({
            "status": "halted",
            "code": "600519",
            "is_trading": True,
        })
        result = await query_tool._arun(stock_code="600519")
        assert "停牌" in result

    @pytest.mark.asyncio
    async def test_query_unsupported_market(self, query_tool):
        result = await query_tool._arun(stock_code="830879")
        assert "操作失败" in result
        assert "北交所" in result
        query_tool._request.assert_not_called()

    @pytest.mark.asyncio
    async def test_query_invalid_code_length(self, query_tool):
        result = await query_tool._arun(stock_code="123")
        assert "操作失败" in result
        query_tool._request.assert_not_called()

    @pytest.mark.asyncio
    async def test_query_unreachable(self, query_tool):
        query_tool._request.side_effect = ConnectionError("refused")
        result = await query_tool._arun(stock_code="600519")
        assert "行情服务不可达" in result

    @pytest.mark.asyncio
    async def test_is_available_always_true_without_channel(self):
        t = QueryStockPriceTool(user_id="u1", thread_id="t1", agent_id="a1")
        assert await t.is_available() is True
