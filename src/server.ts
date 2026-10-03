#!/usr/bin/env node
/**
 * htb — MCP server wrapping the HTB Agent Toolkit CLI.
 *
 * Spawns the bundled zero-dependency Python toolkit (toolkit/htb.py) with
 * node:child_process execFile (no shell), parses its compact JSON stdout and
 * structured JSON stderr errors, and exposes one MCP tool per CLI command.
 *
 * Live control of a Hack The Box account: machines, challenges, flags, VPN.
 */
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { z } from "zod";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { execFile } from "node:child_process";
import { promisify } from "node:util";

const execFileAsync = promisify(execFile);

const PKG_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const TOOLKIT_DIR = process.env.HTB_TOOLKIT_DIR ?? path.join(PKG_ROOT, "toolkit");
const HTB_PY = path.join(TOOLKIT_DIR, "htb.py");

const DEFAULT_TIMEOUT_MS = 90_000;
const START_WAIT_TIMEOUT_MS = 15 * 60_000; // machine start --wait retries full spawn servers
const PROFILES_TIMEOUT_MS = 6 * 60_000; // machine search --profiles walks profiles one by one
const MAX_BUFFER = 16 * 1024 * 1024;
const STDERR_TRUNC = 2000;

const server = new McpServer(
  { name: "htb", version: "0.1.0" },
  {
    instructions: [
      "Live control of the user's Hack The Box account (machines, challenges, flags, VPN),",
      "backed by the bundled HTB Agent Toolkit CLI. Run htb_doctor first to verify auth.",
      "Machine, challenge and VPN state is live, not cached.",
      "Confirm with the user before stop/reset/submit actions: stopping kills the running box,",
      "reset wipes its state, and a submitted flag cannot be un-submitted.",
    ].join(" "),
  },
);

const READONLY = {
  readOnlyHint: true,
  destructiveHint: false,
  idempotentHint: true,
  openWorldHint: true,
} as const;

const WRITE_SAFE = {
  readOnlyHint: false,
  destructiveHint: false,
  openWorldHint: true,
} as const;

const WRITE_DESTRUCTIVE = {
  readOnlyHint: false,
  destructiveHint: true,
  openWorldHint: true,
} as const;

// ---------------------------------------------------------------------------
// Core runner
// ---------------------------------------------------------------------------

interface CliResult {
  stdout: string;
  stderr: string;
}

function pythonMissingResult() {
  return {
    content: [
      {
        type: "text" as const,
        text:
          "Could not start Python. The HTB Agent Toolkit requires Python 3.10 or newer on PATH. " +
          "Install it, or point the HTB_PYTHON environment variable at a Python 3.10+ interpreter " +
          "(e.g. C:\\Python312\\python.exe) in the MCP client config.",
      },
    ],
    isError: true,
  };
}

/**
 * Run `htb.py` with the given argument array. Never goes through a shell, so
 * user input is passed verbatim as argv entries. cwd is the toolkit dir so a
 * `.env.local` sitting beside `htb.py` resolves; process.env is inherited so
 * `HTB_API_KEY` from the MCP client config passes through.
 */
async function runHtb(commandArgs: string[], timeoutMs: number): Promise<CliResult> {
  const fullArgs = [HTB_PY, ...commandArgs];
  const opts = {
    cwd: TOOLKIT_DIR,
    env: process.env,
    timeout: timeoutMs,
    maxBuffer: MAX_BUFFER,
    windowsHide: true,
  };
  let py = process.env.HTB_PYTHON ?? "python";
  try {
    return await execFileAsync(py, fullArgs, opts);
  } catch (err: any) {
    if (err?.code === "ENOENT" && py !== "py") {
      // Windows: `python` may be missing while the `py` launcher exists — retry once.
      py = "py";
      try {
        return await execFileAsync(py, fullArgs, opts);
      } catch (err2: any) {
        if (err2?.code === "ENOENT") throw Object.assign(new Error("python-missing"), { pythonMissing: true });
        throw err2;
      }
    }
    if (err?.code === "ENOENT") throw Object.assign(new Error("python-missing"), { pythonMissing: true });
    throw err;
  }
}

