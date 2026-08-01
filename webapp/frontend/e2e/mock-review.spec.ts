import { expect, test } from "@playwright/test";

test("mock review mode keeps DTO-backed state while pages remain navigable", async ({ page, request }) => {
  const before = await request.get("/api/product-state");
  expect(before.ok()).toBeTruthy();
  expect((await before.json()).onboarding_completed).toBe(true);

  const connected = await request.put("/api/kovaak-connection");
  expect(await connected.json()).toEqual({ connected: true });
  const state = await request.get("/api/kovaak-connection");
  expect(await state.json()).toEqual({ connected: true });
  const analysis = await request.get("/api/sessions/42");
  expect(analysis.ok()).toBeTruthy();

  await page.goto("/history");
  await expect(page.getByRole("main")).toBeVisible();
  await page.goto("/analysis/42");
  await expect(page.getByRole("tabpanel")).toBeVisible();
  await page.getByRole("tab").nth(2).click();
  await expect(page.getByRole("tabpanel")).toBeVisible();
  await page.goto("/settings");
  await expect(page.getByRole("main")).toBeVisible();
});
