# Complete Coach Analysis and Evidence Context v1 - Implementation Plan

> **状态：active。** 2026-07-20 已完成采集集成与 pre-activation 文档治理，点点已批准激活并授权从 Task 1 起继续推进。每次仍只执行一个 Task，并遵守 Allowed files、Tests first、冻结决策和 Stop rule。
> **For executor:** implement only the explicitly authorized Task; obey Allowed files, Tests first, frozen decisions and Stop rule.
> 依赖 spec：[`../specs/2026-07-20-complete-coach-analysis-context-design.md`](../specs/2026-07-20-complete-coach-analysis-context-design.md)
> 相关 spec：[`../specs/2026-07-13-analysis-evidence-coach-context-design.md`](../specs/2026-07-13-analysis-evidence-coach-context-design.md)、[`../specs/2026-07-14-versioned-coach-knowledge-registry-design.md`](../specs/2026-07-14-versioned-coach-knowledge-registry-design.md)、[`../specs/2026-07-17-automatic-run-capture-design.md`](../specs/2026-07-17-automatic-run-capture-design.md)

**Goal:** 把 Run-owned Raw、Stats、Performance 与 optional MP4 转成覆盖 static/dynamic clicking、tracking 和 target switching 的可追溯分析，并通过有界证据工具驱动 Coach、画像、训练计划和复测闭环。

**Architecture:** 先统一时间和场景语义，再把高频完整时间序列留在私有 derived artifacts；每个 aim family 使用独立 analyzer 产出覆盖全部分析单位的 ProcessedEventTable、统一 MetricRecord/EvidenceSegment 和确定性候选 diagnosis。Coach 默认获得预算内的完整规范化 Run facts、动作表目录与安全摘要，通过现有 owner-scoped product-command bridge 对完整 processed rows 做固定的精确、排序、筛选、聚合、共同出现、时序与反例分析，再形成最终教学解释。

**Tech Stack:** Python 3.9、FastAPI、SQLite/app-owned workspace、现有 Pi TypeScript runtime、OpenCV/NumPy/Pandas、JSON schema、pytest/Node tests。

---

## 1. 当前实现事实

- 正式 worker 只有 static flicking producer；`analysis_type` 的存储字段可接受字符串不等于 tracking/switching 已接通。
- `kovaak_tracker/tracking.py`、`analysis.py`、`advice_tracking.py` 是候选/旧实现，不能直接当成 v2 producer。
- Coach 已有 allow-list context、Knowledge Registry 和 owner-scoped `run_product_command` bridge；新 evidence query 应复用这条通道。
- 生产 Training Plan 是安全持久化命令；旧 `coach/planning.py` 的确定性计划逻辑没有接到当前产品主链。
- capture closeout commit `b8c8502` 已通过正常 Git merge 集成，保留 `time_alignment.v2`、Run window persistence、Capture Coordinator/Finalizer 与 lifecycle repair 的实现和证据。
- 合入后的 worker snapshot 仍未冻结并传递 CanonicalTimeWindow；native analyzer 会重新 resolve source，legacy duration 路径仍含 end-inclusive compatibility。Stats/Performance parser 的 presence/order、跨午夜、per-weapon、multi-payload 和 timestamp validity 也仍是 Task 1 delta；必须先修 correctness，再扩 analyzer。
- capture 实现与验证是 Task 1 的基线；本计划只修列明的 correctness delta，不重做或回退已经闭合的采集能力。

## 2. 全局冻结决策

1. Coach 不接收 Raw、完整私有 signal arrays、未经过 allow-list 的内部 event payload、MP4、frames、路径或任意查询语言；完整、类型化、字段白名单化的 ProcessedEventTable 不属于禁止项。
2. Coach 可以接收当前 schema 下完整 CanonicalRunFacts、完整 processed event table 的目录/预算内紧凑索引，并按预算用固定操作查询全部 processed rows、whole-run/segment outcome timeline 与规范化 events；不得把原始 CSV/protobuf/private parser/analyzer payload 冒充这些事实。
3. 用户启用 Coach 并选择 Provider 后，L1-L3 bounded context/tool results 可以作为普通 Coach turn 数据发送给该 Provider，不设逐 Run consent；L0 不发送是成本/体量/可消费性边界，不是敏感数据分级。
4. Coach 通过既有 product-command bridge 查询受限 evidence，不新建任意数据通道。
5. MP4 在 v1 只由本地确定性预处理器消费；Coach 引用 EvidenceSegment，用户在 UI 播放本地片段。
6. 当前不让 Coach 读取视频是能力/成本边界，不是永久禁令；未来视觉模型必须另立版本化、显式授权、限定片段合同。
7. scenario hash 是正式身份；名称 heuristic 不能驱动 family-specific diagnosis。
8. static clicking、dynamic clicking、tracking、switching 分别有 analyzer；共享 schema，不共享未经验证的阈值。
9. movement aiming 在没有玩家移动遥测时保持 outcome-only。
10. 未校准 metric 可以做分布、自身 baseline 和 matched comparison，不能硬写绝对健康线。
11. 每个用户问题默认 1 个主 EvidenceSegment，最多 2 个补充片段；这是解释/视频回放引用上限，不是 Coach 可见动作数量上限。
12. measured/derived 数值、事件、时间、来源、质量和 limitations 是不可改写事实；deterministic diagnosis/prescription 是候选观察与初始排序。Coach 可依据支持证据、反例、历史和知识接受、降低或拒绝候选解释，但不得重算正式指标或把假设写成测量。
13. 所有 schema、metric、scenario、analyzer、alignment 和 knowledge 引用都必须版本化并可回放。

## 3. 执行顺序

```text
Pre-activation upstream reconciliation/activation (not an executable Task)
  -> Task 1 canonical time
  -> Task 2 scenario profiles
  -> Task 3 signal/evidence artifacts
  -> Task 4 bounded Coach evidence queries
  -> Task 5 static clicking migration
  -> Task 5A processed event tables and Coach synthesis authority
  -> Task 6 visual numerical preprocessing
  -> Task 6A validated outcome association repair
  -> Task 10R cross-family knowledge research prerequisite (completed 2026-07-22)
  -> Task 10 minimum knowledge/diagnosis coverage
  -> Task 7 dynamic clicking
  -> Task 8 tracking
  -> Task 9 target switching
  -> Task 11 profile/plan/retest loop
  -> Task 12 integrated release gates
```

