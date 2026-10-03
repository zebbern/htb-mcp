"""Challenge API operations.

``ChallengeService`` is the single entry point for challenge workflows, mirroring
:class:`~htb_agent.services.machines.MachineService`. Verified live against the
v4 Labs API (the v5 base has no challenge routes):

* ``GET /challenge/list`` -> ``{"challenges": [...]}`` active challenges.
* ``GET /challenge/list/retired`` -> same shape, retired challenges
  (``state`` is ``"retired"`` or ``"retired_free"``).
* ``GET /challenge/info/<id-or-name>`` -> ``{"challenge": {...}}``; the lookup
  accepts ids and (case-insensitively) names and url_names.
* ``GET /challenge/categories/list`` -> ``{"info": [{"id", "name", "icon"}]}``.

Lists are unpaginated single shots and large, so they go through the JSON-file
cache in :mod:`htb_agent.cache` under the ``challenges`` namespace.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from urllib.parse import quote

from htb_agent import cache
from htb_agent.http import HtbApiClient
from htb_agent.services.payloads import to_int


class ChallengeService:
    def __init__(self, client: HtbApiClient, *, use_cache: bool = True):
        self.client = client
        self.use_cache = use_cache

    def _cached_get(self, key: str, fetch: Callable[[], Any]) -> Any:
        """Fetch a challenge payload through the local JSON-file cache.

        Cached rows can carry stale ``authUserSolve``/``isTodo`` flags; those
        change with account state, not challenge metadata, so treat cached
        lists as directory data, not live progress.
        """
        if self.use_cache:
            cached = cache.get("challenges", key)
            if cached is not None:
                return cached
        payload = fetch()
        if self.use_cache:
            cache.put("challenges", key, payload)
        return payload

    def list_active(self) -> Any:
        return self._cached_get("list:active", lambda: self.client.get("/challenge/list"))

    def list_retired(self) -> Any:
        return self._cached_get(
            "list:retired", lambda: self.client.get("/challenge/list/retired")
        )

    def categories(self) -> Any:
        return self._cached_get(
            "categories", lambda: self.client.get("/challenge/categories/list")
        )

    def category_map(self) -> dict[int, str]:
        """Map challenge_category_id -> name; empty when the lookup fails."""
        try:
            payload = self.categories()
        except Exception:
            return {}
        info = payload.get("info") if isinstance(payload, dict) else None
        if not isinstance(info, list):
            return {}
        mapping: dict[int, str] = {}
        for item in info:
            if not isinstance(item, dict):
                continue
            category_id = to_int(item.get("id"))
            name = item.get("name")
            if category_id is not None and name:
                mapping[category_id] = str(name)
        return mapping

    def info(self, target: str) -> Any:
        """Raw ``GET /challenge/info/<target>`` payload (id or name)."""
        return self.client.get(f"/challenge/info/{quote(target, safe='')}")

    def info_item(self, target: str) -> dict[str, Any]:
        """The ``challenge`` block of an info payload."""
        payload = self.info(target)
        challenge = payload.get("challenge") if isinstance(payload, dict) else None
        if not isinstance(challenge, dict) or "id" not in challenge:
            raise RuntimeError(f"Unable to read challenge info for {target!r}.")
        return challenge

    def resolve_id(self, target: str | None) -> int:
        """Resolve an id-or-name target to a numeric challenge id.

        The info endpoint resolves names (case-insensitively) server-side, so
        resolution goes through it rather than the cached lists — the same
        pattern as :meth:`MachineService.resolve_id` using the profile route.
        """
        if not target:
            raise RuntimeError("Challenge target is required.")
        if target.isdigit():
            return int(target)
        return int(self.info_item(target)["id"])

    def submit_flag(self, target: str, flag: str, difficulty: int) -> Any:
        """Submit a challenge flag through the Labs v4 API."""
        return self.client.post(
            "/challenge/own",
            data={
                "challenge_id": self.resolve_id(target),
                "flag": flag,
                "difficulty": difficulty,
            },
        )

    def rows(self, payload: Any) -> list[dict[str, Any]]:
        """Compact row dicts for a list payload, with category names resolved."""
        categories = self.category_map()
        return [
            challenge_row(item, categories)
            for item in extract_challenges(payload)
            if isinstance(item, dict)
        ]

    def search(
        self,
        query: str,
        *,
        retired_only: bool = False,
        include_retired: bool = False,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Client-side search over the (cached) challenge lists.

        Challenge lists are single unpaginated payloads, so unlike
        :func:`~htb_agent.services.search.search_machines` there is no page
        walking; the rank ladder mirrors it: exact name, name prefix, name
        substring, exact id/category/difficulty, then any-value substring.
        """
        term = query.strip()
        if not term:
            raise ValueError("Search query is required.")
        if limit < 1:
            raise ValueError("Search limit must be at least 1.")

        if retired_only:
            payloads = [self.list_retired()]
        elif include_retired:
            payloads = [self.list_active(), self.list_retired()]
        else:
            payloads = [self.list_active()]

        categories = self.category_map()
        matches: list[tuple[int, dict[str, Any]]] = []
        seen: set[int] = set()
        for payload in payloads:
            for item in extract_challenges(payload):
                if not isinstance(item, dict):
                    continue
                rank = challenge_match_rank(item, term, categories)
                if rank is None:
                    continue
                row = challenge_row(item, categories)
                challenge_id = row.get("id")
                if isinstance(challenge_id, int):
                    if challenge_id in seen:
                        continue
                    seen.add(challenge_id)
                matches.append((rank, row))
            if len(matches) >= limit:
                break

        matches.sort(key=lambda match: (match[0], str(match[1].get("name") or "").casefold()))
        return [row for _, row in matches[:limit]]


