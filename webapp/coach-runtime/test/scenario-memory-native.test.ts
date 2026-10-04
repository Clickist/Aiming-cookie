import assert from "node:assert/strict";
import { mkdtempSync, mkdirSync, readFileSync, existsSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

// Set DATA_ROOT before importing modules that call getDataRoot() (which caches).
const dataRoot = mkdtempSync(join(tmpdir(), "coach-scenario-memory-"));
process.env.DATA_ROOT = dataRoot;

import { executeNativeWrite, isNativeWriteCommand } from "../src/product-commands-write.ts";

function resetOverrides(): void {
  mkdirSync(join(dataRoot, "config"), { recursive: true });
  rmSync(join(dataRoot, "config", "scenario-overrides.json"), { force: true });
}

function readOverrides(): Record<string, any> {
  return JSON.parse(
    readFileSync(join(dataRoot, "config", "scenario-overrides.json"), "utf-8"),
  );
}

function setCommand(params: Record<string, unknown>, owner = "owner-a") {
  return executeNativeWrite("scenario_memory.set", params, owner);
}

const HASH = "0123456789abcdef".repeat(2);

test("scenario_memory.set is registered and creates config/scenario-overrides.json", () => {
  resetOverrides();
  assert.ok(isNativeWriteCommand("scenario_memory.set"));

  const result = setCommand({
    scenario_hash: HASH,
    aim_family: "static_clicking",
    note: "1w4ts = one wall four targets small",
  });

  assert.equal(result.status, "succeeded");
  assert.equal(result.result_ref, `scenario_override:${HASH}`);
  const file = readOverrides();
  assert.equal(file.schema_version, "scenario_overrides.v1");
  assert.ok(file.overrides[HASH]);
  assert.equal(file.overrides[HASH].aim_family, "static_clicking");
  assert.equal(file.overrides[HASH].confirmed_by, "user");
  assert.equal(file.overrides[HASH].note, "1w4ts = one wall four targets small");
  assert.ok(typeof file.overrides[HASH].updated_at === "string");
});

test("scenario_memory.set upserts by scenario_hash", () => {
  resetOverrides();
  setCommand({ scenario_hash: HASH, aim_family: "static_clicking" });

  const updated = setCommand({ scenario_hash: HASH, aim_family: "target_switching" });
  assert.equal(updated.status, "succeeded");

  const file = readOverrides();
  assert.equal(Object.keys(file.overrides).length, 1);
  assert.equal(file.overrides[HASH].aim_family, "target_switching");
  // 不带 note 的覆盖写入将 note 清空，不保留上一条。
  assert.equal(file.overrides[HASH].note, null);
});

test("scenario_memory.set rejects invalid hashes, families and notes", () => {
  resetOverrides();

  const badHash = setCommand({ scenario_hash: "not-a-hash", aim_family: "static_clicking" });
  assert.equal(badHash.status, "failed");
  assert.equal(badHash.warning_or_error?.code, "invalid_scenario_memory");

  const uppercaseHash = setCommand({ scenario_hash: HASH.toUpperCase(), aim_family: "static_clicking" });
  assert.equal(uppercaseHash.status, "failed");
  assert.equal(uppercaseHash.warning_or_error?.code, "invalid_scenario_memory");

  const badFamily = setCommand({ scenario_hash: HASH, aim_family: "movement_aiming" });
  assert.equal(badFamily.status, "failed");
  assert.equal(badFamily.warning_or_error?.code, "invalid_scenario_memory");

  const badNote = setCommand({ scenario_hash: HASH, aim_family: "static_clicking", note: "x".repeat(201) });
  assert.equal(badNote.status, "failed");
  assert.equal(badNote.warning_or_error?.code, "invalid_scenario_memory");

  const badConfirmedBy = setCommand({
    scenario_hash: HASH, aim_family: "static_clicking", confirmed_by: "llm",
  });
  assert.equal(badConfirmedBy.status, "failed");
  assert.equal(badConfirmedBy.warning_or_error?.code, "invalid_scenario_memory");

  // 拒绝的写入不产生文件。
  assert.ok(!existsSync(join(dataRoot, "config", "scenario-overrides.json")));
});

// ── [2026-10-04] Coach 判断制：confirmed_by="coach" 与用户确认优先 ──────

test("scenario_memory.set records coach judgement and it never downgrades a user confirmation", () => {
  resetOverrides();

  // 用户确认在前：Coach 判定不能覆盖（读侧层序 user > coach 在写入侧兑现）。
  const user = setCommand({
    scenario_hash: HASH,
    aim_family: "static_clicking",
    confirmed_by: "user",
  });
  assert.equal(user.status, "succeeded");
  const downgrade = setCommand({
    scenario_hash: HASH,
    aim_family: "continuous_tracking",
    confirmed_by: "coach",
  });
  assert.equal(downgrade.status, "failed");
  assert.equal(downgrade.warning_or_error?.code, "invalid_scenario_memory");
  assert.equal(readOverrides().overrides[HASH].aim_family, "static_clicking");

  // 新图：Coach 自身判定 confirmed_by=coach 落盘。
  const coachHash = "fedcba9876543210".repeat(2);
  const coach = setCommand({
    scenario_hash: coachHash,
    aim_family: "continuous_tracking",
    confirmed_by: "coach",
    note: "hold_frac 0.98 且官方杀率 1.41/s",
  });
  assert.equal(coach.status, "succeeded");
  const afterCoach = readOverrides();
  assert.equal(afterCoach.overrides[coachHash].confirmed_by, "coach");
  assert.equal(afterCoach.overrides[coachHash].aim_family, "continuous_tracking");

  // coach 记忆可被后续 coach 判定更新（同图改判）。
  const revise = setCommand({
    scenario_hash: coachHash, aim_family: "target_switching", confirmed_by: "coach",
  });
  assert.equal(revise.status, "succeeded");
  assert.equal(readOverrides().overrides[coachHash].aim_family, "target_switching");

  // 用户亲自确认可以覆盖 Coach 判定（并升级为 user 记忆）；升级后 Coach
  // 判定同样不能覆盖用户确认。
  const upgrade = setCommand({
    scenario_hash: coachHash,
    aim_family: "static_clicking",
    confirmed_by: "user",
  });
  assert.equal(upgrade.status, "succeeded");
  const afterUser = readOverrides();
  assert.equal(afterUser.overrides[coachHash].confirmed_by, "user");
  assert.equal(afterUser.overrides[coachHash].aim_family, "static_clicking");
  const downgradeAgain = setCommand({
    scenario_hash: coachHash,
    aim_family: "continuous_tracking",
    confirmed_by: "coach",
  });
  assert.equal(downgradeAgain.status, "failed");
  assert.equal(readOverrides().overrides[coachHash].aim_family, "static_clicking");
});