Task 10R 研究前置已完成，但不替代 Task 6 的代码和真实 visual-quality Gate。Task 6A 先补齐 Stats outcome、Raw click 与 reviewed visual track 的版本化关联合同，并修复 kill/first-damage 语义；没有 accepted rule/profile 时继续 fail closed。Task 10 只有在 Task 6/6A 完成后才可正式通过 Gate，并且是 Task 7、8、9 的前置条件；它先补 definition/scope/limitation 和 prescription/verification 的最小知识覆盖。Task 7、8、9 随后可以分别开发，但共享 contract 或 worker dispatch 的改动必须串行合入并分别回归，不能让多个 executor 同时编辑同一文件。

## Pre-activation governance record - completed 2026-07-20

### 目的

采集分支已通过正常 Git merge 集成；架构/文档治理会话已把点点确认的完整 Coach 上线目标写回主责任事实源，解决原 PRD “v1 flicking-first / tracking later” 与新目标的冲突，并完成 spec/plan 激活。本节仅保留激活审计记录，不是可重复执行的 plan Task。

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
- `docs/archive/completed/plans/2026-07-13-frontend-product-reconstruction.md`
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

### Completed review steps

1. [x] 采集 worktree 通过正常 Git merge 集成，双方提交均保留。
2. [x] PRD/Architecture/Roadmap 与本 spec 完成分层协调，产品/稳定合同只写入对应主责任文档。
3. [x] 本 spec 的硬预算、scope 和未来 vision extension 已审阅。
4. [x] 点点批准 spec/plan 从 proposed 转为 active，并授权从 Task 1 起继续推进。
5. [x] 链接、状态、冲突标记、diff 与回归检查纳入本次集成验证。

### Verify

```powershell
rg -n "complete-coach-analysis-context|状态：active|状态：proposed" docs
git diff --check
git status --short
```

### Activation stop rule - closed

- 采集提交、上游 scope、文档归属与激活授权均已闭合；若未来再次出现这些条件，必须重新停止而不是沿用本记录绕过治理。

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

- `docs/superpowers/specs/2026-07-20-complete-coach-analysis-context-design.md` only for the approved empty-manifest/fail-closed transition amendment
- `docs/superpowers/plans/2026-07-20-complete-coach-analysis-context-v1.md` only for the matching Task 2 contract/Allowed-files amendment
- create `knowledge/scenarios/schema.v1.json`
- create `knowledge/scenarios/registry.v1.json`
- create `knowledge/scenarios/launch-manifest.v1.json`
- create `kovaak_tracker/scenario_profiles.py`
- `webapp/backend/kovaak_run_store.py`
- `webapp/backend/contracts.py`
- `webapp/backend/worker.py`
- `webapp/backend/history_trends.py`
- create `tests/test_scenario_profiles.py`
- `webapp/tests/test_kovaak_runs.py`
- `webapp/tests/test_worker.py`
- `webapp/tests/test_contracts.py`
- `webapp/tests/test_history_trends.py`

### Tests first

- exact scenario hash 返回稳定 family/subdomain/allowed analyzers/metrics；
- 同名不同 hash 不合并，同 hash 显示名变化仍保持 identity；
- name-only heuristic 只能返回 candidate/unknown，不能 dispatch 正式 analyzer；
- unknown/pending/retired hash 不 dispatch family analyzer；Task 2 过渡结果可不含 family metrics、severity 或处方，完整 outcome + 通用 input metrics 由 Task 3 接管；
- schema 拒绝跨 entry duplicate hash、同一 hash 多个 active version、重复 entry version、unknown enums、空 limitations、越界 Registry；同一 entry 的历史 hash version 可回放；
- classification ref/source/review/supersession 可回放，旧 Analysis 继续解析当时 version；
- Registry version 与 scenario profile ref 进入 result/comparability；
- 首发对外宣称支持的每个 static/dynamic/tracking/switching scenario 都在 exact hash manifest 中有 fixture、审核来源、family/motion/target-count、allowed analyzer/metric 和 limitation；manifest 外一律 outcome-only；
- manifest status 只允许 `pending_gate | active | retired`；Task 2 可先登记 future-family `pending_gate` entry，但它不能 dispatch 正式 analyzer 或被 UI/Coach 宣称为支持。Task 5/7/8/9 分别在对应 Gate 通过后改为 `active`，Task 12 验证四个 launch family 齐全；
- 生产 Registry/manifest 在没有真实可分发 hash fixture 与审核证据时允许为空；空集合不宣称支持，不得由名称或测试 hash 填充；
- `classification_confidence` 只允许 `confirmed | candidate | unknown`；
- Task 2 新 snapshot 使用 `analysis_input_snapshot.v3` 并强制完整 `scenario_resolution.v1`；仅迁移前 v1/v2 缺失 resolution 时可进入 legacy compatibility，v3 缺失时 fail closed；
- 迁移前已冻结的 `analysis_type=flicking` request 仍能走受限 `legacy_static_compatibility`；新 unknown Run 不能自动继承。用户显式声明 static 的语义固定为 `classification_source=user_declaration`、`claim_ceiling=descriptive_only`、`family_analyzer_dispatch=none`，但真实输入入口延期到包含 request/route/UI Allowed files 的后续 Task；Task 2 不从现有字段猜测声明。

### 实施步骤

1. 先建立最小 Registry schema 与 exact hash launch manifest；生产资产可以为空。未来 manifest entry 至少包含 `scenario_hash`、`scenario_profile_ref`、`fixture_ref`、`review_source_ref`、`reviewed_at`、`family_gate_refs` 和 `status`，且不能靠 display name 关联。不追求一次覆盖全部 Workshop；未通过后续 analyzer/knowledge/visual Gate 的 entry 保持 `pending_gate`。
2. 从官方/editor metadata 或人工审核记录 family、motion、target-count 和允许指标；不从 Stats 名称自动升级。
3. 为每个 classification 保存 stable entry ref、source refs、review time、status 和 supersession chain。
4. 在 `analysis_input_snapshot.v3` 冻结完整 scenario hash/profile/review/supersession/manifest provenance，并在 worker dispatch 前验证；只对迁移前 v1/v2 frozen flicking request 保留 versioned legacy compatibility。Task 2 不实现或猜测用户声明入口。
5. 将 unknown/outcome-only 作为正常、用户可解释状态；Task 2 先保证不误 dispatch，Task 3 再补齐统一 outcome/general-metric producer。
6. 为后续 curated additions 保留审核流程，不加入在线下载或 LLM 分类。

### Verify

```powershell
$env:KOVAAK_INSTALL_DIR='Z:\__aiming_cookie_test_missing__'
python -m pytest tests/test_scenario_profiles.py webapp/tests/test_kovaak_runs.py webapp/tests/test_worker.py webapp/tests/test_contracts.py -q
python -m pytest webapp/tests/test_history_trends.py -q
```

