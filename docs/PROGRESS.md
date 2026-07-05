# Flicking 模块进度

> 最后更新：2026-06-29

## 2026-06-28：方法论修正 + 分析-对比-建议流程固化

用高手参考视频（`high-level-1w6ts.mp4`，80 cm/360，无 CSV）对比后，修正了真实数据上的两个方法论问题，并把整条"分析→对比→建议"流程固化成正式模块。

### 方法论修正（真实数据验证）

| 弃用 | 原因 | 替代 |
|---|---|---|
| 减速段加速度 std 跨人对比 | 与 peak_speed 强相关（corr=0.76），对高速玩家不公平 | 减速段线性度 `linearity`（归一化 RMSE，无量纲）|
| 静止间隙切分（`extract_flicks`）| 高手连续甩、无静止间隙 → 合并成长段，flick 数少 2/3 | 速度谷切分 `segment_by_valleys` |

新增维度：`decel_frac`（减速段占比）、路径几何（`path_efficiency` / `path_length_deg` / `direction`）。角度量用 `deg = px * FOV / width` 跨分辨率可比；cm/s 仅作自参考（被 sens 主导，不跨人比）。

### 固化的模块

| 模块 | 能力 |
|---|---|
| `docs/aim-kinematics-research.md` | 知识底座：min-jerk 黄金标准 + Becker 2020 + Voltaic/社区处方 + 诊断→处方表 |
| `flicking.py` +`segment_by_valleys` / `compute_fair_metrics` / `FlickFairMetrics` | 谷切分 + 公平指标 + 路径几何 |
| `pan_tracker.py` +`analyze_flicking_reference` / `ReferenceAnalysis` | 无 CSV 参考模式（手动窗口修 HUD 误判）|
| `advice.py`（新）| 规则引擎 `advise`（诊断 + 处方）+ `compare_table`（你 vs 参考）|

### 端到端验证（你 48 cm/360 vs 高手 80 cm/360）

| 指标 | 你 | 高手 | verdict |
|---|---|---|---|
| linearity | 0.171 | 0.099 | worse |
| reverse_ratio | 0.232 | 0.167 | worse |
| decel_frac | 0.750 | 0.455 | worse |
| path_efficiency | 0.967 | 0.928 | same |
| peak_speed °/s | 106 | 125 | worse |

`advise` 产出 4 findings：decel_frac / linearity / reverse（fix）+ peak_position（watch）。

### 已知缺口 / 下一步

**下个 session 接续点 —— 推荐顺序 A → B → C：**

1. **[A] 统一 summary 入口**：`analyze_flicking_video`（有 CSV）目前仍输出旧 summary（`decel_smoothness`）；自己的有 CSV 视频要用 `analyze_flicking_reference`（手动窗口）才拿到公平 summary。让有 CSV 入口也输出公平 summary（复用 `segment_by_valleys` + `compute_fair_metrics`），自己的视频就能直接进 `advice.advise` / `compare_table`。**最小改动，解锁完整流程，应最先做。**
2. **[B] dashboard 接入**：上传视频 → `compare_table` + `advise` 的 UI 展示（对比表 + 诊断 + 处方）。
3. **[C] 二期**：`target_selection`（目标选择策略）、`overshoot`、`reaction`——需目标检测，噪声大，单独立项。
4. 手部镜头分析（后续，`product-strategy.md` 已规划）。

**复测基线（下次自测对比用同一管线 + 谷切分）**：decel_frac 0.75→<0.55、linearity 0.171→<0.12、reverse_ratio 0.232→<0.18。

### 2026-06-28 续：flicking 理论深化（三缺口 deep research）

对 flicking 指标做理论审查发现三个缺口，deep research 补强（三条理论全是 Becker 2020 核心引用，谱系一致）。详见 `docs/aim-kinematics-research.md` §6。

| 缺口 | deep research 结论 | 改动 |
|---|---|---|
| 减速段理想曲线 | min-jerk 速度 v(τ)∝30τ²(1-τ)² 是**曲线**；linearity 拟合的"匀减速直线"=恒定负加速度**≠ min-jerk**，归因错误。减速平滑度金标准是 **SPARC**（Balasubramanian 2012，频域、无量纲——正是 decel_smoothness corr=0.76 不公平的正解） | `flicking.py` 新增 `_segment_sparc`+`sparc` 字段（整段速度算）；linearity 归因改 constant-deceleration；advice linearity 诊断从"减速抖动"改"制动不匀"，新增 sparc 规则 |
| 两段式 vs 流体 | 原 `is_two_stage` 被删、靠社区流派词。学术谱系：Woodworth 1899(initial ballistic+corrective)→Meyer 1988(optimized)→Novak 2002(overlapping=流体/discrete=两段式)；切分标准 Rouse 2022 | `flicking.py` 新增 `_submovement_structure`（主峰后固定窗口扫 corrective，独立于 valley 切分）+`corrective_count`/`submovement_overlap` 字段；advice 新增 two_stage 规则 |
| peak_speed 公平性 | peak_speed 没距离归一化（同类公平性问题）。**Fitts TP=ID/MT**，ID=log₂(D/W+1)，跨距离可比 | `flicking.py` 新增 `throughput` 字段（TP=log₂(D/W+1)/MT，W 从 `target_width_deg` 传入）；advice 新增 throughput 规则 |

**验证**（合成 fluid + two-stage 数据）：sparc 整段后数值进文献范围（fluid −4.4 / two-stage −7.3）；two-stage 的 micro 被 valley 切成独立段后，主峰段仍通过向后窗口抓到（corrective=1, overlap=0.10=深谷离散）；advice 产出 6 findings（含新 sparc/two_stage/throughput）；compare_table 12 行新指标 verdict 正确（sparc 负值 higher-better 逻辑成立）。

**保守增量**：现有 linearity/decel_frac/reverse_ratio 数值与复测基线**不变**（新指标是新增维度 + 文档归因修正），不破坏既有流程。

