import { expect, test } from "@playwright/test";

import {
  RUN_PENDING_MULTIMODAL,
  apiScenario,
  installApiFixtures,
  installDesktopBridge,
} from "../fixtures/task7-fixtures";

test.describe("History → Coach analysis intent", () => {
  test("clicking '让 Coach 分析' drafts the Coach request without pre-submitting", async ({ page }) => {
    await installDesktopBridge(page);
    await installApiFixtures(page, apiScenario({ runs: [RUN_PENDING_MULTIMODAL] }));
    await page.goto("/history");

    // 「让 Coach 分析」在勾选训练后才出现；勾选 pending Run。
    const coachButton = page.getByRole("button", { name: "让 Coach 分析" });
    await expect(coachButton).toHaveCount(0);
    await page.getByRole("checkbox", { name: /选择 1wall 6targets small/ }).check();
    await expect(coachButton).toBeVisible();

    // Track whether the analysis POST happens before the user sends the draft.
    let analyzePosted = false;
    page.on("request", (request) => {
      if (request.method() === "POST" && /\/api\/kovaak-runs\/\d+\/analyze$/.test(new URL(request.url()).pathname)) {
        analyzePosted = true;
      }
    });

    // Click the button — this navigates to / and carries the intent as a draft.
    await coachButton.click();

    // Must land on the Coach workspace with a pre-filled draft, not a confirmation card.
    await expect(page).toHaveURL(/^http:\/\/127\.0\.0\.1:\d+\/$/);
    await expect(page.locator("#coach-draft")).toBeVisible();
    await expect(page.locator("#coach-draft")).toHaveValue(/1wall 6targets small/);

    // The analyze endpoint must NOT have been called before the user sends.
    expect(analyzePosted).toBe(false);
  });
});
