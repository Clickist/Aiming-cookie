/** Coach time references start two seconds early to show the lead-in motion. */
export const COACH_TIMEPOINT_PREROLL_MS = 2_000;
export const PLAYBACK_SHORTCUT_STEP_MS = 2_000;

/** Clamp seeks at zero and, once known, the media duration. */
export function clampPlaybackTimeMs(timeMs: number, durationMs: number | null): number {
  const nonNegative = Math.max(timeMs, 0);
  return durationMs === null ? nonNegative : Math.min(nonNegative, Math.max(durationMs, 0));
}

export function coachTimepointPlaybackStartMs(timeMs: number, durationMs: number | null): number {
  return clampPlaybackTimeMs(timeMs - COACH_TIMEPOINT_PREROLL_MS, durationMs);
}

/** J rewinds and L advances; other keys remain available to their controls. */
export function playbackShortcutDirection(key: string): -1 | 1 | null {
  const normalized = key.toLowerCase();
  if (normalized === "j") return -1;
  if (normalized === "l") return 1;
  return null;
}
