# x76 wiki 知识库评审报告（2026-08-16）

评估对象：`x76-wiki-markdown-2026-08-16/`（x76.gg wiki 导出快照）。
对照基线：registry v7（27 条目，仅存在于 `feat/capture-generalization-knowledge-2026-08-15` 分支，经 `git show` 读取，未触碰工作树）。
本报告为只读评审产物，所有建议供决策，不构成待办合同。抓取快照的 robots 声明为 `use=reference, ai-train=no`，入库时应转述并引用来源，不逐字复制正文。

## 1. Wiki 总览

- 101 篇文章（另含 INDEX.md 与 CRAWL_NOTES.md；抓取时 2 页 404：`practice-improvement`、`half-sideways-strafing`）。
- 分簇统计（按抓取 category + 内容语义）：

| 簇 | 篇数 | 体量 | 内容 |
| --- | --- | --- | --- |
| Glossary 概念/理论 | 86 | 约 9.3K 词 | 术语定义，每篇 50–150 词，双句式（定义 + 概述） |
| Strafe Guide 训练/移动 | 8（含 4 个语言变体） | 约 17.5K 词 | 四篇实质指南：Strafe Aim、Dodge、Angles、Geometric Positioning |
| Settings 特定游戏设置 | 5 | 约 2.4K 词 | BO4/GMod/NS2/Quake Live/CS2 皮肤，逐游戏配置 |
| Meta / Getting started | 2 | 约 0.4K 词 | 服务器规则、欢迎页 |

实质知识密度集中在四篇 Strafe Guide；Glossary 的价值在术语对齐与少量社区共识碎片（bardpill、hit confirming、acc-based scoring、edge tracking、tensing）。

指南级外部致谢：Shwill（视角/速度角解耦）、Sam（Fundamentals of Strafe）、Aimer7（几何站位启发式）、bardOZ（bardpill 训练法）。

## 2. 对照结论摘要

registry v7 的 27 条中 15 条为 community.* 家族条目，已覆盖：敏感度/外设/握姿/加速/迁移/刷分语境/练习意图/难度控制/效率框架/确认时机流派/重置连续性等。wiki 的概念主体（aim 分类、tracking/clicking/switching 家族定义、smoothness/speed/precision）与我们现有条目语义重合度高，多数为"术语换名"而非新知识。

真正缺口集中在四类：

1. **具名训练方法**（bardpill）——registry 有难度控制框架但没有这个具体方法；
2. **switching 家族内部子类结构**（speed vs evasive）——v7 的 target_switching 无子类区分，处方无法靶向；
3. **"读 vs 执行"能力分解 + 训练器边界**——直接约束 reactive/dynamic 家族的诊断推理；
4. **计分制语义**（acc-based scoring）——score/accuracy 联合解读的社区共识，baseline Stats 即可用。

另有一批"fold-in 级"碎片适合以小版本升级并入现有条目，以及明确应跳过的部分。

## 3. Gap 清单与建议骨架（按价值排序）

### Tier 1：直接强化诊断/处方，baseline（Raw+Stats+Performance）数据即可支撑

#### 3.1 `community.bardpill-accuracy-anchored-progression`（建议新增）

- 源：articles/bardpill.md（具名方法，源自玩家 bardOZ）
- 内容一句话：以准度为锚、速度为唯一旋钮的训练法——先慢速打干净，只在命中率保持时逐步提速；"把速度建立在控制上"的具体例程。
- topics：`static_clicking, accuracy_anchored_progression, speed_control`；family_scope：`static_clicking`（wiki 明确为 static 训练法）
- category：training_cue；claim_level：`community_practice`（community_organization 源天花板）
- 数据前提：无额外要求——acc 序列 + 得分/节奏均属 Stats/Performance，matched retest 机制现成
- supported_uses：`explanation_only, candidate_experiment, scenario_prescription`
- 备注：与 `community.difficulty-refinement-and-stress-test`（框架级）互补；因处方形态具体（可操作旋钮唯一），建议独立成条而非并入

#### 3.2 `community.speed-vs-evasive-switching`（建议新增）

