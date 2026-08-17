# Coach 知识库一致性审核报告（registry v7，2026-08-16）

只读审核，未修改任何产品代码、registry、文档或 git 工作树。本报告文件为未跟踪产物，不提交。

## 1. 范围与方法

- **对象**：`knowledge/coach/registry.v7.json`（27 条 entry，46 个 source，feat 分支 `feat/capture-generalization-knowledge-2026-08-15`），对照 `registry.v6.json`、`schema.v3.json`、`migrations/2026-08-15-v6-to-v7-audit.json`。
- **事实源核对**：`kovaak_tracker/metric_definitions.py`、`advice.py`、`advice_tracking.py`、`advice_dynamic_clicking.py`、`advice_target_switching.py`、`native_flicking_analysis.py`、`flicking.py`、`coach/knowledge_registry.py`、`coach/diagnosis.py`、`coach/agent_tools.py`、`coach/agent_kb.py`、`webapp/backend/worker.py`（tier→analyzer 分发）、`kovaak_tracker/scenario_profiles.py`。
- **产品边界**：`docs/PRD.md` §5.7 与首发 family 决策、`docs/ARCHITECTURE.md` §2.3（multimodal / input_native / video_fallback 三层与 baseline 合同）。
- **方法**：先程序化交叉对照（signals / metric_refs / claim_level / family_scope / expected_direction / sources 建矩阵，v6↔v7 逐条 JSON diff，v1→v7 信号面 lineage 追踪），再对候选做语义细读确认。两版 registry 均通过 `validate_registry()`（结构合同层无违规，validator 强制的 source ceiling / applicability / capability ladder 全部成立）。
- **v6→v7 实际变化**（比文件体积差小得多——体积差主要是格式化）：仅 2 条 entry 各新增 metric_refs——`tracking.control-smoothness` 加 `metric:decel_frac`、`metric:path_efficiency`、`metric:reverse_ratio`；`static.flicking-terminal-control` 加 `metric:decel_frac`、`metric:path_efficiency`。其余 25 条字节级不变，signal_aliases、sources 不变。本报告的 v7 特有矛盾集中来自这次小改动及其与既有合同的交互。

**结论速览**：真矛盾 7 条（高 0 / 中 3 / 低 4）；表面矛盾澄清 7 条；设计张力 2 条。内容层（教练口径、断言边界、claim 分级）一致性良好；矛盾集中在接口层（registry token 与管线 token 的命名/存在性漂移、v7 前瞻接线无消费方）与少量条目内"文本 vs 结构"错位。

---

## 2. 六类逐项结果

### 类 1：条目间内容矛盾 —— 未发现真矛盾

逐对核对了共享机制/信号的条目组合，以下高风险概念在全部 27 条中口径一致（互相以 forbidden_inferences 印证，无一对断言相反）：

- **身体原因不可从运动学推断**：`static.flicking-terminal-control.forbidden_inferences`（"Do not infer grip, muscle tension, or pain."）、`tracking.control-smoothness`（"Do not infer tremor, tension, or muscle activation."）、`community.adaptive-mouse-grip`、`community.flick-stopping-strategies`、`community.qiluno.reset-as-continuity`、`hypothesis.tension-management`（"Do not say tension was detected."）——立场统一。
- **不得称为反应时间**：`dynamic.click-error-and-acquisition`、`dynamic.speed-matching-and-reading`、`tracking.predictable-speed-matching`、`tracking.reactive-change-response` 四处一致（"Do not call this reaction time." / "Do not call phase lag reaction time." / "Do not call this slow reflexes."）。
- **预测/领先需要可预测性证据**：`dynamic.speed-matching-and-reading` 的 stop_adjust_rule 与 `community.linear-clicking-strategy` 的 "leading depends on predictability" 一致；`tracking.predictable-speed-matching` MECH[1]（"being ready for a change does not justify turning early"）与 `tracking.reactive-change-response` SCOPE（"When the target has not changed, an early turn is not evidence of a successful change response."）同向。
- **shared metric 的解读**：`metric:sparc` 挂在 3 条（static / control-smoothness / tension-management）、`metric:correction_burden` 挂 2 条、v7 后 `metric:decel_frac`/`path_efficiency`/`reverse_ratio` 挂 2 条——各条的解读均兼容（静态=输入侧观察、control=带 error/coverage 护栏的轨迹属性、tension=假设层），无一组断言相反。
- **expected_direction 对共享指标**：`metric:tracking_error`/`time_in_radius` 同时挂在 `tracking.control-smoothness`（comparison_only）与 `tracking.predictable-speed-matching`（target_band）——见表面矛盾澄清 S1，语义相容。

