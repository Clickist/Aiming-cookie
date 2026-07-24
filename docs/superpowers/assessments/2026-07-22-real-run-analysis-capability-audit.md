# Real Run Analysis Capability Audit

> 状态：assessment completed on 2026-07-22。本文集中记录现存实机数据、已经产品化的采集规则，以及这些证据能支持的 Analysis Gate。它不是采集 implementation plan，也不授权重新采集已经存在的 normal/timescale/restart 证据。

## 1. 结论

Complete Coach Task 6 不能再以“缺少真实 Run”或“capture receipt 未携带完整 `observed_visual_domain`”作为阻塞理由。当前机器已经保留三套 accepted canonical 四件套和一套 pause 负例：

- static normal：`1wall 6targets small`；
- dynamic/timescale：`1wall5targets_pasu`；
- static post-Restart：`1wall 6targets small`；
- pause diagnostic：`1wall 6targets small`，明确不得作为 canonical 正例。

真实数据足以开始 static/dynamic clicking 的视觉 producer 审计。真正未闭合的是：人工标注、当前 detector 在真实画面上的 center/radius/identity/re-entry 误差、producer review 和量化 quality profile。tracking 与 target switching 尚未找到同 family 的完整四件套，必须保留为各自 analyzer/release Gate，不能让 clicking 数据冒充。

## 2. 实机数据集索引

| Evidence ref | 位置 | Scenario / family | 状态与用途 |
|---|---|---|---|
| `field:task4-normal@2026-07-19` | `E:\DevCache\temp\aiming-cookie-task4-normal-20260719-130214` | `1wall 6targets small` / static clicking | accepted canonical 正例；MP4、Raw、Stats、Performance 完整 |
| `field:task4-timescale@2026-07-19` | `E:\DevCache\temp\aiming-cookie-task4-timescale-20260719-131856` | `1wall5targets_pasu` / dynamic clicking | accepted canonical 正例；`timescale ~= 0.7`，85.694 秒四件套完整 |
| `field:task4-restart@2026-07-19` | `E:\DevCache\temp\aiming-cookie-task4-restart-20260719-145546` | `1wall 6targets small` / static clicking | accepted post-Restart 正例；只包含完成的重启后 Challenge |
| `field:task4-pause@2026-07-19` | `E:\DevCache\temp\aiming-cookie-task4-pause-20260719-133540` | `1wall 6targets small` / pause negative | diagnostic-only；没有 accepted canonical Raw/MP4，不得训练 detector/profile |
| `field:run-52096@2026-07-20` | AppData Run `52096` + `E:\DevCache\temp\aiming-cookie-task9-field-20260720-021334` | `1wall 6targets small` / product path | 当前最佳真实产品链正例；60 秒 MP4、41,363 Raw points、Stats/Performance 指纹和 receipt/window 一致 |
| `field:run-52060@2026-07-20` | AppData Run `52060` + Task 7 repair evidence | static / Raw-tail negative | managed Raw 距 canonical end 6,722 ms；不能作为完整 Raw 正例 |
| `field:run-52058@2026-07-20` | SQLite Run `52058` + Task 7 repair evidence | static / pause negative | `pause_unsupported`、`video_pause_unsupported`、无 canonical window、无正常 Analysis mode |
| `field:run-52136@2026-07-20` | AppData Run `52136` + Task 7 interrupt evidence | static / recovery | interrupted finalization 后同 Run 幂等恢复；用于 lifecycle，不用于 detector quality |

外部实机目录是本机 field evidence，不是可分发测试 fixture。CI 不能依赖这些绝对路径；后续若提取小型 fixture，必须先确认可分发性、记录来源 hash 和 annotation protocol，并保留原 field evidence ref。

## 3. 已建立规则的追踪矩阵

