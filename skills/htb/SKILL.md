---
name: htb
description: Control Hack The Box labs live via the htb tools. Use when the task involves HTB machines, challenges, flags, VPN servers, rank progress, or CTF boxes — starting/stopping boxes, searching machines and challenges, submitting flags, or managing lab VPN access.
---

# HTB control skill

You have access to the htb MCP server: 22 tools prefixed `htb_` that wrap the HTB Agent Toolkit CLI and drive the user's live Hack The Box account (machines, challenges, flags, VPN). Every tool returns compact curated JSON (also as structured content); errors come back as `HTB error (<type>): <message>. <hint>`.

## First call of any session: htb_doctor

Run `htb_doctor` before anything else. It reports key resolution (and where the key came from), API connectivity (whoami), the active machine, and the VPN assignment in one JSON report. `machine.active` is `null` when nothing is running; `vpn.server` is `null` when no server is assigned.

## Auth troubleshooting

- `HTB error (config)` — no API key found. Tell the user to either set `HTB_API_KEY` in the MCP client config env, or create `toolkit/.env.local` (copy `toolkit/.env.example`) containing `HTB_API_KEY=<their-app-token>`. Tokens are created at https://app.hackthebox.com/profile/settings (App Tokens section).
- `HTB error (auth)` — the key was rejected (HTTP 401). Tell the user to fix or regenerate the App Token; the hint field says how.
- `HTB error (rate_limit)` — HTTP 429 after the CLI's built-in retries. Back off and retry later. Do NOT add your own tight retry loop; the CLI already retries 429s with exponential backoff.
- `HTB error (conflict)` — a state conflict, typically another active machine blocking a start. The message names the blocker: stop it first (after confirming with the user), then retry.
- Never print, log, or repeat the API key. It is never needed as a tool argument.

## Common workflows

**Start a box and get its IP:** `htb_machine_start` with the name (e.g. `BoardLight`); `wait` defaults to true, so the call retries a full spawn server and returns when the IP is assigned — read `ip` from the result. If it fails with a conflict naming a different active machine, stop that one first (confirm with the user) and retry.

**Find a box by theme:** `htb_machine_search` with a technical term (`kerberos`, `windows easy`), `all=true` to include retired machines. Add `profiles=true` only when the theme lives in description text — it is slow (one API request per machine, minutes). Follow up with `htb_machine_profile` on a candidate.

**Submit a flag:** `htb_machine_submit` / `htb_challenge_submit` take the target, the flag, and a difficulty rating 10–100 in steps of 10 (10 = piece of cake, 100 = brainfuck). Ask the user for the rating if they did not give one. Submissions are irreversible.

**Download challenge files:** Get the challenge id with `htb_challenge_info`, then call `htb_raw` with `method=GET`, `path=/challenge/download/<id>`, and an absolute `output` file path. The API redirects to a signed file URL; the toolkit drops the HTB authorization header on cross-origin redirects. Always set `output` for archives so binary content is saved as bytes.

**Check rank progress:** `htb_user_progress` — read `rank`, `points`, `next_rank` / `next_rank_points`, and `current_rank_progress` / `rank_requirement` (percent). `htb_user_activity` shows the recent owns feeding that progress.

**Switch VPN and get a config:** `htb_vpn_status` (what is assigned), `htb_vpn_servers` (what is available; `static=true` lists the offline alias table with no API key), `htb_vpn_switch <server>` (id, alias like `us-free-1`, or live name), then `htb_vpn_download <server>` for the OVPN file. The toolkit does not run OpenVPN — the user connects with the downloaded file themselves.

## Safety rules

- Confirm with the user before `htb_machine_stop`, `htb_machine_reset`, `htb_machine_submit`, and `htb_challenge_submit`: stopping kills the running box, reset wipes its state, and a submitted flag cannot be un-submitted. State-changing tools carry annotations, but annotations do not replace asking.
- Only one machine can be active at a time; do not start a second one without resolving the conflict first.
- No tight polling loops. One `htb_machine_start` with `wait=true` already retries internally; lists are cached for an hour; `htb_machine_active` is always live.
- VPN switching affects the user's whole HTB connection — mention it before calling `htb_vpn_switch`.
- `htb_raw` is the escape hatch for any HTB API endpoint (v4 base by default; pass baseUrl `https://labs.hackthebox.com/api/v5` for v5). It can save binary responses with `output`. Prefer the typed tools when they cover the need.

## Boundaries

- This controls the user's real HTB account against live labs. Use it for the user's own authorized HTB activity only.
- Never invent tokens, flags, or machine names; resolve names with search/profile tools first.
