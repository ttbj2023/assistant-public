"""quote-service /quote 端点字段标准化纯函数测试.

quote_format 位于 docker/quote-service/ (独立部署单元, 不在 src 包),
通过 importlib 按路径加载, 仅测试纯函数逻辑 (is_halted / normalize_quote).
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

_MODULE_PATH = (
    Path(__file__).resolve().parents[3]
    / "docker"
    / "quote-service"
    / "quote_format.py"
)


def _load_quote_format():
    spec = importlib.util.spec_from_file_location("quote_format", _MODULE_PATH)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


qf = _load_quote_format()


def _raw_quote(**overrides):
    base = {
        "market": 1,
        "code": "600519",
        "price": 1685.20,
        "last_close": 1664.80,
        "open": 1670.0,
        "high": 1690.0,
        "low": 1668.0,
        "vol": 23000.0,
        "amount": 3.86e9,
        "bid1": 1685.0,
        "bid2": 1684.99,
        "bid3": 1684.98,
        "bid4": 0,
        "bid5": 0,
        "ask1": 1685.20,
        "ask2": 1685.50,
        "ask3": 1686.0,
        "ask4": 0,
        "ask5": 0,
        "bid_vol1": 5,
        "bid_vol2": 10,
        "bid_vol3": 3,
        "bid_vol4": 0,
        "bid_vol5": 0,
        "ask_vol1": 2,
        "ask_vol2": 8,
        "ask_vol3": 15,
        "ask_vol4": 0,
        "ask_vol5": 0,
    }
    base.update(overrides)
    return base


class TestIsHalted:
    def test_normal_not_halted(self):
        assert qf.is_halted(_raw_quote()) is False

    def test_zero_price_halted(self):
        assert qf.is_halted(_raw_quote(price=0)) is True

    def test_negative_price_halted(self):
        assert qf.is_halted(_raw_quote(price=-1)) is True

    def test_missing_price_halted(self):
        raw = _raw_quote()
        del raw["price"]
        assert qf.is_halted(raw) is True

    def test_bool_price_rejected(self):
        # bool 是 int 子类, 不应被当作合法价格
        assert qf.is_halted(_raw_quote(price=True)) is True


class TestNormalizeQuote:
    def test_ok_full_fields(self):
        out = qf.normalize_quote(_raw_quote(), is_trading=True)
        assert out["status"] == "ok"
        assert out["is_trading"] is True
        assert out["market"] == 1
        assert out["code"] == "600519"
        assert out["price"] == 1685.2
        assert out["last_close"] == 1664.8
        assert out["vol"] == 23000.0
        # 五档扁平字段 → 列表
        assert out["bid_prices"] == [1685.0, 1684.99, 1684.98, None, None]
        assert out["ask_prices"] == [1685.20, 1685.50, 1686.0, None, None]
        assert out["bid_vols"] == [5.0, 10.0, 3.0, 0.0, 0.0]

    def test_is_trading_injected(self):
        out = qf.normalize_quote(_raw_quote(), is_trading=False)
        assert out["is_trading"] is False

    def test_halted_returns_minimal(self):
        out = qf.normalize_quote(_raw_quote(price=0), is_trading=True)
        assert out["status"] == "halted"
        assert out["is_trading"] is True
        # 停牌时不返回价格/盘口字段
        assert "price" not in out
        assert "bid_prices" not in out

    def test_non_numeric_price_treated_as_halted(self):
        out = qf.normalize_quote(_raw_quote(price="N/A"), is_trading=True)
        assert out["status"] == "halted"

    def test_zero_bid_price_becomes_none(self):
        out = qf.normalize_quote(_raw_quote(), is_trading=True)
        # bid4=0 → _positive_float 返回 None (无该档报价)
        assert out["bid_prices"][3] is None
        assert out["bid_prices"][4] is None