function okResult(stdout: string) {
  const text = stdout.trim() || "(no output)";
  let structuredContent: Record<string, unknown> | undefined;
  try {
    const parsed: unknown = JSON.parse(text);
    structuredContent =
      typeof parsed === "object" && parsed !== null && !Array.isArray(parsed)
        ? (parsed as Record<string, unknown>)
        : { result: parsed };
  } catch {
    // not JSON — text content only
  }
  return { content: [{ type: "text" as const, text }], structuredContent };
}

function errorResult(err: any) {
  if (err?.pythonMissing) return pythonMissingResult();
  if (err?.killed) {
    return {
      content: [
        {
          type: "text" as const,
          text: `HTB CLI timed out after ${Math.round((err?.timeoutMs ?? 0) / 1000) || "the configured"} seconds and was killed. ` +
            "For machine starts use wait=true (15-minute budget); for profile scans expect several minutes.",
        },
      ],
      isError: true,
    };
  }
  const stderr = typeof err?.stderr === "string" ? err.stderr.trim() : "";
  const stdout = typeof err?.stdout === "string" ? err.stdout.trim() : "";
  if (err?.code === 2) {
    const detail = (stderr || stdout || "bad arguments").slice(0, STDERR_TRUNC);
    return {
      content: [{ type: "text" as const, text: `HTB usage error (exit 2): ${detail}` }],
      isError: true,
    };
  }
  if (stderr) {
    try {
      const obj = JSON.parse(stderr);
      if (obj && obj.error === true) {
        let msg = `HTB error (${obj.type ?? "unknown"}): ${obj.message ?? "unknown error"}`;
        if (obj.hint) msg += `. ${obj.hint}`;
        return { content: [{ type: "text" as const, text: msg }], isError: true };
      }
    } catch {
      // stderr is not JSON — fall through to raw
    }
  }
  const raw = (stderr || stdout || String(err?.message ?? err)).slice(0, STDERR_TRUNC);
  return {
    content: [{ type: "text" as const, text: `HTB CLI failed (exit ${err?.code ?? "?"}): ${raw}` }],
    isError: true,
  };
}

/** Shared handler tail: run the CLI, map stdout/stderr onto an MCP result. */
async function callCli(commandArgs: string[], timeoutMs = DEFAULT_TIMEOUT_MS) {
  try {
    const { stdout } = await runHtb(commandArgs, timeoutMs);
    return okResult(stdout);
  } catch (err: any) {
    if (err?.killed) err.timeoutMs = timeoutMs;
    return errorResult(err);
  }
}

// ---------------------------------------------------------------------------
// Tools
// ---------------------------------------------------------------------------

server.registerTool(
  "htb_doctor",
  {
    title: "HTB Session Health Check",
    description:
      "Session health check: verifies API key resolution (and where the key came from), API " +
      "connectivity (whoami), the currently active machine, and the VPN assignment in one call. " +
      "Run this first in any session; exits with an error only when the key or API check fails. " +
      "Needs HTB_API_KEY (env var or toolkit/.env.local).",
    inputSchema: {},
    annotations: READONLY,
  },
  async () => callCli(["doctor"]),
);

server.registerTool(
  "htb_machine_list",
  {
    title: "List HTB Machines",
    description:
      "List Hack The Box machines. Default is the playable list (page 1); use filter for retired, " +
      "todo, or unreleased lists, and spTier for Seasonal/starting-point tier lists. Returns compact " +
      "JSON rows (id, name, os, difficulty, points, active, spawned, free). Lists are cached for 1h; " +
      "noCache bypasses the cache.",
    inputSchema: {
      filter: z
        .enum(["playable", "retired", "todo", "unreleased"])
        .default("playable")
        .describe("Which list to fetch (default playable)"),
      spTier: z
        .number()
        .int()
        .min(1)
        .max(3)
        .optional()
        .describe("Starting-point tier 1-3 (overrides filter)"),
      page: z.number().int().min(1).optional().describe("Page number (playable and retired lists only)"),
      noCache: z.boolean().default(false).describe("Bypass the local list cache for this call"),
    },
    annotations: READONLY,
  },
  async ({ filter, spTier, page, noCache }) => {
    const args = ["machine", "list"];
    if (filter === "retired") args.push("--retired");
    else if (filter === "todo") args.push("--todo");
    else if (filter === "unreleased") args.push("--unreleased");
    if (spTier !== undefined) args.push("--sp-tier", String(spTier));
    if (page !== undefined) args.push("--page", String(page));
    if (noCache) args.push("--no-cache");
    return callCli(args);
  },
);

