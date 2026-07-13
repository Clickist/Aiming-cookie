# Desktop Local-First Vertical Slice

> **Status:** approved for execution on 2026-07-12  
> **Owner / gatekeeper:** Sol  
> **Executors:** Terra subagents, one frozen Task at a time

## Goal and invariants

Build the first Tauri 2 local-first desktop vertical slice: native MP4/CSV selection, managed App Data copies, local Python analysis, Report/video replay, optional Coach, History, storage accounting, and governed light/dark themes.

Frozen product decisions:

- Video, CSV, and CV processing stay local; no server upload, quota, TTL, or automatic cleanup.
- Originals are never modified, moved, or deleted. Managed copies live at `{APP_DATA}/sessions/{session_id}/` and remain until the user deletes the analysis.
- Desktop uses one stable local profile. No login, cloud identity, subscription, or multi-profile work in this slice.
- Tauri owns the loopback Python runtime and worker lifecycle. It uses a dynamic port and a high-entropy per-launch token held only in memory.
- Coach is optional. Its failure must not block analysis, Report, History, video, or storage.
- Windows is the primary eventual platform. The first validation target is macOS, with no installer/signing/updater/release packaging in this slice.
- `DESIGN-cursor.md` is the highest design source. It governs shared foundations and light/dark semantic tokens; `docs/design-system.md` governs implementation; `globals.css` is the executable mapping.
- Do not push. Preserve unrelated working-tree changes.

## Execution protocol

Sol freezes Task, Allowed files, tests-first command, decisions, and stop rule before dispatch. Terra may not invent schema, migrations, retry/deletion/security/retention/theme/product defaults or modify files outside its Task. Parallel work is allowed only for disjoint write sets. Sol reviews diffs and test output; only Sol releases the next dependent Task and creates checkpoint commits.

Stop instead of guessing if implementation requires a schema migration, server upload, changed source-file ownership, token in a URL/log/database/localStorage, asset scope outside managed App Data, an unapproved design value, or expansion into unrelated dirty files.

## Task A1 — Local data configuration and desktop authentication

**Allowed files:** `webapp/backend/config.py`, new focused backend helper modules, and focused tests under `webapp/tests/`.

- Resolve the production desktop data root from system App Data while retaining explicit test/dev override.
- Define one stable desktop local profile.
- Add constant-time validation for `X-Aiming-Cookie-Desktop-Token` on desktop-only routes.
- Keep token out of logs and persistence.

**Tests first:** config override/default behavior and missing/invalid/valid desktop token.

## Task A2 — Path import and disk reserve

**Allowed files:** backend routes/schemas/workspace/queue modules needed by the path-import call and focused tests.

Add:

```http
POST /api/desktop/analyze-paths
X-Aiming-Cookie-Desktop-Token: <launch token>
```

```json
{"video_path":"<absolute path>","csv_path":"<absolute path>","cm_per_360":51.0,"fov":103.0}
```

- Accept only absolute, existing, readable regular files with supported video/CSV extensions.
- Check free space for `video size + CSV size + MIN_FREE_DISK_BYTES` before copy.
- Stream copies into the managed session workspace; never load the whole video into memory.
- On failure remove only the incomplete workspace, never source files.
- Preserve existing multipart behavior.

**Tests first:** success, missing/relative/unreadable/wrong-type paths, insufficient disk, interrupted copy, source preservation, and multipart regression.

## Task A3 — Storage and deletion contract

**Allowed files:** backend queue/routes/schemas/workspace modules and focused tests.

Add:

```http
GET /api/storage
X-Aiming-Cookie-Desktop-Token: <launch token>
```

Return total managed bytes and per-session `session_id`, `status`, `created_at`, and `workspace_bytes`.

Deletion contract:

- Reject `uploading`, `queued`, and `running`.
- Allow `done` and `failed`.
- Verify the resolved workspace is under the managed sessions root.
- Remove the managed workspace before committing DB deletion; report explicit cleanup results.
- Preserve Coach history/profile while marking analysis references deleted.

