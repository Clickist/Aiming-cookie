# Complete Coach Analysis and Evidence Context v1 - Implementation Plan

> **状态：proposed，禁止执行。** 采集 worktree 的上游文档与代码尚未合入；必须先在 executor plan 之外完成下述 pre-activation 文档治理审阅，再由点点批准转为 active。只有 active 后，点点明确指定的一个 Task 才能执行。
> **For executor:** implement only the explicitly authorized Task; obey Allowed files, Tests first, frozen decisions and Stop rule.
> 依赖 spec：[`../specs/2026-07-20-complete-coach-analysis-context-design.md`](../specs/2026-07-20-complete-coach-analysis-context-design.md)
> 相关 spec：[`../specs/2026-07-13-analysis-evidence-coach-context-design.md`](../specs/2026-07-13-analysis-evidence-coach-context-design.md)、[`../specs/2026-07-14-versioned-coach-knowledge-registry-design.md`](../specs/2026-07-14-versioned-coach-knowledge-registry-design.md)、[`../specs/2026-07-17-automatic-run-capture-design.md`](../specs/2026-07-17-automatic-run-capture-design.md)

**Goal:** 把 Run-owned Raw、Stats、Performance 与 optional MP4 转成覆盖 static/dynamic clicking、tracking 和 target switching 的可追溯分析，并通过有界证据工具驱动 Coach、画像、训练计划和复测闭环。

**Architecture:** 先统一时间和场景语义，再把高频完整时间序列留在私有 derived artifacts；每个 aim family 使用独立 analyzer 产出统一 MetricRecord、EvidenceSegment 和确定性 diagnosis。Coach 默认获得预算内的完整规范化 Run facts 与安全摘要，需要细节时通过现有 owner-scoped product-command bridge 查询 outcome timeline/events、分布、片段、派生曲线和比较结果。

**Tech Stack:** Python 3.9、FastAPI、SQLite/app-owned workspace、现有 Pi TypeScript runtime、OpenCV/NumPy/Pandas、JSON schema、pytest/Node tests。

---

## 1. 当前实现事实

- 正式 worker 只有 static flicking producer；`analysis_type` 的存储字段可接受字符串不等于 tracking/switching 已接通。
- `kovaak_tracker/tracking.py`、`analysis.py`、`advice_tracking.py` 是候选/旧实现，不能直接当成 v2 producer。
- Coach 已有 allow-list context、Knowledge Registry 和 owner-scoped `run_product_command` bridge；新 evidence query 应复用这条通道。
- 生产 Training Plan 是安全持久化命令；旧 `coach/planning.py` 的确定性计划逻辑没有接到当前产品主链。
- capture closeout branch `codex/windows-capture-closeout` 已在 clean/pushed `b8c8502` 实现 `time_alignment.v2`、Run window persistence、Capture Coordinator/Finalizer 与 lifecycle repair，但尚未合入当前 checkout；两条分支从 `0aca0ac` 分叉，不能把 capture branch 的代码或上游文档当成当前已合入事实。
- capture branch 的 worker snapshot 仍未冻结并传递 CanonicalTimeWindow；native analyzer 会重新 resolve source，legacy duration 路径仍含 end-inclusive compatibility。Stats/Performance parser 的 presence/order、跨午夜、per-weapon、multi-payload 和 timestamp validity 也仍是 Task 1 delta；必须先修 correctness，再扩 analyzer。
- 本计划不得覆盖、清理或并行修改 capture worktree。激活前必须通过正常 Git 集成保留双方提交，并逐项解决上游文档冲突；不得在当前 checkout 手工复制 capture 实现冒充已合入。

## 2. 全局冻结决策

1. Coach 不接收 Raw、完整私有 signal/event arrays、MP4、frames、路径或任意查询语言。
2. Coach 可以接收当前 schema 下完整、类型化、字段白名单化的 CanonicalRunFacts，并按预算查询 whole-run/segment outcome timeline 与规范化 events；不得把原始 CSV/protobuf/private parser payload 冒充这些事实。
3. 用户启用 Coach 并选择 Provider 后，L1-L3 bounded context/tool results 可以作为普通 Coach turn 数据发送给该 Provider，不设逐 Run consent；L0 不发送是成本/体量/可消费性边界，不是敏感数据分级。
4. Coach 通过既有 product-command bridge 查询受限 evidence，不新建任意数据通道。
5. MP4 在 v1 只由本地确定性预处理器消费；Coach 引用 EvidenceSegment，用户在 UI 播放本地片段。
6. 当前不让 Coach 读取视频是能力/成本边界，不是永久禁令；未来视觉模型必须另立版本化、显式授权、限定片段合同。
7. scenario hash 是正式身份；名称 heuristic 不能驱动 family-specific diagnosis。
8. static clicking、dynamic clicking、tracking、switching 分别有 analyzer；共享 schema，不共享未经验证的阈值。
9. movement aiming 在没有玩家移动遥测时保持 outcome-only。
10. 未校准 metric 可以做分布、自身 baseline 和 matched comparison，不能硬写绝对健康线。
11. 每个用户问题默认 1 个主 EvidenceSegment，最多 2 个补充片段。
12. 所有 schema、metric、scenario、analyzer、alignment 和 knowledge 引用都必须版本化并可回放。

## 3. 执行顺序

```text
Pre-activation upstream reconciliation/activation (not an executable Task)
  -> Task 1 canonical time
  -> Task 2 scenario profiles
  -> Task 3 signal/evidence artifacts
  -> Task 4 bounded Coach evidence queries
  -> Task 5 static clicking migration
  -> Task 6 visual numerical preprocessing
  -> Task 10 minimum knowledge/diagnosis coverage
  -> Task 7 dynamic clicking
  -> Task 8 tracking
  -> Task 9 target switching
  -> Task 11 profile/plan/retest loop
  -> Task 12 integrated release gates
```

Task 10 是 Task 7、8、9 的前置条件；它先补 definition/scope/limitation 和 prescription/verification 的最小知识覆盖。Task 7、8、9 随后可以分别开发，但共享 contract 或 worker dispatch 的改动必须串行合入并分别回归，不能让多个 executor 同时编辑同一文件。

## Pre-activation governance checklist - not an executable Task

### 目的

等待采集分支合入主线后，由架构/文档治理会话把点点确认的完整 Coach 上线目标写回主责任事实源，解决当前 PRD “v1 flicking-first / tracking later” 与新目标的冲突，再决定本 spec/plan 是否可以转为 active。本节不是 plan Task，不能用于绕过“只有 active Task 才能执行”的规则。

### Reconciliation scope

- `docs/PRD.md`
- `docs/ARCHITECTURE.md`
- `docs/ROADMAP.md`
- `docs/PROGRESS.md`
- `docs/frontend-uiux-design.md`
- `docs/README.md`
- `docs/superpowers/specs/2026-07-20-complete-coach-analysis-context-design.md`
- `docs/superpowers/specs/2026-07-17-automatic-run-capture-design.md`
- `docs/superpowers/specs/2026-07-13-analysis-evidence-coach-context-design.md`
- `docs/superpowers/specs/2026-07-13-coach-product-commands-explanations-provider-design.md`
- `docs/superpowers/specs/2026-07-14-versioned-coach-knowledge-registry-design.md`
- `docs/superpowers/specs/2026-07-13-frontend-product-reconstruction-design.md`
- `docs/superpowers/specs/README.md`
- `docs/superpowers/plans/2026-07-13-coach-productization-provider-management.md`
- `docs/superpowers/plans/2026-07-13-frontend-product-reconstruction.md`
- `docs/superpowers/plans/2026-07-20-complete-coach-analysis-context-v1.md`
- `docs/superpowers/plans/README.md`

