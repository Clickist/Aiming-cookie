import assert from "node:assert/strict";
import test from "node:test";

import { loadPiAgent, readPinnedAgentPackageVersion } from "../src/pi-source.ts";

test("loads real Pi Agent from third_party and matches pinned package version", async () => {
  const version = readPinnedAgentPackageVersion();
  assert.equal(version, "0.80.6");

  const mod = await loadPiAgent();
  assert.equal(typeof mod.Agent, "function");
});