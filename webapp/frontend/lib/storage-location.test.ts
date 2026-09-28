import assert from "node:assert/strict";
import { test } from "node:test";

import {
  STORAGE_LOCATION_UNKNOWN_ERROR,
  formatStorageBytes,
  migrationFailureKey,
  migrationPercent,
  storageLocationErrorMessage,
  type StorageMigrationStatusV1,
} from "./storage-location";
import { translate } from "./i18n/core";

// 壳（Rust）不持自然语言：它只回稳定码 storage_location.* 与可选数字（空间不足
// 的 need/have），文案与格式化都在这里。这些测试钉住「码 → 文案」的映射不漂移，
// 未知码不把英文诊断或裸码抛给用户。

function migration(overrides: Partial<StorageMigrationStatusV1> = {}): StorageMigrationStatusV1 {
  return {
    sourceRoot: "C:\\Users\\me\\AppData\\Roaming\\com.aimingcookie.desktop",
    targetRoot: "D:\\ACData",
    phase: "running",
    movedEntries: [],
    pendingEntries: [],
    totalBytes: 0,
    copiedBytes: 0,
    error: null,
    updatedAt: "2026-09-28T00:00:00Z",
    ...overrides,
  };
}

test("storage location error codes map to localized sentences, never to raw codes", () => {
  const fallback = translate("zh-CN", STORAGE_LOCATION_UNKNOWN_ERROR);
  const codes = [
    "storage_location.not_absolute",
    "storage_location.not_a_directory",
    "storage_location.create_failed",
    "storage_location.not_writable",
    "storage_location.nested",
    "storage_location.write_failed",
    "storage_location.migration_in_progress",
  ];
  for (const code of codes) {
    const message = storageLocationErrorMessage(code);
    assert.notEqual(message, code, `${code} must map to a sentence`);
    assert.ok(message.trim().length > 0);
    // 除兜底码本身外，任何已知码都不该落到兜底句（防漏映射/键名漂移）。
    assert.notEqual(message, fallback, `${code} has no dedicated sentence`);
  }
  assert.equal(storageLocationErrorMessage("storage_location.errorUnknown"), fallback);
});

test("insufficient space reports both required and available bytes in human units", () => {
  const message = storageLocationErrorMessage({
    code: "storage_location.insufficient_space",
    requiredBytes: 200 * 1024 * 1024,
    availableBytes: 40 * 1024 * 1024,
  });
  assert.match(message, /200\.0 MB/);
  assert.match(message, /40\.0 MB/);
  // 缺数字时回落通用句，而不是把 {required} 占位符原样上屏。
  const bare = storageLocationErrorMessage({ code: "storage_location.insufficient_space" });
  assert.doesNotMatch(bare, /\{required\}|\{available\}/);
  assert.doesNotMatch(bare, /_|storage_location/);
});

test("unknown or malformed invoke failures degrade to the generic sentence", () => {
  const fallback = translate("zh-CN", STORAGE_LOCATION_UNKNOWN_ERROR);
  assert.equal(storageLocationErrorMessage("some_unknown_code"), fallback);
  assert.equal(storageLocationErrorMessage(undefined), fallback);
  assert.equal(storageLocationErrorMessage(new Error("boom")), fallback);
});

test("byte formatting matches the settings screen magnitude convention", () => {
  assert.equal(formatStorageBytes(0), "0 B");
  assert.equal(formatStorageBytes(-5), "0 B");
  assert.equal(formatStorageBytes(512), "512 B");
  assert.equal(formatStorageBytes(1024), "1.0 KB");
  assert.equal(formatStorageBytes(1536), "1.5 KB");
  assert.equal(formatStorageBytes(1024 ** 3 * 2.5), "2.5 GB");
});

test("migration progress is only reported while running with a known total", () => {
  assert.equal(migrationPercent(migration({ totalBytes: 0 })), null);
  assert.equal(migrationPercent(migration({ phase: "done", totalBytes: 100, copiedBytes: 100 })), null);
  assert.equal(migrationPercent(migration({ totalBytes: 200, copiedBytes: 50 })), 25);
  assert.equal(migrationPercent(migration({ totalBytes: 200, copiedBytes: 250 })), 100);
});

test("known migration failure prefixes translate; unknown causes stay generic", () => {
  const expectZh = (error: string) => translate("zh-CN", migrationFailureKey(migration({ error })));
  assert.match(expectZh("insufficient_space: need 1 bytes + headroom, available 0 bytes"), /空间不足/);
  assert.match(expectZh("source_missing: D:\\gone"), /找不到原数据目录/);
  assert.match(expectZh("target_unavailable: denied"), /不可写/);
  assert.match(expectZh("runs: byte mismatch at runs/7/video.mp4"), /原因未知/);
  assert.match(expectZh(""), /原因未知/);
});