**实现教训（goal-driven 验证抓到两个缺陷）**：
1. 初版 `_submovement_structure` 在 valley 单段内找次峰——逻辑死局（任何它该抓的 corrective 早被 valley 切分阈值 0.15 拆走，单段内只剩单峰）。改为主峰后固定窗口扫，独立于切分。
2. 初版 sparc 只喂减速段（半钟形）——频谱破碎、数值虚高（−21~−29）。改为整段钟形，落进文献范围。

**已知后续**：
- throughput 当前 reference 模式（无 CSV）不传 W → NaN。完整接线需 `detect_targets` 目标宽度 → `compute_fair_metrics(target_width_deg=...)`（属 [C] 目标检测方向）。
- `sparc_low` / `two_stage_overlap` 阈值为理论初始值，需真实数据校准（同 linearity 当初）。
- effective-target-width 版 throughput（We=4.133·SDx）需多次击中同类目标的端点分布，后续。

### 2026-06-28 续二：AI coach 实现（单次 coaching 输出体验）

按 spec（`docs/superpowers/specs/2026-06-28-ai-aim-coach-design.md`）+ plan（`docs/superpowers/plans/2026-06-28-ai-aim-coach.md`）实现 `kovaak_tracker/coach/` 子包。一次 fair summary → `build_report` → `CoachReport`（画像 + 优先级问题[三层根因链] + 5 类 Plotly 图表 + LLM 讲解）。

| 模块 | 能力 |
|---|---|
| `coach/profiles.py` | 画像典型集 + 根因映射表（数据，便于调词）|
| `coach/diagnosis.py` | `build_diagnosis`：画像匹配（典型+规则补偏差）+ 根因链（症状→物理→训练）+ 优先级 |
| `coach/visualization.py` | 5 类 plotly 图表（雷达/减速曲线/对比/问题列表/画像卡），前端无关 |
| `coach/providers.py` | LLM backend（借鉴 pi 骨架：按协议分类 Anthropic/OpenAI-compat + 配置 + 凭据），无 agent 框架 |
| `coach/narrator.py` | 讲解（结构化→教练人话，防幻觉 prompt）|
| `coach/report.py` | `build_report` 端到端 + 降级（讲解失败不阻塞）|

含 PROGRESS [A]：`analyze_flicking_fair_summary`（pan_tracker）让有 CSV 录像也产公平 summary。

**验证**：25 测试全过（pytest，全 mock 不依赖真实 LLM/SDK）。Batch 2（T2/T3/T4/T5）用 dispatch 并行 agent 执行。

**已知后续**：真实 LLM 联调（装 anthropic/openai + 配 key）/ 进步闭环（独立 spec）/ 多轮对话 B（需先补瞄准社区理论）/ web 前端。

### 2026-06-28 续三：视频分析加速（P1/P6/P4，1111s → 160s，7x）

真实联调 `6月23日.mp4` 暴露：`analyze_flicking_reference` 一次 **1111s（18.5min）**，UX 不可接受。profile 后三处优化：

| 优化 | 瓶颈 | 效果 |
|---|---|---|
| P1 compute seek 一次 | 逐帧 `cap.set` O(n²) | compute 的 seek 部分（有限——detect 才是主因）|
| P6 lock 10Hz 采样 + seek 一次 | lock 逐帧 seek + `_has_ui_element` 每帧 | lock **697s → 88s** |
| P4 detect/has_ui 降采样 ≤960px | 全帧 `np.linalg.norm`（106ms/帧）| detect **106 → 26ms**，has_ui 148 → 21ms |

结果：**1111s → 160s（7x）**，指标保持（flick 73/74, linearity 0.174/0.17, decel_frac 0.741/0.746, 全 ±6%）。`ui_band` 顶部 0.12→0.15 排除降采样 promoted 的 HUD（击杀数/timer）假阳性。

**不做 P2（抽帧）**：抽帧损 flick 形态 + SPARC（频域）精度，得不偿失。lock 34s + LLM 10s 是固定下限；<60s 体验需 web 前端 + 服务端异步，非单机优化。

### 2026-06-28 续四：进步闭环（progress loop，scope B）

按 spec（`docs/superpowers/specs/2026-06-28-progress-loop-design.md`）+ plan 实现。coach 从单次输出 → 跨次跟踪。

| 模块 | 能力 |
|---|---|
| `coach/progress.py` | Session JSONL 持久化（`output/history/sessions.jsonl`）+ `build_trend`/`build_comparison`（5 核心指标，verdict 符号感知 delta）+ `ProgressReport` |
| `coach/visualization.py` | + `build_trend_figure` / `build_comparison_figure` |
| `coach/narrator.py` | + `PROGRESS_SYSTEM_PROMPT` / `generate_progress_narration`（进步解读，防幻觉）|
| `coach/report.py` | `build_report(+history_path)` 存历史 + `build_progress_report`（趋势 + 对比 + 进步讲解）|

**用法**：
- `build_report(summary, ..., history_path=P)` —— 跑完自动存历史
- `build_progress_report(P, current_summary, ref_summary?, backend?)` —— 趋势图 + 对比图 + 对比表 + 进步讲解

**验证**：43 测试全过（progress T0-T4 + 原 coach）。dispatch（T1/T2/T3 并行 agent，T0/T4 串行我做）。T1 修正了 plan 的 verdict 负值 bug（sparc -5 vs -7 ratio 0.71<0.95 误判 worse → 改 `delta/|baseline|` 符号感知）。

**不做**（留后续）：④计划调整（动态处方）；多用户/云端；web 趋势页。

### 2026-06-29：理论底座 deep research（教练/反馈/技能习得）

deep-research workflow（5 angle fan-out → 22 源 → 64 claim → 25 对抗验证 → 7 存活 + 18 被反驳透明列）。产出 `docs/coach-theory-foundation.md`。