### Acceptance

- PRD 明确 launch scope、阶段承诺和 movement aiming outcome-only 边界；
- PRD/Architecture 明确 L1-L3 是普通 Provider Coach context，不增加逐 Run consent；L0 边界由成本/体量/可消费性解释。
- Architecture 冻结数据归属、依赖方向、Evidence Broker 与视频当前/未来边界；
- Roadmap 给出采集 Gate 与下游 Task 的真实顺序；
- Progress 只记录当时实现/验证快照，不复制长期合同；
- spec/plan index 状态、反向链接和文档入口一致。
- 自动采集 spec 与 CanonicalTimeWindow 对 Stats 精确锚、Performance 身份校验和 source precision 的主从关系一致；明确修订旧合同还是由新 active 合同取代。
- active Analysis/Coach spec 的“完整原始 CSV/performance payload”禁令被精确化为 L0 原始载体/私有 parser payload 禁止，同时允许完整 allow-listed CanonicalRunFacts、分页 exact timeline 和规范化 events；不得让两份 active spec 对 Coach 数据权限给出相反结论。
- active Coach commands、Knowledge、frontend UI/UX/spec/plan 逐份给出“修订、由新合同 supersede、或保持兼容”的结论；清除旧 Raw 可经普通确认进入 context、flicking-only UI 和旧 knowledge coverage 与完整 Coach 的冲突。
- Provider onboarding 只需清楚列出当前版本会发送的 L1-L3 字段类别与 L0 禁止项；按点点已拍板的普通 Coach context 规则，不新增逐 Run consent 或新的敏感数据状态机。
- 合入后的 `time_alignment.py`、worker/snapshot 逐项对照 Task 1；已经完成的步骤从计划中标记/拆除，不能重复实现或回退采集验证。

### Review steps

1. 验证采集 worktree 已通过正常 Git 合入或被明确保留，主 checkout 不再需要碰另一个 worktree 的 dirty state。
2. 对比合入后的 PRD/Architecture/Roadmap 与本 proposed spec，只把产品/稳定合同回写到对应主责任文档。
3. 审阅本 spec 的硬预算、scope 和未来 vision extension；需要修改时只改本 spec。
4. 点点批准后，把 spec/plan 状态和两个索引从 proposed 改为 active；未批准时保持 proposed。
5. 运行链接、状态和 diff 检查。

### Verify

```powershell
rg -n "complete-coach-analysis-context|状态：active|状态：proposed" docs
git diff --check
git status --short
```

### Activation stop rule

- 采集 worktree 尚未合入、上游 dirty 变更归属不清或需要覆盖另一个 session 的文件；
- 点点已确认的 launch scope 尚未写回 PRD/Architecture/Roadmap；
- 需要用下游 spec 静默覆盖 PRD/Architecture；
- 点点尚未批准把 proposed plan 转为 active；在此之前不能把本节当作 Task 执行。

## Task 1 - Canonical Challenge time window

### 目的

修复精确 Stats 起点没有进入 worker analyzer 的 correctness 缺口，使 Raw、MP4、Stats、Performance、events 和所有 analyzer 消费同一个 `[start, end)` window。

### Allowed files

- `kovaak_tracker/csv_parser.py`
- `kovaak_tracker/performance_parser.py`
- `kovaak_tracker/time_alignment.py`（采集分支合入后的 canonical 文件）
- `kovaak_tracker/native_flicking_analysis.py`
- `webapp/backend/kovaak_run_store.py`
- `webapp/backend/worker.py`
- `webapp/backend/contracts.py`
- `tests/test_csv_parser.py`
- `tests/test_performance_parser.py`
- `tests/test_time_alignment.py`
- `tests/test_native_flicking_analysis.py`
- `webapp/tests/test_kovaak_runs.py`
- `webapp/tests/test_worker.py`
- `webapp/tests/test_contracts.py`

### Tests first

- Stats `Challenge Start` 含 `.797` 时，snapshot/result 保留到毫秒且所有 source 使用相同 zero；
- Stats time-of-day 单独存在时不伪造 UTC；只有 fixture 提供显式、版本化 local-to-UTC mapping 时才验证与 Performance epoch 的日期映射。Stats 内部 `23:59:59.xxx -> 00:00:00.xxx` 使用 24 小时 unwrap 得到正的 challenge-relative time，但仍不称为 UTC；
- Performance 与 Stats anchor 一致、轻微差异、冲突、缺失各有稳定 status/warning；
- 60 秒 window 使用 `[0, 60000)`，边界上的 Raw/event 不重复或越界；
- native analyzer 当前 `<= end_ms` 的回归测试先失败，再改为 end-exclusive；
- high polling 同毫秒 samples 以 record order 保序；
- synthetic 118 click events、118 Stats shots 不因 offset 丢成 117；
- Stats per-weapon aggregate row 解析 `Weapon/Shots/Hits/Damage Done/Damage Possible`，不得把 header 空分隔列之后、row 中不存在的值复制为 weapon facts；
- Performance 合法 event 只允许一个 `oneof` payload；同一 event 含多个已知 payload 时 v1 必须 reject，不能保留当前“后一个覆盖前一个”的有损行为或自行拆分；
- Performance parser 为 header/profile/event 保存已知 field presence，区分 source 明确写入 `0/0.0/""/[]` 与 wire field 缺失；无法区分时不得标 complete；
- Performance event 缺 timestamp 或无任何 payload 时 reject；只有 unknown payload 的 future event 从 L1 record omitted、标 source detected + timeline partial，但保留其 top-level index 空位；每个 accepted record 保存 immutable `source_event_index`，duplicate timestamp 仍按 source order 稳定输出；
- unknown Performance fields 的 observability 明确为 detected/none/not-observable；当前 parser 静默跳过时不能声称没有 unknown fields；
- Performance float32 timestamp 必须 finite 且非负；canonical ms 固定为非负值上的 `floor(float32_seconds * 1000 + 0.5)`，同时保留 source-native value。量化后只接受 `[0, duration_ms)`；NaN/Infinity、负值、end 边界和超窗 records 进入明确 error/partial policy；
- retry 消费同一 frozen CanonicalTimeWindow，不重新读取漂移源文件。

### 实施步骤

1. 写 synthetic 亚秒、跨午夜、end-exclusive、per-weapon 与 multi-payload regression fixtures，先证明当前路径失败或有损。
2. 修正 Stats/Performance parser：保留 source-native precision、known-field presence 与 source record order，解析 per-weapon aggregate，拒绝非法/缺字段 event，并暴露可证明的 unknown-field observability。
3. 在 Run snapshot 冻结 Stats/Performance observed anchors 与选中的 CanonicalTimeWindow。
4. 让 worker 把 frozen window 显式传给 analyzer；移除 analyzer 内部独立选择 anchor 的生产路径，并统一使用 `[start, end)`。
5. 把 alignment/coverage/warnings 投影到 AnalysisResult v2；unknown/failed fail-closed。
6. 回归当前 static flicking metrics 和 Run ingestion。

### Verify

```powershell
$env:KOVAAK_INSTALL_DIR='Z:\__aiming_cookie_test_missing__'
python -m pytest tests/test_csv_parser.py tests/test_performance_parser.py tests/test_time_alignment.py tests/test_native_flicking_analysis.py webapp/tests/test_kovaak_runs.py webapp/tests/test_worker.py webapp/tests/test_contracts.py -q
```

### Stop rule

