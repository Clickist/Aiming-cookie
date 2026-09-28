import assert from "node:assert/strict";
import { resolve } from "node:path";
import test from "node:test";

import { getDataRoot, resolveDataRoot } from "../src/app-data.ts";

// 生效数据根的唯一权威是 Rust 壳（storage-location 指针可能把根挪到自定义盘），
// 壳解析完再以 DATA_ROOT 注入三进程；sidecar 侧只负责「有就用、没有才回落开发
// 目录」，且回落必须只发生在真没注入的时候——把空串当注入会让 sidecar 写进进程
// cwd（打包版即安装目录），污染用户机器。

test("injected DATA_ROOT is the effective root, resolved as-is", () => {
  assert.equal(resolveDataRoot("D:\\ACData", "C:\\repo"), resolve("D:\\ACData"));
  assert.equal(resolveDataRoot("/mnt/data/aiming", "/repo"), resolve("/mnt/data/aiming"));
  // 前后空白是 shell/环境变量搬运噪音，不构成另一个路径。
  assert.equal(resolveDataRoot("  D:\\ACData  ", "C:\\repo"), resolve("D:\\ACData"));
});

test("missing or blank DATA_ROOT falls back to the local development app-data dir", () => {
  assert.equal(resolveDataRoot(undefined, "C:\\repo"), resolve("C:\\repo", "app-data"));
  assert.equal(resolveDataRoot("", "C:\\repo"), resolve("C:\\repo", "app-data"));
  assert.equal(resolveDataRoot("   ", "C:\\repo"), resolve("C:\\repo", "app-data"));
  assert.equal(resolveDataRoot("\t\n", "C:\\repo"), resolve("C:\\repo", "app-data"));
});

test("getDataRoot honors the injected root instead of the process cwd", () => {
  const previous = process.env.DATA_ROOT;
  const injected = resolve("aiming-cookie-app-data-test");
  try {
    process.env.DATA_ROOT = injected;
    assert.equal(getDataRoot(), injected);
  } finally {
    if (previous === undefined) delete process.env.DATA_ROOT;
    else process.env.DATA_ROOT = previous;
  }
});