- 源：articles/speed-switching.md、articles/switching.md
- 内容一句话：switching 分两个子类——speed switching（低 TTK 快速连杀，挑战转换速率而非持续跟踪）与 evasive switching（高血量乱走目标，更像 target_switching×tracking 混合）；子类决定该练什么场景。
- topics：`target_switching, switching_subtype, transition_rate`；family_scope：`target_switching`
- category：mechanism；claim_level：`community_consensus` 候选、稳妥取 `community_practice`
- 数据前提：transition/settle 指标（`metric:target_switching.transition_time_ms`、`metric:target_switching.settle_duration_ms` 等 v7 已有）+ outcome 即可区分子类表现
- 价值：处方靶向——"转换慢但到点准"和"转换快但落点差"对应不同场景变体；也补足 TTK→家族归类逻辑（低 TTK 奖励 clicking/switching，高 TTK 奖励 tracking）
- supported_uses：`explanation_only, diagnosis_support, scenario_prescription`

#### 3.3 `community.reading-vs-execution-decomposition`（建议新增，或扩展 `community.aim-trainer-transfer`）

- 源：articles/dodge-guide.md §2.2–2.4（"Reading Solves Half Your Aim"、"Improving Reading"）
- 内容一句话：tracking/瞄准 = 读（知道鼠标往哪动）+ 执行（动到位）两半；训练器 bot 不会针对你反应/适应，只能练执行——"读"无法从 trainer 数据中分离，强 trainer 玩家实战崩坏常因此。
- topics：`movement_reading, execution_split, trainer_boundary`；family_scope：`reactive_tracking, dynamic_clicking`（推理限制最相关）
- category：limitation；claim_level：`community_practice`
- 数据前提：无需新指标；价值恰在限制推理——reactive 家族 post-change 差不得直接归因鼠标控制
- 价值：诊断护栏（与现有 forbidden_inferences 机制同构）+ 强化 transfer 条目的社区表述
- 备注：与 `dynamic.speed-matching-and-reading`（topics 已含 movement_reading）有语义交叠，入库时二选一定位：扩旧条目 or 新限制条目

#### 3.4 `community.accuracy-multiplied-scoring`（建议新增）

- 源：articles/acc-based-scoring.md
- 内容一句话：score = 命中数 × 准确率的计分制下，体积分高但准度崩的 run 输给慢而干净的 run；最优节奏是把准度压在其上限附近。
- topics：`scoring_semantics, score_farming, pacing`；family_scope：全家族（计分制跨家族）
- category：mechanism；claim_level：`community_practice`（公式本身可验证，但按源天花板落此级）
- 数据前提：score + accuracy 均属 Stats——baseline 可直接检测"score up accuracy down"形态
- signals 建议：`score up accuracy down`（与 signal_aliases 风格一致，可被 query_registry 命中）
- 价值：score-farming 语境的具体检测语义；为 `community.score-farming-context` 提供可观测信号

### Tier 2：有价值，但依赖用户口述或仅作解释层

#### 3.5 `community.edge-tracking-underaim`（建议新增）

- 源：articles/edge-tracking.md
- 内容一句话：在来回反向的目标上刻意贴 hitbox 后缘（underaiming），反向瞬间所需修正更小、重获更快——是策略而非纯错误。
- topics：`trailing_edge, deliberate_underaim, reacquisition`；family_scope：`predictable_tracking, reactive_tracking`
- category：limitation（作为 alternative explanation）；claim_level：`community_practice`
- 数据前提：**需要目标相对几何（贴缘位置），baseline 无**——必须挂 quality_prerequisites 限制，仅支持 explanation_only + 用户口述触发；禁止把"落后于目标"自动当缺陷
- 价值：为 tracking lag/trailing 类信号补充反例，防止过度诊断

#### 3.6 `community.overshoot-sensitivity-trigger`（建议新增，或并入 `community.task-specific-sensitivity`）

- 源：articles/over-flicking.md
- 内容一句话：持续 overshoot 且灵敏度长期未变 → "sens 可能高到失控"的社区经验触发条件，进入降 sens 单变量实验。
- topics：`sensitivity, overspeed_flick, settings_experiment`；family_scope：`static_clicking`
- category：mechanism；claim_level：`community_practice`
- 数据前提：**overshoot 本身需视觉判定，input-only 不可标**（与现有 "Do not label input-only corrections as visual overshoot" 禁令一致）——只能由用户口述触发；实验验证用 matched retest 的 Stats 即可
- 价值：给敏感度实验条目补一个高频触发条件；顺带吸收 under-flicking 的对偶（"不够投入"→ commit 更足的 cue）

#### 3.7 `community.vrt-response-floor`（建议新增）

