# Automatic Run Finalization v1 Implementation Plan

> **For executor:** REQUIRED SUB-SKILL: use `executing-plans` task-by-task and preserve the existing dirty worktree.

> **Status:** active. On 2026-07-19 点点 gave standing authorization to execute Tasks 1-6 sequentially without pausing between Tasks, including use of `gpt-5.6-terra` subagents. Stop before Task 7 asks for KovaaK operation, or earlier only for a PRD/Architecture conflict, an Allowed-files expansion, or a failed Stop rule.

**Goal:** Connect the proven Windows Raw/WGC hardware replay pipeline to stable Stats/Performance discovery so each eligible Challenge becomes one idempotent, Run-owned, `pending_analysis` Run with recoverable Raw/MP4 evidence.

**Architecture:** Tauri remains the sole owner of process-gated Raw Input, KovaaK HWND capture, capture clocks, encoded replay memory, and MP4 export. The Python sidecar remains the sole owner of Stats/Performance parsing, `time_alignment.v2`, SQLite Run state, readiness, Analysis creation, and storage accounting. A launch-scoped loopback control plane with a separate random secret lets the child Python runtime request a canonical epoch-millisecond replay export without a WebView intermediary; native code derives all managed output paths and maps epoch time to the current capture clock.

**Tech stack:** Rust/Tauri, Windows Raw Input, WGC/D3D11/Media Foundation, Python/FastAPI, SQLite/aiosqlite, ACRI v1, `time_alignment.v2`.

**Upstream contracts:**

- [`../../PRD.md`](../../PRD.md)
- [`../../ARCHITECTURE.md`](../../ARCHITECTURE.md)
- [`../specs/2026-07-17-automatic-run-capture-design.md`](../specs/2026-07-17-automatic-run-capture-design.md)
- [`../specs/2026-07-13-kovaak-run-trace-lifecycle-design.md`](../specs/2026-07-13-kovaak-run-trace-lifecycle-design.md)

---

## Frozen decisions

- The control plane is a bounded newline-delimited JSON protocol on `127.0.0.1:0`, not a renderer/WebView bridge and not a public API. Tauri creates a fresh secret and passes address + secret only to its child Python runtime.
- The control request accepts `runId`, a strict request id, capture session id, and canonical `[startEpochMs, endEpochMs)`; it never accepts an arbitrary output path. Native code derives `DATA_ROOT/runs/<run-id>/video-<request-id>.mp4` and its receipt sidecar after containment checks.
- Tauri maps epoch milliseconds to replay PTS using the capture session's own `CaptureClockMetadata`. Python must not duplicate QPC/WGC clock arithmetic or invent another alignment authority.
- A successful native export atomically publishes the MP4 and a JSON receipt containing the request digest, capture session/clock, requested window, replay receipt, and file fingerprint. Matching repeated requests return the existing verified artifact; conflicts fail closed.
- Python source revision identity plus the canonical window is the finalization idempotency authority. A duplicate watcher observation cannot create another Run or overwrite attached evidence.
- `Pause Count > 0`, a window over 300 seconds, capture-session mismatch, or encoded-video coverage gap prevents a permanent MP4 and any combined Raw/video alignment claim. Raw gaps invalidate Raw claims only; hardware/video failure invalidates video only. Independently verified evidence is retained, and the Run is `pending_analysis` or `incomplete_evidence` strictly from `Stats AND (MP4 OR (Raw + Performance))` plus the selected mode's own alignment requirements.
- Run-owned Raw and MP4 live only under the managed Run directory. Stats/Performance remain user-owned and are never moved, rewritten, or deleted.
- Finalization and MP4 muxing never stop or clear the live replay producer. One capture session may finalize multiple Challenges independently.
- Automatic finalization never creates an Analysis. Readiness is `Stats AND (MP4 OR (Raw + Performance))`; a ready Run with no Analysis is exposed as `pending_analysis`.
- Default automatic capture remains disabled until an explicit native setting/command enables it. This plan builds the capability but does not invent the future Settings UI.
- No runtime FFmpeg, CPU video fallback, vendor SDK, desktop-wide capture, automatic evidence TTL, or silent cleanup is introduced.
- No commit or push is part of this plan unless 点点 separately requests it.

