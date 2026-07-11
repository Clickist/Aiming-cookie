# Flicking Mainline Code Review — 2026-07-09

**Scope**: `pan_tracker.py`, `flicking.py`, `csv_parser.py`, `aligner.py`, `start_frame.py` + `tests/`
**Reviewer**: review agent (read-only)
**Health**: **Green-Yellow** — algorithm logic remains sound; all 07-07 fixes verified present; 5a5bb84 theory fix accurate. **Two mandatory issues from 07-08 remain unfixed** (H-1 VideoCapture try/finally, H-2 zero-kill CSV NaN crash).

---

## 07-08 Fix Verification (5a5bb84)

| Fix | Location | Status |
|-----|----------|--------|
| submovement_overlap docstring clarification | `flicking.py:417` + `flicking.py:515-518` | **PASS** — accurately renamed to "trough depth ratio", correctly documented as NOT Novak 2002 time-overlap; aligns with tracking PTC naming-debt pattern |

**Verification**: The fix correctly identifies that `submovement_overlap` is actually measuring trough depth relative to peak (`trough / peak_v`), not temporal overlap of submovements. The docstring now explicitly states this naming ambiguity and references the parallel issue with tracking PTC. The implementation logic (lines 535-536) confirms: `trough / peak_v` is returned, which is a depth ratio, not a time overlap measure.

---

## Mandatory Unfixed Items (from 07-08)

### H-1: `compute_pan_trajectory` — VideoCapture not protected by try/finally (resource leak)

**File**: `pan_tracker.py:141-184`

**Status**: **NOT FIXED** — 5a5bb84 missed this location.

**Current State**:
```python
141: cap = cv2.VideoCapture(video_path)
...
184: cap.release()
```
No try/finally wrapping. If `detect_targets()` (line 159/163), `progress_callback()` (line 156/183), or any loop operation raises, the VideoCapture is never released.

**Impact**: On Windows, an unreleased VideoCapture locks the video file. If the loop crashes on a corrupt frame (possible with truncated H.264 streams), the file remains locked until process exit, preventing re-analysis or deletion.

**Evidence**: All other VideoCapture sites in the codebase have try/finally:
- `start_frame.py:69-87` (detect_start_frame) — has try/finally
- `start_frame.py:186-223` (lock_challenge_window) — has try/finally
- `calibration_cli.py:43-119` — has try/finally
- `tracking.py:51-141` — has try/finally
- `video.py:27-42` (get_video_metadata) — has try/finally (fixed in 5a5bb84)

**Fix**:
```python
cap = cv2.VideoCapture(video_path)
try:
    cap.set(cv2.CAP_PROP_POS_FRAMES, start_frame)
    for off, f in enumerate(range(start_frame, end_frame + 1)):
        ...
finally:
    cap.release()
```

---

### H-2: Zero-kill CSV crashes mainline entry points (`math.ceil(NaN)` ValueError)

**File**: `pan_tracker.py:222` (`analyze_flicking_video`) + `pan_tracker.py:386` (`analyze_flicking_fair_summary`)

**Status**: **NOT FIXED** — both locations remain vulnerable.

**Current State**:
```python
# Line 222 (analyze_flicking_video)
stats = parse_stats_csv(csv_path)
if duration_s is None:
    duration_s = float(math.ceil(stats.kills["time_s"].max()))

# Line 386 (analyze_flicking_fair_summary)
stats = parse_stats_csv(csv_path)
duration_s = float(math.ceil(stats.kills["time_s"].max()))
```

**Impact**: If KovaaK's CSV has zero kills (player missed every target), `stats.kills` is an empty DataFrame. `empty_series.max()` returns `NaN`. `math.ceil(float("nan"))` raises `ValueError: cannot convert float NaN to integer`. This is a valid KovaaK's scenario (missed every shot) but crashes the entire pipeline with an opaque error.

**Evidence**: Runtime test confirms:
```python
>>> import pandas as pd, math
>>> math.ceil(pd.Series([]).max())
ValueError: cannot convert float NaN to integer
```

**Fix**:
```python
max_t = stats.kills["time_s"].max() if len(stats.kills) > 0 else None
if max_t is None or pd.isna(max_t):
    raise ValueError(
        "CSV has no kills — cannot determine scenario duration. "
        "Pass duration_s explicitly or check that the CSV contains kill data."
    )
duration_s = float(math.ceil(max_t))
```

---

## New Findings (2026-07-09)

### High

