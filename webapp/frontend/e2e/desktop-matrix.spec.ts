import { execFile } from "node:child_process";
import { promisify } from "node:util";

import { chromium, expect, test } from "@playwright/test";

import {
  RUN_PENDING_MULTIMODAL,
  RUN_PENDING_NATIVE,
  apiScenario,
  installApiFixtures,
} from "../fixtures/task7-fixtures";

const execFileAsync = promisify(execFile);
const cdpUrl = process.env.AIMING_COOKIE_TAURI_CDP_URL;
const tauriPid = Number(process.env.AIMING_COOKIE_TAURI_PID);
const appUrl = process.env.AIMING_COOKIE_TAURI_APP_URL ?? "http://localhost:3000";

async function dismissNativeFilePicker(processId: number): Promise<string> {
  const script = String.raw`
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
$deadline = [DateTime]::UtcNow.AddSeconds(10)
while ([DateTime]::UtcNow -lt $deadline) {
  $windows = [System.Windows.Automation.AutomationElement]::RootElement.FindAll(
    [System.Windows.Automation.TreeScope]::Descendants,
    [System.Windows.Automation.Condition]::TrueCondition
  )
  $dialog = $windows | Where-Object {
    $_.Current.ProcessId -eq ${processId} -and
    $_.Current.ClassName -eq '#32770' -and
    $_.Current.Name -like '*选择 MP4*'
  } | Select-Object -First 1
  if ($null -ne $dialog) {
    $title = $dialog.Current.Name
    $window = [System.Windows.Automation.WindowPattern]$dialog.GetCurrentPattern(
      [System.Windows.Automation.WindowPattern]::Pattern
    )
    $window.Close()
    $closeDeadline = [DateTime]::UtcNow.AddSeconds(5)
    while ([DateTime]::UtcNow -lt $closeDeadline) {
      $remaining = [System.Windows.Automation.AutomationElement]::RootElement.FindAll(
        [System.Windows.Automation.TreeScope]::Descendants,
        [System.Windows.Automation.Condition]::TrueCondition
      ) | Where-Object {
        $_.Current.ProcessId -eq ${processId} -and
        $_.Current.ClassName -eq '#32770' -and
        $_.Current.Name -eq $title
      }
      if (@($remaining).Count -eq 0) {
        Write-Output $title
        exit 0
      }
      Start-Sleep -Milliseconds 100
    }
    Write-Error 'Windows MP4 picker did not close after Escape'
    exit 1
  }
  Start-Sleep -Milliseconds 100
}
Write-Error 'Windows MP4 picker was not observed'
exit 1
`;
  const { stdout } = await execFileAsync(
    "powershell.exe",
    ["-NoProfile", "-Command", script],
    { encoding: "utf8" },
  );
  return stdout.trim();
}

test("real Tauri Desktop capability and fixture-backed UI matrix", async () => {
  test.skip(!cdpUrl || !Number.isSafeInteger(tauriPid) || tauriPid <= 0,
    "requires an isolated Tauri smoke instance, its CDP endpoint, and native process id");

  const browser = await chromium.connectOverCDP(cdpUrl!);
  const page = browser.contexts()[0]?.pages()[0];
  expect(page, "Tauri WebView page").toBeDefined();
  await page!.setViewportSize({ width: 1280, height: 820 });
  await page!.unrouteAll({ behavior: "wait" });
  await page!.goto(`${appUrl}/settings`);

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

  await installApiFixtures(page!, apiScenario({ runs: [RUN_PENDING_MULTIMODAL, RUN_PENDING_NATIVE] }));
  await page!.goto(`${appUrl}/analyze`);
  await expect(page!.getByRole("heading", { name: "新建分析", exact: true })).toBeVisible();
  await expect(page!.getByText("自动采集：采集中", { exact: false })).toBeVisible();
  await expect(page!.getByText("2 条待分析", { exact: true })).toBeVisible();
  await expect(page!.locator('input[name="run"]:checked')).toHaveCount(0);

  const runs = page!.locator('input[name="run"]');
  await runs.nth(0).locator("xpath=ancestor::label").click();
  await expect(runs.nth(0)).toBeChecked();
  const modes = page!.locator('input[name="input-mode"]');
  await expect(modes).toHaveCount(3);
  for (let index = 0; index < 3; index += 1) {
    await expect(modes.nth(index)).toBeEnabled();
    await modes.nth(index).locator("xpath=ancestor::label").click();
    await expect(modes.nth(index)).toBeChecked();
  }

  await runs.nth(1).locator("xpath=ancestor::label").click();
  await expect(runs.nth(1)).toBeChecked();
  await expect(page!.locator('input[name="input-mode"]:not([disabled])')).toHaveCount(1);
  await expect(page!.locator('input[name="input-mode"]:checked')).toHaveCount(0);
  await page!.goto(`${appUrl}/tasks`);
  await expect(page!.getByText("正在导入", { exact: true }).first()).toBeVisible();
  await expect(page!.getByText("运行中", { exact: true }).first()).toBeVisible();
  await page!.reload();
  await expect(page!.getByText("正在导入", { exact: true }).first()).toBeVisible();
  await expect(page!.getByText("运行中", { exact: true }).first()).toBeVisible();

  await page!.goto(`${appUrl}/analyze`);
  await expect(page!.locator('input[name="run"]')).toHaveCount(2);
  await expect(page!.locator('input[name="run"]:checked')).toHaveCount(0);
  await page!.goto(`${appUrl}/settings`);
  for (const label of ["Run 录像", "Raw trace", "分析产物", "未完成采集"]) {
    await expect(page!.getByText(label, { exact: true }).first()).toBeVisible();
  }
  await expect(page!.getByText("仅保留最近 300 秒", { exact: false })).toBeVisible();

  await page!.goto(`${appUrl}/analyze`);
  const [, pickerTitle] = await Promise.all([
    page!.locator(".task3-analyze-drop-card").first().click(),
    dismissNativeFilePicker(tauriPid),
  ]);
  expect(pickerTitle).toContain("选择 MP4");
  await expect(page!.locator(".task3-analyze-drop-card").first()).toHaveAttribute("data-filled", "false");
});