| 规则 | 产品合同 | 实现与测试 | 实机/SQLite 结果 | Analysis 含义 |
|---|---|---|---|---|
| normal，`Pause Count = 0` | PRD 7.1、Architecture 2/3、Roadmap Gate | `time_alignment.v2`、Run Finalizer、capture/finalization tests | Task 4 normal、Run 52096 pass | 可生成 canonical Run；仍需各 analyzer/quality Gate |
| timescale-only，`Pause Count = 0` | 同上 | timer/performance window tests | `1wall5targets_pasu` 85.694 秒 pass | 可作为 dynamic clicking 真实输入 |
| `Pause Count > 0` | 明确 fail closed | `time_alignment.py` pause guard、`video_pause_unsupported` finalizer tests | Run 52058：alignment unavailable、无 canonical window/MP4/Raw claim | 不进入正常自动 Analysis；只做 negative/diagnostic evidence |
| Restart | 自动 finalization 合同 | source revision/finalization tests | Task 4 restart 仅保留完成的 post-Restart 局 | previous attempt 不得混入分析 |
| encoded-video coverage gap | Architecture/lifecycle contract | commit-first invalidation、tombstone tests | field evidence触发 `video_coverage_gap` | 不得保留伪完整 MP4/Raw claim |
| Raw canonical tail coverage | Raw barrier contract | barrier/coverage tests | Run 52060 负例、Run 52096 修复正例 | incomplete Raw 不得进入 input-native/multimodal readiness |
| interrupted finalization | recovery contract | startup reconciliation tests | Run 52136 同 Run 幂等恢复 | 不重复 Run/export，不改变 evidence identity |

这些规则已经分别落入 PRD/Architecture、代码、测试和 SQLite Run 状态。缺失的是集中索引，不是规则本身。

## 4. 当前四类输入实际提供什么

### MP4

- decoded resolution、frame PTS、完整可见像素；
- 固定 viewport center 的 aim point；
- target/background/HUD 的可观察外观；
- 可在本地生成 target center、visible radius、track、occlusion 和 scene-state 候选。

MP4 不直接提供语义标签。target identity、appearance class、background class 和结果事件必须由 reviewed producer、标注或其它正式来源建立，不能靠字段名猜测。

### Stats

现有四套 field Stats 都包含：

- scenario、hash、Challenge Start、Pause Count/Duration；
- resolution `1920x1080`、FOV `103.0`、Resolution Scale `100.0`；
- crosshair asset presence、scale `0.7`、color `000101FF`；
- sensitivity、DPI、kill rows、weapon/outcome aggregates。

Stats 的 `Resolution Scale` 不能静默改名为 UI scale；crosshair asset basename 也不能进入 Coach。

### Performance

- scenario/hash、map name/scale、time limit、timescale、team/bot profile；
- 约 1 Hz metric-change records，包括 shots/hits/misses/kills/damage/score/targetSize/targetSpeed 等可用字段；
- pause evidence 和 canonical alignment 辅助。

Performance 不能自行提供逐 frame、逐 shot-target 或 target identity 真值。

### Raw Input

- capture-clock aligned `dx/dy/buttons/timestamp`；
- 输入运动学和 click anchor；
- 不提供绝对准星、目标、FOV、theme 或 target appearance。

## 5. Task 6 合同纠正

### 5.1 被拒绝的方案

**Receipt-centric seven-field gate**：要求 capture receipt v2 上报 resolution/UI scale/theme/map-background/target appearance/capture transform/FOV，缺字段就阻塞全部视觉分析。

拒绝原因：resolution/FOV/crosshair 已在 Stats 或 MP4；theme/background/target appearance 本来是本地视频观察/producer validation 问题；UI scale 未证明为所有 metric 的必要输入；capture transform 应由现有 receipt/window、decoded resolution 和版本化 mapping 证明。把它们全部推给采集会重复开发，并把 detector 责任错误转移到 capture。

### 5.2 采用的方案

**Evidence-led, per-metric compatibility gate**：