#### H-3: `segment_by_valleys` — Division by zero risk when `peak_v=0`

**File**: `flicking.py:453`

**Problem**:
```python
peak_v = float(seg[p - s])
if peak_v < prom * 1.5:
    continue
```
When `peak_v = 0` (all-zero speed segment, possible if pan_tracker detected no motion), the guard `peak_v < prom * 1.5` evaluates `0 < positive_number` = True, so it skips. However, if the caller passes a manually crafted `speed` array where `seg[p-s]` returns 0 but the segment is still processed downstream in `compute_fair_metrics`, we get:
- Line 565: `linearity = ... / peak_v` → 0/0 = NaN
- Line 573: `endpk = speed[e] / peak_v` → 0/0 = NaN

**Impact**: Low practical impact (all-zero speed = degenerate video). The guard correctly skips these segments, so the NaN metrics never reach the summary. But the division-by-zero is implicit and could surface if the caller bypasses `segment_by_valleys`.

**Fix**: Add explicit `peak_v <= 0` guard:
```python
if peak_v <= 0 or peak_v < prom * 1.5:
    continue
```

---

### Medium

#### M-1: `_summarize_reference` — Empty metrics list produces empty summary without warning

**File**: `pan_tracker.py:265-294`

**Problem**: When `metrics` is empty (no flicks detected), every metric in the summary becomes `None`. The function returns `{"flick_count": 0, ...}` with no indication that this is abnormal (e.g., due to a black screen or a corrupted video where `detect_targets` found nothing).

**Impact**: A degenerate session produces a "valid" summary that the caller cannot distinguish from a healthy session. The coach may attempt to diagnose an empty dataset and produce nonsensical findings.

**Fix**: Add a warning flag:
```python
if len(metrics) == 0:
    out["_warning"] = "No flicks detected — possible tracking failure (black screen, low contrast, corrupt video)"
```

---

#### M-2: `compute_fair_metrics` — No validation that `flick` indices are within bounds

**File**: `flicking.py:558-559`

**Problem**:
```python
s, p, e, peak_v, duration_s = flick
seg_speed = speed[s:e + 1]
```
No validation that `0 <= s <= p <= e < len(speed)`. If the caller passes a malformed tuple (e.g., from a bug in `segment_by_valleys`), this will raise IndexError or produce incorrect metrics.

**Impact**: Requires a bug upstream in `segment_by_valleys` to trigger. The current implementation of `segment_by_valleys` produces well-formed tuples, but there's no defensive guard if the caller bypasses it.

**Fix**: Add bounds check:
```python
if not (0 <= s <= p <= e < len(speed)):
    raise ValueError(f"Invalid flick indices: s={s}, p={p}, e={e}, len={len(speed)}")
```

---

#### M-3: `_segment_sparc` — DC component exclusion from arc length is undocumented deviation

**File**: `flicking.py:495-496`

**Problem**:
```python
f_v = freqs[1:fc + 1]  # starts from index 1, excluding DC
V_v = spectrum[1:fc + 1]
```
The original Balasubramanian 2012 formula integrates from f=0 to fc. This implementation excludes the DC term (index 0). The deviation is consistent across all flicks (so comparative fairness is preserved), but the docstring does not mention it.

**Impact**: Absolute SPARC values differ slightly from reference implementations. A user comparing against a different implementation may see a systematic offset.

**Fix**: Document the deviation in the docstring:
```python
"""... Note: This implementation excludes the DC component (freqs[0]) from the arc
length, deviating slightly from Balasubramanian 2012. The offset is consistent
across all flicks, so comparative fairness is preserved."""
```

---

### Low

#### L-1: `csv_parser` — No validation that `KILL_HEADER` matches CSV schema before parsing

**File**: `csv_parser.py:150-155`

**Problem**: The header validation at line 150 raises `ValueError` if the header doesn't match, but this check only happens on the first line. A CSV with a malformed header line (missing columns, extra columns) will fail, but the error message doesn't indicate which column is wrong.

**Impact**: Minor — the error is raised before any parsing, so no corruption occurs. The error message could be more helpful.

**Fix**: Improve error message with column-by-column comparison.

---

#### L-2: `aligner` — No guard for empty `track_df`

**File**: `aligner.py:99-104`

**Problem**: `track_df` is assumed to have at least one row. If empty, `track_frames.min()` / `.max()` raise `ValueError`.

**Impact**: Requires the caller to pass an empty DataFrame, which is possible if `tracking` module produced no output. The `lock_challenge_window` function already guards against empty runs, but `align` is a public function that could be called directly.