**7 个经久系统根基理论**（peer-reviewed，可作系统根基）：
- 双过程运动学习（Taylor & Ivry 2012，挑战 Fitts & Posner 串行观）
- deliberate practice 时长上限（Ericsson 1993：>4h/day 无益、>2h 减益、elite ~80min/session）
- contextual interference（交错 > 块状长期保留；实验室强、应用域弱）
- KR/KP 反馈分类（Gentile 1972，50 年经典）
- guidance hypothesis（Salmoni 1984：反馈过频损害学习——依赖）
- min-jerk 是派生结果非 CNS 原则（Harris & Wolpert 1998）—— 强化现有 metric 基础
- corrective submovement 神经编码差异（Schwartze/Rouse 2024）—— 强化 two_stage 维度

**关键约束落地**：诊断规则（advice/diagnosis）只用学术根基；社区/经验层（Voltaic 流派 / target selection / sensitivity）标「易过时」，只进 narrator 内容 + profile 标签，**不进诊断逻辑**。被反驳的（多模态/实时总更好、Ericsson 单调收益/10 年规则等）透明列出，系统不默认「更多反馈=更好」。

**支撑哪里的设计**：progress loop（不鼓励过量练习/高频复测，§1.2+§2.2）；narrator（先 KR 后 KP，§2.1）；B 形多轮（guidance fading + Socratic，§2.2+§2.3）。

**workflow 透明**：遭遇大量 429 限流，部分 verify 失败，但 7 存活 claim 均 2-0/3-0/2-1 投票 + peer-reviewed 主源确认，根基扎实。

### 2026-06-29 续六：瞄准社区前沿 deep research（第二轮）

第二轮 deep research，专攻**社区前沿**（学术不研究 KovaaK's/aim trainer 消费品，瞄准实际前沿在社区）。5 angle → 12 源 → 35 claim → 25 对抗验证 → 6 存活 + 19 被反驳透明。产出 `docs/coach-community-frontier.md`。

**6 个社区共识**（信源【权威社区共识】/【个人经验·视频】）：
- Voltaic **S5 分类法**：3 支柱（clicking/tracking/switching）× 3 子类 = 9（含新增 hybrid 第三类 linear/control/stability）
- 三支柱 + **颜色编码**（red/blue/purple），S5 仍用无争议
- 顶级玩家**子技能分化**（smoothness/stability vs explosive-speed/reactive 很少同时顶峰）
- 2025 May 顶级 tracking 玩家列表（VT Matty #1；时间敏感，编辑榜单非官方）
- Voltaic **三 benchmark track**（KovaaK S5 旗舰 + Aimlabs S3 + Valorant S1）
- 经典 **static clicking 三步**：big flick → micro-correction → hit-confirm（呼应学术 corrective submovement）

**被反驳（19，deep-research 默认淘汰）→ 理论重审全部保留**：deep-research 用学术标准（投票+多源）淘汰 19 条社区 claim，但**方法论修正**（点点指出：社区不能用学术标准严苛要求）后**理论重审**——19 条**无一与学术根基冲突**（多是训练法/技术/配置，与学术互补），故**全部保留**为社区经验素材（标未经验证，进 narrator 文案，不进诊断）。**社区判定 = 理论一致性**（冲突→排除；不冲突→保留），非信源形式（单源/时间敏感）。只有与学术冲突才理论证伪排除（本轮零排除）。详见 `coach-community-frontier.md` §2。

**关键约束**：社区内容**只进 narrator 文案 + profile 标签 + 训练处方理由**，**不进诊断规则**（学术根基 `coach-theory-foundation.md` 的领地）。时间敏感内容（顶级玩家名/rank/S5/energy 数字）定期复核。社区↔学术呼应点：micro-correction = corrective submovement。

### 2026-06-29 续七：YouTube 知识接入 coach（渐进式检索 + 全维度查证）

YouTube 创作者素材（MattyOW / bardOZ / Viscose 等）整理成核实过的知识文档，并接入 narrator——从「静态全量 prompt」改为「signal 驱动的渐进式检索」。

| 工作 | 结果 |
|---|---|
| 5 份整合报告去重 | 1 份 `youtube doc/YouTube 瞄准训练内容综合.md`（10 章）|
| 学术引用核实（gemini-grounding-search）| 剔除编造（Mariano 2024 = 真人 + 编造结论，36.75 亿实为 ZK 空投数字）、纠正（Semmler 2000 结论被颠倒、Van Beek 数字编造），保留真实文献 |
| 全维度查证（tracking / 动态 / 流动性 / 健康 / 握法）| **全部方向准确、未发现编造**，补学术锚点（Kowler 1978 / Lisberger 2015 / Lemon 2008 / Forman 2024）|

**narrator 渐进式检索**：

- `coach/knowledge.py`（新）：signal → {community, cues} 知识库，12 条，KEY 与 `advice.advise()` 的 `Finding.signal` **1:1 对齐**（无 miss）。
- `coach/narrator.py`：静态全量 SYSTEM_PROMPT → BASE 框架 + `build_system_prompt(diagnosis)`（按触发的 signal 拼装，prompt ∝ 信号数而非 KB 总量）。守住 anti-hallucination：社区知识仅供解释已给诊断，禁反推。
- `coach/__init__.py`：`from .report import` → PEP 562 `__getattr__` 惰性导入；纯逻辑模块（narrator / advice / diagnosis / knowledge）不再被 report → visualization → numpy 拖累。

**验证**：narrator 8 测试全过（含 2 个渐进式新测试）；纯逻辑测试 26 项零依赖可跑（lazy import 前 7 个 collection error）。重依赖测试（report / visualization / e2e）仍需 numpy / plotly，与本次改动无关。

**结构化路线**：knowledge.py 只 flicking 一维；tracking 高潜力待结构化（SPARC 对连续有效已证实，等 signal）；动态 / 流动性中潜力；健康 / 握法性质不同（独立模块 / 配置类），不进 knowledge.py。详见记忆 `coach-knowledge-structuring-roadmap`。

