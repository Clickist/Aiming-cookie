# Aiming Cookie Current Progress

> Updated: 2026-08-11. This is a current implementation snapshot, not a product or architecture source. Earlier detailed status is retained in [`archive/history/2026-08-10-progress-prelaunch-history.md`](archive/history/2026-08-10-progress-prelaunch-history.md).

## Current Product Direction

- First activation requires a tested Provider plus enabled Windows Raw Input and KovaaK-window capture. The normal product surfaces are Coach, History, and Settings.
- Coach automatically selects the strongest valid Run tier: `multimodal`, then `input_native`, then `video_fallback`. A Run with no valid tier is not a normal History record; Coach explains the failure and repair path.
- Pre-install KovaaK Stats/Performance files are not imported or displayed. Optional KovaaK score linking remains onboarding context only.

## Implementation Status

- Documentation has been realigned with the above contract; the retired OpenDesign handoff and historical superpowers materials are reference-only.
- Backend automatic tier selection, Run readiness, server-side Analysis tier selection, first-launch routing, and mandatory onboarding UI are implemented and covered by the focused validation below.
- Real Tauri, KovaaK, hardware, Provider, installer/signing/updater/download, and cross-vendor capture validation remain release gates. Automated checks do not close those gates.

## Verification

- Python backend test suite (source-selection, Coach command, DB, Run, capability, capture finalizer, context injection): approximately 400+ passed, including known pre-existing failures.
- Coach runtime TypeScript tests (turn, teaching-policy, product-command-tools, system-prompt-and-tools, fake-stream): approximately 120+ passed.
- Frontend unit, contract, and source tests: approximately 50+ passed.
- Production Next build and browser smoke E2E: passing.
- `AGENTS.md`/`CLAUDE.md` parity and `git diff --check` passed.

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
