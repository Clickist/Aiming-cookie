# RefleK Capability Adoption — Assessment

> 状态：assessment（2026-07-13）
> 参考仓库：`/Users/clickist/Projects/refleks`，`dev` 分支，commit `1f31c96`
> 本文记录采纳判断，不直接授权业务代码实现。文中的“建议纳入”不表示已实现、已排期或已授权；实现入口以 active implementation plan 为准。

## 1. 决策摘要

Aiming Cookie 不再把 RefleK 视为只提供 Raw Input 片段的参考项目，而把它视为一套值得系统吸收的产品能力参考：

```text
KovaaK Run facts
  → time-aligned evidence
  → deterministic analysis
  → History / inspector / replay
  → longitudinal comparison
  → Coach-readable structured context
  → optional training execution / benchmark enrichment
```

采纳目标是**能力和数据语义对齐**，不是复制 RefleK 的 Wails/Go 实现，也不是把 RefleK 的全部代码直接搬入本仓库。

RefleK 的 GPL-3.0 许可证意味着：

- 只借鉴产品行为、信息架构和公开算法思想，不等于可以复制代码；
- 若复用具体源码、算法实现或资源文件，必须先完成许可证、归属、隔离和发布义务审查；
- 本 assessment 不授权未经审查的 GPL 源码复制。

## 2. 采纳矩阵

| RefleK 能力 | Aiming Cookie 决策 | 采纳边界 |
|---|---|---|
| KovaaK Stats/Performance 自动发现 | 建议纳入下一阶段冻结范围并加固 | 稳定配对、可重试、source unavailable、reconciliation |
| Windows Raw Input | 建议纳入下一阶段冻结范围并加固 | 默认关闭、KovaaK gate、相对 dx/dy、不可云同步 |
| Run window / trace persistence | 建议纳入下一阶段冻结范围并重做合同 | 退出后 grace period、trace quality、版本化 artifact |
| Stats/Performance/trace lazy loading | 建议纳入下一阶段冻结范围 | list 轻量，详情和 trace 按需加载 |
| Run ↔ Analysis 关联 | 建议纳入下一阶段冻结范围 | Run 独立存在，Analysis 引用 Run，不反向拥有 |
| Stats/Performance 事件层 | 建议纳入下一阶段冻结范围 | 不能只存 summary/count，必须可重建/可追溯 |
| Raw dx/dy → derived trajectory | 建议纳入下一阶段冻结范围 | 原始 dx/dy 保留；累计坐标是版本化派生数据 |
| Trace replay / click markers / evidence highlight | P0/P1 候选，前置合同完成后采纳 | 作为证据回放，不把推断目标当真实 telemetry |
| Stats 时间线、准确率、TTK、间隔统计 | P0/P1 候选，先修正语义 | 首项 gap 不伪造为 0；显示样本量与 coverage |
| overshoot/undershoot/optimal heuristic | 实验性保留 | 不进入当前 deterministic report 或 Coach 默认上下文 |
| targetInference | 暂不采纳 | 无真实目标 telemetry，不能伪装成测量 |
| History inspector / Run compare | 采纳交互骨架 | 先冻结可比性和 input mode；不复制临时 session ID |
| Overview / streak / recent summary | 适配采纳 | 放入 History 顶部摘要；不恢复独立 Dashboard，除非 PRD 另行修改 |
| Benchmark catalog / rank / playlist | 独立产品域，后置 | 外部 provider、版本、身份、缓存、失败和许可合同独立冻结 |
| Scenario / playlist deep-link | 窄桥接采纳 | Tauri command，结构化失败；不假设 Run 已产生 |
| Settings / welcome | 适配采纳 | 分离 consent、runtime status、UI preference；不使用单体 JSON 双写 |
| Autostart / background resident | 后置 | 必须分离用户意图、平台能力和 OS 实际状态 |
| RefleK updater | 不直接采纳 | 只有签名发布链成熟后重新设计 |
| RefleK cloud sync of run files | 不采纳当前边界 | Raw trace 不进入云端、Coach 或普通日志 |

## 3. 数据进入 Aiming Cookie 的目标链路

### 3.1 Source facts

```text
KovaaKRun
  ├─ Stats source reference + parsed events
  ├─ Performance source reference + parsed events
  ├─ Raw Input trace artifact (optional)
  ├─ capture / pairing / coverage / quality metadata
  ├─ FOV / cm360 / sensitivity provenance
  └─ source availability state
```

### 3.2 Derived analysis

```text
Run + optional MP4
  → input_mode selection
  → source alignment
  → derived trajectory
  → deterministic kinematics
  → evidence-aware diagnosis
  → versioned AnalysisResult
```

支持三种模式：

- `input_native`：Stats/Performance + Raw Input，不依赖 MP4；
- `multimodal`：input-native + MP4，用于视觉校验和增强；
- `video_fallback`：MP4 + Stats，无 Raw Input 时仍可用。