类 1 相关的一条口径张力归入设计张力 T1（见 §4）。

### 类 2：条目内部矛盾 —— 2 条

**M3【低】`hypothesis.tension-management`：stop_adjust_rule 引用本条不具备的证据类型**
- 涉及：`hypothesis.tension-management.stop_adjust_rule[0]`（section_ref `hypothesis.tension-management.stop_adjust_rule`）
- 原文："Stop the experiment for discomfort or when matched and near-transfer evidence do not support it."
- 矛盾点：该 entry 的 supported_uses 止于 `candidate_experiment`，schema 在该层级**禁止** `near_transfer_retest` 字段（`schema.v3.json` entry.allOf 第 2 分支），entry 也确实没有该字段；停止规则却要求用"near-transfer evidence"做判断——引用了本条自身不定义、不承诺获取的证据。对照组：`static.flicking-terminal-control` 同样提到 near transfer，但它有 `near_transfer_retest` 字段支撑（scenario_prescription 层级），自洽。
- 修复建议：把 stop 规则改为仅依赖 matched retest（"…when matched-retest evidence does not support it"），或为该 entry 升级 capability 层级并补 near_transfer_retest。

**M5【低】`static.flicking-terminal-control`：新增 metric:path_efficiency 超出自身定义范围**
- 涉及：`static.flicking-terminal-control.definition` + `.metric_refs`（v7 新增项）
- 原文：definition（section_ref `static.flicking-terminal-control.definition`）——"Input-derived terminal control describes **settling and corrections after** a static flick; it is not target-relative error without a reliable target track."；observation_refs 亦只有 `event.flick` / `metric.terminal_control`。
- 矛盾点：`path_efficiency` 是**整段移动**的路径几何（`metric_definitions.py`："直线位移与实际路径长度的比值"），含主移动阶段，不是"flick 之后的 settling and corrections"。migration audit 的理由 "Terminal control is deceleration control" 只能覆盖 `decel_frac`，覆盖不了 `path_efficiency`。检索面（metric_refs）大于定义面（definition/observation_refs）。
- 修复建议：要么收窄 metric_refs（去掉 path_efficiency），要么在 definition/alternative_explanations 中显式说明 path geometry 作为 terminal-control 的间接证据及其边界。

### 类 3：信号与别名层矛盾 —— 1 条真矛盾 + 2 条澄清

**M2【中】悬空 alias：`'throughput low' → 'throughput below reference'`，目标信号自 v2 起无任何 entry 声明，而管线仍在发射它**
- 涉及：`registry.v7.json.signal_aliases` + 全部 27 条 entry 的 signals + `kovaak_tracker/advice.py`
- 证据链：
  1. v1 registry 曾声明信号 `throughput below reference`；v2（2026-07-22）收缩信号面时删除了该 entry，但 alias 被原样保留，v2→v7 六个版本未清（程序化 lineage 确认）。
  2. v7 中无任何 entry 的 `signals` 包含 `throughput below reference`；alias 的规范目标落空。
  3. `advice.py` 至今仍发射该信号（`advice.py:299` `Finding("throughput below reference", "fix", …)`；`_SIGNAL_METRICS`、`_PLAIN_MEANINGS`、`coach/profiles.py` archetype 条件同用此名）。
  4. `coach/agent_tools.py:make_fetch_knowledge` 按信号查询：传入管线真实发射的 `"throughput below reference"` 时，alias 表按 key 查不到（它只是 value），落回原串后无 entry 命中 → 返回 `unknown signal`。alias 唯一能生效的输入 `"throughput low"` 无人发射。
- 为什么矛盾：alias 层声称存在一个规范信号，知识层却不服务它；管线侧真实信号又双向都对不上。这是"检索能命中"的假象面。
- 严重度：中（当前无崩溃路径——`diagnosis._build_issues` 只对 3 个静态信号解析知识，throughput finding 静默拿不到知识引用；但任何按信号取知识的调用方都会空手而归）。
- 修复建议：删除该 alias，或为 `throughput below reference` 恢复/新增一条 comparison_only 的 outcome-level entry（吞吐是 Fitts 归一化的速度-精度综合量，有管线事实支撑）。

