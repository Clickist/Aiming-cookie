# Hardware Replay Buffer v1 Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

> **Status:** completed at the supported evidence boundary. Task 1–3、hardware replay backpressure repair、Task 4 normal/timescale/Restart 已通过；short-pause 由后续 fail-closed repair 明确排除，AMD/Intel 物理验证保留为 Roadmap 外部发布 Gate。

> **Pause contract (2026-07-19):** `Pause Count > 0` is fail closed in v1: no permanent MP4 and no canonical Raw/Performance alignment claim. `Pause Count = 0` normal/timescale-only Challenges remain supported.

> **Implementation status (2026-07-20):** The resolver, Run ingestion forwarding, and legacy native snapshot guard enforce the fail-closed pause branch. No pause wall duration is reconstructed or approximated；后续 Restart 与 automatic product-path rows 已通过，当前计划不得恢复执行。

**Goal:** Replace the CPU-backed automatic-video prototype with a cross-vendor GPU-resident hardware encoder and a bounded encoded-packet replay buffer that can export one canonical Challenge window without interrupting capture for the next Challenge.

**Architecture:** Keep WGC frames as Direct3D11 surfaces on the same adapter through GPU color conversion and a hardware H.264 Media Foundation transform. Store only timestamped encoded packets in a time- and byte-bounded replay ring. Stats / Performance arrive after the Challenge, resolve `[start_ms, end_ms)`, and request an immutable packet snapshot for MP4 muxing while the live ring continues accepting packets. Raw Input remains the priority path and keeps ACRI v1 unchanged.

**Tech Stack:** Rust/Tauri, Windows.Graphics.Capture, Direct3D11/DXGI, Media Foundation hardware MFT, existing `time_alignment.v2`, existing ACRI v1.

---

## Frozen decisions

- Capture only the KovaaK HWND; never capture the desktop or another window.
- Preserve the captured KovaaK window dimensions within hardware capability and keep `60 FPS`; `1920x1080` is the current field-validation baseline, not a universal user default. Do not add automatic `30 FPS` degradation.
- The automatic-video path is hardware-only. Hardware encoder unavailable, adapter mismatch, or GPU-path failure degrades video independently; never silently fall back to continuous CPU readback/encoding.
- The replay buffer remains bounded to `300 seconds` of wall time. Permanent MP4 support in v1 applies only to `Pause Count = 0` normal/timescale-only Challenges; `Pause Count > 0` pauses fail closed for permanent MP4 and canonical cross-source alignment. Longer or gapped windows also fail closed.
- The encoded replay ring is bounded by both `300 seconds` and `384 MiB`; the 8 Mbps target must retain the full 300-second validation stream within that byte ceiling. Hitting either bound without full canonical coverage produces an explicit coverage failure.
- Visual countdown/HUD/results detection is diagnostic only and cannot decide capture correctness or retention.
- Saving one Run must snapshot packet references and mux off the producer thread; it must not stop, clear, or block the replay ring serving a possible next Challenge.
- Raw Input keeps its existing physical retention and ACRI v1 bytes. Automatic cross-source completeness is claimed only when both sources cover the same canonical window.
- Exact codec vendor selection remains Media Foundation capability-driven so NVIDIA, AMD, and Intel can use their driver-provided hardware encoder. Do not add a vendor-only runtime dependency in v1.
- No commit or push is part of any Task unless 点点 separately requests it.

## Task 1: Prove GPU surface to in-memory hardware H.264 packets

**Allowed files:**

- Modify: `webapp/frontend/src-tauri/Cargo.toml`
- Modify: `webapp/frontend/src-tauri/Cargo.lock`
- Modify: `webapp/frontend/src-tauri/src/window_capture.rs`
- Modify: `webapp/frontend/src-tauri/src/lib.rs`
- Tests: inline Rust tests and one ignored Windows hardware smoke in `window_capture.rs`

**Tests first:**

- pure policy tests reject CPU fallback and distinguish hardware unavailable, adapter mismatch, conversion failure, encoder failure, and backpressure;
- ignored MSVC smoke creates a D3D11 texture, keeps conversion on the GPU, submits monotonic `60 FPS` PTS, and receives non-empty H.264 access units with keyframe metadata;
- status exposes adapter identity, encoder path, first/last packet PTS, submitted packets, dropped packets, and encoder errors without exposing arbitrary filesystem paths.

**Implementation:**

Create an `IMFDXGIDeviceManager` for the existing WGC D3D11 device, select a hardware H.264 MFT matching that adapter, convert the WGC BGRA surface to the encoder-supported GPU format, and emit owned encoded packets rather than writing an MP4 URL. Keep the existing CPU writer only as an explicit test/performance baseline; the automatic path must not select it.

**Verification:**