- 源：articles/vrt.md、articles/reactivity.md
- 内容一句话：视觉反应时间是 reactivity 的硬下限，change response/reacquisition 不可能无限快；解读 reactive 家族指标时应预留人类 VRT 量级的生理下限。
- topics：`reactivity, response_floor`；family_scope：`reactive_tracking`
- category：limitation；claim_level：`community_practice`（glossary 级；若入库时挂我们已有的学术源可另行升级）
- 数据前提：无需新指标；属期待值管理
- 价值：防止把接近生理下限的 reacquisition_time 当可训练缺口

### Tier 3：机制/归一化价值，暂缓或低优先

#### 3.8 `community.target-angular-demand-math`（建议暂缓入库，先留参考）

- 源：articles/angles-guide.md §7（数学推导完整）
- 内容一句话：目标运动按视角/速度角分解为横向（要跟踪）与径向（免费）分量：`v_lateral = v·cosθ`，角速度 `ω = v·cosθ/r`，需求手速 `= cm/360 / 360° × ω`——"角度不减速，只改变投影"。
- 价值：跨场景/跨灵敏度的 tracking 需求归一化理论（同目标不同 cm/360 的需求手速可直接换算）；诊断应用需目标几何（baseline 无），但手速-灵敏度换算只依赖输入+设置
- claim_level：数学为确定性几何，但经 community_organization 源入库只能落 `community_practice`；若走独立推导的产品合同路径可另议
- 数据前提：`quality_prerequisites: input_kinematics_available + settings(cm/360)`；目标角速度部分标 limitation

#### 3.9 `community.strafe-relative-speed-ladder`（建议暂缓，movement_aiming outcome-only 阶段价值有限）

- 源：articles/strafe-aim-guide.md §4–6
- 内容一句话：strafe 形式相对速度阶梯——mirroring 0.00v / HSW mirroring 0.29v / HSW anti-mirroring 1.71v / anti-mirroring 2.00v；形式可自由切换（movement-aim independence）。
- 价值：若未来支持 strafe/移动场景或用户问"为什么这个 strafe 场景更难"，这是现成的难度阶梯；当前 movement_aiming 为 outcome-only，只能作解释层
- 数据前提：movement 遥测不可用——挂现有 outcome-only 边界

### 3.10 fold-in 碎片（不新开条目，作现有条目 entry_version+1 小升级）

| 碎片 | 源 | 并入条目 |
| --- | --- | --- |
| 短促发力本身是瞄准的一部分（快甩需要瞬时张力），技能是"该紧则紧、余时放松" | tensing.md | hypothesis.tension-management |
| hit confirming（点击前短暂聚焦确认锁定）作为一个具名流派 | hit-confirming.md | community.qiluno.confirmation-timing-schools |
| 重置的条件化：低 cm/360 才高频重置、在自然停顿处做、低敏贴边是真实风险 | mouse-resetting.md | community.qiluno.reset-as-continuity |
| chasing / trailing / leading 术语映射（用户口述词 ↔ lagging correction / phase lag / 提前量） | chasing.md、trailing.md、leading.md | dynamic.speed-matching-and-reading、tracking.predictable-speed-matching |
| 握姿三分类谱（palm=稳 ↔ fingertip=活，claw 居中；按手型/鼠形/sens 适配） | mouse-grip.md、claw/palm/fingertip-grip.md | community.adaptive-mouse-grip |
| timescale 调难度（放慢学模式、提速压反应）+ 靠 timescale 刷分属 cheesing | timescale.md、cheesing.md | community.difficulty-refinement-and-stress-test、community.score-farming-context |
| bot 参数（尺寸/血量/数量/闪避模式）决定场景练什么 | bot.md、scenario.md | 各 family 条目 scenario_prescription 解释素材 |
| "LG duel 是最好的 tracking 训练之一" | ql-startup.md | community.aim-trainer-transfer |

## 4. 跳过清单及理由