### 下个 session 接续点（2026-06-29）

理论底座**双层就位**（运动学指标层 + 教练/反馈/习得层），**YouTube 创作者知识已核实并接入 narrator（渐进式 signal 驱动检索）**，全维度（flicking / tracking / 动态 / 流动性 / 健康 / 握法）查证完成。coach 实现完成（单次 `build_report` + 跨次 `build_progress_report`），视频分析加速 7x（1111→160s）。纯逻辑测试 26 项零依赖可跑（lazy import 解耦）；重依赖测试待装 numpy/plotly。

下个 session 候选（按依赖/价值）：

1. **④计划调整（动态处方）** — 闭环最后一块。理论已就位：CI 交错练习（§1.3）+ deliberate practice 上限（§1.2）+ guidance hypothesis（§2.2）。设计基于趋势的自适应训练计划（指标 stall → 调处方 / 推交错多场景 / 提示休息而非「练不够」）。
2. **B 多轮对话** — Socratic + guidance fading。理论锚点：`coach-theory-foundation.md` §2.2 + §2.3。需 LLM tool-use 或对话状态管理；本地后端 tool-use 弱（可能仅 Claude 启用）。
3. **真实联调进步讲解** — deepseek key 跑 `build_progress_report`（需先积累 ≥2 次历史 session；可用合成历史先验验趋势/对比图）。
4. **web 前端** — Plotly 图表层已前端无关，迁 web 直接嵌。

**约束提醒**（来自理论调研）：诊断规则（advice/diagnosis）只用学术根基；社区/经验层（Voltaic 流派 / target selection / sensitivity）只进 narrator 文案 + profile 标签，**不进诊断逻辑**（防随版本过时）。系统不默认「更多反馈=更好」（多模态/实时被对抗投票淘汰）。

## 2026-06-27：真实数据端到端跑通 + 流水线重构为可复用

这个 session 用真实 1w6ts 录像（`6月23日.mp4` + 配套 CSV `1wall 6targets small - Challenge - 2026.06.23-23.44.51 Stats.csv`）跑通了整条链路，并发现+修复了一个根本性问题（CSRT 不适用 flicking），最终把全流程固化成可复用、零人工校准的模块。

### 里程碑

- **Phase 1（CSV↔视频对齐）真实数据验收通过**：`lock_challenge_window` 自动锁帧 775/4372（confidence 0.93），对齐覆盖率 **100%**（114/114 击杀全在轨内）。
- **Phase 2（flick 指标真实区分度）验收通过**：方案 A 给出干净、合理的指标，且形态吻合理论预测（不对称钟形）。
- **整条流水线可复用**：`analyze_flicking_video(video, csv)` 一行调用，自动锁帧 + 自适应检测 + 平移轨迹 + 对齐 + 指标，无需手填起始帧或采样目标色。

### 关键发现：CSRT 单目标追踪不适用 flicking（已废弃）

CSRT 在快速甩枪段丢失目标（真实视频 16% 帧丢失，集中在 flick 行进段），`_ball_speed` 的 forward-fill 在缺口恢复处制造**假速度尖峰**（单帧 48000 px/s），把指标全毁掉：peak_speed 中位假高到 9000+、decel_smoothness 85000+。这不是阈值问题，是追踪范式问题——**插值/调参都救不了**（甩枪段本身没观测到）。

### 方案 A：全局平移轨迹（替代 CSRT）

核心认知：**1w6ts 里目标是世界坐标静止的，玩家甩枪 = 视角平移，flick 速度 = 平移量。** 所以不该追单个目标（会丢），该测全局平移：
- 每帧自适应检测**全部**目标（背景差分，与颜色无关）
- 帧间最近邻匹配，取**中位位移** = 视角平移（丢/刷 1-2 个目标不影响）
- 积分平移成合成轨迹，其速度 = flick 速度

真实指标（109 flicks，对比 synthetic 基线合理）：

| 指标 | 真实值 | 解读 |
|---|---|---|
| peak_speed | 中位 1367 px/s | CSRT 假版 9000+，方案 A 修对 |
| decel_smoothness | 中位 5161 | synthetic fluid 1575 / twostage 6946 区间内 |
| peak_position | 中位 37.5% | 峰在前半段 → 不对称钟形 ✓ |
| accel/decel 面积比 | 中位 0.7 | 减速段 > 加速段，吻合神经科学结论 ✓ |
| two_stage | 0% | 全 fluid 流派 |
| 假尖峰 (>10000px/s) | 0 | CSRT 那版满屏假尖峰，已消除 |

### 可复用流水线

| 环节 | 模块 | 状态 |
|---|---|---|
| 自动锁起始帧 | `start_frame.lock_challenge_window` | ✅ robust（UI 屏检测 + 时长匹配） |
| 自适应目标检测 | `pan_tracker.detect_targets` | ✅ 颜色无关 |
| 平移轨迹（方案A） | `pan_tracker.compute_pan_trajectory` | ✅ |
| CSV 解析 / 对齐 | `csv_parser` / `aligner` | ✅ |
| flick 指标 | `flicking.run_flicking_analysis` | ✅ |
| **端到端编排** | `pan_tracker.analyze_flicking_video` | ✅ 一行调用 |

### 锁起始帧的 robust 流程（重点）

`lock_challenge_window(video, duration_s)`：KovaaK 录像里挑战被 UI 屏夹着（前面倒数、后面结算）。这些 UI 是**大块非背景元素**，用 **Otsu 全帧最大块**检测（对位置/颜色/文字占比都不敏感；百分位阈值在文字密集屏会自我抬升失效，Otsu 不会；退化帧 floor=40 防合并）。把帧分成游戏段/UI 段，挑**长度匹配场景时长**的游戏段 = 挑战。在本视频自动恢复 775/4372，并正确识别出 3 次重开倒数 + 1 结算页。