### Stop rule

- 需要通过 scenario 名称或模型猜测直接产生正式分类；
- 没有 scenario hash 却要伪造稳定 identity；
- 需要在线服务、用户账号或自动修改 Registry；
- taxonomy 改变 PRD launch scope 而上游未更新。
- 非空 launch manifest 不能给每个 advertised hash 提供可分发 fixture、审核来源和 family Gate；没有合格资产时必须保持空集合。

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
- `webapp/coach-runtime/src/analysis-summary-tool.ts`
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
- `kovaak_tracker/analysis_evidence.py` only to register the versioned native static-clicking evidence extension
- `kovaak_tracker/native_flicking_analysis.py`
- `kovaak_tracker/advice.py`
- `kovaak_tracker/coach/diagnosis.py`
- `webapp/backend/worker.py`
- `webapp/backend/coach_context.py`
- `webapp/coach-runtime/src/analysis-summary-tool.ts` only to accept the frozen primary/supporting EvidenceSegment refs on issues
- `tests/test_native_flicking_analysis.py`
- `tests/test_scenario_profiles.py`
- `tests/coach/test_diagnosis.py`
- `webapp/tests/test_worker.py`
- `webapp/tests/test_coach_context.py`
- `webapp/tests/test_history_trends.py`
- `webapp/coach-runtime/test/system-prompt-and-tools.test.ts` only for the issue EvidenceSegment ref projection

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

## Task 5A - Processed event tables and Coach synthesis authority

> **实施状态：completed（2026-07-21）。** static 每次 flick 已投影为不复制 rows 的完整 ProcessedEventTable；新 Analysis 默认生成 `coach_diagnostic_context.v3` 表目录，并通过固定 exact/rank/filter/aggregate/co-occurrence/sequence/compare 查询消费全部 processed rows。规则 diagnosis 保持候选观察，Coach 必须检查支持证据与反例。验证结果记录在 [`../../PROGRESS.md`](../../PROGRESS.md)；Task 6 未开始。

### 目的

把 Task 5 已保存的每次 static flick event 正式投影为完整 ProcessedEventTable，让 Coach 默认知道表的字段、行数和完整度，并能对全部 processed rows 做 exact/rank/filter/aggregate/co-occurrence/sequence 与真实 event/segment comparison。规则层 diagnosis 改为候选观察与初始排序；Coach 基于支持证据和反例形成最终教学解释，但不重算指标。

### Allowed files

- `kovaak_tracker/analysis_evidence.py` only for ProcessedEventTable validator/catalog projection
- `kovaak_tracker/native_flicking_analysis.py` only for static field catalog/table metadata and existing-row refs; metric formulas/thresholds are frozen
- `webapp/backend/worker.py`
- `webapp/backend/contracts.py` only if the public safe table catalog requires a versioned result field
- `webapp/backend/coach_context.py`
- `webapp/backend/coach_commands.py`
- `webapp/backend/coach_runtime.py`
- `webapp/backend/coach_service.py`
- `webapp/backend/coach_store.py`
- `webapp/coach-runtime/prompts/coach-system.md`
- `webapp/coach-runtime/src/analysis-summary-tool.ts`
- `webapp/coach-runtime/src/product-command-tools.ts`
- `webapp/coach-runtime/src/contracts.ts`
- `tests/test_analysis_evidence.py`
- `tests/test_native_flicking_analysis.py`
- `webapp/tests/test_worker.py`
- `webapp/tests/test_coach_context.py`
- `webapp/tests/test_coach_commands.py`
- `webapp/tests/test_coach_runtime.py`
- `webapp/tests/test_coach_tool_runtime.py`
- `webapp/tests/test_coach_store.py`
- `webapp/coach-runtime/test/product-command-tools.test.ts`
- `webapp/coach-runtime/test/system-prompt-and-tools.test.ts`

### Tests first

- 每个有效 static flick 在 ProcessedEventTable 恰有一行；row count、included/excluded count、completeness、event refs 与 EventBundle 一致，不只包含 typical/worst/improved；
- static field catalog 对 movement/peak/accel/decel/settle、path/displacement/efficiency/straightness、reverse/correction/submovement、SPARC、quality/confidence/limitations 声明稳定 type/unit/metric version；无视觉事实时 target error、overshoot/undershoot 不出现；
- 新 Analysis 生成 `coach_diagnostic_context.v3`：总预算内 inline 的 compact index 覆盖全表；超预算整表切换为 table ref，仍提供完整 field catalog/row count/completeness/query capabilities，不允许部分 rows 冒充 complete；v1/v2 原样可读；
- `analysis.events.get` 同时要求 reachable table ref 与先前返回的 event ref，并验证 table membership；猜测、错误 event 类型、另一表、跨 owner、deleted/nonterminal/stale revision ref fail-closed；
- rank/filter/aggregate/co-occurrence/sequence 必须显式指定单一 table ref，只接受其 catalog 注册字段和固定 operator，不能从 analysis ref 猜表；filter 分页稳定，aggregate 返回固定 distribution，co-occurrence 同时返回 supporting/counterexample refs且不声称因果，sequence 只按固定 run phase/decile/adjacent 语义；
- 所有操作报告 evaluated/included/excluded、coverage、completeness 和 limitations；低质量/缺值不会被静默当 0；
- 两个 single-event static segments 的 compare 使用各自 event attributes，真实 `corrective_count=3` 与 `0` 必须返回不同值/delta，不能都返回整局中位数；多 event segment 只聚合范围内 rows；
- prompt 把数值/事件/时间/质量/limitations 视为不可改写事实，把 deterministic issue 视为可检验候选；形成诊断时要求检查支持证据和反例或明确其不可用；
- fixed query 仍受 bridge reachable refs、owner、call/byte budget、audit projection 和 raw/path/video/frame/secret sentinel 约束；Provider 看见完整结果，持久 journal/trace 只见 audit projection。

### 实施步骤

1. 在现有 EventBundle 上建立 ProcessedEventTable validator/catalog/read model，不复制或重算 rows。
2. 让 static adapter 为现有每-flick rows 声明 field catalog、完整度与 table ref；保持指标公式、阈值、SPARC version 和 target-relative fail-closed 不变。
3. 增加 v3 default context 的 processed table catalog/compact index，并保持 v1/v2 compatibility 与 32 KiB 上限。
4. 在现有 product-command bridge 增加 exact/rank/filter/aggregate/co-occurrence/sequence 固定 handlers；字段、operator、group、relation 均由服务端 allow-list，复用原子 budget/audit/cursor/reachability。
5. 修复 evidence compare：event 读 row value，segment 聚合范围内 rows，run 才读取 whole-run MetricRecord；不可比或字段缺失显式 unavailable。
6. 更新 Pi contracts/tools/prompt，使 Coach 先检验规则候选及反例，再给解释和训练方案；不把数据正文硬编码进 prompt。

