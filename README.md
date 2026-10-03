# htb-mcp

MCP server that gives AI agents live control of [Hack The Box](https://www.hackthebox.com) labs — machines, challenges, flags and VPN — by wrapping the bundled, zero-dependency **HTB Agent Toolkit** CLI.

Unlike index-based servers, nothing here is cached content: every tool call drives the real HTB Labs API for your account. The server is a thin, safe adapter: it spawns `toolkit/htb.py` (Python 3.10+, standard library only) with argument arrays (no shell), returns the CLI's compact JSON as structured content, and maps its structured stderr errors (`config`, `auth`, `rate_limit`, `conflict`, `state`, ...) onto MCP errors.

## Quick start

Requirements: Node.js 22.13 or newer **and** Python 3.10 or newer on PATH (set `HTB_PYTHON` if yours is elsewhere).

**Claude Code:**

```bash
claude mcp add htb -- npx -y @zebbern/htb
```

**Codex CLI:**

```bash
codex mcp add htb -- npx -y @zebbern/htb
```

**Any MCP client** (Claude Desktop, Cursor, Kimi, etc.), config JSON — `HTB_API_KEY` is optional if you instead create `toolkit/.env.local` inside the installed package:

```json
{
  "mcpServers": {
    "htb": {
      "command": "npx",
      "args": ["-y", "@zebbern/htb"],
      "env": { "HTB_API_KEY": "your-htb-app-token-here" }
    }
  }
}
```

Create an App Token at https://app.hackthebox.com/profile/settings (App Tokens section). Then call `htb_doctor` once to verify the session.

**As a plugin** (bundles the agent skill that teaches safe, effective usage): this repo is a valid plugin for Claude Code (`.claude-plugin/`), Codex (`.codex-plugin/`) and Kimi (`kimi-plugin/`). Add it from your client's plugin marketplace flow pointing at `zebbern/htb-mcp`, or for Kimi Work use [this plugin link](kimi-work://plugin?id=htb).

Then ask things like:

- *"Check my HTB session and show which machine is active"*
- *"Spawn BoardLight and give me the IP"*
- *"Submit this flag for machine 444 as difficulty 50: HTB{...}"*
- *"Switch my VPN to us-free-1 and download the config"*

## Tools at a glance

| Tool | What it does |
|---|---|
| `htb_doctor` | Session health check: API key source, whoami, active machine, VPN assignment — run it first |
| `htb_machine_list` / `htb_machine_search` | List (playable/retired/todo/unreleased/SP-tier) and ranked search over machines |
| `htb_machine_profile` / `htb_machine_active` | Machine details by id or name; what's running right now (live) |
| `htb_machine_start` | Spawn a box; `wait=true` retries full spawn servers and returns the IP |
| `htb_machine_stop` / `htb_machine_reset` / `htb_machine_extend` | Lifecycle control of the active (or named) machine |
| `htb_machine_submit` | Submit a user/root flag with a difficulty rating (10–100) |
| `htb_challenge_list` / `htb_challenge_search` / `htb_challenge_info` | Browse and search challenges |
| `htb_challenge_submit` | Submit a challenge flag |
| `htb_vpn_servers` / `htb_vpn_status` | List usable VPN servers (`static=true` works with no API key); current assignment |
| `htb_vpn_switch` / `htb_vpn_download` | Switch servers; download OVPN configs |
| `htb_user_info` / `htb_user_progress` / `htb_user_activity` | Profile, rank progression, recent owns |
| `htb_raw` | Escape hatch: call any HTB API endpoint directly (v4 by default, v5 via `baseUrl`); `output` saves binary responses such as challenge archives |

Read-only tools are annotated `readOnlyHint`; `stop`, `reset` and both `submit` tools are annotated `destructiveHint`. Full reference: [docs/tools.md](docs/tools.md).

## Documentation

- [docs/tools.md](docs/tools.md): complete tool reference with parameters and error types
- [docs/architecture.md](docs/architecture.md): the wrapper design (process model, timeouts, error mapping)
- [docs/development.md](docs/development.md): local setup, smoke test, releasing
- [docs/agents.md](docs/agents.md): agent skill, MCP registry and plugin packaging

## Security

- **Token handling:** the App Token is read by the bundled Python toolkit from `HTB_API_KEY` (inherited from your MCP client config) or `toolkit/.env.local`. The MCP server never reads, stores, logs or prints the key — it only passes the environment through to the child process.
- **Never commit `.env.local`.** It is covered by `.gitignore`, and by both the root `.npmignore` and `toolkit/.npmignore` so `npm pack` cannot ship it; the release workflow hard-fails if a tarball ever contains it or the `.cache` directory.
- **No shell injection:** commands are built as argument arrays and run with `execFile`, `shell: false` — machine names, flags and raw JSON bodies are passed verbatim as argv entries.
- **State-changing tools are annotated:** read-only vs destructive hints let MCP hosts gate `stop`/`reset`/`submit`. Annotations don't replace consent — the bundled agent skill instructs agents to confirm before destructive or irreversible actions.
- This controls your real HTB account against live labs. Use it only for your own authorized HTB activity.

## Credits

- [HTB Agent Toolkit](https://github.com/zebbern/htb) — the bundled CLI (MIT), vendored under `toolkit/`
- [Model Context Protocol SDK](https://github.com/modelcontextprotocol/typescript-sdk)
- [Hack The Box](https://www.hackthebox.com) — this is an unofficial client of the public Labs API, not affiliated with or endorsed by Hack The Box Ltd