## Task 1: Native Capture Coordinator and private control plane

**Allowed files:**

- Create: `webapp/frontend/src-tauri/src/capture_coordinator.rs`
- Modify: `webapp/frontend/src-tauri/src/lib.rs`
- Modify: `webapp/frontend/src-tauri/src/runtime.rs`
- Modify: `webapp/frontend/src-tauri/src/window_capture.rs`
- Tests: inline Rust tests in the files above

**Tests first:**

- coordinator defaults to `disabled`, requires explicit enable, waits for a KovaaK process/window, keeps Raw enabled when video degrades, and retains the replay session while process exit is finalizing;
- loopback bind is mandatory, each launch secret is fresh, wrong/missing secret and oversized/malformed messages are rejected, and responses never expose absolute paths;
- epoch `[start_ms,end_ms)` maps to the active capture clock with checked arithmetic and rejects capture-session mismatch, invalid/over-300-second windows, unavailable clocks, and PTS overflow;
- managed artifact paths reject traversal and are always contained under `DATA_ROOT/runs/<run-id>/`;
- a replay export writes MP4 + capture receipt sidecar atomically; a matching repeated request is idempotent, while an existing artifact with a different request digest fails closed;
- export completion does not stop capture and the bounded native command queue remains non-blocking for the producer.

**Implementation:**

Add a native `CaptureCoordinatorState` that owns shared Raw/window-capture handles, a process/HWND monitor, explicit enable state, capture session identity, typed status, and a bounded loopback control server. Start it before `RuntimeProcess`, pass only its ephemeral address/secret into the child environment, and shut it down before dropping capture handles. Add a checked epoch-to-replay-PTS boundary and persist a strict receipt sidecar next to each atomically published MP4.

**Verification:**

- `cargo fmt --check`;
- MSVC `cargo check --locked --all-targets`;
- focused coordinator/window-capture tests;
- focused `cargo clippy --locked --all-targets -- -D warnings`.

**Stop rule:** stop before implementation if the bridge requires a visible frontend, a public/listening-non-loopback endpoint, arbitrary caller paths, a second time-alignment authority, stopping the replay producer, or files outside Allowed files.

## Task 2: Run-owned evidence state and atomic persistence

**Allowed files:**

- Modify: `webapp/backend/db.py`
- Modify: `webapp/backend/kovaak_run_store.py`
- Tests: `webapp/tests/test_db.py`
- Tests: `webapp/tests/test_kovaak_runs.py`

**Tests first:**

- a v13 database migrates forward without changing existing Run/session rows;
- Run rows can express canonical window/capture session, alignment state, finalization state, video pending/attached/unavailable state, internal managed paths, typed errors, and path-free video summaries;
- begin/attach/unavailable transitions require the expected pending path so a stale finalizer cannot overwrite a newer or attached artifact;
- an attached MP4 must have a valid managed path, matching sidecar request digest, immutable fingerprint, and canonical receipt;
- repeated source revisions and repeated successful attachment preserve the same Run/artifact identity;
- startup reconciliation attaches a verified pending MP4, keeps a missing pending artifact retryable, quarantines conflicting app-created partials, and never deletes user Stats/Performance;
- readiness stays derived from evidence and never queues an Analysis.

**Implementation:**

Advance SQLite to v14 with the minimum `kovaak_runs` columns for capture/window/alignment/finalization/video lifecycle. Mirror the existing Raw pending-attach transaction: DB pending first, native atomic artifact publication second, validation/fingerprint third, DB attach last. Extend startup reconciliation to MP4 + receipt sidecars and quarantine ambiguous managed artifacts rather than deleting them.

**Verification:** focused DB/Run tests, migration tests, compileall, and `git diff --check`.