- 合入后的 `time_alignment.py` 合同与本 spec 冲突且无法通过兼容 adapter 解决；
- Stats wall-clock 缺 timezone/locale 证据，需要猜 UTC；
- 需要修改采集时钟或 capture coordinator；
- 真实差异无法区分 source clock error 与 parser bug。
- 无法在不猜日期/时区的前提下解释 Stats time-of-day 与 Performance UTC 的映射。

## Task 2 - Versioned ScenarioProfile registry

### 目的

建立 hash-first、人工审核、fail-closed 的 scenario taxonomy，使 worker 能选择正确 analyzer，未知项目退化为 outcome-only。

### Allowed files

- create `knowledge/scenarios/schema.v1.json`
- create `knowledge/scenarios/registry.v1.json`
- create `knowledge/scenarios/launch-manifest.v1.json`
- create `kovaak_tracker/scenario_profiles.py`
- `webapp/backend/kovaak_run_store.py`
- `webapp/backend/contracts.py`
- `webapp/backend/worker.py`
- create `tests/test_scenario_profiles.py`
- `webapp/tests/test_kovaak_runs.py`
- `webapp/tests/test_worker.py`
- `webapp/tests/test_contracts.py`

### Tests first

- exact scenario hash 返回稳定 family/subdomain/allowed analyzers/metrics；
- 同名不同 hash 不合并，同 hash 显示名变化仍保持 identity；
- name-only heuristic 只能返回 candidate/unknown，不能 dispatch 正式 analyzer；
- unknown hash 只允许 outcome + 通用 input metrics；
- schema 拒绝 duplicate hash、unknown enums、空 limitations、越界 Registry；
- classification ref/source/review/supersession 可回放，旧 Analysis 继续解析当时 version；
- Registry version 与 scenario profile ref 进入 result/comparability；
- 首发对外宣称支持的每个 static/dynamic/tracking/switching scenario 都在 exact hash manifest 中有 fixture、审核来源、family/motion/target-count、allowed analyzer/metric 和 limitation；manifest 外一律 outcome-only；
- manifest status 只允许 `pending_gate | active | retired`；Task 2 可先登记 future-family `pending_gate` entry，但它不能 dispatch 正式 analyzer 或被 UI/Coach 宣称为支持。Task 5/7/8/9 分别在对应 Gate 通过后改为 `active`，Task 12 验证四个 launch family 齐全；
- 迁移前已冻结的 `analysis_type=flicking` request 仍能走受限 `legacy_static_compatibility`；新 unknown Run 不能自动继承。用户显式声明 static 固定 `classification_source=user_declaration`、`claim_ceiling=descriptive_only`、`family_analyzer_dispatch=none`，不生成正式 severity/处方/profile/plan/trend。

### 实施步骤

1. 先建立最小 Registry schema、exact hash launch manifest 和审核 fixtures；manifest entry 至少包含 `scenario_hash`、`scenario_profile_ref`、`fixture_ref`、`review_source_ref`、`reviewed_at`、`family_gate_refs` 和 `status`，且不能靠 display name 关联。不追求一次覆盖全部 Workshop；未通过后续 analyzer/knowledge/visual Gate 的 entry 保持 `pending_gate`。
2. 从官方/editor metadata 或人工审核记录 family、motion、target-count 和允许指标；不从 Stats 名称自动升级。
3. 为每个 classification 保存 stable entry ref、source refs、review time、status 和 supersession chain。
4. 在 Run snapshot/worker dispatch 加载 frozen scenario profile ref；只对迁移前 frozen flicking request 保留 versioned legacy compatibility；用户声明 fallback 使用上述独立 descriptive ceiling 且不进入 family analyzer dispatch。
5. 将 unknown/outcome-only 作为正常、用户可解释状态。
6. 为后续 curated additions 保留审核流程，不加入在线下载或 LLM 分类。

### Verify

```powershell
$env:KOVAAK_INSTALL_DIR='Z:\__aiming_cookie_test_missing__'
python -m pytest tests/test_scenario_profiles.py webapp/tests/test_kovaak_runs.py webapp/tests/test_worker.py webapp/tests/test_contracts.py -q
```

### Stop rule

- 需要通过 scenario 名称或模型猜测直接产生正式分类；
- 没有 scenario hash 却要伪造稳定 identity；
- 需要在线服务、用户账号或自动修改 Registry；
- taxonomy 改变 PRD launch scope 而上游未更新。
- launch manifest 不能给每个 advertised hash 提供可分发 fixture、审核来源和 family Gate。

## Task 3 - Signal/Event/Metric/Evidence contracts and local artifacts

### 目的

实现私有 SignalBundle/EventBundle、可完整投影的 CanonicalRunFacts/NormalizedOutcomeTimeline、公开 MetricRecord/EvidenceSegment 和 Analysis-owned derived artifact 生命周期，为所有 analyzer 与 Coach 查询提供统一底座。

### Allowed files

- create `kovaak_tracker/analysis_evidence.py`
- create `webapp/backend/evidence_store.py`
- `webapp/backend/contracts.py`
- `webapp/backend/workspace.py`
- `webapp/backend/db.py` only if stable artifact metadata requires a migration
- `webapp/backend/queue.py`
- `webapp/backend/worker.py`
- `webapp/backend/routes.py`
- `webapp/backend/schemas.py`
- create `tests/test_analysis_evidence.py`
- create `webapp/tests/test_evidence_store.py`
- `webapp/tests/test_contracts.py`
- `webapp/tests/test_workspace.py`
- `webapp/tests/test_worker.py`
- `webapp/tests/test_routes.py`

### Tests first

- valid bundles/records/segments round-trip，unknown fields/version fail-closed；
- `source_field_registry.v1` 精确覆盖 Stats 26 summary、16 config、13 kill-row、5 per-weapon fields，以及 Performance 4 header、12 profile、17 metric-change payload；field registry version、type/unit/projection policy 有 golden parity；
- CanonicalRunFacts + paged timeline/events 联合保留当前 parser schema 的全部 allow-listed Stats/Performance facts，逐 section 声明 present/source-absent/omitted、complete/partial、parser/source versions、recognized schema status 和 unknown-field observability；
- source-derived scenario/config strings 按 schema 限长、拒绝控制字符与 path/URL/secret sentinel，并在 Coach runtime 中始终标作 untrusted data，不得解释为工具/系统指令；
- Stats `Crosshair` asset value 只投影 presence，不泄漏 basename/path-like token；source scale/unit 未知的 Input Lag/sensitivity/FOV 不伪装成统一物理量；
- field normalization 覆盖非法/空数值、NaN/Infinity、`Cheated` 非 `0/1`、坏 resolution、坏 RGBA 和异常数组对齐；一律 `partial + omitted_known_fields` 或 source-absent，不 silent coercion/default；
- normalized whole-run/segment outcome timeline 保留 Stats/Performance 各自 source-native time precision、source refs 和 count/delta/value semantics；Stats 只生成 kill-row records，不伪造逐 shot/hit/miss timestamps；
- exact page 按 `(canonical_time_ms, source_priority, source_event_index)` 稳定排序，最多 120 records 而非 timestamps，且受 24 KiB 限制；same-time records 不丢失，overview 明确 downsampled；
- oversized facts 返回安全 section refs；typed reader 使用 owner/analysis/immutable evidence revision/query digest/sort/contract version 生成稳定 page descriptor，拒绝 stale revision 和 query drift；bridge-bound opaque cursor 由 Task 4 包装，Task 3 不自行创建 Coach capability；
- complete samples 只写 local derived artifact，不 inline 到 result/API；
- atomic temp-write -> validate -> commit，worker crash/retry 无 half artifact；
- artifact ref owner/analysis/version 绑定，跨 owner/deleted/nonterminal 不可读；
- segment interval 必须落在 CanonicalTimeWindow，focus range 落在 segment 内；
- family analyzer 可通过显式 versioned registration 增加 spec 已允许的 channel/event/metric keys，不需要改存储格式或绕过 validator；
- Analysis deletion 清理 Analysis-owned derived artifact，不删除 Run-owned Raw/MP4/Stats/Performance；
- result/API/message/log 不含 path、原始 CSV/protobuf/private parser、Raw sample 或 video/frame payload；但允许合法 CanonicalRunFacts/normalized timeline/event fields。

