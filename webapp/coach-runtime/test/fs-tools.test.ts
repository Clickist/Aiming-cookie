import assert from "node:assert/strict";
import { mkdtempSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import { createBashTool, createEditTool, createGrepTool, createLsTool, createReadTool, createWriteTool } from "../src/fs-tools.ts";

// The write tool resolves relative paths against the app-data cwd; these tests
// never touch the real data root.
const dataRoot = mkdtempSync(join(tmpdir(), "coach-fs-tools-"));

test("write refuses Coach-managed product state files and names the command", async () => {
  const write = await createWriteTool(dataRoot);
  const cases: Array<{ path: string; commandHint: RegExp }> = [
    { path: "training/plan.json", commandHint: /training_plan/ },
    { path: "training/history.jsonl", commandHint: /training_plan/ },
    { path: "teaching/session.json", commandHint: /teaching_session\.update/ },
    { path: "config/scenario-overrides.json", commandHint: /scenario_memory\.set/ },
    { path: "config/calibration.json", commandHint: /calibration\.save/ },
    { path: "config/peripheral.json", commandHint: /peripheral_profile\.update/ },
    { path: "config/kovaak-connection.json", commandHint: /kovaak\.connection\.disconnect/ },
    // Platform separator and absolute-path forms resolve to the same file.
    { path: join("training", "plan.json"), commandHint: /training_plan/ },
    { path: join(dataRoot, "training", "plan.json"), commandHint: /training_plan/ },
  ];
  for (const { path, commandHint } of cases) {
    await assert.rejects(
      write.execute("guard", { path, content: "{}" }),
      (error: unknown) => {
        assert.ok(error instanceof Error);
        assert.match(error.message, /product state/);
        assert.match(error.message, commandHint);
        return true;
      },
      path,
    );
  }
});

test("write still covers ordinary app-data files", async () => {
  const write = await createWriteTool(dataRoot);
  const result = await write.execute("note", { path: "conversations/note.tmp.json", content: "{}" });
  assert.match(result.content[0]?.text ?? "", /Successfully wrote/);
});

test("read truncates oversized files with pi's paging hint instead of flooding the context", async () => {
  const read = await createReadTool(dataRoot);
  const { writeFile: writeFileAsync, mkdir } = await import("node:fs/promises");
  await mkdir(join(dataRoot, "analyses", "9"), { recursive: true });
  // 3000 lines > pi's 2000-line default limit.
  const big = Array.from({ length: 3000 }, (_, i) => `line ${i}: data`).join("\n");
  await writeFileAsync(join(dataRoot, "analyses", "9", "events.json"), big, "utf8");

  const result = await read.execute("big", { path: "analyses/9/events.json" });
  const text = result.content[0]?.text ?? "";
  // pi 原版在 content 里直接给出截断状态与续读指令（LLM 可见）。
  assert.match(text, /\[Showing lines 1-\d+ of 3000\. Use offset=\d+ to continue\.\]/, "truncation notice present");
  assert.match(text, /offset/, "paging hint present");
  assert.ok(text.length < 120_000, `truncated read should stay bounded, got ${text.length}`);

  // 分页续读：offset/limit 精确取片。
  const page = await read.execute("page", { path: "analyses/9/events.json", offset: 2501, limit: 2 });
  const pageText = page.content[0]?.text ?? "";
  assert.match(pageText, /line 2500: data/);
  assert.match(pageText, /line 2501: data/);
  assert.ok(!pageText.includes("line 2502"), "limit honored");
  assert.match(pageText, /more lines in file\. Use offset/, "remaining-lines notice present");

  // 小文件原样通过。
  await writeFileAsync(join(dataRoot, "analyses", "9", "overview.json"), '{"ok":true}', "utf8");
  const small = await read.execute("small", { path: "analyses/9/overview.json" });
  assert.equal(small.content[0]?.text, '{"ok":true}');
});

test("analysis reads stay silent without a notification scope", async () => {
  // notifyAnalysisRead 走 AsyncLocalStorage 作用域，无监听时应静默不抛。
  const read = await createReadTool(dataRoot);
  const { writeFile: writeFileAsync, mkdir } = await import("node:fs/promises");
  await mkdir(join(dataRoot, "analyses", "8"), { recursive: true });
  await writeFileAsync(join(dataRoot, "analyses", "8", "overview.json"), '{"ok":true}', "utf8");
  const ok = await read.execute("notify", { path: "analyses/8/overview.json" });
  assert.equal(ok.content[0]?.text, '{"ok":true}');
});

// ── pi 原版工具接入冒烟（edit/grep/bash/ls limit）───────────────────────

test("edit performs a precise replacement", async () => {
  const edit = await createEditTool(dataRoot);
  const target = join(dataRoot, "plan-draft.txt");
  const { writeFileSync } = await import("node:fs");
  writeFileSync(target, "alpha\nbeta\ngamma", "utf8");
  const result = await edit.execute("e1", {
    path: target,
    edits: [{ oldText: "beta", newText: "BETA-EDITED" }],
  });
  assert.equal(result.isError ?? false, false);
  assert.match(readFileSync(target, "utf8"), /BETA-EDITED/);
});

test("grep searches file contents", async () => {
  const grep = await createGrepTool(dataRoot);
  const result = await grep.execute("g1", { pattern: "needle", path: dataRoot });
  const text = result.content[0]?.text ?? "";
  assert.ok(text.length === 0 || /needle|No matches/i.test(text), "grep returns matches or a no-match notice");
});

test("ls honors the entry limit", async () => {
  const ls = await createLsTool(dataRoot);
  const result = await ls.execute("l1", { path: dataRoot, limit: 1 });
  const text = result.content[0]?.text ?? "";
  assert.ok(text.split("\n").filter(Boolean).length <= 2, "limit caps entries (plus a truncation notice)");
});

test("bash executes a shell command inside the app-data cwd", async () => {
  const bash = await createBashTool(dataRoot);
  const result = await bash.execute("b1", { command: "echo pi-bash-ok", timeout: 15000 });
  assert.equal(result.isError ?? false, false);
  assert.match(result.content[0]?.text ?? "", /pi-bash-ok/);
});
