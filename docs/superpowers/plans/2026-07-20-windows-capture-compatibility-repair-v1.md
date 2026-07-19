# Windows Capture Compatibility and Lifecycle Repair v1 Implementation Plan

> **For executor:** REQUIRED SUB-SKILL: use `executing-plans` task-by-task and preserve the existing dirty worktree.

**Goal:** Make the Windows capture path resilient across supported Windows hardware and lifecycle edges without changing canonical ACRI v1 evidence semantics or allowing video failures to block Raw Input.

**Architecture:** Python owns Run finalization, source discovery, and runtime shutdown; Tauri owns the capture session, Raw Input, WGC, and hardware replay. Raw Input remains the canonical per-record evidence source. The capture path must batch Windows input work where possible, expose typed failures, and degrade video independently when a hardware encoder or adapter cannot be used.

**Tech Stack:** Rust/Tauri, Win32 Raw Input, Windows Graphics Capture, D3D11, Media Foundation hardware H.264, Python asyncio/FastAPI, pytest, MSVC cargo tests.

**Frozen decisions:**

- Do not change ACRI v1 fields, record order, same-millisecond ordering, or canonical Raw semantics. Do not merge same-millisecond records in the canonical trace.
- 1 ms / 1000 Hz is the effective derived analysis bucket, not a claim that every hardware poll is preserved as a 1 kHz sample. Existing canonical Raw records remain available for path and correction metrics.
- Do not apply a global count deadzone, low-pass filter, or silent noise deletion. Noise handling is an assessment/derived-label concern and remains fail-closed when calibration is absent or unreliable.
- Raw Input remains non-blocking and higher priority than video. Video capability/encoder failure may make video unavailable but must not disable valid Raw + Stats/Performance collection.
- A capture session is not released after each Run: one session may contain delayed sources and consecutive Runs. Release is allowed during orderly runtime shutdown for a matching `finalizing` session; automatic post-process-exit release for a still-running app requires a separately frozen quiescence contract.
- No CPU video fallback is introduced. Hardware capability negotiation and an explicit video-degraded state are preferred to a blocking startup failure.

## Task 1: Finalizer and runtime shutdown lifecycle

**Allowed files:**

- Modify: `webapp/backend/kovaak_capture_finalizer.py`
- Modify: `webapp/backend/desktop_runtime.py`
- Tests: `webapp/tests/test_kovaak_capture_finalizer.py`
- Tests: `webapp/tests/test_desktop_runtime.py`
- Docs after evidence: `docs/PROGRESS.md`

**Tests first:**

- Add a finalizing/matching-session shutdown fixture proving `release_capture_session` is called exactly once and does not mutate Run/source/evidence state.
- Add no-op coverage for capturing, degraded, waiting, missing-client, mismatched-session, and protocol/retryable release failures.
- Add runtime shutdown coverage proving ingestion watcher stop precedes finalizer drain/release and DB close; a pending finalizer cannot write after DB close.
- Add a regression that two Runs in one live session are not released when the first Run finalizes.

**Implementation:**

- Track submitted finalizer futures in the Desktop runtime and drain/cancel them deterministically during shutdown.
- Add a finalizer shutdown method that reads native status and best-effort releases only a matching `finalizing` session. Shutdown release errors are diagnostics only and cannot mutate persisted evidence.
- Do not invent a grace timeout or release a live `capturing` session. Stop and report if safe long-running process-exit quiescence requires a new product contract.

**Verification:** focused finalizer/runtime tests, compileall, adjacent desktop runtime tests, scoped `git diff --check`.

**Stop rule:** stop if orderly shutdown cannot prevent finalizer/DB races without schema changes, if release mutates a Run/source, or if supporting automatic release after KovaaK restarts requires an unfrozen grace/quiescence policy.

## Task 2: Raw high-polling throughput and diagnostics

**Allowed files:**

- Modify: `webapp/frontend/src-tauri/src/raw_input.rs`
- Modify: `webapp/frontend/src-tauri/src/capture_coordinator.rs` only for typed status/error propagation if required
- Tests: inline Rust tests in those native files
- Docs after evidence: `docs/PROGRESS.md`

**Tests first:**

