import assert from "node:assert/strict";
import { existsSync, mkdirSync, renameSync, rmSync, writeFileSync } from "node:fs";
import { dirname } from "node:path";
import test from "node:test";

import { CATALOG_PATH, executeNativeEloshapes } from "../src/eloshapes-native.ts";

test("a missing catalog does not poison the cache once the snapshot appears", () => {
  // Deep-test Bug 9: the first query with the snapshot absent cached an empty
  // catalog, so every later query stayed catalog_unavailable until restart.
  const backup = `${CATALOG_PATH}.testbak`;
  const hadSnapshot = existsSync(CATALOG_PATH);
  if (hadSnapshot) renameSync(CATALOG_PATH, backup);
  try {
    const missing = executeNativeEloshapes("eloshapes.query", { brand_search: "acme" });
    assert.equal(missing.status, "failed");
    assert.equal(missing.warning_or_error?.code, "catalog_unavailable");

    // The snapshot appears after startup (restored artifacts).
    mkdirSync(dirname(CATALOG_PATH), { recursive: true });
    writeFileSync(CATALOG_PATH, JSON.stringify([{
      general__id: 7,
      general__category: "mouse",
      general__brand_names: ["Acme"],
      general__model: "Test Mouse",
      mouse__weight: 55,
    }]), "utf8");
    const recovered = executeNativeEloshapes("eloshapes.query", { brand_search: "acme" });
    assert.equal(recovered.status, "succeeded", JSON.stringify(recovered.warning_or_error));
    assert.equal((recovered.result as { total_matches: number }).total_matches, 1);
  } finally {
    rmSync(CATALOG_PATH, { force: true });
    if (hadSnapshot) renameSync(backup, CATALOG_PATH);
  }
});
