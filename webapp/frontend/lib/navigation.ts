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
