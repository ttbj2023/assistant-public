"""Graph 专用容器 ensure (To Do 清单 / Outlook 子日历).

幂等: 每次按显示名查, 存在直接复用, 不存在创建; 用户手动删掉容器后
下一轮 ensure 会自动重建 (自愈). 返回 (id, created).
"""

from __future__ import annotations

import logging

from src.sync.msgraph_client import MSGraphClient

logger = logging.getLogger(__name__)


async def ensure_todo_list(
    client: MSGraphClient,
    *,
    name: str,
) -> tuple[str, bool]:
    """确保专用 To Do 清单存在.

    Args:
        client: 已授权的 Graph 客户端
        name: 清单显示名

    Returns:
        (清单ID, 是否本次新建)

    Raises:
        RuntimeError: 查询或创建失败

    """
    status, data = await client.request("GET", "/me/todo/lists")
    if status != 200 or data is None:
        raise RuntimeError(f"查询 TODO 清单失败 HTTP {status}: {data}")

    for item in data.get("value", []):
        if item.get("displayName") == name:
            return str(item["id"]), False

    status, data = await client.request(
        "POST",
        "/me/todo/lists",
        json_body={"displayName": name},
    )
    if status != 201 or data is None:
        raise RuntimeError(f"创建 TODO 清单失败 HTTP {status}: {data}")
    logger.info("已创建专用 TODO 清单: %s", name)
    return str(data["id"]), True


async def ensure_calendar(
    client: MSGraphClient,
    *,
    name: str,
) -> tuple[str, bool]:
    """确保专用 Outlook 子日历存在.

    Args:
        client: 已授权的 Graph 客户端
        name: 日历名

    Returns:
        (日历ID, 是否本次新建)

    Raises:
        RuntimeError: 查询或创建失败

    """
    status, data = await client.request("GET", "/me/calendars")
    if status != 200 or data is None:
        raise RuntimeError(f"查询日历失败 HTTP {status}: {data}")

    for item in data.get("value", []):
        if item.get("name") == name:
            return str(item["id"]), False

    status, data = await client.request(
        "POST",
        "/me/calendars",
        json_body={"name": name},
    )
    if status != 201 or data is None:
        raise RuntimeError(f"创建日历失败 HTTP {status}: {data}")
    logger.info("已创建专用日历: %s", name)
    return str(data["id"]), True


__all__ = ["ensure_calendar", "ensure_todo_list"]