- MSVC `cargo fmt --check`, `cargo check --target x86_64-pc-windows-msvc --lib`, focused `cargo clippy`, and focused tests pass;
- synthetic hardware smoke returns valid H.264 packets and monotonic PTS;
- real RTX 3060 KovaaK 1080p60 smoke shows no full-frame staging `Map`/BGRA `Vec<u8>` on the automatic path, zero Raw Input drops, bounded packet queue, and materially lower CPU than the `~1.44`-core baseline.

**Stop rule:** stop before implementation if a same-adapter hardware MFT cannot return encoded packets without full-frame CPU readback, if a vendor-only SDK or runtime FFmpeg dependency is required, or if any file outside Allowed files is needed.

## Task 2: Add the bounded encoded replay ring

**Allowed files:**

- Modify: `webapp/frontend/src-tauri/src/window_capture.rs`
- Tests: inline pure Rust replay-ring tests

**Tests first:**

- retain a full 300-second monotonic packet window at the target bitrate;
- evict only data older than the time/byte bounds while preserving a decodable keyframe boundary;
- reject timestamp regression, missing keyframe coverage, byte overflow, and a requested window longer than 300 seconds;
- snapshotting a completed window does not block or clear later packets;
- consecutive and restarted candidate windows do not merge without separate canonical Stats / Performance windows.

**Implementation:**

Add a single-purpose `EncodedReplayBuffer` over encoded packet ownership/reference counts. Track first/last PTS, bytes, keyframes, evictions, and coverage gaps. A snapshot request returns either immutable packet references plus exact requested offsets or a typed fail-closed reason.

**Verification:** focused Rust tests, format/check/clippy, and a bounded-memory synthetic 300-second packet stream pass.

**Stop rule:** stop if correct eviction requires guessing Challenge state, if packet ownership can block the encoder producer, or if the 300-second / 384 MiB contract cannot retain the 8 Mbps validation stream.

## Task 3: Export a canonical replay window to MP4

**Allowed files:**

- Modify: `webapp/frontend/src-tauri/src/window_capture.rs`
- Modify: `webapp/frontend/src-tauri/src/lib.rs`
- Tests: inline Rust mux/window tests and one ignored MP4 smoke

**Tests first:**

- export `[start, end)` from a keyframe-backed packet snapshot without re-encoding the full window;
- normalize MP4 PTS to the Challenge-relative timeline and preserve capture-clock sidecar provenance;
- handle a requested start between keyframes without exposing pre-Challenge frames in playback;
- reject incomplete packet coverage and keep the live replay producer accepting later packets during mux;
- produce an H.264 MP4 whose ffprobe duration/frame rate and extracted first/last visible frames match the requested window.

**Implementation:**

Mux an immutable encoded-packet snapshot into a new Run-owned MP4 on an isolated worker. Use a keyframe-safe boundary plus container timing/edit semantics or boundary-only repair; do not re-encode the entire Challenge and do not pause live capture. Return typed coverage/mux/finalization diagnostics.

**Verification:** focused tests, ignored synthetic MP4 smoke, ffprobe, frame extraction, and concurrent producer/export stress pass.

**Stop rule:** stop if frame-accurate playback requires a runtime FFmpeg dependency, full-window re-encode, stopping the live encoder, or files outside Allowed files.

## Hardware replay backpressure repair Task

> **Authorization:** 点点 authorized this repair Task on 2026-07-19 after Task 4 field evidence triggered its coverage Stop rule.

**Allowed files:**

- Modify: `webapp/frontend/src-tauri/src/window_capture.rs`
- Tests: inline Rust tests in `window_capture.rs`
- Modify: `docs/PROGRESS.md` only after new runtime evidence exists
- Runtime outputs: temporary validation bundle outside the repository

**Tests first:**

- a 165 Hz source clock reserves exactly 60 frames per second and assigns monotonic, evenly spaced encoded PTS while retaining the original WGC timestamp as provenance;
- without an MFT `NeedInput` permit the worker does not dequeue or classify an otherwise valid frame as backpressure;
- each permit submits at most one frame, and only a full bounded producer queue counts as backpressure;
- the synthetic hardware smoke submits 120 frames without packet drops or replay coverage gaps;
- a real KovaaK idle-window smoke exports the latest 10 seconds with complete packet coverage and zero Raw Input drops before Task 4 resumes.

**Implementation:**

Return the reserved 60 Hz encoded PTS from the capture limiter and carry it beside the real WGC `FrameSample`. Drain asynchronous MFT events before dequeuing, then submit at most one queued frame per available input permit. Poll the hardware path at approximately 1 ms without busy-waiting or moving GPU work into `FrameArrived`.

**Frozen decisions:**

- keep 60 FPS, hardware-only encoding, same-adapter GPU conversion, bounded queues, ACRI v1 bytes, and Raw Input priority;
- do not duplicate frames, fabricate packet coverage, silently fall back to CPU readback, or add a runtime FFmpeg dependency;
- WGC `SystemRelativeTime` remains the source timestamp; the stable encoded PTS is a derived media timeline only.

