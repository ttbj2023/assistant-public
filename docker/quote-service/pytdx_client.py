"""通达信行情连接管理器.

职责:
- best-IP 测速: 并发 TCP 探测 pytdx 内置服务器列表, 选延迟最低的
- 持久连接: heartbeat=True 自动保活 (心跳线程), auto_retry=True 偶发断连重试
- 异步桥接: pytdx 为同步阻塞库, get_quotes 通过 asyncio.to_thread 调用
- 运行时故障转移: 连续失败达阈值则重测速切换服务器

pytdx 线程安全说明:
- heartbeat=True 会自动开启 multithread=True, 内部加锁, 心跳线程与
  get_quotes 的 executor 线程并发访问 socket 是安全的.
"""

from __future__ import annotations

import asyncio
import logging
import socket
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from pytdx.config.hosts import hq_hosts
from pytdx.hq import TdxHq_API

logger = logging.getLogger(__name__)

# (market, code) -> quote dict. market: 0=深 1=沪
QuoteMap = dict[tuple[int, str], dict]

# 连续失败达此阈值触发故障转移 (重测速 + 切换服务器)
_FAILOVER_FAILURE_THRESHOLD = 3
# best-IP 探测的 TCP 连接超时 (秒)
_PROBE_TIMEOUT = 1.5
# best-IP 探测并发数
_PROBE_WORKERS = 32


@dataclass
class TdxClientStats:
    """连接统计, 供 /health 暴露."""

    current_host: str = ""
    last_best_ip_at: str = ""
    consecutive_failures: int = 0
    total_quotes: int = 0
    total_failures: int = 0
    last_quote_at: str = ""
    last_error: str = ""


def _probe(host_entry: tuple) -> tuple[str, int, float, bool]:
    """探测单个服务器 TCP 连通性.

    hq_hosts 元素结构: (name, ip, port). 返回 (ip, port, 延迟秒, 是否成功).
    """
    _name, ip, port = host_entry[0], host_entry[1], int(host_entry[2])
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(_PROBE_TIMEOUT)
    start = time.monotonic()
    try:
        sock.connect((ip, port))
        delay = time.monotonic() - start
        sock.close()
        return ip, port, delay, True
    except OSError:
        return ip, port, -1.0, False


def select_best_ip(candidates: list | None = None) -> tuple[str, int] | None:
    """并发测速, 返回最快服务器的 (ip, port), 全部不可达返回 None."""
    candidates = candidates if candidates is not None else list(hq_hosts)
    with ThreadPoolExecutor(max_workers=_PROBE_WORKERS) as pool:
        probed = list(pool.map(_probe, candidates))
    reachable = [(ip, port, d) for ip, port, d, ok in probed if ok]
    if not reachable:
        logger.error("best-IP: 无可达服务器 (共探测 %d 个)", len(candidates))
        return None
    reachable.sort(key=lambda x: x[2])
    best = reachable[0]
    logger.info(
        "best-IP: 选定 %s:%d (%.0fms), 可达 %d/%d",
        best[0],
        best[1],
        best[2] * 1000,
        len(reachable),
        len(candidates),
    )
    return best[0], best[1]


class TdxClient:
    """通达信行情客户端 (单持久连接 + 故障转移)."""

    def __init__(self, failover_threshold: int = _FAILOVER_FAILURE_THRESHOLD) -> None:
        self._api: TdxHq_API | None = None
        self._host: str = ""
        self._port: int = 7709
        self._failover_threshold = failover_threshold
        self.stats = TdxClientStats()
        self._lock = asyncio.Lock()
        # 同步 API 调用复用单线程池, 避免每次新建线程
        self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="pytdx")

    @property
    def connected(self) -> bool:
        return self._api is not None

    def _select_and_set_host(self) -> bool:
        """测速选定最快服务器并记录. 返回是否成功."""
        best = select_best_ip()
        if best is None:
            return False
        self._host, self._port = best
        self.stats.current_host = f"{self._host}:{self._port}"
        self.stats.last_best_ip_at = _now_iso()
        return True

    def _open_connection(self) -> bool:
        """用当前 host/port 建立持久连接 (heartbeat + auto_retry)."""
        api = TdxHq_API(heartbeat=True, auto_retry=True)
        try:
            if not api.connect(self._host, self._port):
                logger.error("连接 TDX 失败: %s:%d", self._host, self._port)
                return False
        except Exception as e:
            logger.error("连接 TDX 异常: %s:%d, %s", self._host, self._port, e)
            self.stats.last_error = str(e)
            return False
        self._api = api
        logger.info("已连接 TDX: %s:%d", self._host, self._port)
        return True

    async def start(self) -> bool:
        """启动: 测速 + 建立持久连接. 返回是否成功."""
        async with self._lock:
            return await asyncio.to_thread(self._start_locked)

    def _start_locked(self) -> bool:
        if not self._select_and_set_host():
            return False
        return self._open_connection()

    async def ensure_connected(self) -> bool:
        """确保连接可用, 断开则重连. 故障转移计数达阈值则重测速."""
        if self._api is not None:
            return True
        async with self._lock:
            if self._api is not None:
                return True
            # 连续失败达阈值, 重测速换服务器
            if self.stats.consecutive_failures >= self._failover_threshold:
                logger.warning(
                    "连续失败 %d 次达阈值, 触发故障转移重测速",
                    self.stats.consecutive_failures,
                )
                self._select_and_set_host()
                self.stats.consecutive_failures = 0
            return await asyncio.to_thread(self._open_connection)

    async def get_quotes(self, codes: list[tuple[int, str]]) -> QuoteMap | None:
        """批量取实时报价.

        Args:
            codes: [(market, code), ...], market: 0=深 1=沪

        Returns:
            以 (market, code) 为 key 的报价字典; 调用失败返回 None.
            失败计数累计, 达阈值由下次 ensure_connected 触发故障转移.

        """
        if not codes:
            return {}
        if self._api is None:
            ok = await self.ensure_connected()
            if not ok:
                return None

        api = self._api  # 局部变量便于类型窄化 (已由上层保证非空)
        assert api is not None

        try:
            raw: list[dict] = await asyncio.to_thread(api.get_security_quotes, codes)
        except Exception as e:
            self._record_failure(str(e))
            self._api = None
            return None

        if not raw:
            self._record_failure("get_security_quotes 返回空")
            return None

        self._record_success(len(raw))
        result: QuoteMap = {}
        for q in raw:
            market = q.get("market")
            code = q.get("code")
            if market is None or code is None:
                continue
            result[int(market), str(code)] = q
        return result

    def _record_success(self, count: int) -> None:
        self.stats.consecutive_failures = 0
        self.stats.total_quotes += count
        self.stats.last_quote_at = _now_iso()

    def _record_failure(self, err: str) -> None:
        self.stats.consecutive_failures += 1
        self.stats.total_failures += 1
        self.stats.last_error = err
        logger.warning("取报价失败 (连续 %d): %s", self.stats.consecutive_failures, err)

    async def close(self) -> None:
        """关闭连接与线程池 (服务停止时调用)."""
        if self._api is not None:
            try:
                await asyncio.to_thread(self._api.disconnect)
            except Exception as e:
                logger.debug("断开 TDX 异常 (非致命): %s", e)
            self._api = None
        self._executor.shutdown(wait=False, cancel_futures=True)
        logger.info("TdxClient 已关闭")


def _now_iso() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat(timespec="seconds")


__all__ = ["QuoteMap", "TdxClient", "TdxClientStats", "select_best_ip"]
