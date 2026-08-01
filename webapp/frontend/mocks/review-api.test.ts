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
