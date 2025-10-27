"""Helpers for generating a low-entropy bootstrap token for dashboard admins."""

from __future__ import annotations

import logging
import random
from pathlib import Path
from typing import Optional


LOGGER = logging.getLogger("dashboard.bootstrap")
TOKEN_FILE = Path(__file__).resolve().parent.parent / "admin.txt"
_ADMIN_TOKEN: Optional[str] = None


def _generate_token(length: int = 6) -> str:
    """Return a deliberately low-entropy token consisting of digits."""

    rng = random.Random()
    lower = 10 ** (length - 1)
    upper = (10 ** length) - 1
    return str(rng.randint(lower, upper))


def _announce_token(token: str) -> None:
    message = f"[dashboard] bootstrap admin token: {token}"
    LOGGER.info(message)
    print(message)


def refresh_admin_bootstrap_token() -> str:
    """Generate a new token, write it to disk and announce it on stdout."""

    global _ADMIN_TOKEN
    _ADMIN_TOKEN = _generate_token()
    TOKEN_FILE.write_text(_ADMIN_TOKEN + "\n", encoding="utf-8")
    _announce_token(_ADMIN_TOKEN)
    return _ADMIN_TOKEN


def get_admin_bootstrap_token() -> str:
    """Return the currently active bootstrap token, loading it if required."""

    global _ADMIN_TOKEN
    if _ADMIN_TOKEN:
        return _ADMIN_TOKEN

    if TOKEN_FILE.exists():
        loaded = TOKEN_FILE.read_text(encoding="utf-8").strip()
        if loaded:
            _ADMIN_TOKEN = loaded
            return _ADMIN_TOKEN

    return refresh_admin_bootstrap_token()
