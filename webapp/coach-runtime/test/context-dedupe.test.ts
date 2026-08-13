import assert from "node:assert/strict";
import test from "node:test";

import {
  canonicalizeCoachContextDescriptor,
  hashCoachContextDescriptor,
  type CoachContextDedupeDescriptor,
} from "../src/context-dedupe.ts";

const PYTHON_FIXTURES: Array<{
  descriptor: CoachContextDedupeDescriptor;
  canonical: string;
  sha256: string;
}> = [
  {
    descriptor: {
      kind: "analysis",
      analysis_ref: "analysis:42",
      target_ref: "analysis:42",
      start_ms: null,
      end_ms: null,
      comparison_analysis_ref: null,
    },
    canonical: "{\"analysis_ref\":\"analysis:42\",\"comparison_analysis_ref\":null,\"end_ms\":null,\"kind\":\"analysis\",\"start_ms\":null,\"target_ref\":\"analysis:42\"}",
    sha256: "76bb38cb4cbda056adddc623025c94f4ffd774a0d3c2bb151f4cefb947e08a72",
  },
  {
    descriptor: {
      kind: "time_range",
      analysis_ref: "analysis:42",
      target_ref: "segment:focus",
      start_ms: 1250,
      end_ms: 2500.5,
      comparison_analysis_ref: null,
    },
    canonical: "{\"analysis_ref\":\"analysis:42\",\"comparison_analysis_ref\":null,\"end_ms\":2500.5,\"kind\":\"time_range\",\"start_ms\":1250.0,\"target_ref\":\"segment:focus\"}",
    sha256: "64a634dcd770fe2bdfe9d048e278cc2ada5cd776467d000887c601594d487925",
  },
  {
    descriptor: {
      kind: "comparison",
      analysis_ref: "analysis:42",
      target_ref: "analysis:42",
      start_ms: null,
      end_ms: null,
      comparison_analysis_ref: "analysis:43",
    },
    canonical: "{\"analysis_ref\":\"analysis:42\",\"comparison_analysis_ref\":\"analysis:43\",\"end_ms\":null,\"kind\":\"comparison\",\"start_ms\":null,\"target_ref\":\"analysis:42\"}",
    sha256: "c2293082fcbe59094fac0a4335d1cd66bd7b7a84a12879073d5ff7467925e20f",
  },
  {
    descriptor: {
      kind: "time_range",
      analysis_ref: "analysis:42",
      target_ref: "segment:tiny",
      start_ms: 1e-6,
      end_ms: 0.0001,
      comparison_analysis_ref: null,
    },
    canonical: "{\"analysis_ref\":\"analysis:42\",\"comparison_analysis_ref\":null,\"end_ms\":0.0001,\"kind\":\"time_range\",\"start_ms\":1e-06,\"target_ref\":\"segment:tiny\"}",
    sha256: "0fd36c6c69bd56d692422515ff89ddfc173177e47d3f83bbee7a2dd929fa778d",
  },
  {
    descriptor: {
      kind: "time_range",
      analysis_ref: "analysis:42",
      target_ref: "segment:large",
      start_ms: -0,
      end_ms: 1e16,
      comparison_analysis_ref: null,
    },
    canonical: "{\"analysis_ref\":\"analysis:42\",\"comparison_analysis_ref\":null,\"end_ms\":1e+16,\"kind\":\"time_range\",\"start_ms\":-0.0,\"target_ref\":\"segment:large\"}",
    sha256: "099f0e024c3fee80de9ef6fd6d6464d32ce67a24856b59b2cfa73d2d87f061eb",
  },
];

test("Coach context dedupe descriptors match Python canonical JSON and SHA-256", () => {
  for (const fixture of PYTHON_FIXTURES) {
    assert.equal(canonicalizeCoachContextDescriptor(fixture.descriptor), fixture.canonical);
    assert.equal(hashCoachContextDescriptor(fixture.descriptor), fixture.sha256);
  }
});
