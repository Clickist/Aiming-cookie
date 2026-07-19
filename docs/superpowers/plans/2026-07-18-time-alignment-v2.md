# Time Alignment v2 Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace the unsafe Performance-only Raw trace window with a versioned, testable resolver that can prepare the desktop capture path for QPC/MP4 field validation.

**Architecture:** Keep ACRI v1 readable and preserve the product timeline as Challenge-relative milliseconds. Add a pure Python resolver that combines Stats millisecond precision, Performance profile/event facts, pause duration, and explicit termination evidence; then use its half-open wall-elapsed window for Raw extraction. QPC/WGC capture metadata remains a separate follow-up boundary and is not guessed in offline code.

**Tech Stack:** Python 3.11, pytest, existing KovaaK Stats/Performance parsers, existing Rust ACRI v1 codec.

---

## Frozen Decisions

- `challenge_wall_elapsed_ms` includes pause time so Raw and MP4 share one stable timeline.
- `Stats Challenge Start` supplies the millisecond fraction; `Performance.challenge_start_utc` supplies the date/second identity.
- Timer end is `time_limit / timescale + pause_duration`.
- Event-terminated runs use the latest trustworthy Stats/Performance relative event time, with explicit `termination_source`.
- Filename time is a coarse validation hint only, never the exact end.
- Physical extraction uses `[start_ms, end_ms)`; ACRI v1 bytes remain unchanged.
- Conflicting anchors fail closed; when Stats is unavailable, the Performance second/date anchor is an explicit degraded fallback with `stats_anchor_missing` warning. No neighboring-file guessing.

## Task 1: Add pure time resolver

**Files:**
- Create: `kovaak_tracker/time_alignment.py`
- Test: `tests/test_time_alignment.py`

**Tests first:** cover precise start reconstruction, normal timer, timescale, pause, event-terminated run, filename hint, cross-midnight, missing/conflicting source, and half-open boundary behavior.

**Stop rule:** stop if the resolver needs to redefine Stats/Performance parser wire fields or expose QPC as the product timeline.

## Task 2: Integrate resolver into Run trace finalization

**Files:**
- Modify: `webapp/backend/kovaak_run_store.py`
- Modify: `kovaak_tracker/native_flicking_analysis.py`
- Test: `webapp/tests/test_kovaak_runs.py`
- Test: `tests/test_native_flicking_analysis.py`

**Tests first:** add timescale and event-end regression cases; ensure existing ACRI v1 extraction remains readable and the alignment result reports v2 provenance.

**Stop rule:** stop if DB schema, AnalysisResult v2 shape, ownership, or deletion semantics must change.

## Task 3: Prepare the real-device capture boundary

**Files:**
- Modify: `webapp/frontend/src-tauri/src/raw_input.rs`
- Test: inline Rust tests in `webapp/frontend/src-tauri/src/raw_input.rs`
- Modify: `docs/superpowers/specs/2026-07-13-kovaak-run-trace-lifecycle-design.md`
- Modify: `docs/superpowers/specs/2026-07-17-automatic-run-capture-design.md`

Add non-destructive capture metadata hooks for QPC/UTC correlation without changing ACRI v1 records. Do not implement WGC or MP4 encoding in this plan; the output must make a later WGC spike able to record a paired validation bundle.

**Stop rule:** stop before adding a new binary codec, helper process, WGC dependency, or frontend route.

## Verification Gate

- Focused Python tests pass.
- Existing Python regression suite passes.
- Rust `cargo fmt --check`, `cargo test`, and `cargo clippy -- -D warnings` pass where available.
- Six user-provided real pairs plus RefleK fixtures resolve without unsafe fallback.
- Report remaining real-device Gate: Raw QPC + WGC `SystemRelativeTime` + MP4 PTS in one live KovaaK session.
