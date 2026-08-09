import assert from "node:assert/strict";
import { test } from "node:test";

import {
  analysisHref,
  analysisIdFromLocation,
  guidanceRoute,
  resolveGuidanceTarget,
  validateGuidancePrefill,
} from "./navigation";

test("analysis navigation uses the static shell query route", () => {
  assert.equal(analysisHref(42), "/analysis?id=42");
  assert.throws(() => analysisHref(0), /invalid/i);
});

test("analysis route parser accepts the static shell and legacy dynamic route", () => {
  assert.equal(analysisIdFromLocation("/analysis", "?id=42"), 42);
  assert.equal(analysisIdFromLocation("/analysis/", "?id=42"), 42);
  assert.equal(analysisIdFromLocation("/analysis/43", ""), 43);
  assert.equal(analysisIdFromLocation("/analysis", "?id=0"), null);
  assert.equal(analysisIdFromLocation("/history", "?id=42"), null);
});

test("guidance navigation resolves only registered semantic targets", () => {
  assert.equal(guidanceRoute("settings.provider_auth"), "/settings");
  assert.equal(guidanceRoute("training.current"), "/");
  assert.equal(resolveGuidanceTarget("desktop.capture_control")?.sectionId, "capture");
  assert.equal(guidanceRoute("javascript:alert(1)"), null);
  assert.equal(resolveGuidanceTarget("#capture"), null);
});

test("guidance safe-prefill accepts bounded opaque refs only", () => {
  assert.deepEqual(validateGuidancePrefill("history.runs", { run_ref: "run:42" }), { run_ref: "run:42" });
  assert.deepEqual(validateGuidancePrefill("settings.provider_auth", {}), {});
  assert.equal(validateGuidancePrefill("history.runs", { run_ref: "C:\\secret.txt" }), null);
  assert.equal(validateGuidancePrefill("history.runs", { url: "https://example.test" }), null);
});
