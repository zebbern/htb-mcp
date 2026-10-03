"""API key and base-URL resolution for the HTB Agent Toolkit.

Token resolution order, first match wins:

1. ``HTB_API_KEY`` environment variable
2. ``HTB_API_TOKEN`` environment variable (legacy alias)
3. ``.env.local`` file — searched in the current working directory, then each
   parent directory up to the filesystem root, then the directory containing
   ``htb.py`` (this package's parent directory).

Nothing is ever written to disk: there is no token saving and no per-user
config directory. The toolkit is configured purely through environment
variables and ``.env.local``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_BASE_URL = "https://labs.hackthebox.com/api/v4"

# Keys extracted from .env.local, in priority order.
_DOTENV_KEYS = ("HTB_API_KEY", "HTB_API_TOKEN")


@dataclass(frozen=True)
class Config:
    base_url: str
    token: str


class ConfigError(RuntimeError):
    pass


def load_config(base_url: str | None = None) -> Config:
    return Config(
        base_url=resolve_base_url(base_url),
        token=load_token(),
    )


def resolve_base_url(base_url: str | None = None) -> str:
    return (base_url or os.environ.get("HTB_API_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")


def load_token() -> str:
    """Return the normalized API token. Thin wrapper over :func:`resolve_token`."""
    token, _source = resolve_token()
    return token


def resolve_token() -> tuple[str, str]:
    """Return ``(normalized token, source)`` without ever exposing the token.

    ``source`` is exactly one of ``"env:HTB_API_KEY"``, ``"env:HTB_API_TOKEN"``,
    or ``"file:<absolute path to the .env.local used>"`` — it identifies where
    the token came from (for diagnostics such as ``doctor``) without leaking
    any part of the token itself.
    """
    for key in _DOTENV_KEYS:
        value = os.environ.get(key)
        if value:
            return _normalize_token(value), f"env:{key}"

    env_file = _find_dotenv_local()
    if env_file is not None:
        value = _read_dotenv_key(env_file, _DOTENV_KEYS)
        if value:
            return _normalize_token(value), f"file:{env_file}"

    raise ConfigError(
        "No HTB API key found. Put HTB_API_KEY=<your-app-token> in .env.local "
        "(project root or beside htb.py), or export HTB_API_KEY. Create an App "
        "Token in your HTB profile settings: "
        "https://app.hackthebox.com/profile/settings"
    )


def _find_dotenv_local() -> Path | None:
    """Locate the nearest ``.env.local``.

    Search order: current working directory, each parent directory up to the
    filesystem root, then the directory containing ``htb.py`` (the parent of
    this package). First match wins.
    """
    cwd = Path.cwd()
    candidates = [cwd, *cwd.parents, Path(__file__).resolve().parent.parent]
    seen: set[Path] = set()
    for directory in candidates:
        try:
            directory = directory.resolve()
        except OSError:
            continue
        if directory in seen:
            continue
        seen.add(directory)
        candidate = directory / ".env.local"
        if candidate.is_file():
            return candidate
    return None


def _read_dotenv_key(path: Path, keys: tuple[str, ...]) -> str | None:
    """Minimal stdlib dotenv reader.

    Handles ``KEY=VALUE`` lines, blank lines, ``#`` comments, an optional
    ``export `` prefix, and single/double quotes around values. Returns the
    first non-empty value among ``keys`` (checked in file order), else None.
    """
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("export "):
            stripped = stripped[len("export "):].lstrip()
        if "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        if key.strip() not in keys:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        value = value.strip()
        if value:
            return value
    return None


def _normalize_token(value: str) -> str:
    token = value.strip()
    if not token:
        raise ConfigError("Empty API token.")

    if token.lower().startswith("authorization:"):
        token = token.split(":", 1)[1].strip()

    if token.lower().startswith("bearer "):
        token = token.split(None, 1)[1].strip()

    return token
