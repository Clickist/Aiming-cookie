# RefleK Capability Adoption — Implementation Plan

> 状态：active（点点已在 2026-07-13 当前会话明确授权按顺序执行 Task 1–7，并允许不冲突的 Terra subagents 并行开发）。
> 上游 assessment：[`../assessments/2026-07-13-reflek-capability-adoption.md`](../assessments/2026-07-13-reflek-capability-adoption.md)
> 依赖 specs：[`../specs/2026-07-13-kovaak-run-trace-lifecycle-design.md`](../specs/2026-07-13-kovaak-run-trace-lifecycle-design.md)、[`../specs/2026-07-13-analysis-evidence-coach-context-design.md`](../specs/2026-07-13-analysis-evidence-coach-context-design.md)
> 本计划不授权复制 RefleK GPL-3.0 源码；实现必须独立编写，许可证边界另行审查。



## 目标

将 KovaaK Stats、Performance、Windows Raw Input 和可选 MP4 统一接入 Aiming Cookie 的：

```text
recoverable Run
  → evidence-aware input mode
  → deterministic AnalysisResult
  → History / evidence replay
  → structured Coach context
```

Benchmark、外部 rank、独立 Dashboard、updater、cloud trace sync 不属于本计划的 P0 实施；它们只能在对应产品合同批准后进入独立 plan。

## 冻结决策

1. `KovaaKRun` 独立于 `Analysis Session`；Run 可无视频、可无分析；Analysis 可引用 Run。
2. Raw Input canonical record 继续是相对 `dx/dy`、时间戳和按钮；累计 `x/y` 只是版本化 derived trajectory。
3. Raw trace 默认关闭、仅 Windows、仅 KovaaK process gate、不进入云端/普通日志/Coach 默认上下文。
4. input-native、multimodal、video-fallback 必须由 versioned result contract 明确表达，不能由前端猜测。
5. Stats/Performance/Raw Input/MP4 是不同 evidence source；一个 source 的推断不能序列化成另一个 source 的测量。
6. History 采用轻列表 + lazy details；不在列表加载完整 events/trace。
7. Coach 只读稳定、结构化、用户可见的 Analysis/Run/History summary；原始 trace 需要单独确认，默认不发送。
8. 未经许可证审查，不复制 RefleK 源代码、前端文件、资源或大段实现。

## Task 1 — Raw Input 与 ingestion correctness

### Allowed files

- `webapp/frontend/src-tauri/src/raw_input.rs`
- `webapp/frontend/src-tauri/src/lib.rs`
- `webapp/backend/kovaak_ingest.py`
- `webapp/backend/desktop_runtime.py`
- `webapp/backend/kovaak_run_store.py`
- `webapp/backend/db.py`
- `webapp/tests/test_kovaak_ingest.py`
- `webapp/tests/test_kovaak_runs.py`
- inline `#[cfg(test)]` tests in `webapp/frontend/src-tauri/src/raw_input.rs` only
- `webapp/frontend/src-tauri/src/lib.rs` tests only if needed for the existing command boundary

### Tests first

- process exit 后在 ingestion grace period 内 trace 仍可配对；
- ingestion failure 不会永久 emitted，下一轮可重试；
- Stats/Performance pair identity 与补全幂等；
- Run + trace attach 崩溃恢复和 orphan reconciliation；
- snapshot write failure 可观察；
- Rust/Python golden fixture 互读；
- snapshot 上限、时间单调性、资源限制；
- high-rate buffer 不在 capture thread 做完整同步快照。

### Stop rule

- 本 Task 不得修改本 plan、spec 或任何 index，也不得自行将 Proposed 标记为 Active；
- 需要改变 AnalysisResult schema；
- 需要改变 Run/Analysis ownership；
- 需要定义 trace retention/delete 但 lifecycle spec 尚未 active；
- 需要 Windows 实机才能决定的行为无法被 fixture/test 覆盖；
- 需要扩大 Allowed files。

## Task 2 — Run / Analysis / evidence contract

> 2026-07-14 点点裁决：为闭合真实 producer 与已冻结 v2 contract 的一致性，
> 本 Task 允许在不改变 Task 3 科学语义或 Task 4 mode dispatch 的前提下，
> 仅修改 `worker.py` / `test_worker.py` 补齐结果归属、版本、metric provenance
> 与 artifact metadata。
> 实施状态：completed（2026-07-14）；后续 Task 3 亦已完成，当前接力点为 Task 4。

### Allowed files

- `webapp/backend/contracts.py`
- `webapp/backend/schemas.py`
- `webapp/backend/db.py`
- `webapp/backend/queue.py`
- `webapp/backend/routes.py`
- `webapp/backend/kovaak_run_store.py`
- `webapp/backend/worker.py` only for v2 producer/contract alignment
- `webapp/tests/test_db.py`
- `webapp/tests/test_contracts.py`
- `webapp/tests/test_routes.py`
- `webapp/tests/test_queue.py`
- `webapp/tests/test_worker.py` only for v2 producer/contract alignment
- no spec/index changes by executor