澄清（详见 §3）：S2 `decel jitter → sparc low` 的归并是产品自己的指标语义（`flicking.py:91` 明文 "sparc … the correct proxy for 'decel jitter / tense release' (§6.1)"），非错误归并；S4 `accel mismatch high` 一签两用（legacy 未校准 finding 与 comparison candidate 各挂不同指标）——两路都路由到 `tracking.reactive-change-response`，不构成知识矛盾。

### 类 4：claim 层矛盾 —— 未发现

- 同一 `source_ref` 不同 `source_level`：结构性不可能（source_ref 全局唯一，`_normalize_source_v2` 强制）。
- claim_level 超出 source ceiling：validator 强制（`_SOURCE_CLAIM_CEILING`），v7 全部通过。
- research_supported 条目与社区条目断言冲突：逐条读了引用 `research.manual-intermittent-control`、`research.sparc-smoothness`、`research.challenge-point`、`research.mouse-shape-ergonomics`、`research.cursor-latency-tracking` 的条目，措辞均在来源边界内（例：mouse-fit 的 mechanisms 明说 "the study does not establish an individual fit outcome for an aim player"），无越界断言，与社区条目无内容互斥。
- scenario_prescription 五条全部 `experimental`，其中 `switching.transition-and-arrival` 只引 product_contract 双源、另四条多引 `research.scenario-prescription`（experimental，applicability 仅 static/dynamic/predictable，故 switching 不能引）——保守且机械一致。

### 类 5：范围层矛盾 —— 未发现真矛盾

- family 枚举（7 值）与 PRD/ARCHITECTURE 对齐：PRD v1 四家族中 continuous tracking 在知识层细分为 predictable/reactive/control；`movement_aiming` 仅由 `movement.outcome-only-boundary`（outcome_only）使用，且 schema 强制 outcome_only 条目 family_scope == {movement_aiming}——与 ARCHITECTURE "没有玩家移动遥测的 movement aiming 保持 outcome-only" 一致。
- topics 与 family_scope：validator 强制 entry family ⊆ source applicability，46 个 source 的 applicability 全部覆盖其支撑条目。
- 跨家族条目（friction/sensitivity/grip/score-farm/transfer/accel/practice-intent/difficulty/reset/tension）覆盖 6 家族一致；两条 hypothesis 条目的家族收缩由 research source 的 applicability 驱动（见 S5），非任意。
- scenario_prescription 引用的 4 个 scenario profile 全部在 `active_scenario_profile_refs()` 中（且 loader 加载时强制）。

### 类 6：接口层矛盾 —— 3 条真矛盾（含最重要的两条）

**M1【中】`tracking.control-smoothness` 的 v7 新增 metric_refs 与其 quality_prerequisites / definition 在任一证据层级下都不可同时满足**
- 涉及：`tracking.control-smoothness.metric_refs`（v7 新增 `metric:decel_frac`、`metric:path_efficiency`、`metric:reverse_ratio`）、`.quality_prerequisites`、`.definition`；`migrations/2026-08-15-v6-to-v7-audit.json`；`webapp/backend/worker.py` 分发逻辑。
- 原文摘录：
  - quality_prerequisites：`visual_profile_accepted, canonical_time_aligned, sparc_version_fixed, matched_filter_window`（section_ref `tracking.control-smoothness.scope` 同构约束）；
  - definition："Control tracking evaluates smoothness and correction burden **jointly with error and coverage**…"；
  - migration audit 理由："…Coach can now retrieve this entry when a baseline tracking analysis has **no visual-pipeline metrics** to query on."
- 矛盾点（三层交叉验证）：
  1. 新增的三个指标是 input-native flick 分析器（`native_flicking_analysis.py`）的产物；而本条要求的 `visual_profile_accepted` 只在 multimodal 层成立——**input_native 层拿不到视觉 gate，multimodal 层的 continuous_tracking 分析器（`tracking_analysis.py`）又不产这三个指标**（它产 `continuous_tracking.*` 键）。
  2. 当前分发合同里 continuous_tracking 家族**只有 multimodal 分析器**（`worker.py:1366-1372` 要求 `input_mode == "multimodal"`）；unlisted baseline 分支只覆盖 static/dynamic（`worker.py:1393-1399`）。即"没有视觉管线指标可查的 baseline tracking 分析"这一 v7 声明的目标场景，现在的管线根本产生不了。
  3. 定义要求 error/coverage 联合评估，input-native 指标不含这两者。
  → 检索面（新 metric_refs）、使用前提（quality_prerequisites）、定义（definition）三者互相打架，且 migration 理由描述的输入当前不存在。属于前瞻接线，但接线时没有同步任何前置条件。
