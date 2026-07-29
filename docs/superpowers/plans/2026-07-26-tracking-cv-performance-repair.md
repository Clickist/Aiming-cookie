# Tracking CV Performance Repair - Implementation Plan

> **状态：Task 1 completed and field-verified；Task 2 未激活（2026-07-27）。** 不提交、不推送。Task 2 只能在量化视觉质量 Gate 通过后激活；当前没有足够 annotation 证据，不进入 production registry。

**Goal:** 在不降低现有 Tracking 分析质量与合同语义的前提下，消除桌面实机 45 分钟级 CV 退化和 API/heartbeat 阻塞，并验证可进一步加速的新 producer 候选。

**Architecture:** Task 1 将现有、已审核的 `run_visual_preprocessing` 放入一次性、secret-free Python 子进程；父 worker 继续维护 SQLite lease/heartbeat、API 与 retry，子进程只消费冻结 job snapshot 并返回同一 `visual_signal` JSON。Task 2 保留现有 producer 为 reference，用半分辨率逐帧 tracker 加全分辨率小 ROI 中心校正建立新版本；没有 annotation quality 证据时不得进入 production registry。

**Tech Stack:** Python 3.11、asyncio subprocess、OpenCV、SQLite queue/lease、pytest、Windows Tauri field harness。

---

## Task 1 - Isolate reviewed visual preprocessing

**Status:** completed and field-verified on 2026-07-27. The production `.venv` uses Python 3.11 / OpenCV 5.0 and `min(16, logical CPU count)`. Saved Run 479 completed three end-to-end repetitions in `147.242s / 151.134s / 148.039s` (median `148.039s`); every artifact was byte-identical to Analysis 3 (SHA-256 `11e0d312...43ea439`, `863,126` bytes), with quality `accepted`, 3,600 observations and target/crosshair coverage `99.944% / 100%`. API probes remained responsive, heartbeat stayed current, and the one-shot children exited without orphans. The `<=130s` target was not met.

**Allowed files:**

- create `webapp/backend/visual_worker_process.py`
- `webapp/backend/worker.py`
- create `webapp/tests/test_visual_worker_process.py`
- `webapp/tests/test_worker.py`
- this plan and `docs/superpowers/plans/README.md`

**Tests first:**

1. Parent sends one bounded JSON request and receives the unchanged visual result.
2. Child environment omits `AIMING_COOKIE_DESKTOP_TOKEN` and native capture address/secret.
3. Waiting for CV does not block an event-loop ticker or the existing heartbeat task.
4. `SourceSnapshotChangedError` and `VisualPreprocessingUnavailable.code` survive the process boundary; unknown failure stays generic and path-free.
5. Cancellation/desktop shutdown terminates and waits for the CV child; no orphan remains.
6. All reviewed visual call sites use the isolated helper; legacy video fallback is unchanged.

**Implementation:**

- Child stdin: one JSON object containing the queue job projection required by `run_visual_preprocessing`.
- Child stdout: exactly one `{ok,result}` or `{ok:false,error:{kind,code}}` JSON object; stdout contains no logs.
- Parent uses `asyncio.create_subprocess_exec`, sanitized environment, piped stdin/stdout and inherited redacted stderr.
- Parent cancellation calls `terminate()`, waits with a short bounded grace, then `kill()` only if necessary.
- No timeout is added to Analysis semantics; queue heartbeat/retry remains the source of recovery truth.
- Field follow-up: for Continuous Tracking only, the same one-shot child also runs the existing `run_continuous_tracking_analysis` after reviewed CV and returns both unchanged results. This avoids the verified desktop parent-thread stall without changing detector resolution, quality gates, metrics, or public DTOs.

**Verify:** focused worker/process tests, adjacent queue/runtime tests, full Python gate, compileall, `git diff --check`, then isolated Tauri field replay of the saved Run. Gate: exact producer result parity, API health remains responsive, heartbeat gap is below lease TTL, wall time returns to the standalone 2–3 minute range.

**Stop rule:** result parity changes, child needs desktop/capture secret, source revision errors are flattened, shutdown can orphan the child, or field runtime still exceeds 5 minutes.

## Task 2 - Candidate half-resolution tracker with full-resolution ROI correction

**Status:** not activated; Task 1 field Gate passed, but the required annotation-quality evidence is still unavailable.

