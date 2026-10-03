"""Pure helpers for interpreting HTB Labs machine-list payloads.

These functions have no I/O and no state; they only reshape the JSON the API
returns. Keeping them here lets ``MachineService`` stay focused on API calls.
"""

from __future__ import annotations

from typing import Any


def machine_rows(payload: Any) -> list[dict[str, Any]]:
    return [machine_row(item) for item in extract_items(payload) if isinstance(item, dict)]


def profile_summary(payload: Any) -> dict[str, Any]:
    """Compact projection of a machine profile payload's ``info`` block.

    Keeps only the scalar fields an agent needs; maker/maker2 objects are
    flattened to their names (avatar/url junk dropped), and tags are reduced
    to names when present. Based on the live ``GET /machine/profile/<id>``
    payload (keys: id, name, os, difficultyText, points, static_points,
    stars, retired, free, release, maker, maker2, user_owns_count,
    root_owns_count, recommended, tags).
    """
    source = payload.get("info") if isinstance(payload, dict) else None
    if not isinstance(source, dict):
        return {"info": None}
    info: dict[str, Any] = {
        "id": source.get("id"),
        "name": source.get("name"),
        "os": source.get("os"),
        "difficulty": source.get("difficultyText")
        or source.get("difficulty_text")
        or source.get("difficulty"),
        "points": source.get("points") or source.get("static_points"),
        "stars": source.get("stars"),
        "retired": source.get("retired"),
        "free": source.get("free"),
        "release": source.get("release") or source.get("release_date"),
        "maker": _name_of(source.get("maker")),
        "maker2": _name_of(source.get("maker2")),
        "user_owns": source.get("user_owns_count"),
        "system_owns": source.get("root_owns_count"),
        "recommended": source.get("recommended"),
    }
    tags = _tag_names(source.get("tags"))
    if tags:
        info["tags"] = tags
    return {"info": info}


def _name_of(value: Any) -> Any:
    if isinstance(value, dict):
        return value.get("name")
    return value


def _tag_names(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    names: list[str] = []
    for item in value:
        if isinstance(item, dict) and item.get("name"):
            names.append(str(item["name"]))
        elif isinstance(item, str):
            names.append(item)
    return names


def machine_row(item: dict[str, Any]) -> dict[str, Any]:
    play_info = item.get("playInfo")
    if not isinstance(play_info, dict):
        play_info = {}
    return {
        "id": item.get("id"),
        "name": item.get("name"),
        "os": item.get("os"),
        "difficulty": item.get("difficultyText") or item.get("difficulty_text") or item.get("difficulty"),
        "points": item.get("points") or item.get("static_points"),
        "active": item.get("isActive") if item.get("isActive") is not None else play_info.get("isActive"),
        "spawned": item.get("isSpawned") if item.get("isSpawned") is not None else play_info.get("isSpawned"),
        "free": item.get("free"),
    }


def extract_items(payload: Any) -> list[Any]:
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return []
    for key in ("data", "message", "machines", "info"):
        value = payload.get(key)
        if isinstance(value, list):
            return value
        if isinstance(value, dict):
            nested = value.get("data")
            if isinstance(nested, list):
                return nested
    return []


def has_next_page(payload: Any, current_page: int) -> bool:
    if not isinstance(payload, dict):
        return False

    links = payload.get("links")
    if isinstance(links, dict) and "next" in links:
        return bool(links.get("next"))

    meta = payload.get("meta")
    if isinstance(meta, dict):
        last_page = to_int(meta.get("last_page"))
        page = to_int(meta.get("current_page")) or current_page
        if last_page is not None:
            return page < last_page

    return bool(extract_items(payload))


def page_signature(items: list[Any]) -> tuple[Any, ...]:
    signature: list[Any] = []
    for item in items:
        if isinstance(item, dict):
            signature.append(item.get("id", item.get("name")))
        else:
            signature.append(repr(item))
    return tuple(signature)


def academy_module_names(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    names: list[str] = []
    for item in value:
        if isinstance(item, dict) and item.get("name"):
            names.append(str(item["name"]))
    return names


def text(value: Any) -> str:
    return str(value).casefold()


def to_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