**Stop rule:** stop if migration cannot preserve existing rows, if correctness requires moving/deleting user source files, if an artifact can be published outside the managed Run root, or if a stale writer can overwrite newer evidence.

## Task 3: Python native client and idempotent Run Finalizer

**Allowed files:**

- Create: `webapp/backend/native_capture_client.py`
- Create: `webapp/backend/kovaak_capture_finalizer.py`
- Modify: `webapp/backend/config.py`
- Modify: `webapp/backend/desktop_runtime.py`
- Modify: `webapp/backend/kovaak_run_store.py`
- Tests: create `webapp/tests/test_native_capture_client.py`
- Tests: create `webapp/tests/test_kovaak_capture_finalizer.py`
- Tests: `webapp/tests/test_desktop_runtime.py`
- Tests: `webapp/tests/test_kovaak_runs.py`

**Tests first:**

- the stdlib client enforces loopback, launch secret, request/response size limits, connect/read timeouts, strict schemas, and typed retryable vs terminal failures;
- Stats-first and Performance-first observations converge on one source-key Run; the same revision is a no-op and a conflicting revision remains `pairing_conflict`;
- normal and timescale-only pairs resolve one canonical window and request one native export;
- pause, over-300-second, capture-session mismatch, and encoded-video coverage-gap responses produce explicit video-unavailable evidence without permanent MP4/canonical video claims; hardware unavailable degrades video only, and a Raw gap degrades Raw only;
- Raw and video degrade independently while readiness follows `Stats AND (MP4 OR (Raw + Performance))`;
- a transient control failure leaves recoverable pending state and releases the watcher revision for retry; a terminal coverage failure is recorded once without a hot retry loop;
- repeated finalization after response loss reuses the verified sidecar/artifact and cannot duplicate the Run.

**Implementation:**

Create a strict private client and a single `KovaaKCaptureFinalizer` orchestration boundary. Refactor Desktop discovery so one coroutine parses/fingerprints stable sources, resolves the existing `time_alignment.v2` window, slices Raw through the existing ACRI helper, requests native replay export, validates the receipt/artifact, and commits evidence state idempotently. Do not reparse in a second authority or let the native side inspect Stats/Performance.

**Verification:** focused ingestion/finalizer/client/runtime tests, compileall, and `git diff --check`.

**Stop rule:** stop if the same source revision can create multiple Runs, if a window cannot be bound to a capture session, if pause evidence reaches native export, if retry can overwrite verified evidence, or if source/internal paths cross a public API/log boundary.

## Task 4: Readiness, automatic MP4 Analysis input, and public Run DTOs

**Allowed files:**

- Modify: `webapp/backend/kovaak_run_store.py`
- Modify: `webapp/backend/coach_commands.py`
- Modify: `webapp/backend/routes.py`
- Modify: `webapp/backend/schemas.py`
- Tests: `webapp/tests/test_kovaak_runs.py`
- Tests: `webapp/tests/test_routes.py`
- Tests: `webapp/tests/test_history.py`
- Tests: `webapp/tests/test_coach_commands.py`

**Tests first:**

- public Run DTOs expose finalization/readiness, evidence refs, availability, alignment/coverage summaries, and typed limitations but no path, token, Raw samples, request digest, or private receipt payload;
- a ready Run with no Analysis is `pending_analysis`; multiple ready Runs remain separate; creating one Analysis does not mutate or enqueue the others;
- `input_native` uses Stats+Performance+Raw, `multimodal` additionally uses the attached Run-owned MP4, and video-only automatic degradation can use Stats+MP4 without a user-supplied path;
- automatic Run-owned MP4 is frozen by fingerprint but is not copied into or owned by the Analysis workspace; terminal Analysis deletion cannot remove it;
- incomplete evidence cannot start an unsupported mode and returns a stable reason;
- legacy manual `MP4 + Stats` fallback keeps its existing explicit-source behavior.

**Implementation:**