### 本 session 修改/新增的文件

| 文件 | 改动 |
|---|---|
| `kovaak_tracker/pan_tracker.py` | **新增**：detect_targets / compute_pan_trajectory / analyze_flicking_video |
| `kovaak_tracker/start_frame.py` | **+lock_challenge_window**（robust 自动锁帧，替代失败的 MSE detect_start_frame） |
| `kovaak_tracker/vision.py` | +底部 HUD 排除（detect_ball_by_color 之前只排顶部，导致 CSRT 粘在底部 UI） |
| `kovaak_tracker/flicking.py` | 修 `_ball_speed`：缺口检测从 `ball_w==0` 改为含 NaN（真实丢帧写 NaN，synthetic 写 0）；`extract_flicks` 用 `nanmax` |
| `kovaak_tracker/tracking.py` | 3.9 兼容：`str | None` 运行时别名改 `Optional[str]`（本机 Python 3.9.7） |

### 环境突破

- **cv2 装上了**（之前一直装不上）：注册表系统代理导致 pip SSL 崩，解法 `HTTPS_PROXY="" HTTP_PROXY="" NO_PROXY="*" pip install --proxy "" opencv-contrib-python`。
- 本机只有一个 Python 3.9.7（无 conda），项目 CLAUDE.md 写的 "3.10+" 实机没达到，所以做了 3.9 兼容修复。

### `.perf` 已解（不进主线）

KovaaK 3.9.x 另在 `performances/` 生成 `.perf`（与 CSV 同名配对）。已解码：标准 protobuf，头部（场景/hash/Challenge Start 毫秒时间戳/时长/目标列表）+ **1Hz 逐秒遥测**（射击/命中/miss/击杀/score）。字段经 CSV 四项汇总交叉验证（kills 114 / miss 7 / hit 114 / score 1074 全中）。**结论**：1Hz 太粗（flick<0.5s），不能替代 CSV 做逐 flick 对齐；CSV 仍是主线数据源。`.perf` 可作 per-second 趋势层（疲劳/节奏）+ 对齐一致性校验，写 parser 是可选增量。

### 已知限制 / 下一步

1. **overshoot 指标对平移轨迹无意义**：合成轨迹的 ball 是积分点，不是真目标位置 → `analyze_flicking_video` 默认 `crosshair=None` 跳过 overshoot。要算过冲需另追踪被击中目标的落点。
2. **duration_s 从 CSV 推**：`ceil(kills.time_s.max())`，假设末次击杀≈场景结束。clicking 场景成立；玩家提前停打的场景需手传 `duration_s`。
3. **`ui_area_frac=0.001`** 适合小目标 clicking 场景；大目标场景可能误判目标为 UI，需调（或改用相对目标大小的自适应阈值）。
4. **方案 A 仅适用 world-static 目标的 clicking 场景**（1w6ts 类）；tracking 场景（目标自身移动）不适用，tracking 仍用原有 CSRT 连续分析。
5. **dashboard 未接 pan_tracker**：Flicking tab 还读旧 metrics.json/flicking_segments.csv。接进来后 `analyze_flicking_video` 的产物要进 dashboard。
6. **多目标/多挑战录像**：当前 `lock_challenge_window` 取最接近 duration 的那一段；一段录像里多次完整挑战的情况还没处理（用 CSV 的 Challenge Start 墙钟时间匹配？）。

### 旧版架构决策（仍有效）

- 扁平结构，不拆 tracking/ flicking/ 子包。
- Flicking 用独立减速诊断视图，不融合进 Tension Quadrant（维度映射待真实数据验证后再说——现在真实数据有了，可重新评估）。

## 2026-06-29 续五：④ 计划调整（动态处方）完成

处方层 deep research（`wf_7793cb5f-265`，105 agents / 2.19M tokens / 22 确认 + 3 否决 + 7 caveats）→ `docs/coach-prescription-manual.md`（诊断信号→处方映射，标信源级别；社区判定宽松=理论一致性）。用 research 结论回填 ④ spec §11.1（interleave 升级渐进 hybrid + 元认知对抗话术 Simon&Bjork 2001）。

实现（分支 `coach/plan-adjustment`，6 commit，TDD）：

| 模块 | 改动 |
|---|---|
| `coach/planning.py`（新）| `build_plan` 规则引擎 + `TrainingPlan`/`PlanAdjustment`。stall(verdict=same,history≥N_MIN=3)→渐进hybrid interleave（reason 必带元认知对抗）；worse→regress_focus；better→maintain；最近两次 session 间隔<REST_GAP_DAYS=1.0→rest；history<N_MIN→仅观测 |
| `progress.py` | `ProgressReport` 加 `plan`/`plan_narration`（向后兼容 Any/None）|
| `narrator.py` | `PLAN_SYSTEM_PROMPT` + `generate_plan_narration`（外部焦点 + 元认知对抗 + 交错渐进话术）|
| `report.py` | `build_progress_report` 编排（现跑 advise 取处方池）|

**验证**：57 测试全过（含 8 planning + I1 逆序回归）。subagent-driven：Task 1（planning）dispatch implementer+reviewer approved；Task 2/3/4 inline（plan 代码 verbatim）；final whole-branch review（opus）Ready-to-merge-with-fixes → 修 I1（`_maybe_rest` `timedelta(0) <= gap` guard）+ 记 §11.2 偏差（trend reserved / per-metric adjustment / now dropped）。

**理论锚点（全学术根基进规则）**：§1.3 CI 交错 / §1.2 Ericsson 训练量上限 / §2.2 guidance hypothesis + Simon&Bjork 2001 元认知过度自信。社区经验（VDIM/weakness-specific/技术要领）只进 narrator 文案 + 处方理由，不进诊断规则。

