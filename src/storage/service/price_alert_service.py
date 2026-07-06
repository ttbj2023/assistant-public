"""价格监控引擎 (app 内常驻轮询).

一次性语义: 规则触发即派发 + disable 结束, 无长期监控/状态机/日限. 全局单例
引擎遍历所有用户的 price_alert.db (per-agent 物理隔离存储), 跨用户去重 code 后
批量调 quote-service /quotes 取价, 派发复用统一 NotificationService.

轮询循环仿 _periodic_semantic_cache_cleanup (fastapi_app.py); 生命周期由
LifecycleRegistry 管理 (register_resource + close_all 逆序关闭).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx

from src.config.runtime_env import get_quote_service_base_url
from src.core.market_hours import is_trading_hours
from src.core.notification import DeliverySpec, get_notification_service
from src.storage.dao.async_database_manager import create_async_price_alert_db_manager
from src.storage.dao.async_price_alert_dao import AsyncPriceAlertDAO
from src.storage.models.price_alert import PriceAlertRule

logger = logging.getLogger(__name__)

_DEFAULT_POLL_INTERVAL = 60.0
# 非交易时段(盘外/周末/节假日)探测是否开盘的间隔, 避免以交易频率空转
_NON_TRADING_CHECK_INTERVAL = 300.0
_DEFAULT_BASE_URL = "http://127.0.0.1:8767"


# ───────────────────── 纯函数 (单测目标) ─────────────────────


def is_triggered(direction: str, threshold: float, price: float) -> bool:
    """一次性触发判定: 价格是否进入触发区 (含创建时已穿越即触发)."""
    if direction == "above":
        return price > threshold
    if direction == "below":
        return price < threshold
    return False


def format_alert(
    stock_name: str,
    stock_code: str,
    direction: str,
    threshold: float,
    price: float,
) -> str:
    """格式化告警文案."""
    verb = "向上突破" if direction == "above" else "向下突破"
    head = f"{stock_name}({stock_code})" if stock_name else stock_code
    return f"📊 {head} 当前价 {price:.2f} 已{verb} {threshold:.2f}"


def rule_to_delivery(rule: PriceAlertRule) -> DeliverySpec | None:
    """从规则构建投递描述; 投递参数缺失返回 None."""
    if rule.delivery_method == "wechat":
        if not rule.openclaw_channel or not rule.account_id or not rule.target:
            return None
        return DeliverySpec(
            method="wechat",
            openclaw_channel=rule.openclaw_channel,
            account_id=rule.account_id,
            target=rule.target,
        )
    if rule.delivery_method == "email":
        if not rule.email_address:
            return None
        return DeliverySpec(method="email", email_address=rule.email_address)
    return None


def discover_price_alert_dbs() -> list[tuple[str, str, str]]:
    """扫描数据目录, 发现所有 price_alert.db.

    Returns:
        (user_id, thread_id, agent_id) 元组列表.
    """
    from src.core.path_resolver import get_user_path_resolver

    base = get_user_path_resolver().base_path
    if not base.exists():
        return []

    results: list[tuple[str, str, str]] = []
    for user_dir in base.iterdir():
        if not user_dir.is_dir():
            continue
        for thread_dir in user_dir.iterdir():
            if not thread_dir.is_dir():
                continue
            for agent_dir in thread_dir.iterdir():
                if not agent_dir.is_dir():
                    continue
                if (agent_dir / "database" / "price_alert.db").exists():
                    results.append((user_dir.name, thread_dir.name, agent_dir.name))
    return results


# ───────────────────── 引擎 ─────────────────────


@dataclass
class EngineStats:
    """引擎运行统计."""

    running: bool = False
    total_ticks: int = 0
    last_tick_at: str = ""
    last_tick_trading: bool = False
    last_tick_rules: int = 0
    last_tick_triggered: int = 0
    last_tick_error: str = ""
    total_triggered: int = 0


class PriceAlertEngine:
    """价格监控全局轮询引擎 (单例)."""

    def __init__(self, *, poll_interval: float = _DEFAULT_POLL_INTERVAL) -> None:
        self._poll_interval = poll_interval
        self._task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()
        self._http = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=5.0, read=15.0, write=10.0, pool=5.0)
        )
        self.stats = EngineStats()

    # ── 生命周期 ──────────────────────────────────────────

    async def start(self) -> None:
        if self._task is not None:
            return
        self._stop_event.clear()
        self.stats.running = True
        self._task = asyncio.create_task(self._run(), name="price-alert-engine")
        logger.info("PriceAlertEngine 已启动 (poll=%.0fs)", self._poll_interval)

    async def stop(self) -> None:
        self.stats.running = False
        self._stop_event.set()
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        await self._http.aclose()
        logger.info("PriceAlertEngine 已停止")

    async def _run(self) -> None:
        """主循环: 交易时段按 poll_interval 轮询; 非交易时段不取价, 低频探测开盘.

        非交易时段不执行 tick(不取价不评估), 仅以 _NON_TRADING_CHECK_INTERVAL
        探测是否进入交易时段, 避免以交易频率空转. tick 内部仍独立判断交易时段
        (防御: 直接调用或跨边界时安全).
        """
        while not self._stop_event.is_set():
            in_trading = is_trading_hours()
            self.stats.last_tick_trading = in_trading
            if in_trading:
                try:
                    await self.tick()
                except Exception as e:
                    logger.exception("tick 异常 (非致命, 继续): %s", e)
                    self.stats.last_tick_error = str(e)
            sleep_for = (
                self._poll_interval if in_trading else _NON_TRADING_CHECK_INTERVAL
            )
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._stop_event.wait(), timeout=sleep_for)

    # ── 单次轮询 ──────────────────────────────────────────

    async def tick(self) -> None:
        """一次轮询: 收集 active 规则 → 去重 → 批量取价 → 评估 → 派发."""
        self.stats.total_ticks += 1
        self.stats.last_tick_at = _now_iso()
        self.stats.last_tick_triggered = 0

        trading = is_trading_hours()
        self.stats.last_tick_trading = trading
        if not trading:
            self.stats.last_tick_rules = 0
            return

        owners_rules = await self._collect_active_rules()
        self.stats.last_tick_rules = len(owners_rules)
        if not owners_rules:
            return

        codes = _dedupe_codes([r for _, r in owners_rules])
        quotes = await self._fetch_quotes(codes)
        if quotes is None:
            logger.warning("本轮取价失败, 跳过评估")
            return

        triggered = 0
        for owner, rule in owners_rules:
            price = quotes.get((rule.market, rule.stock_code))
            if price is None or price <= 0:
                continue
            if not is_triggered(
                str(rule.direction), float(rule.threshold_price), price
            ):
                continue
            if await self._dispatch(rule, price):
                dao = await self._get_dao(*owner)
                await dao.disable(rule.rule_id, owner, triggered=True)
                triggered += 1

        self.stats.last_tick_triggered = triggered
        self.stats.total_triggered += triggered

    async def _collect_active_rules(
        self,
    ) -> list[tuple[tuple[str, str, str], PriceAlertRule]]:
        """遍历所有 price_alert.db 收集 active 规则 (跨用户)."""
        result: list[tuple[tuple[str, str, str], PriceAlertRule]] = []
        for owner in discover_price_alert_dbs():
            try:
                dao = await self._get_dao(*owner)
                rules = await dao.list_active_all()
                for r in rules:
                    result.append((owner, r))
            except Exception as e:
                logger.warning("收集规则失败 %s: %s", "/".join(owner), e)
        return result

    async def _get_dao(
        self,
        user_id: str,
        thread_id: str,
        agent_id: str,
    ) -> AsyncPriceAlertDAO:
        db_manager = await create_async_price_alert_db_manager(
            user_id, thread_id, agent_id=agent_id
        )
        return AsyncPriceAlertDAO(db_manager.session_factory)

    async def _fetch_quotes(
        self, codes: list[tuple[int, str]]
    ) -> dict[tuple[int, str], float] | None:
        """调 quote-service /quotes 批量取价, 返回 {(market, code): price}."""
        if not codes:
            return {}
        items = [{"market": m, "code": c} for m, c in codes]
        try:
            resp = await self._http.post(
                f"{self._get_base_url()}/quotes", json={"items": items}
            )
            resp.raise_for_status()
        except httpx.HTTPError as e:
            logger.error("取价 HTTP 失败: %s", e)
            return None

        result: dict[tuple[int, str], float] = {}
        for q in resp.json().get("quotes", []):
            if q.get("status") != "ok":
                continue
            price = q.get("price")
            if isinstance(price, int | float) and price > 0:
                result[int(q["market"]), str(q["code"])] = float(price)
        return result

    def _get_base_url(self) -> str:
        """行情服务地址 (委托模块级 get_quote_service_url)."""
        return get_quote_service_url()

    async def _dispatch(self, rule: PriceAlertRule, price: float) -> bool:
        """派发告警 (NotificationService)."""
        delivery = rule_to_delivery(rule)
        if delivery is None:
            logger.warning("规则 %s 投递参数缺失, 跳过派发", rule.rule_id)
            return False
        text = format_alert(
            rule.stock_name,
            rule.stock_code,
            str(rule.direction),
            float(rule.threshold_price),
            price,
        )
        subject = f"价格告警: {rule.stock_name or rule.stock_code}"
        return await get_notification_service().send(delivery, text, subject=subject)

    # ── CRUD (供工具调用, 按属主隔离) ─────────────────────

    async def create_rule(
        self,
        owner: tuple[str, str, str],
        **fields: Any,
    ) -> PriceAlertRule:
        """创建规则 (owner 注入 user_id/thread_id/agent_id)."""
        user_id, thread_id, agent_id = owner
        fields.update({
            "user_id": user_id,
            "thread_id": thread_id,
            "agent_id": agent_id,
        })
        dao = await self._get_dao(*owner)
        return await dao.create(**fields)

    async def list_active(self, owner: tuple[str, str, str]) -> list[PriceAlertRule]:
        dao = await self._get_dao(*owner)
        return await dao.list_active_by_owner(*owner)

    async def disable_rule(
        self,
        rule_id: str,
        owner: tuple[str, str, str],
    ) -> bool:
        dao = await self._get_dao(*owner)
        return await dao.disable(rule_id, owner, triggered=False)


# ───────────────────── 辅助 ─────────────────────


def get_quote_service_url() -> str:
    """行情服务地址解析 (QUOTE_SERVICE_BASE_URL > price_alert config > 默认).

    引擎与 query_stock_price 工具共享, 避免重复.
    """
    url = get_quote_service_base_url()
    if url:
        return url.rstrip("/")
    try:
        from src.config.tools_config import get_config

        shared = get_config().get_internal_tool_config("price_alert")
        if shared and shared.config:
            return shared.config.get("base_url", _DEFAULT_BASE_URL).rstrip("/")
    except Exception as e:
        logger.debug("读取 price_alert 共享配置失败: %s", e)
    return _DEFAULT_BASE_URL


def _dedupe_codes(rules: list[PriceAlertRule]) -> list[tuple[int, str]]:
    """从规则列表去重出 (market, code) 列表."""
    seen: set[tuple[int, str]] = set()
    for r in rules:
        seen.add((int(r.market), str(r.stock_code)))
    return list(seen)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


# ───────────────────── 单例 + 生命周期 ─────────────────────


_engine_instance: PriceAlertEngine | None = None


def get_price_alert_engine() -> PriceAlertEngine:
    """获取或创建 PriceAlertEngine 单例 (不自启动; 由 lifespan 调 start())."""
    global _engine_instance
    if _engine_instance is None:
        _engine_instance = PriceAlertEngine()
    return _engine_instance


async def shutdown_price_alert_engine() -> None:
    """应用关闭时调用, 停止轮询并重置单例."""
    global _engine_instance
    if _engine_instance is not None:
        await _engine_instance.stop()
        _engine_instance = None


__all__ = [
    "EngineStats",
    "PriceAlertEngine",
    "discover_price_alert_dbs",
    "format_alert",
    "get_price_alert_engine",
    "get_quote_service_url",
    "is_triggered",
    "rule_to_delivery",
    "shutdown_price_alert_engine",
]
