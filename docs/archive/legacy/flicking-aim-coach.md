# Flicking AI 教练：从运动学原理到诊断建议

> flicking 分析 + 建议模块的完整介绍：它测什么、为什么这样测、怎么从指标读到训练处方。
> 理论细节见 `docs/aim-kinematics-research.md`，开发历程与决策见 `docs/PROGRESS.md`。

## 1. 它是什么

不是打分器，是**诊断器**。输入一段 KovaaK flicking 录像，输出三件事：

1. **诊断**：你的 flick 在哪些维度偏离了健康运动学
2. **归因**：每个问题背后的物理原因（为什么）
3. **处方**：针对性的训练场景（怎么练）

对标一个能看录像、懂运动学、给个性化作业的 aiming 教练。核心价值不在算分，而在「指标 → 诊断信号 → 处方」这条可解释的链路——每个建议都能追到一条运动学理论或社区共识。

## 2. 三条设计原则

1. **减速段是成败关键**（Becker 2020）：flick 速度曲线是不对称钟形，减速段比加速段长（命中精度需求），减速段运动学是预测命中的最强信号。所有核心指标都围绕减速段质量。
2. **公平跨人比较**：诊断指标全部无量纲、与峰值速度/分辨率/灵敏度无关。这是 2026-06-28 方法论修正的核心——旧指标 `decel_smoothness` 因与 peak_speed 强相关（corr=0.76）对高速玩家天然不公平，被弃用。
3. **双重理论支撑**：每条诊断规则都挂靠运动学黄金标准（学术）+ Voltaic/KovaaK 社区共识（实战）。

## 3. 数据流（录像 → 处方）

```
                        KovaaK flicking 录像
                                │
          ┌─────────────────────┴─────────────────────┐
          ▼                                           ▼
  [无 CSV] analyze_flicking_reference         [有 CSV] analyze_flicking_video
   lock_challenge_window                      parse_stats_csv + 锁帧 + 对齐
   compute_pan_trajectory                     (当前仍走旧切分/旧指标，
   segment_by_valleys                          公平指标接入是待办，见 §7)
   compute_fair_metrics
   _summarize_reference  → {metric: {med,p75,p90}}
          │                                           │
          └───────────────────┬───────────────────────┘
                              ▼
           self_summary  (+ 可选 reference_summary)
                              │
              ┌───────────────┴────────────────┐
              ▼                                ▼
      advice.advise()                  advice.compare_table()
      → findings[{signal,              → [{metric, self, ref,
         severity, diagnosis,             verdict}]  对比表
         prescriptions}]
```

**两个入口**：
- **无 CSV**（`analyze_flicking_reference`）：给一段录像就能分析，用速度谷切分 + 公平指标。适合下载高手录像做参考对比。
- **有 CSV**（`analyze_flicking_fair_summary`，主线入口）：录像 + KovaaK stats CSV，能拿到击杀时刻/命中率/灵敏度元数据，已统一走 `segment_by_valleys` + `compute_fair_metrics` 产出公平 summary（PROGRESS [A]，已完成）。原 `analyze_flicking_video`（旧 `run_flicking_analysis` 静止间隙切分 + `decel_smoothness`）是历史入口，不再推荐。

**建议模块目前对接的是 reference 模式产出的公平 summary**——这是完整跑通的链路。

## 4. 原理底座

### 4.1 运动学黄金标准

- **Minimum-jerk**（Flash & Hogan 1985）：点对点运动的速度是对称钟形，峰在中点（50%）。这是「无精度压力」下的数学最优。
- **Becker 2020**（mouse reach 实测 + 机器学习）：aim 速度是**不对称**钟形，减速段 > 加速段；减速段运动学参数是预测成功/失败的最强信号；减速是自适应的（由峰值速度 + 峰值时刻共同决定）。

调和：min-jerk 是理想，aim 因命中需求延长减速段。

### 4.2 理论谱系

```
Woodworth 1899 ──→ Flash&Hogan 1985 ──→ Meyer 1988 ──→ Novak 2002 ──→ Becker 2020
  两阶段模型        min-jerk            optimized       overlapping      减速段=成败
  (initial+         对称钟形            submovements    =流体/discrete   最强信号
   corrective)                          +Fitts关联      =两段式
```

我们补的三条理论（min-jerk 减速曲线、submovement、Fitts）**全是 Becker 2020 的核心引用**——补的不是外部理论，而是 Becker 谱系里原本就该有、之前漏挂的。

