import assert from "node:assert/strict";
import test from "node:test";

import { shouldShowKovaakInstallGuide } from "./kovaak-install-guide";
import type { KovaaKLocalDirectoryStatusV1, KovaaKLocalDirectoriesV1 } from "./types";

const directory = (source: KovaaKLocalDirectoryStatusV1["source"]): KovaaKLocalDirectoryStatusV1 => ({
  path: source === "unavailable" ? null : "C:\\ProgramData\\KovaaKs",
  source,
  matching_file_count: 0,
  matching_files: "no_matching_files",
});

const response = (
  stats: KovaaKLocalDirectoryStatusV1,
  performance: KovaaKLocalDirectoryStatusV1 = directory("automatic"),
): KovaaKLocalDirectoriesV1 => ({
  schema_version: "kovaak_local_directories.v1",
  stats,
  performance,
  activation: "not_requested",
});

test("guide shows only when the stats directory is unavailable", () => {
  assert.equal(shouldShowKovaakInstallGuide(response(directory("unavailable"))), true);
});

test("any discovered stats source (environment/confirmed/automatic) hides the guide", () => {
  for (const source of ["environment", "confirmed", "automatic"] as const) {
    assert.equal(shouldShowKovaakInstallGuide(response(directory(source))), false, source);
  }
});

test("performance directory alone being unavailable does not trigger the guide", () => {
  assert.equal(shouldShowKovaakInstallGuide(response(directory("automatic"), directory("unavailable"))), false);
});

test("missing response fails open to hidden", () => {
  assert.equal(shouldShowKovaakInstallGuide(null), false);
  assert.equal(shouldShowKovaakInstallGuide(undefined), false);
});
