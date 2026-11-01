import assert from "node:assert/strict";
import { test } from "node:test";

import {
  clampPlaybackTimeMs,
  COACH_TIMEPOINT_PREROLL_MS,
  coachTimepointPlaybackStartMs,
  playbackShortcutDirection,
  PLAYBACK_SHORTCUT_STEP_MS,
} from "./video-playback";

test("Coach timepoint preroll starts two seconds early and clamps at zero and duration", () => {
  assert.equal(COACH_TIMEPOINT_PREROLL_MS, 2_000);
  assert.equal(coachTimepointPlaybackStartMs(10_000, 30_000), 8_000);
  assert.equal(coachTimepointPlaybackStartMs(1_000, 30_000), 0);
  assert.equal(coachTimepointPlaybackStartMs(10_000, 1_000), 1_000);
});

test("playback seeks clamp both boundaries and allow forward seeks until duration is known", () => {
  assert.equal(clampPlaybackTimeMs(-500, 10_000), 0);
  assert.equal(clampPlaybackTimeMs(11_000, 10_000), 10_000);
  assert.equal(clampPlaybackTimeMs(-500, null), 0);
  assert.equal(clampPlaybackTimeMs(11_000, null), 11_000);
  assert.equal(clampPlaybackTimeMs(11_000, 0), 0);
});

test("J/L shortcuts use two-second steps and leave other keys unassigned", () => {
  assert.equal(PLAYBACK_SHORTCUT_STEP_MS, 2_000);
  assert.equal(playbackShortcutDirection("j"), -1);
  assert.equal(playbackShortcutDirection("J"), -1);
  assert.equal(playbackShortcutDirection("l"), 1);
  assert.equal(playbackShortcutDirection("L"), 1);
  assert.equal(playbackShortcutDirection("ArrowLeft"), null);
  assert.equal(playbackShortcutDirection("k"), null);
});