server.registerTool(
  "htb_machine_search",
  {
    title: "Search HTB Machines",
    description:
      "Client-side search over the HTB machine lists, ranked by relevance. Matches id, name, OS, " +
      "difficulty, tags, makers, points, IP. Use `all` to also scan retired machines. `profiles` " +
      "additionally fetches every scanned machine's profile to match description text — powerful " +
      "but SLOW (one API request per machine, can take minutes), use it only when list fields are " +
      "not enough. Follow up with htb_machine_profile on a candidate.",
    inputSchema: {
      query: z.string().min(1).describe("Search terms, e.g. 'kerberos', 'windows easy', or a machine name"),
      all: z.boolean().default(false).describe("Also scan retired machines"),
      retired: z.boolean().default(false).describe("Search only retired machines"),
      profiles: z
        .boolean()
        .default(false)
        .describe("Also match machine description text (slow: one request per machine)"),
      limit: z.number().int().min(1).max(50).default(20).describe("Max results (default 20)"),
      maxPages: z.number().int().min(1).optional().describe("Max list pages to scan (default 10)"),
    },
    annotations: READONLY,
  },
  async ({ query, all, retired, profiles, limit, maxPages }) => {
    const args = ["machine", "search", query];
    if (all) args.push("--all");
    if (retired) args.push("--retired");
    if (profiles) args.push("--profiles");
    args.push("--limit", String(limit));
    if (maxPages !== undefined) args.push("--max-pages", String(maxPages));
    return callCli(args, profiles ? PROFILES_TIMEOUT_MS : DEFAULT_TIMEOUT_MS);
  },
);

server.registerTool(
  "htb_machine_profile",
  {
    title: "HTB Machine Profile",
    description:
      "Show a machine profile by id or name (e.g. 444 or 'BoardLight'). Returns an info block: id, " +
      "name, os, difficulty, points, stars, retired, free, release, maker, user/system owns, tags. " +
      "Set full=true for the complete raw API payload.",
    inputSchema: {
      target: z.string().min(1).describe("Machine id or name (e.g. '444' or 'BoardLight')"),
      full: z.boolean().default(false).describe("Return the raw API payload instead of the curated info block"),
    },
    annotations: READONLY,
  },
  async ({ target, full }) => {
    const args = ["machine", "profile", target];
    if (full) args.push("--full");
    return callCli(args);
  },
);

server.registerTool(
  "htb_machine_active",
  {
    title: "Active HTB Machine",
    description:
      "Show the currently active machine: id, name, ip, os, difficulty, expires_at/expires_in, " +
      "lab server. Always live, never cached. An empty info block means nothing is running. " +
      "details=true adds the synopsis and linked academy modules.",
    inputSchema: {
      details: z.boolean().default(false).describe("Add synopsis and academy modules to the response"),
    },
    annotations: READONLY,
  },
  async ({ details }) => {
    const args = ["machine", "active"];
    if (details) args.push("--details");
    return callCli(args);
  },
);

server.registerTool(
  "htb_machine_start",
  {
    title: "Start HTB Machine",
    description:
      "Start (spawn) a machine by id or name. mode 'auto' (default) tries play then falls back to " +
      "spawn. With wait=true (default) the CLI retries while spawn capacity is full, then waits for " +
      "the IP — this can take several minutes at peak times; the response contains {id, name, ip, " +
      "spawn}. Only one machine can be active at a time: a conflict error names the blocker, stop it " +
      "(with the user's consent) and retry.",
    inputSchema: {
      target: z.string().min(1).describe("Machine id or name to start"),
      wait: z
        .boolean()
        .default(true)
        .describe("Retry a full spawn server and wait for the machine IP (default true)"),
      mode: z.enum(["auto", "play", "spawn"]).optional().describe("Start mode (default auto: play, then spawn)"),
      retryFor: z
        .number()
        .int()
        .min(1)
        .optional()
        .describe("Seconds to keep retrying a full spawn server (default 600)"),
      interval: z.number().int().min(1).optional().describe("Seconds between retries/polls (default 15)"),
    },
    annotations: WRITE_SAFE,
  },
  async ({ target, wait, mode, retryFor, interval }) => {
    const args = ["machine", "start", target];
    if (wait) args.push("--wait");
    if (mode) args.push("--mode", mode);
    if (retryFor !== undefined) args.push("--retry-for", String(retryFor));
    if (interval !== undefined) args.push("--interval", String(interval));
    return callCli(args, wait ? START_WAIT_TIMEOUT_MS : DEFAULT_TIMEOUT_MS);
  },
);

