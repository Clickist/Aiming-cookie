import assert from "node:assert/strict";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import { createWriteTool } from "../src/fs-tools.ts";

// The write tool resolves relative paths against the app-data cwd; these tests
// never touch the real data root.
const dataRoot = mkdtempSync(join(tmpdir(), "coach-fs-tools-"));

test("write refuses Coach-managed product state files and names the command", async () => {
  const write = createWriteTool(dataRoot);
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
  const write = createWriteTool(dataRoot);
  const result = await write.execute("note", { path: "conversations/note.tmp.json", content: "{}" });
  assert.match(result.content[0]?.text ?? "", /Successfully wrote/);
});