def extract_challenges(payload: Any) -> list[Any]:
    """The challenge list of a list payload (``{"challenges": [...]}``)."""
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        challenges = payload.get("challenges")
        if isinstance(challenges, list):
            return challenges
    return []


def challenge_row(item: dict[str, Any], categories: dict[int, str] | None = None) -> dict[str, Any]:
    """Compact projection of a challenge-list row.

    Based on the live ``GET /challenge/list`` shape: ``points`` arrives as a
    string, the category is a numeric ``challenge_category_id`` (resolved to a
    name via ``categories`` when available), and ``state`` distinguishes
    ``active`` / ``retired`` / ``retired_free``.
    """
    points = to_int(item.get("points"))
    if points is None:
        points = to_int(item.get("static_points"))
    category_id = to_int(item.get("challenge_category_id"))
    category = None
    if categories and category_id is not None:
        category = categories.get(category_id)
    if category is None and category_id is not None:
        category = category_id
    return {
        "id": item.get("id"),
        "name": item.get("name"),
        "category": category,
        "difficulty": item.get("difficulty"),
        "points": points,
        "retired": item.get("retired"),
        "state": item.get("state"),
        "solved": item.get("authUserSolve"),
        "solves": item.get("solves"),
    }


def challenge_summary(payload: Any) -> dict[str, Any]:
    """Compact projection of an info payload's ``challenge`` block.

    Keeps the scalar fields an agent needs, mirroring
    :func:`~htb_agent.services.payloads.profile_summary` for machines. Based on
    the live ``GET /challenge/info/<id>`` payload (keys: id, name,
    category_name, difficulty, points, release_date, description, solves,
    creator_name, creator2_name, stars, retired, state, authUserSolve,
    first_blood_user, recommended, play_methods).
    """
    source = payload.get("challenge") if isinstance(payload, dict) else None
    if not isinstance(source, dict):
        return {"info": None}
    points = to_int(source.get("points"))
    if points is None:
        points = to_int(source.get("static_points"))
    return {
        "info": {
            "id": source.get("id"),
            "name": source.get("name"),
            "category": source.get("category_name"),
            "difficulty": source.get("difficulty"),
            "points": points,
            "stars": source.get("stars"),
            "retired": source.get("retired"),
            "state": source.get("state"),
            "solved": source.get("authUserSolve"),
            "release": source.get("release_date"),
            "description": source.get("description"),
            "solves": source.get("solves"),
            "maker": source.get("creator_name"),
            "maker2": source.get("creator2_name"),
            "first_blood": source.get("first_blood_user"),
            "recommended": source.get("recommended"),
        }
    }


def challenge_match_rank(
    item: dict[str, Any], query: str, categories: dict[int, str] | None = None
) -> int | None:
    term = query.casefold()
    name = str(item.get("name")).casefold() if item.get("name") is not None else ""
    if name == term:
        return 0
    if name.startswith(term):
        return 1
    if term in name:
        return 2

    category_id = to_int(item.get("challenge_category_id"))
    category = categories.get(category_id) if categories and category_id is not None else None
    exact_fields = [item.get("id"), category, item.get("difficulty")]
    if any(str(value).casefold() == term for value in exact_fields if value is not None):
        return 3

    values = [
        item.get("id"),
        item.get("name"),
        category,
        item.get("difficulty"),
        item.get("points"),
        item.get("state"),
        item.get("url_name"),
    ]
    return (
        4
        if any(term in str(value).casefold() for value in values if value is not None)
        else None
    )