server.registerTool(
  "htb_machine_stop",
  {
    title: "Stop HTB Machine",
    description:
      "Stop (terminate) a machine; defaults to the currently active machine when target is omitted. " +
      "This kills the running box and its state — confirm with the user before calling.",
    inputSchema: {
      target: z.string().min(1).optional().describe("Machine id or name; omit to stop the active machine"),
    },
    annotations: WRITE_DESTRUCTIVE,
  },
  async ({ target }) => {
    const args = ["machine", "stop"];
    if (target) args.push(target);
    return callCli(args);
  },
);

server.registerTool(
  "htb_machine_reset",
  {
    title: "Reset HTB Machine",
    description:
      "Reset a machine to a fresh state; defaults to the currently active machine when target is " +
      "omitted. Wipes the box back to its initial state — confirm with the user before calling.",
    inputSchema: {
      target: z.string().min(1).optional().describe("Machine id or name; omit to reset the active machine"),
    },
    annotations: WRITE_DESTRUCTIVE,
  },
  async ({ target }) => {
    const args = ["machine", "reset"];
    if (target) args.push(target);
    return callCli(args);
  },
);

server.registerTool(
  "htb_machine_extend",
  {
    title: "Extend HTB Machine",
    description:
      "Extend a machine's expiry time; defaults to the currently active machine when target is " +
      "omitted. Non-destructive: the running box and its state are untouched.",
    inputSchema: {
      target: z.string().min(1).optional().describe("Machine id or name; omit to extend the active machine"),
    },
    annotations: WRITE_SAFE,
  },
  async ({ target }) => {
    const args = ["machine", "extend"];
    if (target) args.push(target);
    return callCli(args);
  },
);

server.registerTool(
  "htb_machine_submit",
  {
    title: "Submit Machine Flag",
    description:
      "Submit a user or root flag for a machine (HTB infers which from the flag value). difficulty " +
      "is a required rating in steps of 10 from 10 (piece of cake) to 100 (brainfuck) — ask the user " +
      "if they did not provide one. Submissions are irreversible: confirm with the user first.",
    inputSchema: {
      target: z.string().min(1).describe("Machine id or name"),
      flag: z.string().min(1).describe("The flag, e.g. HTB{...}"),
      difficulty: z
        .number()
        .int()
        .min(10)
        .max(100)
        .multipleOf(10)
        .describe("Difficulty rating 10-100 in steps of 10"),
    },
    annotations: WRITE_DESTRUCTIVE,
  },
  async ({ target, flag, difficulty }) =>
    callCli(["machine", "submit", target, flag, "--difficulty", String(difficulty)]),
);

server.registerTool(
  "htb_challenge_list",
  {
    title: "List HTB Challenges",
    description:
      "List Hack The Box challenges (active by default; retired=true for retired ones). Returns " +
      "compact JSON rows (id, name, category, difficulty, points, retired, state, solved, solves). " +
      "Cached for 1h; noCache bypasses the cache.",
    inputSchema: {
      retired: z.boolean().default(false).describe("List retired challenges instead of active ones"),
      noCache: z.boolean().default(false).describe("Bypass the local list cache for this call"),
    },
    annotations: READONLY,
  },
  async ({ retired, noCache }) => {
    const args = ["challenge", "list"];
    if (retired) args.push("--retired");
    if (noCache) args.push("--no-cache");
    return callCli(args);
  },
);

