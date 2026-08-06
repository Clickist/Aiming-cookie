# Raw Input 1000 Hz Canonical Normalization v1 Implementation Plan

> **For executor:** REQUIRED SUB-SKILL: use `executing-plans` task-by-task and preserve the existing dirty worktree.

> **Status:** Completed. Task 1 implementation and automated verification completed on 2026-08-04; 点点于 2026-08-07 确认 Raw Input 已完成实测，且后续数据采集核心通路未改动。

**Goal:** Keep mouse hardware untouched while bounding Aiming Cookie canonical Raw Input motion to 1000 Hz without losing per-millisecond X/Y net displacement or mouse-button edges.

**Architecture:** Tauri continues receiving Windows Raw Input at the device's native reporting rate, but a native 1 ms accumulator runs before the bounded capture queue. It emits at most one non-zero motion record per millisecond plus ordered zero-motion button-edge records. New snapshots use `ACRI v2`; Rust and Python continue reading historical `ACRI v1`, and an existing rolling v1 snapshot is deterministically normalized before being atomically rewritten as v2.

**Tech Stack:** Rust/Tauri, Win32 Raw Input, Python snapshot decoder, pytest, MSVC cargo tests.

**Frozen decisions:**

- Do not configure or claim to configure mouse hardware polling rate.
- Canonical motion is bounded to integer-millisecond buckets and at most 1000 non-zero motion records per second. Empty milliseconds do not create records.
- Sum every report's `dx` and `dy` within its millisecond using checked intermediate arithmetic. This preserves per-millisecond axis net displacement, not sub-millisecond path length or direction changes.
- Preserve left/right/middle press and release edges in receive order as `dx = 0, dy = 0` records. Button-edge records are exempt from the motion-rate cap and share only millisecond-level timing semantics with the bucket motion record.
- Flush the pending bucket before snapshot barriers, KovaaK process-gate closure, capture stop, and orderly shutdown.
- Intentional normalization is not loss and must not increment queue-drop diagnostics. Actual queue overflow or an unrepresentable checked sum remains explicit coverage loss/failure.
- Write new and migrated rolling snapshots as `ACRI v2`; keep finalized `ACRI v1` traces readable and preserve their actual format version in provenance. Never relabel mixed v1/v2 semantics as one format.
- Do not change Analysis metric thresholds, add a user setting, modify frontend UI, or introduce filters/deadzones/low-pass smoothing.

## Task 1: Implement and verify canonical normalization

**Allowed files:**

- Modify: `webapp/frontend/src-tauri/src/raw_input.rs`
- Modify: `webapp/backend/kovaak_run_store.py`
- Tests: inline Rust tests in `webapp/frontend/src-tauri/src/raw_input.rs`
- Tests: `webapp/tests/test_kovaak_runs.py`
- Tests only if current finalizer behavior needs regression coverage: `webapp/tests/test_kovaak_capture_finalizer.py`
- Docs after evidence: `docs/PROGRESS.md`
- Plan closeout after all gates: this file and `docs/superpowers/plans/README.md`, moved/indexed under `docs/archive/completed/plans/`

**Tests first:**

1. Add Rust tests for 125/500/1000 Hz-equivalent streams proving existing samples remain unchanged except for the new v2 format marker.
2. Add a deterministic 8K-equivalent burst covering multiple milliseconds and assert: no more than one non-zero motion record per millisecond, exact per-millisecond `sum(dx)`/`sum(dy)`, no zero-fill records, monotonic timestamps, and no normalization-induced `droppedPoints`.
3. Add same-millisecond direction-reversal coverage proving axis net displacement is retained while sub-millisecond path length is intentionally unavailable.
4. Add left/right/middle press-release fixtures, including multiple edges and movement in one millisecond, proving edge order and final button state are preserved independently of the motion record.
5. Add barrier, process-gate close, capture stop, and shutdown tests proving the pending bucket is flushed before acknowledgement/termination.
6. Add checked-overflow coverage proving an unrepresentable aggregate cannot wrap or saturate into apparently valid evidence.
7. Add Rust and Python codec fixtures proving: v1 remains readable; v2 round-trips; unknown versions fail closed; a rolling v1 snapshot migrates deterministically to v2 without changing per-millisecond X/Y net displacement or button-edge order; provenance reports the actual version.
8. Add finalizer regression coverage proving normalization is not reported as `trace_raw_queue_dropped`, while a real queue loss intersecting the Run window still yields the existing fail-closed result.

**Implementation:**

1. Introduce one small native accumulator owned by the Raw Input thread. It tracks the current integer-millisecond bucket, checked X/Y sums, current button state, and ordered button transitions.
2. On a new millisecond, enqueue the previous non-zero motion aggregate; on a button transition, enqueue a zero-motion state record without consuming the bucket's motion allowance.
3. Centralize pending-bucket flush so barrier/process/stop paths cannot acknowledge coverage before the last aggregate reaches the snapshot worker.
4. Upgrade snapshot writing to `ACRI v2`, decode both v1 and v2 in Rust/Python, and normalize a loaded rolling v1 snapshot before any v2 write.
5. Keep `CAPTURE_QUEUE_CAPACITY`, retention bounds, public Raw status fields, Analysis DTOs, and analyzers unchanged unless a test demonstrates an unavoidable contract mismatch; if so, stop instead of expanding scope.

**Verification:**

- Run focused Python codec/finalizer tests with `KOVAAK_INSTALL_DIR` set to a nonexistent test path.
- Run MSVC `cargo fmt --check`, focused native tests, `cargo check`, and `cargo clippy -- -D warnings` for `webapp/frontend/src-tauri`.
- Run the adjacent full Rust suite and relevant Python Run/worker tests.
- Run a real Windows 1K mouse smoke, then a 4K/8K input smoke proving canonical output rate, per-millisecond displacement, button edges, queue drops, memory, snapshot I/O, and game/capture latency. Automated tests do not close this field Gate.
- Run scoped `git diff --check`, Agent contract parity if touched, and repository-local links for changed Markdown.

**Stop rule:** stop if preserving button edges requires changing the public record shape, if v1 migration cannot avoid mixed semantics, if barrier ordering cannot prove the final bucket is durable, if checked accumulation cannot fail closed, or if implementation requires Analysis/schema/UI changes outside Allowed files.
