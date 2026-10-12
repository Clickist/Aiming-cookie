import assert from "node:assert/strict";
import test from "node:test";

const { classifyCoachFailureCode, isRetryableFailureCode } = await import("../src/turn.ts");

// 1008 报障包野外原文：pi storage 层把 node EBUSY 原文透传进 SessionError。
const EBUSY_TURN_ERROR = new Error(
  "Failed to append session entry 8417efff: EBUSY: resource busy or locked, open 'D:\\集锦\\kvk\\conversations\\--coach--\\2026-09-28T21-12-54-084Z_2.jsonl'",
);

test("classifyCoachFailureCode maps local session-file lock to local_storage_busy", () => {
  assert.equal(classifyCoachFailureCode(EBUSY_TURN_ERROR), "local_storage_busy");
  assert.equal(classifyCoachFailureCode(new Error("resource busy or locked, open 'x'")), "local_storage_busy");
  // cause.code 判据：文本不带 EBUSY 但 cause 指明时也要命中。
  const withCause = new Error("open failed") as Error & { cause?: unknown };
  withCause.cause = { code: "EBUSY" };
  // classify 读 message 文本；cause-only 形态由 isStorageBusyFileError 负责
  // （wrapper 层），分类层以文本为主——不命中是预期，锁住这个边界。
  assert.equal(classifyCoachFailureCode(withCause), "turn_failed");
});

test("classifyCoachFailureCode keeps existing families intact (order guard)", () => {
  assert.equal(classifyCoachFailureCode(new Error("预扣费额度失败, 用户剩余额度: ¥0.01")), "quota_exhausted");
  assert.equal(classifyCoachFailureCode(new Error("Authentication Fails: invalid api key")), "provider_auth_invalid");
  assert.equal(classifyCoachFailureCode(new Error("HTTP 429 too many requests")), "service_overloaded");
  assert.equal(classifyCoachFailureCode(new Error("fetch failed")), "network_transient");
  assert.equal(classifyCoachFailureCode(new Error("something exploded")), "turn_failed");
});

test("isRetryableFailureCode: transient codes retryable, deterministic ones not", () => {
  assert.equal(isRetryableFailureCode("local_storage_busy"), true);
  assert.equal(isRetryableFailureCode("service_overloaded"), true);
  assert.equal(isRetryableFailureCode("network_transient"), true);
  assert.equal(isRetryableFailureCode("quota_exhausted"), false);
  assert.equal(isRetryableFailureCode("provider_auth_invalid"), false);
  assert.equal(isRetryableFailureCode("turn_failed"), false);
  assert.equal(isRetryableFailureCode("stopped"), false);
});

test("classifyCoachFailureCode maps gateway provisioning_in_flight to trial_provisioning", () => {
  // 野外报文形态：网关 error 体折进 provider message（含 type 字符串与中文文案）
  assert.equal(classifyCoachFailureCode(new Error('403 {"error":{"message":"免费试用额度正在准备中，请稍候片刻","type":"provisioning_in_flight"}}')), "trial_provisioning");
  assert.equal(isRetryableFailureCode("trial_provisioning"), true);
});
test("order guard: provisioning_in_flight 不被 quota/鉴权族吞掉", () => {
  assert.equal(classifyCoachFailureCode(new Error("provisioning_in_flight 用户额度不足")), "trial_provisioning");
});

test("classifyCoachFailureCode maps session JSONL corruption to local_storage_corrupt", () => {
  // 鲁棒性审计 P0-1：pi invalid_entry 原文（撕裂行自愈失败后的残余形态）。
  const torn = new Error(
    "Invalid JSONL session file C:\\d\\conversations\\--coach--\\a_2.jsonl: line 4 is not valid JSON",
  );
  assert.equal(classifyCoachFailureCode(torn), "local_storage_corrupt");
  assert.equal(isRetryableFailureCode("local_storage_corrupt"), true);
  // 砖化红线：该分类绝不能落不可重试的 turn_failed。
  assert.notEqual(classifyCoachFailureCode(torn), "turn_failed");
});