### Verify

```powershell
$env:KOVAAK_INSTALL_DIR='Z:\__aiming_cookie_test_missing__'
python -m pytest tests/test_analysis_evidence.py tests/test_native_flicking_analysis.py webapp/tests/test_worker.py webapp/tests/test_coach_context.py webapp/tests/test_coach_commands.py webapp/tests/test_coach_runtime.py webapp/tests/test_coach_tool_runtime.py webapp/tests/test_coach_store.py -q
$env:PI_SOURCE_DIR = (Resolve-Path third_party\pi).Path
$env:TSX_TSCONFIG_PATH = (Resolve-Path third_party\pi\tsconfig.json).Path
$env:PYTHON_BIN = (Resolve-Path .venv\Scripts\python.exe).Path
$loader = (Resolve-Path third_party\pi\node_modules\tsx\dist\loader.mjs).Path
$loaderUrl = & node -e "const { pathToFileURL } = require('node:url'); process.stdout.write(pathToFileURL(process.argv[1]).href)" $loader
$tests = @((Resolve-Path webapp\coach-runtime\test\product-command-tools.test.ts).Path, (Resolve-Path webapp\coach-runtime\test\system-prompt-and-tools.test.ts).Path)
node "--import=$loaderUrl" --test @tests
```

### Stop rule

- 需要改变 static metric 公式、阈值、SPARC 或 segmentation；
- 需要让 Coach 读取 Raw samples、未注册 event attributes、任意表达式/代码/时间范围或 artifact path；
- 无法为某字段给出稳定 type/unit/version，却仍要求 Coach 排序或比较；
- v3 无法在不静默丢表/rows 的情况下满足 context/response budget；
- compare 只能继续复用 whole-run MetricRecord 才能返回结果；
- 需要把 LLM 候选因果写回正式测量、severity、profile 或 plan target。

## Task 6 - Local visual numerical preprocessing

> **实施状态：dynamic detector v2 candidate Gate passed；一个 exact single-target Tracking producer 已通过真实 production Gate（2026-07-24）。** dynamic numerical producer、artifact/worker integration、annotation-quality evaluator、false-positive Gate、reviewed exclusion regions、canonical detector-config hash binding 和 bounded visual events 均已实现。producer `visual_round_detector.circularity_0_60_center_overlay_0_50.v2` 在中心单目标被准星遮挡时做受限恢复；同一 component 出现多目标峰时不猜目标或 identity，而是输出 `merged_target_component` ambiguity，并以 `target_merge_ambiguous` 禁用 tracking/switching。独立 calibration 为 `60/62`、holdout 为 `74/78`，合计 `134/140` matched targets、`0` observed false positives、center P95 `2.96 px`、radius error `0.76 px`、coverage `95.71%`；这仍只支持 exact scenario/config 的 dynamic-clicking candidate。另一个严格绑定 scenario hash `b2ae4a24b710e36afc6e57c61f590ab4`、`1920x1080`、单目标的 producer 复用旧黑球 HSV 初始化与 CSRT 连续跟踪，不回退 KCF；真实 60 秒 Run 为 `3599/3600` 有目标样本、`99.972%` coverage，已可供 Task 8 production dispatch。该成功不得外推到其它 Tracking、Dynamic 或 Switching 场景。证据索引见 [`../assessments/2026-07-22-real-run-analysis-capability-audit.md`](../assessments/2026-07-22-real-run-analysis-capability-audit.md)。

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
- `tests/test_analysis_evidence.py` only for OutcomeAssociation cross-field validation regressions
- `webapp/tests/test_worker.py`
- small, redistributable fixtures under `tests/fixtures/visual_signals/`

### Tests first

- synthetic target/crosshair tracks recover position、identity、radius 和 known change-points within declared tolerance；
- fps/frame PTS 映射到 CanonicalTimeWindow，variable/missing frame 明确 partial；
- occlusion、effects、results UI、no target、multiple targets 产生 confidence/limitations，不补虚假 track；
- track identity crossing/re-entry 有 deterministic behavior；
- 先用 assessment 已索引的 normal/timescale/restart 实机四件套做离线 field Gate，pause/Raw-tail gap 只能做负例；不重新索要同类采集；
- 每个 producer/version 绑定 annotation protocol 和量化 visual quality profile；center/radius error、false positive、identity switch、occlusion reentry、coverage 分别 gate 可用 metric families；
- runtime selector 只使用 exact scenario hash、decoded resolution、canonical video mapping version，以及 profile 明确依赖时的 Stats FOV；背景/目标/HUD 语义属于 annotation/profile review metadata，不从视频猜标签，也不得要求 capture receipt 提供；
- quality profile 按 metric family 声明 required selector keys；未知但无关的条件不 blanket-block，其它 scenario/profile 或实际不匹配仍 fail-closed 为 limited/rejected；candidate morphology/coverage 只能作为 runtime quality，不得让 detector 循环自证正确；
- directly observed / validated aligned / inferred / unavailable OutcomeAssociation 语义分开，`.perf` 约 1 Hz aggregate 或 nearest-target 不得伪造逐 shot-target 真值；
- OutcomeAssociation validator 拒绝 kind/availability/ref/confidence/limitation 的矛盾组合；available 只能引用 hit/miss/kill/first_damage outcome event，不能把 shot 自身当 outcome，target track ref 必须绑定当前 Analysis 且在最终 artifact 中可达完整 position x/y + radius/hitbox channels；EventBundle v1 没有可解析的 validation-rule registry/binding，因此一律拒绝 `validated_aligned`，不能用第二条 opaque provenance ref 冒充已验证；未来新版本合同必须持久化有角色且可注册验证的 rule ref 后才能开放该状态；
- window 首尾缺帧或无法从 source PTS 估计边界覆盖时 visual artifact 必须 partial/limited，不能用“已读到的帧数”把截断窗口标为 complete；
- output 只写 local Signal/Event artifact 和 safe summary，并为后续 family analyzer 提供生成完整 ProcessedEventTable 所需的 registered visual fields/quality/association metadata；
- MP4 failure 保留 native/outcome result；
- Coach payload/tool trace 无 frame/image/video bytes/path。

### 实施步骤

