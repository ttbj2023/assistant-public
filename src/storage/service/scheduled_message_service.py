"""定时消息业务服务.

提供定时消息的创建, 调度, 发送和状态管理.
支持多渠道发送: 微信(通过OpenClaw CLI), 邮件(通过SMTP)等.
渠道配置从UserChannelConfigService读取, 支持多用户隔离.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any, override

from src.storage.dao.async_scheduled_message_dao import AsyncScheduledMessageDAO
from src.storage.models.scheduled_message import MessageStatus, ScheduledMessage

from .health_check_mixin import ServiceHealthCheckMixin

logger = logging.getLogger(__name__)

_ServiceRegistry: dict[str, ScheduledMessageService] = {}


class ScheduledMessageService(ServiceHealthCheckMixin):
    """定时消息业务服务."""

    def __init__(
        self,
        session_factory: Callable[[], Any],
        user_id: str,
        thread_id: str,
        agent_id: str,
        max_pending_messages: int = 50,
        max_schedule_ahead_hours: int = 168,
        default_channel: str = "wechat",
    ) -> None:
        super().__init__()
        self.session_factory = session_factory
        self.user_id = user_id
        self.thread_id = thread_id
        self.agent_id = agent_id
        self.logger = logging.getLogger(f"{__name__}.ScheduledMessageService")

        self.dao = AsyncScheduledMessageDAO(session_factory)

        self._max_pending = max_pending_messages
        self._max_ahead_hours = max_schedule_ahead_hours
        self._default_channel = default_channel

        self._timers: dict[str, asyncio.TimerHandle] = {}
        self._background_tasks: set[asyncio.Task] = set()
        self._initialized = False

    async def initialize(self) -> None:
        """初始化: 标记过期消息为missed, 加载pending消息到内存调度器."""
        if self._initialized:
            return

        now = datetime.now(UTC)
        missed_count = await self.dao.mark_expired_as_missed(now)
        if missed_count > 0:
            self.logger.info("🕐 标记%d条过期消息为missed", missed_count)

        pending = await self.dao.get_pending_messages(
            self.user_id,
            self.thread_id,
            self.agent_id,
        )
        for msg in pending:
            self._register_timer(msg)

        self._initialized = True
        self.logger.info(
            "✅ ScheduledMessageService初始化完成, %d条pending消息已注册",
            len(pending),
        )

    def _register_timer(self, msg: ScheduledMessage) -> None:
        """注册asyncio定时器."""
        if msg.message_id in self._timers:
            return

        loop = asyncio.get_event_loop()
        now = datetime.now(UTC)
        send_time_utc = msg.send_time
        if send_time_utc.tzinfo is None:
            send_time_utc = send_time_utc.replace(tzinfo=UTC)

        delay = (send_time_utc - now).total_seconds()
        if delay <= 0:
            self._background_tasks.add(
                asyncio.ensure_future(self._send_message(msg.message_id)),
            )
            return

        def _fire_and_forget(message_id: str) -> None:
            task = asyncio.ensure_future(self._send_message(message_id))
            self._background_tasks.add(task)
            task.add_done_callback(self._background_tasks.discard)

        handle = loop.call_later(delay, _fire_and_forget, msg.message_id)
        self._timers[msg.message_id] = handle
        self.logger.debug("⏰ 注册定时器: %s, delay=%.1fs", msg.message_id, delay)

    def _cancel_timer(self, message_id: str) -> None:
        handle = self._timers.pop(message_id, None)
        if handle:
            handle.cancel()

    async def shutdown(self, task_timeout: float = 5.0) -> None:
        """优雅关闭: cancel未触发定时器, 短超时等待在途发送任务.

        Args:
            task_timeout: 在途 background_tasks 的最长等待秒数, 超时则 cancel
        """
        # 1. cancel 所有未触发的定时器
        for handle in self._timers.values():
            handle.cancel()
        self._timers.clear()

        # 2. 短超时 await 在途发送任务, 超时则 cancel 剩余
        pending = [t for t in self._background_tasks if not t.done()]
        if pending:
            try:
                await asyncio.wait_for(
                    asyncio.gather(*pending, return_exceptions=True),
                    timeout=task_timeout,
                )
            except TimeoutError:
                self.logger.warning(
                    "关闭超时(%.1fs), cancel %d个在途发送任务",
                    task_timeout,
                    len(pending),
                )
                for task in pending:
                    task.cancel()
        self._background_tasks.clear()
        self.logger.info("ScheduledMessageService已关闭")

    async def schedule_message(
        self,
        message: str,
        send_time: datetime,
        description: str | None = None,
        channel: str | None = None,
        subject: str | None = None,
        html_body: str | None = None,
        timezone: str = "Asia/Shanghai",
    ) -> ScheduledMessage:
        """创建定时消息并注册调度器.

        Args:
            message: 消息内容
            send_time: 发送时间(无时区时按timezone参数解释为本地时间)
            description: 备注说明
            channel: 发送渠道
            subject: 邮件主题
            html_body: 邮件HTML正文
            timezone: 用户时区, 用于将无时区的send_time转为UTC

        """
        start_time = time.time()

        try:
            now = datetime.now(UTC)
            if send_time.tzinfo is None:
                from zoneinfo import ZoneInfo

                send_time = send_time.replace(tzinfo=ZoneInfo(timezone)).astimezone(UTC)
            else:
                send_time = send_time.astimezone(UTC)

            if send_time <= now:
                # 模型给出过去时间视为"尽快发送", 自动顺延到最近可调度时间
                send_time = now + timedelta(seconds=30)
                logger.warning(
                    "send_time 已过期, 自动顺延 30 秒视为尽快发送: user=%s thread=%s send_time=%s",
                    self.user_id,
                    self.thread_id,
                    send_time.isoformat(),
                )

            max_ahead = timedelta(hours=self._max_ahead_hours)
            if send_time - now > max_ahead:
                raise ValueError(f"发送时间不能超过{self._max_ahead_hours}小时后")

            # 统一存为naive UTC, 避免SQLite/SQLModel剥离时区后导致歧义
            send_time = send_time.replace(tzinfo=None)

            pending_count = await self.dao.count_pending(
                self.user_id,
                self.thread_id,
                self.agent_id,
            )
            if pending_count >= self._max_pending:
                raise ValueError(f"待发送消息数量已达上限({self._max_pending})")

            effective_channel = channel or self._default_channel

            msg = await self.dao.create_message(
                message=message,
                send_time=send_time,
                user_id=self.user_id,
                thread_id=self.thread_id,
                agent_id=self.agent_id,
                description=description,
                channel=effective_channel,
                subject=subject,
                html_body=html_body,
            )

            self._register_timer(msg)

            duration = (time.time() - start_time) * 1000
            self.logger.info(
                "✅ 定时消息创建成功: %s, channel=%s, send_time=%s, duration=%.2fms",
                msg.message_id,
                effective_channel,
                send_time.isoformat(),
                duration,
            )
            return msg

        except ValueError:
            raise
        except Exception as e:
            duration = (time.time() - start_time) * 1000
            self.logger.error("❌ 创建定时消息失败: %s, duration=%.2fms", e, duration)
            raise RuntimeError(f"创建定时消息失败: {e}") from e

    async def list_pending_messages(self) -> list[ScheduledMessage]:
        """列出所有待发送消息."""
        try:
            return await self.dao.get_pending_messages(
                self.user_id,
                self.thread_id,
                self.agent_id,
            )
        except Exception as e:
            self.logger.error("❌ 查询待发送消息失败: %s", e)
            raise RuntimeError(f"查询待发送消息失败: {e}") from e

    async def cancel_message(self, message_id: str) -> bool:
        """取消定时消息."""
        start_time = time.time()

        try:
            msg = await self.dao.get_by_message_id(message_id)
            if not msg:
                raise FileNotFoundError(f"消息不存在: {message_id}")
            if msg.status != MessageStatus.PENDING:
                raise ValueError(
                    f"消息状态为{msg.status.value}, 只有pending状态的消息可以取消",
                )

            self._cancel_timer(message_id)
            updated = await self.dao.update_status(message_id, MessageStatus.CANCELLED)

            duration = (time.time() - start_time) * 1000
            self.logger.info(
                "✅ 取消定时消息: %s, duration=%.2fms",
                message_id,
                duration,
            )
            return updated

        except (FileNotFoundError, ValueError):
            raise
        except Exception as e:
            duration = (time.time() - start_time) * 1000
            self.logger.error("❌ 取消定时消息失败: %s, duration=%.2fms", e, duration)
            raise RuntimeError(f"取消定时消息失败: {e}") from e

    async def _send_message(self, message_id: str) -> None:
        """根据消息渠道类型分发发送 (经 NotificationService 统一派发)."""
        self._timers.pop(message_id, None)

        try:
            msg = await self.dao.get_by_message_id(message_id)
            if not msg or msg.status != MessageStatus.PENDING:
                self.logger.debug(
                    "消息%s状态已变更(%s), 跳过发送",
                    message_id,
                    msg.status if msg else "not_found",
                )
                return

            channel = msg.channel or self._default_channel
            self.logger.info("📤 开始发送消息: %s, channel=%s", message_id, channel)

            from src.core.notification import (
                get_notification_service,
                resolve_delivery,
            )

            delivery = await resolve_delivery(
                self.user_id, self.thread_id, self.agent_id, channel
            )
            if delivery is None:
                self.logger.error(
                    "❌ 渠道%s配置缺失或无效, 消息%s发送失败",
                    channel,
                    message_id,
                )
                await self.dao.update_status(message_id, MessageStatus.FAILED)
                return

            success = await get_notification_service().send(
                delivery,
                msg.message,
                subject=msg.subject or "定时消息提醒",
                html=msg.html_body,
            )

            now = datetime.now(UTC)
            if success:
                await self.dao.update_status(
                    message_id,
                    MessageStatus.SENT,
                    sent_at=now,
                )
            else:
                await self.dao.update_status(message_id, MessageStatus.FAILED)

        except Exception as e:
            self.logger.error("❌ 发送消息异常: %s, %s", message_id, e)
            try:
                await self.dao.update_status(message_id, MessageStatus.FAILED)
            except Exception as inner_e:
                self.logger.error(
                    "更新消息状态为failed也失败: %s, inner_error=%s",
                    message_id,
                    inner_e,
                )

    async def get_and_acknowledge_missed_messages(self) -> str:
        """获取missed消息并标记为notified, 返回格式化文本供prompt注入."""
        try:
            missed = await self.dao.get_missed_messages(
                self.user_id,
                self.thread_id,
                self.agent_id,
            )
            if not missed:
                return ""

            lines = []
            for msg in missed:
                time_str = msg.send_time.strftime("%Y-%m-%d %H:%M")
                desc = f" ({msg.description})" if msg.description else ""
                lines.append(f"- [原定 {time_str}] {msg.message}{desc}")

            message_ids = [msg.message_id for msg in missed]
            for mid in message_ids:
                await self.dao.update_status(mid, MessageStatus.NOTIFIED)

            self.logger.info("📋 已获取%d条missed消息并标记为notified", len(missed))
            return "\n".join(lines)

        except Exception as e:
            self.logger.error("❌ 获取missed消息失败: %s", e)
            return ""

    @override
    async def _check_service_health(self) -> dict[str, Any]:
        try:
            db_ok = await self.dao.health_check()
            return {
                "status": "healthy" if db_ok else "unhealthy",
                "database_connected": db_ok,
                "pending_timers": len(self._timers),
                "initialized": self._initialized,
                "error": None,
            }
        except Exception as e:
            self.logger.debug("定时消息服务健康检查失败: %s", e)
            return {
                "status": "unhealthy",
                "database_connected": False,
                "error": str(e),
            }


def discover_scheduled_message_dbs() -> list[tuple[str, str, str]]:
    """扫描数据目录, 发现所有scheduled_message.db文件.

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
                db_path = agent_dir / "database" / "scheduled_message.db"
                if db_path.exists():
                    results.append((user_dir.name, thread_dir.name, agent_dir.name))

    return results


