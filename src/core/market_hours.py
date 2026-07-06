"""A股交易时段判断 (Asia/Shanghai, 纯函数).

A股连续交易时段 (工作日): 上午 09:30-11:30, 下午 13:00-15:00.
盘外/周末返回 False, 价格监控轮询据此跳过, 避免对收盘价误触发.

节假日仅按周末过滤; 临时休市 (如法定节假日调休) 不在判定范围. 届时价格为
上一交易日收盘快照, 监控因一次性语义 (触发即结束) 影响可控.
"""

from __future__ import annotations

from datetime import datetime, time
from zoneinfo import ZoneInfo

_CN_TZ = ZoneInfo("Asia/Shanghai")

# (开始, 结束) 连续交易时段, 闭区间
_SESSIONS = (
    (time(9, 30), time(11, 30)),
    (time(13, 0), time(15, 0)),
)


def to_cn(dt: datetime | None = None) -> datetime:
    """转换为 Asia/Shanghai 本地时间. dt 为 None 时取当前时刻."""
    if dt is None:
        return datetime.now(_CN_TZ)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ZoneInfo("UTC"))
    return dt.astimezone(_CN_TZ)


def is_trading_hours(dt: datetime | None = None) -> bool:
    """判断给定时刻 (默认当前) 是否处于 A股连续交易时段."""
    cn = to_cn(dt)
    if cn.weekday() >= 5:  # 周六/日
        return False
    now_t = cn.time()
    return any(start <= now_t <= end for start, end in _SESSIONS)


__all__ = ["is_trading_hours", "to_cn"]
