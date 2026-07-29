# Viscose S2 Sync and Coach Progression Design

> Status: active. Approved by 点点 on 2026-07-29. This spec narrows v1 Benchmark scope and extends the existing teaching loop; it does not authorize a second Coach, plan, scenario registry or score store.

## 1. User outcome

The learner can enter a 17-digit Steam ID, explicitly agree to a manual refresh, and import the highest scores for all 39 Viscose S2 Easier and 39 Medium scenarios. Coach can see a de-identified score summary, use it to choose what to inspect first, then use Analysis evidence to teach one Easier item. After a confirmed matched retest improves, Coach may recommend the paired Medium item as a stress test and new baseline.

## 2. Single sources of truth

- Existing `benchmark_records` owns score snapshots and external identity consent.
- One versioned S2 course catalog owns Easier/Medium names, taxonomy, pairing and optional reviewed local ScenarioProfile refs.
- Scenario Registry/Launch Manifest remain the only authority for exact local identity and analyzer eligibility.
- Knowledge Registry remains the only prescription source.
- TeachingSession, Training Plan item/execution/retest and confirmation remain the only teaching state and facts.

## 3. Sync contract

- Provider scope is the KovaaK web application endpoint for benchmark IDs `2335` and `2336`; the endpoint is undocumented and therefore treated as unstable.
- Steam ID must be exactly 17 digits and requires `identity_consent=true` on every sync request.
- Both responses must contain exactly the catalog's 39 unique scenario names. Scores are finite, non-negative and converted from the upstream hundredths representation. Scenario and overall rank values are bounded integers.
- Payload completeness and learner completion are different: KovaaK includes all 39 catalog rows, while a scenario with a zero highest score is treated as not yet played for the displayed completion count.
- The complete normalized snapshot is validated before one transaction writes it. A timeout, HTTP error, malformed payload, missing/duplicate/unknown scenario or database error writes nothing from that refresh.
- The response reports counts and timestamps but does not echo Steam ID. Previous successful records remain readable after failure.

## 4. Coach projection

`coach_benchmark_summary.v1` contains only catalog ref/version, observed time, completion, provisional rank state, scenario name, score, scenario rank and bounded review candidates. It never contains Steam ID, leaderboard identity, URLs, paths, raw responses or secrets.

The summary may answer “what have I played?” and prioritize an item for inspection. It cannot itself assert reading, tension, grip, hardware, movement phase or root cause. The attached Analysis and Registry still supply the observation, candidate explanation, cue, dose and retest.

## 5. Easier to Medium progression

- The current exact Easier Analysis produces the existing 11-field Training Plan item.
- The existing confirmation flow records execution and matched retest facts.
- A Medium recommendation is created only from `coach_retest_outcome.v1:improved`. Two-Analysis automation still requires a versioned meaningful-change policy; a user-confirmed single-Analysis outcome remains explicitly user-reported.
- Recommendation text identifies the paired Medium scenario and states that it is a stress test/new baseline, not proven transfer.
- Without an active exact Medium ScenarioProfile, the recommendation cannot create a plan item or claim comparable Analysis. Once exact review exists, the existing item compiler and confirmation path are reused.

## 6. UI boundary

Backend ships first. OpenDesign covers the same Steam ID module in onboarding and Settings plus a compact Easier/Medium score view. v1 has no independent Benchmark route, leaderboard browser, social comparison, background sync or generic provider.

## 7. Verification

- Catalog structure and 39 unique pairs.
- Provider success and every fail-closed payload/network branch.
- Atomic owner-scoped persistence and old-snapshot retention.
- Coach summary redaction and boundedness.
- Low scores do not create diagnoses.
- Only confirmed improved Easier retests create a name-level Medium recommendation; missing exact Medium identity never creates a plan item.
