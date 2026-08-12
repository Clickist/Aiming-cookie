import { defineConfig, devices } from "@playwright/test";

const tauriCdp = !!process.env.AIMING_COOKIE_TAURI_CDP_URL;

export default defineConfig({
  testDir: "./e2e",
  testIgnore: "mock-review.spec.ts",
  outputDir: "./test-results",
  fullyParallel: false,
  forbidOnly: true,
  retries: 0,
  workers: 1,
  reporter: [["list"], ["html", { outputFolder: "playwright-report", open: "never" }]],
  expect: {
    timeout: 10_000,
    toHaveScreenshot: {
      animations: "disabled",
      maxDiffPixelRatio: 0.005,
    },
  },
  use: {
    ...devices["Desktop Edge"],
    baseURL: "http://127.0.0.1:3106",
    channel: "msedge",
    headless: true,
    screenshot: "only-on-failure",
    trace: "retain-on-failure",
  },
  // Tauri E2E tests connect via CDP; the browser webServer is only needed for non-Tauri specs.
  webServer: tauriCdp ? undefined : {
    command: "npm run start -- --port 3106",
    url: "http://127.0.0.1:3106",
    reuseExistingServer: true,
    timeout: 30_000,
  },
});
