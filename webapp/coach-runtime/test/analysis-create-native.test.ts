import assert from "node:assert/strict";
import { mkdtempSync, mkdirSync, readFileSync, writeFileSync, existsSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

// Set DATA_ROOT before importing modules that call getDataRoot() (which caches).
const dataRoot = mkdtempSync(join(tmpdir(), "coach-write-"));
process.env.DATA_ROOT = dataRoot;

import { executeNativeWrite, executeNativeAnalysisDelete } from "../src/product-commands-write.ts";

function ensureDirs(): void {
  for (const sub of ["config", "training", "analyses"]) {
    mkdirSync(join(dataRoot, sub), { recursive: true });
  }
}

test("training_plan.generate_draft writes plan.json", () => {
  ensureDirs();
  rmSync(join(dataRoot, "training", "plan.json"), { force: true });
  const result = executeNativeWrite(
    "training_plan.generate_draft",
    { plan_payload: { title: "Test Plan" }, evidence_refs: [], verification_targets: [] },
    "owner-a",
  );
  assert.equal(result.status, "succeeded");
  const planPath = join(dataRoot, "training", "plan.json");
  assert.ok(existsSync(planPath));
  const plan = JSON.parse(readFileSync(planPath, "utf-8"));
  assert.equal(plan.status, "draft");
  assert.equal(plan.plan_payload.title, "Test Plan");
});

test("training_plan.save transitions draft to saved", () => {
  ensureDirs();
  rmSync(join(dataRoot, "training", "plan.json"), { force: true });
  const draftResult = executeNativeWrite(
    "training_plan.generate_draft",
    { plan_payload: { title: "Plan 2" } },
    "owner-a",
  );
  const planRef = (draftResult.result as Record<string, unknown>).plan_ref as string;

  const result = executeNativeWrite("training_plan.save", { plan_ref: planRef }, "owner-a");
  assert.equal(result.status, "succeeded");
  const plan = JSON.parse(readFileSync(join(dataRoot, "training", "plan.json"), "utf-8"));
  assert.equal(plan.status, "saved");
});

test("training_plan.activate transitions saved to active", () => {
  ensureDirs();
  rmSync(join(dataRoot, "training", "plan.json"), { force: true });
  const draftResult = executeNativeWrite(
    "training_plan.generate_draft",
    { plan_payload: { title: "Plan 3" } },
    "owner-a",
  );
  const planRef = (draftResult.result as Record<string, unknown>).plan_ref as string;
  executeNativeWrite("training_plan.save", { plan_ref: planRef }, "owner-a");

  const result = executeNativeWrite("training_plan.activate", { plan_ref: planRef }, "owner-a");
  assert.equal(result.status, "succeeded");
  const plan = JSON.parse(readFileSync(join(dataRoot, "training", "plan.json"), "utf-8"));
  assert.equal(plan.status, "active");
});

test("training_plan.execution.record appends to history.jsonl", () => {
  ensureDirs();
  rmSync(join(dataRoot, "training", "history.jsonl"), { force: true });
  const result = executeNativeWrite(
    "training_plan.execution.record",
    {
      item_ref: "plan-item:test",
      scenario_ref: "scenario:test",
      run_refs: [],
      completion_status: "completed",
    },
    "owner-a",
  );
  assert.equal(result.status, "succeeded");
  const historyPath = join(dataRoot, "training", "history.jsonl");
  assert.ok(existsSync(historyPath));
  const lines = readFileSync(historyPath, "utf-8").trim().split("\n");
  assert.equal(lines.length, 1);
  const record = JSON.parse(lines[0]);
  assert.equal(record.completion_status, "completed");
});

test("analysis.delete removes the Python session and the analyses directory", async () => {
  ensureDirs();
  const analysisDir = join(dataRoot, "analyses", "42");
  mkdirSync(analysisDir, { recursive: true });
  writeFileSync(join(analysisDir, "overview.json"), JSON.stringify({ status: "done" }));
  const runtimeConfig = join(dataRoot, "desktop-runtime.json");
  writeFileSync(
    runtimeConfig,
    JSON.stringify({ python_base_url: "http://127.0.0.1:9999", python_token: "test-token" }),
  );

  const originalFetch = globalThis.fetch;
  const requests: { url: string; method?: string; headers?: HeadersInit }[] = [];
  globalThis.fetch = (async (url, init) => {
    requests.push({ url: String(url), method: init?.method, headers: init?.headers });
    return new Response(JSON.stringify({ deleted: true, id: 42 }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  }) as typeof fetch;
  try {
    const result = await executeNativeAnalysisDelete("analysis.delete", { analysis_ref: "analysis:42" }, "owner-a");
    assert.equal(result.status, "succeeded");
    assert.ok(!existsSync(analysisDir));
    assert.equal(requests[0]?.url, "http://127.0.0.1:9999/api/sessions/42");
  } finally {
    globalThis.fetch = originalFetch;
    rmSync(runtimeConfig, { force: true });
  }
});

test("calibration.save writes config/calibration.json", () => {
  ensureDirs();
  const result = executeNativeWrite(
    "calibration.save",
    { cm_per_360: 30.5, fov: 103 },
    "owner-a",
  );
  assert.equal(result.status, "succeeded");
  const cal = JSON.parse(readFileSync(join(dataRoot, "config", "calibration.json"), "utf-8"));
  assert.equal(cal.cm_per_360, 30.5);
  assert.equal(cal.fov, 103);
});
