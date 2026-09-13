import { promises as fs } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";

import { chromium, expect, test } from "@playwright/test";

const cdpUrl = process.env.AIMING_COOKIE_TAURI_CDP_URL;
const tauriPid = Number(process.env.AIMING_COOKIE_TAURI_PID);
const appUrl = process.env.AIMING_COOKIE_TAURI_APP_URL ?? "http://localhost:3000";

// Live check for the v0.1.5 diagnostics export: invoke the Tauri command
// directly (the save dialog itself needs a human) and validate the bundle.
test("real Tauri capture diagnostics export writes a valid bundle", async () => {
  test.skip(!cdpUrl || !Number.isSafeInteger(tauriPid) || tauriPid <= 0,
    "requires an isolated Tauri smoke instance, its CDP endpoint, and native process id");

  const outPath = path.join(tmpdir(), `ac-diagnostics-live-${Date.now()}.json`);

  const browser = await chromium.connectOverCDP(cdpUrl!);
  const page = browser.contexts()[0]?.pages()[0];
  expect(page, "Tauri WebView page").toBeDefined();
  await page!.goto(`${appUrl}/settings`);

  const returned = await page!.evaluate(async (target: string) => {
    type TauriInternals = {
      invoke: <T>(command: string, args?: Record<string, unknown>) => Promise<T>;
    };
    const internals = (window as unknown as { __TAURI_INTERNALS__: TauriInternals }).__TAURI_INTERNALS__;
    return internals.invoke<string>("desktop_export_capture_diagnostics", { path: target });
  }, outPath);

  expect(returned, "command returns the written path").toBe(outPath);

  const bundle = JSON.parse(await fs.readFile(outPath, "utf8")) as Record<string, unknown>;
  expect(bundle.schemaVersion).toBe("capture_diagnostics.v5");
  expect(typeof bundle.generatedAtUtcMs).toBe("number");
  expect(bundle.targetOs).toBe("windows");
  expect(bundle.appVersion).toBeTruthy();
  // WGC 采集排障依赖 GPU 型号；真实机器至少有一个适配器。
  const gpuNames = bundle.gpuNames as unknown[] | undefined;
  expect(Array.isArray(gpuNames), "gpu adapter names").toBe(true);
  expect(gpuNames!.length).toBeGreaterThan(0);
  expect(typeof bundle.captureDataRoot).toBe("string");
  // v4 optionally embeds the watcher-owned snapshot. Missing or malformed source data is null.
  expect(["object", "undefined"]).toContain(typeof bundle.watcherSnapshot);
  const coordinator = bundle.coordinator as Record<string, unknown> | undefined;
  expect(coordinator, "coordinator status snapshot").toBeDefined();
  // The session id is an internal correlation secret and must be stripped.
  expect(coordinator!.captureSessionId ?? null).toBeNull();
  const events = bundle.events as unknown[] | undefined;
  expect(Array.isArray(events), "diagnostic event ring buffer").toBe(true);
  expect(events!.length).toBeGreaterThan(0);
  // v2：局末结果的磁盘证据必须随包导出（日志尾部 + run 摘要 + 导出回执），
  // 否则内测排障还得再问用户一轮。
  expect(Array.isArray(bundle.recentRuns), "recent run summaries").toBe(true);
  expect(Array.isArray(bundle.exportReceipts), "export receipt history").toBe(true);
  for (const receipt of bundle.exportReceipts as Record<string, unknown>[]) {
    expect(receipt.captureSessionId ?? null).toBeNull();
  }
  // v3：日志健康自检 + coach-error / 轮转日志尾部（缺失时为 null，存在即字符串）。
  const logHealth = bundle.logHealth as Record<string, unknown> | undefined;
  expect(logHealth, "log health block").toBeDefined();
  expect(["number", "object"]).toContain(typeof logHealth!.nativeLogAgeSeconds);
  expect(["number", "object"]).toContain(typeof logHealth!.backendLogAgeSeconds);
  expect(["string", "object"]).toContain(typeof (bundle.coachErrorLogTail ?? null));
  expect(["string", "object"]).toContain(typeof (bundle.backendLogRotatedTail ?? null));

  await fs.rm(outPath, { force: true });
});