### 实施步骤

1. 写纯 Python field Registry、validators/serializers，包括 CanonicalRunFacts、NormalizedOutcomeTimeline、exact record ordering 和 family-neutral extension registration，先不接 worker。
2. 在 app-owned workspace 实现 versioned derived artifact writer/reader 和 checksum validation。
3. 扩展 Artifact Manifest 与 AnalysisResult 的 safe refs。
4. 把 artifact commit 放在 terminal result commit 的可恢复边界内。
5. 增加 public projection 和删除/reconciliation 回归。

### Verify

```powershell
$env:KOVAAK_INSTALL_DIR='Z:\__aiming_cookie_test_missing__'
python -m pytest tests/test_analysis_evidence.py webapp/tests/test_evidence_store.py webapp/tests/test_contracts.py webapp/tests/test_workspace.py webapp/tests/test_worker.py webapp/tests/test_routes.py -q
```

### Stop rule

- 需要把高频完整 SignalBundle、Raw samples 或私有 EventBundle 写进 SQLite、AnalysisResult 或 Coach message；规范化 outcome timeline 的 bounded/exact pages 不在此禁止项内；
- 无法与现有 deletion/reconciliation 原子性合同兼容；
- 需要让 Analysis 删除 Run-owned source；
- 需要暴露 absolute path 或任意 artifact enumeration。

## Task 4 - Bounded Coach evidence query commands

### 目的

在现有 owner-scoped product-command bridge 上实现 metric distribution、evidence list、signal window、comparison、run facts、outcome timeline 和 normalized events，使 Coach 有足够分析颗粒度但不能触碰原始载体或无类型内部 payload。`profile.aiming.snapshot` 到 Task 11 有真实 store 后才注册；此前稳定 unavailable。

### Allowed files

- `webapp/backend/coach_commands.py`
- `webapp/backend/coach_context.py`
- `webapp/backend/coach_runtime.py`
- `webapp/backend/coach_service.py`
- `webapp/backend/coach_store.py`
- `webapp/backend/routes.py`
- `webapp/coach-runtime/prompts/coach-system.md`
- `webapp/coach-runtime/src/product-command-tools.ts`
- `webapp/coach-runtime/src/contracts.ts`
- `webapp/tests/test_coach_commands.py`
- `webapp/tests/test_coach_context.py`
- `webapp/tests/test_coach_runtime.py`
- `webapp/tests/test_coach_tool_runtime.py`
- create `webapp/tests/test_coach_store.py`
- `webapp/tests/test_routes_coach.py`
- `webapp/coach-runtime/test/product-command-tools.test.ts`
- `webapp/coach-runtime/test/system-prompt-and-tools.test.ts`

### Tests first

- 七个 analysis read-only command 的合法 payload、owner scope、stable result refs；profile capability 未构建时未注册或返回 `unavailable/profile_not_built`；
- arbitrary time/frame/path/artifact/SQL/Python/channel/metric 被拒绝；
- signal window 限 1 segment、12 秒、4 channels、600 points/channel、turn total 2400 points；
- metric <= 8、list <= 20、compare 2-4 refs 且 comparability fail-closed；
- default context 在 CanonicalRunFacts <= 8 KiB 时携带完整 allow-listed facts，超限只携带 section summaries/refs；
- 历史 `coach_diagnostic_context.v1` 原样可读/展示且只能保持 v1 低粒度语义；不得凭空升级为含 CanonicalRunFacts 的 v2。只有新完成 Analysis 生成 v2；coerce、store append/load、runtime canonicalization 和 route output 同时接受两种 schema，v2 仍满足 32 KiB Gate；
- `analysis.run_facts.get` 可返回完整 allow-listed sections，超 24 KiB 显式分节，不 silent truncate；
- `analysis.outcomes.timeline` 只接受 whole-run 或 existing segment scope，最多 8 series/120 records；exact page 使用服务端 cursor、固定排序并受 byte limit，overview 标 downsampled；
- `analysis.events.list` 首次只接受 allow-listed event kinds、whole-run/segment scope 和 limit <= 20，后续只能回传服务端 cursor；facts/timeline/events cursor 不可互换；
- bridge 只接受 default context 或同一 bridge handler 返回的 reachable refs；即使 analysis 属于同 owner，模型猜出的未到达 ref 也拒绝；
- backend bridge state 保存不可伪造的 `bridge_id/turn_id`；cursor registry 绑定该 id，revoke/expiry 时清理。同 owner 新 bridge、已撤销 bridge、跨 command-kind 或过期 cursor 全部拒绝；cursor 值不进入 SQLite、safe-parameters summary、tool event 或 trace；
- prompt-injection-like scenario/config 文本只能作为 bounded structured data 返回，不能注册工具、改变 policy 或触发产品命令；
- bridge turn state 通过 per-bridge lock 原子维护 calls、canonical-JSON UTF-8 serialized bytes 和 signal points；所有尝试消耗 call，只有返回的 schema-valid result 消耗 bytes/points；并发/连续 6 次异构调用不能突破 64 KiB/2400 points，SignalWindow 最多占 32 KiB，仍可完成至少一次 list/compare；
- default context <= 32 KiB、single response <= 24 KiB、histogram <= 16 bins、limitations/string bounds 和 deterministic truncation shape；
- queued/running/deleted/other-owner/unknown version unavailable；
- 当前 loopback bridge transport HTTP response/Provider transcript 收到完整 schema-valid tool result；除此之外，`coach_product_commands.result_json`、tool event、message trace、普通 API response/log 只保存 refs/keys/query digest/budget/status 的 audit projection，不保存 facts/events/points/cursor/path/raw carrier/video/frame/secret；
- audit projection 成功持久化前不返回完整 result；写入失败只返回 `unavailable/audit_unavailable`，不能改为持久化或返回未审计的完整 result。完整 result 只活在当前 Provider turn，不作为恢复 command journal 的来源；
- default context 限 24 metrics、6 issues、4 trends，并保留 segment refs/limitations。

### 实施步骤

1. 在 backend command registry 写 payload validators 和七个只读 handlers；只通过 Task 3 typed evidence reader 读取，不直接解析 worker result JSON；profile command 暂不注册为可用 capability。
2. 扩 bridge id/turn id、reachable-ref set、per-bridge lock、cursor registry 和 budget ledger，原子执行 owner/status/version/call/byte/point checks；revoke 同时清理 cursor，不信任 Coach payload，也不只做单调用限额。
3. 为七个 evidence commands 建立 Provider full-result / journal audit-projection 双投影和 audit-write fail-closed；扩 v1/v2 context store/coerce/runtime/route canonicalization 与 event validator。
4. 用确定性 extrema-preserving downsample 生成 SignalWindow，并按 remaining serialized-byte ledger 继续降采样；facts/exact records 放不下时返回 refs/unavailable，不 silent truncate。
5. 通过现有 `run_product_command` 暴露命令，不增加 filesystem/data-lake tool。
6. 更新 prompt 只描述何时下钻、如何引用和何时停止，不塞数据正文。

