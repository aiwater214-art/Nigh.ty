"""HTTP helpers for interacting with the Nigh.ty server."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List

import httpx


@dataclass
class AuthSession:
    token: str
    username: str


class ServerClient:
    def __init__(self, base_url: str) -> None:
        self._base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(base_url=self._base_url, timeout=10.0)

    async def close(self) -> None:
        await self._client.aclose()

    async def login(self, username: str, password: str) -> AuthSession:
        response = await self._client.post(
            "/login",
            json={"username": username, "password": password},
        )
        response.raise_for_status()
        data = response.json()
        return AuthSession(token=data["token"], username=data["username"])

    async def list_worlds(self, token: str) -> List[Dict[str, Any]]:
        response = await self._client.get("/worlds", params={"token": token})
        response.raise_for_status()
        return response.json()

    async def create_world(self, name: str, token: str) -> Dict[str, Any]:
        response = await self._client.post(
            "/worlds",
            params={"token": token},
            json={"name": name},
        )
        response.raise_for_status()
        return response.json()

    async def get_config(self) -> Dict[str, Any]:
        response = await self._client.get("/config")
        response.raise_for_status()
        return response.json()

    async def __aenter__(self) -> "ServerClient":
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.close()
