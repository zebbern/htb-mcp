"""Current-user profile operations.

``UserService`` is the single entry point for the authenticated user's data.
``whoami`` (``GET /user/info``) is also the lightweight call used to verify a
token, since any authenticated request validates the bearer token.
"""

from __future__ import annotations

from typing import Any

from htb_agent.http import HtbApiClient
from htb_agent.services.payloads import to_int


class UserService:
    def __init__(self, client: HtbApiClient):
        self.client = client

    def whoami(self) -> dict[str, Any]:
        """Return the authenticated user's basic identity (id, name)."""
        data = self.client.get("/user/info")
        info = data.get("info") if isinstance(data, dict) else None
        if not isinstance(info, dict) or "id" not in info:
            raise RuntimeError("Could not read the current user from /user/info.")
        return info

    def profile(self, user_id: int | None = None) -> dict[str, Any]:
        """Return a full basic profile. Defaults to the authenticated user."""
        if user_id is None:
            user_id = int(self.whoami()["id"])
        data = self.client.get(f"/user/profile/basic/{user_id}")
        profile = data.get("profile") if isinstance(data, dict) else None
        if not isinstance(profile, dict):
            raise RuntimeError(f"Could not read the profile for user {user_id}.")
        return profile

    def summary(self) -> dict[str, Any]:
        return user_summary(self.profile())

    def progress(self) -> dict[str, Any]:
        """Focused rank-progression view of the basic profile."""
        return user_progress(self.profile())

    def activity(
        self, user_id: int | None = None, *, limit: int = 20, max_pages: int = 10
    ) -> list[dict[str, Any]]:
        """Recent owns (machine user/root flags and challenges), newest first.

        ``GET /user/profile/activity/<id>`` exists only on the v5 base (v4
        404s); pages come back as ``{"data": [...], "meta": {"page",
        "lastPage", "totalItems"}}`` with 15 rows per page.
        """
        if user_id is None:
            user_id = int(self.whoami()["id"])
        if limit < 1:
            raise ValueError("Activity limit must be at least 1.")

        items: list[dict[str, Any]] = []
        for page in range(1, max_pages + 1):
            payload = self.client.get(
                f"/user/profile/activity/{user_id}", query={"page": page}, version="v5"
            )
            data = payload.get("data") if isinstance(payload, dict) else None
            if not isinstance(data, list) or not data:
                break
            items.extend(item for item in data if isinstance(item, dict))
            meta = payload.get("meta") if isinstance(payload, dict) else None
            last_page = to_int(meta.get("lastPage")) if isinstance(meta, dict) else None
            if last_page is not None and page >= last_page:
                break
            if len(items) >= limit:
                break
        return items[:limit]

    def activity_rows(
        self, user_id: int | None = None, *, limit: int = 20, max_pages: int = 10
    ) -> list[dict[str, Any]]:
        return [
            activity_row(item)
            for item in self.activity(user_id, limit=limit, max_pages=max_pages)
        ]


def user_summary(profile: dict[str, Any]) -> dict[str, Any]:
    team = profile.get("team")
    team_name = team.get("name") if isinstance(team, dict) else team
    return {
        "info": {
            "id": profile.get("id"),
            "name": profile.get("name"),
            "rank": profile.get("rank"),
            "points": profile.get("points"),
            "ranking": profile.get("ranking"),
            "user_owns": profile.get("user_owns"),
            "system_owns": profile.get("system_owns"),
            "respects": profile.get("respects"),
            "country": profile.get("country_name"),
            "team": team_name,
            "vip": vip_tier(profile),
        }
    }


def user_progress(profile: dict[str, Any]) -> dict[str, Any]:
    """Compact rank-progression projection of a basic profile payload.

    Fields verified live in ``GET /user/profile/basic/<id>``: rank, points,
    ranking, next_rank, next_rank_points, current_rank_progress,
    rank_requirement, rank_ownership, user_owns, system_owns, respects.
    """
    return {
        "info": {
            "rank": profile.get("rank"),
            "points": profile.get("points"),
            "ranking": profile.get("ranking"),
            "next_rank": profile.get("next_rank"),
            "next_rank_points": profile.get("next_rank_points"),
            "current_rank_progress": profile.get("current_rank_progress"),
            "rank_requirement": profile.get("rank_requirement"),
            "rank_ownership": profile.get("rank_ownership"),
            "user_owns": profile.get("user_owns"),
            "system_owns": profile.get("system_owns"),
            "respects": profile.get("respects"),
        }
    }


def activity_row(item: dict[str, Any]) -> dict[str, Any]:
    """Compact projection of one activity event.

    ``type`` is ``user``/``root`` for machine flags and ``challenge`` for
    challenge owns; ``blood`` marks first bloods.
    """
    return {
        "date": item.get("ownDate"),
        "type": item.get("type"),
        "id": item.get("id"),
        "name": item.get("name"),
        "points": item.get("points"),
        "blood": item.get("blood"),
    }


def vip_tier(profile: dict[str, Any]) -> str:
    """HTB's tiers: VIP+ (dedicated) outranks VIP; otherwise no subscription.

    ``isVip`` is false for VIP+ accounts, so dedicated VIP must be checked
    separately or the tool reports a paying user as having none.
    """
    if profile.get("isDedicatedVip"):
        return "VIP+"
    if profile.get("isVip"):
        return "VIP"
    return "no"
