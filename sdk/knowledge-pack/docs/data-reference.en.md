# Aiming Cookie Data Reference (for knowledge pack authors)

> 中文版：[data-reference.md](data-reference.md)
> Version: 2026-09-20 · Audience: knowledge pack authors (coaches / content creators / top-ranked players)
> Purpose: explains, for every kind of data your pack consumes, "where it comes from, how it is captured, what it reflects, and how the analysis pipeline uses it". Once you understand the data, you know whether each rule and each explanation you write can stand.
> **Data boundary statement (red line)**: this document describes **local machine data only** — all capture, parsing, and analysis happens on the user's own computer. This document contains, and the product has, no network-egress / upload data path at all; the Coach's LLM requests carry only the projection-layer summaries described in section 8, never any raw data itself.
> Source of truth: the `kovaak_tracker/` analysis pipeline code, `docs/PRD.md` §5.7, `docs/ARCHITECTURE.md`.

---

## 0. The data journey of one practice run (overview)

After a player finishes a round in KovaaK's, Aiming Cookie turns the data into knowledge in this order:

```text
While the game runs (inside the KovaaK process gate)     After the round ends
├─ Raw Input mouse input stream (1)                      ├─ Stats CSV arrives (2) → scenario identity + outcome
├─ MP4 replay ring buffer recording (4)                  ├─ Performance .perf arrives (3) → event stream + UTC anchor
                                                         ├─ Time alignment: all three anchored to the same
                                                         │  "challenge window"
                                                         ├─ Visual preprocessing (5, optional): MP4 → target/crosshair
                                                         │  numeric evidence
                                                         ├─ External telemetry import (6, optional): supplementary
                                                         │  evidence on the target-trajectory side
                                                         ▼
                                             Derived metric computation (7): input kinematics / family metrics
                                                         ▼
                                             Analysis projections L1-L3 (8): overview / metrics / events / evidence
                                                         ▼
                                             Signals and observations (9): mapping rule matching + registry
                                             knowledge annotation
                                                         ▼
                                             Coach explanation / diagnosis / training recommendations
```

The bracketed numbers correspond to the nine data kinds below.

---

## 1. Raw Input trace (raw mouse input stream)

- **Source**: the Windows Raw Input API, captured by the desktop native layer.
- **How it is captured**: only while KovaaK runs in the foreground (process gate) and only after the user explicitly enables the feature; it records only relative movement `dx/dy`, the timestamp `timestamp_ms`, and mouse buttons `buttons` — **no keyboard, no desktop absolute coordinates**. Raw Input is off by default; the first enable must clearly tell the user what is collected, why, and how to turn it off.
- **Normalization terms (ACRI v2, frozen 2026-08-04)**: regardless of the mouse hardware's polling rate, the canonical motion time granularity is fixed at 1 ms and at most 1000 Hz — `dx/dy` within the same millisecond are summed into at most one motion record, and no zero-padded records are generated; mouse button press/release edges are not subject to that cap and keep their order. This normalization preserves per-millisecond net X/Y displacement and **deliberately does not preserve sub-millisecond path shape**; the product does not describe it as a hardware polling-rate measurement or as sub-millisecond motion evidence.
- **What it reflects**: the single source of truth for the player's hand motion — input kinematics. Speed, acceleration, deceleration fraction (decel_frac), smoothness (SPARC), path efficiency, and every other input-side metric are computed from this raw denominator.
- **How the pipeline uses it**: at analysis time the challenge window is cut as a half-open interval `[start_ms, end_ms)` (`kovaak_tracker/time_alignment.py`) and the trajectory points inside the window are replayed one by one; each analyzer (e.g. `kovaak_tracker/native_flicking_analysis.py`) segments individual flicks on the trajectory and computes kinematic metrics. A Run without Raw Input takes the degraded path (see section 10 on evidence tiers) and cannot produce input-side conclusions.

## 2. KovaaK Stats CSV (result statistics)

