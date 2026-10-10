import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { copyFileSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

test("skill drift check accepts Windows line endings but rejects changed content", () => {
  const scratch = path.join(root, "tmp");
  mkdirSync(scratch, { recursive: true });
  const fixture = mkdtempSync(path.join(scratch, "skill-sync-"));
  assert.ok(path.resolve(fixture).startsWith(path.resolve(scratch) + path.sep));
  try {
    const script = path.join(fixture, "scripts", "sync-skills.mjs");
    const canonical = path.join(fixture, "skills", "htb", "SKILL.md");
    const copy = path.join(fixture, "kimi-plugin", "skills", "htb", "SKILL.md");
    for (const file of [script, canonical, copy]) mkdirSync(path.dirname(file), { recursive: true });
    copyFileSync(path.join(root, "scripts", "sync-skills.mjs"), script);
    writeFileSync(canonical, "# Skill\r\nContent\r\n");
    writeFileSync(copy, "<!-- GENERATED COPY of skills/htb/SKILL.md - do not edit directly; run: npm run sync:skills -->\r\n\r\n# Skill\r\nContent\r\n");
    const check = () => spawnSync(process.execPath, [script, "--check"], { encoding: "utf8" });
    assert.equal(check().status, 0);
    writeFileSync(copy, readFileSync(copy, "utf8").replace("Content", "Changed"));
    assert.equal(check().status, 1);
  } finally {
    rmSync(fixture, { recursive: true, force: true });
  }
});