1. 以 real-run capability assessment 为入口，冻结 normal/timescale/restart 正例与 pause/coverage 负例的用途；不得跨 family 冒充。
2. 审计旧 `tracking.py`/`vision.py` primitives，先在 synthetic fixture 锁定数学行为，再在真实 static/dynamic frames 测 false positive、center/radius、identity 和 HUD 混入。
3. 建立稀疏、可审核 annotation ledger；外部 field evidence 不直接进入 CI，可分发小 fixture 必须记录来源 hash、协议与许可边界。
4. 把 provisional seven-field runtime domain 改为最小 runtime selector + profile calibration metadata，并按 metric family 执行 compatibility。
5. 实现 frame -> canonical time、fixed viewport aim point、multi-target tracker、radius/confidence、low-confidence/occlusion events 和 deterministic smoothing；原始 frame 不进入 derived contract。
6. worker 在 multimodal 路径写 visual Signal/Event artifact；visual failure 保留 native/outcome result。只有 reviewed producer/profile 可以注册 production。

### Verify

```powershell
$env:KOVAAK_INSTALL_DIR='Z:\__aiming_cookie_test_missing__'
python -m pytest tests/test_analysis_evidence.py tests/test_visual_signals.py webapp/tests/test_worker.py -q
```

### Stop rule

- 需要修改或重新实现 MP4 capture；
- 需要为已有 normal/timescale/restart 四件套重新采集同类证据，或要求 receipt 上报 detector 才能观察的 theme/target/background 分类；
- detector 只能靠单一真实视频调参且无可复现 fixture；
- 无法量化 target/crosshair confidence；
- 需要把视频、frame 或外部媒体 URL 交给 Coach/Provider。

## Task 6A - Validated outcome association repair

> **实施状态：implemented + automated + reviewed candidate replay passed / production pending（2026-07-23）。** 已完成版本化关联合同、验证和消费，不修改 capture/native receipt；production rule registry 与 launch manifest 继续为空。timescale field audit 中 `55` 个 one-shot kill 均有唯一 `0..50 ms` Raw click 候选，延迟为 `0..8 ms`；`44` 个唯一准星中心重叠候选经独立复核为 `41 stable / 3 not_stable`，现有 `1 px` inner-hitbox Gate 严格形成 `36` 条 `validated_aligned` association，其余样本保持 unavailable。detector v2 已恢复 `029/043` 的唯一中心目标 observation，但两条人工 truth 仍是 `not_stable`，因此不会自动升级为可用 association；`033` 被显式标为 merged ambiguity，不再伪装成稳定目标。opt-in association replay 为 `11 passed`，完整 MP4 opt-in replay 为 `1 passed`；完整 Python 为 `1163 passed, 5 skipped`。实际 `video preprocessing -> track -> association -> dynamic analyzer` 的 candidate-mode 联合回放、同条件 dynamic baseline、identity continuity 与 occlusion re-entry 仍是 production 激活前 Gate。

### 目的

新增可审计的 `event_bundle.v2`，把 accepted hitscan rule 下的 Stats one-shot kill、Raw click 与 reviewed stable visual track 关联为 `validated_aligned`，并确保 kill 不再被 Target Switching 错称为 `first_damage`。

### Allowed files

- `docs/superpowers/specs/2026-07-20-complete-coach-analysis-context-design.md` only for this versioned contract
- `docs/superpowers/plans/2026-07-20-complete-coach-analysis-context-v1.md`
- `docs/PROGRESS.md` only for four-state implementation/Gate status
- create `knowledge/scenarios/outcome-association-rules.v1.json`
- create `kovaak_tracker/outcome_association.py`
- `kovaak_tracker/analysis_evidence.py`
- `kovaak_tracker/visual_signals.py` only for version-dispatch / artifact merge if required
- `kovaak_tracker/dynamic_clicking_analysis.py` only for validated outcome consumption
- `kovaak_tracker/target_switching_analysis.py`
- `webapp/backend/worker.py`
- `webapp/backend/evidence_store.py`
- `webapp/backend/contracts.py`
- `webapp/backend/coach_commands.py`
- create `tests/test_outcome_association.py`
- `tests/test_analysis_evidence.py`
- `tests/test_visual_signals.py`
- `tests/test_dynamic_clicking_analysis.py`
- `tests/test_target_switching_analysis.py`
- `webapp/tests/test_worker.py`
- `webapp/tests/test_evidence_store.py`
- `webapp/tests/test_contracts.py`
- `webapp/tests/test_coach_commands.py`
- small redistributable PNG/JSON fixtures under `tests/fixtures/visual_signals/`; no private MP4/Raw/Stats/Performance

### Frozen decisions

- `event_bundle.v1` historical behavior is unchanged and continues rejecting `validated_aligned`;
- `analysis_evidence_artifact.v1` remains read-only and accepts only `event_bundle.v1`;
- new association evidence uses `analysis_evidence_artifact.v2`, which may contain unchanged visual `event_bundle.v1` plus outcome association `event_bundle.v2`; store、AnalysisResult contract 与 Coach evidence broker dual-read v1/v2 without migration;
- `event_bundle.v2` persists the exact immutable rule binding used by the calculation plus a typed `outcome_association_validation.v1` per validated association; opaque rule refs in v1 do not count;
- first accepted rule shape is hitscan `one_shot_kill + unique_fixed_aim_overlap + stable_identity` only;
- Stats `Shots/Hits` remain kill-row aggregates and are usable only as the rule's one-shot/one-hit prerequisite, not as synthetic per-shot events;
- target disappearance is corroborating/quality evidence only and never selects the target or proves a hit;
- validated kill may support previous/next target identity and transition timing, but never `first_damage`;
- no accepted rule/profile means no validated association and no production family activation.

### Tests first

- v1 historical bundles still round-trip and reject every `validated_aligned` variant;
- producer rejects unknown/retired registry rule; persisted v2 evidence rejects binding digest mismatch, wrong scenario/profile/window/source refs, projectile/unknown temporal model, multiple or zero temporal/geometric candidates, non-one-shot kill, unstable identity, excess sample gap, sample confidence below the first-rule fixed `1.0` threshold, center outside radius and unavailable visual quality;
- a valid synthetic one-shot hitscan fixture produces exactly one Raw shot event、Stats kill event、stable target ref and `validated_aligned + available` association with complete validation record;
- target disappearance without Stats kill, Raw click or unique center overlap remains unavailable;
- Target Switching consumes validated kill for chain identity/transition but leaves first-damage fields unavailable;
- v1 artifact/bundle round-trip remains byte-for-byte semantic compatible; v2 artifact write/read/retry preserves the exact rule binding and validation record, while new production calculations fail closed when the active registry rule changes or becomes unavailable;
- no raw trace, media frame/path or private parser payload enters Coach/public result.