- 修复建议：三选一—— 给 continuous_tracking 建 input-native baseline 分析器并放宽该条 prerequisites（区分 input-only 与 visual 两档）； 收回这三个 refs 到 static 条目； 保留 refs 但在 scope/limitations 中显式声明"input-native 检索仅支持解释、不支持 diagnosis_support"。

**M4【中】`metric:settle_time_ms` 命名漂移 + 与 target_switching 的同名概念碰撞**
- 涉及：`static.flicking-terminal-control.metric_refs`（`metric:settle_time_ms`）vs 管线静态键 `settle_duration_ms`（`metric_definitions.py` 双形态 `static_clicking.settle_duration_ms` / 裸 `settle_duration_ms`）vs 代码中 `settle_time_ms`（`target_switching_analysis.py:516` 等，是**切换 episode 的捕获确认时刻**字段）。
- 矛盾点：registry 用 `settle_time_ms` 指静态 flick 的"移动结束后时长"，但管线里这个名字属于 target_switching 语义；静态的真实键名是 `settle_duration_ms`。同 token 双语义 + 静态侧检索键落空（没有任何调用方会传 `metric:settle_time_ms`，管线也永不发射它）。switching 条目自己声明的是 `metric:target_switching.settle_duration_ms`，进一步坐实命名空间混乱。
- 修复建议：改为 `metric:settle_duration_ms`（或 `metric:static_clicking.settle_duration_ms`），并在 knowledge contract 中冻结 metric token 命名规则。

**M7【低】其余 metric token 命名漂移/无管线对应物（M4 同类，清单化）**
- `metric:click_pacing`、`metric:post_change_stability`：全仓 0 命中，纯 registry 抽象 token，无发射方、无查询方。
- `metric:target_state_outcome`：管线键是 `dynamic_clicking.target_state_accuracy`（`metric_definitions.py`）。
- `metric:selection_error`：管线/测试键是 `target_switching.selection_error_ratio`（`tests/coach/test_advice_target_switching.py:66`）。
- 查询形态不一致（当前被 signal 匹配掩盖）：`advice_dynamic_clicking.py:108` 传 `metric_refs=["dynamic_clicking.normalized_click_error"]`（带家族前缀、无 `metric:` 前缀）vs registry 的 `metric:normalized_click_error`——交集恒空，靠 signal +16 兜底；`diagnosis.py:177` 传静态裸名（`"sparc"`/`"reverse_ratio"`/`"submovement_overlap"`）vs registry 的 `metric:sparc`——同样恒空。任何一条 entry 若未来删掉 signals 只留 metric_refs，这些查询会静默失效。
- 修复建议：建立 metric token ↔ 管线 key 的单一映射表（或统一前缀约定），至少让两类查询调用方传 `metric:` 前缀形态。

**M6【低】v7 内容变更未 bump entry_version（@2 → @2）**
- 涉及：`static.flicking-terminal-control`、`tracking.control-smoothness`（v6=v7 均 entry_version 2，metric_refs 实际变化）；对照 v5→v6 的纪律——`hypothesis.tension-management` 内容变更时 2→3。
- 矛盾点：版本号语义（同 id+version = 同内容）被 v7 打破；migration audit 以 `@2 → @2` 自我记录了这个破坏。动机可推断：`advice_tracking.py:296` 等硬编码 `knowledge:tracking.control-smoothness@2`，bump 会破坏既有引用——但这让 entry_ref 失去内容寻址意义。
- 修复建议：要么恢复"内容变即 bump"并迁移硬编码引用，要么在 schema/文档中明文定义 entry_version 的兼容语义（何种变更可不 bump）。

---

## 3. 表面矛盾澄清列表（措辞不同，语义相容）

