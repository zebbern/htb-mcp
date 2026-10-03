"""Tiny JSON-file cache for machine- and challenge-list payloads.

Machine and challenge lists are large and change slowly, so
``MachineService`` and ``ChallengeService`` cache them in
``<toolkit root>/.cache/`` (the directory containing ``htb.py``). One file
per cache key: ``sha1("<namespace>:<key>").json`` holding
``{"time": <epoch>, "payload": ...}``.

Caveat: cached rows can carry stale ``active``/``spawned`` flags — a
machine's live session state changes far faster than the list metadata.
``machine active`` is always live and never cached.

Control: the env var ``HTB_CACHE_TTL`` sets the time-to-live in seconds
(default 3600); a value that parses to ``0`` disables the cache entirely, and
the ``--no-cache`` flag bypasses it per invocation.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any

CACHE_DIR = Path(__file__).resolve().parent.parent / ".cache"

DEFAULT_TTL = 3600


def enabled() -> bool:
    """False only when ``HTB_CACHE_TTL`` explicitly parses to 0."""
    return ttl_seconds() != 0


def ttl_seconds() -> int:
    """Cache TTL in seconds: ``HTB_CACHE_TTL`` when set (invalid -> default)."""
    raw = os.environ.get("HTB_CACHE_TTL")
    if raw is None:
        return DEFAULT_TTL
    try:
        return int(raw.strip())
    except ValueError:
        return DEFAULT_TTL


def get(namespace: str, key: str) -> Any | None:
    """Return the cached payload for ``namespace``/``key``, or None on a miss.

    A miss is: cache disabled, no file, a corrupt/unreadable file, or an entry
    older than the TTL.
    """
    if not enabled():
        return None
    try:
        raw = json.loads(_path(namespace, key).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(raw, dict):
        return None
    when = raw.get("time")
    if not isinstance(when, (int, float)):
        return None
    if time.time() - when >= ttl_seconds():
        return None
    return raw.get("payload")


def put(namespace: str, key: str, payload: Any) -> None:
    """Best-effort write of ``payload``; never raises on ``OSError``."""
    if not enabled():
        return
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        _path(namespace, key).write_text(
            json.dumps({"time": time.time(), "payload": payload}, ensure_ascii=False),
            encoding="utf-8",
        )
    except OSError:
        pass


def _path(namespace: str, key: str) -> Path:
    digest = hashlib.sha1(f"{namespace}:{key}".encode("utf-8")).hexdigest()
    return CACHE_DIR / f"{digest}.json"
