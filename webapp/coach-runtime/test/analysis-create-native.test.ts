import assert from "node:assert/strict";
import { mkdtempSync, mkdirSync, readFileSync, writeFileSync, existsSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

// Set DATA_ROOT before importing modules that call getDataRoot() (which caches).
const dataRoot = mkdtempSync(join(tmpdir(), "coach-write-"));
process.env.DATA_ROOT = dataRoot;

import { executeNativeWrite, executeNativeAnalysisDelete, executeNativeAnalysisRetry } from "../src/product-commands-write.ts";

function ensureDirs(): void {
  for (const sub of ["config", "training", "analyses"]) {
    mkdirSync(join(dataRoot, sub), { recursive: true });
  }
}

test("training_plan.generate_draft writes the plans-doc format plan.json", () => {
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
  const doc = JSON.parse(readFileSync(planPath, "utf-8"));
  // Python read side (training_plan_store) reads {plans:{...}}; the write side
  // must not regress to the flat object that left /current-training empty.
  assert.deepEqual(Object.keys(doc).sort(), ["executions", "items", "plans", "retests", "transitions"]);
  const planRef = (result.result as Record<string, unknown>).plan_ref as string;
  const plan = doc.plans[planRef];
  assert.equal(plan.status, "draft");
  assert.equal(plan.owner_id, "owner-a");
  assert.equal(plan.current_version, 1);
  assert.equal(plan.versions["1"].plan_payload.title, "Test Plan");
  assert.equal(doc.transitions[0].event, "generated");
});

test("training_plan.generate_draft defaults the owner to desktop-local", () => {
  ensureDirs();
  rmSync(join(dataRoot, "training", "plan.json"), { force: true });
  const result = executeNativeWrite(
    "training_plan.generate_draft",
    { plan_payload: { title: "No Owner" } },
    "",
  );
  assert.equal(result.status, "succeeded");
  const planRef = (result.result as Record<string, unknown>).plan_ref as string;
  const doc = JSON.parse(readFileSync(join(dataRoot, "training", "plan.json"), "utf-8"));
  assert.equal(doc.plans[planRef].owner_id, "desktop-local");
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
  const doc = JSON.parse(readFileSync(join(dataRoot, "training", "plan.json"), "utf-8"));
  assert.equal(doc.plans[planRef].status, "saved");
  assert.equal(doc.transitions.at(-1).event, "saved");
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
  const doc = JSON.parse(readFileSync(join(dataRoot, "training", "plan.json"), "utf-8"));
  assert.equal(doc.plans[planRef].status, "active");
  assert.equal(doc.transitions.at(-1).event, "activated");
});

test("training_plan.adjust appends a version and bumps current_version", () => {
  ensureDirs();
  rmSync(join(dataRoot, "training", "plan.json"), { force: true });
  const draftResult = executeNativeWrite(
    "training_plan.generate_draft",
    { plan_payload: { title: "v1" } },
    "owner-a",
  );
  const planRef = (draftResult.result as Record<string, unknown>).plan_ref as string;
  executeNativeWrite("training_plan.save", { plan_ref: planRef }, "owner-a");
  const adjusted = executeNativeWrite(
    "training_plan.adjust",
    { plan_ref: planRef, plan_payload: { title: "v2" }, adjustment_reason: "test" },
    "owner-a",
  );
  assert.equal(adjusted.status, "succeeded");
  const doc = JSON.parse(readFileSync(join(dataRoot, "training", "plan.json"), "utf-8"));
  assert.equal(doc.plans[planRef].current_version, 2);
  assert.equal(doc.plans[planRef].versions["2"].plan_payload.title, "v2");
  assert.equal(doc.plans[planRef].versions["1"].plan_payload.title, "v1");
});

test("training_plan.item.add stores the item in the doc items map", () => {
  ensureDirs();
  rmSync(join(dataRoot, "training", "plan.json"), { force: true });
  const draftResult = executeNativeWrite(
    "training_plan.generate_draft",
    { plan_payload: { title: "Plan items" } },
    "owner-a",
  );
  const planRef = (draftResult.result as Record<string, unknown>).plan_ref as string;
  const itemResult = executeNativeWrite(
    "training_plan.item.add",
    { plan_ref: planRef, item_payload: { title: "Practice" } },
    "owner-a",
  );
  assert.equal(itemResult.status, "succeeded");
  const itemRef = (itemResult.result as Record<string, unknown>).item_ref as string;
  const doc = JSON.parse(readFileSync(join(dataRoot, "training", "plan.json"), "utf-8"));
  assert.equal(doc.items[itemRef].plan_id, planRef);
  assert.equal(doc.items[itemRef].status, "planned");
  assert.equal(doc.items[itemRef].item_payload.title, "Practice");
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

test("analysis.retry re-enqueues the failed session through the Python backend", async () => {
  ensureDirs();
  const runtimeConfig = join(dataRoot, "desktop-runtime.json");
  writeFileSync(
    runtimeConfig,
    JSON.stringify({ python_base_url: "http://127.0.0.1:9999", python_token: "test-token" }),
  );

  const originalFetch = globalThis.fetch;
  const requests: { url: string; method?: string; headers?: HeadersInit }[] = [];
  globalThis.fetch = (async (url, init) => {
    requests.push({ url: String(url), method: init?.method, headers: init?.headers });
    return new Response(JSON.stringify({ id: 42, status: "queued" }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  }) as typeof fetch;
  try {
    const result = await executeNativeAnalysisRetry(
      "analysis.retry", { analysis_ref: "analysis:42" }, "owner-a", "idem-key-1",
    );
    assert.equal(result.status, "succeeded");
    assert.equal(requests[0]?.url, "http://127.0.0.1:9999/api/sessions/42/retry");
    assert.equal(requests[0]?.method, "POST");
    const headers = requests[0]?.headers as Record<string, string>;
    assert.equal(headers["Idempotency-Key"], "idem-key-1");
    const payload = result.result as Record<string, unknown>;
    assert.equal(payload.analysis_ref, "analysis:42");
    assert.equal(payload.retried, true);
    assert.equal(payload.session_id, 42);
    assert.equal(payload.session_status, "queued");
  } finally {
    globalThis.fetch = originalFetch;
    rmSync(runtimeConfig, { force: true });
  }
});

test("analysis.retry maps a missing session to not_found and bad refs to internal_error", async () => {
  ensureDirs();
  const runtimeConfig = join(dataRoot, "desktop-runtime.json");
  writeFileSync(
    runtimeConfig,
    JSON.stringify({ python_base_url: "http://127.0.0.1:9999", python_token: "test-token" }),
  );

  const originalFetch = globalThis.fetch;
  globalThis.fetch = (async () => new Response("session 不存在", { status: 404 })) as typeof fetch;
  try {
    const missing = await executeNativeAnalysisRetry(
      "analysis.retry", { analysis_ref: "analysis:42" }, "owner-a", "idem-key-2",
    );
    assert.equal(missing.status, "failed");
    assert.equal(missing.warning_or_error?.code, "not_found");

    const invalid = await executeNativeAnalysisRetry(
      "analysis.retry", { analysis_ref: "not-a-ref" }, "owner-a", "idem-key-3",
    );
    assert.equal(invalid.status, "failed");
    assert.equal(invalid.warning_or_error?.code, "internal_error");
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
