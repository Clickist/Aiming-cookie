import { copyFile, mkdir, unlink } from "node:fs/promises";
import path from "node:path";

import { chromium, expect, test } from "@playwright/test";

import {
  UNAVAILABLE_EVIDENCE_SEGMENTS,
  analysisSession,
  apiScenario,
  installApiFixtures,
  partialAnalysisSession,
} from "../fixtures/task7-fixtures";

const cdpUrl = process.env.AIMING_COOKIE_TAURI_CDP_URL;
const appDataDir = process.env.AIMING_COOKIE_TAURI_APP_DATA;
const mediaFixture = process.env.AIMING_COOKIE_TAURI_MEDIA_FIXTURE;

test("real Tauri WebView plays managed media and degrades a removed source locally", async () => {
  test.skip(
    !cdpUrl || !appDataDir || !mediaFixture,
    "requires an isolated Tauri smoke instance and a valid MP4 fixture",
  );

  const videoPath = path.join(appDataDir!, "sessions", "42", "video.mp4");
  await mkdir(path.dirname(videoPath), { recursive: true });
  await copyFile(mediaFixture!, videoPath);

  const browser = await chromium.connectOverCDP(cdpUrl!);
  const page = browser.contexts()[0]?.pages()[0];
  expect(page, "Tauri WebView page").toBeDefined();
  await page!.unrouteAll({ behavior: "wait" });
  const base = analysisSession();
  const baseResult = base.result;
  if (!baseResult || baseResult.schema_version !== "analysis_result.v2") {
    throw new Error("video fallback fixture requires AnalysisResultV2");
  }
  await installApiFixtures(page!, apiScenario({
    analysis: analysisSession({
      input_mode: "video_fallback",
      result: { ...baseResult, input_mode: "video_fallback" },
      history: { ...base.history!, input_mode: "video_fallback" },
    }),
  }));

  await page!.goto("http://localhost:3000/analysis/42");
  const rangeResponse = page!.waitForResponse((response) =>
    response.url() === "http://aiming-cookie-media.localhost/analysis/42"
      && response.status() === 206,
  );
  await page!.getByRole("tab", { name: "视频" }).click();
  const video = page!.locator("video[controls]");
  await expect(video).toBeVisible();
  await rangeResponse;
  await expect.poll(() =>
    video.evaluate((element) => (element as HTMLVideoElement).readyState)
  ).toBeGreaterThanOrEqual(1);
  const mediaState = await video.evaluate((element) => {
    const media = element as HTMLVideoElement;
    return { currentSrc: media.currentSrc, duration: media.duration, readyState: media.readyState };
  });
  expect(mediaState.currentSrc).toBe("http://aiming-cookie-media.localhost/analysis/42");
  expect(mediaState.duration).toBeGreaterThan(0);
  expect(mediaState.readyState).toBeGreaterThanOrEqual(1);

  await unlink(videoPath);
  const unavailable = await page!.evaluate(async (url) => {
    const response = await fetch(url);
    return { status: response.status, body: await response.json() };
  }, mediaState.currentSrc);
  expect(unavailable).toEqual({
    status: 410,
    body: {
      schema_version: "managed_video_unavailable.v1",
      availability: "unavailable",
      reason: "managed_video_unavailable",
    },
  });

  await page!.unrouteAll({ behavior: "wait" });
  await installApiFixtures(page!, apiScenario({
    analysis: partialAnalysisSession(),
    evidenceSegments: UNAVAILABLE_EVIDENCE_SEGMENTS,
  }));
  await page!.reload();
  await page!.getByRole("tab", { name: "视频" }).click();
  await expect(page!.getByText("视觉证据当前不可用")).toBeVisible();
  await expect(page!.getByRole("heading", { name: "1wall 6targets small" })).toBeVisible();
  await expect(page!.locator('.ac-state[role="alert"]')).toHaveCount(0);
});
