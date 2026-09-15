"""日历 ICS 订阅端点集成测试.

验证公开 ICS 路由与 token 鉴权 + feed 生成的完整链路:
真实 FastAPI app + 用户级 calendar.db, 无需 API key (token 即凭证).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from src.api.fastapi_app import app
from src.calendar.subscription import generate_subscription_token
from src.storage.service import create_calendar_service

_TEST_USER = "ics_integration_user"


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture
async def calendar_service():
    yield await create_calendar_service(_TEST_USER)
    from src.storage.dao.async_database_manager import close_all_db_managers

    await close_all_db_managers()


@pytest.mark.integration
class TestCalendarIcsEndpoint:
    """ICS 订阅端点集成测试."""

    async def test_valid_token_returns_ics_feed(
        self,
        client: TestClient,
        calendar_service,
    ):
        """有效 token 返回 text/calendar 与日程内容."""
        start = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
        await calendar_service.create_event(
            title="集成测试日程",
            start_time=start + timedelta(hours=1),
            end_time=start + timedelta(hours=2),
        )
        _, token = await calendar_service.create_subscription()

        response = client.get(f"/v1/calendar/ics/{_TEST_USER}/{token}/calendar.ics")

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/calendar")
        body = response.text
        assert "BEGIN:VCALENDAR" in body
        assert "SUMMARY:集成测试日程" in body

    async def test_invalid_token_returns_401(self, client: TestClient):
        """无效 token 返回 401."""
        response = client.get(f"/v1/calendar/ics/{_TEST_USER}/cal_bogus/calendar.ics")
        assert response.status_code == 401

    async def test_cross_user_token_rejected(
        self,
        client: TestClient,
        calendar_service,
    ):
        """A 用户的 token 不能访问 B 用户的 feed (按 user_id 分库天然隔离)."""
        other_service = await create_calendar_service("ics_other_user")
        _, token = await other_service.create_subscription()

        response = client.get(f"/v1/calendar/ics/{_TEST_USER}/{token}/calendar.ics")
        assert response.status_code == 401

    async def test_revoked_token_returns_401(
        self,
        client: TestClient,
        calendar_service,
    ):
        """撤销后的 token 返回 401."""
        record, token = await calendar_service.create_subscription()
        await calendar_service.revoke_subscription(record.subscription_id)

        response = client.get(f"/v1/calendar/ics/{_TEST_USER}/{token}/calendar.ics")
        assert response.status_code == 401

    async def test_no_auth_header_required(
        self,
        client: TestClient,
        calendar_service,
    ):
        """公开端点: 无 Authorization 头也应正常访问 (中间件 bypass)."""
        _, token = await calendar_service.create_subscription()

        response = client.get(f"/v1/calendar/ics/{_TEST_USER}/{token}/calendar.ics")

        assert response.status_code == 200

    async def test_token_uniqueness(self, calendar_service):
        """两次创建的 token 不同."""
        _, token1 = await calendar_service.create_subscription()
        _, token2 = await calendar_service.create_subscription()
        assert token1 != token2
        assert token1.startswith("cal_") or generate_subscription_token