### Tests first

- `AnalysisResult v2` 三种 input mode 序列化和读取；
- 旧 `analysis_result.v1` 向后兼容；
- Run → Analysis 引用 owner 校验；
- evidence availability/source provenance；
- missing/partial/alignment-failed states；
- API 不泄漏任意本地路径；
- artifact IDs 稳定，源文件移动/删除显示 source unavailable。

### Stop rule

- 需要直接定义未在 PRD/Architecture 中裁决的删除、保留、同步默认值；
- 需要改变 Coach raw trace boundary；
- 需要引入 benchmark/provider identity；
- 需要修改 front-end route without UI spec。

## Task 3 — Input-native analysis adapter

> 2026-07-14 点点裁决：为保证排队与重试期间 Raw trace 不会静默换版，
> 本 Task 额外允许 `kovaak_run_store.py` 冻结 trace fingerprint，并允许
> `test_kovaak_runs.py` 验证该 snapshot contract；不改变 Run/trace ownership、
> retention、删除或 Task 1 lifecycle 语义。
> 实施状态：completed（2026-07-14）；Task 4 尚未开始。
> 2026-07-15 correctness correction：worker 不再“先校验路径、后由 parser 重开路径”，
> 而是对 Stats、Performance、Raw trace 各做一次有界读取，校验该组 bytes 后直接交给
> bytes parser；仅为此增加 `csv_parser.py` 的 bytes 入口与 Raw snapshot bytes decoder，
> multimodal visual validation 复用 native 阶段解析出的同一 Stats 对象，不再在 native
> 校验结束后按路径重读。2026-07-15 进一步修正 SPARC：选中频段的频率轴按 cutoff
> span 归一化，避免同一轮廓随采样间隔漂移；native/video-fallback 分别写入独立 v2
> SPARC metric version，旧版值不得混入趋势，且 v2 在真实数据校准前不触发 legacy
> `-5.0` 绝对阈值；native 同时输出诚实命名
> `trough_depth_ratio` 与下游兼容键 `submovement_overlap`，二者明确是同一谷深 proxy，
> 不冒充 literal temporal overlap。以上修正不复制 Stats/Performance 原文件，也不改变
> ownership、retention 或删除合同。已冻结 source 缺失、不可读或 revision 改变时，worker
> 统一写入不可重试的 `input_validation / source_unavailable`，不再误报可重试内部错误，也不
> 将绝对路径、底层异常或 traceback 写入错误对象与普通日志；恢复方式是重新提交并冻结新 snapshot。

### Allowed files

- `kovaak_tracker/native_flicking_analysis.py` (new)
- `kovaak_tracker/performance_parser.py` only for required event semantics
- `webapp/backend/worker.py`
- `webapp/backend/contracts.py`
- `webapp/backend/kovaak_run_store.py` only for trace snapshot fingerprint
- `tests/test_native_flicking_analysis.py` (new)
- `tests/test_performance_parser.py`
- `tests/coach/test_report.py`
- `webapp/tests/test_kovaak_runs.py` only for trace snapshot fingerprint

### Tests first

- raw `dx/dy` → derived trajectory 单位和 prefix sum；
- stats/performance/raw trace window alignment；
- input-native 不依赖 MP4；
- multimodal 追加 video evidence；
- video-fallback 无 Raw Input 仍通过；
- deterministic metrics 的单位、sample count、coverage 和 missing evidence；
- 不输出 targetInference/未标定 sensitivity heuristic 为事实。

### Stop rule

- 需要把猜测 target 位置当测量；
- 需要依赖未验证的 RefleK heuristic 作为正式诊断；
- 需要修改现有 fair metrics 的科学合同但无上游决策；
- 无法保留 video fallback。

## Task 4 — Worker mode dispatch

> 实施状态：not started（截至 2026-07-14）。
> 新 session 只有在点点明确指定“继续 Task 4”后才开工；不得自动开始 Task 5 或前端任务。
> 2026-07-15 pre-Task correctness correction：现有 run-based path import 已在进入 queued
> 前冻结 MP4 SHA-256 / size / mtime，验证 Analysis managed copy 与 frozen revision 一致，
> 将 fingerprint/checksum 写入 snapshot/result manifest，并让同一路径换版参与幂等冲突；
> 该局部修正不代表 Task 4 的三模式正式验收已开始或完成。

### Allowed files

- `webapp/backend/worker.py`
- `webapp/tests/test_worker.py`
- Task 2/3 已冻结的 contract/adapter 只能消费，不在本 Task 重定义

### Tests first