### Verify

```powershell
$env:KOVAAK_INSTALL_DIR='Z:\__aiming_cookie_test_missing__'
python -m pytest webapp/tests/test_coach_commands.py webapp/tests/test_coach_context.py webapp/tests/test_coach_runtime.py webapp/tests/test_coach_tool_runtime.py webapp/tests/test_coach_store.py webapp/tests/test_routes_coach.py -q
$env:PI_SOURCE_DIR = (Resolve-Path third_party\pi).Path
$env:TSX_TSCONFIG_PATH = (Resolve-Path third_party\pi\tsconfig.json).Path
$env:PYTHON_BIN = (Resolve-Path .venv\Scripts\python.exe).Path
$loader = (Resolve-Path third_party\pi\node_modules\tsx\dist\loader.mjs).Path
$loaderUrl = & node -e "const { pathToFileURL } = require('node:url'); process.stdout.write(pathToFileURL(process.argv[1]).href)" $loader
$tests = @((Resolve-Path webapp\coach-runtime\test\product-command-tools.test.ts).Path, (Resolve-Path webapp\coach-runtime\test\system-prompt-and-tools.test.ts).Path)
node "--import=$loaderUrl" --test @tests
```

### Stop rule

- 需要给 Coach 任意范围、任意代码、任意文件或完整 artifact 读取能力；
- command bridge 无法在 handler 重新执行 owner/status authorization；
- tool trace 必须保存完整 points/payload 才能工作；
- budget 只能靠 prompt 而不能由服务端强制。
- 现有 command journal 无法在不保存完整 evidence result 的前提下保持审计/幂等合同。

## Task 5 - Migrate static clicking onto the unified evidence model

### 目的

把现有 input-native flicking producer 迁移为第一个统一 analyzer，保持当前指标和 diagnosis 无回归，并生成可查询/播放的 EvidenceSegment。

### Allowed files

- `knowledge/scenarios/launch-manifest.v1.json` only to activate reviewed static entries after this Task's gates pass
- `kovaak_tracker/native_flicking_analysis.py`
- `kovaak_tracker/advice.py`
- `kovaak_tracker/coach/diagnosis.py`
- `webapp/backend/worker.py`
- `webapp/backend/coach_context.py`
- `tests/test_native_flicking_analysis.py`
- `tests/test_scenario_profiles.py`
- `tests/coach/test_diagnosis.py`
- `webapp/tests/test_worker.py`
- `webapp/tests/test_coach_context.py`
- `webapp/tests/test_history_trends.py`

### Tests first

- 现有 movement timing、decel、path、reverse、submovement、SPARC 分布/limitations 不变；
- 每个 flick event 有 stable event ref，典型/最差/改善 segment rank 可解释；
- 无 target track 时不生成 overshoot/undershoot/target error；
- 每个 issue 默认 1 main、最多 2 supporting segment refs；
- old result 继续可读，新 result 使用新 analyzer/metric/evidence version；
- Task 2 已登记的 static launch scenario 全部走新 profile dispatch；迁移前 frozen flicking request 可走受限 legacy compatibility，新 unknown Run 不被静默当成 static；
- static manifest entry 只有在本 Task 的 analyzer/regression/knowledge prerequisites 全部通过后从 `pending_gate` 改为 `active`；
- current History comparability 不跨 metric/alignment/scenario version。

### 实施步骤

1. 先用现有 golden/synthetic fixtures 锁定 metric distributions 和 diagnosis refs。
2. 新 adapter 生成统一 Event/Metric/Evidence，不重写已验证的算法核心。
3. worker dispatch 对 ScenarioProfile `static_clicking` 选择该 analyzer；只对迁移前 frozen flicking request 保留带 limitation 的 versioned legacy compatibility。
4. default context 投影新 refs；删除 legacy duplicate fields 只能另立 migration Task，本 Task 保留兼容读取。

### Verify

```powershell
$env:KOVAAK_INSTALL_DIR='Z:\__aiming_cookie_test_missing__'
python -m pytest tests/test_native_flicking_analysis.py tests/test_scenario_profiles.py tests/coach/test_diagnosis.py webapp/tests/test_worker.py webapp/tests/test_coach_context.py webapp/tests/test_history_trends.py -q
```

### Stop rule

- 迁移需要改变现有 metric 公式或阈值；
- 需要删除 legacy result compatibility；
- 需要从 Raw 推断 target-relative facts；
- static regression 无法解释或超过既有允许误差。

## Task 6 - Local visual numerical preprocessing

### 目的

把 MP4 本地转换为 crosshair/target tracks、hitbox/radius、confidence 和视觉 events，供 dynamic clicking、tracking 与 switching 使用；不把视频交给 Coach/Provider。

### Allowed files

- create `kovaak_tracker/visual_signals.py`
- `kovaak_tracker/analysis_evidence.py` only for visual channel/event/outcome-association registration
- `kovaak_tracker/video.py`
- `kovaak_tracker/vision.py`
- `kovaak_tracker/tracking.py` only for reviewed detector primitives
- `kovaak_tracker/start_frame.py` only for canonical window adapter
- `webapp/backend/worker.py`
- `webapp/backend/contracts.py` only for the new registered producer/profile refs
- `webapp/backend/evidence_store.py` only if the generic Task 3 writer cannot consume the registered artifact without a versioned adapter
- create `tests/test_visual_signals.py`
- `webapp/tests/test_worker.py`
- small, redistributable fixtures under `tests/fixtures/visual_signals/`

### Tests first

- synthetic target/crosshair tracks recover position、identity、radius 和 known change-points within declared tolerance；
- fps/frame PTS 映射到 CanonicalTimeWindow，variable/missing frame 明确 partial；
- occlusion、effects、results UI、no target、multiple targets 产生 confidence/limitations，不补虚假 track；
- track identity crossing/re-entry 有 deterministic behavior；
- 每个 producer/version 绑定 annotation protocol 和量化 visual quality profile；center/radius error、identity switch、occlusion reentry、coverage 分别 gate 可用 metric families；
- quality profile 明确 validated visual domains；resolution/UI/theme/map/target appearance/capture transform/FOV 不匹配时 runtime compatibility fail-closed 为 limited/rejected；
- directly observed / validated aligned / inferred / unavailable OutcomeAssociation 分开，`.perf` 约 1 Hz aggregate 或 nearest-target 不得伪造逐 shot-target 真值；
- output 只写 local Signal/Event artifact 和 safe summary；
- MP4 failure 保留 native/outcome result；
- Coach payload/tool trace 无 frame/image/video bytes/path。

### 实施步骤

1. 审计旧 `tracking.py`/`vision.py` primitives，逐个用 synthetic fixture 证明后再复用。
2. 实现 frame -> canonical time、crosshair detector、multi-target tracker、radius/confidence 和可观测 outcome association。
3. 写 low-confidence/occlusion events 和 deterministic track smoothing；原始 frame 不进入 derived contract。
4. 建立可审计 annotation protocol/quality profile 和 visual-domain compatibility predicate，以量化阈值决定每个 metric family 的 accepted/limited/rejected；未通过或域不匹配只降级。
5. worker 在 multimodal 路径先写 visual Signal/Event artifact，再由 family analyzer 消费。
6. 用人工标注真实片段量化 center/radius/identity/reentry/coverage；测试 fixture 必须可合法随仓库分发。

### Verify

```powershell
$env:KOVAAK_INSTALL_DIR='Z:\__aiming_cookie_test_missing__'
python -m pytest tests/test_visual_signals.py webapp/tests/test_worker.py -q
```

