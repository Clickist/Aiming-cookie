# Target Switching Coach Connection v1

> **Status: completed on 2026-07-29.** This Task connects the reviewed Target Switching result to the existing History comparison, Knowledge Registry and TeachingSession plan compiler. It does not add a second analyzer, registry, teaching state machine or plan store.

## Task 1 - Connect matched Switching evidence to prescribed practice

### Allowed files

- `docs/superpowers/plans/2026-07-29-target-switching-coach-connection-v1.md`
- `docs/superpowers/plans/README.md`
- `webapp/backend/history_trends.py`
- `knowledge/coach/registry.v3.json`
- `kovaak_tracker/advice_target_switching.py`
- `tests/coach/test_advice_target_switching.py`
- `tests/coach/test_knowledge_registry.py`
- `tests/coach/test_target_switching_teaching_connection.py`
- `webapp/tests/test_target_switching_coach_connection.py`
- `webapp/coach-runtime/test/knowledge-registry.test.ts`

No other file is allowed. In particular, do not modify `DataView.tsx` or any frontend, Scenario Registry/Manifest, analyzer/advice/visual/association files, `worker.py`, existing TeachingSession or Training Plan runtime/store files, DB/routes/schemas, `PROGRESS.md`, real Run/media/Provider data or the field database.

### Tests first

1. Prove the existing comparison accepts a fully covered deterministic `target_switching.transition_time_ms` or `target_switching.settle_duration_ms` metric from an accepted Stats-kill-bounded chain even when the result-level video target coverage is below `1.0`. Other families, partial metrics, other Switching conditions and unaligned evidence retain the existing strict rejection.
2. Prove the existing Switching baseline selector skips repeated Analyses of the same `kovaak_run_ref` and selects the first independently recorded comparable Run.
3. Prove `switching.transition-and-arrival` alone binds `scenario:switching.beants_larger@1`, declares both real issue metric refs and compiles either grounded issue into the existing complete 11-field Training Plan item. `switching.selection-observable-only` remains `not_applicable`.
4. Prove the existing advice accepts only `row_kind=switch_chain` with the activated `stats_bounded_switch_chain` classification. Other new classifications, non-chain rows, unclassified acquisitions, no matched baseline, improvement or equality produce no candidate.
5. Prove no issue, no active plan, a mismatched scenario, a non-deterministic metric or a current metric that is not worse than its matched baseline still produces no training item or fabricated problem.
6. Run Python and TypeScript Registry validation plus the affected History, Coach context, TeachingSession and Training Plan regressions without touching real data.

### Minimal implementation

1. Reuse `compare_analysis_results()` as the sole comparability authority. Recognize metric-scoped completeness only for a supported Target Switching result whose deterministic metric has full coverage and the exact `condition:target_switching:stats_kill_bounded_chain` condition. Keep result-level coverage unchanged for every other consumer.
2. In `build_matched_target_switching_baseline()`, skip a baseline only when both results expose the same non-empty `kovaak_run_ref`; preserve historical fixtures without Run refs.
3. Extend the existing advice row predicate to accept `stats_bounded_switch_chain` only when its existing `row_kind` is `switch_chain`. Preserve the old accepted classifications and reject all partial/unclassified rows.
4. Extend the existing v3 `switching.transition-and-arrival` entry with the two analyzer metric refs and the exact active Beants Larger scenario prescription. Reuse the existing cue, dose, matched retest, near-transfer retest and review condition.
5. Reuse `_compile_prepared_plan_item`, the current active Scenario Registry/Manifest lookup, TeachingTurn exact payload check and existing Training Plan store. Do not add family routing or translation tables.

### Frozen decisions

- `Analysis 25` becoming comparable does not imply it has a problem. If it is better than the independent matched baseline, existing advice returns no negative issue and Coach must not manufacture one.
- Result-level target/crosshair coverage remains visible and unchanged. Only the two fully covered Stats-bounded Switching metrics can use metric-scoped completeness for comparison.
- Same-Run re-analysis is not an independent baseline.
- `switching.selection-observable-only` cannot prescribe practice because the active scenario has no expected-target rule. Selection, first-shot, first-damage, persistent identity and re-entry claims remain unavailable.
- A Stats-bounded chain supports only the recorded post-kill transition and arrival timing/path facts. It does not identify the prior target or prove selection intent or correctness.
- No static threshold, universal dose, new scenario, hardware cause or unsolicited device recommendation is introduced.
- Real Training Plan rows are never written by validation. An in-memory active plan ref may exercise the existing compiler.

### Stop rule

- The exact Switching profile is no longer active in both Scenario Registry and Launch Manifest, or its hash/analyzer identity changes.
- Safe completion requires `worker.py`, analyzer/advice/visual/association code, frontend, runtime/store/schema changes or writes to real user data.
- Metric-scoped comparison cannot be bounded to the exact supported Stats-kill condition and full deterministic metric coverage.
- Advice would need to accept a classification other than the reviewed `stats_bounded_switch_chain`, accept a non-`switch_chain` row, or infer selection, prior-target identity, first-shot or damage semantics.
- One issue resolves to multiple knowledge entries/metrics, or selection becomes prescribable without an observable rule.
- Existing comparability, owner scope, confirmation/idempotency, TeachingSession, Training Plan or Registry parity tests regress.

### Completion evidence

- The implementation reuses the existing History comparison, Target Switching advice, Knowledge Registry v3 and 11-field Training Plan compiler. No parallel analyzer, registry, teaching state machine, plan store or frontend route was added.
- Scoped Python regression: `199 passed`.
- Complete Coach runtime regression with pinned Pi source: `156 passed`.
- Python `compileall`, Registry v3 JSON/load parity, `AGENTS.md`/`CLAUDE.md` byte parity and scoped `git diff --check` passed.
- Validation used an isolated temporary database/root and did not read or write real Run, Provider, media, Training Plan or field-database data.