server.registerTool(
  "htb_challenge_search",
  {
    title: "Search HTB Challenges",
    description:
      "Client-side search over the (cached) challenge lists, ranked by relevance. Matches id, name, " +
      "category, difficulty, points, state. Use `all` to also scan retired challenges. Follow up " +
      "with htb_challenge_info on a candidate.",
    inputSchema: {
      query: z.string().min(1).describe("Search terms, e.g. 'crypto' or 'web easy'"),
      all: z.boolean().default(false).describe("Also scan retired challenges"),
      retired: z.boolean().default(false).describe("Search only retired challenges"),
      limit: z.number().int().min(1).max(50).default(20).describe("Max results (default 20)"),
    },
    annotations: READONLY,
  },
  async ({ query, all, retired, limit }) => {
    const args = ["challenge", "search", query];
    if (all) args.push("--all");
    if (retired) args.push("--retired");
    args.push("--limit", String(limit));
    return callCli(args);
  },
);

server.registerTool(
  "htb_challenge_info",
  {
    title: "HTB Challenge Info",
    description:
      "Show a challenge by id or name (names resolve case-insensitively). Returns an info block: " +
      "id, name, category, difficulty, points, stars, retired, state, solved, release, description, " +
      "solves, maker. Set full=true for the raw API payload.",
    inputSchema: {
      target: z.string().min(1).describe("Challenge id or name"),
      full: z.boolean().default(false).describe("Return the raw API payload instead of the curated info block"),
    },
    annotations: READONLY,
  },
  async ({ target, full }) => {
    const args = ["challenge", "info", target];
    if (full) args.push("--full");
    return callCli(args);
  },
);

server.registerTool(
  "htb_challenge_submit",
  {
    title: "Submit Challenge Flag",
    description:
      "Submit a challenge flag. difficulty is a required rating in steps of 10 from 10 to 100 — ask " +
      "the user if they did not provide one. Submissions are irreversible: confirm with the user " +
      "first.",
    inputSchema: {
      target: z.string().min(1).describe("Challenge id or name"),
      flag: z.string().min(1).describe("The flag, e.g. HTB{...}"),
      difficulty: z
        .number()
        .int()
        .min(10)
        .max(100)
        .multipleOf(10)
        .describe("Difficulty rating 10-100 in steps of 10"),
    },
    annotations: WRITE_DESTRUCTIVE,
  },
  async ({ target, flag, difficulty }) =>
    callCli(["challenge", "submit", target, flag, "--difficulty", String(difficulty)]),
);

server.registerTool(
  "htb_vpn_servers",
  {
    title: "List HTB VPN Servers",
    description:
      "List the VPN servers the account can use, live (includes VIP/VIP+/dedicated pools), assigned " +
      "server first. Rows: id, name, group, location, clients, full, assigned. Default product pool " +
      "is 'labs'. static=true shows only the built-in offline alias table and needs NO API key.",
    inputSchema: {
      product: z
        .enum(["labs", "starting_point", "competitive", "fortresses", "release_arena"])
        .optional()
        .describe("Product pool to list (default labs)"),
      static: z
        .boolean()
        .default(false)
        .describe("Show only the offline alias table (works without an API key)"),
    },
    annotations: READONLY,
  },
  async ({ product, static: useStatic }) => {
    const args = ["vpn", "servers"];
    if (product) args.push(product);
    if (useStatic) args.push("--static");
    return callCli(args);
  },
);

server.registerTool(
  "htb_vpn_status",
  {
    title: "HTB VPN Status",
    description:
      "Show the VPN server currently assigned to the account: id, name, location, product — or " +
      "assigned=false when none.",
    inputSchema: {
      product: z.string().optional().describe("Product pool, e.g. 'labs' (default)"),
    },
    annotations: READONLY,
  },
  async ({ product }) => {
    const args = ["vpn", "status"];
    if (product) args.push(product);
    return callCli(args);
  },
);

server.registerTool(
  "htb_vpn_switch",
  {
    title: "Switch HTB VPN Server",
    description:
      "Switch the account to a different VPN server. Accepts a numeric id (289), a static alias " +
      "(us-free-1, eu-sp-1, ...) or a live name from htb_vpn_servers (e.g. 'EU Machines VIP+ 1'). " +
      "Affects the user's whole HTB connection — mention it before switching.",
    inputSchema: {
      server: z.string().min(1).describe("Server id, alias like 'us-free-1', or live name"),
    },
    annotations: WRITE_SAFE,
  },
  async ({ server: srv }) => callCli(["vpn", "switch", srv]),
);