- **Source**: the stats file exported by the KovaaK's game itself; discovered automatically by the product, with manual import also supported.
- **How it is captured**: file watching; once KovaaK writes the file, the product parses it (`kovaak_tracker/csv_parser.py`).
- **Content structure**: a single CSV with four blocks —
  1. **Kill table**: one row per kill, fixed 13 columns (Kill #, Timestamp, Bot, Weapon, TTK, Shots, Hits, Accuracy, Damage Done/Possible, Efficiency, OverShots, Cheated);
  2. **Weapon summary row**: per-weapon aggregation of shots / hits / damage across the whole run;
  3. **Summary block**: `Key:Value` whole-run aggregates (Kills, Deaths, Avg TTK, Total Overshots, etc.), where **Challenge Start is the wall-clock anchor** — converting the kill table's `HH:MM:SS.mmm` timestamps into seconds within the scenario depends entirely on it;
  4. **Input configuration block**: FOV, DPI, Sens, resolution, etc.
- **What it reflects**: outcome-layer facts — the result of every shot (killed or not, seconds taken, accuracy, overshoot), plus scenario identity and player settings.
- **How the pipeline uses it**: the kill table provides TTK distribution, accuracy, overshoot, and other outcome metrics plus their time series; the summary block's Challenge Start participates in time alignment; the input configuration feeds the settings context (e.g. `cm_per_360` on the `settings` channel). Bot/Weapon names are untrusted text, used only for grouped display, never for diagnosis.

## 3. KovaaK Performance (.perf event stream)

- **Source**: `.perf` performance files exported by KovaaK's; auto-discovered like Stats.
- **How it is captured**: file watching; a parser shipped inside the product (`kovaak_tracker/performance_parser.py`) reads the binary protobuf directly (field mapping adapted from RefleK's GPL-3.0 implementation), skipping unknown fields for forward compatibility.
- **What it reflects**: event-layer facts — a time-ordered stream of shots / hits / kills / score deltas, plus the scenario name, scenario hash, and **`challenge_start_utc`** (the UTC anchor of the canonical time window).
- **How the pipeline uses it**: cross-checks the Stats Challenge Start, and together they align Raw Input, video, and events onto the same challenge window; the event stream is the source of per-event evidence such as "which shot missed, when the target changed", and it is the skeleton of the per-row evidence tables in family analysis (dynamic clicking / tracking / target switching).

## 4. Run-owned MP4 (managed replay video)

- **Source**: Windows Graphics Capture, capturing only the KovaaK window.
- **How it is captured**: recorded continuously inside the process gate (GPU hardware-encode path with a three-level degradation contract), maintaining a **bounded buffer of the most recent 300 seconds** — not an infinite screen recording; after Stats / Performance arrive, the window is cut retrospectively according to the time-alignment result, keeping only the segment matching the challenge window. A permanent MP4 is produced only for rounds with `Pause Count = 0` (a paused round's timeline is untrustworthy and does not enter the evidence chain).
- **What it reflects**: what actually happened on screen — intuitive replay and visual evidence for locating problems.
- **How the pipeline uses it**: two uses. First, the user can click to replay and locate a specific mistake; second, it is the input for local visual preprocessing (kind 5). Video is **auxiliary** evidence: basic kinematics treats Raw Input as the source of truth, and video does not participate in input-kinematics computation.

## 5. Local deterministic visual signals (CV preprocessing)

- **Source**: the Run-owned MP4, preprocessed by local deterministic algorithms.
- **How it is captured**: a local CV pipeline (`kovaak_tracker/vision.py`, `kovaak_tracker/visual_signals.py`) — **model-free, deterministic algorithms**: target detection, crosshair detection, target-to-crosshair relative error, event numerification. Every step carries a quality gate: low confidence, occlusion, missing frames, and similar cases are explicitly annotated (the `low_confidence_or_occluded`, `target_occlusion`, `target_identity_unresolved` limitation tokens in the vocabulary) rather than force-computing a number.
- **What it reflects**: target-relative geometry (error in pixels), hit association, target identity and speed — the "on-screen" facts that input data cannot provide.
- **How the pipeline uses it**: fully enabled only on **precisely reviewed scenarios** (exact reviewed hash entries of the scenario registry); other scenarios keep the degraded tier with quality limitations attached. Author's note: many of the visual tokens in the vocabulary's `limitation_tokens` (e.g. `click_geometry_visible_radius_conditioned`) are this pipeline's honest annotations — family rules commonly use them in `blocking_limitations` as the gate for "don't conclude when the evidence isn't good enough".

## 6. ExternalTelemetryRun (external telemetry import, optional channel)

- **Source**: data produced by an external RPM (ReadProcessMemory) sampling pipeline that the user runs themselves.
- **How it is captured**: after the user configures a local watch root directory, the product **read-only imports** the cleaned output (`cleaned/round_NN.jsonl`); the product does round segmentation and track reconstruction, and the imported artifact is the `external_run.v1` format (contract in `docs/EXTERNAL_TELEMETRY_IMPORT.md`, cleaner in `telemetry_capture/cleaner.py`). This channel is unconfigured by default and does not affect the main chain.
- **What it reflects**: supplementary evidence on the target-trajectory side — T2K (target-to-crosshair) distribution, target spawns/deaths/timeouts, etc.
- **How the pipeline uses it**: it supplements "what the target is doing" measurement for tracking / target-switching scenarios; its scenario labels are proposals only and do not enter the reviewed scenario registry directly. Without this channel, the related conclusions are treated as unavailable and annotated.

## 7. Derived metrics (the analyzer output vocabulary)

- **Source**: the raw data kinds 1-6 above, computed by the deterministic Python pipelines of each family analyzer (`static_clicking` / the `continuous_tracking` family / `target_switching` / `dynamic_clicking`).
- **How it is captured**: not captured independently — it is a computed product. Every metric carries `metric_version` (algorithm version), provenance (source chain), `availability` (whether usable), and `limitations` (limitation annotations).
- **What it reflects**: intermediate-layer measurement facts. The main family vocabulary:
  - `static_clicking.*`: decel_frac, SPARC, path_efficiency, reverse_ratio, peak_position_pct, submovement_overlap, etc. (flick kinematics);
  - `continuous_tracking.*`: phase_lag_ms, loss_count, reacquisition_latency_ms, sparc, time_in_radius_ratio, etc. (tracking);
  - `target_switching.*`: transition_time_ms, settle_duration_ms, terminal_correction_ratio, etc. (target switching);
  - `dynamic_clicking.*`: normalized_click_error, acquisition_time_ms, relative_velocity, etc. (dynamic clicking).
  The complete list is the official vocabulary's `metric_keys` (`knowledge/mapping/vocabulary.v1.json`).
- **How the pipeline uses it**: this layer is your pack's **input**. The static rules' `summary` channel is shaped like `{metric_name: {med: median, metric_version: version}}`; the `metrics` / `baseline_metrics` that family rules compare also come from this layer. The Chinese display names and neutral descriptions of metrics are provided by `kovaak_tracker/metric_definitions.py` — it is a **display-only dictionary** with no good/bad direction; the good/bad direction claim always belongs to your knowledge entries (with a claim_level).

## 8. L1-L3 projections (the Coach-visible layer)

- **Source**: the analysis output of all data above.
- **How it is captured**: after analysis completes, the product writes the results as versioned, field-whitelisted, budget-capped projection files (`webapp/backend/analysis_output.py`): under `analyses/<id>/`, `overview.json` (diagnostic overview, with signals and knowledge references), `metrics.json` (metric summary), `events.json` (events), `evidence.json` (evidence locations).
- **What it reflects**: the analysis's **conclusions and evidence index**, not the raw data itself.
- **How the pipeline uses it**: the Coach (LLM) and the frontend consume only this projection layer. **L0 (the raw trace itself, raw CSV/protobuf, MP4 bytes, file paths) never enters a Provider request**. What this means from the author's perspective: the signal your mapping rule matched, and the way your registry entry got cited, all happen at the projection layer; and the only pack content that enters the Coach's context is entry prose and indexes — never any raw data.

## 9. Signal and observation vocabulary (the product's stable contract)

- **Source**: defined by the product and exported frozen with the code (`knowledge/mapping/vocabulary.v1.json`).
- **Contents**: `signals` (phenomenon names like "decel_frac high", "sparc low"), `metric_keys` (metric keys), `observation_refs` (observation objects like "metric.terminal_control", "event.switch_chain") — these three sets are **append-only** for authors (hard contract); `limitation_tokens`, `row_fields`, etc. are product-owned (renaming counts as breaking).
- **What it reflects**: the **matching language** between the measurement side and the knowledge side. The signal a mapping rule emits, the signals/metric_refs a registry entry declares, and the observation_ref a diagnostic annotation writes can only line up through this one shared vocabulary.
- **How the pipeline uses it**: import validation forces your pack to stay inside this vocabulary (SPEC section 5, rule 4); at runtime, signal/metric/observation are what connect "a measured phenomenon" to "a knowledge entry's explanation". Read the vocabulary once before writing your pack and you basically know what the product "can see".

---

## 10. Evidence-tier semantics (what each tier may and may not claim)

Every analysis automatically picks its path by the **highest available** evidence tier; the user does not choose manually. The tier decides what conclusions that analysis is **entitled to claim**:

| Tier | Data composition | May claim | May not claim |
|---|---|---|---|
| `multimodal` | Stats + Performance + Raw Input + managed MP4 + canonical window | Input-kinematics conclusions + visual evidence + full Coach consumption | —— (highest tier) |
| `input_native` | Stats + Performance + Raw Input + canonical window (no video) | All input-side conclusions | Any visual evidence: may not claim target-relative error, visual reaction moments, or video evidence |
| `video_fallback` | Stats + managed MP4 (no Raw Input; includes imported historical KovaaK data) | Outcome-layer observations, intuitive replay | Any input-kinematics conclusion |

The bottom line: **a lower-tier path may only claim what its sources actually support**; no tier may fabricate target-relative error, visual reaction moments, or video evidence. This is why the vocabulary's `limitation_tokens` exist — every degradation and every quality gap is explicitly annotated, and your family rules can reference those annotations via `blocking_limitations`, turning "insufficient evidence" into "this rule does not trigger".

## 11. The "thresholds not yet calibrated against product data" statement (required reading for authors)

The product's honest position on the static-clicking threshold family (THRESHOLDS, e.g. `decel_frac > 0.65`, `sparc < -5.0`, `cm_per_360 < 25`) is: **these numbers come from heuristics in the initial research draft, not from health ranges calibrated against product data** (the THRESHOLDS comment and `_finalize_uncalibrated_findings` in `kovaak_tracker/advice.py`).

Three practical consequences for authors:

1. **Do not assume absolute healthy ranges**. All official rules carry the `threshold_requires_product_calibration` limitation; any absolute statement of the form "below X means failing" does not hold. Thresholds should be framed as "hypotheses that trigger a controlled experiment".
2. **The current runtime uniformly finalizes static signals**. Regardless of the declared severity / claim_level, a static rule's evaluation result is currently rewritten to `severity=info`, `claim_level=experimental`, `limitations=[threshold_requires_product_calibration]` (SPEC 6.7 item 3). Write your threshold declarations truthfully and keep the limitation — they take effect only once calibration is unlocked.
3. **The official rules run on the same engine and the same finalize pass as your pack**. Follow the official pack `knowledge/mapping/official.v1.json` as the model: state in the diagnostic sentence that "this threshold still needs calibration against real product data", and leave absolute verdicts to retesting.