- **S1** `metric:tracking_error`/`time_in_radius` 挂在 comparison_only（control-smoothness）与 target_band（predictable）两条下：`expected_direction` 是 entry 级语义（该条**核心观测**的方向），不是 per-metric 标注；predictable 条的 target_band 指 gain/phase 描述子，其 scope 明文 "use descriptive error and coverage when this condition is absent"，error/coverage 在两条下都退化为描述/比较——不冲突。
- **S2** alias `decel jitter → sparc low`："减速抖动"并入"平滑度低"看似错并（registry 另有 `reverse_ratio high` 表反向修正），但 `flicking.py:88-91` 明文规定 sparc 是 "decel jitter / tense release" 的指定代理指标——产品自己的指标语义，归并正确。
- **S3** `static.flicking-terminal-control` cue（"arrive under control, then let the click follow the settled aim point"）vs `community.qiluno.confirmation-timing-schools`（两派并存、"Do not present either strategy as the only correct technique"）：前者是 candidate_experiment 层级的单变量实验提示（有 dose/matched_retest/stop 护栏），后者是 explanation_only 的描述性知识；一个说"可以试这个 cue"，一个说"没有唯一正确流派"——可共存（口径协调问题归 T1）。
- **S4** `accel mismatch high` 一签两用：legacy 未校准 finding（指标 `accel_mismatch`，`advice_tracking.py:253` 附近）与 comparison candidate（指标 `continuous_tracking.observed_change_response_ms` → `metric:change_response`）共用信号名。两路最终都路由到 `tracking.reactive-change-response`，知识层无矛盾；指标语义不同属管线侧已知分层。
- **S5** `hypothesis.mouse-fit-differential-intake` 仅挂 static+dynamic、`hypothesis.input-latency-differential-intake` 仅挂 predictable+reactive（不含 control_tracking）：由各自 research source 的 applicability 硬约束（validator 强制 entry family ⊆ source applicability），来源驱动而非任意收缩。
- **S6** `coach_first_party` 同时标注 Raw Input 文章（immie/Keeah/MattyOW 等外部作者）与 qiluno/viscose 视频：语义是"教练本人第一方发表的内容"（vs `community_organization` 的组织发布），使用一致；且两者 claim ceiling 同为 community_practice，不会造成 claim 通胀。
- **S7** `movement.outcome-only-boundary` 的 quality_prerequisites 是 `movement_telemetry_unavailable`（以"缺失"为前提）：与其他条目的能力型前提写法相反，但对 outcome-only 边界条目语义自洽（它是 fail-closed gate，非能力声明）。

## 4. 设计张力（两者有理，需产品决策）

- **T1 静态确认时机的教练口径**：`static.flicking-terminal-control`（deterministic 骨架 + 社区 cue"settle 后再点击"）与 `community.qiluno.confirmation-timing-schools`（"click during deceleration"也是合法流派）同属 static_clicking、共享 topics（confirmation/terminal_control/click_rhythm 邻域），按当前打分（topic +4）可能同批召回。Coach 同时拿到两条时，话术优先级未定义——是"按条目 cue 走"还是"先呈现两派"？建议在 static 条目 cue 中加一句指向替代流派，或由 diagnosis/agent 层规定召回排序口径。
- **T2 v7 前瞻接线无消费方**：新增的 5 个 metric_refs 在 feat 分支上没有任何查询调用方（`fetch_knowledge` 只收 signal；`agent_kb` 是 v1 兼容投影；`query_registry` 的 metric 分数无生产者触发），测试也只 bump 了版本号断言、未新增 v7 检索测试。前瞻设计 vs 死检索面——若这是为后续 baseline 消费方铺路，建议在 migration audit 中标注消费方 issue，或补一条"metric 检索可召回"的合同测试（传 `metric:decel_frac` 断言召回 static 条目），防止下次收缩时静默丢失。

## 5. 总体结论

知识体系的**内容一致性水平高**：27 条 entry 在身体原因、反应时间、预测证据、单一正确技术、迁移证据等所有敏感断言上口径统一，claim 分级与 source ceiling 机械可靠，能力阶梯（supported_uses ↔ 处方字段）无违规，家族范围与 PRD/ARCHITECTURE 合同对齐。v6→v7 的变更本身极小且方向（input-native 检索面）符合 ARCHITECTURE 的 baseline 合同。

矛盾不在"知识打架"，而在**知识层与管线层的接口失配**：3 条中严重度问题全部是 token 层的（悬空 alias、命名碰撞、前提与检索面不可同满足），共同根因是 metric/signal token 没有单一事实源与命名合同，registry 演进（v2 信号收缩、v7 前瞻接线）留下了未被测试覆盖的缝。若不处理，风险形态是"静默失效"（检索召回不到、fetch_knowledge 返回 unknown signal、知识引用空挂）而非错误答案——对 Coach 的直接伤害是解释覆盖面缩水，不是误导。

建议优先级：M1、M2（中，影响检索可用性）→ M4+M7（建立 metric token 映射合同，一次解决一类）→ M3、M5、M6（文本/元数据修正，顺手）→ T1、T2（产品口径决策）。

---

*审核人：知识体系审核 subagent（只读）。核对材料均为 2026-08-16 仓库状态：registry v7 取自 feat 分支，代码与文档取自 main 工作树（未做任何写操作）。*