### 4.3 指标 → 理论映射

| 指标 | 度量什么 | 理论锚点 | 健康区间 | 偏离的意义 |
|---|---|---|---|---|
| `decel_frac` | 减速段占 flick 时长比 | Becker 不对称钟形 | 0.50–0.65 | >0.7 减速在蹭；<0.4 减速不足/撞墙 |
| `peak_position_pct` | 峰值速度时刻 % | min-jerk 50% / aim 35–50% | 35–50 | <30 加速过急；>60 加速拖沓 |
| `linearity` | 减速段速度偏离匀减速直线 | **constant-deceleration**（非 min-jerk，§6.1 of research） | <0.12 | >0.13 制动不匀 |
| `sparc` | 整段速度的频域弧长 | **SPARC** (Balasubramanian 2012) | >−5 | <−5 减速抖动、张力释放不平滑 |
| `reverse_ratio` | 减速段反向加速帧占比 | 单调制动 | <0.18 | >0.22 锯齿/反复修正 |
| `path_efficiency` | 起终点直线/实际路径 | min-jerk 点对点直线 | >0.85 | <0.85 路径绕 |
| `corrective_count` | corrective submovement 数 | Woodworth/Meyer | 少 | 多 = 反复修正 |
| `submovement_overlap` | corrective 与 primary 重叠度 | Novak 2002 overlapping | 高=流体 | 低=两段式（Bardpill） |
| `peak_speed_deg` | 峰值角速度 °/s | 速度-精度权衡（相对参考） | — | <参考×0.7 发力不足 |
| `throughput` | Fitts TP = log₂(D/W+1)/MT | **Fitts 1954** | — | <参考×0.7 跨距离发力不足 |

> `linearity` vs `sparc` 的分工是 2026-06-28 理论深化的关键结论：linearity 度量「制动节奏匀不匀」（锚点 constant-deceleration），sparc 才是「减速抖动/张力释放」的理论正解（频域、无量纲）。两者不是同一维度。

## 5. 分析模块

代码：`kovaak_tracker/pan_tracker.py` + `kovaak_tracker/flicking.py`。

### 5.1 为什么追「全局平移」而不是单个目标

CSRT 单目标追踪在快速甩枪段丢目标，forward-fill 在缺口恢复处制造假速度尖峰（单帧 48000 px/s），把所有指标毁掉。这不是阈值问题，是追踪范式问题。

**核心认知**：1w6ts 这类 clicking 场景里，目标是世界坐标静止的，玩家甩枪 = 视角平移，**flick 速度 = 视角平移量 = 目标的屏幕速度**。所以：
- `detect_targets`：每帧自适应检测**全部**目标（颜色无关的背景差分）
- `compute_pan_trajectory`：帧间最近邻匹配，取**中位位移** = 视角平移（丢/刷 1–2 个目标不影响），积分成合成轨迹

### 5.2 谷切分（`segment_by_valleys`）

静止间隙切分对高手失败（连续甩、无静止间隙 → flick 被合并成超长段，flick 数少 2/3）。改为**速度谷切分**：一个 flick = 相邻速度谷之间。对高速连续 flick 鲁棒。

### 5.3 公平指标（`compute_fair_metrics`）

逐指标的设计要点（公平性 = 无量纲 / 跨速度）：

- `linearity`：减速段速度对一次直线（匀减速）拟合的归一化 RMSE，/peak
- `sparc`：**整段**速度曲线的 SPARC（半钟形会让频谱破碎、数值虚高，必须整段）
- `reverse_ratio` / `decel_frac` / `endpoint_peak` / `peak_position_pct`：减速段形状无量纲量
- `corrective_count` / `submovement_overlap`：**主峰后固定窗口**扫 corrective submovement——独立于 valley 切分（two-stage 的急停 micro 会被 valley 切成独立段，但在速度序列里仍紧邻主峰，窗口法能抓到）
- `path_efficiency` / `path_length_deg` / `direction_deg`：路径几何，角度量用 `deg = px × FOV / width` 跨分辨率可比
- `throughput`：Fitts TP，需要目标宽度 W（当前 reference 模式无 W → NaN，接线见 §7）

## 6. 建议模块

代码：`kovaak_tracker/advice.py`。规则引擎，summary 驱动，source-agnostic（喂它 `analyze_flicking_reference` 的 summary 或任何同形状的 summary 都行）。