### 实施步骤

1. 先实现 rule registry、`event_bundle.v2` 与 `analysis_evidence_artifact.v2` validator/dispatcher；v1 validators 与 tests 不变。
2. 实现纯 association producer：从 frozen Stats kill rows、Raw left-button rising edges 与 reviewed visual tracks 生成 complete 或 unavailable records。
3. 在 Worker visual completion 后接 producer；只有 frozen ScenarioProfile family gate、rule digest 与 visual profile 全匹配才运行。
4. Dynamic/Switching 只消费通过 validator 的同 outcome-kind association；修正 kill/first-damage 语义。
5. 用现有 normal/dynamic field evidence建立本地 annotation ledger；没有独立 review/identity truth 时不写 production rule、producer 或 manifest entry。

### Verify

```powershell
$env:KOVAAK_INSTALL_DIR='Z:\__aiming_cookie_test_missing__'
python -m pytest tests/test_outcome_association.py tests/test_analysis_evidence.py tests/test_visual_signals.py tests/test_dynamic_clicking_analysis.py tests/test_target_switching_analysis.py webapp/tests/test_worker.py -q
python -m pytest -q
git diff --check
```

### Stop rule

- 需要猜测 temporal threshold、quality threshold 或 weapon model，而不是从 accepted rule/annotation 读取；
- 需要用 target disappearance、nearest target、aggregate accuracy 或场景名称解歧；
- 需要把 projectile/unknown weapon 当 hitscan；
- 需要修改 capture/native receipt、重新采集已有 static/dynamic evidence，或提交私人原始数据；
- 无法保持 v1 historical artifact 可读，或必须迁移/放宽 v1 才能验证 v2 artifact。

## Task 7 - Dynamic clicking analyzer

> **实施状态：implemented + automated + opt-in real replay passed / production pending（2026-07-23）。** analyzer、advice、Worker、History 与 synthetic fixtures 已存在；exact timescale replay 已验证 `5,142` 帧、`36` 个 bounded visual events、`127` 个完整 click rows。真实 visual artifact 在可唯一绑定目标的 click 上可提供 normalized click error、acquisition/relative-motion 等描述；没有 directly observed/validated hit-or-miss outcome 时，`target_state_accuracy` 与 Coach candidate advice 继续保持 unavailable/empty，Stats kill 不升级为 hit。跨帧稳定 identity、occlusion re-entry 和 predictive-lead 仍未通过真实 Gate；真实 replay 不修改 production registry/manifest。正式激活仍需要第二条同 exact scenario/profile/condition 的完整 Run、动态可比基线和 production producer/profile registration。

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
2. 为每次 acquisition/flick/click 生成完整 ProcessedEventTable，再生成 MetricRecords 和典型/失败/对照 EvidenceSegments；代表片段不得替代完整 rows。
3. 添加只依赖正式 metrics/processed rows 的 deterministic candidate advice rules，并保留 supporting/counterexample row refs。
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

> **实施状态：implemented + automated + one exact real product Gate passed（2026-07-24）。** analyzer、advice、Worker、History 与 synthetic fixtures 已存在；scenario hash `b2ae4a24b710e36afc6e57c61f590ab4` 的单目标 `1920x1080` 路径已通过真实 `analysis.create_from_run -> Worker -> evidence -> profile` 回放并进入 production manifest。其它 Tracking 场景继续 fail closed，不能从这一条 exact Gate 推断 family-wide production readiness。

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
2. 实现 tracking episodes/change-points/loss/reacquisition events，并为 episode 与固定分析窗口声明不同 row kind/field catalog。
3. 生成覆盖全部分析单位的 ProcessedEventTable、MetricRecords 与 best/typical/failure/recovery EvidenceSegments；重叠 row kind 不得被当成独立样本聚合。
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

> **实施状态：implemented + automated / real and production Gate pending（2026-07-22）。** analyzer、advice、Worker、History 与 direct-outcome synthetic fixtures 已存在；Task 6A 前 `validated_aligned` 不可用，且尚无同 family 完整真实 Run/identity-outcome 标注，未生产启用。

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
- `previous outcome` 和 outcome-kind 一致的 target-conditioned metrics 可消费 directly observed/validated OutcomeAssociation；validated one-shot kill 只支持 previous/next target identity、transition 与 first-shot anchor，`first_damage` 只消费 source 直接提供的 first-damage evidence 或未来独立 first-damage rule；`.perf` aggregate 或 nearest-target association 不足时 unavailable；
- target identity 丢失时退化为 `unclassified_discrete_acquisition`，不生成 selection error；
- carry-over overshoot 与普通 terminal correction 分开；
- issue/segment/plan refs 完整且不复用 static absolute thresholds。
- switching manifest entry 只有在 analyzer、knowledge、fixture 与 visual-quality prerequisites 全部通过后从 `pending_gate` 改为 `active`；

### 实施步骤

1. 先冻结 switching event state machine 和 ambiguous transitions。
2. 实现 distance/direction/target-state conditioned metrics。
3. 为每条可观测 switching chain 生成完整 ProcessedEventTable，再生成 transition、selection、acquisition、settle 的独立 EvidenceSegments。
4. 添加确定性 candidate advice、supporting/counterexample row refs 与 verification targets。
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

## Task 10R - Cross-family Coach knowledge research prerequisite (completed 2026-07-22)

本节是已完成的 research prerequisite，不是可重复执行的 implementation Task。证据审计、社区教练术语、运动控制边界、跨 family coverage matrix 与 v2 建议见 [`../assessments/2026-07-22-task10-cross-family-coach-knowledge-research.md`](../assessments/2026-07-22-task10-cross-family-coach-knowledge-research.md)。

研究结论只证明已有足够材料编写保守的首发最小知识合同，不证明 Task 6 visual producer、Task 10 Registry v2、Task 7-9 analyzer 或完整 Coach 已完成。社区材料提供 taxonomy、观察词和 cue；学术材料约束机制、测量与复测；产品合同决定何时可观测、何时 fail closed。三类来源不得互相冒充。

## Task 10 - Minimum cross-family Knowledge Registry and diagnosis contracts

> **实施状态：implemented + automated / formal upstream Gate pending（2026-07-22）。** Registry v2、migration audit、Python/TypeScript retrieval 与新 turn 默认 v2 已存在并通过自动化；Task 6/6A 的 reviewed visual/outcome Gate 未闭合，因此不能称跨-family production-ready，也不得重复实现 v2。

### 目的

