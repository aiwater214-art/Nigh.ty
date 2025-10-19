import asyncio
import sys
import types

# Provide a lightweight pygame stub so the client module can be imported without the
# optional dependency installed in the test environment.
pygame_stub = types.ModuleType("pygame")
pygame_stub.QUIT = object()
pygame_stub.init = lambda: None
pygame_stub.display = types.SimpleNamespace(
    set_mode=lambda size: types.SimpleNamespace(fill=lambda color: None),
    set_caption=lambda title: None,
    flip=lambda: None,
)
pygame_stub.time = types.SimpleNamespace(Clock=lambda: types.SimpleNamespace(tick=lambda fps: None))
pygame_stub.event = types.SimpleNamespace(get=lambda: [])
pygame_stub.draw = types.SimpleNamespace(circle=lambda *args, **kwargs: None)
pygame_stub.mouse = types.SimpleNamespace(get_focused=lambda: False, get_pos=lambda: (0, 0))

sys.modules.setdefault("pygame", pygame_stub)

from client import game as game_module  # noqa: E402  (import after pygame stub setup)
from client.game import GameClient  # noqa: E402


class _DummyWebSocket:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


def test_run_encodes_player_name(monkeypatch):
    captured_url: dict[str, str] = {}

    def fake_connect(url):
        captured_url["url"] = url
        return _DummyWebSocket()

    async def fake_render_loop(self, websocket):
        self._running = False

    async def fake_receiver(self, websocket):
        return None

    monkeypatch.setattr(game_module.websockets, "connect", fake_connect)
    monkeypatch.setattr(GameClient, "_render_loop", fake_render_loop)
    monkeypatch.setattr(GameClient, "_receiver", fake_receiver)

    async def run_client() -> None:
        client = GameClient(
            "ws://example.com",
            "world-1",
            "token/with special",
            "Alice & Bob",
        )

        await client.run()

    asyncio.run(run_client())

    assert (
        captured_url["url"]
        == "ws://example.com/ws/world/world-1?token=token%2Fwith+special&player_name=Alice+%26+Bob"
    )
