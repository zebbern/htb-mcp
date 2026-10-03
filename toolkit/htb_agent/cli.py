"""Entry point: parse args, build a client, dispatch to a handler, render errors.

Error contract: handled exceptions print one structured JSON object to stderr
and exit 1 (``--pretty`` keeps the plain ``error: ...`` text style). Argparse
errors keep exit 2; KeyboardInterrupt keeps exit 130.
"""

from __future__ import annotations

import json
import sys
from typing import Any

from htb_agent.config import ConfigError, load_config
from htb_agent.handlers import AUTH_KEY_HINT, ExitStatus, print_result
from htb_agent.http import ApiError, HtbApiClient, StateError
from htb_agent.parser import build_parser

# Handlers that support -q/--quiet (bare single-value stdout for scripting).
_QUIET_COMMANDS = {"machine_active", "machine_start", "user_info", "vpn_download", "doctor"}


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if not hasattr(args, "handler"):
        parser.print_help()
        return 2

    try:
        if getattr(args, "quiet", False):
            if getattr(args, "pretty", False):
                raise ValueError("--quiet cannot be combined with --pretty")
            if getattr(args.handler, "__name__", None) not in _QUIET_COMMANDS:
                raise ValueError("--quiet is not supported for this command.")

        client = None
        auth_requirement = getattr(args, "needs_auth", True)
        needs_auth = auth_requirement(args) if callable(auth_requirement) else auth_requirement
        if needs_auth:
            config = load_config(base_url=args.base_url)
            client = HtbApiClient(config.base_url, config.token, timeout=args.timeout)
        result = args.handler(args, client)
        if isinstance(result, ExitStatus):
            return result.code
        if result is not None:
            print_result(result, args)
        return 0
    except (ApiError, ConfigError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
        return _report_error(exc, args)
    except KeyboardInterrupt:
        print("interrupted.", file=sys.stderr)
        return 130


def _report_error(exc: BaseException, args: Any) -> int:
    # 401 means a rejected key; 403 is usually a state/permission issue
    # (e.g. "you already have an active instance"), so do not blame the key.
    hint = AUTH_KEY_HINT if isinstance(exc, ApiError) and exc.status == 401 else None
    if getattr(args, "pretty", False):
        print(f"error: {exc}", file=sys.stderr)
        if hint:
            print(f"hint: {hint}", file=sys.stderr)
        return 1

    report: dict[str, Any] = {
        "error": True,
        "type": _error_type(exc),
        "status": exc.status if isinstance(exc, ApiError) else None,
        "message": str(exc),
    }
    if hint:
        report["hint"] = hint
    print(json.dumps(report, ensure_ascii=False), file=sys.stderr)
    return 1


def _error_type(exc: BaseException) -> str:
    if isinstance(exc, ConfigError):
        return "config"
    if isinstance(exc, StateError):
        return "state"
    if isinstance(exc, ApiError):
        status = exc.status
        if status is None:
            return "network"
        mapped = {
            401: "auth",
            403: "forbidden",
            404: "not_found",
            409: "conflict",
            429: "rate_limit",
        }.get(status)
        if mapped is not None:
            return mapped
        return "server" if 500 <= status < 600 else "api"
    if isinstance(exc, (json.JSONDecodeError, ValueError)):
        return "usage"
    return "runtime"


if __name__ == "__main__":
    raise SystemExit(main())