**待点点**：review 分支 `coach/plan-adjustment`（`git log main..coach/plan-adjustment` / `git diff main...coach/plan-adjustment`）→ 决定 merge。**未碰 main、未 push**。工作树仅 `output/ref_pan_trajectory.csv`（先前联调 regenerable 产物，与 ④ 无关）。

## 2026-07-05 webapp 切片 1+2(后端 API+Worker / 前端骨架)状态 + 暴露问题

> ⚠️ **下个 session 必读**:暴露了真实项目债,点点判定"降智",开新 session 重来。问题全记录于此。

### 完成
- **spec** `docs/superpowers/specs/2026-07-05-flicking-coach-webapp-design.md`:产品 Aiming Cookie,FastAPI + Next.js + DeepSeek,香港部署不备案,品牌色 Cursor Orange
- **切片 1(后端 API+Worker)已 merge main**(14 commit):FastAPI POST/GET + SQLite 队列(BEGIN IMMEDIATE 替代 SKIP LOCKED)+ Worker + LLM 金额限额;22 单元测试 + 1 E2E(6月23日.mp4 真实视频 161s 跑通)
- **切片 2(前端骨架)分支 `webapp-slice2-frontend`**(未 merge,6 commit):Next.js 16 + Tailwind v4 + DESIGN-cursor tokens + 上传/等待/结果页;`npm run build` 过

### 🔴 核心问题(webapp 后端不通的根)

**1. coach 系统是 tracking 时代的,跟 flicking scope 冲突**
- memory `current-scope-flicking-only` 明确"只管 flicking",**我没贯彻**
- `coach/build_report` + `advise` + `knowledge.py` 12 signals 全是 tracking 时代(PTC/J-E Ratio)写的
- 期望 summary 格式:`{peak_speed_deg, linearity, sparc, reverse_ratio, decel_frac, ...}`
- 我切片 1 直接 `build_report(flicking summary)` 是错的——硬把 flicking 塞进 tracking 教练

**2. `analyze_flicking_video` 仍用旧指标(PROGRESS [A] 待办,`flicking-aim-coach.md` §7 明确)**
- `analyze_flicking_video` 走旧 `run_flicking_analysis`(静止间隙切分 + 旧 `decel_smoothness`)
- 实测产 summary keys:`flick_count, median_decel_smoothness, median_peak_position_pct, avg_peak_speed...`
- **不是公平指标**(`decel_frac/sparc/linearity` 等)。advise 不认 → `diagnosis.issues=[]` → ResultView 空
- 文档 §7 原话:"让 `analyze_flicking_video` 也输出公平 summary(复用 `segment_by_valleys` + `compute_fair_metrics`)是 PROGRESS [A]——最小改动,解锁完整流程,应最先做"
- **webapp 后端不通的根因就在此**:我跳过 PROGRESS [A] 直接接 build_report
- DB `aiming_cookie_dev.db` session 1 实测:`status=done` 但 `issues=[]` + `narration=None`

**3. LLM narration 没 key**
- worker 调 DeepSeek,`DEEPSEEK_API_KEY` 没设 → Connection error → build_report best-effort catch → `narration=None`
- `notes: ['讲解不可用: Connection error.']`

### 🟡 前端问题

**4. 非常丑(点点 review)**
- DESIGN-cursor tokens 配了(`globals.css` @theme inline v4),Inter 字体装了
- 原生 file input → DropZone(虚线+图标+拖拽)改了
- 但点点说"非常难看",具体哪没说(没截图)。可能:暖奶油没渲染?布局粗糙?没视觉冲击?
- **没真验证渲染**(我没浏览器,点点只说丑)

### ⚙️ 环境折腾记录(Windows)

**5. uvicorn `--reload` 孤儿进程**
- 8000 端口被孤儿占,杀不掉:`Get-WmiObject` 没匹配;Git Bash `taskkill /F` 被转义成 `F:/`;`Stop-Process` 杀不掉 reloader
- 临时换 8001(`lib/api.ts` 默认改 8001),8000 孤儿仍在(重启电脑才能清)

**6. 切片 2 测试忘了启 worker 进程**
- 只启了 uvicorn(API),session 卡 queued 轮询几十次(我的疏忽)
- worker 启动后 session 1 才跑完

### 修复路径(下个 session 决定)

**根问题是 PROGRESS [A]**:`analyze_flicking_video` 接公平指标。两条:
- **(a) 正路**:做 PROGRESS [A]——改 `analyze_flicking_video` 用 `segment_by_valleys` + `compute_fair_metrics` 产公平 summary。然后 worker → build_report → advise 自然 work(coach 设计本身 OK)
- (b) worker 改用 `analyze_flicking_reference`(无 CSV,已跑通公平指标)——但要配套无 CSV 录像路径

**(a) 是正路**。文档早说了"应最先做",我跳过才撞墙。

### ④ 计划调整也是 tracking 时代的债

- `build_plan` 用 `findings`(来自 advise)→ 间接依赖 tracking advise
- ④ 已 merge main,但 flicking scope 下也是债
- 暂不展开,先解决 PROGRESS [A]

### 待点点(下个 session)

1. **PROGRESS [A]**:改 `analyze_flicking_video` 接公平指标(正路)
2. **前端重做**:点点 review 具体丑在哪,按 DESIGN-cursor.md 重做
3. **设 `DEEPSEEK_API_KEY`**(环境变量)
4. **清 8000 孤儿**(或重启电脑)
5. **切片 2 分支 `webapp-slice2-frontend` 处理**:merge / 重做 / 弃
6. **重读** `docs/flicking-aim-coach.md`(flicking coach 设计基础,我之前没读是大错)

## 2026-07-05 续：上述诊断修正

> 这一段是后一个 session 的修正,不重写上面的历史——上面记录的是当时 session 的真实判断,留下来路。下面是事后核实的事实。

### 修正 1:advise / build_report / knowledge.py 不是"tracking 时代"