Extend the existing analysis input snapshot with a stable `video` artifact, derive supported modes/readiness from current evidence, and teach the shared Analysis creation handler to consume attached Run-owned MP4 when present. Keep all absolute paths inside the DB-private snapshot and preserve existing manual fallback semantics.

**Verification:** focused Run/route/history/Coach tests, compileall, and `git diff --check`.

**Stop rule:** stop if Analysis deletion can own/delete Run evidence, if selecting one Run changes another, if readiness diverges from the frozen formula, or if any public DTO exposes private capture fields.

## Task 5: Storage accounting and explicit Run-evidence removal

**Allowed files:**

- Modify: `webapp/backend/db.py`
- Modify: `webapp/backend/kovaak_run_store.py`
- Modify: `webapp/backend/routes.py`
- Modify: `webapp/backend/schemas.py`
- Modify: `webapp/backend/workspace.py`
- Tests: `webapp/tests/test_db.py`
- Tests: `webapp/tests/test_kovaak_runs.py`
- Tests: `webapp/tests/test_routes.py`
- Tests: `webapp/tests/test_workspace.py`

**Tests first:**

- storage totals classify Run MP4, Run Raw, Analysis artifacts, and incomplete/recovery data without counting user-owned Stats/Performance;
- only an authenticated Desktop request can remove one explicit Run-owned MP4 or Raw artifact; Run metadata, Analysis rows, other evidence, and user sources remain;
- removal is commit-first with a recoverable tombstone, validates managed containment, and is idempotent;
- deletion failure keeps the tombstone and marks the artifact unavailable without lying that bytes were reclaimed; startup reconciliation retries only the recorded managed artifact;
- public removal responses explain affected modes/refs and expose no path;
- no quota, TTL, oldest-first cleanup, or bulk clear endpoint exists.

**Implementation:**

Add the minimum evidence-deletion tombstone migration and reuse the established Analysis deletion/reconciliation pattern for one Run-owned artifact at a time. Extend `/storage` with classified totals and add narrow per-Run evidence removal endpoints; do not implement Run metadata deletion.

**Verification:** focused migration/storage/workspace/API tests, compileall, and `git diff --check`.

**Stop rule:** stop if removal can touch a user source, cascade into Run/Analysis deletion, escape the managed root, or requires an unfrozen bulk/retention policy.

## Task 6: Automated vertical slice and non-interactive Gates

**Allowed files:**

- Tests: files already allowed by Tasks 1-5
- Modify: `docs/PROGRESS.md` only after new evidence exists
- Runtime outputs: temporary validation bundles outside the repository

**Tests first / verification:**

- a fake private native endpoint drives stable Stats/Performance -> canonical normal/timescale Run -> Raw/MP4 attachment -> `pending_analysis` without a WebView;
- two consecutive source pairs produce two Runs and duplicate observations remain idempotent;
- pause, over-300-second, coverage-gap, response-loss recovery, app restart reconciliation, and storage removal branches fail closed with no source deletion/path leak;
- Python focused suites and compileall pass with `KOVAAK_INSTALL_DIR` forced to a nonexistent test path;
- MSVC fmt/check/focused tests/clippy pass;
- `git diff --check` and final status review pass.

**Stop rule:** stop if any automated branch needs real KovaaK behavior to pass, if Windows build/lint fails, or if the implementation changed a PRD/Architecture contract.

## Task 7: Windows product-path field Gate

**Allowed files:**

- Modify: `docs/PROGRESS.md` only after evidence exists
- Runtime outputs: temporary validation bundles outside the repository
- Tests: no business-code changes

**Before starting:** tell 点点 the product-path capture is ready, wait for explicit `开始`, announce capture started, then ask for the exact KovaaK actions.

**Field matrix:**