### 6.1 规则引擎 `advise(self_summary, reference_summary?, cm_per_360?)`

输出一组 `Finding`，每条含：
- **signal**：触发信号（如 `sparc low`）
- **severity**：`info` / `watch` / `fix`
- **diagnosis**：人话诊断陈述
- **prescriptions**：训练场景 + 为什么有效

规则表（信号 → 阈值 → 诊断 → 处方）见 `aim-kinematics-research.md §3`，覆盖：decel_frac / linearity / sparc / reverse / two_stage / peak_position / path_efficiency / peak_speed / throughput / sensitivity。每条都挂靠 §4 的理论锚点。

### 6.2 对比 `compare_table(self_summary, reference_summary)`

逐指标 self vs reference 的 `better/worse/same/info`：
- 单调指标：`sparc`/`throughput`/`peak_speed` higher-better，`linearity`/`reverse_ratio`/`decel_frac`/`endpoint_peak` lower-better
- 非单调指标（`peak_position`/`path_length`/`submovement_overlap`/`corrective_count`）标 `info`——流体 vs 两段式是风格，不是绝对优劣，交给 `advise` 的区间诊断

> SPARC 是负值（越接近 0 越平滑），higher-better 对负值仍成立（−4 比 −7「高」=更平滑=更好）。

### 6.3 处方库（Voltaic 场景）

| 场景 | 练什么 | 何时开 |
|---|---|---|
| 1w4ts Voltaic | 整体 static + 减速 + pathing（benchmark）| 综合诊断 |
| Pasu | 加速→减速完整度、clean | decel_frac / linearity / sparc 问题 |
| Multiclick | 落点精度、micro correction | reverse / two-stage 问题 |
| linetrace | 直线 flick、path efficiency | path_efficiency 低 |
| Tile Frenzy | 速度、arm 发力 | peak_speed / throughput 低 |

核心口诀（Voltaic VDIM）：**Clean lines. Clean movements. Deceleration after a big flick.**

## 7. 当前状态与后续

### 已就绪 ✅
- **`kovaak_tracker.coach.build_report`**：完整单次 coaching 输出（画像 + 三层根因链 + 5 类可视化 + LLM 讲解），消费 `analyze_flicking_fair_summary` 产出的公平 summary（PROGRESS [A]，已完成）。设计见 `docs/superpowers/specs/2026-06-28-ai-aim-coach-design.md`，25 测试全过
- 无 CSV 参考模式（`analyze_flicking_reference`）：录像 → 公平指标 → summary，完整跑通
- `advise` / `compare_table`：消费公平 summary 输出诊断 + 对比表
- 真实验证：你（48 cm/360）vs 高手（80 cm/360）的 1w6ts 对比，产出 4+ findings

### 已知缺口（诚实记录）
1. **有 CSV 模式——已完成**：`analyze_flicking_fair_summary`（在 `pan_tracker.py`）已统一走 `segment_by_valleys` + `compute_fair_metrics`，产出公平 summary；webapp 后端 worker（Phase 1A）已切换到这个入口。原 `analyze_flicking_video`（旧 `run_flicking_analysis` 静止间隙切分 + `decel_smoothness`）仍是历史入口，不再推荐使用。
2. **throughput 接线**：reference 模式（无 CSV）拿不到目标宽度 W → NaN。完整接线需 `detect_targets` 的目标尺寸 → `compute_fair_metrics(target_width_deg=...)`。
3. **阈值校准**：`sparc_low` / `two_stage_overlap` 是理论初始值，需真实数据校准（同 linearity / decel_frac 当初的轨迹）。
4. **目标检测类指标未实现**：overshoot / reaction / target_selection——需要单目标落点追踪，噪声大，属后续二期。

## 8. 理论来源

完整引用见 `aim-kinematics-research.md §7`。核心：
- **运动学**：Flash & Hogan 1985（min-jerk）、Becker et al. 2020（减速段=成败最强信号）、Balasubramanian 2012（SPARC 平滑度金标准）
- **Submovement**：Woodworth 1899、Meyer et al. 1988（optimized submovements）、Novak et al. 2002（overlapping）、Schwartze/Rouse 2024（initial vs corrective 编码 + 切分标准）
- **Fitts**：Fitts 1954、MacKenzie/Zhai（effective target width）
- **社区共识**：r/FPSAimTrainer、Voltaic VDIM guide、aiming.pro（灵敏度）
