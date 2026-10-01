"""每用户 MSA device flow 授权管理服务.

状态机: none -> pending -> authorized / failed; authorized --revoke--> none.

- pending 态是进程内存 (device_code 约 15 分钟短时效, 进程重启即失效,
  用户重新发起即可); authorized 态的唯一权威是 token 文件存在性.
- 后台轮询任务由 start_authorization 派发, 完成/失败后落 token 文件或
  记录错误, 任务自身出队.
- 授权完成回调: 唤醒同步引擎立即首轮同步; 发起对话有微信渠道配置时
  经渠道网关推送回执 (无配置/失败仅日志, 用户可手动查状态).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from src.sync.graph_sync_engine import notify_local_write
from src.sync.msgraph_client import MSGraphAuthError, MSGraphClient
from src.sync.token_store import GraphTokenStore

logger = logging.getLogger(__name__)

_HTTP_TIMEOUT = httpx.Timeout(connect=5.0, read=30.0, write=10.0, pool=5.0)

_AUTH_SUCCESS_TEXT = (
    "Outlook 授权成功, TODO 与日程同步已开始. "
    "待办将同步到 To Do 的「Assistant」清单, "
    "日程到 Outlook 的「Assistant」子日历."
)


@dataclass
class PendingFlow:
    """进行中的 device flow 状态 (进程内存)."""

    status: str  # "pending" | "failed"
    verification_uri: str
    user_code: str
    expires_at: float
    error: str = ""
    # 回执上下文: 发起授权的对话 (供完成时查该对话的渠道配置)
    reply_thread_id: str = ""
    reply_agent_id: str = ""


class GraphAuthService:
    """MSA device flow 授权管理 (发起/后台轮询/状态查询/解绑)."""

    def __init__(
        self,
        *,
        http: httpx.AsyncClient | None = None,
        base_path: Path | None = None,
    ) -> None:
        """初始化.

        Args:
            http: 共享 httpx 客户端 (缺省惰性自建并由本服务持有生命周期)
            base_path: token 存储根目录 (缺省用户数据根, 测试注入)

        """
        self._http = http
        self._owns_http = http is None
        self._base_path = base_path
        self._pending: dict[str, PendingFlow] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}

    async def start_authorization(
        self,
        user_id: str,
        *,
        thread_id: str = "",
        agent_id: str = "",
    ) -> dict[str, Any]:
        """发起 device flow 并派发后台轮询任务.

        Args:
            user_id: 用户ID
            thread_id: 发起对话线程ID (授权完成后向该对话渠道回执)
            agent_id: 发起对话 Agent ID (渠道配置三级隔离查询用)

        Returns:
            含 verification_uri / user_code / expires_in / interval 的验证信息

        Raises:
            RuntimeError: 用户已授权 (token 文件已存在, 需先解绑)

        """
        store = self._token_store(user_id)
        if store.exists():
            raise RuntimeError(
                "已授权, 重新授权请先解绑 (DELETE /v1/sync/msgraph/authorize)"
            )

        client = self._make_client()
        flow = await client.start_device_flow()

        interval = float(flow.get("interval") or 5)
        expires_in = int(flow.get("expires_in") or 900)
        self._pending[user_id] = PendingFlow(
            status="pending",
            verification_uri=str(flow["verification_uri"]),
            user_code=str(flow["user_code"]),
            expires_at=time.time() + expires_in,
            reply_thread_id=thread_id,
            reply_agent_id=agent_id,
        )
        self._spawn_poll_task(user_id, str(flow["device_code"]), interval, expires_in)

        logger.info("MSA device flow 已发起: user=%s", user_id)
        return {
            "verification_uri": flow["verification_uri"],
            "user_code": flow["user_code"],
            "expires_in": expires_in,
            "interval": interval,
        }

    def get_status(self, user_id: str) -> dict[str, Any]:
        """查询授权状态.

        Args:
            user_id: 用户ID

        Returns:
            {status: none|pending|failed|authorized}; pending 附验证信息,
            failed 附错误原因

        """
        if self._token_store(user_id).exists():
            return {"status": "authorized"}
        flow = self._pending.get(user_id)
        if flow is None:
            return {"status": "none"}
        result: dict[str, Any] = {"status": flow.status}
        if flow.status == "pending":
            result.update({
                "verification_uri": flow.verification_uri,
                "user_code": flow.user_code,
                "expires_at": flow.expires_at,
            })
        else:
            result["error"] = flow.error
        return result

    def revoke(self, user_id: str) -> bool:
        """解绑: 取消进行中的轮询并删除 token 文件.

        Args:
            user_id: 用户ID

        Returns:
            是否存在需要清理的授权状态 (pending 或 token 文件)

        """
        had_state = False
        task = self._tasks.get(user_id)
        if task is not None:
            task.cancel()
            had_state = True
        if self._pending.pop(user_id, None) is not None:
            had_state = True
        if self._token_store(user_id).delete():
            had_state = True
        if had_state:
            logger.info("MSA 授权已解绑: user=%s", user_id)
        return had_state

    async def shutdown(self) -> None:
        """停止全部后台轮询并释放自持有的 http 客户端."""
        # await 会触发 done_callback 出队, 先快照再遍历
        tasks = list(self._tasks.values())
        for task in tasks:
            task.cancel()
        for task in tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._tasks.clear()
        self._pending.clear()
        if self._owns_http and self._http is not None:
            await self._http.aclose()
            self._http = None

    # ── 内部 ─────────────────────────────────────────────

    def _spawn_poll_task(
        self,
        user_id: str,
        device_code: str,
        interval: float,
        timeout: float,
    ) -> None:
        # 重新发起时取消旧任务, 同一用户仅保留一个轮询
        old_task = self._tasks.get(user_id)
        if old_task is not None:
            old_task.cancel()

        task = asyncio.create_task(
            self._poll_and_store(user_id, device_code, interval, timeout),
            name=f"msgraph-auth-poll-{user_id}",
        )
        self._tasks[user_id] = task
        task.add_done_callback(lambda _: self._tasks.pop(user_id, None))

    async def _poll_and_store(
        self,
        user_id: str,
        device_code: str,
        interval: float,
        timeout: float,
    ) -> None:
        try:
            tokens = await self._make_client().poll_device_flow(
                device_code,
                interval=interval,
                timeout=timeout,
            )
        except MSGraphAuthError as e:
            flow = self._pending.get(user_id)
            if flow is not None:
                flow.status = "failed"
                flow.error = str(e)
            logger.warning("MSA device flow 失败: user=%s, error=%s", user_id, e)
            return
        except asyncio.CancelledError:
            raise

        await self._token_store(user_id).save(tokens)
        flow = self._pending.pop(user_id, None)
        logger.info("MSA 授权完成: user=%s", user_id)

        # 完成回调: 唤醒引擎立即首轮同步 + 微信渠道回执 (皆不容失败影响授权)
        notify_local_write(user_id)
        with contextlib.suppress(Exception):
            await self._send_auth_success_reply(user_id, flow)

    async def _send_auth_success_reply(
        self,
        user_id: str,
        flow: PendingFlow | None,
    ) -> None:
        """授权成功后向发起对话的微信渠道推送回执.

        仅当发起时捕获了对话上下文且该对话有微信渠道配置 (渠道自动发现
        机制写入的 target/account_id) 时发送; 其余情况静默 (用户可查状态).
        """
        if flow is None or not flow.reply_thread_id or not flow.reply_agent_id:
            return

        from src.storage.service.user_channel_config_service import (
            get_user_channel_config_service,
        )

        config_service = await get_user_channel_config_service(
            user_id,
            flow.reply_thread_id,
            flow.reply_agent_id,
        )
        wechat_cfg = await config_service.get_config_for_channel("wechat")
        if not wechat_cfg:
            logger.info(
                "发起对话无微信渠道配置, 跳过授权回执: user=%s",
                user_id,
            )
            return

        from src.core.notification import DeliverySpec, get_notification_service

        spec = DeliverySpec(
            method="wechat",
            account_id=str(wechat_cfg.get("account_id", "")),
            target=str(wechat_cfg.get("target", "")),
        )
        outcome = await get_notification_service().send(spec, _AUTH_SUCCESS_TEXT)
        if outcome.ok:
            logger.info("授权回执已推送: user=%s", user_id)
        else:
            logger.warning(
                "授权回执推送失败 (不影响授权): user=%s, error=%s",
                user_id,
                outcome.error,
            )

    def _make_client(self) -> MSGraphClient:
        if self._http is None:
            self._http = httpx.AsyncClient(timeout=_HTTP_TIMEOUT)
        return MSGraphClient(http=self._http)

    def _token_store(self, user_id: str) -> GraphTokenStore:
        return GraphTokenStore(user_id, base_path=self._base_path)


# ───────────────────── 单例 + 生命周期 ─────────────────────

_service_instance: GraphAuthService | None = None


def get_graph_auth_service() -> GraphAuthService:
    """获取或创建 GraphAuthService 单例 (自建 http 客户端, 关闭经 shutdown)."""
    global _service_instance
    if _service_instance is None:
        _service_instance = GraphAuthService()
    return _service_instance


async def shutdown_graph_auth_service() -> None:
    """应用关闭时调用, 停止轮询并释放资源."""
    global _service_instance
    if _service_instance is not None:
        await _service_instance.shutdown()
        _service_instance = None


__all__ = [
    "GraphAuthService",
    "get_graph_auth_service",
    "shutdown_graph_auth_service",
]
