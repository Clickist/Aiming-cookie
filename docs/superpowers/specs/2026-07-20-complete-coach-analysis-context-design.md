# Complete Coach Analysis and Evidence Context - Design Contract

> 状态：active。本文冻结完整 Coach 的局部设计合同；实施仍只由 active plan 中被点点明确授权的单个 Task 推进。
> 目的：冻结数据采集完成后，static/dynamic clicking、tracking 与 target switching 如何被预处理、分析、解释、查询、持久化并进入训练闭环。
> 上游：[`../../PRD.md`](../../PRD.md)、[`../../ARCHITECTURE.md`](../../ARCHITECTURE.md)。2026-07-20 已将点点确认的完整 Coach 上线目标、L0-L3 边界和当前/未来视频能力边界同步到上游事实源。
> 相关合同：[`2026-07-13-analysis-evidence-coach-context-design.md`](2026-07-13-analysis-evidence-coach-context-design.md)、[`2026-07-14-versioned-coach-knowledge-registry-design.md`](2026-07-14-versioned-coach-knowledge-registry-design.md)、[`2026-07-17-automatic-run-capture-design.md`](2026-07-17-automatic-run-capture-design.md)。

## 1. 目标、假设与非目标

### 1.1 上线目标

产品应把一次或多次 KovaaK Run 转成可追溯的教练闭环：

```text
Run-owned sources
  -> canonical time/scenario
  -> private signals/events
  -> family-specific analyzers
  -> metrics/evidence segments
  -> deterministic diagnosis
  -> bounded Coach context/tools
  -> explanation/prescription
  -> profile/plan/retest
```

首个完整 Coach 目标覆盖：

- static clicking；
- dynamic clicking；
- continuous tracking，包括 predictable、reactive 和 control/smoothness 条件；
- target switching；
- movement aiming 在缺少玩家移动遥测时只提供 outcome-only 观察，不生成移动机制诊断。

### 1.2 事实边界

- Raw Input 是鼠标输入运动学事实，不是目标相对误差事实。
- Stats / Performance 是场景条件、结果与时间锚；不是逐帧目标轨迹。
- MP4 可由本地确定性预处理器转换成目标、准星、误差、事件和 confidence；CV 结果仍是带质量边界的视觉测量，不是无条件真值。
- Coach 的数据边界由“模型能否可靠消费”和“本轮预算是否允许”决定，不由“数据是否来自原始 source”一刀切。原始文件载体与私有 parser object 不提供；版本化、类型化、字段白名单化的完整 Stats/Performance Run facts 可以提供。
- 用户启用 Coach 并选择 Provider 后，L1-L3 的 bounded context/tool results 可以作为普通 Coach turn 数据发送给该 Provider，不需要逐 Run 二次确认或额外隐私 Gate。L0 当前不发送是体量、成本和模型消费合同的工程边界，不是把瞄准数据定义为高敏感数据。
- Coach 负责解释、综合、提出可验证假设与训练方案；不得重新计算指标或覆盖确定性结果。
- 单次 Run 可以发现问题，但身体、视觉、认知或控制机制只能表述为 plausible mechanism，不能被伪装成已测因果。

### 1.3 非目标

- 不修改 Raw、MP4、Stats 或 Performance 的采集实现与 Run-owned 生命周期。
- 不把旧 tracking 模块直接标记为生产能力。
- 不从 scenario 名称猜 aim family 后产生正式诊断。
- 不给 Coach 任意 SQL、Python、filesystem、artifact path、原始 CSV、Raw trace 或任意时间范围读取能力。
- 不用一次分析自动生成灵敏度、硬件、身体或医疗结论。
- 不在本文冻结正式前端布局；EvidenceSegment 的本地视频播放由正式前端计划消费。

## 2. 当前能力与实现差距

| 层 | 当前可用事实 | 当前缺口 |
|---|---|---|
| Raw Input | 毫秒时间戳、`dx/dy`、button state、record order；可派生输入速度、加速度、路径、反转、submovement 与 SPARC | 高 polling 数据可能共享毫秒时间戳；不能单独得到目标、误差、lag 或命中原因 |
| Stats | scenario/config、shots/hits/misses/kills 与 run summary；可提供精确 Challenge Start | 精确 Stats 起点尚未作为 canonical window 传给当前 worker analyzer |
| Performance | scenario hash/name、Challenge anchor/profile、稀疏 changed-metric events 与结果交叉验证；官方当前描述约 1 Hz，实际 cadence 按 Run 测量 | 不是连续坐标遥测；不得当成逐靶/逐帧轨迹 |
| MP4 | 本地 60 FPS 窗口录像，可用于回放和视觉预处理 | 正式 worker 没有统一的 target/crosshair signal producer；质量、遮挡和坐标校准合同未冻结 |
| static flicking | 正式 worker 已有 click-anchored input-native producer、分布、outlier、确定性 diagnosis | 仍需迁移到统一时间、场景、MetricRecord 和 EvidenceSegment 合同 |
| tracking | 旧 CV metrics、`advice_tracking.py` 和 Registry 候选知识存在 | 没有正式 Run producer、版本化 signal/metric contract 或生产 Coach 路径 |
| switching | 可复用部分离散鼠标运动指标 | 没有目标身份、事件模型、producer、diagnosis 或知识/处方合同 |
| Coach | allow-list context、Knowledge Registry、owner-scoped product-command bridge、Training Plan store | 默认 context 偏摘要；缺少有界分布、事件、信号窗口和片段比较查询 |

