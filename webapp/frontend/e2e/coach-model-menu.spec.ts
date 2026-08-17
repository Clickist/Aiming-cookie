import { expect, test } from "@playwright/test";

import {
  PROVIDER_CATALOG_TWO_MODELS,
  PROVIDER_PROFILE,
  READY_PROVIDER_STATUS,
  apiScenario,
  installApiFixtures,
  installDesktopBridge,
} from "../fixtures/task7-fixtures";

test.describe("Coach composer model menu", () => {
  test("opens upward, switches the default profile model, and shows the new name", async ({ page }) => {
    await installDesktopBridge(page);
    await installApiFixtures(page, apiScenario({
      catalog: PROVIDER_CATALOG_TWO_MODELS,
      profiles: { profiles: [{ ...PROVIDER_PROFILE }] },
      providerStatus: READY_PROVIDER_STATUS,
    }));
    await page.goto("/");

    await expect(page.getByText("Aiming Coach", { exact: true })).toBeVisible();
    const menuButton = page.locator(".task6-composer-model");
    await expect(menuButton).toBeVisible();
    await expect(menuButton).toContainText("GPT-5.4");

    // Opens a menu listing both catalog models with the current one checked.
    await menuButton.click();
    const menu = page.getByRole("menu", { name: "当前 Provider 的模型" });
    await expect(menu).toBeVisible();
    await expect(menu.getByRole("menuitemradio")).toHaveCount(2);
    await expect(menu.getByRole("menuitemradio", { checked: true })).toContainText("GPT-5.4");

    // Switching posts the model switch request.
    let switchPosted = false;
    page.on("request", (request) => {
      if (request.method() === "POST" && /\/provider-profiles\/model$/.test(new URL(request.url()).pathname)) {
        switchPosted = true;
      }
    });
    await menu.getByRole("menuitemradio", { name: "GPT-5.4 Mini" }).click();

    // Menu closes and the button shows the resolved model name.
    await expect(menu).not.toBeVisible();
    await expect(menuButton).toContainText("GPT-5.4 Mini", { timeout: 10_000 });
    expect(switchPosted).toBe(true);
  });

  test("does not render when the provider catalog offers a single model", async ({ page }) => {
    await installDesktopBridge(page);
    await installApiFixtures(page, apiScenario({
      profiles: { profiles: [{ ...PROVIDER_PROFILE }] },
      providerStatus: READY_PROVIDER_STATUS,
    }));
    await page.goto("/");

    await expect(page.getByText("Aiming Coach", { exact: true })).toBeVisible();
    await expect(page.getByRole("textbox", { name: "向 Coach 提问" })).toBeVisible();
    await expect(page.locator(".task6-composer-model")).toHaveCount(0);
  });
});