- one ordinary pause-free Challenge auto-finalizes into one Run-owned Raw/MP4 Run with `pending_analysis` and no manual path input;
- two consecutive Challenges become two independent Runs while capture remains live;
- Restart before the completed attempt finalizes only the persisted completed window;
- pause remains `incomplete_evidence` with no permanent MP4/canonical Raw claim;
- closing/reopening the app after a deliberately interrupted finalization reconciles without duplicate Run/evidence;
- storage accounting reflects the produced evidence; explicit removal affects only the selected Run artifact;
- capture remains 60 FPS hardware-only, Raw priority, bounded to 300 seconds / 384 MiB, with zero unexplained drops on NVIDIA. AMD/Intel remain external release Gates until hardware is available.

**Stop rule:** stop and report unexpected missing coverage in an eligible normal/timescale row, incorrect independent-evidence degradation/readiness, duplicate Run, path leak, source mutation/deletion, unbounded resource growth, CPU video fallback, irrecoverable finalization, or Analysis auto-start.

## Task 8: Product-path field repair

> **Authorization:** On 2026-07-19 点点 authorized this repair after Task 7 field evidence triggered the Stop rule.

**Allowed files:**

- Modify: `webapp/backend/kovaak_capture_finalizer.py`
- Modify: `webapp/backend/kovaak_run_store.py`
- Modify: `webapp/backend/kovaak_ingest.py`
- Tests: `webapp/tests/test_kovaak_capture_finalizer.py`
- Tests: `webapp/tests/test_kovaak_runs.py`
- Tests: `webapp/tests/test_kovaak_ingest.py`
- Docs after new evidence exists: `docs/PROGRESS.md`

**Frozen repair decisions:**

- An encoded-video `capture_coverage_gap` is not renamed or presented as pause evidence. It makes the automatic Run `incomplete_evidence`: no permanent MP4 and no canonical Raw claim remain. Other video-only failures retain the existing independent-evidence degradation behavior.
- A timer-only profile whose `bot_max_lives` entries are all zero is not event-terminated. Only a positive bot life limit, positive kill limit, or positive damage limit permits Stats event termination to override the timer profile.
- A stable Stats-only or Performance-only source revision is consumed once and remains `waiting_for_sources`; it is not a transient failure and cannot hot-loop retry/upsert. Arrival of the counterpart revision must still merge into the same Run and finalize once. Truly transient source/control/trace failures remain retryable.

**Tests first:**

- replace the old coverage-gap expectation with `incomplete_evidence`, no supported input mode, no attached Raw/MP4, no Analysis, managed Raw cleanup through the existing recoverable evidence lifecycle, idempotent duplicate observation, and unchanged user Stats/Performance;
- prove `bot_max_lives=[0, ...]` uses the full normal/timescale timer window while a positive bot life/kill/damage limit still uses the trusted terminal event;
- prove stable missing-source discovery is emitted and persisted once without repeated callback/upsert/SQLite sequence growth, then finalizes exactly once when the counterpart arrives; keep genuine retryable failures retryable.

**Verification:** focused tests for the three files above with `KOVAAK_INSTALL_DIR` forced to a nonexistent path, adjacent finalizer/readiness regressions, Python compileall, scoped `git diff --check`, and final status review.

**Stop rule:** stop if the repair needs schema/native/routes changes, deletes or mutates user Stats/Performance, labels a generic coverage gap as pause, degrades Raw for unrelated video failures, breaks counterpart arrival/retry recovery, or expands beyond the Allowed files.

## Task 9: Raw snapshot ordered-barrier repair

> **Authorization:** On 2026-07-20 点点 authorized the complete repair scope and independent execution through automated Gates, stopping only before the next required KovaaK field action or for a PRD/Architecture conflict.

**Allowed files:**

- Modify: `webapp/frontend/src-tauri/src/raw_input.rs`
- Modify: `webapp/frontend/src-tauri/src/capture_coordinator.rs`
- Modify: `webapp/backend/native_capture_client.py`
- Modify: `webapp/backend/kovaak_capture_finalizer.py`
- Modify: `webapp/backend/kovaak_run_store.py`
- Tests: inline Rust tests in the native files above
- Tests: `webapp/tests/test_native_capture_client.py`
- Tests: `webapp/tests/test_kovaak_capture_finalizer.py`
- Tests: `webapp/tests/test_kovaak_runs.py`
- Docs after new evidence exists: `docs/PROGRESS.md`
- Runtime outputs: temporary validation bundles outside the repository

