# Task 12 Field Blocker Remediation - Implementation Plan

> **状态：implementation complete / Task 12 release Gate remains active（2026-07-27）。** 点点已授权 Task 1–5 分配给 terra-xhigh subagents 并行执行；本计划不提交、不推送，不修改 PRD / Architecture。

**Goal:** 收敛 Task 12 真实 `Run 479` 暴露的 Analysis Data、video-fallback、Tasks 读模型、capture 长运行日志与 Tracking CV 调度问题，不改变已验证的分析结果。

**Architecture:** 数据页通过 owner-scoped `frontend_analysis_data.v1` 只读投影消费 immutable evidence，仅暴露相对时间、聚合事件和不可逆的目标相对误差序列。video-fallback 的视频可用性与 EvidenceSegment 是否存在分离；Tasks 和 capture 只修正已有状态机的错误分类/生命周期。OpenCV 只在 secret-free 一次性 visual child 内限制为不超过逻辑核心数且最多 16 线程，不修改 detector、CSRT、帧率、采样或产物合同。此前 8 / 16 线程的失败 preflight 实际使用了 Python 3.9 / OpenCV 4.13，与 production runtime 不一致，不能据此归因于线程数；恢复 production `.venv` 的 Python 3.11 / OpenCV 5.0 后，16 线程三轮均恢复 reference 字节 parity。

**Tech Stack:** FastAPI / Pydantic / SQLite / pytest，Next.js / React / TypeScript / Playwright，Tauri / Rust，OpenCV。

---

## Task 1 - Versioned Analysis Data projection and truthful Data UI

### Allowed files

- `webapp/backend/read_models.py`
- `webapp/backend/routes.py`
- `webapp/backend/schemas.py`
- `webapp/backend/evidence_store.py` only if the existing owner/revision loader needs a small reusable read helper
- `webapp/tests/test_capability_contracts.py`
- `webapp/tests/test_routes.py`
- `webapp/tests/test_evidence_store.py`
- `webapp/frontend/lib/api.ts`
- `webapp/frontend/lib/types.ts`
- `webapp/frontend/lib/contracts.ts`
- `webapp/frontend/components/task5/AnalysisWorkspace.tsx`
- `webapp/frontend/components/task5/DataView.tsx`
- `webapp/frontend/components/task5/VideoView.tsx` only for shared event-marker consumption
- `webapp/frontend/components/task5/task5.module.css`
- Task 5 focused frontend tests / fixtures / E2E only

### Tests first

1. 写失败合同测试：`frontend_analysis_data.v1` 必须 owner-scoped、path-free，不含 Raw trace、完整 processed rows、原始 samples、screen/capture coordinates、artifact path/ref 或 secret。
2. 用 Tracking artifact 验证非空 `event_markers` / `event_distribution`；marker 最多 128 个，只含 stable kind/ref 与 challenge-relative `relative_ms`。
3. 投影最多 120 点的 `target_relative_error_radius` 序列；值由 crosshair/target/radius 在后端派生、归一化与量化，不返回原坐标。缺 channel 时返回明确 unavailable reason，不猜值。
4. analysis-scope limitations 只在页面显示一次；metric 行只保留其独有 limitation。已知英文技术限制转为中文主文案，原文最多放在单个可展开技术区。
5. Data 使用真实 distribution 和 error series；点击事件切到 Video 并 seek 到同一相对时间。
6. 从 Analysis Data 删除固定 Trend 空模块；History 既有 comparability 路径保持不变。无安全轨迹时只显示紧凑局部原因，不渲染大号伪图。

### Stop rule

- 需要暴露 screen/capture coordinates、Raw Input points、原始 CSV/protobuf、完整 processed rows 或 artifact 路径；
- 需要创建通用图表框架、新数据仓库或把 History trend 复制回 Analysis；
- 投影无法在已提交 artifact revision 与 owner 绑定下验证。

## Task 2 - Decouple managed video playback from EvidenceSegment presence

### Allowed files

- `webapp/backend/routes.py`
- `webapp/backend/schemas.py` only if the existing response needs a backward-compatible availability field
- `webapp/tests/test_routes.py`
- `webapp/frontend/components/task5/VideoView.tsx`
- `webapp/frontend/lib/api.ts`
- `webapp/frontend/lib/types.ts`
- Task 5 focused frontend / Desktop tests

### Tests first

1. done video-fallback Analysis with seekable Run-owned MP4 and no derived artifact returns `frontend_evidence_segments.v1` with `video_availability=available` and `segments=[]`, not 404.
2. An Analysis that declares a derived artifact but fails owner/revision/integrity validation still fails closed; it must not be flattened into an honest-empty segment list.
3. `VideoView` derives playback from Analysis MP4 availability and managed URL; EvidenceSegment failure only removes segment overlays. Media 410/onError remains a local unavailable state and does not fail the whole Analysis.
4. Browser range route and real Tauri `aiming-cookie-media` URL both load video-fallback MP4; native-only still renders no empty player.

### Stop rule

- 需要暴露 Run 绝对路径、放宽 owner 校验或把 artifact 损坏伪装成空 segments。

## Task 3 - Repair Tasks failure domain and terminal timestamp consistency

### Allowed files

- `webapp/backend/queue.py`
- `webapp/backend/db.py`
- `webapp/backend/read_models.py`
- `webapp/tests/test_queue.py`
- `webapp/tests/test_db.py`
- `webapp/tests/test_capability_contracts.py`
- `webapp/tests/test_worker.py` only if current phase/domain assignment needs a focused assertion

### Tests first

