"""Command handlers: the controller layer between parsed args and services.

Each handler takes ``(args, client)`` and returns a value to render (dict/list
printed as JSON, or pretty text when ``--pretty`` is passed), or ``None`` when
it prints its own output. A handler may instead return :class:`ExitStatus`
when it has printed everything itself and only needs to set the exit code.

Output contract: default stdout is compact curated JSON; ``--full`` returns
the complete raw API payload where a command has one; ``--pretty`` renders
for humans and always wins over ``--full``. ``-q``/``--quiet`` prints a
single bare value on the few commands that support it.

Parser definitions live in :mod:`htb_agent.parser`; rendering in
:mod:`htb_agent.output`.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from typing import Any

from htb_agent import __version__
from htb_agent.config import ConfigError, resolve_base_url, resolve_token
from htb_agent.http import ApiError, HtbApiClient, StateError
from htb_agent.output import (
    print_json,
    print_pretty,
    print_status_line,
    print_table,
)
from htb_agent.services.challenges import ChallengeService, challenge_summary
from htb_agent.services.machines import MachineService
from htb_agent.services.payloads import machine_rows, profile_summary
from htb_agent.services.user import UserService
from htb_agent.services.vpn import VpnService, server_rows, vpn_rows

AUTH_KEY_HINT = (
    "the API key was rejected. Check HTB_API_KEY in .env.local,"
    " or generate a fresh App Token in your HTB profile settings:"
    " https://app.hackthebox.com/profile/settings"
)


class ExitStatus:
    """Handler result that only carries a process exit code.

    Used when the handler has already printed everything it needs to (e.g.
    ``doctor``, whose report must go out even on failure, and ``-q`` flows
    that print nothing on error).
    """

    def __init__(self, code: int):
        self.code = code


def _machine_service(args: argparse.Namespace, client: HtbApiClient) -> MachineService:
    return MachineService(client, use_cache=not getattr(args, "no_cache", False))


def _challenge_service(args: argparse.Namespace, client: HtbApiClient) -> ChallengeService:
    return ChallengeService(client, use_cache=not getattr(args, "no_cache", False))


def _vpn_service(client: HtbApiClient) -> VpnService:
    return VpnService(client)


def machine_profile(args: argparse.Namespace, client: HtbApiClient) -> Any:
    payload = _machine_service(args, client).profile(args.target)
    if args.pretty or getattr(args, "full", False):
        return payload
    return profile_summary(payload)


def machine_active(args: argparse.Namespace, client: HtbApiClient) -> Any:
    service = _machine_service(args, client)
    if not args.pretty and args.oneline:
        raise ValueError("--oneline requires --pretty; default output is JSON.")
    if getattr(args, "quiet", False):
        summary = service.active_summary()
        info = summary.get("info") if isinstance(summary, dict) else None
        ip = info.get("ip") if isinstance(info, dict) else None
        if not ip:
            raise StateError("No active machine with an IP.")
        print(ip)
        return None
    if args.pretty:
        summary = service.active_summary(include_details=args.details)
        if args.oneline:
            info = summary.get("info") if isinstance(summary, dict) else None
            print_status_line(info if isinstance(info, dict) else {}, color=args.color)
            return None
        return summary
    if getattr(args, "full", False):
        return service.active_with_profile()
    return service.active_summary(include_details=args.details)


def machine_list(args: argparse.Namespace, client: HtbApiClient) -> Any:
    service = _machine_service(args, client)
    selected = sum(bool(value) for value in [args.retired, args.todo, args.unreleased, args.sp_tier])
    if selected > 1:
        raise ValueError("Choose only one list filter.")
    if args.retired:
        payload = service.list_retired(args.page)
    elif args.todo:
        payload = service.list_todo()
    elif args.unreleased:
        payload = service.list_unreleased()
    elif args.sp_tier:
        payload = service.list_starting_point(args.sp_tier)
    else:
        payload = service.list_playable(args.page)

    if args.pretty:
        print_table(
            machine_rows(payload),
            ["id", "name", "os", "difficulty", "points", "active", "spawned", "free"],
            color=args.color,
            wide=args.wide,
        )
        return None
    if getattr(args, "full", False):
        return payload
    return machine_rows(payload)


def machine_search(args: argparse.Namespace, client: HtbApiClient) -> Any:
    if args.retired and args.all:
        raise ValueError("Choose either --retired or --all, not both.")

    rows = _machine_service(args, client).search(
        args.query,
        retired_only=args.retired,
        include_retired=args.all,
        include_profiles=args.profiles,
        limit=args.limit,
        max_pages=args.max_pages,
    )
    if not args.pretty:
        return rows
    print_table(
        rows,
        ["id", "name", "os", "difficulty", "points", "retired", "active", "spawned", "free"],
        color=args.color,
        wide=args.wide,
    )
    return None


def machine_start(args: argparse.Namespace, client: HtbApiClient) -> Any:
    service = _machine_service(args, client)
    if getattr(args, "quiet", False) and not args.wait:
        raise ValueError("--quiet requires --wait for machine start")
    if not args.wait:
        return service.start(args.target, args.mode)

    machine_id = service.resolve_id(args.target)
    deadline = time.monotonic() + args.retry_for
    result = service.start_with_retry(
        str(machine_id),
        args.mode,
        retry_for=args.retry_for,
        interval=args.interval,
    )
    # Honor --interval (cadence) and --retry-for (budget) for the IP poll too,
    # so the whole --wait flow respects the limits the user asked for instead of
    # a hidden 300s cap.
    info = service.wait_for_active_ip(
        machine_id,
        timeout=max(0, math.ceil(deadline - time.monotonic())),
        interval=args.interval,
    )
    if getattr(args, "quiet", False):
        ip = info.get("ip")
        if not ip:
            raise StateError("Machine started but reported no IP.")
        print(ip)
        return None
    return {
        "id": machine_id,
        "name": info.get("name"),
        "ip": info.get("ip"),
        "spawn": result,
    }


def machine_stop(args: argparse.Namespace, client: HtbApiClient) -> Any:
    return _machine_service(args, client).stop(args.target)


def machine_reset(args: argparse.Namespace, client: HtbApiClient) -> Any:
    return _machine_service(args, client).reset(args.target)


def machine_extend(args: argparse.Namespace, client: HtbApiClient) -> Any:
    return _machine_service(args, client).extend(args.target)


def machine_submit(args: argparse.Namespace, client: HtbApiClient) -> Any:
    return _machine_service(args, client).submit_flag(args.target, args.flag, args.difficulty)


def challenge_list(args: argparse.Namespace, client: HtbApiClient) -> Any:
    service = _challenge_service(args, client)
    payload = service.list_retired() if args.retired else service.list_active()
    if args.pretty:
        print_table(
            service.rows(payload),
            ["id", "name", "category", "difficulty", "points", "state", "solved", "solves"],
            color=args.color,
            wide=args.wide,
        )
        return None
    if getattr(args, "full", False):
        return payload
    return service.rows(payload)


def challenge_info(args: argparse.Namespace, client: HtbApiClient) -> Any:
    payload = _challenge_service(args, client).info(args.target)
    if args.pretty or getattr(args, "full", False):
        return payload
    return challenge_summary(payload)


def challenge_search(args: argparse.Namespace, client: HtbApiClient) -> Any:
    if args.retired and args.all:
        raise ValueError("Choose either --retired or --all, not both.")

    rows = _challenge_service(args, client).search(
        args.query,
        retired_only=args.retired,
        include_retired=args.all,
        limit=args.limit,
    )
    if not args.pretty:
        return rows
    print_table(
        rows,
        ["id", "name", "category", "difficulty", "points", "retired", "state", "solved"],
        color=args.color,
        wide=args.wide,
    )
    return None


def challenge_submit(args: argparse.Namespace, client: HtbApiClient) -> Any:
    return _challenge_service(args, client).submit_flag(args.target, args.flag, args.difficulty)


def vpn_servers(args: argparse.Namespace, client: HtbApiClient) -> Any:
    if not args.static:
        payload = None
        try:
            payload = _vpn_service(client).list_servers(args.product)
            rows = server_rows(payload)
        except ApiError as exc:
            print(
                f"warning: could not fetch live servers ({exc});"
                " showing built-in aliases. Use --static to silence this.",
                file=sys.stderr,
            )
            rows = []
        if rows:
            if args.pretty:
                print_table(
                    rows,
                    ["id", "name", "group", "location", "clients", "full", "assigned"],
                    color=args.color,
                    wide=args.wide,
                )
                return None
            if getattr(args, "full", False):
                return payload
            return rows

    rows = vpn_rows()
    if not args.pretty:
        return rows
    print_table(rows, ["alias", "id", "name", "scope", "location"], color=args.color, wide=args.wide)
    return None


def vpn_status(args: argparse.Namespace, client: HtbApiClient) -> Any:
    payload = _vpn_service(client).list_servers(args.product)
    if args.pretty:
        assigned = _assigned_server(payload)
        if assigned is None:
            print(f"No VPN server assigned for {args.product}.")
        else:
            print(
                f"{assigned.get('friendly_name')} (id {assigned.get('id')},"
                f" {assigned.get('location')})"
            )
        return None
    if getattr(args, "full", False):
        return payload
    assigned = _assigned_server(payload)
    if assigned is None:
        return {"assigned": False, "product": args.product}
    return {
        "id": assigned.get("id"),
        "name": assigned.get("friendly_name"),
        "location": assigned.get("location"),
        "product": args.product,
    }


def _assigned_server(payload: Any) -> dict[str, Any] | None:
    """The ``data.assigned`` block of a /connections/servers payload, if any."""
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        return None
    assigned = data.get("assigned")
    if isinstance(assigned, dict) and assigned.get("id") is not None:
        return assigned
    return None


def vpn_switch(args: argparse.Namespace, client: HtbApiClient) -> Any:
    return _vpn_service(client).switch(args.server)


def vpn_download(args: argparse.Namespace, client: HtbApiClient) -> Any:
    path = _vpn_service(client).download_ovpn(args.server, args.variant, args.output)
    if getattr(args, "quiet", False):
        print(str(path))
        return None
    return {"output": str(path)}


def user_info(args: argparse.Namespace, client: HtbApiClient) -> Any:
    service = UserService(client)
    if getattr(args, "quiet", False):
        name = service.summary().get("info", {}).get("name")
        if not name:
            raise StateError("Could not read the current username.")
        print(name)
        return None
    if args.pretty:
        return service.summary()
    if getattr(args, "full", False):
        return service.profile()
    return service.summary()


def user_progress(args: argparse.Namespace, client: HtbApiClient) -> Any:
    service = UserService(client)
    if args.pretty:
        return service.progress()
    if getattr(args, "full", False):
        return service.profile()
    return service.progress()


def user_activity(args: argparse.Namespace, client: HtbApiClient) -> Any:
    service = UserService(client)
    if getattr(args, "full", False):
        return service.activity(limit=args.limit)
    rows = service.activity_rows(limit=args.limit)
    if not args.pretty:
        return rows
    print_table(
        rows,
        ["date", "type", "name", "points", "blood"],
        color=args.color,
        wide=args.wide,
    )
    return None


def doctor(args: argparse.Namespace, client: HtbApiClient | None) -> Any:
    """Diagnose key resolution, API connectivity, active machine, and VPN.

    Manages its own auth (the parser sets ``needs_auth=False``) so a missing
    or rejected key shows up as a failed check instead of aborting the run.
    Overall ``ok`` is ``key.ok && api.ok`` — the machine and VPN checks are
    informational and their failures never flip the overall result.
    """
    report: dict[str, Any] = {"ok": False, "version": __version__}

    try:
        token, source = resolve_token()
    except ConfigError as exc:
        report["key"] = {"ok": False, "error": str(exc)}
        return _doctor_finish(report, args)
    report["key"] = {"ok": True, "source": source}

    doctor_client = HtbApiClient(resolve_base_url(args.base_url), token, timeout=args.timeout)
    try:
        user = UserService(doctor_client).summary().get("info", {})
        report["api"] = {
            "ok": True,
            "user": {"id": user.get("id"), "name": user.get("name"), "vip": user.get("vip")},
        }
    except (ApiError, RuntimeError) as exc:
        api_report: dict[str, Any] = {"ok": False, "error": str(exc)}
        if isinstance(exc, ApiError) and exc.status == 401:
            api_report["hint"] = AUTH_KEY_HINT
        report["api"] = api_report
        # With no working API the remaining checks could only repeat the same
        # failure, so stop at the first broken link.
        return _doctor_finish(report, args)

    report["ok"] = True

    try:
        active = MachineService(doctor_client).active_summary()
        info = active.get("info") if isinstance(active, dict) else None
        if isinstance(info, dict) and info.get("id") is not None:
            machine: dict[str, Any] | None = {
                "id": info.get("id"),
                "name": info.get("name"),
                "ip": info.get("ip"),
                "expires_in": info.get("expires_in"),
            }
        else:
            machine = None
        report["machine"] = {"ok": True, "active": machine}
    except (ApiError, RuntimeError) as exc:
        report["machine"] = {"ok": False, "error": str(exc)}

    try:
        assigned = _assigned_server(_vpn_service(doctor_client).list_servers("labs"))
        server: dict[str, Any] | None = None
        if assigned is not None:
            server = {
                "id": assigned.get("id"),
                "name": assigned.get("friendly_name"),
                "location": assigned.get("location"),
            }
        report["vpn"] = {"ok": True, "server": server}
    except (ApiError, RuntimeError) as exc:
        report["vpn"] = {"ok": False, "error": str(exc)}

    return _doctor_finish(report, args)


def _doctor_finish(report: dict[str, Any], args: argparse.Namespace) -> ExitStatus:
    ok = bool(report.get("ok"))
    if getattr(args, "quiet", False):
        if ok:
            print("ok")
        return ExitStatus(0 if ok else 1)
    if args.pretty:
        _print_doctor_pretty(report)
    else:
        print_json(report)
    return ExitStatus(0 if ok else 1)


def _print_doctor_pretty(report: dict[str, Any]) -> None:
    print(f"doctor: {'ok' if report.get('ok') else 'not ok'} (version {report.get('version')})")

    key = report.get("key", {})
    if key.get("ok"):
        print(f"  key      ok    {key.get('source')}")
    else:
        print(f"  key      fail  {key.get('error')}")

    api = report.get("api")
    if api is None:
        print("  api      skip  (no key)")
    elif api.get("ok"):
        user = api.get("user") or {}
        print(f"  api      ok    {user.get('name')} (id {user.get('id')}, {user.get('vip')})")
    else:
        print(f"  api      fail  {api.get('error')}")
        if api.get("hint"):
            print(f"           hint  {api['hint']}")

    machine = report.get("machine")
    if machine is None:
        print("  machine  skip")
    elif machine.get("ok"):
        active = machine.get("active")
        if active is None:
            print("  machine  ok    no active machine")
        else:
            expires = active.get("expires_in") or "?"
            print(
                f"  machine  ok    {active.get('name')} (id {active.get('id')})"
                f" at {active.get('ip')}, expires {expires}"
            )
    else:
        print(f"  machine  fail  {machine.get('error')}")

    vpn = report.get("vpn")
    if vpn is None:
        print("  vpn      skip")
    elif vpn.get("ok"):
        server = vpn.get("server")
        if server is None:
            print("  vpn      ok    no VPN server assigned")
        else:
            print(f"  vpn      ok    {server.get('name')} (id {server.get('id')}, {server.get('location')})")
    else:
        print(f"  vpn      fail  {vpn.get('error')}")


def raw(args: argparse.Namespace, client: HtbApiClient) -> Any:
    data = json.loads(args.data) if args.data else None
    response = client.request(args.method, args.path, data=data)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_bytes(response.body)
        return {"status": response.status, "output": str(args.output)}
    if response.content_type.startswith("application/json"):
        return response.json()
    return response.body.decode("utf-8", errors="replace")


def print_result(value: Any, args: argparse.Namespace) -> None:
    if isinstance(value, (dict, list)):
        if args.pretty:
            print_pretty(value, color=args.color)
        else:
            print_json(value)
    else:
        print(value)
