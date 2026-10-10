# Architecture

htb-mcp is a **wrapper**, not an index: unlike its sibling hacktricks-mcp (which ships a pre-built SQLite FTS index of a wiki), htb-mcp contains no data of its own. It spawns the bundled HTB Agent Toolkit CLI for every tool call and relays its JSON.

## Components

```
MCP client (Claude Code / Codex / Kimi / ...)
        |  stdio
        v
dist/server.js (McpServer, 22 tools)
        |  execFile (no shell), argv array, cwd = toolkit/
        v
toolkit/htb.py  (HTB Agent Toolkit, Python 3.10+, stdlib only)
        |  HTTPS
        v
Hack The Box Labs API (v4 default, some v5)
```

## Why wrap the CLI instead of re-implementing the API

- **One contract, already tested.** The toolkit owns auth resolution, rate-limit retries with `Retry-After`, spawn-capacity retry budgets, preservation of existing assignments, name→id resolution, and list caching. Re-implementing that in TypeScript would duplicate a maintained codebase for zero user-visible gain.
- **Zero npm dependency surface for the data plane.** The toolkit is Python stdlib only; the MCP server adds just `@modelcontextprotocol/sdk` and `zod`.
- **Machine-readable by design.** Compact JSON on stdout and one structured error object on stderr map cleanly onto MCP text/structured content and `isError` results.

## Process model

- `execFile` (promisified) with an **argv array**, `shell: false` — user input (machine names, flags, raw JSON bodies) is never concatenated into a shell string.
- `cwd` is the toolkit dir so `.env.local` beside `htb.py` resolves; `process.env` is inherited so `HTB_API_KEY` from the MCP client config passes through untouched. `PYTHONIOENCODING=utf-8` ensures Python's pipe encoding agrees with Node on Windows. The key is never logged or printed.
- Python resolution: `HTB_PYTHON` env var → `python` → retry once with `py` (Windows launcher). If all fail, the tool returns an MCP error explaining Python 3.10+ is required and `HTB_PYTHON` can point at it. `HTB_TOOLKIT_DIR` overrides the bundled `toolkit/` location.

## Timeouts

| Call | Budget | Why |
|---|---|---|
| default | 90 s | cached lists, single API calls |
| `htb_machine_start` with `wait=true` | 15 min | outer process limit; the CLI's default 600-second budget covers both spawn retries and IP readiness |
| `htb_machine_search` with `profiles=true` | 6 min | one profile request per scanned machine |

On timeout the child process is killed and the tool returns a clear error.

## Error mapping

- exit 0 → stdout JSON becomes text content plus `structuredContent` (generic shape; outputs vary per tool, so there are no per-tool outputSchemas).
- exit 1 → the final stderr JSON error object becomes `HTB error (<type>): <message>. <hint>`, `isError: true`, even when progress or rate-limit warnings precede it. Non-JSON stderr is included raw (truncated to ~2,000 chars).
- exit 2 → argparse usage error, reported with the argparse output.

The Python client also rejects explicit top-level `success:false` or `error:true` payloads returned with HTTP 200. For mutation requests, `status:false` also signals failure; for GET requests it can be a legitimate state value. HTTP acceptance alone is not proof that an operation succeeded.

## State and secrets on disk

| Path | Content | Shipped in npm? | Committed? |
|---|---|---|---|
| `toolkit/` | the bundled CLI (htb.py, htb_agent/, launchers, LICENSE, .env.example) | yes | yes |
| `toolkit/.env.local` | local App Token (convenience copy for this machine) | **no** (`.npmignore`, root and nested) | **no** (`.gitignore`) |
| `toolkit/.cache/` | machine/challenge list cache, TTL `HTB_CACHE_TTL` | no | no |

The release workflow additionally greps `npm pack --dry-run` output and fails the build if either path ever appears in the tarball.
