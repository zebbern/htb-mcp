# Development

Local setup, testing and releasing. Design details are in [architecture.md](architecture.md); tool behavior is in [tools.md](tools.md).

## Setup

```bash
git clone https://github.com/zebbern/htb-mcp.git
cd htb-mcp
npm install
```

Requires Node.js 22.13 or newer, and Python 3.10 or newer on PATH for the bundled toolkit (`HTB_PYTHON` can point at a specific interpreter).

## Scripts

| Command | What it does |
|---|---|
| `npm run build` | Compile TypeScript to `dist/` |
| `npm run smoke` | End-to-end MCP test over stdio against the built server |
| `npm test` | Python regressions plus MCP → Python → local HTTP fixture integration tests |
| `npm run sync:skills` | Copy the canonical skill into `kimi-plugin/` |
| `npm start` | Run the built server on stdio |

Typical loop after touching `src/server.ts`:

```bash
npm run build && npm test && npm run smoke
```

The smoke test needs **no API key**: it lists tools, then calls `htb_vpn_servers {static: true}` (offline alias table), then `htb_doctor` — a failing doctor is reported but does not fail the run, so CI works without secrets.

Regression tests use synthetic data and a loopback HTTP server with a test-only key. They cover multiword search, Windows Unicode output, list-filter precedence, provisioning readiness and timeouts, preservation of existing machines, reset preconditions, application errors inside HTTP 200 responses, and skill drift checks with Windows line endings. They do not mutate a real HTB account or claim that HTB's live provisioning service is healthy.

For live testing against your own account, put `HTB_API_KEY=<your-app-token>` in `toolkit/.env.local` (copy `toolkit/.env.example`). This file is git-ignored and npm-ignored; never commit it.

## Toolkit updates

`toolkit/` is a vendored copy of the [HTB Agent Toolkit](https://github.com/zebbern/htb). To refresh it, copy the new `htb.py`, `htb_agent/`, `htb`, `htb.bat`, `.env.example` and `LICENSE` over the existing files (leave out the upstream repo's `.git`, `tests/`, `docs/`, caches and env files). Keep [tools.md](tools.md) in sync when the CLI's command surface changes.

## Releasing to npm

The package name is `@zebbern/htb`.

### One-time manual publish

The first publish must come from a logged-in machine:

```bash
npm login
npm publish --access public
```

For a version already prepared and committed without a `v*` tag, run `npm whoami` and then `npm publish --access public` from the repository root. Do not push a `v*` tag for the same version after publishing manually: the tag triggers the GitHub release workflow, which also publishes to npm.

`prepublishOnly` runs the build automatically. The published tarball includes `dist/`, `toolkit/` (minus `.env.local` and `.cache/`) and `skills/`.

### Automated releases (trusted publishing)

Everything runs off version tags:

```bash
npm version patch   # or minor / major
git push --follow-tags
```

`npm version` automatically runs `scripts/version-sync.mjs` (npm lifecycle hook), which stamps the new version into `server.json` and both plugin manifests and stages them, so the version commit is always consistent. `kimi-plugin/kimi.plugin.json` is excluded on purpose: the Kimi marketplace manages its own numbering.

The `release.yml` workflow then: builds, runs regression and smoke tests, **verifies the tarball contains no `.env.local` or `.cache`** (hard fail otherwise), creates the GitHub Release, and publishes to npm via OIDC trusted publishing (no `NPM_TOKEN` anywhere). MCP registry publishing is intentionally local, see [agents.md](agents.md#mcp-registry).

### What ships in the package

Defined by `files` in `package.json`: `dist/`, `toolkit/`, `skills/`. Root `.npmignore` and nested `toolkit/.npmignore` exclude the token file, the runtime cache, sources and dev files. Always sanity-check with `npm pack --dry-run` before publishing.

## CI

- `ci.yml` on push/PR: build, regression tests, skill-drift guard, stdio smoke test (keyless), on Linux and Windows.
- There is no sync workflow: unlike hacktricks-mcp this repo has no upstream index to refresh.