KovaaK 官方资料确认 `.perf` 是完成 Challenge 后与 Stats 一起生成的 performance 文件，提供 scenario hash、start UTC、profile snapshot 和 changed metrics，而不是坐标遥测；未来 schema 允许新增字段，解析必须按版本 fail-closed。参见 [Performance Files](https://wiki.kovaaks.com/en/home/KovaaK%27s/PerformanceFiles)。scenario editor 虽有 aim type 等信息，但不能假设这些字段已经出现在 `.perf`；参见 [Edit Scenario](https://wiki.kovaaks.com/en/home/KovaaK%27s/ScenarioCreation/EditScenario)。

## 3. 选择的架构

### 3.1 高层数据流

```text
Stats + Performance + Raw + optional MP4
                    |
                    v
          CanonicalTimeWindow
          ScenarioProfile
                    |
       +------------+-------------------------+
       |                                      |
       v                                      v
CanonicalRunFacts / OutcomeTimeline   private SignalBundle/EventBundle
       |                                      |
       |                                      v
       |                         family-specific analyzer
       |                                      |
       |                         MetricRecord / EvidenceSegment
       |                                      |
       |                         deterministic diagnosis
       |                                      |
       +-------------------+------------------+
                           v
          default Coach projection + bounded evidence broker
                      v
          Coach explanation and plan
                      |
          profile/history/retest loop
```

### 3.2 决策与取舍

| 方案 | 结论 | 原因 |
|---|---|---|
| 把完整结果压成一段大摘要 | 拒绝 | 成本低，但 Coach 无法核对分布、片段和替代解释，容易退化成传话筒 |
| 让 Coach 自由查询原始载体、无类型 payload 或视频 | 拒绝用于 v1 | 颗粒度高，但 token、解析可靠性、隐私、延迟、注入面与误用风险不可控 |
| 在预算内提供完整规范化 Stats/Performance facts | 采用 | 这些事实体量通常有限且语义明确；保留信息密度，让 Coach 能发现确定性摘要未预设的条件与时序模式 |
| 统一证据合同 + 专项 analyzer + 有界证据 broker | 采用 | 保留足够细节，同时由确定性层控制事实、范围、预算和 provenance |
| 一个通用 analyzer 处理所有 aim family | 拒绝 | clicking、tracking、switching 的事件语义、适用指标和失败模式不同 |

### 3.3 Coach 数据分层

| 层 | 内容 | Coach 权限 |
|---|---|---|
| L0 OriginalCarrier | `.perf` protobuf bytes、原始 CSV bytes/text、Raw trace、MP4、绝对路径、私有 parser object、unknown future fields | v1 不提供 |
| L1 CanonicalSourceFacts | 经过版本化 parser 规范化的 scenario/config、source quality、outcome totals、Stats events、Performance changed-metric events | 默认或按需提供；允许完整规范化投影 |
| L2 DerivedEvidence | MetricRecord、分布、EvidenceSegment、SignalWindow、comparisons | 默认摘要 + 按需查询 |
| L3 Diagnosis/Plan | issue、mechanism boundary、knowledge、prescription、profile、plan、retest | 默认或按任务提供 |

“完整规范化”指当前 schema/version 下所有已知、安全、allow-listed fields 均被保留，并带 completeness/provenance；不表示把原始文件先整体传给 Coach 再删字段，也不表示自动暴露未来新增 unknown fields。

L1-L3 是否进入第三方网络由用户已选择的 Provider/runtime 决定；本合同不增加逐 Analysis consent 状态机。无 Provider 时这些结构仍留在本地，确定性 Analysis/History 正常可用。

## 4. CanonicalTimeWindow

所有 source 和 analyzer 必须消费同一个、不可自行重算的时间窗口：

```text
canonical_time_window.v1
  run_ref
  canonical_zero = challenge_relative_ms
  start_utc_ms?              # only when a trusted epoch mapping exists
  end_utc_ms?                # exclusive, same availability as start_utc_ms
  duration_ms
  anchor_sources[]
    source_ref
    clock_domain = utc_epoch | local_time_of_day | capture_relative | challenge_relative
    observed_value
    observed_end_value?
    precision_ms
    mapping_ref?
    role = primary | corroborating
  selected_anchor_source
  alignment_version
  coverage_by_source
  status = aligned | partial | failed
  warnings[]
```

规则：

- Challenge-relative 时间统一为 `[0, duration_ms)`；所有 interval 使用半开区间。
- Stats 的 `Challenge Start` 是 `HH:MM:SS.mmm` local time-of-day observation，没有日期或时区，不能独立生成 UTC。它的亚秒精度必须保留；只有通过 Performance UTC、capture clock 或其它版本化 mapping 才能参与 epoch anchor refinement。
- Performance `challenge_start_utc` 是 UTC epoch observation；Stats/Performance identity、日期/跨午夜和 offset 冲突必须先通过 mapping/quality Gate，不能把 time-of-day 数值直接减 epoch。
- 没有可信 epoch mapping 时，Stats kill rows 只在 challenge-relative clock 中按 source order 做 24 小时 unwrap；跨午夜可得到正的 relative time，但仍不能补出日期、时区或 UTC。映射到 Performance epoch 必须另有显式、版本化 local-to-UTC mapping。
- Performance event timestamp 必须是 finite、非负的 source-native float32 seconds。`canonical_time_ms = floor(float32_seconds * 1000 + 0.5)`；原 float32 值与 precision 同时保留。量化后只接受 `[0, duration_ms)`，NaN/Infinity、负值、等于 end 或超窗记录进入明确 error/partial policy，不能 clamp 到边界。
- Raw、video、Stats events 和 Performance events 都由同一 window 投影；不允许每个 analyzer 维护自己的 offset。
- source 覆盖不足时保留 partial coverage 与明确 limitation；不得靠删除首尾 event 伪装完整。
- time alignment 版本变化会使 segment、metric 与 History comparability 失效，必须创建新版本结果。

## 5. ScenarioProfile

scenario 名称只用于显示；稳定身份与正式分类以 scenario hash 和审核过的 Registry 为准：

```text
scenario_profile.v1
  scenario_identity
    scenario_hash
    display_name
    registry_version
    classification_ref
  taxonomy_source = reviewed_registry | official_metadata | unknown
  status = active | superseded | retired
  reviewed_at?
  source_refs[]
  supersedes[]
  aim_family
    = static_clicking
    | dynamic_clicking
    | continuous_tracking
    | target_switching
    | movement_aiming
    | unknown
  subdomains[]
    = precision | speed | smooth | reactive | predictable | control | mixed
  target_motion
    model = static | predictable | reactive | mixed | unknown
    target_count_model = single | sequential | concurrent | unknown
  mechanics
    weapon_model?
    hit_rule?
    target_size_source?
  allowed_analyzers[]
  allowed_metric_families[]
  classification_confidence
  limitations[]
```

规则：

- `scenario_hash` 是精确同场景趋势的主身份；名称变体不能覆盖 hash 冲突。
- reviewed Registry 可以由官方/editor metadata 和人工审查构建；名字 heuristic 只能生成待审候选。
- unknown 或 hash 未审核时仍可产出 Stats outcome 和通用输入运动学，但不得运行 family-specific severity/处方。
- `classification_ref` 必须解析到当时的 registry entry/version、审核来源和 supersession chain；Registry 更新不重写历史 Analysis。
- scenario profile 决定“哪些指标有意义”，不能由 Coach 或 analyzer 绕过。
- exact scenario trend 与 cross-scenario skill profile 分开；后者只允许使用经过验证的归一化 metric family。
- 迁移前已经冻结为 `analysis_type=flicking` 的 AnalysisRequest 可以使用版本化 `legacy_static_compatibility`：只保留当前 input-only static metrics/diagnosis，附带 classification limitation，不能生成 target-relative 或其它 family claim。新建 unknown Run 不得自动走该兼容路径；Task 2 必须先覆盖首发明确宣称支持的 hash manifest。用户显式声明“这是 static”最多解锁 `user_declared_static_descriptive` 的输入运动学描述，不能绕过 hash 审核产生正式 severity、处方、画像贡献、计划或跨场景趋势。
- `user_declared_static_descriptive` 固定 `classification_source=user_declaration`、`claim_ceiling=descriptive_only`、`family_analyzer_dispatch=none`；它不是 ScenarioProfile Registry entry，不能被 Coach 或 worker 升级。

首发 `launch_scenario_manifest.v1` 每项至少包含 `scenario_hash`、`scenario_profile_ref`、`fixture_ref`、`review_source_ref`、`reviewed_at`、`family_gate_refs[]` 与 `status = pending_gate | active | retired`，只能通过 hash 连接 Registry。`pending_gate` 不能驱动正式 analyzer 或对外支持声明；static/dynamic/tracking/switching 每个首发 family 至少有一个 `active` entry 通过 analyzer、knowledge、fixture 及必要 visual-quality Gate 后才能发布。否则对应 family 在 UI/Coach 中只能是 `unavailable/outcome_only`。

## 6. 私有 SignalBundle 与 EventBundle

### 6.1 SignalBundle

```text
signal_bundle.v1
  analysis_ref
  canonical_time_window_ref
  visual_quality_profile_ref?
  observed_visual_domain?
  channels[]
    channel_key
    source_refs[]
    coordinate_space
    unit
    sample_rate_semantics
    samples_ref              # local derived artifact, not inline
    coverage
    confidence_summary
    transform_version
    limitations[]
```

首版 channel allow-list：

- `mouse.delta_x/y`、`mouse.position_x/y`、`mouse.speed`、`mouse.acceleration`；
- `crosshair.position_x/y`、`crosshair.velocity_x/y`；
- `target.<track_id>.position_x/y`、velocity、acceleration、visible radius/hitbox；
- `aim_error.x/y/radial`、`aim_error.normalized_radius`；
- `target_relative.crosshair_velocity_projection`；
- `outcome.score_rate`、`outcome.damage_rate`，仅在 source 真正提供时。

完整 samples 只存在于 Analysis-owned、local-only derived artifact。它们不进入 AnalysisResult JSON、默认 Coach context、消息、日志或普通 API；公开边界只暴露 artifact/segment refs 和安全 summary。

视觉 producer 的 `confidence` 不能是未经校准的自由标量。每个 producer/version 必须绑定：

```text
visual_quality_profile.v1
  producer_id / producer_version
  annotation_set_ref / annotation_protocol_version
  coordinate_space
  validated_domains[]
    resolution / ui_scale / theme
    map_or_background_class
    target_appearance_class
    capture_transform_version
    fov_range?
  compatibility_predicate_version
  acceptance_thresholds
    center_error_median / center_error_p95
    radius_or_hitbox_error
    identity_switch_rate
    occlusion_reentry_accuracy
    minimum_coverage
  validated_metric_families[]
  status = accepted | limited | rejected
  limitations[]
```

只有运行时 `observed_visual_domain` 通过版本化 compatibility predicate 匹配某个 `validated_domains[]`，且 channel 达到对应 quality profile，才能驱动其 `validated_metric_families`。resolution、UI/theme、地图/背景、目标外观、capture transform 或 FOV 未知/不匹配时必须降为 `limited/rejected`，不能沿用别的视觉域的 accepted 状态。`limited/rejected` 只能产生 descriptive/experimental 或 outcome-only 结果，不能进入 severity、AimingProfile 或训练计划。

### 6.2 EventBundle

```text
event_bundle.v1
  events[]
    event_id
    event_kind
    start_ms
    end_ms
    actor_refs[]
    source_refs[]
    confidence
    attributes              # event-kind allow-list only
    limitations[]
  outcome_associations[]
    association_id
    shot_event_ref
    outcome_event_ref?
    target_track_ref?
    weapon_temporal_model = hitscan | projectile | unknown
    association_kind = directly_observed | validated_aligned | inferred
    source_refs[]
    confidence
    availability
    limitations[]
```

允许的核心事件：

- shared：`shot`、`hit`、`miss`、`kill`、`movement_start/end`、`low_confidence`；
- clicking：`target_available`、`acquire`、`settle`、`click_anchor`；
- tracking：`tracking_episode`、`off_target_start/end`、`reacquired`、`target_change_point`；
- switching：`leave_previous`、`candidate_visible`、`target_selected`、`transition`、`next_target_acquired`、`first_damage`。

无法可靠确定目标身份时不得生成 `target_selected` 或 selection error；只能保留 `unclassified_discrete_acquisition`。

`shot/hit/miss` 只有在 source 提供直接 record 或存在版本化、已验证 association 时才能生成。Stats kill row 中的 `Shots/Hits` 是该 kill 的聚合字段，不能升级成带伪时间戳的逐 shot/hit/miss events。

`.perf` 提供稀疏 metric-change records；官方当前描述约 1 Hz，但实际 cadence 必须按 Run 测量并随 source quality 携带，parser 不得假定固定频率。这些 records 不能自行建立逐 shot、逐 damage、逐 target 关联，projectile 的 fired/hit 也可能不在同一 record。只有 `directly_observed` 或通过版本化 fixture/规则验证的 `validated_aligned` association 才能驱动 target-conditioned accuracy、`previous outcome`、`first_damage` 或 selection diagnosis；`inferred`/unavailable 只能作为实验性候选。nearest-target 猜配不得升级为正式 association。

### 6.3 CanonicalRunFacts 与 NormalizedOutcomeTimeline

Stats/Performance 的 L1 投影使用独立、可完整提供给 Coach 的合同：

```text
canonical_run_facts.v1
  analysis_ref
  scenario_profile_ref
  canonical_time_window_ref
  field_registry_version
  source_contracts[]
    source_kind
    source_ref
    parser_version
    source_schema_version?
    recognized_schema_status = recognized | forward_compatible | unrecognized | not_versioned
    unknown_field_observability = none_detected | detected | not_observable
  sections[]
    section_key
    facts
    present_field_keys[]
    source_absent_field_keys[]
    omitted_known_fields[]
      field_key
      reason
    completeness = complete_allowlisted | partial
  outcome_record_sets
    stats_kill_rows_ref?
    performance_metric_changes_ref?
  completeness = complete_allowlisted | partial
  unknown_field_policy = excluded
  limitations[]
```

```text
normalized_outcome_timeline.v1
  analysis_ref
  scope = whole_run | evidence_segment
  segment_ref?
  canonical_time_window_ref
  mode = overview | exact_page
  resolution = deterministic_binned | source_native
  selected_series[]
  overview_series[]?
    metric_key / unit / points[] / source_refs[]
  records[]?
    canonical_time_ms
    source_time
      clock_domain
      value
      unit
      precision
    source_priority
    source_event_index
    values[]
      metric_key
      value
      value_semantics
      unit
    source_refs[]
  event_refs[]
  completeness = complete | paged | downsampled | partial
  next_cursor?
  limitations[]
```

#### 6.3.1 Source Field Registry v1

`source_field_registry.v1` 是 L1 完整性的唯一字段清单。CanonicalRunFacts 保存非重复 Run facts 和精确 record-set refs；NormalizedOutcomeTimeline / normalized events 保存重复记录。三者合起来覆盖 Registry 中所有安全字段，不能因为 records 单独分页就把 facts 标成丢失。

Stats summary（26 个当前已知字段）：

```text
Kills -> outcome_totals.kills : int/count
Deaths -> outcome_totals.deaths : int/count
Fight Time -> outcome_totals.fight_time_s : float/seconds
Time Remaining -> outcome_totals.time_remaining_s : float/seconds
Avg TTK -> outcome_totals.avg_ttk_s : float/seconds
Damage Done -> outcome_totals.damage_done : float/source_damage_unit
Total Overshots -> outcome_totals.overshots : int/count
Damage Taken -> outcome_totals.damage_taken : float/source_damage_unit
Hit Count -> outcome_totals.hits : int/count
Miss Count -> outcome_totals.misses : int/count
Midairs -> outcome_totals.midairs : int/count
Midaired -> outcome_totals.midaired : int/count
Directs -> outcome_totals.directs : int/count
Directed -> outcome_totals.directed : int/count
Reloads -> outcome_totals.reloads : int/count
Distance Traveled -> outcome_totals.distance_traveled : float/source_native
MBS Points -> outcome_totals.mbs_points : float/points
Score -> outcome_totals.score : float/points
Scenario -> scenario.stats_display_name : bounded untrusted string
Hash -> scenario.stats_scenario_hash : validated bounded string
Game Version -> scenario.game_version : bounded untrusted string
Challenge Start -> source_quality.stats_start_time_of_day_ms : int/milliseconds since local midnight
Pause Count -> outcome_totals.pause_count : int/count
Pause Duration -> outcome_totals.pause_duration_s : float/seconds
Avg Target Scale -> challenge_configuration.avg_target_scale : float/source_scale
Avg Time Dilation -> challenge_configuration.avg_time_dilation : float/source_scale
```

Stats input/config（16 个当前已知字段）：

```text
Input Lag -> input_and_calibration.input_lag : float/source_native_unknown_unit
Max FPS (config) -> input_and_calibration.max_fps : float/fps
Sens Scale -> input_and_calibration.sensitivity_scale : bounded untrusted string
Sens Increment -> input_and_calibration.sensitivity_increment : float/source_scale
Horiz Sens -> input_and_calibration.horizontal_sensitivity : float/source_scale
Vert Sens -> input_and_calibration.vertical_sensitivity : float/source_scale
DPI -> input_and_calibration.dpi : int/counts_per_inch
FOV -> input_and_calibration.fov_source_value : float/source_scale
FOVScale -> input_and_calibration.fov_scale : bounded untrusted string
Hide Gun -> challenge_configuration.hide_gun : bool
Crosshair -> challenge_configuration.crosshair_asset_configured : bool/presence_only
Crosshair Scale -> challenge_configuration.crosshair_scale : float/source_scale
Crosshair Color -> challenge_configuration.crosshair_color_rgba : validated 8-digit hex
Resolution -> input_and_calibration.resolution_width/height : parsed positive ints/pixels
Avg FPS -> input_and_calibration.avg_fps : float/fps
Resolution Scale -> input_and_calibration.resolution_scale_pct : float/percent
```

`Crosshair` 的 source value 可能是 asset basename/path-like token，L1 只保留是否配置；basename 本身不进入 Coach。`Input Lag`、sensitivity 与 FOV 只有在 source scale/unit 可解析时才能生成统一物理量，否则保留 source-native 数值和 limitation。

Stats kill row（13 个当前已知字段，每行一个 kill record）：

```text
Kill # -> kill_index : int
Timestamp -> source_time_of_day_ms + mapped canonical_time_ms : millisecond precision
Bot -> bot_name : bounded untrusted string
Weapon -> weapon_name : bounded untrusted string
TTK -> ttk_s : float/seconds
Shots -> shots : int/count aggregated within this kill row
Hits -> hits : int/count aggregated within this kill row
Accuracy -> accuracy : float/ratio
Damage Done -> damage_done : float/source_damage_unit
Damage Possible -> damage_possible : float/source_damage_unit
Efficiency -> efficiency : float/ratio
Cheated -> cheated : bool
OverShots -> overshots : int/count
```

Stats per-weapon aggregate（当前 fixture 的 5 个有值字段）：

```text
Weapon -> weapon_name : bounded untrusted string
Shots -> shots : int/count
Hits -> hits : int/count
Damage Done -> damage_done : float/source_damage_unit
Damage Possible -> damage_possible : float/source_damage_unit
```

weapon header 中位于空分隔列之后、但当前 aggregate row 没有对应值的 labels 不能从 header 复制成 weapon facts；只有 parser 证明 row value 存在后才能在新 Registry version 中加入。

Performance header（4 个字段）与 profile（12 个字段）：

```text
scenario_name -> scenario.performance_display_name : bounded untrusted string
scenario_hash -> scenario.performance_scenario_hash : validated bounded string
challenge_start_utc -> source_quality.performance_start_utc_ms : int/Unix epoch milliseconds UTC
schema_version -> source_quality.performance_schema_version : uint
time_limit -> challenge_configuration.time_limit_s : float/seconds
player_profile -> challenge_configuration.player_profile : bounded untrusted string
added_bots -> challenge_configuration.added_bots[] : bounded untrusted strings
player_max_lives -> challenge_configuration.player_max_lives : int/count; 0 means unlimited
bot_max_lives -> challenge_configuration.bot_max_lives[] : int/count, index-aligned to added_bots
player_team -> challenge_configuration.player_team : int/source_team_id
bot_teams -> challenge_configuration.bot_teams[] : int/source_team_id, index-aligned to added_bots
map_name -> challenge_configuration.map_name : bounded untrusted string
map_scale -> challenge_configuration.map_scale : float/source_scale
timescale -> challenge_configuration.timescale : float/source_scale
end_challenge_after_kills -> challenge_configuration.end_after_kills : float/source condition; 0 disabled
end_challenge_after_damage -> challenge_configuration.end_after_damage : float/source damage unit; 0 disabled
```

Profile 的 `0 = unlimited/disabled` 等含义来自 KovaaK 官方 [Performance Files schema](https://wiki.kovaaks.com/en/home/KovaaK%27s/PerformanceFiles)；parser 只负责按 wire field 解码，不能自行扩展 source 语义。未由官方 schema 定义单位的 scale/source values 仍保留 `source_*` unit 与 limitation。

Performance metric-change payload（17 种；timestamp 始终保留 source-native float32 seconds 与映射后的 canonical time；count/delta/value 的行为语义来自官方 schema contract，而不是 parser 猜测）：

```text
count/per-tick increment: shotsFired, shotsHit, shotsMissed, kills, deaths,
                          overshots, reloads, pauseCount
delta/per-tick increment: damageDone, damagePossible, score, playerDamageTaken,
                          distanceTraveled, mbsPoints
value/instantaneous:      targetSize, targetSpeed, randomSensScale
```

合法 protobuf event 的 payload 是 `oneof`。v1 对同一 event 出现多个已知 payload、缺 timestamp、或既无已知也无 unknown payload 的输入按 malformed 拒绝，不能保留“最后一个覆盖前一个”的有损结果，也不能在没有 source contract 的情况下自行拆成多个事件。只有 unknown payload 的 future event 不进入 L1 records，但必须保留其 top-level `source_event_index` 空位，标 `unknown_field_observability=detected`，并把 normalized timeline/events 标为 `partial` + extension limitation；已知 records 继续可用。未来 schema 的 unknown fields 仍属于 L0；当 parser 只能静默跳过 unknown field 时，`unknown_field_observability = not_observable`，不得宣称已确认没有 unknown fields。

#### 6.3.2 Completeness、排序与分页规则

- `field_registry_version` 必须精确绑定以上 source key、canonical key、type/unit、projection policy 和 required/optional status；Registry 变化创建新版本，不重写历史 facts。
- `complete_allowlisted` 表示已识别 source contract 下的每个 Registry field 都被记为 present 或 source-absent，且没有解析失败、非法值、安全删除或预算截断。已知字段无法解析/验证、被删除或无法说明去向时必须是 `partial`，并列入 `omitted_known_fields[]`；不能用默认值伪造 complete。
- parser 必须保留每个已知 header/profile/event field 的 presence metadata；dataclass 的 `0`、`0.0`、空字符串或空数组不能同时代表“source 缺失”和“source 明确写入零/空值”。无法区分时该 source contract 只能是 `partial`。
- Stats 以受版本控制的 header/field signature 判断 source contract；Performance 以 `schema_version` 判断。未来 Performance schema 可按其 append-only promise 标 `forward_compatible`，但只投影本 Registry 已知字段，并显式保留 unknown-field observability。
- parser 的路径、basename、file bytes/text、protobuf unknown fields、内部对象、checksum 实现细节和异常文本不进入 L1 Coach 投影；source/provenance 使用 stable refs。
- 所有 source-derived strings 都是 untrusted data：执行 schema 长度、类型、控制字符、path/URL/secret sentinel 校验，以结构化字段传递；system/tool policy 明确不得把 scenario/config 文本解释为指令。
- 默认 context 在完整 CanonicalRunFacts 序列化后不超过 8 KiB 时直接携带；超出时携带 section summaries/refs，由 Coach 按需读取。
- timeline 可以覆盖 whole Run 或一个既有 EvidenceSegment，Coach 不能提交任意 start/end。exact page 单页最多 120 records、8 selected series，同时受 24 KiB response 与 turn byte ledger 约束；相同 timestamp 的多个 records 分别计数，不能用“120 timestamps”绕过预算。
- exact records 固定按 `(canonical_time_ms, source_priority, source_event_index)` 排序；`source_priority` 是版本化 Registry 值，`source_event_index` 是包含 unknown-only 空位的 immutable top-level source record order。量化后相同的 canonical time 不能改变 source-native precision 或顺序。
- 精确 timeline/events 超过单页时返回 bridge/owner/analysis/immutable-evidence-revision/query-digest/sort/contract-version-bound 的 opaque `next_cursor`；Coach 只能在当前 bridge 生命周期内回传服务端签发的 cursor，不能构造 offset、时间范围、修改 query 或跨 owner/analysis/turn 复用。
- overview 可以确定性分箱，但必须标 `downsampled`、resolution 和 limitations；精确 page 不能静默丢 event/field。
- Stats exact events 只有 kill-row records；Performance changed-metric events 保留各自 source/time precision 与 count/delta/value semantics。不能为了形成统一 timeline 而伪造逐 shot association。
- Coach 可以从 L1 facts/timeline 识别条件和时序模式、提出解释或请求进一步 evidence；它自行完成的算术不能写回正式 MetricRecord、severity、History baseline 或 AimingProfile。

## 7. MetricRecord、EvidenceSegment 与 SignalWindow

### 7.1 MetricRecord

```text
metric_record.v1
  metric_key
  metric_version
  value?
  unit
  availability
  classification = deterministic | experimental
  provenance
    kind = measured | derived | fused | inferred
    source_refs[]
  population
    sample_count
    valid_count
    excluded_count
  distribution?
    min | p10 | p25 | median | p75 | p90 | max
    histogram_bins[]?
  condition_refs[]
  event_refs[]
  evidence_segment_refs[]
  coverage
  confidence
  limitations[]
```

`provenance.kind = inferred` 或 `classification = experimental` 不能驱动正式 severity、计划目标或跨 Run skill profile。未校准指标允许报告分布、个体 baseline 和同条件变化，不允许套通用健康阈值。

### 7.2 EvidenceSegment

```text
evidence_segment.v1
  segment_id
  analysis_ref
  segment_kind
  start_ms
  end_ms
  focus_start_ms
  focus_end_ms
  title_key
  rank_reason
  issue_refs[]
  metric_refs[]
  event_refs[]
  available_channels[]
  source_coverage
  confidence
  video_playback
    availability
    artifact_ref?
    start_ms?
    end_ms?
  limitations[]
```

规则：

- segment 只能由版本化确定性 analyzer 生成；Coach 不能创建任意时间范围。
- 默认可播放片段目标为 0.5-20 秒；更长 tracking episode 应拆成 focus segments。
- 每个用户问题默认引用 1 个主 segment，最多 2 个补充 segment。
- UI 用本地 `video_playback` reference 播放相同时间段；Coach 当前不接收视频内容。
- segment rank 必须解释是典型、最差、改善、对照还是低置信度，不允许只挑最差帧制造结论。

### 7.3 有界 SignalWindow

Coach 可查看的是已经存在 segment 内的白名单派生曲线，不是原始样本：

```text
signal_window.v1
  segment_ref
  focus_range_ms
  channels[]
    channel_key
    unit
    points[]                 # deterministic downsampled
    source_coverage
    confidence
  downsample_version
  limitations[]
```

v1 硬预算：

- 单次查询只能选择 1 个既有 segment；
- focus window 最长 12 秒；
- 最多 4 个 allow-listed derived channels；
- 每 channel 最多 600 points，由服务端确定性降采样并保留 extrema；
- 每个 Coach turn 最多 6 次 evidence query，所有 SignalWindow 合计最多 2400 points；
- 默认 context 序列化后最多 32 KiB；单次 evidence response 最多 24 KiB；每 turn evidence responses 合计最多 64 KiB，其中 SignalWindow 合计最多占 32 KiB，必须为 list/compare 保留剩余预算；
- histogram 最多 16 bins，每个对象最多 8 条 bounded limitation，所有字符串都有 schema 长度上限；
- bridge 创建 turn 时建立 owner/thread/message-scoped 原子 budget ledger，记录 calls/serialized bytes/signal points，并使用 per-bridge lock 串行执行授权、读取、序列化和扣减；不能只依赖 prompt 或单次 handler 上限；
- 每次合法或非法尝试都消耗一次 call；只有实际返回给 Provider 的 schema-valid response 才按 canonical JSON UTF-8 byte length 和实际 points 消耗 byte/point budget。验证失败、owner 拒绝或超限错误不得虚构 response usage；
- 服务端可根据剩余 byte/point budget 确定性减少 SignalWindow 采样点，并返回 `truncated`、`budget_used`、`budget_remaining`；CanonicalRunFacts、exact timeline/events 不能丢字段后标 complete，放不下时必须改用 section/page refs 或显式 unavailable；provider/runtime 可以施加更低预算，但不能突破产品硬上限；
- 超预算、低 confidence、owner 不匹配或 unavailable 一律 fail-closed。

### 7.4 MotionPredictabilityEvidence

```text
motion_predictability_evidence.v1
  evidence_id
  segment_ref
  kind = known_script | periodicity | repeatability | model_fit
  model_id / model_version
  fit_metric_key / fit_metric_version
  fit_value
  acceptance_threshold_ref
  source_refs[]
  availability
  confidence
  limitations[]
```

正式 `predictive_lead` MetricRecord 必须在 `condition_refs[]` 引用 accepted 的 MotionPredictabilityEvidence；EvidenceSegment 同时暴露该 safe ref。缺失、unavailable、低 confidence 或 threshold/version 不可解析时，validator 只能接受 descriptive relative lag/lead，不得接受 predictive-control mechanism claim。

## 8. Aim-family analyzer 合同

### 8.1 共用输出

每个 analyzer 必须返回：

```text
analyzer_result.v1
  analyzer_id / analyzer_version
  scenario_profile_ref
  support_status = supported | partial | outcome_only | unsupported
  quality_gate
  metrics[]
  events[]
  evidence_segments[]
  diagnosis_inputs[]
  limitations[]
```

analyzer 只能消费允许的 Signal/Event channels。unsupported 不得回退到另一个 family 的阈值。

### 8.2 Static clicking

复用并迁移当前 click-anchored input-native 指标：movement timing、peak/time-to-peak、accel/decel/settle、path efficiency/straightness、reverse/correction/submovement、SPARC 和分布/outlier refs。

当可靠 target track 可用时，可附加 normalized click error、initial acquisition 与 terminal correction；没有 target facts 时保留 input-only 语义，不使用 overshoot/undershoot 命名。

### 8.3 Dynamic clicking

正式 target-relative 诊断至少要求：可靠 target track、click/hit event、统一坐标和 canonical time。允许指标：

- normalized click error 与 miss vector distribution；
- target speed/acceleration/change-state conditioned accuracy；
- acquisition time、terminal correction、click timing；
- crosshair-target relative velocity、lead/lag descriptor；
- static-matched 与 dynamic-condition 差异。

只有 scenario profile 标明 predictable 且片段质量足够时才可讨论 predictive lead；否则只说“该速度/变向条件下误差上升”。

ScenarioProfile 的 `predictable` 只是场景级适用性标签，不足以证明单个片段使用了预测控制。正式 `predictive_lead` 还必须引用第 7.4 节的 segment-level MotionPredictabilityEvidence；缺失时只报告相对 lag/lead descriptor，不解释为认知预测机制。

### 8.4 Continuous tracking

正式 target-relative tracking 至少要求 target/crosshair trajectory、hitbox/radius、统一坐标、时间对齐和 confidence。允许指标：

- median/p90 error、time in hitbox / within radius；
- loss count/duration、reacquisition latency；
- lag 与补偿 lag 后的 velocity gain；
- target change-point response；
- speed/acceleration mismatch；
- correction burden、SPARC/smoothness；
- 仅对足够长、近似平稳片段计算 coherence/phase/gain。

predictable 与 reactive 必须分开评估。manual tracking 可能包含间歇式纠偏，因此“有 correction”不等于“不平滑”；参见 [Parker et al. 2020](https://doi.org/10.1007/s00221-020-05962-0) 与 [Miall et al. 1993](https://doi.org/10.1080/00222895.1993.9941639)。SPARC 的版本、窗口、滤波和数值方向必须固定，参见 [Balasubramanian et al. 2012](https://doi.org/10.1109/TBME.2011.2179545)。

### 8.5 Target switching

最小事件链：

```text
previous outcome
  -> leave previous
  -> candidate/selection (only when observable)
  -> transition
  -> next acquire
  -> settle/fire/damage
```

允许指标：inter-target transition time、distance-conditioned path efficiency、next-target pre-alignment、first-shot error、acquire/settle duration、carry-over overshoot、selection error（仅当期望选择语义可观测）。

switching 不能被简化为连续多个 flick：多目标竞争与选择可能和运动规格并行，参见 [sensorimotor decision review](https://pmc.ncbi.nlm.nih.gov/articles/PMC6107066/) 与 [continuous tracking switch experiment](https://pmc.ncbi.nlm.nih.gov/articles/PMC7099481/)。

### 8.6 Movement aiming

没有玩家移动/视角/目标联合遥测时，只报告 Stats outcome 和输入运动学，不把误差归因为 counter-strafe、movement sync 或 movement reading。正式机制诊断需新数据合同，不由本文猜测。

## 9. 确定性诊断、知识与处方

每条正式 issue 必须形成闭环：

```text
observation
  -> scope/conditions
  -> plausible mechanisms + alternatives
  -> knowledge refs + claim levels
  -> prescribed drill condition/cue
  -> target metric/direction
  -> delayed matched + transfer retest
```

规则：

- severity 来自版本化规则和足够质量的 metric/event，不来自 LLM。
- 绝对阈值必须有 calibration dataset/version；否则使用用户自身 baseline、matched-condition difference 或 descriptive rank。
- Knowledge Registry 需补 dynamic clicking、predictable/reactive/control tracking、switching、failure modes、feedback 与 transfer/retest 条目。
- 学术来源锚定机制和验证；社区 taxonomy/scenario/drill 可用于覆盖和表达，但不得伪装成训练迁移证明。
- immediate score 属于 performance，不自动等于 learning；训练计划必须包含延迟复测与至少一个 matched/near-transfer 条件。参见 [Kantak & Winstein 2012](https://doi.org/10.1016/j.bbr.2011.11.028)。

### 9.1 首发知识覆盖矩阵

首发不是“有一篇 tracking 文档”就算覆盖。每个 family 必须把 analyzer 能输出的 observation 映射到可解释的 quality prerequisite、好/坏方向、替代解释、改善 cue 和复测条件：

| Family / condition | 必须覆盖的 observation | “好/坏”只能如何判断 | 改善与验证边界 |
|---|---|---|---|
| static clicking | movement timing、peak placement、terminal control、path/correction、click timing、分布/outlier | exact scenario/self baseline 或有 calibration ref 的方向；无 target track 时只用 input-only 语言 | cue 必须指向 acquisition/terminal/click phase；matched static retest + 一个 size/distance 近迁移 |
| dynamic clicking | normalized error、relative velocity、target-state conditioned outcome、lead/lag descriptor | 只在视觉质量和 OutcomeAssociation 通过时按 target speed/change condition 比较；predictive claim 另需 MotionPredictabilityEvidence | 分开练 acquisition、velocity matching、click timing；matched motion condition + 未见过的 speed/change condition |
| predictable tracking | error/time-in-radius、lag、gain、loss、correction/smoothness | 只在足够长、近似平稳且 alignment 可分离时解释 phase/gain；correction 存在本身不是坏 | cue 聚焦速度匹配、稳定跟随与小幅修正；matched script + phase/speed 变化近迁移 |
| reactive tracking | change response、loss/reacquisition、overshoot/under-response、post-change stability | 以 validated change-points 和 self/matched distribution 判断；不得把 capture offset 当反应时间 | cue 聚焦变向确认、重新捕获和回稳；matched random condition + 未见变化序列 |
| control/smoothness tracking | correction burden、SPARC、error 与 coverage 的联合变化 | smoothness 不能脱离 accuracy/error 单独优化；窗口、滤波、SPARC 方向与版本固定 | 训练目标必须同时保留误差/coverage guardrail；matched smooth condition + 不同 target size/speed |
| target switching | previous outcome、leave/transition/acquire/settle、first shot/damage、可观测 selection | target identity/outcome chain 完整时才能区分 transition、terminal 和 selection；否则只描述离散 acquisition | cue 分开 transition efficiency 与 arrival control；matched target layout + distance/direction/target-count 近迁移 |
| movement aiming | outcome totals、通用输入运动学 | 没有玩家移动遥测时不判断 counter-strafe、movement sync 或 movement reading | 只给 outcome observation；不进入 mechanism prescription、画像或计划 |

每个 active knowledge entry 至少包含 `definition`、`scope`、`quality prerequisites`、`expected direction`、`limitations`、`counterevidence/alternatives`、`cue`、`dose guardrail`、`matched retest` 与 `near-transfer retest`。缺任何一项时，对应正式 issue 不得进入 Coach 处方链；社区 scenario/drill 只能作为可替换 catalog entry。

`expected_direction` 只能是 `lower_better | higher_better | target_band | descriptive_only | comparison_only`；需要条件联合解释的 correction、SPARC、lag/gain 等不得被压成无条件单向分数。movement outcome-only 的 `cue/dose/retest` 固定为 `not_applicable`，并且不能生成 issue、prescription、profile contribution 或 plan item。

## 10. Coach 权限与上下文预算

### 10.1 默认 context

`coach_diagnostic_context.v2` 默认只投影：

- analysis/scenario/analyzer refs；
- 完整 CanonicalRunFacts（序列化后 <= 8 KiB）或 bounded section summaries/refs；
- source availability、alignment、coverage 与 confidence；
- 最多 24 个核心 MetricRecord summary；
- 最多 6 个 ranked issues；
- 每个 issue 的主/补充 EvidenceSegment refs；
- 最多 4 个可比 History trend summaries；
- active Training Plan、最近复测结果与已知 limitations。

历史 `coach_diagnostic_context.v1` 原样可读/展示且保持 v1 低粒度语义，不能凭空升级为包含 CanonicalRunFacts 的 v2。只有新完成的 Analysis 生成 v2；store/coerce/runtime/route 必须并行接受 v1/v2，版本不支持时显式 unavailable，不能把历史 context 读成 `None` 后静默丢失。

### 10.2 受限查询命令

沿用现有 owner-scoped product-command bridge，新增只读命令：

```text
analysis.metrics.distribution
analysis.evidence.list
analysis.evidence.signal_window
analysis.evidence.compare
analysis.run_facts.get
analysis.outcomes.timeline
analysis.events.list
profile.aiming.snapshot
```

| 命令 | 输入边界 | 输出边界 |
|---|---|---|
| metric distribution | bridge-reachable completed analysis ref + 最多 8 个 metric keys | summary/distribution/condition/event/segment refs；不返回完整 per-event raw array |
| evidence list | bridge-reachable completed analysis ref + allow-listed kind/issue filters + limit <= 20 | segment metadata、rank、confidence、refs |
| signal window | 1 个现有 segment ref + 最多 4 个 allow-listed channels | 第 7.3 节的 bounded derived points |
| evidence compare | 2-4 个 segment/run refs + 最多 8 个 metric keys | matched conditions、delta、comparability/limitations；不拼接原始序列 |
| run facts | bridge-reachable completed analysis ref + `all` 或 allow-listed sections | 完整 allow-listed CanonicalRunFacts；超单响应预算时显式返回 section refs，不静默截断 |
| outcome timeline | bridge-reachable completed analysis ref + `whole_run`/1 个 segment ref + overview/exact-page + 最多 8 series，或当前 bridge 签发的 cursor | exact page 最多 120 records 且受 24 KiB 限制；overview 明确 downsampled |
| normalized events | bridge-reachable completed analysis ref + whole-run/segment scope + allow-listed event kinds + limit <= 20，或当前 bridge 签发的 cursor | 类型化 Stats kill rows、Performance metric changes 或安全 EventBundle facts，保留 source/time precision、value semantics 与 association limitations |
| aiming profile | owner 当前 profile | bounded dimensions、trend、confidence、last assessed、plan/retest refs |

`profile.aiming.snapshot` 只有在 AimingProfile store 完成并注册 capability 后才可用；此前不得用空对象伪装画像存在，应返回稳定 `unavailable/profile_not_built` 或完全不注册该 capability。

bridge 初始化时只把默认 context 中的 analysis/run/segment refs 放入 reachable set；随后只允许使用 handler 返回的新 safe refs。模型猜出的同 owner 历史 analysis id 也必须拒绝。timeline/events 第一页使用完整 query，后续页只能使用当前 bridge 返回的 opaque cursor；cursor 不能和 facts section ref、另一命令或另一 query 互换。compare 中的 refs 必须全部 reachable，且 exact-run 与 segment comparison 分别执行其版本化 comparability predicate。

backend bridge state 必须保存不可伪造的 `bridge_id/turn_id`，cursor registry 绑定该 id；bridge revoke/expiry 同时清理 cursor。同 owner 新 bridge、已撤销 bridge、跨 command-kind 或过期 cursor 全部拒绝，cursor value 不进入 SQLite、safe-parameters summary、tool event 或 trace。

### 10.3 禁止项与审计

Coach 不得提交或获得：

- 任意 start/end time、frame range、SQL、Python、regex、path、URL 或 artifact id 枚举；
- Raw `dx/dy/timestamp/buttons`、完整私有 SignalBundle/EventBundle；
- 原始 Stats CSV bytes/text、`.perf` protobuf bytes、绝对路径、私有 parser object、unknown future fields 或未经过字段白名单的 source payload；
- MP4、frame、thumbnail、video bytes 或外部可访问媒体 URL；
- 另一个 owner、queued/running/deleted analysis 或未经用户拥有的 evidence；
- 未在 ScenarioProfile/segment 中 allow-list 的 channel/metric。

完整 tool result 只通过当前 loopback bridge transport 的 HTTP response 返回给当前 Provider transcript，属于本轮已授权的 Coach context。除该 bridge response 外，SQLite command journal、tool event、message trace、普通 API response 和日志只保存 audit projection：command、owner/thread/message、analysis/segment refs、requested metric/channel keys、query digest、budget used/remaining、result refs 和 status；不保存 facts、events、points、正文、路径、cursor 内容、token 或原始 payload。现有 `coach_product_commands.result_json` 在这些 evidence commands 上必须接收 audit projection，不能持久化 Provider 看见的完整 result。audit projection 持久化成功前不得返回完整 evidence；写入失败时只返回无正文的 `unavailable/audit_unavailable`，不能回退为保存或返回未审计的完整 result。

本节禁止的是 L0 原始载体和无类型内部 payload，不禁止第 6.3 节的完整 CanonicalRunFacts、分页精确 outcome timeline 或规范化 events。L1 输出仍必须通过字段 allow-list 直接构造，不能先序列化完整 parser result 再做字符串删除。

## 11. 视频的当前与未来边界

### 11.1 v1

- MP4 只由本地确定性预处理器读取，产出版本化 numerical signals/events/confidence。
- Coach 先基于这些数值证据分析，再引用 EvidenceSegment；用户在 UI 中播放对应本地时间段。
- 视频预处理失败时，保留可用 Raw/Stats/Performance 结果并降级 support status，不虚构 target-relative 事实。
- 当前 Provider 是否支持视觉不影响 v1 分析能力；Coach runtime 不上传视频。

### 11.2 未来可选视觉模型

“Coach 当前不读视频”不是永久产品禁令。未来若引入视觉模型，必须新建版本化 `coach_vision_evidence` 合同，并同时满足：

- 用户显式启用，清楚显示 provider、片段时长、预计成本、上传/本地处理和隐私影响；
- 只能读取确定性层已生成的 EvidenceSegment 限定片段，不能浏览完整 Run 或任意文件；
- 片段数量、时长、分辨率和调用预算有硬上限；
- 输出标记 provider/model/version、segment refs、cost、retention 与 `model_inferred`；
- 不能覆盖 deterministic metric、改变 source artifact 或静默进入 History baseline；
- provider 不支持、用户未授权或预算不足时完全回退到 v1 数值链路。

## 12. History、画像与训练计划

### 12.1 AimingProfile

```text
aiming_profile.v1
  owner_ref
  dimensions[]
    dimension_key
    current_level_descriptor
    supporting_metric_refs[]
    scenario_profile_refs[]
    trend_direction
    confidence
    last_assessed_at
    limitations[]
  recurring_patterns[]
  resolved_patterns[]
  active_plan_ref?
  next_retest_refs[]
```

画像是可更新的 evidence-backed assessment，不是永久人格标签。单次低质量 Run 不得覆盖多次高质量趋势；冲突证据必须保留。

画像聚合使用 append-only、按 Analysis 幂等的 `profile_contribution.v1`。Analysis terminal commit 后创建或替换同一 `analysis_ref` contribution；retry 不重复累计。Analysis 删除时先 invalidation contribution，再从剩余有效 contributions 确定性重建受影响 dimensions；已删除 evidence 只保留 tombstone，不能继续支撑当前画像。完成、重试、删除和启动 reconciliation 必须共用这一合同。

### 12.2 Training Plan

每个 `plan_item` 有稳定 ref、owner、plan revision、status，并至少引用：diagnosis、knowledge、scenario profile、baseline metric、目标方向、练习条件、cue、剂量上限、matched retest、near-transfer retest 和 review date。社区场景名称是可替换 catalog entry，不是处方科学有效性的唯一依据。

用户实际训练写入 append-only `plan_execution.v1`：plan/item revision、scenario/run refs、planned/completed dose、completion status、用户反馈和时间；复测写入 `retest.v1`：matched/near-transfer 类型、expected metric/direction、Analysis refs、comparability、result 与 limitations。调整计划只能引用这些 stable facts，不能从聊天文本猜测完成情况。plan execution/retest 跟随本地 owner 与现有 Training Plan 保留/删除合同；本文不新增静默自动删除。

计划调整只能基于：新可比证据、执行记录、用户反馈或显式约束。Coach 可以起草和解释；保存、激活、暂停、调整和复盘继续使用现有确认/审计合同。

## 13. 安全、性能与失败语义

- local-first：原始与 derived artifacts 默认只留在 app-owned workspace。
- owner scope：所有 evidence/profile/plan 查询在 handler 内重新授权，不信任 Coach payload。
- deterministic replay：相同 source revisions + versions + config 产生相同结构化结果。
- bounded cost：视频预处理、derived artifact 大小、Coach context 和 evidence query 都有独立预算；不得阻塞 Raw capture。
- fail-closed：unknown schema/metric/channel/scenario/analyzer version 不进入正式 diagnosis。
- graceful degradation：visual failure 不删除 native facts；native failure 不让 video 自动冒充 input-native；unknown scenario 退化为 outcome-only。
- privacy sentinel：path、secret、raw markers、video/frame payload 不得出现在 Coach payload、tool trace、stored message、日志或 public API。
- information preservation：CanonicalRunFacts 和 exact outcome pages 必须声明 completeness；预算不足时分节/分页或显式 unavailable，不能静默丢字段后仍标 complete。
- deletion：Analysis-owned signal/evidence artifacts 跟随 terminal Analysis deletion/reconciliation；Run-owned sources 不被 Analysis 删除。

## 14. 发布 Gate

### Gate A - canonical correctness

- Stats time-of-day observation 经可信 mapping 后与 Performance anchor、Raw、MP4 使用同一 CanonicalTimeWindow；Stats 单独存在时不伪造 UTC；
- 同一真实/黄金 Run 的 shots/hits/misses、click events、coverage 和 segment 时间一致；
- high polling same-ms records 由 record order/版本化重采样正确处理。

### Gate B - contracts and privacy

- ScenarioProfile、Signal/EventBundle、CanonicalRunFacts、NormalizedOutcomeTimeline、MetricRecord、EvidenceSegment、SignalWindow schema/validator 完整；
- raw/path/secret/video/frame sentinel 覆盖 Python、Pi、tool trace、stored message 和 API；
- 当前 schema 的完整 allow-listed Stats/Performance facts 能进入 Coach，且原始 CSV/protobuf/private parser/unknown fields 不能进入；
- source field Registry 对当前 Stats/Performance schema 的 known/present/source-absent/omitted/unknown-observability 可审计；
- oversized Run facts 显式分节，exact timeline/events 使用 bridge/owner/revision/query-bound cursor 分页，无 silent truncation 或跨 owner/query/bridge cursor replay；
- query budget、owner scope、deleted/nonterminal/unknown ref 全部 fail-closed。
- 当前 loopback bridge transport response/Provider transcript 只在当前 turn 获得完整 evidence result；SQLite journal、tool event、message trace、普通 API response/log 只持久化 audit projection。

### Gate C - analyzer validity

- static clicking 现有 metric/diagnosis 无回归；
- launch manifest 中 static/dynamic/tracking/switching 每个 family 至少一个 exact hash 绑定可分发 fixture、审核来源、ScenarioProfile、analyzer/knowledge Gate 和必要 visual-quality profile；没有合格 entry 的 family 只能显示 unavailable/outcome-only；
- dynamic clicking、tracking、switching 各有 synthetic golden fixtures、人工标注视频片段和至少一组真实 Run 验证；
- 每个 visual producer/version 有可审计标注协议、validated visual domains 和量化 quality profile；runtime domain compatibility、center/radius error、identity continuity、occlusion reentry 和 coverage 达到其声明的 metric-family 门槛，未达到时只降级，不允许 severity/profile/plan；
- target-conditioned accuracy 与 switching outcome chain 有逐事件 OutcomeAssociation；缺失时相关 metric/claim 正确 unavailable；
- tracking 的 lag/gain/change response 能区分系统 alignment error 与人的条件性响应；
- switching 在目标身份不可观测时不会生成 selection claim；
- unknown/movement aiming 正确退化为 outcome-only。

### Gate D - Coach usefulness

- Coach 能从粗摘要下钻到完整规范化 Run facts、whole-run/segment outcome timeline、规范化 events、分布、一个主 signal window 和最多两个对照片段；
- 回答引用的 metric/knowledge/evidence refs 都能解析；
- 处方包含目标、机制边界、cue、matched retest 与 near-transfer retest；
- 画像和计划能在新证据到来后更新，同时保留冲突与不确定性。

### Gate E - operational release

- 预处理 latency、CPU、峰值内存、artifact 大小和 Coach token budget 在发布基准内；
- worker crash/retry 不产生半写 artifacts 或重复 profile updates；
- 无 Provider 时确定性 analysis、evidence、History 和规则化提示仍完整可用；
- 前端能从 EvidenceSegment 播放本地对应视频段，但不会把媒体交给 Coach。
