# Windows Window Capture v1 Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a Windows-only KovaaK window capture boundary that can produce timestamped frame samples for later MP4 encoding without blocking Raw Input.

**Architecture:** Keep Raw Input as the priority capture path. Add a separate bounded frame producer using Windows.Graphics.Capture and a bounded writer/encoder boundary. Every frame keeps the Windows capture `SystemRelativeTime` and the local QPC/UTC correlation metadata; the product timeline remains `time_alignment.v2`. A failed or backpressured video path must degrade independently and never block Raw Input.

**Tech Stack:** Rust/Tauri, Windows.Graphics.Capture, Direct3D11, Media Foundation Sink Writer, existing ACRI v1 Raw Input codec.

---

## Frozen decisions

- Capture only the KovaaK window HWND; never the desktop.
- Raw Input has priority over video. Video queues are bounded and may drop frames with diagnostics.
- `SystemRelativeTime`/QPC are capture clocks only; Challenge-relative milliseconds remain the product timeline.
- Do not change ACRI v1 bytes.
- Do not add a frontend route until the native capture boundary has offline tests and a compile gate.
- Do not call this complete until one real KovaaK session verifies window selection, frame timestamps, MP4 output, and Raw/MP4 correlation.

## Task 1: Freeze native capture contracts — completed

**Files:**
- Modify: `webapp/frontend/src-tauri/Cargo.toml`
- Modify: `webapp/frontend/src-tauri/src/raw_input.rs`
- Create: `webapp/frontend/src-tauri/src/window_capture.rs`
- Test: inline Rust tests in `window_capture.rs`

Define the bounded frame sample, capture clock provenance, queue/drop diagnostics, and an unsupported non-Windows implementation. No HWND capture or encoder yet.

**Stop rule:** stop if the new API requires changing Raw Input records or product timeline semantics.

## Task 2: Implement WGC frame source — completed for metadata probe

**Files:**
- Modify: `webapp/frontend/src-tauri/src/window_capture.rs`
- Modify: `webapp/frontend/src-tauri/src/lib.rs`
- Test: inline Rust tests plus a non-window smoke command

Create a Windows.Graphics.Capture item from a supplied KovaaK HWND, create a D3D11 frame pool, and emit bounded frame metadata with `SystemRelativeTime` and QPC/UTC provenance. The current low-overhead probe intentionally omits GPU readback; expose start/stop/status only and keep MP4 for Task 3.

Offline and real-device gate passed: MSVC `cargo check --lib`, five native `window_capture` tests, and 5-second KovaaK `UnrealWindow` smokes in all three display modes: `Full screen windowed` (`1920x1080`, 824 frames), `Full screen` (`1920x1080`, 825 frames), and `Windowed` (`1922x1112`, 825 frames); all had zero timestamp regressions. Windowed dimensions include the non-client area and must be resolved by the writer/crop contract before MP4 output.

**Stop rule:** stop before Task 3 if the frame pool cannot be compiled or the capture API needs a different runtime boundary. Do not interpret the metadata probe as MP4 readiness.

## Task 3: Add Media Foundation writer — functional prototype complete, performance Gate failed

**Files:**
- Modify: `webapp/frontend/src-tauri/src/window_capture.rs`
- Modify: `webapp/frontend/src-tauri/src/lib.rs`
- Test: offline writer timestamp/queue tests

Encode bounded frame samples to H.264 MP4 with Media Foundation hardware preference where available, preserving frame PTS and independent video-drop diagnostics.

Implemented CPU-backed BGRA readback with a pre-readback `60 FPS` gate and a bounded writer thread (`try_send`, capacity `4`). Synthetic MP4 and real KovaaK `Full screen windowed` smokes passed. The live file was independently verified as H.264/yuv420p, `1920x1080`, constant `60 FPS`, `301` frames over `5.01665s`; visual frame extraction showed the expected KovaaK-only image. The writer received `276` source frames with zero writer drops and zero encoder errors; Media Foundation repeated frames to preserve the constant-rate output timeline, so diagnostics distinguish writer submissions from final encoded frame count.

The current CPU readback consumed approximately `1.44` CPU cores in the live 60 FPS smoke. A manual 30 FPS A/B roughly halved that cost, but the product default remains 60 FPS. Point-by-point CPU copies are no longer the release route: the approved successor is GPU-resident hardware encoding plus a 300-second encoded-packet replay buffer, specified in proposed [`2026-07-18-hardware-replay-buffer-v1.md`](2026-07-18-hardware-replay-buffer-v1.md). Keep this implementation as a functional and performance baseline; do not extend it into a CPU fallback for automatic capture.

**Stop rule:** no live session until the writer can start/stop deterministically and rejects invalid dimensions/timestamps.

**Closeout boundary:** Task 3 does not authorize the GPU encoder, replay buffer, Run storage, or Challenge finalizer. Those require the successor plan to be reviewed, marked active, and authorized one Task at a time.

## Verification gate

- Python alignment/native focused tests pass.
- Rust formatting/check/test pass on an installed Windows target.
- Non-Windows returns explicit unsupported status.
- Video queue is bounded and Raw Input remains non-blocking.
- Then ask 点点 to open KovaaK for the first real capture session.