### Stop rule

- 需要修改或重新实现 MP4 capture；
- detector 只能靠单一真实视频调参且无可复现 fixture；
- 无法量化 target/crosshair confidence；
- 需要把视频、frame 或外部媒体 URL 交给 Coach/Provider。

## Task 7 - Dynamic clicking analyzer

### 目的

实现目标移动条件下的 acquisition/click error/relative velocity 指标、证据片段和确定性 diagnosis，不把 static flicking 阈值直接套用到动态目标。

前置：Task 10 已为本 family 提供可解析的 definition/scope/limitation/prescription/verification knowledge refs。

### Allowed files

- `knowledge/scenarios/launch-manifest.v1.json` only to activate reviewed dynamic-clicking entries after this Task's gates pass
- create `kovaak_tracker/dynamic_clicking_analysis.py`
- create `kovaak_tracker/advice_dynamic_clicking.py`
- `kovaak_tracker/analysis_evidence.py` only for dynamic family registration
- `kovaak_tracker/coach/diagnosis.py`
- `webapp/backend/worker.py`
- `webapp/backend/contracts.py` only for analyzer/version registration
- `webapp/backend/evidence_store.py` only for a versioned generic-store adapter
- `webapp/backend/history_trends.py`
- create `tests/test_dynamic_clicking_analysis.py`
- `tests/test_scenario_profiles.py`
- create `tests/coach/test_advice_dynamic_clicking.py`
- `webapp/tests/test_worker.py`
- `webapp/tests/test_coach_context.py`

### Tests first

- known target motion/click fixtures 正确计算 normalized error、miss vector、target-state conditioned accuracy、relative velocity；
- predictable、reactive、unknown motion 使用不同允许 claim；
- target/click association ambiguous 时 event unavailable，不强配；
- 没有 directly observed/validated OutcomeAssociation 时 click-relative error 可用，但 target-conditioned accuracy/outcome claim unavailable；
- `predictive_lead` 需要 segment-level scripted/periodic/repeatability/model-fit evidence；只有 ScenarioProfile 标签时仅输出 relative lag/lead descriptor；
- MotionPredictabilityEvidence 保存 segment/model/version/fit metric/value/threshold/source refs；正式 predictive-lead metric 缺该 accepted ref 时 schema/validator 拒绝 mechanism claim；
- 视觉 confidence/coverage 不足时降级 input-only/outcome-only；
- static-matched comparison 只有 comparability 成立才出现；
- diagnosis 使用 observation -> alternatives -> prescription -> retest，未校准不产生绝对 severity。
- dynamic-clicking manifest entry 只有在 analyzer、knowledge、fixture 与 visual-quality prerequisites 全部通过后从 `pending_gate` 改为 `active`；

### 实施步骤

1. 先实现纯 analyzer synthetic fixtures，不接 worker。
2. 生成 dynamic event chain、MetricRecords 和典型/失败/对照片段。
3. 添加只依赖正式 metrics 的 deterministic advice rules。
4. 接 ScenarioProfile dispatch、Coach projection 和 History comparability。

### Verify

```powershell
$env:KOVAAK_INSTALL_DIR='Z:\__aiming_cookie_test_missing__'
python -m pytest tests/test_dynamic_clicking_analysis.py tests/test_scenario_profiles.py tests/coach/test_advice_dynamic_clicking.py webapp/tests/test_worker.py webapp/tests/test_coach_context.py -q
```

### Stop rule

- target identity/click association 无法可靠建立；
- 需要用 target 名称或最终 accuracy 猜 predictive/reactive 机制；
- 需要发明未经校准的 target-speed 阈值；
- 需要修改 visual producer 合同而未先版本化。

## Task 8 - Continuous tracking analyzer

### 目的

实现 error/time-on-target、loss/reacquisition、lag/gain、change response、correction burden 和 smoothness，并区分 predictable/reactive/control 条件。

前置：Task 10 已为本 family 提供可解析的 definition/scope/limitation/prescription/verification knowledge refs。

### Allowed files

- `knowledge/scenarios/launch-manifest.v1.json` only to activate reviewed tracking entries after this Task's gates pass
- create `kovaak_tracker/tracking_analysis.py`
- `kovaak_tracker/advice_tracking.py`
- `kovaak_tracker/analysis_evidence.py` only for tracking family registration
- `kovaak_tracker/coach/diagnosis.py`
- `webapp/backend/worker.py`
- `webapp/backend/contracts.py` only for analyzer/version registration
- `webapp/backend/evidence_store.py` only for a versioned generic-store adapter
- `webapp/backend/history_trends.py`
- create `tests/test_tracking_analysis.py`
- `tests/test_scenario_profiles.py`
- `tests/coach/test_advice_tracking.py`
- `webapp/tests/test_worker.py`
- `webapp/tests/test_coach_context.py`
- `webapp/tests/test_history_trends.py`

### Tests first

- zero lag、known lag、under/over gain、loss/reacquisition、change-point fixtures 精确恢复已知结果；
- alignment offset 与 human response descriptor 分开，系统误差不能标成人的反应时间；
- hitbox/radius 缺失时 time-on-target unavailable；
- frequency/coherence 只在足够长且近似平稳片段可用；
- reactive/predictable/control scenario 不互套阈值；
- predictable tracking 的 predictive-lead claim 同样要求 accepted MotionPredictabilityEvidence ref；场景标签或 analyzer 内部布尔值不足；
- low confidence/occlusion segments 不进入 severity；
- visual producer quality profile 未通过对应 metric-family threshold 时只 descriptive/outcome-only，不进入 profile/plan；
- 旧 `analysis.py` 数值只有通过 parity/review 的部分才迁移，其余保持 legacy。
- tracking manifest entry 只有在 analyzer、knowledge、fixture 与 visual-quality prerequisites 全部通过后从 `pending_gate` 改为 `active`；

### 实施步骤

1. 用 analytical synthetic signals 锁定每个 metric 的定义、方向和窗口。
2. 实现 tracking episodes/change-points/loss/reacquisition events。
3. 生成 MetricRecords 与 best/typical/failure/recovery segments。
4. 审查并迁移 `advice_tracking.py`：`None`/未校准阈值保持 info/experimental。
5. 接 worker、Coach context/tools 和 exact/matched History。

### Verify

```powershell
$env:KOVAAK_INSTALL_DIR='Z:\__aiming_cookie_test_missing__'
python -m pytest tests/test_tracking_analysis.py tests/test_scenario_profiles.py tests/coach/test_advice_tracking.py webapp/tests/test_worker.py webapp/tests/test_coach_context.py webapp/tests/test_history_trends.py -q
```

### Stop rule

- 没有 target/crosshair/hitbox 真值却要求 target-relative metric；
- lag 无法与 capture/alignment latency 分离；
- 需要把 correction count 直接解释为张力/视觉/认知原因；
- 旧模块结果无法通过新 fixture 仍要原样接入生产。

## Task 9 - Target switching analyzer

### 目的

实现 previous outcome -> leave -> candidate/selection -> transition -> acquire -> settle/fire/damage 事件链，并把 selection、transition 和 terminal control 与普通 flick 区分。

前置：Task 10 已为本 family 提供可解析的 definition/scope/limitation/prescription/verification knowledge refs。

### Allowed files

