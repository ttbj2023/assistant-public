"""graph_containers 单元测试 (专用清单/日历 ensure).

Mock 策略: httpx.MockTransport 路由 GET/POST, 断言不存在时创建/存在时不创建.
"""

from __future__ import annotations

import httpx
import pytest

from src.sync.graph_containers import ensure_calendar, ensure_todo_list
from src.sync.msgraph_client import MSGraphClient

_GRAPH_BASE = "https://graph.microsoft.com/v1.0"


def _client(
    todo_lists: list[dict],
    calendars: list[dict],
    created: dict,
) -> MSGraphClient:
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url == f"{_GRAPH_BASE}/me/todo/lists":
            if request.method == "POST":
                created.setdefault("todo_posts", []).append(request.read().decode())
                return httpx.Response(
                    201,
                    json={"id": "lid-new", "displayName": "Assistant"},
                    request=request,
                )
            return httpx.Response(200, json={"value": todo_lists}, request=request)
        if url == f"{_GRAPH_BASE}/me/calendars":
            if request.method == "POST":
                created.setdefault("cal_posts", []).append(request.read().decode())
                return httpx.Response(
                    201,
                    json={"id": "cal-new", "name": "Assistant"},
                    request=request,
                )
            return httpx.Response(200, json={"value": calendars}, request=request)
        return httpx.Response(500, json={"error": "unexpected"}, request=request)

    return MSGraphClient(
        tokens={"access_token": "at", "refresh_token": "rt"},
        http=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )


class TestEnsureTodoList:
    """ensure_todo_list 测试."""

    async def test_清单已存在_返回id且不创建(self):
        created: dict = {}
        client = _client(
            todo_lists=[{"id": "lid-1", "displayName": "Assistant"}],
            calendars=[],
            created=created,
        )

        list_id, created_flag = await ensure_todo_list(client, name="Assistant")

        assert list_id == "lid-1"
        assert created_flag is False
        assert "todo_posts" not in created

    async def test_清单不存在_创建并返回新id(self):
        created: dict = {}
        client = _client(todo_lists=[], calendars=[], created=created)

        list_id, created_flag = await ensure_todo_list(client, name="Assistant")

        assert list_id == "lid-new"
        assert created_flag is True
        assert '"displayName":"Assistant"' in created["todo_posts"][0]

    async def test_创建失败_抛出RuntimeError(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "POST":
                return httpx.Response(
                    400,
                    json={"error": {"message": "bad"}},
                    request=request,
                )
            return httpx.Response(200, json={"value": []}, request=request)

        client = MSGraphClient(
            tokens={"access_token": "at", "refresh_token": "rt"},
            http=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        )

        with pytest.raises(RuntimeError, match=r"创建.*清单"):
            await ensure_todo_list(client, name="Assistant")


class TestEnsureCalendar:
    """ensure_calendar 测试."""

    async def test_日历已存在_返回id且不创建(self):
        created: dict = {}
        client = _client(
            todo_lists=[],
            calendars=[{"id": "cal-1", "name": "Assistant"}],
            created=created,
        )

        cal_id, created_flag = await ensure_calendar(client, name="Assistant")

        assert cal_id == "cal-1"
        assert created_flag is False
        assert "cal_posts" not in created

    async def test_日历不存在_创建并返回新id(self):
        created: dict = {}
        client = _client(todo_lists=[], calendars=[], created=created)

        cal_id, created_flag = await ensure_calendar(client, name="Assistant")

        assert cal_id == "cal-new"
        assert created_flag is True
        assert '"name":"Assistant"' in created["cal_posts"][0]