**上面"核心问题 1"判断错了**。审计 `kovaak_tracker/advice.py:64-197` 的 `advise()`,它消费的 signal 全是 flicking 公平指标(`decel_frac` / `linearity` / `sparc` / `reverse_ratio` / `submovement_overlap` / `peak_position` / `path_efficiency` / `peak_speed_deg` / `throughput` / `cm_per_360`),不是 tracking 的 PTC/J-E Ratio。`coach/knowledge.py` 12 条 KNOWLEDGE 也全是 flicking signal(sparc low / decel_frac high 等)。

也就是说:**coach 系统从一开始就是按 flicking 公平指标设计的**,"硬把 flicking 塞进 tracking 教练"这个诊断是反的——它本来就是 flicking 教练。

### 修正 2:真问题是 worker 调错入口函数(Phase 1A 已修)

webapp 后端不通的真原因:**worker 切片 1 调的是 `analyze_flicking_video`(旧 `run_flicking_analysis`,产 `decel_smoothness` 等旧 key)**,而 `advise()` 期望的是公平 summary keys(`decel_frac` / `sparc` 等)。所以 summary 喂进去全部 `None`-skip,`findings=[]`,`issues=[]`,ResultView 空。

**Phase 1A 已修**:`webapp/backend/worker.py:21-22` 已改为调 `analyze_flicking_fair_summary`(`pan_tracker.py`),它直接返回 `{metric: {med, p75, p90}, ...}` 公平 summary。这就是上面"修复路径 (a)"的正路落地。

### 修正 3:dashboard 已删(Phase 1B)

`dashboard.py`(Streamlit)已在 Phase 1B 从仓库删除。webapp 前端(`webapp/frontend/`,Next.js 16 + Tailwind v4 + Cursor 风设计系统)接替展示层。tracking 分析代码(`app.py` / `Analyze.py` / `analysis.py` / `tracking.py` / `vision.py`)仍保留待 v1 重构。

### 修正 4:④ 计划调整也不是"tracking 时代的债"

`build_plan` 用 `findings`(来自 `advise()`)→ 因为 advise 本来就是 flicking 规则(见修正 1),④ 计划调整在 flicking scope 下是有效的,不是债。

## 2026-07-05 续二：webapp frontend 重写 + coach agent + tracking v1

接修正 1-4 后分三个 phase 推进,所有改动**截至本段写作时仍在工作树未 commit**。

### Phase 1：worker 入口修复 + dashboard 删除

- **1A**：`webapp/backend/worker.py` 入口从 `analyze_flicking_video` 改为 `analyze_flicking_fair_summary`(`pan_tracker.py`),直接返回 `{metric: {med, p75, p90}, ...}` 公平 summary。`advise()` 终于收到正确的 keys,`findings` 不再空。E2E 真实视频跑通。
- **1B**：`dashboard.py` + `kovaak_tracker/dashboard_data.py` 从仓库删除。webapp 前端接替展示层。

### Phase 2：agent loop + DeepSeek + tracking v1 + chat backend

- **agent loop**：新建 `kovaak_tracker/coach/agent.py`(替换 narrator.py 单次 LLM,运行时入口)。3 个 narration 入口(`narrate_diagnosis` / `narrate_progress` / `narrate_plan`)+ 1 个 chat 入口(`chat_with_coach`),共用 tool-use loop。`agent_tools.py`(tool schema + handlers)+ `agent_kb.py`(按 signal / topic 索引的预备知识切片)配套。防幻觉铁律:诊断 payload 是 ground truth,数值必须来自 payload 或 tool 切片。`narrator.py` 保留作 manual fallback。
- **DeepSeek 接通**：`coach/providers.py` DeepSeek 后端调通(OpenAI 兼容 endpoint)。
- **tracking coach v1**：新建 `kovaak_tracker/advice_tracking.py`,7 个 signal(accuracy / loss_count / off_time / avg_error / speed / accel / ptc)。后三个标 None = uncalibrated,emit `info` / `watch` 级别(spec §7 解释性假设而非硬诊断)。复用 flicking `Prescription` / `Finding` dataclass,下游(diagnosis / visualization / narrator)统一。
- **chat backend**：`POST /api/sessions/{id}/chat` + `GET /api/sessions/{id}/chat`(`webapp/backend/routes.py`)。history 持久化在 SQLite(`db.load_chat_history` / `save_chat_message`)。chat 调用 `chat_with_coach` agent 入口,失败 best-effort 降级。

### Phase 3：前端 fresh 重写 + 4 屏 + Stitch 设计 + coach 页时间戳联动

点点 review 旧 slice 2 前端("非常难看")后决定起 fresh。旧分支 `webapp-slice2-frontend`(暖奶油风)未 merge,代码已删。

- **新前端** `webapp/frontend/`：Next.js 16 + React 19 + Tailwind v4,**dark 风落地**
- **4 屏全齐**:
  - `app/page.tsx` upload(CSV 必填 + 视频上传)
  - `app/sessions/[id]/page.tsx` processing(删渐变,dark)
  - `app/sessions/[id]/report/` report(dark bento + Plotly chart 组件 `components/PlotlyChart.tsx`)
  - `app/sessions/[id]/coach/` coach(左 65% `<video>` + 自定义 timeline,右 35% 聊天;点 chat 消息跳视频时间点)
- **设计依据**:`stitch_cursor_design_system/`(点点用 Stitch 跑出来的 dark 设计 + `obsidian_hearth` design tokens),多版本 dark 风 HTML 原型供参考
- **视频流**:`GET /api/sessions/{id}/video` 流式返回给 coach 页 `<video>` 标签

### 测试

新增 `tests/coach/test_advice_tracking.py` / `test_agent.py` / `test_agent_chat.py` + `webapp/tests/test_routes_chat.py` / `test_routes_coach.py`。webapp 总测试数 ~40。

### 待点点(下个 session)