1. worker 只消费冻结的现有 Run inputs，不重读用户源路径，也不要求新 capture receipt；
2. runtime selector 使用 exact scenario hash、decoded resolution、canonical video mapping version，以及 profile 明确依赖时的 Stats FOV；
3. background/target/HUD 语义属于 annotation/profile review metadata，不从视频猜标签；producer 只生成 candidate morphology/coverage 等数值质量摘要，且不能用这些摘要循环自证 detector 正确；
4. visual quality profile 声明每个 metric family 真正需要匹配的 selector keys、annotation refs、误差阈值与禁止用途；
5. compatibility 和 runtime quality 按 metric family `accepted | limited | rejected`，未知但与某 metric 无关的字段不能 blanket-block 全部 family；
6. observed context 和 quality 结果写入本地 visual artifact，Coach 只得到 safe summary/limitations 和 evidence refs。

### 5.3 现有真实画面对当前 detector 的反证

Task 4 normal/timescale 的目标是白色场景中的深色球体，而当前 synthetic test/producer 只证明固定 HSV 红色圆形目标。对真实末帧使用简单深色圆形 detector 时会同时检出目标和底部 HUD/文字候选；说明当前实现缺少 reviewed HUD mask、真实外观配置和标注误差，不能注册为 production producer。

这证明 Task 6 的真实阻塞是 detector/profile validation，不是采集缺字段。

## 6. Task 6-9 使用边界

- Task 6：必须先消费 normal/timescale/restart 正例与 pause/coverage 负例，完成 shared visual producer 的真实审计；
- Task 7 dynamic clicking：可以使用 `1wall5targets_pasu` 作为第一条真实 family Run；
- Task 8 tracking：仍需 tracking family 的完整真实 Run 和人工标注；
- Task 9 switching：仍需 switching family 的完整真实 Run、target identity/outcome chain 标注；
- Task 12 release Gate：每个 launch family 仍至少需要一条真实 Run，clicking 数据不能跨 family 代替。

Task 10 知识合同不应被“重新采集已有 clicking 证据”阻塞；但没有对应 analyzer facts 时，知识 entry 不得冒充已实现 diagnosis。

## 7. 下一步 Gate

1. 对 normal 与 timescale MP4 建稀疏、可审核 annotation ledger；
2. 在真实帧上量化 center/radius、false positive、identity continuity、re-entry 和 coverage；
3. 让 runtime compatibility 只消费现有 source-derived facts和 producer-observed摘要；
4. 删除 worker 对上游 `observed_visual_domain` snapshot 的硬依赖；
5. normal/timescale/restart/pause 分别形成 positive/positive/identity/negative 回归；
6. tracking/switching 的缺失证据留给各自 Task，不重复采集 static/dynamic clicking。

在上述 Gate 完成前，production visual producer registry 保持为空是正确的 fail-closed 行为。

## 8. 2026-07-22 candidate field audit（不构成 production review）

本轮从现有 normal canonical MP4 抽取 6 帧、timescale/dynamic canonical MP4 抽取 9 帧，生成 contact sheet 和 detector overlay。审计素材保存在本机 `E:\DevCache\temp\aiming-cookie-task6-annotation-candidate-20260722`，不进入仓库或 Provider。

直接深色 round-blob detector 会稳定把顶部计时文字、底部场景文字和中心准星当作候选。候选 `visual_target_detector.v2` 使用版本化 normalized exclusion regions 后，这 15 帧 overlay 中未观察到明显 HUD false positive 或明显漏掉的可见深色球；这只说明该配置值得进入独立标注复核，不证明 center/radius/identity/re-entry 达标。

代码已增加 annotation-vs-prediction evaluator，确定性计算 center median/p95、radius error、false-positive rate、coverage、identity-switch rate 和 occlusion re-entry accuracy。没有观察到 re-entry 时结果为 `None + occlusion_reentry_not_observed`，不能自动填成通过；quality profile 现同时绑定 detector config ref、HUD mask version、审核环境/目标标签，worker 拒绝 detector config 与 profile calibration ref 不一致的注册。

本次 overlay 检查不是独立人工 annotation ledger，也没有第二 reviewer、连续 identity truth、遮挡/re-entry truth、restart/pause detector replay 或可分发 fixture。因此 production registry 继续为空，Task 6 仍未完成；不得用本段结果启动 Task 10 或激活任何 launch family。
