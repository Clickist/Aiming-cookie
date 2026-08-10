export function analysisHref(analysisId: number): string {
  if (!Number.isSafeInteger(analysisId) || analysisId <= 0) {
    throw new Error("Analysis id is invalid");
  }
  return `/analysis?id=${analysisId}`;
}

export function analysisIdFromLocation(pathname: string, search: string): number | null {
  let raw: string | null = null;
  if (pathname === "/analysis" || pathname === "/analysis/") {
    raw = new URLSearchParams(search).get("id");
  } else {
    const match = /^\/analysis\/(\d+)$/.exec(pathname);
    raw = match?.[1] ?? null;
  }
  if (raw === null || !/^\d+$/.test(raw)) return null;
  const value = Number(raw);
  return Number.isSafeInteger(value) && value > 0 ? value : null;
}

export type GuidanceTargetId =
  | "coach.panel"
  | "settings.provider_auth"
  | "desktop.capture_control"
  | "history.runs"
  | "training.current"
  | "storage.incomplete";

export interface GuidanceTargetResolution {
  targetId: GuidanceTargetId;
  route: string;
  sectionId: string | null;
  safePrefillKeys: readonly string[];
}

const GUIDANCE_TARGETS: Record<GuidanceTargetId, GuidanceTargetResolution> = {
  "coach.panel": { targetId: "coach.panel", route: "/", sectionId: null, safePrefillKeys: [] },
  "settings.provider_auth": {
    targetId: "settings.provider_auth",
    route: "/settings",
    sectionId: "llm-provider",
    safePrefillKeys: ["provider_profile_ref"],
  },
  "desktop.capture_control": {
    targetId: "desktop.capture_control",
    route: "/settings",
    sectionId: "capture",
    safePrefillKeys: [],
  },
  "history.runs": { targetId: "history.runs", route: "/history", sectionId: null, safePrefillKeys: ["run_ref"] },
  "training.current": { targetId: "training.current", route: "/", sectionId: null, safePrefillKeys: ["plan_ref"] },
  "storage.incomplete": { targetId: "storage.incomplete", route: "/settings", sectionId: "storage", safePrefillKeys: ["item_ref"] },
};

const SAFE_PREFILL = /^[A-Za-z0-9._:-]{1,160}$/;
const SAFE_PREFILL_BY_KEY: Record<string, RegExp> = {
  provider_profile_ref: /^provider_profile:[1-9][0-9]*$/,
  run_ref: /^run:[1-9][0-9]*$/,
  plan_ref: /^plan:[A-Za-z0-9._:-]{1,160}$/,
  item_ref: /^incomplete:[a-f0-9]{16,128}$/,
};

export function resolveGuidanceTarget(targetId: string): GuidanceTargetResolution | null {
  return Object.prototype.hasOwnProperty.call(GUIDANCE_TARGETS, targetId)
    ? GUIDANCE_TARGETS[targetId as GuidanceTargetId]
    : null;
}

export function validateGuidancePrefill(
  targetId: string,
  prefill: Record<string, string> | null | undefined,
): Record<string, string> | null {
  const target = resolveGuidanceTarget(targetId);
  if (!target || prefill == null) return prefill == null && target ? {} : null;
  const entries = Object.entries(prefill);
  if (entries.some(([key, value]) => !target.safePrefillKeys.includes(key) || !SAFE_PREFILL.test(value) || !SAFE_PREFILL_BY_KEY[key]?.test(value))) return null;
  return Object.fromEntries(entries);
}