**Fix**: Add:
```python
if len(track_df) == 0:
    raise ValueError("track_df is empty — cannot align")
```

---

#### L-3: `advice.THRESHOLDS` — No documentation of calibration source

**File**: `advice.py:35-48`

**Problem`: Thresholds like `"sparc_low": -5.0` and `"decel_frac_high": 0.65` are constants without documented population source. Are they from real user data? Academic papers? Community consensus?

**Impact**: Users may misinterpret these as universal standards when they are likely calibration values from a specific population. Changing them requires finding the right place to edit.

**Fix**: Add comments referencing the source (e.g., "# Calibrated on 10 users, 2026-06" or "# From docs/aim-kinematics-research.md §2").

---

## Old Pipeline Status (aligner.py + legacy flicking functions)

**Files**: `aligner.py` (159 lines), `flicking.py` legacy functions (`extract_flicks`, `analyze_flicks`, `run_flicking_analysis`)

**Current Usage**:
- `aligner.align()` — **only used by** `run_flicking_analysis` (flicking.py:371)
- `extract_flicks()` — **only used by** `analyze_flicks` (flicking.py:253)
- `analyze_flicks()` — **only used by** `run_flicking_analysis` (flicking.py:372)
- `run_flicking_analysis()` — **only used by** `analyze_flicking_video` (pan_tracker.py:237)
- `analyze_flicking_video()` — **NOT imported anywhere** in the codebase

**Runtime Status**:
- Main entry point `analyze_flicking_fair_summary` uses **valley segmentation** (`segment_by_valleys`), NOT the legacy pipeline
- Reference path `analyze_flicking_reference` uses **valley segmentation**, NOT the legacy pipeline
- Legacy path `analyze_flicking_video` is **exported but unused**

**Test Coverage**: No tests for the legacy path.

**Recommendation**: Safe to delete in v2. The legacy path (~470 lines total across aligner.py + flicking.py legacy functions) has zero runtime usage and zero test coverage. This is dead code from pre-valley-segmentation era.

---

## Fairness Metrics Correctness (Deep Dive)

### SPARC (`_segment_sparc`)
- **Theory**: Balasubramanian 2012, spectral arc length of speed profile
- **Implementation**: Correct — DC-normalizes, computes arc length from freqs[1:fc]
- **Deviations**: Excludes DC component (see M-3); short segments return NaN (documented limitation)
- **Verdict**: **CORRECT** (with documented deviation)

### Submovement Overlap (`_submovement_structure`)
- **Theory**: Trough depth ratio between primary peak and first corrective
- **Implementation**: Correct — finds corrective peaks, computes `trough / peak_v`
- **Naming**: Accurately renamed in 5a5bb84 to reflect trough depth, NOT Novak time-overlap
- **Verdict**: **CORRECT** (naming now aligns with implementation)

### Fitts Throughput (`compute_fair_metrics`)
- **Theory**: `TP = log2(D/W + 1) / MT` (bits/s), distance-normalized speed
- **Implementation**: Correct — uses `straight_px` for distance, `target_width_deg` for width
- **Guard**: Returns NaN when `target_width_deg` is unavailable (no-CSV mode)
- **Verdict**: **CORRECT**

### Linearity (`compute_fair_metrics`)
- **Theory**: RMSE of decel-phase speed vs constant-deceleration fit, normalized by peak
- **Implementation**: Correct — `polyfit(decel, 1)` → residuals → `sqrt(mean(resid^2)) / peak_v`
- **Guard**: Returns NaN when decel phase < 3 frames
- **Verdict**: **CORRECT**

### Decel Fraction (`compute_fair_metrics`)
- **Theory**: `(end - peak) / (end - start)` — decel-phase length as fraction of total flick
- **Implementation**: Correct — `(e - p) / max(1.0, (e - s))`
- **Verdict**: **CORRECT**

### Reverse Ratio (`compute_fair_metrics`)
- **Theory**: Fraction of decel-phase frames with positive acceleration (reversing direction)
- **Implementation**: Correct — `mean(accel[p:e+1] > 0)`
- **Verdict**: **CORRECT**

### Path Efficiency (`compute_fair_metrics`)
- **Theory**: Straight-line distance / actual path length
- **Implementation**: Correct — `hypot(x[-1]-x[0], y[-1]-y[0]) / sum(hypot(diff(xs), diff(ys)))`
- **Verdict**: **CORRECT**

### Peak Position (`compute_fair_metrics`)
- **Theory**: Peak location as percentage of flick length
- **Implementation**: Correct — `round(100 * (p - s) / (e - s), 1)`
- **Verdict**: **CORRECT**

---

## CV Pan Estimation Failure Modes

### Failure Mode 1: Low Texture / Uniform Background
**Symptom**: `detect_targets()` returns empty centroids array
**Current Handling**: `pan` becomes `[0, 0]`; `n_targets = 0`
**Impact**: Speed becomes 0; `segment_by_valleys` returns empty flick list; summary has `flick_count: 0`
**Detection**: Check `n_targets` column in output CSV; if mostly 0, detection failed

### Failure Mode 2: Motion Blur During Fast Flicks
**Symptom**: Target detection becomes intermittent during high-speed motion
**Current Handling**: Median matching is robust to a few lost targets (requires >=2 matches)
**Impact**: Slightly noisy pan, but smoothed by `_ball_speed` Savitzky-Golay filter
**Detection**: High `reverse_ratio` in metrics may indicate tracking instability

### Failure Mode 3: Black Screen / Corrupt Video
**Symptom**: `cap.read()` returns False; frame is all black
**Current Handling**: Loop breaks at line 157 (`if not ok: break`); progress callback sent to 1.0
**Impact**: Partial trajectory up to failure point
**Detection**: Shorter-than-expected `duration_frames` in summary

### Failure Mode 4: No Targets (Scenario Ended Early)
**Symptom**: All targets despawn (e.g., challenge completed mid-video)
**Current Handling**: `n_targets` drops to 0; `pan` becomes 0; `segment_by_valleys` may detect spurious "flicks" in noise
**Impact**: May flick count inflated by 1 if noise passes prominence threshold (see M-3 all-zero speed case)
**Detection**: Check `n_targets` distribution; should be non-zero for most frames

---

## Summary

### Health Status: **Green-Yellow**

The flicking mainline algorithm is fundamentally sound. All fairness metrics implement their theoretical anchors correctly. Valley segmentation is robust to fast consecutive flicks. The 5a5bb84 fix accurately documents the `submovement_overlap` naming issue.

**Two High-severity issues remain from 07-08:**
1. **H-1** (VideoCapture try/finally) — resource leak on Windows, could block file operations
2. **H-2** (zero-kill CSV) — valid KovaaK's scenario crashes with opaque error

These are straightforward defensive fixes, not algorithmic redesigns.

**Counts**: 0 Critical / 3 High / 3 Medium / 3 Low

---

## Top 3 Actionable

1. **H-1**: Add try/finally to `compute_pan_trajectory` (pan_tracker.py:141-184). This is the last VideoCapture site without resource cleanup — a known Windows file-lock issue.

2. **H-2**: Guard zero-kill CSV in `analyze_flicking_fair_summary` / `analyze_flicking_video`. A valid KovaaK's scenario (0 kills) currently crashes with `ValueError: cannot convert float NaN to integer`.

3. **M-1**: Add empty-flick warning to `_summarize_reference`. Distinguish "no flicks detected" from "healthy session with zero flicks" in the summary.

---

## Mandatory Items Status (07-08 → 07-09)

| Item | 07-08 Status | 07-09 Status | Notes |
|------|--------------|---------------|-------|
| H-1: VideoCapture try/finally | Unfixed | **Still Unfixed** | Exact location: pan_tracker.py:141-184 |
| H-2: Zero-kill CSV | Unfixed | **Still Unfixed** | Exact locations: pan_tracker.py:222, 386 |

Both items are **High** severity and have exact fix approaches. Neither requires algorithmic changes — both are defensive guards for edge cases.

---

## Recommendations

### Immediate (before next release)
1. Fix H-1 (try/finally) — 5 lines, eliminates Windows file-lock risk
2. Fix H-2 (zero-kill CSV) — 6 lines, eliminates crash on valid input

### Short-term (next sprint)
3. Add M-1 (empty-flick warning) — 3 lines, improves user feedback
4. Document M-3 (SPARC DC exclusion) — 2 lines, clarifies theoretical deviation

### Long-term (v2 planning)
5. Delete old pipeline (aligner.py + legacy flicking functions) — ~470 lines, zero usage
6. Add unit tests for `segment_by_valleys`, `_segment_sparc`, `_submovement_structure` edge cases
7. Calibrate `advice.THRESHOLDS` on real user data and document source
