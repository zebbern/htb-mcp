# Tool reference

This is the single source of truth for the MCP tools exposed by htb-mcp. The README only carries a summary table; keep details here.

htb-mcp is a thin MCP wrapper around the bundled **HTB Agent Toolkit** CLI (`toolkit/htb.py`, Python 3.10+ stdlib only). Every tool maps 1:1 onto a CLI command, returns the CLI's compact curated JSON as text (and as structured content), and surfaces the CLI's structured stderr errors as `HTB error (<type>): <message>. <hint>`.

Error `type` values to branch on: `config` (no API key — see README auth), `auth` (key rejected, regenerate the App Token), `rate_limit` (429 after built-in retries — back off, do not add your own loop), `conflict` (an active machine blocks a start — the message names it), `state` (request conflicts with account state), `usage` (bad input), `not_found` / `forbidden` / `server` / `network` / `api` / `runtime`.

Annotation groups:

- **read-only**: `readOnlyHint: true`, `destructiveHint: false`, `idempotentHint: true`, `openWorldHint: true`
- **state-changing, non-destructive** (`htb_machine_start`, `htb_machine_extend`, `htb_vpn_switch`): `readOnlyHint: false`, `destructiveHint: false`, `openWorldHint: true`
- **destructive** (`htb_machine_stop`, `htb_machine_reset`, `htb_machine_submit`, `htb_challenge_submit`): `readOnlyHint: false`, `destructiveHint: true`, `openWorldHint: true`
- **htb_raw**: `readOnlyHint: false`, `openWorldHint: true` (it can call anything)

Machine/challenge targets accept a numeric id (`444`) or a name (`BoardLight`; names resolve case-insensitively). `stop`/`reset`/`extend` default to the active machine when the target is omitted.

## htb_doctor → `doctor`

Session health check: key source, API whoami, active machine, VPN assignment. Run it first in any session. Read-only. No parameters.

## htb_machine_list → `machine list`

| Name | Type | Default | CLI flag |
|---|---|---|---|
| `filter` | enum: `playable` / `retired` / `todo` / `unreleased` | `playable` | `--retired` / `--todo` / `--unreleased` |
| `spTier` | integer 1–3 | none | `--sp-tier N` |
| `page` | integer | none | `--page N` (playable and retired lists only) |
| `noCache` | boolean | false | `--no-cache` |

Read-only. Lists are cached for `HTB_CACHE_TTL` seconds (default 3600).

## htb_machine_search → `machine search <query>`

| Name | Type | Default | CLI flag |
|---|---|---|---|
| `query` | string | required | positional |
| `all` | boolean | false | `--all` (also scan retired) |
| `retired` | boolean | false | `--retired` (retired only) |
| `profiles` | boolean | false | `--profiles` — also match profile text; **slow**, one request per machine (6-minute MCP timeout) |
| `limit` | integer 1–50 | 20 | `--limit` |
| `maxPages` | integer | none | `--max-pages` (CLI default 10) |

Read-only.

## htb_machine_profile → `machine profile <target>`

`target` (required), `full` (boolean → `--full`, raw API payload). Read-only.

## htb_machine_active → `machine active`

`details` (boolean → `--details`: synopsis + academy modules). Always live, never cached. Read-only.

## htb_machine_start → `machine start <target>`

| Name | Type | Default | CLI flag |
|---|---|---|---|
| `target` | string | required | positional |
| `wait` | boolean | true | `--wait` (retry full spawn servers, wait for IP; 15-minute MCP timeout) |
| `mode` | enum: `auto` / `play` / `spawn` | `auto` | `--mode` |
| `retryFor` | integer seconds | none | `--retry-for` (CLI default 600) |
| `interval` | integer seconds | none | `--interval` (CLI default 15) |

State-changing, non-destructive. With `wait` the response is `{id, name, ip, spawn}`.

## htb_machine_stop / htb_machine_reset / htb_machine_extend → `machine stop|reset|extend [target]`

`target` optional; omit to act on the active machine. `stop` and `reset` are destructive (kill the box / wipe its state); `extend` is not.

## htb_machine_submit → `machine submit <target> <flag> --difficulty N`

`target`, `flag` required; `difficulty` required integer 10–100 in steps of 10. Irreversible — destructive annotation.

## htb_challenge_list → `challenge list`

`retired` (boolean), `noCache` (boolean). Read-only, cached like machine lists.

## htb_challenge_search → `challenge search <query>`

`query` required; `all`, `retired` booleans; `limit` integer default 20. Read-only.

## htb_challenge_info → `challenge info <target>`

`target` required, `full` boolean. Read-only.

## htb_challenge_submit → `challenge submit <target> <flag> --difficulty N`

Same fields as `htb_machine_submit`. Irreversible. (Route implemented per the v4 machine-own analogue; use `htb_raw` as fallback if the endpoint moved.)

## htb_vpn_servers → `vpn servers [product] [--static]`

| Name | Type | Default | CLI flag |
|---|---|---|---|
| `product` | enum: `labs` / `starting_point` / `competitive` / `fortresses` / `release_arena` | `labs` | positional |
| `static` | boolean | false | `--static` — offline alias table, **needs no API key** |

Read-only.

## htb_vpn_status → `vpn status [product]`

`product` optional string. Read-only.

## htb_vpn_switch → `vpn switch <server>`

`server` required: numeric id, static alias (`us-free-1`), or live name. State-changing (affects the whole HTB connection), non-destructive.

## htb_vpn_download → `vpn download <server> [-o file] [--variant N]`

`server` required; `output` optional path (default `lab-vpn.ovpn` in the toolkit dir); `variant` optional integer (default 0 = UDP). Read-only with respect to account state (writes a local file). The toolkit does not run OpenVPN.

## htb_user_info → `user info`

`full` boolean. Read-only.

## htb_user_progress → `user progress`

Rank, points, next rank and requirements. Read-only, no parameters.

## htb_user_activity → `user activity`

`limit` integer default 20 (`--limit`). Recent owns, newest first. Read-only.

## htb_raw → `raw <METHOD> <path> [--data JSON] [--base-url URL]`

| Name | Type | Default | Description |
|---|---|---|---|
| `method` | enum: GET/POST/PUT/PATCH/DELETE | required | HTTP method |
| `path` | string | required | API path, e.g. `/machine/active` |
| `data` | string | none | raw JSON request body |
| `baseUrl` | string | none | default v4 base; pass `https://labs.hackthebox.com/api/v5` for v5 |

Escape hatch for any endpoint. Can mutate state — `readOnlyHint: false`.

## Recommended agent workflow

1. `htb_doctor` first; fix auth per the error `type` before anything else.
2. Read tools (`machine_search`, `machine_active`, `vpn_status`, `user_progress`) freely; they are cheap and cached where sensible.
3. Confirm with the user before `stop`, `reset`, and both `submit` tools.
4. Prefer one `htb_machine_start` with `wait=true` over polling.

See [../skills/htb/SKILL.md](../skills/htb/SKILL.md) for the full agent-facing playbook.
