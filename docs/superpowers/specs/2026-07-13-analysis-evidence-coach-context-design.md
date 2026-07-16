# Analysis Evidence and Coach Context — Design Contract

> 状态：active
> 目的：冻结 KovaaK Run、Raw Input、Performance、Stats 与 MP4 如何进入 AnalysisResult 和 Coach。
> 上游：[`../../PRD.md`](../../PRD.md)、[`../../ARCHITECTURE.md`](../../ARCHITECTURE.md)、[`../assessments/2026-07-13-reflek-capability-adoption.md`](../assessments/2026-07-13-reflek-capability-adoption.md)

## 1. 分析模式

| input_mode | Stats | Performance | Raw trace | MP4 | 结果边界 |
|---|---|---|---|---|---|
| `input_native` | required for scenario/events | required for v1 trace pairing | required | absent/optional | 输入运动学、事件对齐、非视觉诊断；缺任一 required source 则 native unavailable |
| `multimodal` | required | required for native pairing | required | required | native result 是运动学主事实；MP4 提供直观回放、问题定位和可验证视觉证据；视觉失败保留 native |
| `video_fallback` | required | optional | not used | required | compatibility fallback：沿用现有 CV 运动学和视频证据；不得生成 Raw Input provenance，不作为长期主分析方向 |

`Stats` 提供 scenario/config/kill facts；`Performance` 在 v1 提供 Raw Input pairing anchor 和 performance event facts；Raw trace 没有有效 Performance anchor 时不能被标为 paired。

规则：

- multimodal 不得让 MP4 静默覆盖 Raw Input measurement；
- visual alignment 失败时保留有效 native result，并标 warning；
- MP4 的存在不得成为 input-native 诊断成功的前置条件；视频处理失败只影响回放/视觉证据 availability；
- input-native 不得生成 target-relative visual measurement；
- video-fallback 不得伪造 Raw Input provenance。

## 2. AnalysisRequest v1

```text
schema_version = analysis_request.v1
analysis_type
input_mode
owner/local_profile
kovaak_run_id?
source_snapshot
  stats
  performance
  raw_input_trace
  video
calibration
  cm_per_360
  fov
requested_at
```

提交时冻结 source snapshot；重试不能因为用户源文件随后修改而静默改变分析含义。
若已冻结 source 在 worker 消费前缺失、不可读或 fingerprint 不一致，Analysis 必须以稳定的
`input_validation / source_unavailable` 失败，标记为不可对同一 snapshot 重试，并提示用户重新
提交以建立新 snapshot；错误对象和普通日志不得包含源文件绝对路径或底层异常文本。

当 path-based Analysis request 携带 `video` 时，创建命令必须先冻结用户源 MP4 的
SHA-256 / size / mtime revision，再复制到 Analysis managed workspace，并在进入 queued
状态前验证 managed 副本与该 revision 完全一致。复制期间源文件缺失、不可读、被替换、
内容或 mtime 改变、partial copy 或目标 checksum 不一致时均 fail-closed；后续视觉处理只
消费这份已验证的 managed 副本。`source_snapshot.video` 与 artifact manifest 保留
fingerprint/checksum，但任何公开投影、幂等审计和错误文案都不得包含用户源绝对路径。

### Calibration provenance

```text
calibration
  cm_per_360
    value?
    unit = cm_per_360
    provenance = user_input | stats_config | profile_default | unknown
    availability = available | missing | conflicting | invalid
  fov
    value?
    unit = degrees
    provenance = user_input | stats_config | profile_default | unknown
    availability = available | missing | conflicting | invalid
```

规则：

- raw count path length 可以在没有 calibration 时输出，但单位必须是 `raw_counts`，不能写成 pixels 或 physical distance；
- angular metrics 必须有有效 FOV/calibration；
- throughput 必须同时有可靠 target-size source 和有效 calibration；
- 冲突或缺失时输出 `unavailable` + limitation code，不静默采用默认值。

API/UI 使用 stable source/artifact reference，绝对路径只允许存在于受控本地存储层。

## 3. AnalysisResult v2