在 Task 6 完成后、Task 7-9 前，依据已完成的 Task 10R assessment 补齐 dynamic clicking、tracking 和 switching 的 definition、scope、limitations、处方、feedback 与 transfer/retest 最小知识覆盖，并冻结各 family candidate diagnosis 输出引用合同。知识只解释后续 analyzer 的正式 facts，不自己触发 diagnosis；真人教练术语必须映射到可观测 pattern 与明确反证边界。

### Allowed files

- create `knowledge/coach/schema.v2.json`
- create `knowledge/coach/registry.v2.json`
- create `knowledge/coach/migration-audit.v2.json`
- create `tests/fixtures/coach/knowledge/family-contracts.v2.json`
- v1 knowledge assets are read-only historical fixtures; do not edit their shape or contents
- `kovaak_tracker/coach/knowledge_registry.py`
- `kovaak_tracker/coach/agent_kb.py`
- `kovaak_tracker/coach/knowledge.py`
- `kovaak_tracker/coach/agent_tools.py`
- `kovaak_tracker/coach/diagnosis.py`
- `webapp/backend/coach_runtime.py`
- `webapp/coach-runtime/src/contracts.ts`
- `webapp/coach-runtime/src/knowledge-registry.ts`
- `webapp/coach-runtime/src/knowledge-tools.ts`
- `tests/coach/test_agent.py`
- `tests/coach/test_knowledge_registry.py`
- `tests/coach/test_diagnosis.py`
- `webapp/tests/test_coach_runtime.py`
- `webapp/coach-runtime/test/knowledge-registry.test.ts`
- `webapp/coach-runtime/test/knowledge-tools.test.ts`
- `webapp/coach-runtime/test/knowledge-parity.test.ts`
- `webapp/coach-runtime/test/knowledge-analysis-e2e.test.ts`

### Tests first

- Task 6 shared visual producer 已完成真实 normal/timescale field Gate，visual observation context 可由既有 Run facts + producer observation 重放，不依赖新增 capture receipt；OutcomeAssociation 明确拒绝 `inferred + available` 等不一致组合，EventBundle v1 对没有可解析 rule registry/binding 的 `validated_aligned` fail closed；正式 outcome claim 当前只能由 `directly_observed` 支撑，未来新合同只有在验证规则可注册、可重放时才可开放 `validated_aligned`；
- v1 schema/registry/trace 原样可解析；v2 是新 turn 的唯一 active retrieval，历史解析按 `registry_version + entry_ref` 精确选择版本，同 entry ref 跨 Registry 不串读；
- spec 已冻结的每个新增 canonical signal/metric 至少有 active entry 和 limitations，后续 analyzer 可以从第一条正式 issue 起引用；
- static、dynamic、predictable/reactive/control tracking 与 switching 全部通过 spec 9.1 的 knowledge coverage matrix；每项含 definition/scope/quality prerequisite/expected direction/limitations/counterevidence/cue/dose guardrail/matched retest/near-transfer retest，且 direction 只能使用冻结 enum；
- 每个 v2 active entry 是可独立消费的完整 observation -> explanation -> cue -> retest record；claim-bearing section 各自有 claim level/source refs，community first-party cue 可以与 academic mechanism 同 entry 存在但不能互相升级；
- community source 保存作者/组织、标题、发布时间（若可得）、访问日期、locator、适用场景和 supports sections；单一 coach/organization source 不得标为 community consensus；
- movement outcome-only 的 cue/dose/retest 固定 `not_applicable`，并验证不能生成 issue、prescription、profile contribution 或 plan item；
- academic/community/product/experimental claim level 不越级；
- predictable/reactive、smoothness/correction、switching/ordinary flick 边界明确；
- `reading`、`speed matching`、`confirmation`、`tension management` 等教练词各自列出可支持的 processed fields/conditions、替代解释和不可观测边界：reading 只能由 target motion/change-conditioned response 间接支持；speed matching 需要 target/crosshair relative motion；confirmation 需要 click/settle/outcome 时序；tension 没有身体传感器或单变量实验时只能是候选假设；
- exact signal/metric/topic retrieval 最多 3 条，零命中不全库 fallback；
- Python/TS/backend trace parity、v1 -> v2 migration audit、historical refs 可解析；tool event 只保存 registry/entry/section/source/claim refs，不保存知识正文；
- knowledge entry 不生成 measured fact、最终 severity 或自动处方；规则 severity 只是 candidate issue 的初始排序。

### 实施步骤

1. 先写失败测试：v1 historical replay、v2 exact retrieval、跨 Registry 防串读、字段级 source/claim ceiling 和完整处方链。
2. 建共享 `family-contracts.v2.json`，冻结 processed field/signal/metric -> observation/coach-term -> knowledge coverage matrix；fixture 只引用 spec 已冻结的 future analyzer contract，不伪装成 analyzer 已实现。
3. 实现独立 v2 schema/loader 和 registry-version resolver；v1 assets 不改，new-turn active retrieval 切到 v2，历史 trace 仍按精确版本验证。
4. 优先迁移 definition、scope、quality、direction、limitation、alternatives 和 forbidden inference，再加入 cue/dose/retest；学术来源锚定 mechanism/validation，社区来源只锚定其 taxonomy/observation/cue section。
5. 完成 v1 -> v2 migration audit、legacy Python adapter、Python/TS/backend trace parity 和 bounded refs-only event。
6. 用 versioned family fixture 做 diagnosis-shape -> retrieval -> Pi refs-only E2E；Task 7-9 再分别补真实 analyzer E2E。

### Verify

```powershell
python -m pytest tests/coach/test_knowledge_registry.py tests/coach/test_diagnosis.py tests/coach/test_agent.py webapp/tests/test_coach_runtime.py -q
$env:PI_SOURCE_DIR = (Resolve-Path third_party\pi).Path
$env:TSX_TSCONFIG_PATH = (Resolve-Path third_party\pi\tsconfig.json).Path
$env:PYTHON_BIN = (Resolve-Path .venv\Scripts\python.exe).Path
$loader = (Resolve-Path third_party\pi\node_modules\tsx\dist\loader.mjs).Path
$loaderUrl = & node -e "const { pathToFileURL } = require('node:url'); process.stdout.write(pathToFileURL(process.argv[1]).href)" $loader
$tests = (Get-ChildItem webapp\coach-runtime\test -Filter 'knowledge*.test.ts' | Sort-Object Name).FullName
node "--import=$loaderUrl" --test @tests
```

### Stop rule