**Verification:** focused Rust tests, `cargo fmt --check`, MSVC `cargo check --target x86_64-pc-windows-msvc --lib`, focused `cargo clippy`, the ignored synthetic hardware smoke, and the real idle-window replay/performance smoke pass.

**Stop rule:** stop if another repository file is required, if MFT scheduling cannot avoid false backpressure without blocking `FrameArrived` or Raw Input, if stable media PTS requires falsifying source timestamps, or if the real idle-window smoke still has packet drops or a replay coverage gap.

## Task 4: Windows field Gate and pause semantics

**Allowed files:**

- Modify: `docs/PROGRESS.md` only after evidence exists
- Runtime outputs: temporary validation bundle outside the repository
- Tests: no business-code changes

**Tests first / field matrix:**

- normal 60-second Challenge;
- timescale-extended Challenge;
- one short in-game pause, then continue and finish; this row must fail closed without creating a permanent MP4 or canonical Raw/Performance alignment;
- Restart before the completed attempt;
- synthetic/requested wall window over 300 seconds must fail closed;
- KovaaK left open after finalization while the ring continues bounded capture;
- hardware unavailable/adapter mismatch diagnostic path;
- at least NVIDIA real hardware now; AMD and Intel remain release Gates until available.

**Verification:** preserve Raw/WGC/encoded packet capture clocks, Stats `Challenge Start` / `Pause Duration`, Performance events, packet coverage, Raw coverage, drop metrics, CPU/GPU usage, memory high-water mark, ffprobe output, and extracted boundary frames. The pause sample must exercise the fail-closed result: no permanent MP4 and no canonical Raw/Performance alignment claim; any unproven pause wall duration remains diagnostic only.

**Stop rule:** stop and report if Raw or video lacks complete `[start_ms, end_ms)` coverage, if short-pause semantics conflict across sources, if capture memory exceeds the frozen bound, or if real 1080p60 performance is not materially better than the CPU-backed baseline.

## Pause-aware time alignment assessment/repair Task

> **Authorization:** 点点 authorized this assessment/repair Task on 2026-07-19 after the Task 4 short-pause sample contradicted the current event-time assumption.

**Allowed files:**

- Modify only after source proof: `kovaak_tracker/time_alignment.py`
- Modify: `kovaak_tracker/native_flicking_analysis.py` (legacy snapshot guard only)
- Modify: `webapp/backend/kovaak_run_store.py` (forward parsed Stats pause count only)
- Tests: `tests/test_time_alignment.py`, `tests/test_native_flicking_analysis.py`, focused `webapp/tests/test_kovaak_runs.py`
- Modify: `docs/PROGRESS.md` only after new evidence exists
- Runtime outputs: temporary validation evidence outside the repository

**Assessment first:**

- inspect the pause Performance wire payload for an explicit millisecond-resolution pause duration, resume timestamp, or equivalent game-emitted wall-clock field;
- determine whether Stats `Pause Duration` preserves sub-second precision or is integer/coarse by comparing the raw CSV value with frame-clock diagnostics;
- keep filesystem timestamps, Raw inactivity, visual pause-menu transitions, filename timestamps, and results-screen detection diagnostic-only unless an upstream frozen contract is explicitly revised;
- compare normal, timescale, and paused samples so a pause repair cannot regress existing timer/event windows.

**Tests first for the fail-closed repair:**

- reproduce the field failure where a paused 60-second run resolves to about 59.944 seconds instead of its wall duration;
- reject any parsed `pauseCount > 0` event before producing a canonical window;
- reject non-zero/coarse-only pause duration evidence rather than guessing a canonical end;
- preserve normal and timescale windows when pause evidence is zero.

**Frozen decisions:**

- Performance event timestamps are active-game time in the observed paused sample and cannot be treated as wall time;
- no filename/mtime, Raw inactivity, or visual heuristic may silently become the correctness source;
- do not round, extrapolate, or hide uncertainty to manufacture millisecond alignment;
- v1 pause handling is fail closed: no permanent MP4 or canonical Raw/Performance alignment is produced when pause evidence is present;
- no repair may reconstruct a paused wall duration unless a game-emitted millisecond-resolution source is proven.

**Verification:** focused time-alignment tests and the normal/timescale regression set preserve their existing windows; paused evidence produces a typed fail-closed result. Task 4 remains stopped before Restart.

**Stop rule:** if no explicit millisecond-resolution pause wall duration source is proven, do not reconstruct or approximate a paused end; keep the fail-closed guard, update Progress with evidence, and stop before Restart.

## Out of scope

- Capture Coordinator database/API schema, Run storage transactions, startup recovery, frontend status UI, Storage deletion UI, AMD/Intel hardware procurement, audio capture, HEVC/AV1, and vendor-specific encoder SDKs require later reviewed Tasks/plans.
