# Flicking Mainline Code Review — 2026-07-08

**Scope**: `pan_tracker.py`, `flicking.py`, `csv_parser.py`, `aligner.py`, `start_frame.py` + `tests/`
**Reviewer**: parallel agent (read-only)
**Health**: **Yellow-Green** — algorithm logic is sound; mainline metrics (SPARC / linearity / throughput / submovement / path_efficiency / decel_frac) implement correctly against their stated theoretical anchors. Issues are concentrated in resource management (1 missing try/finally), edge-case input handling (zero-kill CSV), and defensive guards. 07-07 fixes all verified present and correct.

---

## 07-07 Fix Verification (all PASS)

| Fix | Location | Status |
|-----|----------|--------|
| start_frame sliding-window minimum length | `start_frame.py:104-105` `if len(window) < hold_frames + 1: continue` | **PASS** — prevents false positive at tail of video |
| aligner NaN guard | `aligner.py:111` `if pd.isna(row.time_s) or pd.isna(row.TTK): continue` | **PASS** — skips kills with unparseable time/TTK |
| csv_parser TTK coerce | `csv_parser.py:181` `pd.to_numeric(kills["TTK"].str.rstrip("s"), errors="coerce")` | **PASS** — handles "0.395s" suffix and malformed values |
| csv_parser case-insensitive yaw | `csv_parser.py:66` `_GAME_YAW_LOWER` dict | **PASS** — `.lower()` lookup on sens_scale |
| segment_by_valleys empty guard | `flicking.py:439` `if speed.size == 0: return []` | **PASS** |
| Short-flick SPARC docstring | `flicking.py:473-479` | **PASS** — documents NaN behavior for <16-frame flicks |
| inferred_fps bias (pass fps param) | `pan_tracker.py:334,395` — `fps=meta.fps` passed explicitly to `compute_pan_trajectory` and `_ball_speed` | **PASS** — fps no longer re-inferred inside functions |

---

## Findings

### High

#### H-1: `compute_pan_trajectory` — VideoCapture not protected by try/finally (resource leak)

**File**: `pan_tracker.py:141-184`

**Problem**: `cap = cv2.VideoCapture(video_path)` at line 141 has no try/finally. `cap.release()` at line 184 is only reached on normal exit. If `detect_targets()`, `progress_callback()`, or any operation inside the loop raises, the VideoCapture is never released.

All other VideoCapture sites in the codebase have try/finally (verified):
- `start_frame.py:69-87` (detect_start_frame) — has try/finally
- `start_frame.py:186-223` (lock_challenge_window) — has try/finally
- `calibration_cli.py:43-119` — has try/finally
- `tracking.py:51-141` — has try/finally
- `video.py:63-72` (read_frame) — has try/finally

The 07-07 fix ("VideoCapture try/finally, 5 处") applied to tracking-module files but **missed pan_tracker.py**.

**Impact**: On Windows, an unreleased VideoCapture locks the video file. If `detect_targets` raises on a corrupt frame (possible with truncated H.264 streams), the file remains locked until process exit, preventing the user from re-running analysis or deleting the file.

**Fix**: Wrap lines 141-184 in `try: ... finally: cap.release()`.

---

#### H-2: Zero-kill CSV crashes mainline entry points (`math.ceil(NaN)` ValueError)

**File**: `pan_tracker.py:386` (`analyze_flicking_fair_summary`) and `pan_tracker.py:222` (`analyze_flicking_video`)

**Problem**:
```python
duration_s = float(math.ceil(stats.kills["time_s"].max()))
```
If the KovaaK's CSV has zero kills (player missed every target), `stats.kills` is an empty DataFrame. `empty_series.max()` returns NaN. `math.ceil(float("nan"))` raises `ValueError: cannot convert float NaN to integer` (confirmed by runtime test).

**Impact**: A zero-kill scenario CSV — uncommon but valid in KovaaK's (missed every shot) — crashes the entire analysis pipeline at the very first step with an opaque ValueError, not a user-friendly message.

**Fix**: Guard against empty/NaN:
```python
max_t = stats.kills["time_s"].max() if len(stats.kills) else float("nan")
if pd.isna(max_t):
    raise ValueError("CSV has no kills — cannot determine scenario duration. Pass duration_s explicitly.")
duration_s = float(math.ceil(max_t))
```
Or accept a `duration_s` parameter in `analyze_flicking_fair_summary` (currently hardcoded from kills).

---

### Medium

#### M-1: `_has_ui_element` — uint8 wrapping corrupts Otsu threshold

**File**: `start_frame.py:269`