**Allowed files:** `kovaak_tracker/visual_signals.py`, reviewed producer registration in `webapp/backend/worker.py`, focused visual/worker tests, small redistributable annotation fixtures, this plan/Progress Gate evidence.

**Tests first:** full-frame reference comparison, target absence/occlusion/re-entry, ROI false-positive rejection, center/radius/identity/coverage metrics, exact selector/version fail-closed, and deterministic runtime benchmark.

**Activation Gate:** candidate must pass the same annotation protocol with center median `<=4px`, center P95 `<=7px`, radius error `<=2px`, false-positive rate `<=0.05`, identity switch rate `<=0.01`, coverage `>=0.95`; it must not be activated from reference-relative numbers alone.

**Current evidence:** the production reference now has three exact-parity end-to-end measurements with median `148.039s`. An offline saved-Run experiment gives 100% temporal coverage; ROI center correction is accepted on `95.61%` of tracked frames and lowers reference-relative center P95 from `7.16px` to `3.87px`. Its estimated single-path time is about `63s` versus `128.48s` in the same microbenchmark. These are candidate/reference-relative numbers, not annotation-based production quality proof.

**Stop rule:** annotation fixtures are unavailable, any quality metric regresses beyond the active profile, identity/re-entry must be guessed, or the optimization requires changing Analysis/Coach/public DTO semantics.

## Task 3 - Exact-parity production-path optimization

> **Status: completed at the Stop rule on 2026-07-29 with no code change.** This Task profiled only the current reviewed full-resolution production path. Task 2 remains unactivated; reference-relative half-resolution/ROI results cannot enter production without the independent annotation Gate.

### Allowed files

- `kovaak_tracker/visual_signals.py`
- `webapp/backend/worker.py`
- `webapp/backend/visual_worker_process.py` only for proven process-boundary overhead
- `tests/test_visual_signals.py`
- `webapp/tests/test_worker.py`
- `webapp/tests/test_visual_worker_process.py`
- this plan and `docs/PROGRESS.md`

No Coach, Switching, Registry/Manifest, capture, DB/routes/schemas, frontend, public DTO or private source file may change.

### Tests first and measurement

1. Profile the saved Run 479 path before editing and separate decode, color conversion, detector, CSRT update, analysis, serialization and process startup costs.
2. Add an exact-parity regression for any proposed optimization: identical observations, quality result and final artifact bytes for the reviewed input.
3. Prefer eliminating duplicate decode/conversion/copies/passes, pathological thread scheduling or redundant serialization. Do not lower FPS, resolution, detector/CSRT settings or quality thresholds.
4. Benchmark at least three production-path repetitions after implementation and record wall time, CPU and peak memory. A single warm-cache run does not replace the median.

### Stop rule

- No dominant cost can be removed without changing reviewed observations or detector behavior.
- Output/artifact parity changes, quality evidence decreases, or source/retry/cancellation semantics change.
- The only speedup requires activating Task 2 without annotation evidence.
- Private frames, media, Raw or absolute paths would need to enter Git, logs or a public contract.

### Task 3 closeout

- Read-only Run 479 profiling measured the 16-thread reviewed visual producer at `118.826s`: CSRT update `105.404s / 3597 calls` (`88.7%`), decode `11.949s / 3601 reads` (`10.1%`), detector `0.051s / 3 calls` and color conversion `0.023s / 3 calls`. Tracking analysis was `2.061s` and result JSON serialization `0.023s`.
- The visual signal, event and sample components plus quality summary exactly matched Analysis 3. The only safe scheduling candidate, OpenCV single-thread mode, also kept exact parity but regressed visual time to `170.665s` with `157.052s` in CSRT, so it was rejected.
- The bounded production protocol measured child import at `0.196s`, CV plus Tracking at `127.496s`, a `1.841 MB` evidence request encode at `0.025s`, and the evidence child at `9.289s` (`7.868s` in-process build/write). No duplicate media decode/conversion or material import/serialization overhead remained to remove.
- A copied-database queue trace hung after CV and was terminated at `364s`; the standalone production protocol completed, so the isolated harness timeout is not treated as product timing evidence. All trace processes were cleaned. The unchanged three-run production baseline remains `147.242s / 151.134s / 148.039s` (median `148.039s`); no changed-code three-run result exists because no safe code change was accepted.
