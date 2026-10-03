# Agents: skill, registry and plugin packaging

How htb-mcp shows up inside agent ecosystems. The artifacts referenced here live in `skills/` and `server.json`; this page explains the intent so there is one place to update the reasoning.

## Why a skill on top of an MCP server

The MCP tools tell an agent *what* it can call. A skill tells the agent *how to be good at it*: run `htb_doctor` first, how to read the structured error types (`config` vs `auth` vs `rate_limit` vs `conflict`), when `profiles=true` is worth its minutes-long cost, and — critically for a toolset that mutates a live account — when to stop and ask the user before `stop`/`reset`/`submit`. Skills are cheap (a markdown file) and measurably improve tool use quality.

This repo ships `skills/htb/SKILL.md` in the [Agent Skills](https://agentskills.io) format (YAML frontmatter plus instructions). The same file works in Claude Code, Kimi Work, Cursor and other hosts that support skills. It is documentation for agents, not executable code.

When editing tool behavior in `src/server.ts`, keep the skill in sync: the skill describes the workflow, [tools.md](tools.md) describes the API.

## MCP registry

`server.json` at the repo root is the manifest for the [official MCP registry](https://registry.modelcontextprotocol.io). It declares the optional `HTB_API_KEY` environment variable (marked secret) so registry UIs can prompt for it. Publishing is a local, one-command step after each npm release (kept out of CI by design):

```bash
mcp-publisher login github   # device flow, once per machine
mcp-publisher publish        # from the repo root, after npm has the new version
```

The registry verifies npm ownership via the `mcpName` field in `package.json`, and requires `server.json` `version` + `packages[].version` to match the published npm version exactly. `description` must stay under 100 characters.

## Claude Code, Codex and Kimi plugin packaging

This repo doubles as an installable plugin for three ecosystems:

- `.claude-plugin/plugin.json` + `.mcp.json`: Claude Code plugin format
- `.codex-plugin/plugin.json` + `mcp.json`: Codex plugin format
- `kimi-plugin/`: Kimi Work plugin, registered into the personal marketplace from that directory

**One skill, one file.** The canonical skill is `skills/htb/SKILL.md` in the [Agent Skills](https://agentskills.io) format. Claude Code and Codex auto-discover `skills/` at the plugin root, so they read this file directly with no copy. Kimi plugins are installed as self-contained directories, so `kimi-plugin/skills/htb/SKILL.md` is a generated copy: never edit it directly, run `npm run sync:skills` after changing the canonical file. CI fails on drift (`sync-skills.mjs --check`), and the `npm version` hook re-syncs automatically.

When bumping versions, the same hook stamps `server.json` and both plugin manifests. There is no extra code to maintain.

## Claude Desktop / Cursor / generic MCP

No plugin wrapper needed: the server is plain stdio MCP. The README quick-start config (with the optional `env.HTB_API_KEY`) covers these clients.

## A note on trust

This plugin can act on the user's real HTB account. The skill and the server instructions both direct agents to confirm destructive actions, and the destructive tools carry `destructiveHint` annotations so hosts can gate them. The token never leaves the local machine: it is read from the client-injected env var or `toolkit/.env.local` and passed to the HTB API by the bundled Python toolkit — the MCP server itself never sees, stores, or logs it.
