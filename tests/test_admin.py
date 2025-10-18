import asyncio

import pytest
from fastapi.testclient import TestClient

from app.core.events import CONFIG_CHANNEL, config_pubsub
from tests.conftest import TestingSessionLocal
from app.models import User


def _make_headers(client: TestClient, username: str, password: str) -> dict[str, str]:
    token_response = client.post(
        "/api/token",
        data={"username": username, "password": password},
        headers={"content-type": "application/x-www-form-urlencoded"},
    )
    assert token_response.status_code == 200
    token = token_response.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def _promote_admin(username: str) -> None:
    db = TestingSessionLocal()
    try:
        user = db.query(User).filter(User.username == username).first()
        assert user is not None
        user.is_admin = True
        db.add(user)
        db.commit()
    finally:
        db.close()


def test_non_admin_cannot_update_config(client: TestClient):
    response = client.post(
        "/api/register",
        json={
            "username": "bob",
            "email": "bob@example.com",
            "password": "secret",
            "full_name": "Bob",
        },
    )
    assert response.status_code == 201

    headers = _make_headers(client, "bob", "secret")
    patch = client.patch(
        "/api/admin/config",
        json={"tick_rate": 55.0},
        headers=headers,
    )
    assert patch.status_code == 403


@pytest.mark.asyncio
async def test_admin_config_update_publishes_event(client: TestClient):
    response = client.post(
        "/api/register",
        json={
            "username": "carol",
            "email": "carol@example.com",
            "password": "secret",
            "full_name": "Carol",
        },
    )
    assert response.status_code == 201
    _promote_admin("carol")

    headers = _make_headers(client, "carol", "secret")

    async def wait_for_event() -> dict:
        async with config_pubsub.subscribe(CONFIG_CHANNEL) as queue:
            payload = await queue.get()
            return payload

    listener = asyncio.create_task(wait_for_event())

    patch_response = await asyncio.to_thread(
        client.patch,
        "/api/admin/config",
        json={
            "tick_rate": 42.0,
            "width": 1500.0,
            "food_count": 350,
        },
        headers=headers,
    )
    assert patch_response.status_code == 200

    event_payload = await asyncio.wait_for(listener, timeout=2.0)
    assert event_payload["tick_rate"] == pytest.approx(42.0)
    assert event_payload["width"] == pytest.approx(1500.0)
    assert int(event_payload["food_count"]) == 350

    config_response = client.get("/api/config")
    assert config_response.status_code == 200
    config = config_response.json()
    assert config["tick_rate"] == pytest.approx(42.0)
    assert config["width"] == pytest.approx(1500.0)
    assert config["food_count"] == 350
