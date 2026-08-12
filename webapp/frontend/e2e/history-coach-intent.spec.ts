import { expect, test } from "@playwright/test";

import {
  RUN_PENDING_MULTIMODAL,
  apiScenario,
  installApiFixtures,
  installDesktopBridge,
} from "../fixtures/task7-fixtures";

test.describe("History → Coach analysis intent", () => {
  test("clicking '让 Coach 分析' shows confirmation card on Coach workspace without pre-submitting", async ({ page }) => {
    await installDesktopBridge(page);
    await installApiFixtures(page, apiScenario({ runs: [RUN_PENDING_MULTIMODAL] }));
    await page.goto("/history");

    // Verify the pending Run is visible with the Coach analysis action.
    const coachButton = page.getByRole("button", { name: "让 Coach 分析" }).first();
    await expect(coachButton).toBeVisible();

    // Track whether the analysis POST happens before user confirmation.
    let analyzePosted = false;
    page.on("request", (request) => {
      if (request.method() === "POST" && /\/api\/kovaak-runs\/\d+\/analyze$/.test(new URL(request.url()).pathname)) {
        analyzePosted = true;
      }
    });

    // Click the button — this navigates to / and should carry the intent.
    await coachButton.click();

    // Must land on the Coach workspace.
    await expect(page).toHaveURL(/^http:\/\/127\.0\.0\.1:\d+\/$/);

    // The confirmation card with the Run and "确认并开始" must appear.
    await expect(page.getByText("分析所选训练", { exact: false })).toBeVisible();
    await expect(page.getByRole("button", { name: "确认并开始" })).toBeVisible();

    // The analyze endpoint must NOT have been called before confirmation.
    expect(analyzePosted).toBe(false);
  });
});
