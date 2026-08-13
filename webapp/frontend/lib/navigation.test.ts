import assert from "node:assert/strict";
import { test } from "node:test";

import {
  analysisHref,
  analysisIdFromLocation,
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
