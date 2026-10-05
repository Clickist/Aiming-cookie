import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join, resolve } from "node:path";
import test from "node:test";

const frontendRoot = resolve(import.meta.dirname, "..");

function read(relativePath: string): string {
  return readFileSync(join(frontendRoot, relativePath), "utf8");
}

// RuntimeGate 启动闸门（2026-10-06 启动闪退案前端侧）合同：
// 启动屏 = logo + 流动进度条（不承诺百分比）+ 慢启动提示；失败态窗口存活、
// 给一键导出诊断包出口；reduced-motion 下流动条降级为静态满宽条（全局
// reduced-motion 块会把动画钉在首帧＝滑块停在轨道外，必须有显式兜底）。

test("gate shows the product mark and an indeterminate bar, no fake percentage", () => {
  const tsx = read("components/task3/RuntimeGate.tsx");
  assert.match(tsx, /src="\/logo-mark\.png"/);
  assert.match(tsx, /task3-runtime-gate__track/);
  assert.match(tsx, /task3-runtime-gate__thumb/);
  assert.doesNotMatch(tsx, /width:\s*[%"']?\d+%.*progress|aria-valuenow/i);
});

test("gate surfaces slow-start hint and the diagnostics export exit", () => {
  const tsx = read("components/task3/RuntimeGate.tsx");
  assert.match(tsx, /desktop\.runtime\.slowHint/);
  assert.match(tsx, /exportDesktopCaptureDiagnostics/);
  assert.match(tsx, /RUNTIME_FAILED_CODE/);
  assert.match(tsx, /awaitDesktopRuntimeConnection/);
});

test("gate keeps the app mounted only after the runtime reports ready", () => {
  const tsx = read("components/task3/RuntimeGate.tsx");
  assert.match(tsx, /state === "ready"\) return <>\{children\}<\/>/);
  const layout = read("app/layout.tsx");
  assert.match(layout, /<RuntimeGate>[\s\S]*?<AppShell>/);
});

test("gate styles carry the slide animation and a reduced-motion static fallback", () => {
  const css = read("components/task3/task3.css");
  const track = css.match(/\.task3-runtime-gate__track\s*\{[^}]*\}/)?.[0] ?? "";
  assert.match(track, /overflow:\s*hidden/);
  const thumb = css.match(/\.task3-runtime-gate__thumb\s*\{[^}]*\}/)?.[0] ?? "";
  assert.match(thumb, /task3-runtime-gate-slide/);
  assert.match(css, /@keyframes task3-runtime-gate-slide/);
  // logo 资产是纯白单色：浅色底反相成黑（点点 1006 分离度反馈），深色恢复原白。
  const mark = css.match(/\.task3-runtime-gate__mark\s*\{[^}]*\}/)?.[0] ?? "";
  assert.match(mark, /filter:\s*invert\(1\)/);
  assert.match(css, /:root\[data-theme="dark"\] \.task3-runtime-gate__mark\s*\{[^}]*filter:\s*none/);
  const reduce = css.match(
    /@media \(prefers-reduced-motion: reduce\)\s*\{[\s\S]*?task3-runtime-gate__thumb[\s\S]*?\}/,
  )?.[0];
  assert.ok(reduce, "reduced-motion override for the gate thumb must exist");
  assert.match(reduce, /width:\s*100%/);
});

test("gate i18n keys exist in both locales", () => {
  for (const shard of ["lib/i18n/dict/task6.zh.ts", "lib/i18n/dict/task6.en.ts"]) {
    const dict = read(shard);
    for (const key of [
      "desktop.runtime.starting",
      "desktop.runtime.slowHint",
      "desktop.runtime.failedTitle",
      "desktop.runtime.failedBody",
      "desktop.runtime.exportDiagnostics",
      "desktop.runtime.exportDone",
      "desktop.runtime.exportFailed",
    ]) {
      assert.ok(dict.includes(`"${key}"`), `${shard} missing ${key}`);
    }
  }
});
