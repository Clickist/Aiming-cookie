import { promises as fs } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";

import { chromium, expect, test } from "@playwright/test";

const cdpUrl = process.env.AIMING_COOKIE_TAURI_CDP_URL;
const tauriPid = Number(process.env.AIMING_COOKIE_TAURI_PID);
const appUrl = process.env.AIMING_COOKIE_TAURI_APP_URL ?? "http://localhost:3000";

// Live check for the v0.1.3 diagnostics export: invoke the Tauri command
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
  expect(bundle.schemaVersion).toBe("capture_diagnostics.v1");
  expect(typeof bundle.generatedAtUtcMs).toBe("number");
  expect(bundle.targetOs).toBe("windows");
  expect(bundle.appVersion).toBeTruthy();
  expect(typeof bundle.captureDataRoot).toBe("string");
  const coordinator = bundle.coordinator as Record<string, unknown> | undefined;
  expect(coordinator, "coordinator status snapshot").toBeDefined();
  // The session id is an internal correlation secret and must be stripped.
  expect(coordinator!.captureSessionId ?? null).toBeNull();
  const events = bundle.events as unknown[] | undefined;
  expect(Array.isArray(events), "diagnostic event ring buffer").toBe(true);
  expect(events!.length).toBeGreaterThan(0);

  await fs.rm(outPath, { force: true });
});
