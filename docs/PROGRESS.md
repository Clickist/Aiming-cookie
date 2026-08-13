# Aiming Cookie Current Progress

> Updated: 2026-08-13. This is a current implementation snapshot, not a product or architecture source. Earlier detailed status is retained in [`archive/history/2026-08-10-progress-prelaunch-history.md`](archive/history/2026-08-10-progress-prelaunch-history.md).

## Current Product Direction

- First activation requires a tested Provider plus enabled Windows Raw Input and KovaaK-window capture. The normal product surfaces are Coach, History, and Settings.
- Coach automatically selects the strongest valid Run tier: `multimodal`, then `input_native`, then `video_fallback`. A Run with no valid tier is not a normal History record; Coach explains the failure and repair path.
- Pre-install KovaaK Stats/Performance files are not imported or displayed. Optional KovaaK score linking remains onboarding context only.

## Implementation Status

- Documentation has been realigned with the above contract; the retired OpenDesign handoff and historical superpowers materials are reference-only.
- Backend automatic tier selection, Run readiness, server-side Analysis tier selection, first-launch routing, and mandatory onboarding UI are implemented and covered by the focused validation below.
- Desktop Coach requests now use the Node sidecar directly. Python remains the local Analysis API/ingestion/worker runtime; it is not a desktop Coach request proxy.
- Real Tauri Provider, KovaaK field capture, hardware load, packaged installer/signing/updater/download, clean-machine onboarding, and cross-vendor capture validation remain release gates.

## Verification

- Python full suite: 1682 passed, 5 skipped; the focused DB/queue/routes/Coach command suite also passed 231 tests.
- Coach runtime TypeScript suite: 188 passed, 2 skipped; Node native Analysis includes 9 focused tests covering all three input tiers, Python worker v3 snapshot reads, cleanup, and auto-discovered KovaaK path derivation.
- Frontend unit/contracts: 179 passed; frontend type-check passed.
- Rust/Tauri MSVC: fmt, check, clippy passed; tests 92 passed, 7 field-only tests ignored.
- `desktop-coach-provider.spec.ts` now exercises the product UI and observes `/v1/agent-runs`, but the real Provider field test was not rerun in this cleanup. Its skip is not counted as passing validation.
- `git diff --check` passed. Full Python, production browser Playwright, real Tauri, real KovaaK, hardware, and Provider field checks remain to be reported separately if run.

## 2026-08-13 Session Changes

- Made Analysis Session reservation atomic across Python upload/import and Node `analysis.create_from_run`, with failed input setup removing the reservation and workspace.
- Implemented Node-native Run-to-Analysis input freezing with `multimodal > input_native > video_fallback`, canonical v3 snapshots, worker-readable fingerprints, scenario resolution, and auto-discovered KovaaK path reuse.
- Fixed Agent Run retry message reuse, Provider-wait recovery, TeachingSession stale-CAS handling, canonical context projection, Python-compatible context dedupe keys, and duplicate Agent Run readers.
- Kept the desktop Coach product adapter on Node sidecar routes and removed the unused frontend Python soft-start adapter. Browser-only fallback and Python Analysis routes remain compatibility surfaces.
- Hardened frontend sidecar reconnection, stale batch workflows, context clearing, frameless window controls, and SessionRail contract preservation.
- Added product-path Provider E2E coverage without copying credentials from a developer DB; the real Provider field run remains open.

This validation does not prove packaged release readiness. It does not cover installer packaging, signing, updater, clean-machine onboarding, real KovaaK four-source field capture, long-running hardware load, or cross-vendor GPU behavior.

## 2026-08-11 Session Changes

Full-codebase audit completed across 5 subsystems with ~70 findings. Key fixes applied this session:

- **Backend deduplication**: monolithic `worker.py` / `coach_commands.py` / `kovaak_run_store.py` split into focused modules (source validation, family analysis, visual producers, run projection, snapshot codec, context refs, confirmations, guidance, etc.).
- **v0 turn schema cleanup**: unified to a single v1 turn contract path; removed obsolete v0 multi-path branching in `teaching-policy.ts` and `turn.ts`.
- **v3 diagnosis context injection fix**: Coach can now see analysis data (diagnosis, events, metrics) through the evidence bridge; previously the context was assembled but not reaching the LLM turn.
- **Metric localization**: `metric_definitions.py` is now the single source of truth for metric display names (Chinese); frontend and backend both consume it.
- **Coach tool calling fix**: product command tools are now retryable and inject context correctly; `teaching_session.update` is registered as a product command.
- **Video evidence navigation**: added 2-second seek padding so clicking an evidence segment lands slightly before the event, not after.
- **FOV/DPI/sensitivity KeyError tolerance**: missing optional fields no longer crash analysis or frontend rendering.
- **Codex review regression fixes**: schema synchronization between frontend contracts and backend DTOs; deletion confirmation flow restored.