```json
{"deleted":true,"id":123,"files_removed":["workspace"],"cleanup_failed":[]}
```

**Gate A:** focused tests plus all Python tests pass; originals remain unchanged.  
**Checkpoint:** `feat(desktop-data): add local path import and managed storage`

## Task B1 — Tauri shell and runtime lifecycle

**Allowed files:** new `webapp/frontend/src-tauri/` tree, minimal frontend package/config changes needed to invoke it, runtime entrypoint/helpers, and focused tests.

- Add a minimal Tauri 2 shell and native dialog plugin.
- Tauri generates the per-launch token, starts Python on `127.0.0.1:0`, and parses one stdout readiness line:

```json
{"type":"ready","port":43127}
```

- Tauri exposes base URL/token only through an in-memory command to the current WebView.
- Runtime owns worker startup/shutdown; Tauri terminates the process tree on exit or startup failure.
- No arbitrary shell capability, persistent token, macOS-only shell/path assumption, installer, signing, updater, or bundled Python work.

**Tests first:** readiness parsing, timeout/malformed/early-exit handling, token redaction, fresh token/port, and shutdown cleanup.

**Gate B:** Rust tests, lifecycle Python tests, frontend build, and static Windows path/process review.  
**Checkpoint:** `feat(desktop-runtime): add Tauri shell and local runtime lifecycle`

## Task C1 — Desktop transport and native import

**Allowed files:** frontend API/contracts/import flow and focused frontend tests.

- Browser preview retains relative `/api`; Tauri uses in-memory dynamic base URL.
- Only desktop-only requests receive the launch token.
- Native picker selects MP4 and CSV paths; Desktop calls path import rather than multipart.
- Cancellation creates no session and duplicate submission is blocked.

## Task C2 — Managed local video

**Allowed files:** Tauri asset configuration and existing Report/video integration.

- Use Tauri asset protocol/`convertFileSrc` for managed `video.mp4`.
- Scope access only to `{APP_DATA}/sessions/**/*`; do not expose `$HOME` or arbitrary paths.
- CSP permits only the required local media source.
- Never put the launch token in media URLs or add an anonymous HTTP media endpoint.
- Preserve native video controls, seeking, and `pinned_frame_sec` Coach flow.

## Task C3 — Governed light/dark design system

**Allowed files:** `DESIGN-cursor.md`, `docs/design-system.md`, `webapp/frontend/app/globals.css`, minimal theme/settings components and focused tests. `DESIGN-cursor-dark.md` is input only and ceases to be a parallel authority after merge.

- Rewrite the design source for Aiming Cookie Desktop and include shared foundations plus complete light/dark semantic palettes.
- Both themes use identical semantic token names. Components must not add raw hex or theme-specific token branches.
- Theme options are System/Light/Dark; first run follows system, selection persists locally, System reacts live, and first paint avoids theme flash.
- Sol reviews contrast and light/dark screenshots before release.

## Task C4 — Storage settings and functional integration

**Allowed files:** minimal Settings/storage UI, existing functional pages, and focused tests.

- Display managed total and per-analysis size/status.
- Allow whole-analysis deletion only for done/failed; active entries are disabled.
- Confirmation says only Aiming Cookie's managed copy is removed and originals remain.
- Implement only functional Desktop wiring and approved design-system migration; no unrelated visual or information-architecture redesign.

**Gate C:** native import → managed copy → local analysis → Report → managed video/seek → optional Coach timestamp → storage → delete, with originals unchanged; Light/Dark/System states; frontend tests/build; Tauri tests; full Python/Node regression; `git diff --check`.  
**Checkpoints:**

- `feat(desktop): connect native import and local media playback`
- `feat(frontend): add governed light and dark themes`

## Final delivery

Report Tasks/agents, commit hashes, changed files, validations, unrun checks, deviations, Windows release blockers, final `git status`, and no-push confirmation. Never stage or modify unrelated existing dirty files.
