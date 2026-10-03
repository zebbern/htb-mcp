/**
 * End-to-end smoke test: spawn the built MCP server over stdio and exercise
 * the offline tool path (htb_vpn_servers --static needs no API key), then run
 * htb_doctor and report its verdict. A failing doctor (missing key, etc.) is
 * reported but does not fail the smoke — only tools/list breakage does.
 */
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StdioClientTransport } from "@modelcontextprotocol/sdk/client/stdio.js";
import path from "node:path";
import { fileURLToPath } from "node:url";

const PKG_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

const EXPECTED_TOOLS = [
  "htb_doctor",
  "htb_machine_list",
  "htb_machine_search",
  "htb_machine_profile",
  "htb_machine_active",
  "htb_machine_start",
  "htb_machine_stop",
  "htb_machine_reset",
  "htb_machine_extend",
  "htb_machine_submit",
  "htb_challenge_list",
  "htb_challenge_search",
  "htb_challenge_info",
  "htb_challenge_submit",
  "htb_vpn_servers",
  "htb_vpn_status",
  "htb_vpn_switch",
  "htb_vpn_download",
  "htb_user_info",
  "htb_user_progress",
  "htb_user_activity",
  "htb_raw",
];

async function main() {
  const transport = new StdioClientTransport({
    command: process.execPath,
    args: [path.join(PKG_ROOT, "dist", "server.js")],
    stderr: "pipe",
    // Pass the full environment: the SDK's default filtered env drops vars
    // like ELECTRON_RUN_AS_NODE that some Node runtimes need.
    env: { ...process.env } as Record<string, string>,
  });
  transport.stderr?.on("data", (d: Buffer) => {
    const s = d.toString();
    if (!s.includes("running on stdio")) console.error("[server stderr]", s.slice(0, 500));
  });
  const client = new Client({ name: "smoke-test", version: "0.0.1" });
  await client.connect(transport);

  const { tools } = await client.listTools();
  const names = tools.map((t) => t.name).sort();
  console.log(`Tools (${names.length}):`, names.join(", "));
  for (const expected of EXPECTED_TOOLS) {
    if (!names.includes(expected)) throw new Error(`Missing tool: ${expected}`);
  }
  console.log("tools/list OK");

  const vpn = await client.callTool({ name: "htb_vpn_servers", arguments: { static: true } });
  if ((vpn as any).isError) {
    throw new Error(`htb_vpn_servers {static:true} failed: ${(vpn.content as any[])[0]?.text}`);
  }
  const vpnText = (vpn.content as any[])[0]?.text ?? "";
  let vpnRows: unknown[] = [];
  try {
    const parsed = JSON.parse(vpnText);
    vpnRows = Array.isArray(parsed) ? parsed : (parsed.servers ?? parsed.rows ?? []);
  } catch {
    throw new Error(`htb_vpn_servers {static:true} did not return JSON: ${vpnText.slice(0, 200)}`);
  }
  if (vpnRows.length === 0) throw new Error("htb_vpn_servers {static:true} returned zero server rows");
  console.log(`vpn_servers (static) OK — ${vpnRows.length} server rows`);

  const doctor = await client.callTool({ name: "htb_doctor", arguments: {} });
  const doctorText = (doctor.content as any[])[0]?.text ?? "";
  if ((doctor as any).isError) {
    console.warn(`doctor reported an error (not failing smoke): ${doctorText.slice(0, 300)}`);
  } else {
    console.log("doctor:", doctorText.slice(0, 500));
  }

  await client.close();
  console.log("Smoke test passed");
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
