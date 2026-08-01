import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  testMatch: "mock-review.spec.ts",
  workers: 1,
  use: { ...devices["Desktop Edge"], baseURL: "http://localhost:3107", channel: "msedge", headless: true },
  webServer: { command: "npm run dev:mock -- --port 3107", url: "http://localhost:3107", reuseExistingServer: false, timeout: 30_000 },
});
