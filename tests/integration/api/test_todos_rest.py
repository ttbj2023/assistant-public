"""TODO REST API 集成测试.

验证 /v1/todos 端点与认证、用户级统一存储的完整链路.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from src.api.fastapi_app import app

_HEADERS = {"Authorization": "Bearer sk-project-test_user-test_thread-e2e123456789"}


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def _todo_payload(**overrides) -> dict:
    payload = {
        "title": "REST 测试任务",
        "priority": "high",
        "due_date": "2026-10-01T10:00:00Z",
    }
    payload.update(overrides)
    return payload


@pytest.mark.integration
class TestTodosRestApi:
    def test_create_and_list_todos(self, client: TestClient):
        resp = client.post("/v1/todos", headers=_HEADERS, json=_todo_payload())
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["title"] == "REST 测试任务"
        todo_id = data["id"]

        resp = client.get("/v1/todos", headers=_HEADERS)
        assert resp.status_code == 200
        assert any(t["id"] == todo_id for t in resp.json()["data"])

    def test_create_missing_title_returns_400(self, client: TestClient):
        payload = _todo_payload()
        payload.pop("title")
        resp = client.post("/v1/todos", headers=_HEADERS, json=payload)
        assert resp.status_code == 400

    def test_list_filter_by_status(self, client: TestClient):
        resp = client.post("/v1/todos", headers=_HEADERS, json=_todo_payload())
        assert resp.status_code == 200

        resp = client.get(
            "/v1/todos",
            headers=_HEADERS,
            params={"status": "completed"},
        )
        assert resp.status_code == 200
        assert all(t["status"] == "completed" for t in resp.json()["data"])

    def test_update_todo(self, client: TestClient):
        resp = client.post("/v1/todos", headers=_HEADERS, json=_todo_payload())
        todo_id = resp.json()["id"]

        resp = client.patch(
            f"/v1/todos/{todo_id}",
            headers=_HEADERS,
            json={"title": "改名任务", "status": "completed"},
        )
        assert resp.status_code == 200
        assert resp.json()["title"] == "改名任务"
        assert resp.json()["status"] == "completed"

    def test_delete_todo(self, client: TestClient):
        resp = client.post("/v1/todos", headers=_HEADERS, json=_todo_payload())
        todo_id = resp.json()["id"]

        resp = client.delete(f"/v1/todos/{todo_id}", headers=_HEADERS)
        assert resp.status_code == 200

        resp = client.get(f"/v1/todos/{todo_id}", headers=_HEADERS)
        assert resp.status_code == 404

    def test_get_missing_returns_404(self, client: TestClient):
        resp = client.get("/v1/todos/999999", headers=_HEADERS)
        assert resp.status_code == 404

    def test_unauthenticated_returns_401(self, client: TestClient):
        resp = client.get("/v1/todos")
        assert resp.status_code == 401
