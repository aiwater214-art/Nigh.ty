# Nigh.ty Multiplayer Prototype

This repository contains a lightweight agar.io-style prototype with a FastAPI-powered
multiplayer server and a pygame client. Players authenticate with a dashboard token,
create or join persistent worlds, and receive real-time updates over WebSockets.

## Requirements

- Python 3.11+
- Recommended: virtual environment (``python -m venv .venv``)
- Dependencies listed in `requirements.txt`
- Optional: Redis is **not** required—world snapshots are stored to JSON files by default

## Installation

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Copy the sample environment configuration:

```bash
cp .env.example .env
```

Edit `.env` to point at your dashboard token and desired snapshot directory.

## Running the Server

```bash
source .env
uvicorn server.network:create_app --factory --host 0.0.0.0 --port 8000
```

The server exposes:

- `POST /login` — exchange a dashboard token for a gameplay token
- `GET /worlds?token=...` — list active worlds
- `POST /worlds?token=...` — create a new world
- `WS /ws/world/{world_id}?token=...&player_name=...` — gameplay channel broadcasting world updates

World state is simulated at ~30 ticks per second. Snapshots are stored periodically under
`data/snapshots/` (configurable via `SNAPSHOT_DIR`).

## Running the Client

```bash
source .env
python -m client.main --server http://localhost:8000 --username YourName
```

Useful options:

- `--list` — list available worlds without starting the game
- `--create My World` — create and join a new world
- `--world <world_id>` — join a specific world
- `--ws ws://localhost:8000` — override the WebSocket endpoint if required

The client opens a pygame window, subscribes to WebSocket updates, and sends mouse-based
movement targets back to the server.

## Development Notes

- Snapshots are saved in-memory and flushed to disk using background tasks.
- The simulation spawns collectible food pellets and resolves simple collision rules for
  player cells (absorption when sufficiently larger).
- Worlds persist while the server is running; snapshots allow manual inspection or future
  restoration logic.

## Project Structure

```
server/
  network.py     # FastAPI app and WebSocket handling
  player.py      # Player dataclass
  world.py       # Simulation state, tick loop, persistence
client/
  api.py         # HTTP client helpers
  game.py        # pygame rendering and WebSocket client
  main.py        # Command-line entry point
```

Have fun exploring the night-time world!