- Add deterministic burst tests for 1000 Hz-equivalent and 2000 Hz-equivalent traffic, queue-full behavior, point ordering, same-millisecond records, and barrier acknowledgement after all preceding points publish.
- Add tests proving a batched input read preserves every canonical record and does not block on snapshot/file work.
- Add diagnostics coverage that distinguishes queue drops, snapshot failures, and high-rate backlog; no silent “healthy” state after drops.

**Implementation:**

- Use bounded/batched Windows Raw Input draining where supported instead of doing avoidable per-message allocation work; preserve canonical records and button state order.
- Keep the existing non-blocking `try_send` boundary and make high-rate loss visible to trace quality/finalization rather than hiding it as noise.
- Do not compact canonical records, add sub-millisecond fields, or raise limits until an explicit storage/contract decision exists. A 1000 Hz mouse remains the supported v1 operating target; higher rates must fail or degrade explicitly when coverage is not complete.

**Verification:** MSVC fmt/check, focused native tests, clippy, non-interactive burst smoke where possible, scoped diff check.

**Stop rule:** stop if batching changes ACRI order/fields, blocks the Raw thread, requires analysis/schema changes, or cannot distinguish an actual high-rate coverage loss.

## Task 3: Hardware capability negotiation and video degradation

**Allowed files:**

- Modify: `webapp/frontend/src-tauri/src/window_capture.rs`
- Modify: `webapp/frontend/src-tauri/src/capture_coordinator.rs`
- Tests: inline Rust tests in those native files
- Docs after evidence: `docs/PROGRESS.md`

**Tests first:**

- Add capability fixtures for no hardware MFT, unsupported input format, D3D11-unaware MFT, adapter mismatch, encoder backpressure, and successful hardware H.264 negotiation.
- Prove each video failure keeps Raw independent and exposes a stable degraded reason; no CPU encoder is selected.
- Prove the selected MFT/adapter/input-format diagnostics do not expose filesystem paths or secrets.

**Implementation:**

- Enumerate and validate the selected hardware MFT, D3D11 awareness, adapter identity, input format, and sustained queue contract before entering the live writer path.
- Keep the replay ring bounded and keep hardware/video work isolated from Raw Input.
- Map capability failures to explicit video-unavailable/degraded states while preserving eligible Raw + Stats/Performance readiness.

**Verification:** MSVC fmt/check/clippy, focused native tests, synthetic capability smoke, scoped diff check.

**Stop rule:** stop if compatibility requires CPU fallback, cross-adapter copying without bounded evidence, schema/route changes, or a new product decision about readiness semantics.

## Task 4: Win32/runtime failure classification

**Allowed files:**

- Modify: `webapp/frontend/src-tauri/src/raw_input.rs`
- Modify: `webapp/frontend/src-tauri/src/runtime.rs`
- Modify: `webapp/frontend/src-tauri/src/capture_coordinator.rs` only for typed native status mapping
- Tests: inline Rust tests in those native files
- Docs after evidence: `docs/PROGRESS.md`

**Tests first:**

- Add failure-injection coverage for `CreateToolhelp32Snapshot`, `GetRawInputData`, Raw registration, monitor spawn, and runtime ready-then-exit cleanup.
- Add a regression proving a failed monitor start rolls state back and can be retried.
- Add a shutdown test for `WM_QUIT`/native thread termination without lingering Raw registration.

**Implementation:**

- Separate “KovaaK absent” from Win32/API failure in status and diagnostics.
- Ensure startup failures after readiness still terminate descendants and do not leave ports/DB users behind.
- Roll back coordinator enabled state when monitor startup fails; preserve typed retryable/terminal classification.

**Verification:** MSVC focused tests, clippy, non-interactive process cleanup smoke, scoped diff check.

**Stop rule:** stop if failure classification requires public schema/route changes or cannot be kept path/secret-free.

## Out of scope

- AMD/Intel physical hardware procurement; only capability negotiation and graceful degradation are implemented here.
- Noise filtering/deletion, medical tremor interpretation, or a new canonical compact trace. Noise remains a separate assessment and derived-analysis task.
- Automatic release after KovaaK process exit while the app remains open, until session-quiescence semantics are explicitly frozen.
- Frontend Storage/History UI, Analysis redesign, commits, pushes, resets, or cleanup of pre-existing dirty changes.