- `input_native` 不读取或要求 MP4；
- `multimodal` 先保留 native deterministic result，视觉失败只追加 warning；
- `video_fallback` 继续通过既有 MP4 + CSV 路径；
- path-based `multimodal` / `video_fallback` 的 MP4 revision 与 managed copy 必须一致，
  source 变化、消失或 partial copy 时 fail-closed，且 result/audit 不泄露绝对路径；
- fallback 不生成 Raw Input provenance；
- 三种模式均写 `analysis_result.v2`，旧 v1 结果仍可读。

### Stop rule

- 需要改变 Task 2 的 v2 contract 或 Task 3 的科学语义；
- 需要把 source path 写入 result/API；
- 无法保留 legacy video fallback。

## Task 5 — Unified Coach diagnostic context/tools

### Allowed files

- `webapp/backend/coach_context.py` (new)
- `webapp/backend/coach_service.py`
- `webapp/backend/coach_engine.py`
- `webapp/backend/coach_runtime.py`
- `webapp/backend/coach_store.py`
- `webapp/backend/routes.py` / `schemas.py` / `db.py` only for context refs
- `webapp/coach-runtime/src/**`
- `webapp/coach-runtime/test/**`
- `webapp/tests/test_coach_context.py` (new)
- relevant existing Coach tests

### Tests first

- `analysis_result → coach_diagnostic_context.v1` 单一 allow-list projection；
- Python、Pi、tool response、stored context 使用同一结构；
- raw trace、绝对路径、原始 payload、未验证 heuristic sentinel 不进入任何 Coach sink；
- context schema/version/ref 可审计；删除 Analysis 后 ref 变 unavailable，消息保留。

### Stop rule

- 需要默认分享 raw trace；
- 需要把 Coach 输出写回 deterministic AnalysisResult；
- 需要引入 Benchmark 到默认 Coach context。

## Task 6 — History / Run inspector / evidence replay / comparable trends

### Allowed files

- `webapp/backend/history_trends.py` (new)
- `webapp/backend/routes.py` / `schemas.py` / `kovaak_run_store.py` only for read models
- `webapp/tests/test_history.py` / `test_history_trends.py` (new)
- `webapp/frontend/app/**`
- `webapp/frontend/components/**`
- `webapp/frontend/lib/api.ts` / `types.ts` / `contracts.ts` and focused tests

### Tests first

- Run 与 Analysis 轻列表，detail lazy load；
- source unavailable / trace quality / input mode 可见；
- MP4 可 seek；native-only 明确无视觉回放，不伪造；
- diagnosis/evidence stable ref 定位；
- 只有同 scenario/mode/metric version/unit/calibration/quality 才可比；
- 不足两条或不可比时不制造趋势。

### Stop rule

- 需要改变删除/retention 语义；
- 需要下载或 API 返回 raw trace；
- 需要新增独立 Dashboard 或 leaderboard。

## Task 7 — Benchmark / external progress product domain

### 冻结决策

- Benchmark 与 AnalysisResult/诊断处方分离；不复用 RefleK GPL catalog 或 rank 实现；
- v1 只提供 provider-neutral 本地记录/导入合同，不启用未经审查的在线 provider；
- 每条记录必须显式包含 provider、provider license note、catalog version、canonical scenario id、metric/unit、observed_at 与 availability；
- 外部身份默认不连接；只有显式 consent 才保存 opaque external identity ref；
- cache 状态为 `available | stale | unavailable`；不同 provider/catalog/scenario/metric/unit 不可比较；
- History 内作为独立分区，不进入默认 Coach context，不建立全局 leaderboard。

### Allowed files

- `webapp/backend/benchmark_store.py` (new)
- `webapp/backend/db.py` / `schemas.py` / `routes.py`
- `webapp/backend/history_trends.py` only for explicit external comparison
- `webapp/tests/test_benchmark_store.py` (new) and focused route/db tests
- `webapp/frontend/components/history/BenchmarkPanel.tsx` and related frontend types/API/tests

### Tests first

- provider/catalog/scenario identity required；
- owner/consent isolation；
- stale/unavailable/offline behavior；
- comparability predicate；
- no Benchmark fields leak into AnalysisResult or default Coach context；
- API/UI 无 provider secret、绝对路径或 raw trace。

### Stop rule

- 需要接入未完成 license/security review 的在线 provider；
- 需要存储 provider 密钥或 Steam credential；
- 需要新增 leaderboard、cloud trace sync 或默认 Coach 可见性。

## 全局验证 Gate

- Python tests、Rust tests、contract tests、frontend type/build checks；
- cross-language codec golden tests；
- no regressions for video+CSV fallback；
- source paths not exposed as business identifiers；
- Windows real-device / high polling-rate verification before release claim；
- `git status --short` must distinguish this work from pre-existing changes；
- every completed Task must report changed files, tests, unrun checks, deviations and remaining risks。