| 跳过内容 | 篇数/范围 | 理由 |
| --- | --- | --- |
| 特定游戏设置指南 | bo4-guide、gmod-config、ns2-settings-guide、ql-startup（主体）、skz-skins-guide | 逐游戏配置与皮肤操作，无泛化训练知识 |
| PvP 对抗策略主体 | dodge-guide 大部分（读意图、conditioning、假动作、control、位置图书馆）、geometric-positioning-guide 主体 | PRD 边界：我们分析 trainer 数据，无对手/站位/血量数据源；结论无法落地为诊断或处方 |
| 设置-风格相关性（激进=高 sens+高 FOV 等） | dodge-guide §6.1 | 相关性陈述且依赖对局风格数据，无数据源 |
| 游戏机制词汇 | hitscan、projectile、leading、travel、arc、trajectory、recoil、spread、rof、ttk、utility、crosshair、reticle、fov、verticality、third-person-shooter、counter-strafe、gamesense、map、mechanical-skill、crosshair-placement、target-acquisition | 教育性 glossary；与 7 大家族的运动学诊断无数据接口（TTK→家族逻辑已以 fold-in 吸收进 3.2） |
| CPS | cps.md | 反指标定位（"准确有节奏的点击胜过频率"），一句话 fold-in 价值，不单独入库 |
| blink（Overwatch 瞬移目标） | blink.md | 单一游戏机制；reacquisition 概念已被 reactive 条目覆盖 |
| 语言变体 | strafe-aim-guide 的 fr/ja/ko/zh 四篇 | EN 版已覆盖，无独立增量 |
| 社区治理与元页面 | meta--server-rules、getting-started--welcome | 服务器规则/欢迎页，非训练知识 |
| coach、vod 词条 | coach.md、vod.md | 属我们产品自身域（Coach 视频讲解），不是待入库的外部知识 |
| 404 缺失页 | practice-improvement、half-sideways-strafing | 抓取失败；dodge-guide 引用的 practice-improvement 指南缺失，引用链断裂需知悉 |

与现有 27 条重复而不列 gap 的：sensitivity/cm-360/dpi/edpi/acceleration（已被 task-specific-sensitivity、mouse-acceleration-context 覆盖）、aim-types/aim/aim-theory/mouse-control/pathing/speed/smoothness/precision/accuracy（家族定义与效率框架已覆盖）、deliberate-practice/cheesing（practice-intent、score-farming 已覆盖）、flicking/micro-correction（flick-stopping-strategies、flicking-terminal-control 已覆盖）、strafing/strafe-aiming/mirroring/anti-mirroring/movement-aim-independence/strafe-scenarios（movement 家族边界已定义，理论层见 3.9）。

## 5. 入库实施提示（面向知识库文件化改造）

1. **源注册**：x76 wiki 整体注册为单一 `community_organization` 源（source_ref 建议 `community.x76-wiki`），retrieved_at 2026-08-15，locator 用 wiki URL + INDEX.md 的 SHA-256；claim 天花板即被 schema 的 `_SOURCE_CLAIM_CEILING` 压到 community_practice——除我们另行挂学术源外，本批建议全部按 community_practice 起步，宁低勿高。
2. **文件化适配**：Tier 1 四条（3.1–3.4）与 Tier 2 三条（3.5–3.7）适合做成独立知识文件（每条一个完整 claim 单元，含 cue/dose_guardrail/matched_retest 骨架，信号挂 Stats/Performance 可观测项）；Tier 3 两条更适合 mechanism 型参考文件，等目标几何或 strafe 场景支持后再升级 supported_uses。
3. **检索对齐**：query_registry 按 signal(+16)/metric(+8)/topic(+4)/use(+2) 计分——新条目务必挂 v7 已有 metric_refs（如 `metric:target_switching.transition_time_ms`）与家族 topics，否则不会被召回；`score up accuracy down` 这类信号建议同步登记 signal_aliases。
4. **fold-in 走小版本**：第 3.10 节碎片按现有条目 entry_version+1 升级，不产生新文件，避免 registry 膨胀。
5. **引用纪律**：robots 声明 `ai-train=no, use=reference`——所有入库文本转述改写，不得逐字搬运；来源块记 URL + SHA-256 即可追溯。
6. **实施顺序建议**：3.1→3.4→3.2→3.3（处方价值密度排序），Tier 2 在数据前提标注机制就绪后跟进。

## 6. 评审过程说明

- registry v7 经 `git show feat/capture-generalization-knowledge-2026-08-15:knowledge/coach/registry.v7.json` 读取，schema 按 `knowledge/coach/schema.v3.json`（main 上已有），消费逻辑参考 `kovaak_tracker/coach/knowledge_registry.py`（v3 校验 + query_registry 计分召回）。
- 全部 101 篇文章已通读（总量约 29.5K 词；四篇实质指南全文精读，glossary 全量过目）。
- 未修改任何产品代码、registry、文档体系与 git 工作树；本报告为仓库根新增未跟踪文件。
