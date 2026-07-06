"""实时报价字段标准化 (纯函数, 便于单测).

pytdx get_security_quotes 返回扁平的五档字段 (bid1..bid5 / ask1..ask5),
本模块将其标准化为结构化字段, 供 /quote 端点透传给 app 工具.

单位说明 (以 pytdx 实际返回为准, 必要时按实测调整):
- price 系列: 元 (pytdx 内部 _cal_price 已除以 100, 精确到分)
- vol: 手 (成交量)
- amount: 元 (成交额)
"""

from __future__ import annotations

from typing import Any

_BID_INDICES = (1, 2, 3, 4, 5)


def _to_float(v: Any) -> float | None:
    """安全转 float; None 或非数值返回 None."""
    if isinstance(v, bool):  # bool 是 int 子类, 显式排除
        return None
    if not isinstance(v, int | float):
        return None
    return float(v)


def _positive_float(v: Any) -> float | None:
    """仅当 v 为正数时返回 float, 否则 None (用于停牌/无报价档位判定)."""
    f = _to_float(v)
    return f if f is not None and f > 0 else None


def is_halted(raw: dict) -> bool:
    """停牌/无行情判定: 现价缺失或 <= 0."""
    price = _to_float(raw.get("price"))
    return price is None or price <= 0


def normalize_quote(raw: dict, *, is_trading: bool) -> dict[str, Any]:
    """pytdx 原始报价 -> 标准化字段 (供 /quote 端点返回).

    Args:
        raw: pytdx get_security_quotes 单条 OrderedDict
        is_trading: 当前是否交易时段 (服务端判断后注入, 避免 app 侧重写逻辑)

    Returns:
        标准化报价 dict; 停牌时 status='halted' 且核心价格为 None.
    """
    if is_halted(raw):
        return {
            "market": int(raw.get("market", 0)),
            "code": str(raw.get("code", "")),
            "status": "halted",
            "is_trading": is_trading,
        }

    return {
        "market": int(raw.get("market", 0)),
        "code": str(raw.get("code", "")),
        "status": "ok",
        "is_trading": is_trading,
        "price": _positive_float(raw.get("price")),
        "last_close": _positive_float(raw.get("last_close")),
        "open": _positive_float(raw.get("open")),
        "high": _positive_float(raw.get("high")),
        "low": _positive_float(raw.get("low")),
        "vol": _to_float(raw.get("vol")),
        "amount": _to_float(raw.get("amount")),
        "bid_prices": [_positive_float(raw.get(f"bid{i}")) for i in _BID_INDICES],
        "ask_prices": [_positive_float(raw.get(f"ask{i}")) for i in _BID_INDICES],
        "bid_vols": [_to_float(raw.get(f"bid_vol{i}")) for i in _BID_INDICES],
        "ask_vols": [_to_float(raw.get(f"ask_vol{i}")) for i in _BID_INDICES],
    }


__all__ = ["is_halted", "normalize_quote"]