- `knowledge/scenarios/launch-manifest.v1.json` only to activate reviewed switching entries after this Task's gates pass
- create `kovaak_tracker/target_switching_analysis.py`
- create `kovaak_tracker/advice_target_switching.py`
- `kovaak_tracker/analysis_evidence.py` only for switching family registration
- `kovaak_tracker/coach/diagnosis.py`
- `webapp/backend/worker.py`
- `webapp/backend/contracts.py` only for analyzer/version registration
- `webapp/backend/evidence_store.py` only for a versioned generic-store adapter
- `webapp/backend/history_trends.py`
- create `tests/test_target_switching_analysis.py`
- `tests/test_scenario_profiles.py`
- create `tests/coach/test_advice_target_switching.py`
- `webapp/tests/test_worker.py`
- `webapp/tests/test_coach_context.py`
- `webapp/tests/test_history_trends.py`

### Tests first

- synthetic multi-target identities 产生正确 transition/acquire/first-shot events；
- known distance/direction fixtures 正确归一化 transition time/path efficiency；
- concurrent candidates 下 selection observable/unobservable 分开；
- `previous outcome`、`first_damage` 和 target-conditioned metrics 只消费 directly observed/validated OutcomeAssociation；`.perf` aggregate 或 nearest-target association 不足时 unavailable；
- target identity 丢失时退化为 `unclassified_discrete_acquisition`，不生成 selection error；
- carry-over overshoot 与普通 terminal correction 分开；
- issue/segment/plan refs 完整且不复用 static absolute thresholds。
- switching manifest entry 只有在 analyzer、knowledge、fixture 与 visual-quality prerequisites 全部通过后从 `pending_gate` 改为 `active`；

### 实施步骤

1. 先冻结 switching event state machine 和 ambiguous transitions。
2. 实现 distance/direction/target-state conditioned metrics。
3. 生成 transition、selection、acquisition、settle 的独立 EvidenceSegments。
4. 添加确定性 advice 与 verification targets。
5. 接 worker、Coach tools/context 和 History。

### Verify

```powershell
$env:KOVAAK_INSTALL_DIR='Z:\__aiming_cookie_test_missing__'
python -m pytest tests/test_target_switching_analysis.py tests/test_scenario_profiles.py tests/coach/test_advice_target_switching.py webapp/tests/test_worker.py webapp/tests/test_coach_context.py webapp/tests/test_history_trends.py -q
```

### Stop rule

- 无法稳定识别 target identity/previous outcome；
- 需要把多个 click-anchored flick 直接命名为 switching；
- 需要猜“正确目标”或 selection priority；
- 需要跨 aim family 复用未经验证的阈值。

## Task 10 - Minimum cross-family Knowledge Registry and diagnosis contracts

### 目的

在 Task 6 后、Task 7-9 前，补齐 dynamic clicking、tracking 和 switching 的 definition、scope、limitations、处方、feedback 与 transfer/retest 最小知识覆盖，并冻结各 family diagnosis 输出引用合同。知识只解释后续 analyzer 的正式 facts，不自己触发 diagnosis。

### Allowed files

- `knowledge/coach/schema.v1.json` only if new validated enum/category is required
- `knowledge/coach/registry.v1.json`
- `knowledge/coach/migration-audit.v1.json`
- `kovaak_tracker/coach/knowledge_registry.py`
- `kovaak_tracker/coach/diagnosis.py`
- `webapp/coach-runtime/src/knowledge-registry.ts`
- `webapp/coach-runtime/src/knowledge-tools.ts`
- `tests/coach/test_knowledge_registry.py`
- `tests/coach/test_diagnosis.py`
- `webapp/coach-runtime/test/knowledge-registry.test.ts`
- `webapp/coach-runtime/test/knowledge-tools.test.ts`
- `webapp/coach-runtime/test/knowledge-parity.test.ts`
- `webapp/coach-runtime/test/knowledge-analysis-e2e.test.ts`

### Tests first

- spec 已冻结的每个新增 canonical signal/metric 至少有 active entry 和 limitations，后续 analyzer 可以从第一条正式 issue 起引用；
- static、dynamic、predictable/reactive/control tracking 与 switching 全部通过 spec 9.1 的 knowledge coverage matrix；每项含 definition/scope/quality prerequisite/expected direction/limitations/counterevidence/cue/dose guardrail/matched retest/near-transfer retest，且 direction 只能使用冻结 enum；
- movement outcome-only 的 cue/dose/retest 固定 `not_applicable`，并验证不能生成 issue、prescription、profile contribution 或 plan item；
- academic/community/product/experimental claim level 不越级；
- predictable/reactive、smoothness/correction、switching/ordinary flick 边界明确；
- exact signal/metric/topic retrieval 最多 3 条，零命中不全库 fallback；
- Python/TS parity、migration audit、historical refs 可解析；
- knowledge entry 不生成 measured fact、severity 或自动处方。

### 实施步骤

1. 为每个正式 analyzer 建 signal/metric -> knowledge coverage matrix。
2. 优先写 definition、scope、limitation、counterevidence，再写 cue/prescription。
3. 学术来源锚定机制/验证；社区 taxonomy/drill 明确标 community practice。
4. 扩 migration audit 和 Python/TS parity fixtures。
5. 先用 versioned family contract fixtures 做 diagnosis-shape -> retrieval -> Pi refs-only E2E；Task 7-9 再分别补真实 analyzer E2E。

### Verify

```powershell
python -m pytest tests/coach/test_knowledge_registry.py tests/coach/test_diagnosis.py -q
$env:PI_SOURCE_DIR = (Resolve-Path third_party\pi).Path
$env:TSX_TSCONFIG_PATH = (Resolve-Path third_party\pi\tsconfig.json).Path
$env:PYTHON_BIN = (Resolve-Path .venv\Scripts\python.exe).Path
$loader = (Resolve-Path third_party\pi\node_modules\tsx\dist\loader.mjs).Path
$loaderUrl = & node -e "const { pathToFileURL } = require('node:url'); process.stdout.write(pathToFileURL(process.argv[1]).href)" $loader
$tests = (Get-ChildItem webapp\coach-runtime\test -Filter 'knowledge*.test.ts' | Sort-Object Name).FullName
node "--import=$loaderUrl" --test @tests
```

### Stop rule

- 需要用知识 entry 替代 analyzer 或触发 severity；
- 来源无法支持 claim level 或要求保留绝对未校准阈值；
- Python/TS 不能读取同一 canonical Registry；
- 需要在线搜索、embedding 或模型自动改 Registry。

## Task 11 - Persistent aiming profile, plan and retest loop

### 目的

把多 Run 的可比 evidence 聚合为可更新画像，并让当前 production Training Plan 关联 diagnosis、baseline、目标、执行、matched retest 和 near-transfer retest。

### Allowed files

- create `webapp/backend/aiming_profile_store.py`
- `webapp/backend/db.py`
- `webapp/backend/history_trends.py`
- `webapp/backend/training_plan_store.py`
- `webapp/backend/queue.py`
- `webapp/backend/worker.py`
- `webapp/backend/coach_commands.py`
- `webapp/backend/coach_context.py`
- `webapp/backend/schemas.py`
- `webapp/backend/routes.py`
- create `webapp/tests/test_aiming_profile_store.py`
- `webapp/tests/test_db.py`
- `webapp/tests/test_history_trends.py`
- `webapp/tests/test_training_plan_store.py`
- `webapp/tests/test_queue.py`
- `webapp/tests/test_worker.py`
- `webapp/tests/test_coach_commands.py`
- `webapp/tests/test_coach_context.py`
- `webapp/tests/test_routes_coach.py`

### Tests first

