import assert from "node:assert/strict";
import { createServer } from "node:http";
import { once } from "node:events";
import path from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StdioClientTransport } from "@modelcontextprotocol/sdk/client/stdio.js";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

test("MCP tools preserve API contracts through the real Python subprocess", async (t) => {
  let mode = "ready";
  let polls = 0;
  const requests: string[] = [];
  const bodies: Array<{ route: string; body: unknown }> = [];
  const api = createServer(async (req, res) => {
    const chunks: Buffer[] = [];
    for await (const chunk of req) chunks.push(Buffer.from(chunk));
    requests.push(`${req.method} ${req.url}`);
    const url = new URL(req.url!, "http://localhost");
    const route = url.pathname.replace(/^\/api\/v[45]/, "");
    const rawBody = Buffer.concat(chunks).toString("utf8");
    if (rawBody) bodies.push({ route, body: JSON.parse(rawBody) });
    let body: unknown;
    if (route === "/challenge/info/250") {
      body = { challenge: { id: 250, name: "Protein Cookies", description: "Cookies 🍪 — café" } };
    } else if (route === "/machine/paginated") {
      body = { data: [{ id: 987, name: "Touch", os: "Windows", difficultyText: "Easy" }] };
    } else if (route === "/challenge/list") {
      body = { challenges: [{ id: 250, name: "Protein Cookies", difficulty: "Easy", challenge_category_id: 1 }] };
    } else if (route === "/challenge/categories/list") {
      body = { info: [{ id: 1, name: "Crypto" }] };
    } else if (route === "/machines") {
      assert.equal(url.searchParams.get("spTier"), "1");
      body = { data: [{ id: 394, name: "Meow" }] };
    } else if (route === "/machine/play/351") {
      polls = 0;
      body = mode === "rejected" ? { success: false, message: "Start rejected 🍪" } : { success: true, message: "Machine deployed to lab." };
    } else if (route === "/machine/active") {
      polls++;
      const ready = mode === "ready" && polls > 1;
      body = { info: { id: 351, name: "Cap", ip: ready ? "10.10.10.1" : null, isSpawning: !ready } };
    } else if (route === "/explicit-error") {
      body = { success: false, message: "Rejected" };
    } else if (route === "/vm/reset") {
      body = { success: true, message: "Machine reset." };
    } else if (route === "/vm/extend") {
      body = { success: true, message: "Machine extended." };
    } else if (route === "/machine/own") {
      assert.match(url.pathname, /^\/api\/v5\//);
      body = { success: true, message: "Machine flag accepted." };
    } else if (route === "/challenge/own") {
      body = { success: true, message: "Challenge flag accepted." };
    } else {
      res.statusCode = 404;
      body = { message: `Unexpected test route: ${route}` };
    }
    res.setHeader("Content-Type", "application/json");
    res.end(JSON.stringify(body));
  });
  api.listen(0, "127.0.0.1");
  await once(api, "listening");
  const port = (api.address() as { port: number }).port;
  const client = new Client({ name: "regression-test", version: "1.0.0" });
  const transport = new StdioClientTransport({
    command: process.execPath,
    args: [path.join(root, "dist", "server.js")],
    stderr: "pipe",
    env: {
      ...process.env,
      HTB_TOOLKIT_DIR: path.join(root, "toolkit"),
      HTB_API_KEY: "test-only-key",
      HTB_API_BASE_URL: `http://127.0.0.1:${port}/api/v4`,
      HTB_CACHE_TTL: "0",
      PYTHONIOENCODING: "cp1252",
      PYTHONUTF8: "0",
    } as Record<string, string>,
  });
  transport.stderr?.resume();
  try {
    await client.connect(transport);
    const call = (name: string, args: Record<string, unknown>) => client.callTool({ name, arguments: args });
    await t.test("Unicode survives a legacy Windows parent encoding", async () => {
      const result = await call("htb_challenge_info", { target: "250" });
      assert.notEqual(result.isError, true, JSON.stringify(result));
      assert.equal((result.structuredContent as any).info.description, "Cookies 🍪 — café");
    });
    await t.test("multiword machine and challenge searches", async () => {
      for (const [name, query, id] of [["htb_machine_search", "windows easy", 987], ["htb_challenge_search", "crypto easy", 250]] as const) {
        const result = await call(name, { query, limit: 5 });
        assert.notEqual(result.isError, true, JSON.stringify(result));
        assert.equal((result.structuredContent as any).result[0]?.id, id);
      }
    });
    await t.test("starting-point tier overrides the list filter", async () => {
      const result = await call("htb_machine_list", { filter: "retired", spTier: 1 });
      assert.notEqual(result.isError, true, JSON.stringify(result));
      assert.equal((result.structuredContent as any).result[0].id, 394);
    });
    await t.test("start waits until the target has a ready IP", async () => {
      mode = "ready";
      const result = await call("htb_machine_start", { target: "351", wait: true, retryFor: 3, interval: 1 });
      assert.notEqual(result.isError, true, JSON.stringify(result));
      assert.equal(result.structuredContent?.ip, "10.10.10.1");
      assert.ok(polls >= 2);
    });
    await t.test("progress stderr does not hide a final structured error", async () => {
      mode = "pending";
      const result = await call("htb_machine_start", { target: "351", wait: true, retryFor: 1, interval: 1 });
      assert.equal(result.isError, true);
      const text = (result.content as any[])[0].text;
      assert.match(text, /^HTB error \(state\):/);
      assert.match(text, /isSpawning=true/);
      assert.doesNotMatch(text, /waiting for machine IP/);
    });
    await t.test("reset only reaches the API for a ready matching assignment", async () => {
      mode = "pending";
      const before = requests.filter((r) => r === "POST /api/v4/vm/reset").length;
      const pending = await call("htb_machine_reset", { target: "351" });
      assert.equal(pending.isError, true);
      assert.match((pending.content as any[])[0].text, /still provisioning/);
      assert.equal(requests.filter((r) => r === "POST /api/v4/vm/reset").length, before);
      mode = "ready";
      const ready = await call("htb_machine_reset", { target: "351" });
      assert.notEqual(ready.isError, true, JSON.stringify(ready));
      assert.equal(requests.filter((r) => r === "POST /api/v4/vm/reset").length, before + 1);
    });
    await t.test("HTTP 200 with a failure body is an MCP error", async () => {
      mode = "rejected";
      const result = await call("htb_machine_start", { target: "351", wait: false });
      assert.equal(result.isError, true);
      assert.match((result.content as any[])[0].text, /Start rejected 🍪/);
      const raw = await call("htb_raw", { method: "GET", path: "/explicit-error" });
      assert.equal(raw.isError, true);
    });
    await t.test("extend and flag submissions use the expected API contracts", async () => {
      for (const [name, args] of [
        ["htb_machine_extend", { target: "351" }],
        ["htb_machine_submit", { target: "351", flag: "HTB{synthetic-machine}", difficulty: 10 }],
        ["htb_challenge_submit", { target: "250", flag: "HTB{synthetic-challenge}", difficulty: 10 }],
      ] as const) {
        const result = await call(name, args);
        assert.notEqual(result.isError, true, JSON.stringify(result));
      }
      assert.deepEqual(bodies.find((item) => item.route === "/vm/extend")?.body, { machine_id: 351 });
      assert.deepEqual(bodies.find((item) => item.route === "/machine/own")?.body, {
        id: 351, flag: "HTB{synthetic-machine}", difficulty: 10,
      });
      assert.deepEqual(bodies.find((item) => item.route === "/challenge/own")?.body, {
        challenge_id: 250, flag: "HTB{synthetic-challenge}", difficulty: 10,
      });
    });
    assert.ok(requests.length > 0);
    assert.ok(requests.every((r) => !r.includes("/vm/terminate")), "start must never silently terminate an assignment");
  } finally {
    await client.close();
    api.close();
    await once(api, "close");
  }
});
