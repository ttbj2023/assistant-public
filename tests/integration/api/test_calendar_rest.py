"""日历 REST API 集成测试.

验证 /v1/calendar/* 端点与认证中间件、用户级存储的完整链路.
真实 FastAPI app + calendar.db, 认证走测试 API key.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from src.api.fastapi_app import app
from src.storage.dao.async_database_manager import close_all_db_managers

_HEADERS = {"Authorization": "Bearer sk-project-test_user-test_thread-e2e123456789"}


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(autouse=True)
async def _cleanup_db():
    yield
    await close_all_db_managers()


def _event_payload(**overrides) -> dict:
    start = datetime(2026, 10, 1, 2, 0, tzinfo=UTC)
    payload = {
        "title": "REST 测试日程",
        "start_time": start.isoformat(),
        "end_time": (start + timedelta(hours=1)).isoformat(),
    }
    payload.update(overrides)
    return payload


@pytest.mark.integration
class TestCalendarEventsCrud:
    """日程 CRUD 端点."""

    def test_create_and_get_event(self, client: TestClient):
        resp = client.post("/v1/calendar/events", headers=_HEADERS, json=_event_payload())
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["title"] == "REST 测试日程"
        assert data["event_uid"].startswith("evt_")
        event_id = data["id"]

        resp = client.get(f"/v1/calendar/events/{event_id}", headers=_HEADERS)
        assert resp.status_code == 200
        assert resp.json()["id"] == event_id

    def test_create_event_missing_title_returns_400(self, client: TestClient):
        """全局异常处理器把 RequestValidationError 统一转 400."""
        payload = _event_payload()
        payload.pop("title")
        resp = client.post("/v1/calendar/events", headers=_HEADERS, json=payload)
        assert resp.status_code == 400

    def test_create_event_bad_time_range_returns_422(self, client: TestClient):
        start = datetime(2026, 10, 1, 2, 0, tzinfo=UTC)
        resp = client.post(
            "/v1/calendar/events",
            headers=_HEADERS,
            json=_event_payload(
                start_time=(start + timedelta(hours=2)).isoformat(),
                end_time=start.isoformat(),
            ),
        )
        assert resp.status_code == 422

    def test_list_events_with_time_window(self, client: TestClient):
        client.post("/v1/calendar/events", headers=_HEADERS, json=_event_payload())

        resp = client.get(
            "/v1/calendar/events",
            headers=_HEADERS,
            params={
                "start_from": "2026-09-30T00:00:00Z",
                "start_to": "2026-10-02T00:00:00Z",
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["object"] == "list"
        assert any(e["title"] == "REST 测试日程" for e in body["data"])

    def test_list_events_expand_recurring(self, client: TestClient):
        """expand=true 展开重复日程为具体实例."""
        start = datetime(2026, 10, 5, 2, 0, tzinfo=UTC)  # 周一
        client.post(
            "/v1/calendar/events",
            headers=_HEADERS,
            json=_event_payload(
                title="每周例会",
                start_time=start.isoformat(),
                end_time=(start + timedelta(hours=1)).isoformat(),
                recurrence_rule="FREQ=WEEKLY;INTERVAL=1",
            ),
        )

        resp = client.get(
            "/v1/calendar/events",
            headers=_HEADERS,
            params={
                "start_from": "2026-10-01T00:00:00Z",
                "start_to": "2026-11-01T00:00:00Z",
                "expand": "true",
            },
        )
        assert resp.status_code == 200
        instances = [e for e in resp.json()["data"] if e["title"] == "每周例会"]
        assert len(instances) == 4  # 10/5, 10/12, 10/19, 10/26
        assert instances[0]["is_recurring_instance"] is True

    def test_update_event(self, client: TestClient):
        resp = client.post("/v1/calendar/events", headers=_HEADERS, json=_event_payload())
        event_id = resp.json()["id"]

        resp = client.patch(
            f"/v1/calendar/events/{event_id}",
            headers=_HEADERS,
            json={"title": "改名后的日程"},
        )
        assert resp.status_code == 200
        assert resp.json()["title"] == "改名后的日程"

    def test_delete_event(self, client: TestClient):
        resp = client.post("/v1/calendar/events", headers=_HEADERS, json=_event_payload())
        event_id = resp.json()["id"]

        resp = client.delete(f"/v1/calendar/events/{event_id}", headers=_HEADERS)
        assert resp.status_code == 200

        resp = client.get(f"/v1/calendar/events/{event_id}", headers=_HEADERS)
        assert resp.status_code == 404

    def test_get_missing_event_returns_404(self, client: TestClient):
        resp = client.get("/v1/calendar/events/999999", headers=_HEADERS)
        assert resp.status_code == 404

    def test_unauthenticated_returns_401(self, client: TestClient):
        resp = client.get("/v1/calendar/events")
        assert resp.status_code == 401


@pytest.mark.integration
class TestCalendarSubscriptionApi:
    """订阅管理端点."""

    def test_create_subscription_returns_url_and_plaintext(self, client: TestClient):
        resp = client.post("/v1/calendar/subscription", headers=_HEADERS)
        assert resp.status_code == 200
        data = resp.json()
        assert data["token"].startswith("cal_")
        assert data["subscription_id"].startswith("sub_")
        assert f"/v1/calendar/ics/test_user/{data['token']}/calendar.ics" in data[
            "url"
        ]

    def test_subscription_feed_roundtrip(self, client: TestClient):
        """REST 创建订阅 + 创建日程 → ICS feed 包含该日程."""
        client.post("/v1/calendar/events", headers=_HEADERS, json=_event_payload())
        resp = client.post("/v1/calendar/subscription", headers=_HEADERS)
        url = resp.json()["url"]

        feed_resp = client.get(url)
        assert feed_resp.status_code == 200
        assert "REST 测试日程" in feed_resp.text

    def test_revoke_subscription_invalidates_feed(self, client: TestClient):
        resp = client.post("/v1/calendar/subscription", headers=_HEADERS)
        data = resp.json()

        resp = client.delete(
            f"/v1/calendar/subscription/{data['subscription_id']}",
            headers=_HEADERS,
        )
        assert resp.status_code == 200

        feed_resp = client.get(data["url"])
        assert feed_resp.status_code == 401

    def test_list_subscriptions_hides_token(self, client: TestClient):
        """订阅列表不泄露 token 明文 (只返回标识与状态)."""
        client.post("/v1/calendar/subscription", headers=_HEADERS)
        resp = client.get("/v1/calendar/subscription", headers=_HEADERS)
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["data"]) >= 1
        for item in body["data"]:
            assert "token" not in item
            assert "token_hash" not in item
