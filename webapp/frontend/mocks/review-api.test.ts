import assert from "node:assert/strict";
import test from "node:test";

import { apiScenario, handleReviewApiRequest } from "./review-scenario";

test("review API preserves visible onboarding, provider, and KovaaK state without retaining secrets", () => {
  const scenario = apiScenario({ productState: { ...apiScenario().productState, onboarding_completed: false, onboarding_completion_kind: null } });
  const onboarding = handleReviewApiRequest(scenario, { method: "POST", path: "/api/product-state/onboarding", body: { completion_kind: "connected" } });
  assert.equal(onboarding.status, 200);
  assert.equal(scenario.productState.onboarding_completed, true);

  const created = handleReviewApiRequest(scenario, { method: "POST", path: "/api/provider-profiles", body: { name: "Review provider", kind: "custom_openai_compatible", base_url: "https://example.invalid", model_id: "review-model", api_key: "must-not-be-stored" } });
  assert.equal(created.status, 200);
  assert.equal((created.body as { api_key?: string }).api_key, undefined);
  assert.equal(scenario.profiles.profiles.at(-1)?.credential_configured, true);

  const connected = handleReviewApiRequest(scenario, { method: "PUT", path: "/api/kovaak-connection" });
  assert.deepEqual(connected.body, { connected: true });
  assert.equal(scenario.kovaakConnected, true);
});

test("review API rejects unknown routes explicitly", () => {
  const result = handleReviewApiRequest(apiScenario(), { method: "GET", path: "/api/not-a-product-route" });
  assert.equal(result.status, 501);
});

test("default analysis fixture is one internally consistent real static-clicking projection", () => {
  const scenario = apiScenario();
  const result = scenario.analysis.result;
  assert.equal(result?.schema_version, "analysis_result.v2");
  if (!result || result.schema_version !== "analysis_result.v2") return;

  assert.equal(result.input_mode, "input_native");
  assert.equal(result.input_snapshot.scenario_resolution?.aim_family, "static_clicking");
  assert.equal(result.deterministic.support_status, "partial");
  const summary = result.deterministic.diagnosis?.summary;
  assert.deepEqual(Object.keys(summary ?? {}).sort(), [
    "decel_frac",
    "path_efficiency",
    "peak_position_pct",
    "reverse_ratio",
    "sparc",
    "submovement_overlap",
  ]);
  assert.deepEqual(summary?.path_efficiency, { med: 0.9621542770432387, metric_version: "native_flicking.v1" });
  assert.deepEqual(summary?.sparc, { med: -4.177432518826556, metric_version: "native_flicking.sparc.v2" });
  assert.equal(Object.keys(result.deterministic.metrics ?? {}).length, 23);
  assert.equal(scenario.analysisData.event_distribution[0]?.kind, "static_flick");
  assert.equal(scenario.analysisData.event_distribution[0]?.count, 123);
  assert.equal(scenario.analysisData.target_relative_error_radius.availability, "unavailable");
  assert.equal(scenario.analysisFamilyData.family, "flicking");
  assert.equal(scenario.analysisFamilyData.rows.length, 123);
  assert.equal(scenario.evidenceSegments.video_availability, "unavailable");
});

test("review API paginates the full real Flick projection like the backend", () => {
  const scenario = apiScenario();
  const first = handleReviewApiRequest(scenario, {
    method: "GET",
    path: "/api/sessions/42/analysis-data/family",
    query: { limit: "50", offset: "0" },
  }).body as typeof scenario.analysisFamilyData;
  const last = handleReviewApiRequest(scenario, {
    method: "GET",
    path: "/api/sessions/42/analysis-data/family",
    query: { limit: "50", offset: "100" },
  }).body as typeof scenario.analysisFamilyData;

  assert.equal(first.total_count, 123);
  assert.equal(first.rows.length, 50);
  assert.equal(first.next_offset, 50);
  assert.equal(last.rows.length, 23);
  assert.equal(last.next_offset, null);
});

test("review API attaches analysis context and is idempotent", () => {
  const scenario = apiScenario({
    coachContexts: { schema_version: "coach_context_list.v1", contexts: [] },
  });
  const request = {
    method: "POST",
    path: "/api/coach/context/attach",
    body: { schema_version: "coach_context_attach.v1", kind: "analysis", analysis_ref: "analysis:42" },
  } as const;
  const attached = handleReviewApiRequest(scenario, request);
  assert.equal(attached.status, 200);
  assert.equal((attached.body as { action: string }).action, "attached");
  assert.equal(scenario.coachContexts.contexts.length, 1);

  const duplicate = handleReviewApiRequest(scenario, request);
  assert.equal(duplicate.status, 200);
  assert.equal((duplicate.body as { action: string }).action, "already_attached");
  assert.equal(scenario.coachContexts.contexts.length, 1);
});