def _load_default_service_config() -> dict[str, Any]:
    """从config.yaml读取scheduled_messenger的默认配置.

    Returns:
        包含 openclaw_defaults 等工具专属配置的字典(SMTP 已迁移到系统级 smtp 段).

    """
    try:
        from src.config.tools_config import get_config as get_tools_config

        tools_config = get_tools_config()
        messenger = tools_config.get_internal_tool_config("scheduled_messenger")
        if messenger is None:
            return {}
        return messenger.config
    except Exception:
        logger.warning("无法加载scheduled_messenger默认配置, 使用空配置", exc_info=True)
        return {}


async def get_scheduled_message_service(
    user_id: str,
    thread_id: str,
    agent_id: str,
    **config_kwargs: Any,
) -> ScheduledMessageService:
    """获取或创建ScheduledMessageService实例(带缓存).

    创建新实例时自动合并config.yaml中的默认配置,
    调用方传入的config_kwargs优先级更高.
    """
    cache_key = f"{user_id}:{thread_id}:{agent_id}"
    existing = _ServiceRegistry.get(cache_key)
    if existing:
        return existing

    from src.storage.dao.async_database_manager import (
        create_async_scheduled_message_db_manager,
    )

    db_manager = await create_async_scheduled_message_db_manager(
        user_id,
        thread_id,
        agent_id=agent_id,
    )

    defaults = _load_default_service_config()
    merged = {**defaults, **config_kwargs}
    # SMTP 已迁移到系统级配置(src.config.smtp_config); openclaw 渠道默认已统一到
    # openclaw.notification_defaults. 过滤旧残留避免透传给构造函数.
    merged.pop("smtp_config", None)
    merged.pop("openclaw_defaults", None)

    service = ScheduledMessageService(
        session_factory=db_manager.session_factory,
        user_id=user_id,
        thread_id=thread_id,
        agent_id=agent_id,
        **merged,
    )

    await service.initialize()

    _ServiceRegistry[cache_key] = service
    return service


