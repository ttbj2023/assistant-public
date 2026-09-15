"""日历 ICS 订阅路由 - 持久 token 鉴权的只读订阅 feed.

支持两种路径模式:
  - 直连: /v1/calendar/ics/{user_id}/{token}/calendar.ics
  - CF Tunnel 路径分发: /{env}/v1/calendar/ics/... (env 由 FILE_SERVER_BASE_URL 控制)

token 为随机持久串, 明文不落库 (只存 sha256), 撤销即失效.
按 user_id 分库, 跨用户 token 查询天然落空.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from fastapi import APIRouter, HTTPException
from fastapi.responses import PlainTextResponse

from src.auth.static_user_manager import get_static_user_manager
from src.calendar.ics_builder import build_calendar_feed
from src.core.datetime_utils import now_utc
from src.storage.service import create_calendar_service

logger = logging.getLogger(__name__)

router = APIRouter(tags=["calendar-ics"])

# feed 输出窗口: 过去 30 天 + 未来 180 天, 手机端按需展示
_FEED_PAST_DAYS = 30
_FEED_FUTURE_DAYS = 180

_ICS_PATH_SUFFIX = "{user_id}/{token}/calendar.ics"


@router.get(f"/v1/calendar/ics/{_ICS_PATH_SUFFIX}")
@router.get(f"/{{_env_prefix}}/v1/calendar/ics/{_ICS_PATH_SUFFIX}")
async def get_calendar_ics(
    user_id: str,
    token: str,
    _env_prefix: str | None = None,
) -> PlainTextResponse:
    """通过持久 token 拉取 ICS 订阅内容."""
    service = await create_calendar_service(user_id)
    subscription = await service.verify_subscription_token(token)
    if subscription is None:
        logger.warning("[calendar-ics] token 无效或已撤销: user=%s", user_id)
        raise HTTPException(status_code=401, detail="订阅链接无效或已撤销")

    now = now_utc()
    events = await service.list_events_in_range(
        now - timedelta(days=_FEED_PAST_DAYS),
        now + timedelta(days=_FEED_FUTURE_DAYS),
    )

    # 拉取时间戳节流更新, 失败不影响 feed 输出
    await service.touch_subscription(subscription)

    calendar_name = _resolve_calendar_name(user_id)
    feed = build_calendar_feed(events, calendar_name=calendar_name)

    logger.info(
        "[calendar-ics] feed 输出: user=%s events=%d bytes=%d",
        user_id,
        len(events),
        len(feed.encode("utf-8")),
    )
    return PlainTextResponse(
        content=feed,
        media_type="text/calendar; charset=utf-8",
    )


def _resolve_calendar_name(user_id: str) -> str:
    """取用户显示名作日历名, 失败回退默认值."""
    try:
        user = get_static_user_manager().get_user_by_id(user_id)
        if user is not None and user.display_name:
            return f"{user.display_name}的日程"
    except Exception as e:
        logger.warning("获取用户显示名失败: %s", e)
    return "Assistant 日程"
