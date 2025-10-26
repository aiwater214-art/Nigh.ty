"""Tests for the realtime server configuration endpoint."""

import os

os.environ.setdefault("FASTAPI_USE_PYDANTIC_V1", "1")

import httpx
import pytest
from contextlib import asynccontextmanager

from app.crud import get_gameplay_config, update_gameplay_config
from client.api import ServerClient


from server.network import create_app
from tests.conftest import TestingSessionLocal



@asynccontextmanager
async def lifespan_client(app):
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            yield client

@pytest.mark.asyncio
async def test_config_endpoint_and_client(monkeypatch, tmp_path):
    monkeypatch.setenv("DASHBOARD_API_KEY", "test-dashboard-key")
    monkeypatch.setenv("SNAPSHOT_DIR", str(tmp_path))

    app = create_app()

    db = TestingSessionLocal()
    try:
        update_gameplay_config(
            db,
            width=2048.0,
            height=1024.0,
            tick_rate=45.0,
            food_count=350,
            snapshot_interval=7.5,
        )
        expected = get_gameplay_config(db).as_dict()
    finally:
        db.close()

    async with lifespan_client(app) as http_client:
        response = await http_client.get("/config")
        assert response.status_code == 200
        assert response.json() == expected

    async with lifespan_client(app) as http_client:
        client = ServerClient("http://testserver")
        await client._client.aclose()
        client._client = http_client
        config = await client.get_config()
        assert config == expected