async def initialize_all_scheduled_services() -> dict[str, int]:
    """启动时预加载所有用户的定时消息服务并注册定时器.

    扫描数据目录中所有scheduled_message.db,
    为每个用户创建ScheduledMessageService实例(带默认配置),
    注册asyncio定时器确保服务重启后定时消息不会丢失.

    Returns:
        统计信息: {"users": 用户数, "timers": 注册的定时器数}

    """
    try:
        entries = discover_scheduled_message_dbs()
    except Exception as e:
        logger.warning("扫描scheduled_message.db失败(非致命): %s", e)
        return {"users": 0, "timers": 0}

    if not entries:
        return {"users": 0, "timers": 0}

    total_timers = 0
    for user_id, thread_id, agent_id in entries:
        try:
            service = await get_scheduled_message_service(user_id, thread_id, agent_id)
            total_timers += len(service._timers)
        except Exception as e:
            logger.warning(
                "预加载定时消息服务失败: %s/%s/%s, %s",
                user_id,
                thread_id,
                agent_id,
                e,
            )

    logger.info(
        "⏰ 定时消息服务预加载完成: %d个用户, %d条定时器已注册",
        len(entries),
        total_timers,
    )
    return {"users": len(entries), "timers": total_timers}


async def shutdown_all_scheduled_services() -> None:
    """关闭并清空所有定时消息服务实例(应用关闭时调用).

    逐实例 shutdown 以释放 asyncio 定时器和在途发送任务,
    随后清空注册表避免进程级 dict 无限累积.
    底层 DB 连接由 close_all_db_managers() 统一关闭.
    """
    count = len(_ServiceRegistry)
    for service in _ServiceRegistry.values():
        try:
            await service.shutdown()
        except Exception as e:
            logger.warning("关闭定时消息服务异常(非致命): %s", e)
    _ServiceRegistry.clear()
    logger.info("已清空定时消息服务注册表(%d个实例)", count)
