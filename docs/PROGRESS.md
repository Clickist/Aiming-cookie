# Aiming Cookie Current Progress

> Updated: 2026-08-10. This is a current implementation snapshot, not a product or architecture source. Earlier detailed status is retained in [`archive/history/2026-08-10-progress-prelaunch-history.md`](archive/history/2026-08-10-progress-prelaunch-history.md).

## Current Product Direction

- First activation requires a tested Provider plus enabled Windows Raw Input and KovaaK-window capture. The normal product surfaces are Coach, History, and Settings.
- Coach automatically selects the strongest valid Run tier: `multimodal`, then `input_native`, then `video_fallback`. A Run with no valid tier is not a normal History record; Coach explains the failure and repair path.
- Pre-install KovaaK Stats/Performance files are not imported or displayed. Optional KovaaK score linking remains onboarding context only.

## Implementation Status

- Documentation has been realigned with the above contract; the retired OpenDesign handoff and historical superpowers materials are reference-only.
- Backend automatic tier selection, Run readiness, server-side Analysis tier selection, first-launch routing, and mandatory onboarding UI are implemented and covered by the focused validation below.
- Real Tauri, KovaaK, hardware, Provider, installer/signing/updater/download, and cross-vendor capture validation remain release gates. Automated checks do not close those gates.

## Verification

- Python source-selection, Coach command, DB, Run, and capability suite: `230 passed`.
- Frontend type-check, unit tests: `34 passed`, and contract tests: `126 passed`.
- Production Next build and browser smoke: `14 passed`.
- Changed-Markdown local link scan, `git diff --check`, and `AGENTS.md`/`CLAUDE.md` parity passed.