server.registerTool(
  "htb_vpn_download",
  {
    title: "Download HTB VPN Config",
    description:
      "Download an OVPN config file for a server (id, alias, or live name). Default output is " +
      "lab-vpn.ovpn in the toolkit directory; variant 0 = UDP. The toolkit does NOT run OpenVPN — " +
      "the user connects themselves with the downloaded file.",
    inputSchema: {
      server: z.string().min(1).describe("Server id, alias like 'us-free-1', or live name"),
      output: z.string().optional().describe("Output file path (default lab-vpn.ovpn)"),
      variant: z.number().int().min(0).optional().describe("Config variant (default 0 = UDP)"),
    },
    annotations: READONLY,
  },
  async ({ server: srv, output, variant }) => {
    const args = ["vpn", "download", srv];
    if (output) args.push("-o", output);
    if (variant !== undefined) args.push("--variant", String(variant));
    return callCli(args);
  },
);

server.registerTool(
  "htb_user_info",
  {
    title: "HTB User Profile",
    description:
      "Show the user's HTB profile: id, name, rank, points, ranking, user/system owns, respects, " +
      "country, team, vip. Set full=true for the raw profile payload.",
    inputSchema: {
      full: z.boolean().default(false).describe("Return the raw API payload instead of the curated info block"),
    },
    annotations: READONLY,
  },
  async ({ full }) => {
    const args = ["user", "info"];
    if (full) args.push("--full");
    return callCli(args);
  },
);

server.registerTool(
  "htb_user_progress",
  {
    title: "HTB Rank Progress",
    description:
      "Show rank progression: current rank and points, next rank and its points requirement, " +
      "current_rank_progress / rank_requirement (percent), rank_ownership, and own counts. " +
      "Use this to answer 'how close am I to ranking up?'.",
    inputSchema: {},
    annotations: READONLY,
  },
  async () => callCli(["user", "progress"]),
);

server.registerTool(
  "htb_user_activity",
  {
    title: "HTB User Activity",
    description:
      "Show recent owns (machine user/root flags and challenges), newest first. Rows: date, type " +
      "(user/root/challenge), id, name, points, blood.",
    inputSchema: {
      limit: z.number().int().min(1).max(100).default(20).describe("Max entries (default 20)"),
    },
    annotations: READONLY,
  },
  async ({ limit }) => callCli(["user", "activity", "--limit", String(limit)]),
);

server.registerTool(
  "htb_raw",
  {
    title: "Raw HTB API Call",
    description:
      "Escape hatch: call any HTB API endpoint directly. Hits the v4 base " +
      "(https://labs.hackthebox.com/api/v4) by default — pass baseUrl " +
      "'https://labs.hackthebox.com/api/v5' for v5 endpoints. path like '/machine/active' (leading " +
      "slash optional); data takes a raw JSON string body. Set output to save a binary response " +
      "(for example, GET /challenge/download/<id>) instead of decoding it as text. Relative output " +
      "paths resolve under the toolkit directory. Use when a typed tool is missing or an endpoint moved.",
    inputSchema: {
      method: z.enum(["GET", "POST", "PUT", "PATCH", "DELETE"]).describe("HTTP method"),
      path: z.string().min(1).describe("API path, e.g. '/machine/active'"),
      data: z.string().optional().describe("Raw JSON request body string"),
      baseUrl: z.string().optional().describe("Override API base URL (use the v5 URL for v5 endpoints)"),
      output: z.string().min(1).optional().describe("Save the response body to this file path"),
    },
    annotations: {
      readOnlyHint: false,
      openWorldHint: true,
    },
  },
  async ({ method, path: apiPath, data, baseUrl, output }) => {
    const args = ["raw", method, apiPath];
    if (data !== undefined) args.push("--data", data);
    if (baseUrl !== undefined) args.push("--base-url", baseUrl);
    if (output !== undefined) args.push("--output", output);
    return callCli(args);
  },
);

// ---------------------------------------------------------------------------

async function main() {
  const transport = new StdioServerTransport();
  await server.connect(transport);
  console.error("htb running on stdio");
}

main().catch((err) => {
  console.error("Fatal error:", err);
  process.exit(1);
});