```text
schema_version = analysis_result.v2
analysis_version
analysis_id
analysis_type
input_mode
owner/local_profile
kovaak_run_ref?
created_at / completed_at
status
input_snapshot
evidence
  sources[]
  alignment
  coverage
  warnings[]
deterministic
  summary
  metrics[]
  diagnosis
  timeline[]
  figures
narration
artifact_manifest
warnings[]
errors[]
normalization_issues[]
```

Terminal persistence 不能只校验 envelope 自洽；还必须把结果绑定到当前已 claim 的 Analysis request：

- `analysis_id = analysis:{session_id}`，且 `artifact_manifest.analysis_id` 与唯一 `analysis_result` owned output 使用同一引用；
- owner/local profile、`analysis_type`、`input_mode` 必须与 session request 一致；
- session 引用 Run 时，`kovaak_run_ref = run:{kovaak_run_id}`；未引用 Run 时结果不得附带其他 Run；
- 任一绑定不一致都必须在写入 `done` / result 前 fail-closed，不能把另一 request 的结构合法结果挂到当前 Analysis。

### Metric provenance

每个关键 metric 至少包含：

```text
key
value
unit
availability
provenance.kind       measured | derived | fused | inferred
provenance.sources[]
metric_version
sample_count?
coverage?
limitations[]
```

`inferred` 默认不能驱动正式 severity、Coach 处方或 History 比较。SPARC 必须同时归一化幅度谱和选中频段的频率轴；直接使用 Hz 间隔会把采样时间尺度写入结果。公式修正分别使用 `native_flicking.sparc.v2` 与 `flicking_fair_summary.sparc.v2`，旧版与修正版不得进入同一 History trend/baseline；v2 在真实产品数据校准前不得套用 legacy `-5.0` 或草稿 `-0.5` 绝对阈值。

## 4. Evidence source

```text
source
  raw_input | performance | stats | mp4 | fused
availability
  available | missing | unsupported | unavailable | invalid
role
  kinematics | event_anchor | scenario_config | visual_evidence | cross_validation
artifact_ref?
parser_or_format_version?
alignment
  not_required | aligned | partial | failed | unavailable
warnings[]
```

## 5. TimeAlignment and Timeline

```text
timebase_version = time_alignment.v1
raw_clock_source = system_wall_clock_epoch_ms
anchor_source = performance.challenge_start_utc
offset_ms
guard_before_ms = 0
guard_after_ms = 0
ordered_sequence_key = record_order
coverage_ratio
status = aligned | partial | failed | unavailable
warnings[]
```

- raw epoch → challenge-relative 的公式是 `relative_ms = raw_epoch_ms - challenge_start_utc`；
- canonical timeline 以 challenge-relative milliseconds 为基础；
- `frame` 只有 MP4 存在时才可选；
- event 标明 source；
- input-native timeline 不依赖 fps；
- multimodal 可增加 video anchor，但原 event time 不被改写；
- `partial` / `failed` 的可用指标范围必须由 evidence/metric limitation 明确表达，不能由调用端自行猜测。

### Evidence reference

```text
evidence_reference
  id
  source
  artifact_id?
  challenge_time_range_ms?
  alignment_status
  availability
  local_only
  metric_keys[]?
```

Coach 和 UI 只能引用这种稳定 reference，不引用绝对路径或 raw samples。

## 6. Deterministic / experimental 边界

### 可进入正式结果

- Stats/Performance source facts；
- total kills/shots/hits/accuracy；
- event timeline、有效 inter-kill gaps；
- trace point count、duration、coverage、button transitions；
- 经版本化验证的 cumulative trajectory、path length、straightness、velocity、acceleration、movement timing；
- PRD 已定义且验证通过的 SPARC、linearity、reverse/submovement、deceleration metrics；当前 `submovement_overlap` 仅是版本化 trough-depth ratio proxy，必须携带非 temporal-overlap limitation，不能表述为已完成时间重叠分解；
- 目标尺寸来源可靠时的 throughput。

### 不可作为事实进入正式结果

- RefleK targetInference；
- 以 kill endpoint 代替真实 target center 的过冲/欠冲距离；
- raw counts 命名为 pixels；
- 未校准 confidence；
- 未验证的 sensitivity 数值建议；
- 因规则未命中而默认标记 `optimal`。