**Problem**:
```python
dist = np.linalg.norm(small.astype(np.float32) - bg, axis=2)  # range [0, ~442]
otsu_thr, _ = cv2.threshold(dist.astype(np.uint8), 0, 255, ...)
```
`np.linalg.norm` of RGB differences can reach `sqrt(3 x 255^2) ~ 442`. Casting to `uint8` wraps values >255 modulo 256. Confirmed: `256 -> 0`, `300 -> 44`, `400 -> 144`, `441 -> 185`. This creates phantom low-distance pixels in the threshold input, distorting the Otsu computation.

**Impact**: The mask itself uses the float `dist > thr` comparison (line 272), so the final mask is only indirectly affected (via a potentially skewed threshold). For typical KovaaK's backgrounds (textured scenes, moderate contrast), distances rarely exceed 255. But for extreme-contrast UI screens (white results page on black background), the Otsu threshold may be off, causing the challenge-window auto-lock to mis-select the gameplay segment. This is the lock-window detector, not the flick metrics — so metrics are unaffected, but the window boundaries (which frames to analyze) could be wrong.

**Fix**: `dist_clipped = np.clip(dist, 0, 255).astype(np.uint8)` before Otsu.

---

#### M-2: `align()` crashes on NA Shots/Hits (`int(pd.NA)` TypeError)

**File**: `aligner.py:128-129`

**Problem**:
```python
shots=int(row.Shots),
hits=int(row.Hits),
```
`csv_parser` coerces Shots/Hits with `errors="coerce"` then `.astype("Int64")`, producing `pd.NA` for unparseable values. The align loop guards `time_s` and `TTK` NaN (line 111), but **not** Shots/Hits. If a kill row has a malformed Shots or Hits value, `int(pd.NA)` raises `TypeError: int() argument must be a real number, not 'NAType'` (confirmed by runtime test).

**Impact**: Requires a malformed CSV (Shots/Hits column has non-numeric data). KovaaK's always writes integers, so this is a defensive issue. But the crash is opaque — a TypeError from inside the align function with no indication that the CSV is at fault.

**Fix**: Extend the skip guard or default to 0:
```python
if pd.isna(row.time_s) or pd.isna(row.TTK) or pd.isna(row.Shots):
    continue
```

---

#### M-3: `segment_by_valleys` passes spurious flick when speed is all-zero

**File**: `flicking.py:441-458`

**Problem**: When `speed` is all zeros (e.g., pan_tracker detected no motion throughout):
- `peakmax = 0.0`, `prom = 0.0`
- `find_peaks(-zeros, prominence=0)` returns empty (no local minima in flat signal)
- `bounds = [0, len-1]` — one segment covering entire signal
- `peak_v = 0.0`
- Guard `if peak_v < prom * 1.5:` evaluates `0.0 < 0.0` = **False** — does not skip

A flick with `peak_v=0` gets passed to `compute_fair_metrics`, where `endpoint_peak = speed[e] / peak_v` = `0/0` = NaN, and `linearity = rmse / peak_v` = `0/0` = NaN. The NaN metrics get filtered in `_summarize_reference`, so the summary is not corrupted, but `flick_count` is inflated by 1 spurious flick.

**Impact**: Low practical impact (all-zero speed means no targets detected = degenerate video). Summary shows `flick_count: 1` with all-None metrics. Misleading but not crash-causing.

**Fix**: Change guard to `if peak_v <= 0:` or `if prom <= 0 or peak_v <= 0: return []` at the top.

---

#### M-4: No `fps > 0` guard in pan_tracker entry points

**File**: `pan_tracker.py:389` (`analyze_flicking_fair_summary`), `pan_tracker.py:322` (`analyze_flicking_reference`)

**Problem**: `fps = meta.fps` from `cv2.CAP_PROP_FPS`. If the video is corrupt or the codec doesn't report fps, OpenCV returns 0.0. This `fps=0` propagates to `_ball_speed` (`off / fps` = inf), `segment_by_valleys` (`dist = max(1, int(0 * 0.08))` = 1), and downstream. No crash, but produces NaN/inf-laced output.

`aligner.py:96` has `if fps <= 0: raise ValueError(...)`, but this guard is in the old `align()` path. The valley-segmentation path (`analyze_flicking_fair_summary` / `analyze_flicking_reference`) has no such guard.

**Impact**: Rare (requires corrupt video). Does not crash but produces garbage output silently.

**Fix**: Add `if fps <= 0: raise ValueError(f"Invalid fps ({fps}) from video metadata")` after reading `meta.fps`.

---

#### M-5: `detect_start_frame` per-sample `cap.set` is O(frame_no) on H.264

**File**: `start_frame.py:76`

**Problem**:
```python
while f < total:
    cap.set(cv2.CAP_PROP_POS_FRAMES, f)  # O(frame_no) per call on H.264
    ok, frame = cap.read()
    ...
    f += sample_step
```
Every sampled frame does a seek + read. On H.264, each `cap.set` re-decodes from the last keyframe. For a 60s clip at 60fps with sample_step=6, this is ~600 seeks.