1. stale lease exhaustion no longer hard-codes `network`; the current local analysis/runtime failure maps to the existing frozen `kinematics` domain and clears the nonterminal `task_phase` without inventing a new domain.
2. `recover_stale_jobs(now=...)` separates comparison time from write time. Marking a nonterminal stale Analysis failed always writes the real clock and cannot persist the injected comparison timestamp.
3. Session `error.category` and Tasks `failure.domain` remain semantically compatible for source/alignment/kinematics/video/provider/coach/network paths.
4. retry attempt history preserves original failure and uses the child attempt's actual timestamps.
5. v19 migration repairs only failed `stale_lease_exhausted` rows whose equal `finished_at` / `updated_at` are more than 24 hours in the future: `finished_at` becomes honest null and `updated_at` becomes migration time. Other future/different-code/history rows stay unchanged.

### Stop rule

- 需要扩大冻结 failure-domain enum，改写已成功的历史 Analysis，或针对隔离 DB 中的手工测试值添加通用 migration。

## Task 4 - Long-run capture log correctness and isolated OpenCV scheduling

### Allowed files

- `webapp/backend/kovaak_capture_finalizer.py`
- `webapp/backend/native_capture_client.py` only if typed status classification is incomplete
- `webapp/backend/visual_worker_process.py`
- `webapp/tests/test_kovaak_capture_finalizer.py`
- `webapp/tests/test_visual_worker_process.py`
- `webapp/tests/test_worker.py` only for child-boundary regression
- `webapp/frontend/src-tauri/src/capture_coordinator.rs` and its focused tests only if Python diagnosis proves the native response code is wrong
- `docs/superpowers/plans/2026-07-26-tracking-cv-performance-repair.md` for measured evidence only

### Tests first

1. Diagnose the three long-run `capture_export_failed` calls by operation/state before changing logging. Expected disabled/absent/shutdown states do not emit warning/traceback loops; a real native export/finalization failure remains visible with its typed code.
2. Repeated finalizer polling while capture is disabled and KovaaK absent is side-effect free and log-clean; shutdown ordering remains consumer-first.
3. Add a child-only OpenCV configuration helper with limit `min(16, logical CPU count)`; inject fake cv2/CPU-count inputs to prove the current 16-core host calls `setNumThreads(16)` and lower-core hosts are not over-provisioned. Parent runtime and other analyzers are untouched.
4. Child output, quality, 3,600 observations, coverage and normalized artifact stay byte-identical to the Run 479 reference.
5. Run 479 real benchmark three times after warm-up; target median end-to-end `<=130s`, no heartbeat/API stall, no orphan child. A miss is reported, not hidden by changing detector/sample semantics.

### Stop rule

- 需要降低帧率、隔帧插值、改 detector/CSRT/ROI、更改任何度量结果，或抑制真实 native failure。

## Task 5 - Integrated gates and documentation closeout

### Allowed files

- focused tests already authorized above
- `docs/PROGRESS.md`
- `docs/superpowers/plans/2026-07-20-complete-coach-analysis-context-v1.md`
- this plan

### Verify

1. Run Task 1–4 focused tests first, then full Python with nonexistent `KOVAAK_INSTALL_DIR`, full frontend unit/E2E, Coach Node tests, Rust fmt/check/tests and `git diff --check`.
2. Reuse isolated `Run 479`; do not ask the user to capture again. Validate Analysis Data at 1280x820 / 960x640, multimodal EvidenceSegment seek, video-fallback playback, owner/410 failure, retry history and long-run/clean shutdown logs.
3. Record exact CV wall time, artifact digest/quality/coverage, test counts, screenshots, process/port cleanup and remaining Static/Dynamic/Switching/high-polling/AMD/Intel/Provider/release Gates.
4. Task 12 remains No-Go unless every pre-existing cross-family and hardware Gate passes; this repair plan may complete while Task 12 remains in progress.

### Stop rule

- 任一 privacy/owner/artifact-parity Gate 失败；
- 实机验证需要新采集、改变冻结产品语义或提交私人 Raw/MP4/Stats/Performance。

## 2026-07-27 execution closeout

- Task 1：`frontend_analysis_data.v1`、真实 event distribution 与 120 点目标相对误差已接入；limitations 去重/中文化，内部 metric/source code 不再直接显示，unavailable 指标折叠保留检查入口。Run 479 的 1280x820 / 960x640 页面无横向滚动。
- Task 2：video-fallback 可在无 EvidenceSegment 时播放 managed MP4；artifact owner/revision/integrity 失败继续 fail-closed，缺失媒体保持局部 HTTP 410。
- Task 3：Tasks failure domain、真实写入时间、attempt 历史与精确 v19 migration 已通过回归。
- Task 4：production Python 3.11 / OpenCV 5.0 三轮为 `147.242s / 151.134s / 148.039s`，中位数 `148.039s`；三轮 artifact 均与 reference SHA-256 `11e0d312...43ea439` 字节一致，quality `accepted`、3,600 observations、coverage `99.944% / 100%`。`<=130s` 目标未达到。真实 Tauri 产品节奏 40/40 capture status available，正常关闭四类错误计数均为 0；50ms 人工压力的 `1/120` transient unavailable 保留为非阻塞残余。
- Task 5：Python `1301 passed, 5 skipped`；Coach `75 passed`；Frontend adapter/source/Browser 为 `7 / 44 / 47 passed`（3 Desktop-conditional skipped），14 张 screenshot baseline 通过；MSVC Rust `73 passed, 7 ignored`，fmt/check/clippy 通过。真实 Tauri READY、path-free managed media 410、Settings 与进程/端口清理通过。
- 本 remediation plan 可视为完成，但 Task 12 仍因 Static / Dynamic / Switching、高 polling-rate、AMD/Intel、真实 Provider/OAuth、真实 worker restart 与发布工程保持 No-Go。