实验性结果如保留，必须：

- `classification = experimental` 或独立 experiment envelope；
- 不进入 Coach 默认上下文；
- 不参与正式趋势和比较；
- UI 明确说明是推测而非测量。

## 7. Artifact Manifest v2

```text
schema_version = artifact_manifest.v2
analysis_id
external_inputs[]
owned_outputs[]
```

每个 artifact：

```text
id
kind
source
availability
ownership = analysis | kovaak_run | user_source
managed
local_only
status
format/parser version
checksum?
derived_from[]
```

规则：

- Raw Input trace 只能出现在 `external_inputs[]`，`ownership = kovaak_run`；
- Analysis 不能删除 Run-owned trace；
- Analysis-owned outputs 进入 `owned_outputs[]`，terminal Analysis 删除时按 Analysis lifecycle 处理；
- user source 由用户拥有，Aiming Cookie 不删除；
- Raw Input trace 必须 `local_only = true`，不提供 public URL 和绝对 path。

terminal Analysis 的 SQLite/workspace 原子性与恢复顺序由 active
[`2026-07-16-analysis-deletion-reconciliation-design.md`](2026-07-16-analysis-deletion-reconciliation-design.md)
冻结；本 spec 不重复定义 tombstone 或 reconciliation 状态机。

## 8. Coach Diagnostic Context v1

Coach 只通过 allow-list projection 获取：

```text
schema_version = coach_diagnostic_context.v1
analysis_ref
  analysis_id
  analysis_result_version
  analysis_type
  input_mode
diagnosis
  profile
  issues
  summary
  comparison
  meta
evidence_summary
warnings[]
```

允许：

- 用户已看见的 deterministic diagnosis；
- metric summary + unit + provenance；
- input mode；
- evidence availability、coverage、limitations；
- 同类可比 History trend；
- stable evidence/time-range references。

禁止：

- raw trace、dx/dy samples、timestamp samples；
- mouse trace path、Stats/Performance absolute path；
- 完整原始 CSV/performance payload；
- targetInference；
- 未验证的 heuristic；
- 任意 filesystem/shell/network access。

实现必须是字段 allow-list，不是先传完整结果再删除敏感字段。

### Coach error / limitation shape

```text
warning_or_error
  code
  domain
  retryable
  user_message_key
  evidence_ref?
```

### Comparability predicate

只有同时满足下列字段，History 才能计算趋势或 baseline：

```text
analysis_type
scenario_identity_version
input_mode
metric_key
metric_version
unit
calibration_compatibility
minimum_evidence_quality
classification = deterministic
```

否则只能展示“不可直接比较”及原因，不显示伪造的 PB、趋势或差异百分比。

## 9. Coach 行为

- Coach 可以解释确定性结果、比较可比记录、定位 evidence；
- Coach 不能改写 deterministic result；
- 没有 MP4 时，Coach 必须知道视觉证据不可用；
- alignment partial/failed 时，Coach 必须降低结论范围；
- Raw trace 若未来需要进入上下文，必须新建用户确认和 evidence contract；本 spec 不授权。

## 10. 兼容性

- 新写结果使用 `analysis_result.v2`；
- `analysis_result.v1` 和 legacy 继续可读；
- unknown version fail closed；
- v1 读取不能虚构 input mode/provenance，只能标 legacy/unknown；
- video-fallback 现有路径必须保留回归测试。
- 保留 video-fallback 是兼容性要求，不代表新功能继续以 MP4 作为主要运动学事实源。

## 11. 必要测试

- 三种 input mode 的 request/result matrix；
- v1/legacy read compatibility；
- missing/partial/alignment-failed evidence；
- input-native result 不要求 video/frame；
- multimodal visual failure 保留 native summary；
- video-fallback 不产生 raw provenance；
- Coach sentinel：raw trace 特征值不出现在 Pi payload、Python Agent payload、tool response、stored message 或 API response；
- no absolute path in result/Coach context；
- inferred/experimental metric 不参与正式 diagnosis/trend。
