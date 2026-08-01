import { NextRequest } from "next/server";

import { apiScenario, handleReviewApiRequest, readReviewVideo, type ApiScenario } from "@/mocks/review-scenario";

export const runtime = "nodejs";

const scenarios = new Map<string, ApiScenario>();

async function route(request: NextRequest, context: { params: Promise<{ path: string[] }> }) {
  const { path } = await context.params;
  const owner = request.headers.get("X-User-Id") ?? "dev";
  const scenario = scenarios.get(owner) ?? apiScenario();
  scenarios.set(owner, scenario);
  const contentType = request.headers.get("content-type") ?? "";
  const body = contentType.includes("application/json") ? await request.json().catch(() => null) : null;
  const result = handleReviewApiRequest(scenario, { method: request.method, path: `/api/${path.join("/")}`, body });
  if (result.video) {
    const video = await readReviewVideo();
    return new Response(new Uint8Array(video).buffer as ArrayBuffer, { headers: { "Content-Type": "video/mp4", "Accept-Ranges": "bytes" } });
  }
  return Response.json(result.body, { status: result.status });
}

export const GET = route;
export const POST = route;
export const PUT = route;
export const DELETE = route;
