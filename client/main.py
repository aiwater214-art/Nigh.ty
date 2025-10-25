"""Command line entry point for the multiplayer client."""
from __future__ import annotations

import argparse
import asyncio
import os
from typing import Optional

from dotenv import load_dotenv

from .api import ServerClient
from .game import GameClient


def http_to_ws(url: str) -> str:
    if url.startswith("https://"):
        return "wss://" + url[len("https://") :]
    if url.startswith("http://"):
        return "ws://" + url[len("http://") :]
    return url


load_dotenv()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Connect to a Nigh.ty game server")
    parser.add_argument("--server", default=os.getenv("SERVER_BASE_URL", "http://localhost:8000"), help="Base HTTP URL of the server")
    parser.add_argument("--ws", default=os.getenv("SERVER_WS_URL"), help="Override WebSocket URL (defaults to server converted to ws://)")
    parser.add_argument("--username", default=os.getenv("PLAYER_NAME", "guest"), help="Player display name")
    parser.add_argument("--dashboard-token", default=os.getenv("DASHBOARD_TOKEN"), help="Dashboard authentication token")
    parser.add_argument("--world", help="ID of the world to join")
    parser.add_argument("--create", help="Create a new world with this name and join it")
    parser.add_argument("--list", action="store_true", help="List worlds and exit")
    return parser


async def async_main(args: argparse.Namespace) -> None:
    if not args.dashboard_token:
        raise SystemExit("--dashboard-token or DASHBOARD_TOKEN environment variable is required")

    async with ServerClient(args.server) as client:
        session = await client.login(args.username, args.dashboard_token)
        print(f"Authenticated as {session.username}")

        worlds = await client.list_worlds(session.token)
        if args.list:
            for world in worlds:
                print(f"- {world['id']} :: {world['name']} ({world['players']} players)")
            return

        world_id: Optional[str] = args.world
        if args.create:
            world = await client.create_world(args.create, session.token)
            world_id = world["id"]
            print(f"Created world {world['name']} ({world_id})")
        elif not world_id:
            if worlds:
                world_id = worlds[0]["id"]
                print(f"Joining existing world {world_id}")
            else:
                world = await client.create_world("Starter World", session.token)
                world_id = world["id"]
                print(f"Created default world {world_id}")

        if not world_id:
            raise SystemExit("No world available to join")

        config = await client.get_config()
        ws_url = args.ws or http_to_ws(args.server)
        game = GameClient(ws_url, world_id, session.token, session.username, initial_config=config)
        await game.run()


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    asyncio.run(async_main(args))


if __name__ == "__main__":  # pragma: no cover
    main()