每个结果必须能回答：

- 哪些输入存在；
- 哪些输入实际被使用；
- 时间窗如何对齐；
- 哪些指标是 measured/source fact；
- 哪些指标是 versioned derivation；
- 哪些证据缺失或不可靠；
- 哪些结论不能生成。

### 3.3 Coach context

Coach 默认只接收：

- 用户可见的 deterministic diagnosis；
- AnalysisResult 的结构化 summary；
- Run/History 的可比较趋势；
- evidence provenance、coverage 和 warnings；
- 用户明确选择的局部证据引用。

Coach 默认不接收：

- Raw Input 原始 trace；
- 任意本地路径；
- 未标注的 targetInference；
- 未验证的 overshoot/undershoot/sensitivity heuristic；
- 外部 benchmark 数据中未标注 provider/version 的字段。

## 4. 当前高优先级实现缺口

### P0 — 数据正确性

1. KovaaK 进程退出后，当前 Rust 线程会清空 Raw Input buffer；Performance 文件通常稍后才被 watcher 发现，可能导致 trace 永久丢失。
2. watcher 在异步 ingestion 成功前标记 emitted；解析、DB、snapshot 任一失败后，同一 runtime 不会重试。
3. Stats/Performance 跨目录 discovery 通过 stem COALESCE 合并，缺少明确 pair identity 和配对状态。
4. Run 先 commit、trace 后写入，崩溃可留下 Run 无 trace 或孤儿 trace。
5. Raw Input capture thread 每秒复制并同步写出整个 rolling buffer，和 WM_INPUT 共用 mutex；高 polling rate 下会产生高 I/O、长临界区和无声丢点。
6. 时间窗口缺少 clock source、offset、guard window、coverage 和 confidence。
7. Rust/Python codec 没有跨语言 golden fixtures，也没有资源上限和语义校验。

### P0 — 跨层合同

1. `AnalysisResult v1` 尚未表达 `analysis_id`、`analysis_type`、`input_mode`、`kovaak_run_id`、evidence provenance、availability、warnings/errors。
2. artifact manifest 仍然只理解 video + CSV，没有 Performance、Raw Input、Run 和 alignment artifact。
3. `sessions` 没有 `kovaak_run_id`、input mode 和 evidence relation。
4. 当前 worker 仍固定执行 `video + csv → run_analysis`。
5. 当前 API 将绝对源路径作为 Run DTO 字段返回，UI/Coach 不能依赖 stable artifact ID。

### P1 — 产品能力

1. 没有用户可达的 Run → Analysis 创建路径。
2. 没有 Run/Analysis History 的轻量列表、详情 lazy loading 和 source availability。
3. 没有 Stats/Performance/Raw Input/MP4 evidence view。
4. 没有 trace replay、kill/event highlight、诊断到证据定位。
5. 没有同类 input mode 下的可比 baseline、趋势和 compare。
6. Coach 只有受限 analysis summary 工具，未能读取 Run、History、趋势和 evidence 状态。

### 条件性扩展

- Benchmark catalog/rank/score/playlist 是新产品域，不应成为 input-native P0 的硬依赖；
- 独立 Dashboard 与当前 PRD/UI 冲突，暂采用 History 内摘要；若坚持独立 Dashboard，必须先更新 PRD 和 UI contract；
- 外部 Steam/KovaaK identity、远端 progress、缓存和同步必须另立合同。

## 5. 不直接迁移的 RefleK 算法

RefleK 的下列能力可作为研究和 UX 参考，但不能直接进入当前正式报告：

- targetInference：没有真实 target telemetry，是推测重建；
- overshoot/undershoot/optimal：kill endpoint 被当成 target proxy，且阈值未标定；
- `px` 命名：实际是 raw counts / virtual coordinate，不是屏幕像素；
- confidence：规则分数，不是校准概率；
- sensitivity suggestion：经验型比例调整，不是验证后的处方；
- scenarioAnalysis 中把第一条 inter-kill gap 固定为 0，会污染平均 TTK、KPM 和相关性。

可进入 deterministic report 的第一批指标应限于：

- source facts、coverage、alignment status；
- total kills/shots/hits/accuracy；
- 可靠的 event timeline；
- trace point count、duration、button transitions；
- 经过版本化并验证的 trajectory path length、straightness、velocity/acceleration、movement timing；
- 带 sample count、unit、evidence 和 quality 的 aggregation。

## 6. 许可证和第三方边界

- RefleK repository 标记为 GPL-3.0；
- 本项目可以研究其行为、数据流和交互；
- 具体源码、复制粘贴、改写后仍可能产生许可义务；
- 在许可证审查完成前，implementation plan 只允许新写代码和独立实现，不允许直接复制 RefleK 源文件或大段实现。
