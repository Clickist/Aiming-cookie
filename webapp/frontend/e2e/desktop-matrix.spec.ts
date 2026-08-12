import { chromium, expect, test } from "@playwright/test";

import {
  RUN_PENDING_MULTIMODAL,
  RUN_PENDING_NATIVE,
  apiScenario,
  installApiFixtures,
  redirectTauriRuntime,
} from "../fixtures/task7-fixtures";

const cdpUrl = process.env.AIMING_COOKIE_TAURI_CDP_URL;
const tauriPid = Number(process.env.AIMING_COOKIE_TAURI_PID);
const appUrl = process.env.AIMING_COOKIE_TAURI_APP_URL ?? "http://localhost:3000";

test("real Tauri Desktop capability and fixture-backed UI matrix", async () => {
  test.skip(!cdpUrl || !Number.isSafeInteger(tauriPid) || tauriPid <= 0,
    "requires an isolated Tauri smoke instance, its CDP endpoint, and native process id");

  const browser = await chromium.connectOverCDP(cdpUrl!);
  const page = browser.contexts()[0]?.pages()[0];
  expect(page, "Tauri WebView page").toBeDefined();
  await page!.setViewportSize({ width: 1280, height: 820 });
  await page!.unrouteAll({ behavior: "wait" });
  await page!.goto(`${appUrl}/settings`);

  // Verify the WebView renders real Aiming Cookie product content, not just a live process.
  await expect(page!.locator('[aria-label="Aiming Cookie"]')).toBeVisible();

  const actual = await page!.evaluate(async () => {
    type TauriInternals = {
      invoke: <T>(command: string, args?: Record<string, unknown>) => Promise<T>;
    };
    type RuntimeConnection = { baseUrl: string; token: string };
    const internals = (window as unknown as { __TAURI_INTERNALS__: TauriInternals }).__TAURI_INTERNALS__;
    const native = await internals.invoke<Record<string, unknown>>("desktop_capture_coordinator_status");
    const runtime = await internals.invoke<RuntimeConnection>("desktop_runtime_connection");
    const headers = {
      "X-Aiming-Cookie-Desktop-Token": runtime.token,
      "X-User-Id": "desktop-local",
    };
    const read = async (path: string) => {
      const response = await fetch(`${runtime.baseUrl}${path}`, { headers });
      return { status: response.status, body: await response.json() as unknown };
    };
    return {
      native,
      runtimeOrigin: new URL(runtime.baseUrl).origin,
      capture: await read("/api/capture-status"),
      storage: await read("/api/storage"),
      tasks: await read("/api/tasks"),
      viewport: {
        innerWidth: window.innerWidth,
        innerHeight: window.innerHeight,
        outerWidth: window.outerWidth,
        outerHeight: window.outerHeight,
      },
    };
  });

  expect(actual.runtimeOrigin).toMatch(/^http:\/\/127\.0\.0\.1:\d+$/);
  expect(actual.capture.status).toBe(200);
  expect(actual.capture.body).toMatchObject({ schema_version: "capture_status.v1" });
  expect(actual.storage.status).toBe(200);
  expect(actual.storage.body).toMatchObject({ total_bytes: expect.any(Number) });
  expect(actual.tasks.status).toBe(200);
  expect(actual.tasks.body).toMatchObject({ schema_version: "task_list.v1" });
  expect(actual.native).toMatchObject({
    enabled: expect.any(Boolean),
    kovaakProcessPresent: expect.any(Boolean),
    raw: { state: expect.any(String) },
    video: { state: expect.any(String) },
  });
  expect(actual.viewport.innerWidth).toBeGreaterThanOrEqual(960);
  expect(actual.viewport.innerHeight).toBeGreaterThanOrEqual(640);
  expect(JSON.stringify(actual)).not.toMatch(/(?:[A-Z]:\\|\/Users\/|secret|token|traceback|stack)/i);

  // Redirect Tauri runtime API calls to the dev server so Playwright fixtures can intercept.
  await redirectTauriRuntime(page!, appUrl);
  await installApiFixtures(page!, apiScenario({ runs: [RUN_PENDING_MULTIMODAL, RUN_PENDING_NATIVE] }));

  // History page shows pending runs for analysis.
  await page!.goto(`${appUrl}/history`);
  await expect(page!.getByRole("button", { name: "让 Coach 分析" }).first()).toBeVisible();

  // Coach workspace is the default home surface with Session rail.
  await page!.goto(`${appUrl}/`);
  await expect(page!.getByRole("navigation", { name: "会话" })).toBeVisible();
  await expect(page!.getByText("Aiming Coach", { exact: true })).toBeVisible();

  // Retired URLs redirect to History (bounded compatibility).
  await page!.goto(`${appUrl}/analyze`);
  await expect(page!).toHaveURL(/\/history/);
  await page!.goto(`${appUrl}/tasks`);
  await expect(page!).toHaveURL(/\/history/);

  // Settings storage section stays on the product surface.
  await page!.goto(`${appUrl}/settings`);
  for (const label of ["Run 录像", "Raw trace", "分析产物", "未完成采集"]) {
    await expect(page!.getByText(label, { exact: true }).first()).toBeVisible();
  }
  await expect(page!.getByText("仅保留最近 300 秒", { exact: false })).toBeVisible();
});