`lock_challenge_window` (line 192-206) already solved this with `cap.grab()` + `cap.retrieve()` pattern: grab every frame (O(1)), retrieve only on stride frames. `detect_start_frame` does not use this pattern.

**Impact**: This function is used for interactive UI (suggest start frame), not the main analysis pipeline. Performance is suboptimal but not blocking. For long videos (5+ minutes), it could add 10-30 seconds of latency.

**Fix**: Switch to grab/retrieve pattern like `lock_challenge_window`, or read sequentially and skip non-stride frames.

---

### Low

#### L-1: `compute_pan_trajectory` reads video metadata twice
**File**: `pan_tracker.py:138` + callers (e.g., `pan_tracker.py:388`)

`get_video_metadata(video_path)` is called both inside `compute_pan_trajectory` and in its callers (`analyze_flicking_fair_summary`, `analyze_flicking_reference`, `analyze_flicking_video`). Each call opens and closes a VideoCapture. Minor waste (~2ms per call), not a correctness issue.

---

#### L-2: `_segment_sparc` excludes DC component from arc length
**File**: `flicking.py:495-497`

```python
f_v = freqs[1:fc + 1]  # starts from index 1, excluding DC
V_v = spectrum[1:fc + 1]
```
The original Balasubramanian 2012 formula integrates from f=0 to fc. This implementation excludes the DC term (index 0, which is 1.0 after normalization), starting the arc from freqs[1]. The deviation is consistent across all flicks (so comparative fairness is preserved), but absolute SPARC values differ slightly from reference implementations. Documented as a design choice, not a bug.

---

#### L-3: `csv_parser` — "Challenge Start" missing key unguarded
**File**: `csv_parser.py:183`

`summary["Challenge Start"]` raises `KeyError` if the summary block doesn't contain this field. Non-standard CSVs (manually edited, truncated) could lack it. The error is a bare KeyError, not a user-friendly message.

---

#### L-4: `extract_flicks` (legacy path) doesn't guard all-NaN speed
**File**: `flicking.py:99-102`

The legacy `extract_flicks` function (pre-valley-segmentation) checks `speed.size == 0` but not all-NaN. If speed is all-NaN (all tracking frames bad), `np.nanmax(speed)` returns NaN with a RuntimeWarning, and downstream logic processes NaN values. This path is not used by the current mainline (`analyze_flicking_fair_summary` uses `segment_by_valleys`), so impact is limited to the legacy `run_flicking_analysis` CLI path.

---

#### L-5: No unit tests for core algorithms
**Files**: `tests/`

`segment_by_valleys`, `_segment_sparc`, `_submovement_structure`, `compute_fair_metrics`, and `lock_challenge_window` have no dedicated unit tests. Only `test_progress_a.py` tests the end-to-end summary shape and throughput wiring. Edge cases (empty speed, single flick, two-stage flicks, short segments) are untested. The algorithms are correct by inspection, but regression protection is thin.

---

#### L-6: `np.polyfit` RankWarning unsuppressed in linearity computation
**File**: `flicking.py:560`

```python
fit = np.polyfit(t, decel, 1)
```
If the decel phase has near-constant values (flat deceleration), `polyfit` emits a `RankWarning`. Not suppressed. Cosmetic issue — does not affect correctness (the fit is still computed, residuals are ~0, linearity is ~0).

---

## Top 3 Actionable

1. **H-1**: Add try/finally to `compute_pan_trajectory` (pan_tracker.py:141-184). This is the only VideoCapture site in the codebase without it — a known pattern that was missed in the 07-07 fix.

2. **H-2**: Guard zero-kill CSV in `analyze_flicking_fair_summary` / `analyze_flicking_video`. A valid KovaaK's scenario (0 kills) currently crashes with an opaque ValueError.

3. **M-1**: Fix uint8 wrapping in `_has_ui_element` Otsu threshold (start_frame.py:269). One-line fix (`np.clip`) that prevents threshold corruption on high-contrast UI screens.

---

## Summary

The flicking mainline is architecturally sound. The valley segmentation, fair metrics (SPARC, linearity, throughput, submovement, path efficiency, decel_frac), CSV parsing, and alignment logic all implement their stated algorithms correctly. The 07-07 fixes are all present and verified.

The main risks are in **input edge cases** (zero-kill CSV, all-zero speed, corrupt video metadata) and **resource management** (one missing try/finally). These are straightforward defensive fixes, not algorithmic redesigns. No Critical issues found — the bugs require unusual inputs to trigger and do not affect the normal analysis path.

**Counts**: 0 Critical / 2 High / 5 Medium / 6 Low