**Frozen decisions:**

- Do not infer snapshot coverage from the last Raw point/button event: a valid player may be idle at the canonical tail.
- Do not use NTFS mtime as the correctness authority, increase continuous full-snapshot write frequency, or change ACRI v1 for this repair.
- The Raw capture thread is the ordering authority. It records a `coveredThroughEpochMs` barrier before draining the current Windows input queue, then enqueues the barrier after those Raw messages on the existing single-producer snapshot channel.
- The snapshot worker acknowledges only after atomically publishing the snapshot that follows that barrier. The request and response are bound to the current capture session and never carry a filesystem path.
- Barrier/control queues remain bounded and non-blocking on the Raw capture thread. Busy, unavailable, timeout, or stale coverage stays retryable; no point is synthesized and Raw remains independent from unrelated video failure.

**Tests first:**

- a non-empty but pre-barrier snapshot cannot attach in the automatic path and remains `trace_waiting_snapshot` without a managed trace;
- a barrier covering the canonical end attaches the same snapshot window even when its last real Raw point is well before the end, proving idle-tail support;
- a barrier below the canonical end remains retryable, while the same stale condition after retention becomes unavailable without mutating Stats/Performance;
- the Raw capture thread orders accepted input before the barrier, the snapshot worker writes even when the ring is clean, and an unavailable/full control path returns a bounded retryable error instead of blocking or dropping through the barrier path;
- the private protocol rejects unknown fields/paths, binds the request to the current capture session, validates the exact coverage response, and maps native busy/timeout/failure to retryable errors;
- finalization flushes only a trace-pending complete source pair, does not flush pause/missing-source/already-attached duplicates, attaches Raw after the acknowledged barrier, does not duplicate MP4 export/Run identity on retry, and preserves Raw when only video is unavailable.

**Implementation:**

Add a bounded Raw control request consumed by the Raw Input thread. The thread establishes the capture-clock barrier, drains already queued Windows input, and sends a barrier message on its existing point/snapshot channel. The snapshot worker atomically publishes ACRI v1 and replies with the covered-through time only after publication succeeds. Extend the launch-scoped loopback protocol and strict Python client with `flushRawSnapshot`; the coordinator validates capture-session ownership. Refactor the existing Run trace-attachment block only as needed so the finalizer can persist/parse sources once, request the barrier for a pending normal/timescale trace, then attach that exact canonical window only when `coveredThroughEpochMs >= window_end_epoch_ms`.

**Verification:**

- focused Python client/finalizer/Run tests with `KOVAAK_INSTALL_DIR` forced to a nonexistent path;
- Python compileall and adjacent ingestion/readiness regressions;
- MSVC `cargo fmt --check`, `cargo check --locked --all-targets`, focused Raw/coordinator tests, and focused `cargo clippy --locked --all-targets -- -D warnings`;
- non-interactive native barrier smoke where possible, scoped `git diff --check`, and final status review;
- stop and tell 点点 capture is ready before any real KovaaK Challenge.

**Stop rule:** stop if an ordered barrier cannot be established without blocking the Raw capture thread, if the acknowledgement cannot be bound to the current capture session and the atomically published snapshot, if ACRI/schema/DB/routes/PRD/Architecture changes become necessary, if unrelated video failure degrades valid Raw, or if retry can duplicate a Run/export or mutate user Stats/Performance.

## Out of scope

- Formal Settings/New Analysis/History/Storage frontend implementation and tray/floating visuals;
- AMD/Intel field procurement;
- audio, desktop capture, HEVC/AV1, cloud sync, Run metadata deletion, automatic retention/cleanup, or product accounts;
- changing paused-Challenge fail-closed semantics without a new PRD decision.