- profile dimension 只从 comparable deterministic metrics 更新；
- append-only profile contribution 以 analysis ref 幂等；terminal retry 不重复，删除先 invalidation 再确定性重建，startup reconciliation 修复中断状态；
- 单次低 confidence Run 不覆盖多次高 confidence trend；
- conflicting evidence 保留 refs/limitations，不被平均掉；
- exact scenario 与 validated cross-scenario normalized profile 分开；
- plan item 要求 diagnosis/knowledge/scenario/baseline/target/cue/dose/retest refs；
- plan item、plan execution、matched/near-transfer retest 有 stable owner/revision/status refs；完成剂量与用户反馈来自执行记录，不从聊天猜测；
- matched delayed retest 与 near-transfer retest 都可记录/复盘；
- analysis deletion 保留 Coach/profile/history 引用 tombstone，不保留已删 derived data，已删 contribution 不再支撑 current profile；
- `profile.aiming.snapshot` 只在 store/migration ready 后注册，返回 bounded current dimensions/contribution/retest refs；
- command idempotency、owner scope、activate/adjust/review 确认合同无回归。

### 实施步骤

1. 先建 SQLite migration、append-only contribution 和 read/write model，profile update 使用 idempotent analysis ref。
2. 扩 comparability predicate、conflict-preserving aggregation、deletion invalidation/rebuild 和 startup reconciliation。
3. 扩 production Training Plan schema/commands，加入 plan item/execution/retest stable facts；不接回 legacy `planning.py`。
4. 在 analysis terminal commit 后创建/替换 contribution；retry 不重复累计；删除事务先 invalidation，失败可恢复。
5. store ready 后注册 `profile.aiming.snapshot`，default Coach context 返回 bounded profile/retest refs。

### Verify

```powershell
$env:KOVAAK_INSTALL_DIR='Z:\__aiming_cookie_test_missing__'
python -m pytest webapp/tests/test_aiming_profile_store.py webapp/tests/test_db.py webapp/tests/test_history_trends.py webapp/tests/test_training_plan_store.py webapp/tests/test_queue.py webapp/tests/test_worker.py webapp/tests/test_coach_commands.py webapp/tests/test_coach_context.py webapp/tests/test_routes_coach.py -q
```

### Stop rule

- 需要把 experimental/inferred metric 写成正式能力画像；
- 需要让 LLM 直接写数据库或绕过 plan commands；
- 无法保证 retry/idempotency 或 owner isolation；
- 需要删除 Coach/profile/plan 才能删除 Analysis。

## Task 12 - Integrated release gates and frontend handoff

### 目的

用跨 family E2E、隐私 sentinel、性能基准和真实 Run validation 证明完整下游链可上线，并把 EvidenceSegment 本地播放接口交给正式 frontend plan。

### Allowed files

- focused tests under `tests/**`, `webapp/tests/**`, `webapp/coach-runtime/test/**`
- small redistributable fixtures under `tests/fixtures/**`
- `docs/PROGRESS.md`
- `docs/ROADMAP.md` only for Gate status, not product scope changes
- `docs/DEVELOPMENT.md` only for stable commands/fixture instructions
- `docs/superpowers/plans/2026-07-20-complete-coach-analysis-context-v1.md` only for completion evidence
- frontend files are not allowed; playback implementation must be added to and authorized through the active frontend plan after the backend interface is frozen

### Tests first

- one E2E each for static clicking、dynamic clicking、tracking、switching、unknown/outcome-only，以及已识别 `movement_aiming` 但仍禁止机制诊断的 outcome-only；
- source -> canonical window -> analyzer -> evidence -> Coach query -> knowledge -> plan/profile refs 全链；
- small Run 的完整 allow-listed CanonicalRunFacts 进入默认 Coach context；oversized facts 显式分节，exact timeline/events 可用 owner-bound cursor 无损分页；
- source field Registry golden 覆盖所有首发已知 Stats/Performance fields；known field 的 present/source-absent/omitted 与 unknown-field observability 可审计；
- Coach 能消费 scenario/config/outcome totals、whole-run/segment timeline 和 normalized events，同时原始 CSV/protobuf/private parser/unknown fields sentinel 不进入 payload/tool trace/message；
- 当前 loopback bridge transport response/Provider transcript 可见完整 evidence tool result，而 SQLite command journal、tool event、message trace、普通 API response/log 只含 audit projection；恢复/retry 不依赖已丢弃的完整 result；
- no Provider 时 deterministic analysis/evidence/History 可用；
- raw/path/secret/video/frame sentinel 覆盖 payload、tools、trace、message、logs、API；
- crash/retry/deletion/reconciliation/idempotency；
- latency/CPU/peak memory/artifact size/context token/query budget 基准；
- real Run validation 记录标注协议、validated visual domain、runtime compatibility、center/radius/identity/reentry 的量化预期/实测误差、coverage/quality profile 和未通过项；
- OutcomeAssociation 缺失/ambiguous/projectile aggregate 时，dynamic/switching 的 target-conditioned outcome claims 正确 unavailable。
- static/dynamic/tracking/switching 每个 launch family 至少一个 advertised scenario hash 通过对应 fixture/analyzer/knowledge/visual-quality Gate；manifest 外和 user-declared fallback 均不产生正式 family claim。

### 实施步骤

1. 先列 release matrix 和 expected failures，不用单一 happy-path 冒充完整覆盖。
2. 运行 focused/full Python、Pi、TypeScript、schema、sentinel 和 diff checks。
3. 在不提交私人原始数据的前提下运行真实 Run validation，保存聚合/标注证据到 Progress。
4. 冻结 EvidenceSegment API/local playback adapter，向 frontend plan 增加独立 Task；本 Task 不越权修改 UI。
5. 只有所有 Gate 通过才把 plan 标 completed；未通过项保留在 Roadmap/Progress。

### Verify

```powershell
$env:KOVAAK_INSTALL_DIR='Z:\__aiming_cookie_test_missing__'
python -m pytest -q
$env:PI_SOURCE_DIR = (Resolve-Path third_party\pi).Path
$env:TSX_TSCONFIG_PATH = (Resolve-Path third_party\pi\tsconfig.json).Path
$env:PYTHON_BIN = (Resolve-Path .venv\Scripts\python.exe).Path
$loader = (Resolve-Path third_party\pi\node_modules\tsx\dist\loader.mjs).Path
$loaderUrl = & node -e "const { pathToFileURL } = require('node:url'); process.stdout.write(pathToFileURL(process.argv[1]).href)" $loader
$tests = (Get-ChildItem webapp\coach-runtime\test -Filter *.test.ts | Sort-Object Name).FullName
node "--import=$loaderUrl" --test @tests
git diff --check
git status --short
```

### Stop rule

- 缺少任一 launch family 的可复现 fixture 或真实验证；
- privacy/cost/owner/correctness Gate 未通过；
- 需要把私人 Raw/MP4/Stats/Performance 提交到仓库；
- 需要绕过 frontend plan 直接修改正式 UI；
- 只能靠 Coach prompt 隐藏 analyzer/contract 缺口。

## 4. 全局验收

- 每个 completed Task 报告 changed files、验证命令/结果、未运行检查、偏差、风险和最终 `git status`。
- 每次只执行一个已授权 Task；未经点点明确指示不 commit、不 push、不开始下一个 Task。
- 所有 Python 测试设置不存在的 `KOVAAK_INSTALL_DIR`，不得扫描本机真实 KovaaK 数据。
- 任何 Task 发现需要扩大 Allowed files、修改冻结合同或依赖采集 worktree 未合入改动，立即停止并回到 spec/plan 审阅。
- 完整 Coach 的上线判断以 Task 12 Gate 为准，不以“代码路径存在”或“LLM 能回答”代替可验证完成度。
