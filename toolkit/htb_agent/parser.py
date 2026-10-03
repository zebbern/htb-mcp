"""Argument parser construction.

Builds the full ``htb`` argument parser and binds each subcommand to a handler
in :mod:`htb_agent.handlers`. This module is declarative only; it contains no
request or rendering logic.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from htb_agent import __version__, handlers
from htb_agent.helpfmt import HtbHelpFormatter, banner
from htb_agent.services.vpn import VPN_PRODUCTS

_SubParsers = argparse._SubParsersAction


def _positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"expected an integer, got {value!r}") from exc
    if parsed < 1:
        raise argparse.ArgumentTypeError("value must be at least 1")
    return parsed


def _non_negative_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"expected an integer, got {value!r}") from exc
    if parsed < 0:
        raise argparse.ArgumentTypeError("value must be at least 0")
    return parsed


def _vpn_servers_needs_auth(args: argparse.Namespace) -> bool:
    return not args.static


def _add_global_arguments(parser: argparse.ArgumentParser, *, inherited: bool) -> None:
    """Add the flags accepted at every level.

    The root parser keeps real defaults so the attributes always exist. The
    inherited copy (mixed into every subcommand via ``parents=``) uses
    ``SUPPRESS`` for both default and help: ``SUPPRESS`` defaults mean a flag
    placed before the subcommand is not clobbered by the subparser, and
    ``SUPPRESS`` help keeps per-command ``--help`` output uncluttered.
    """
    sup = argparse.SUPPRESS

    def add(*flags: str, default: object, help: str, **kwargs: object) -> None:
        parser.add_argument(
            *flags,
            default=sup if inherited else default,
            help=sup if inherited else help,
            **kwargs,  # type: ignore[arg-type]
        )

    add("--base-url", default=None, help="Override the API base URL.")
    add(
        "--timeout",
        type=_positive_int,
        default=30,
        help="Per-request timeout in seconds. Default 30.",
    )
    add(
        "--pretty",
        action="store_true",
        default=False,
        help="Human-readable output. Default output is JSON.",
    )
    add(
        "--full",
        action="store_true",
        default=False,
        help="Return the complete raw API payload instead of compact JSON.",
    )
    add(
        "--no-cache",
        action="store_true",
        default=False,
        help="Bypass the local list caches (machines, challenges).",
    )
    add(
        "-q",
        "--quiet",
        action="store_true",
        default=False,
        help="Print a single bare value for scripting (supported commands only).",
    )
    add(
        "--color",
        choices=["auto", "always", "never"],
        default="auto",
        help="Colorize human output (--pretty). Defaults to auto.",
    )
    add("--wide", action="store_true", default=False, help="Do not truncate table columns.")


def _leaf(
    subparsers: _SubParsers,
    name: str,
    common: argparse.ArgumentParser,
    **kwargs: Any,
) -> argparse.ArgumentParser:
    """Add a runnable subcommand that also accepts the global flags."""
    return subparsers.add_parser(name, parents=[common], **kwargs)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="htb",
        formatter_class=HtbHelpFormatter,
        description=banner("Hack The Box API toolkit for AI agents."),
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    _add_global_arguments(parser, inherited=False)

    # Mixed into every subcommand so global flags also work *after* the command,
    # e.g. both "htb --pretty machine list" and "htb machine list --pretty".
    common = argparse.ArgumentParser(add_help=False)
    _add_global_arguments(common, inherited=True)

    subparsers = parser.add_subparsers(dest="command")
    _add_machine_commands(subparsers, common)
    _add_challenge_commands(subparsers, common)
    _add_vpn_commands(subparsers, common)
    _add_user_commands(subparsers, common)
    _add_doctor_command(subparsers, common)
    _add_raw_command(subparsers, common)
    return parser


def _add_machine_commands(subparsers: _SubParsers, common: argparse.ArgumentParser) -> None:
    machine = subparsers.add_parser("machine", help="Manage machines.")
    machine_sub = machine.add_subparsers(dest="machine_command")

    profile = _leaf(machine_sub, "profile", common, help="Show a machine profile by id or name.")
    profile.add_argument("target")
    profile.set_defaults(handler=handlers.machine_profile)

    info = _leaf(machine_sub, "info", common, help="Alias for 'machine profile'.")
    info.add_argument("target")
    info.set_defaults(handler=handlers.machine_profile)

    active = _leaf(machine_sub, "active", common, help="Show the active machine.")
    active.add_argument("--details", action="store_true", help="Include synopsis and Academy module names.")
    active.add_argument(
        "--oneline",
        action="store_true",
        help="Print a single compact status line instead of the full summary (requires --pretty).",
    )
    active.set_defaults(handler=handlers.machine_active)

    list_cmd = _leaf(machine_sub, "list", common, help="List machines.")
    list_cmd.add_argument("--page", type=_positive_int, default=None)
    list_cmd.add_argument("--retired", action="store_true")
    list_cmd.add_argument("--todo", action="store_true")
    list_cmd.add_argument("--unreleased", action="store_true")
    list_cmd.add_argument("--sp-tier", type=int, choices=[1, 2, 3], default=None)
    list_cmd.set_defaults(handler=handlers.machine_list)

    search = _leaf(
        machine_sub,
        "search",
        common,
        help="Search machines by id, name, OS, difficulty, tag, maker, or profile text.",
    )
    search.add_argument("query")
    search.add_argument("--retired", action="store_true", help="Search retired machines only.")
    search.add_argument("--all", action="store_true", help="Search playable and retired machines.")
    search.add_argument(
        "--profiles",
        action="store_true",
        help="Also fetch machine profiles and search description/profile-only fields.",
    )
    search.add_argument(
        "--limit",
        type=_positive_int,
        default=20,
        help="Maximum matching rows to print.",
    )
    search.add_argument(
        "--max-pages",
        type=_positive_int,
        default=10,
        help="Maximum API pages to scan per list.",
    )
    search.set_defaults(handler=handlers.machine_search)

    start = _leaf(machine_sub, "start", common, help="Start a machine by id or name.")
    start.add_argument("target")
    start.add_argument("--mode", choices=["auto", "play", "spawn"], default="auto")
    start.add_argument(
        "--wait",
        action="store_true",
        help="Retry while spawn capacity is full, then wait for the machine IP."
        " Useful at peak times such as seasonal releases (Saturdays 19:00 UTC).",
    )
    start.add_argument(
        "--retry-for",
        type=_positive_int,
        default=600,
        metavar="SECONDS",
        help="With --wait: budget (seconds) for the whole flow — retrying the"
        " spawn while capacity is full and then waiting for the machine IP."
        " Default 600.",
    )
    start.add_argument(
        "--interval",
        type=_positive_int,
        default=15,
        metavar="SECONDS",
        help="With --wait: base delay between spawn attempts (jittered) and between"
        " IP-availability polls. Default 15.",
    )
    start.set_defaults(handler=handlers.machine_start)

    stop = _leaf(machine_sub, "stop", common, help="Stop a machine. Defaults to active machine.")
    stop.add_argument("target", nargs="?")
    stop.set_defaults(handler=handlers.machine_stop)

    reset = _leaf(machine_sub, "reset", common, help="Reset a machine. Defaults to active machine.")
    reset.add_argument("target", nargs="?")
    reset.set_defaults(handler=handlers.machine_reset)

    extend = _leaf(
        machine_sub, "extend", common, help="Extend a machine's expiry. Defaults to active machine."
    )
    extend.add_argument("target", nargs="?")
    extend.set_defaults(handler=handlers.machine_extend)

    submit = _leaf(machine_sub, "submit", common, help="Submit a user or root flag.")
    submit.add_argument("target")
    submit.add_argument("flag")
    submit.add_argument(
        "--difficulty",
        type=int,
        choices=range(10, 101, 10),
        required=True,
    )
    submit.set_defaults(handler=handlers.machine_submit)


def _add_challenge_commands(subparsers: _SubParsers, common: argparse.ArgumentParser) -> None:
    challenge = subparsers.add_parser("challenge", help="Browse and submit challenges.")
    challenge_sub = challenge.add_subparsers(dest="challenge_command")

    list_cmd = _leaf(challenge_sub, "list", common, help="List challenges.")
    list_cmd.add_argument(
        "--retired", action="store_true", help="List retired challenges instead of active ones."
    )
    list_cmd.set_defaults(handler=handlers.challenge_list)

    info = _leaf(challenge_sub, "info", common, help="Show a challenge by id or name.")
    info.add_argument("target")
    info.set_defaults(handler=handlers.challenge_info)

    search = _leaf(
        challenge_sub,
        "search",
        common,
        help="Search challenges by id, name, category, difficulty, or state.",
    )
    search.add_argument("query")
    search.add_argument("--retired", action="store_true", help="Search retired challenges only.")
    search.add_argument("--all", action="store_true", help="Search active and retired challenges.")
    search.add_argument(
        "--limit",
        type=_positive_int,
        default=20,
        help="Maximum matching rows to print.",
    )
    search.set_defaults(handler=handlers.challenge_search)

    submit = _leaf(challenge_sub, "submit", common, help="Submit a challenge flag.")
    submit.add_argument("target")
    submit.add_argument("flag")
    submit.add_argument(
        "--difficulty",
        type=int,
        choices=range(10, 101, 10),
        required=True,
    )
    submit.set_defaults(handler=handlers.challenge_submit)


def _add_vpn_commands(subparsers: _SubParsers, common: argparse.ArgumentParser) -> None:
    vpn = subparsers.add_parser("vpn", help="Manage VPN server selection and OVPN files.")
    vpn_sub = vpn.add_subparsers(dest="vpn_command")

    servers = _leaf(
        vpn_sub,
        "servers",
        common,
        help="List VPN servers your account can use (live), including VIP/VIP+.",
    )
    servers.add_argument(
        "product",
        nargs="?",
        default="labs",
        choices=list(VPN_PRODUCTS),
        help="Which server pool to list. Default labs (regular machines).",
    )
    servers.add_argument(
        "--static",
        action="store_true",
        help="Skip the API and show only the built-in offline aliases.",
    )
    servers.set_defaults(
        handler=handlers.vpn_servers,
        needs_auth=_vpn_servers_needs_auth,
    )

    status = _leaf(
        vpn_sub,
        "status",
        common,
        help="Show the currently assigned VPN server.",
    )
    status.add_argument(
        "product",
        nargs="?",
        default="labs",
        choices=list(VPN_PRODUCTS),
        help="Which server pool to check. Default labs (regular machines).",
    )
    status.set_defaults(handler=handlers.vpn_status)

    switch = _leaf(vpn_sub, "switch", common, help="Switch to a VPN server by id, alias, or name.")
    switch.add_argument("server")
    switch.set_defaults(handler=handlers.vpn_switch)

    download = _leaf(vpn_sub, "download", common, help="Download an OVPN file.")
    download.add_argument("server")
    download.add_argument("-o", "--output", type=Path, default=Path("lab-vpn.ovpn"))
    download.add_argument("--variant", type=_non_negative_int, default=0)
    download.set_defaults(handler=handlers.vpn_download)


def _add_user_commands(subparsers: _SubParsers, common: argparse.ArgumentParser) -> None:
    user = subparsers.add_parser("user", help="Show your HTB user profile.")
    user_sub = user.add_subparsers(dest="user_command")

    info = _leaf(user_sub, "info", common, help="Show your profile: rank, points, and owns.")
    info.set_defaults(handler=handlers.user_info)

    progress = _leaf(
        user_sub,
        "progress",
        common,
        help="Show rank progression: current rank, next rank, and owns.",
    )
    progress.set_defaults(handler=handlers.user_progress)

    activity = _leaf(
        user_sub,
        "activity",
        common,
        help="Show recent owns (machine user/root flags and challenges).",
    )
    activity.add_argument(
        "--limit",
        type=_positive_int,
        default=20,
        help="Maximum events to print. Default 20.",
    )
    activity.set_defaults(handler=handlers.user_activity)


def _add_doctor_command(subparsers: _SubParsers, common: argparse.ArgumentParser) -> None:
    doctor = _leaf(
        subparsers,
        "doctor",
        common,
        help="Check the API key, connectivity, active machine, and VPN assignment.",
    )
    # doctor manages its own auth flow so it can diagnose a missing or
    # rejected key instead of dying on load_config before any check runs.
    doctor.set_defaults(handler=handlers.doctor, needs_auth=False)


def _add_raw_command(subparsers: _SubParsers, common: argparse.ArgumentParser) -> None:
    raw = _leaf(subparsers, "raw", common, help="Call an API endpoint directly.")
    raw.add_argument("method", choices=["GET", "POST", "PUT", "PATCH", "DELETE"])
    raw.add_argument("path", help="Path such as /machine/active.")
    raw.add_argument("--data", default=None, help="JSON body for write requests.")
    raw.add_argument("-o", "--output", type=Path, default=None)
    raw.set_defaults(handler=handlers.raw)