- Task 6 未完成，或 `visual_runtime_selector.v1` / OutcomeAssociation consistency Gate 未闭合；
- 需要修改 capture/native receipt，或无法从既有 frozen Run facts + producer observation 构造可审计 visual context；
- 没有真实 reviewed visual producer、annotation protocol、quality profile 和可分发 fixture 时，不得把空 registry 或 synthetic profile 宣称为生产视觉能力；
- 需要修改任何 v1 asset 才能实现 v2，或 v1 historical trace 无法按原 registry version 验证；
- v2 无法用冻结的 family contract fixture 表达完整 observation/quality/alternatives/cue/retest record；
- 需要用知识 entry 替代 analyzer 或触发 severity；
- 来源无法支持 claim level 或要求保留绝对未校准阈值；
- Python/TS 不能读取同一 canonical Registry；
- Task 10R assessment 之外出现需要新来源才能支持的 claim；executor 不在本 Task 在线搜索、使用 embedding 或让模型自动改 Registry。

## Task 11 - Persistent aiming profile, plan and retest loop

> **实施状态：backend implemented + verified（2026-07-24）。** comparable、deterministic、available 的单项 metric 可从 partial Analysis 幂等进入画像；synthetic、outcome-only、unavailable 或 inferred metric 继续禁止。删除先失效 contribution，再清理 workspace；画像、plan item、显式用户 execution、matched/near-transfer retest、bounded Coach snapshot/context refs 均已接通。核心后端合并回归为 `543 passed`。

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

- profile dimension 只从 comparable deterministic metrics/processed facts 更新；Coach 接受或拒绝某个候选解释的会话状态可以保存 evidence refs，但不得冒充测量维度；
- append-only profile contribution 以 analysis ref 幂等；terminal retry 不重复，删除先 invalidation 再确定性重建，startup reconciliation 修复中断状态；
- 单次低 confidence Run 不覆盖多次高 confidence trend；
- conflicting evidence、counterexample refs 和 candidate hypothesis revision 保留，不被平均成单一确定因果；
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
5. store ready 后注册 `profile.aiming.snapshot`，default Coach context 返回 bounded profile/retest refs；LLM 候选因果只作为可修订 hypothesis state 保存，不进入正式 metric、severity 或 plan target，除非后续确定性复测合同支持。

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

> **实施状态：in progress / Tracking field blockers remediated, complete release Gate blocked（2026-07-27）。** 真实 `Run 479` 已通过 source -> analyzer -> evidence -> Coach query -> profile 与 Desktop managed playback 链；后续 remediation 已闭合 video-fallback、Analysis Data、Tasks 失败域/时间一致性与产品节奏 capture lifecycle，并保持 Tracking artifact 字节 parity。当前完整 Gate 为 Python `1301 passed, 5 skipped`、Coach `75 passed`、Browser Playwright `47 passed, 3 skipped`、MSVC Rust `73 passed, 7 ignored`。本 Task 本身不创建或修改前端文件。Static、Dynamic、Switching 尚未各有一个 production-active exact scenario，高 polling-rate、AMD/Intel、真实 Provider/OAuth、真实 worker restart 与发布工程也未闭合，因此 Task 12 不得标 completed。

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
- 每个 launch family 的 ProcessedEventTable 覆盖全部分析单位并声明 field catalog/row count/completeness；static 明确验证全部 flick，不只代表 EvidenceSegments；
- source -> canonical window -> analyzer -> evidence -> Coach query -> knowledge -> plan/profile refs 全链；
- small Run 的完整 allow-listed CanonicalRunFacts 进入默认 Coach context；oversized facts 显式分节，exact timeline/events 可用 owner-bound cursor 无损分页；
- source field Registry golden 覆盖所有首发已知 Stats/Performance fields；known field 的 present/source-absent/omitted 与 unknown-field observability 可审计；
- Coach 能消费 scenario/config/outcome totals、whole-run/segment timeline 和完整 processed event tables；能用 rank/filter/aggregate/co-occurrence/sequence 找到支持证据与至少一个反例，并可降低/拒绝规则 candidate issue，同时原始 CSV/protobuf/private parser/unknown fields sentinel 不进入 payload/tool trace/message；
- event/segment compare golden 证明局部真实值不复用 whole-run MetricRecord；
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

### 2026-07-27 field checkpoint

| Gate | 状态 | 证据 / 阻断 |
|---|---|---|
| Tracking source / capture | passed on current NVIDIA host | `Run 479`：60 秒，Stats / Performance / Raw / MP4 available，Raw `51,443` points / `0` drop，MP4 `3,632` packets / `0` encoder/drop error |
| input-native | passed with limitation | `Analysis 1` 诚实保持 Preview / outcome-only，未伪造 target-relative 机制结论 |
| multimodal Tracking | passed with latency target miss | `Analysis 3` 产品链通过；production Python 3.11 / OpenCV 5.0 三轮为 `147.242s / 151.134s / 148.039s`，中位数 `148.039s`，artifact 均与 reference 字节一致，quality `accepted`、target/crosshair coverage `99.944% / 100%`；计划 `<=130s` 目标未达到 |
| Coach-safe context / profile / no Provider | passed | `coach_diagnostic_context.v3`、`analysis.evidence.list` 返回 3 segments、`profile_contribution.v1` 为 5 dimensions；Provider 未配置时只显示激活入口 |
| privacy / owner / idempotency | passed for exercised path | 公开 API、context、tool result 与 UI 无 path / Raw trace / CSV / protobuf / private payload / secret / token / traceback；跨 owner 拒绝，同 key 重放返回同一 Analysis |
| deletion / retry / restart recovery | passed | 删除探针保留 Run-owned MP4 与 deleted Coach ref；`Analysis 2 -> 3` attempt 历史在重启后找回。stale lease 使用既有 `kinematics` domain 与真实写入时间，v19 仅修复精确命中的未来异常行 |
| video-fallback Desktop playback | passed | EvidenceSegment 与 managed MP4 availability 已解耦；无 segment 时正式 `VideoView` 仍播放 Run-owned MP4，媒体缺失局部降级为 path-free HTTP 410，artifact 损坏继续 fail-closed |
| Analysis Data UX | passed for Tracking evidence | `frontend_analysis_data.v1` 提供 bounded events/distribution 与 120 点目标相对误差；英文限制只在技术详情，正式 UI 使用用户语义并折叠 unavailable 指标，1280/960 无横向滚动 |
| Desktop lifecycle log | passed at product cadence / stress residual recorded | 真实 Tauri 40 次 250ms status 全 available，正常关闭四类错误为 0 且进程/端口清理；50ms 人工压力曾有 `1/120` transient unavailable，无 traceback 或 export 误分类 |
| Static / Dynamic / Switching / cross-hardware | blocked | 缺 production-active exact scenario 实证、高 polling-rate 与 AMD/Intel 物理 Gate，不得用 Tracking 单行替代 |

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