1. **分批 commit Phase 1/2/3**(目前全在工作树)
2. **tracking 真实数据校准**:speed/accel/ptc threshold 当前是 None / 初值,需真实 tracking session 标定
3. **timeline markers**:worker 持久化 miss-frame 时间戳到 DB,coach 页自定义 timeline 直接读
4. **auth + per-user quota**(Wave 2D 待定)
5. **CLAUDE.md 更新**:本 phase 改动(architecture / flicking+tracking 双 scope / agent 取代 narrator 单次)需要落进 CLAUDE.md(部分已落)

## 2026-07-05 续三：全量 code review + Critical 修复 + cm/360 全链路接通

接续二(Phase 1/2/3 改动堆积未 commit),用 5 个并行 subagent(同步模式)做全量 review,发现 4 Critical + 多 Warning。同时 cm/360 公式调研修正 memory 错误结论,接通全链路。

### Review 方法

5 个 domain 各派一个 general-purpose subagent(同步模式 `run_in_background=false`,final message 直接返回,绕开 teammate 通信坑——teammate 模式在此环境挂了:plain text 不可见 + SendMessage API 400):coach agent loop / tracking v1 / coach 核心+providers / webapp backend / webapp frontend。

### Critical(4 条,已全部修复)

| # | 位置 | 问题 | 修复 |
|---|---|---|---|
| C1 | routes.py:49-50 | X-User-Id 未校验直接拼路径 → 路径穿越 | `_USER_ID_RE` 正则 + 扩展名白名单 + 2 渗透测试 |
| C2 | test_e2e.py:51 | 断言 `not exists` vs worker 保留视频行为相反 | 改 `assert exists` |
| C3 | (原诊断错,见下) | cm/360 + FOV 链路断(worker 不传 + csv_parser 提取未用) | 全链路接通(见下) |
| C4 | agent.py:229,242 | max_turns/max_tokens 返回半截 preamble 当 narration | 异常路径 `narration=None` + 2 回归测试 |

### C3 cm/360 全链路接通(诊断修正 + 公式调研)

**原 C3 诊断错**:agent 报"前端收集 cm/360+FOV 但 FormData 没 append,后端不用"。核实发现更严重——后端 `analyze_flicking_fair_summary` 有 `fov` + `cm_per_360` 参数且真用(pan_tracker.py:360 `deg_per_px` + :380 `cm_per_deg` + :262 `peak_cm_per_s`),但:
1. `worker.run_analysis` 没传(用 default 103/None)
2. `analyze_flicking_fair_summary` 内部 parse csv_parser 拿到 stats(dpi/horiz_sens/fov 属性),但只读 `stats.kills`,没用 `stats.fov`

**公式调研**(点点要"搜如何计算"):Gemini grounding search(2026-07-05,WebSearch/web-search-prime 限流,用 gemini-grounding-search skill 走 Google OAuth)确认公式 `cm/360 = 914.4 / (yaw × Horiz_Sens × DPI)`,yaw 依赖 game:
- Valorant yaw=**0.07**(第一次 Gemini 搜错说 0.022,第二次专门确认 0.07)
- 点点 CSV(DPI 1600, Horiz Sens 0.16, Sens Scale=Valorant)→ **51.03 cm**(点点确认对,之前 memory 记 48 是记错)
- memory `kovaak-cm360-approx-wrong` 原结论"公式不准"已修正——根因是漏 yaw 因子,含 yaw 后公式完全准确

**全链路接通**:
- `csv_parser.py`:加 `GAME_YAW` 表(Valorant=0.07, Source/CSGO/Quake/Apex/Fortnite=0.022, OW/OW2/COD=0.0066) + `cm_per_360` 属性(含 KovaaK's `"cm/360"` scale 特殊处理——Horiz Sens 直接是 cm/360)
- `db.py`:sessions 表加 `cm_per_360` + `fov` 列 + migration(`_migrate_add_column_if_missing`,兼容旧 db)
- `queue.py`:enqueue 接收 + claim_next 返回
- `routes.py`:`/analyze` 加 Form 字段(`Optional[float]`,Python 3.9 兼容——`float | None` 在 3.9 FastAPI 反射失败)
- `worker.py`:`run_analysis` 从 job 读 + csv_parser fallback → 传 `analyze_flicking_fair_summary`
- 前端:撤回 disabled + FormData append cm_per_360 + fov

**数据流**:用户 UI 填(主) > CSV `csv_parser.cm_per_360` fallback(DPI + Horiz Sens + Sens Scale yaw 表) > None。

### LLM 预算三连(部分修)

- **W2** `worker.py _estimate_llm_cost_cny("")` 空字符串 cost≈0 → 加 `min_output_tokens=500` 保守下界(已修)
- **W1** `routes.py` chat 不调 budget → 加 `check_and_record` 预检查(429) + `queue.add_llm_cost` 累加记账(已修)
- budget wrapper(架构性,所有 LLM 入口统一走 budget)未做,跟点点决策

### 未修(跟下个 phase 决策)

- IDOR / session ownership check:跟 slice 3 Clerk
- budget wrapper 架构性修复
- 其余 Warning/Nit:chat 长度上限 / history 无界 / 轮询退避 / RSC localhost / 图标 aria-label / 导出 PDF 占位 / font-headline-sm 无效 utility / next lint 废弃等

### 验证

- coach 全套 **108 passed**(含 C4 的 2 新回归测试)
- webapp 全套 **44 passed**(含 C1 的 2 新渗透测试)
- 前端 `npm run build` ✓绿(TypeScript 全过,3 静态 + 3 动态路由)
- cm/360 e2e 验证:点点真实 CSV → `cm_per_360=51.03, fov=103.0`(公式对)

### 待点点

1. **commit 策略**:本轮修复 + Phase 1/2/3 + cm/360 接通叠加在同文件,无法文件级分离。建议按主题分 commit(review fix / cm/360 接通 / Phase 1/2/3)。
2. budget wrapper 架构性修复
3. IDOR 跟 Clerk
4. Warning/Nit backlog
