# Flicking 模块进度

> 最后更新：2026-06-28

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
