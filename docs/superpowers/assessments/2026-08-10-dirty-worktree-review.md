# 2026-08-10 Dirty Worktree Review

## Scope and boundaries

- Repository: `C:\Users\袜子\Desktop\Aiming-cookie`
- Branch: `main`
- Review mode: user-authorized review, remediation, batched commits, and push
- Preserve all pre-existing user changes; no reset, checkout, broad cleanup, or overwrite
- Keep unknown research/materials, raw/private media, credentials, `.firecrawl/**`, and `artifacts/**` out of product commits unless explicitly classified and required
- Automated validation is reported separately from real Provider, Tauri/KovaaK, hardware, and release gates

## Baseline

- HEAD at review start: `6a5cc35`
- Remote relation at review start: `main...origin/main [ahead 23]`
- Tracked modified files: 37
- Tracked deletion: `docs/opendesign-desktop-handoff.md`
- Untracked paths: `2026-08-06-human-aim-coaching-sop.md`, two archive documents, `logo.jfif`, and `viscose-youtube/`

## Review waves

| Wave | Scope | Status | Required evidence |
|---|---|---|---|
| Backend | `webapp/backend/**` and Python tests | complete | file/line finding, focused tests, minimal fix |
| Frontend | `webapp/frontend/**` and frontend tests | complete | file/line finding, type/test result, minimal fix |
| Docs and hygiene | docs, AGENTS/CLAUDE, untracked material, links | complete | source-order check, link/parity result, commit classification |

## Commit candidates

- Product/backend contract changes: reviewed and ready for staged commit
- Frontend/contract-test synchronization: reviewed and ready for staged commit
- Documentation/archive governance: reviewed and ready for staged commit
- Research and media artifacts: pending classification; do not include by default

## Verification checklist

- [x] Independent findings rechecked in the main review
- [x] Focused tests for each confirmed remediation
- [x] Applicable broader test/build suites
- [x] `git diff --check` and staged diff checks
- [x] Changed-Markdown local link validation
- [x] `cmp -s AGENTS.md CLAUDE.md`
- [ ] Final `git status`, local/remote HEAD, and remaining field/release gates reported

## Main-review verification

- Python focused DB/Coach/Run/source/capability suite: `230 passed`.
- Frontend type-check; unit: `34 passed`; contracts: `126 passed`.
- Production build and browser smoke: `14 passed`.
- The root onboarding smoke initially exposed a real routing defect: `AppShell` owns Coach routes and did not mount the page component containing the gate. The gate now lives in `AppShell`; the stale smoke copy was updated to assert the onboarding heading.
- Real Tauri, KovaaK, hardware, Provider, installer/signing/updater/download, and cross-vendor capture gates remain unverified by these automated checks.
