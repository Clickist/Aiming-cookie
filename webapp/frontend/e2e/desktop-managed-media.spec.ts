import { copyFile, mkdir, unlink } from "node:fs/promises";
import path from "node:path";

import { chromium, expect, test } from "@playwright/test";

import {
  COACH_PRIMARY,
  UNAVAILABLE_EVIDENCE_SEGMENTS,
  analysisSession,
  apiScenario,
  installApiFixtures,
  partialAnalysisSession,
  redirectTauriRuntime,
} from "../fixtures/task7-fixtures";

const cdpUrl = process.env.AIMING_COOKIE_TAURI_CDP_URL;
const appDataDir = process.env.AIMING_COOKIE_TAURI_APP_DATA;
const mediaFixture = process.env.AIMING_COOKIE_TAURI_MEDIA_FIXTURE;
const appUrl = process.env.AIMING_COOKIE_TAURI_APP_URL ?? "http://localhost:3000";

const evidenceCard = {
  schema_version: "coach_message_card.v1" as const,
  kind: "evidence" as const,
  analysis_ref: "analysis:42",
  target_ref: null,
  time_range_ms: [1000],
};

function coachPrimaryWithEvidence() {
  return {
    ...COACH_PRIMARY,
    messages: [{
      ...COACH_PRIMARY.messages[0],
      cards: [evidenceCard],
    }],
  };
}

test("real Tauri WebView plays managed media from Coach evidence and degrades a removed source locally", async () => {
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
  await page!.setViewportSize({ width: 1280, height: 820 });
  await page!.unrouteAll({ behavior: "wait" });

  const base = analysisSession();
  const baseResult = base.result;
  if (!baseResult || baseResult.schema_version !== "analysis_result.v2") {
    throw new Error("video fallback fixture requires AnalysisResultV2");
  }
  await redirectTauriRuntime(page!, appUrl);
  await installApiFixtures(page!, apiScenario({
    analysis: analysisSession({
      result: { ...baseResult, input_mode: "video_fallback" },
      history: {
        ...base.history!,
        input_mode: "video_fallback",
        source_availability: { ...base.history!.source_availability, mp4: "available" },
        visual_replay: {
          kind: "seekable_mp4",
          available: true,
          seekable: true,
          endpoint: "/api/sessions/42/video",
          artifact_ref: "analysis:42:video",
          reason: null,
        },
      },
    }),
    coachPrimary: coachPrimaryWithEvidence(),
  }));

  await page!.goto(new URL("/", appUrl).toString());

  // Open the video from the Coach evidence card.
  const videoButton = page!.getByRole("button", { name: /在视频中查看/ });
  await expect(videoButton).toBeVisible();

  const rangeResponse = page!.waitForResponse((response) =>
    response.url() === "http://aiming-cookie-media.localhost/analysis/42"
      && response.status() === 206,
  );
  await videoButton.click();

  // Verify the Coach video pane opened and the managed media plays.
  await expect(page!.locator('[aria-label="Coach 视频讲解"]')).toBeVisible();
  const video = page!.locator("video");
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

  // Remove the source and verify the WebView reports a local media error.
  await unlink(videoPath);
  const mediaErrorCode = await video.evaluate((element) => new Promise<number>((resolve) => {
    const media = element as HTMLVideoElement;
    const timeout = window.setTimeout(() => resolve(-1), 5_000);
    media.addEventListener("error", () => {
      window.clearTimeout(timeout);
      resolve(media.error?.code ?? 0);
    }, { once: true });
    media.load();
  }));
  expect(mediaErrorCode).toBeGreaterThan(0);

  // Switch to an unavailable analysis: the evidence card degrades without a video button.
  await page!.unrouteAll({ behavior: "wait" });
  await installApiFixtures(page!, apiScenario({
    analysis: partialAnalysisSession(),
    coachPrimary: coachPrimaryWithEvidence(),
    evidenceSegments: UNAVAILABLE_EVIDENCE_SEGMENTS,
  }));
  await page!.reload();
  await expect(page!.getByRole("button", { name: /在视频中查看/ })).toHaveCount(0);
  await expect(page!.locator('[aria-label="Aiming Cookie"]')).toBeVisible();
});
