# 瞄准运动学 & 社区共识研究（advice.py 知识底座）

> 用途：`advice.py` 规则引擎的知识底座。诊断信号 → 处方的映射全部来自这里的运动学黄金标准 + Voltaic/KovaaK 社区共识。
> 上游：`flicking-analysis-plan.md` 已有 Becker 2020 神经科学、社区流派、病理模式表。本文补：方法论修正（这次真实数据验证得出）+ 训练处方缺口。

## 1. 方法论修正（2026-06-28 真实数据验证）

原 plan 的指标在真实 1w6ts 数据上暴露两个问题，必须修正：

| 弃用 | 原因 | 替代 |
|---|---|---|
| 减速段加速度 std（`decel_smoothness`）作跨人/跨速度指标 | 与 peak_speed 强相关（实测 corr=0.76），加速度随速度超线性增长，除以一次 peak 不够 → 对高速玩家天然偏高，不公平 | **减速段线性度** `linearity`：减速段速度曲线偏离"匀减速直线"的归一化 RMSE（/peak），无量纲、跨速度公平 |
| 静止间隙切分（`extract_flicks` lookback 合并） | 高手连续甩、flick 间无静止间隙 → 合并成超长段，flick 数少 2/3、peak_pos/two_stage 全失真 | **速度谷切分** `segment_by_valleys`：flick = 相邻速度谷之间，对高速连续 flick 鲁棒 |
| `endpoint/peak`（旧切分）| 终点不是谷时漂移大 | 谷切分下终点=谷，endpoint/peak 失去区分度；减速质量改用 linearity/reverse 判定 |

新增维度（原 plan 缺）：
- **decel 段占比 `decfrac`**：减速段长 / flick 长。实测"急加速+长减速"型 decfrac≈0.75，是核心效率问题。
- **路径几何**：`path_efficiency`（起终点直线/实际路径长，1=完美直线）、`path_length_deg`、`direction` 分布。

## 2. 运动学黄金标准

- **Minimum-jerk trajectory**（Flash & Hogan）：点对点运动速度是**对称钟形，峰在中点（50%）**，是运动平滑度的数学黄金标准。
- **Becker 2020**（aiming 实测）：aim 速度曲线是**不对称钟形，减速段 > 加速段**——因为有精准命中的末端精度需求。减速段运动学是预测成败的**最强信号**。
- 调和：min-jerk 是"无精度压力"理想；aim 因命中需求延长减速段。
- **指标健康区间**（据此设定 advice 阈值）：

| 指标 | min-jerk 理想 | 健康 aim | 偏离 = 问题 |
|---|---|---|---|
| peak_position % | 50 | 35–50 | <30 加速过急/减速过长；>60 加速拖沓 |
| decfrac | 0.50 | 0.50–0.65 | >0.7 减速段蹭；<0.4 减速不足/撞 |
| linearity（匀减速线性度）| 低 | <0.12 | >0.15 制动不匀（减速抖动看 sparc）|
| sparc（减速段平滑度，§6.1）| 高（≈0）| >−0.5 | <−0.5 减速抖动、张力释放不平滑 |
| reverse 占比 | 0 | <0.18 | >0.22 减速段锯齿/反复修正 |

## 3. 诊断信号 → 处方（advice.py 核心规则）

社区共识来源：r/FPSAimTrainer、r/aimlab、Voltaic VDIM guide、Steam KovaaK discussions。

| 诊断信号 | 阈值（初步） | 诊断陈述 | 处方 |
|---|---|---|---|
| decfrac 高 | >0.65 | 急加速 + 长减速，减速段在"蹭" | 果断减速一次到位；pasu / 1w4ts 练完整加减速；意识：flick→confirm |
| linearity 高 | >0.13 | 制动不匀（接近匀减速的线性度差；抖动另看 sparc）| clean lines；pasu；降速练制动；减速段当一次动作 |
| sparc 低 | <−0.5（待校准）| 减速段平滑度差、张力释放抖（频域弧长短，§6.1）| clean lines；pasu；减速段当一次独立动作 |
| reverse 高 | >0.20 | 减速段锯齿/反复修正 | 转流体派（减速段即修正）；pasu；别 readjust |
| two_stage（overlap 低）| overlap<0.3 | discrete corrective submovements（两段式），primary→停→corrective 有延迟（§6.2）| 流体派：corrective 与 primary 重叠（overlapping submovements），减速段即微调 |
| peak °/s 远低于参考 | <参考×0.7 | 甩得偏慢，发力不足/手腕主导 | 练 arm 发力与动态速度；Tile Frenzy / speed 类 |
| throughput 低 | <参考×0.7 | 跨距离发力能力不足（已按 D/W 归一化，§6.3）| 练 arm 发力；Tile Frenzy / speed 类；先求速度再收精度 |
| peak °/s 远高于参考 + 精度差 | — | 速度失控 | speed management；降速换精度；accuracy focus |
| path_efficiency 低 | <0.85 | flick 路径不直、绕路 | linetrace；clean lines；走最短路径 |
| 过冲 overshoot（需目标位置）| — | 制动不足 | 降 sens 5–10% + 保持 arm 速度；clean lines |
| peak_position 太前 | <30% | 加速过急、减速拖沓 | 平衡加减速，峰靠中 |
| peak_position 太后 | >60% | 加速拖沓、来不及减速 | 果断加速 |

## 4. 灵敏度决策框架（cm/360）

社区共识：通用推荐 **28–43 cm/360**（aiming.pro）；tracking 偏快 20–25（aimer7）；~45 cm = arm flicking，~24 cm = wrist。

| cm/360 | 分类 | 对 flicking 的含义 |
|---|---|---|
| <25 | 偏快（wrist）| 制动难，手抖放大；过冲持续→建议 +5–10% cm/360 |
| 25–45 | 主流健康 | 一般无需动 |
| >50 | arm aimer | 控制精度高、过冲少；速度靠 arm 能力，慢则练发力 |

**原则**：sens 是放大器/缩小器，不是根因。制动失控的根因是发力-释放不对称的技术问题；调 sens 是辅助实验，**必须复测验证**（降 sens 后 linearity/reverse 应下降，否则调回）。

## 5. 场景处方库（Voltaic）

| 场景 | 练什么 | 用法 |
|---|---|---|
| 1w4ts Voltaic | 整体 static + 减速 + pathing（benchmark）| acc 90%+ 为目标 |
| 1w4ts 30% larger | 减速精度 | 减速段质量专项 |
| Pasu | 加速 + 减速完整度、干净 | 单目标切换，练完整 flick |
| Multiclick | 点击精度、micro correction | 落点精度 |
| linetrace | 直线 flick、path efficiency | path_efficiency 低时 |
| Tile Frenzy | 基本功、speed | 速度/发力 |
| Voltaic weakness-specific playlist | 针对性 flick 弱点 | 综合诊断后选 |

核心口诀（Voltaic VDIM）：**Clean lines. Clean movements. Deceleration after a big flick.**

## 6. 理论深化（2026-06-28 deep research）

针对 flicking 指标的三个理论缺口做的 deep research。**三条理论全是 Becker 2020 的核心引用**，理论谱系一致。结论直接指导 `flicking.py` 的指标实现与 `advice.py` 的规则。

### 6.1 减速段理想曲线：min-jerk ≠ 匀减速（linearity 归因修正 + SPARC）

**问题**：`linearity` 拟合减速段速度对**一次直线**（`polyfit deg=1`）的归一化 RMSE，原挂 min-jerk 名下。但两者不是一回事。

**min-jerk 速度曲线**（Flash & Hogan 1985，原始论文确认）：

$$v(\tau) \propto 30\tau^2(1-\tau)^2,\quad \tau=t/D$$

对称钟形，峰在 τ=0.5。**减速段（τ∈[0.5,1]）是平滑曲线，不是直线。**

**匀减速（直线拟合的隐含理想）** = 恒定负加速度 = 加速度阶跃 = **jerk 不连续**，在平滑性谱系里反而是较不平滑的减速形式。

**结论**：
- `linearity` 度量的是"**制动线性度 / 接近匀减速的程度**"，理论锚点是 **constant-deceleration（恒定制动）**，**不是 min-jerk**。它诊断"制动节奏匀不匀"。
- 它**不适合**作"减速抖动/张力释放不平滑"的代理——一个真正平滑的 min-jerk 减速反而会偏离匀减速直线，得到较差的 linearity 分数。
- "减速段抖动/平滑度"应改用运动平滑度金标准度量。

**平滑度金标准：SPARC（Spectral Arc Length, Balasubramanian 2012）**
- 频域度量：速度幅度谱的归一化弧长
- **无量纲**（跨速度/跨人公平——正是 `decel_smoothness` corr=0.76 不公平问题的正解；linearity 解决的是"线性度公平"，SPARC 解决的是"平滑度公平"）
- 对噪声鲁棒、敏感于平滑度变化，优于 dimensionless jerk (DLJ)；现代运动控制/康复的金标准

**指标改动**：`linearity` 保留（制动线性度维度），归因改 constant-deceleration；**新增 `sparc`** 作减速平滑度正解；advice 的 linearity 诊断从"减速抖动"改为"制动不匀"，抖动改由 sparc 判定。

### 6.2 Submovement 理论：两段式 vs 流体的学术定义

**问题**：`FlickFairMetrics` 删掉了 `is_two_stage`（旧切分证伪时一并丢），但"两段式 vs 流体"是真实理论维度，原靠社区流派词（Bardpill/Zeonlo）支撑。

**理论谱系**（Becker 2020 核心引用）：
- **Woodworth 1899** 两阶段：initial submovement（ballistic，前馈）+ corrective submovement（视觉反馈修正）
- **Meyer 1988** optimized submovements（Psychological Review 95:340）：优化 primary+secondary 相对时长，最小化总运动时间保持精度 → **直接关联 Fitts' Law**
- **Novak 2002** overlapping submovements：快速运动中 submovements 重叠融合 = **流体**；可分离 = 两段式
- **Flash & Hogan 1985**：每个 submovement 都是钟形速度曲线
- **Schwartze/Rouse 2024（PMC11427045）**：corrective 与 initial 在 M1 用不同神经子空间编码，corrective gain 更高（1.14–1.36×）——区分的神经证据

**submovement 切分学术标准**（Rouse 2022 / Schwartze 2024）：
1. 速度峰，且
2. ±200ms 内无更大峰
3. peak prominence ≥ **50% of adjacent speed troughs**（注：旧 `is_two_stage` 用 40%×peak，与此接近但不等价）
4. 他们用 250 px/s 绝对阈值，我们改 **自适应 = peak 的比例**，跨分辨率公平
- **initial**：大的初始 reach，至少移动到目标一半距离
- **corrective**：initial 之后的所有 submovement

**"两段式 vs 流体"学术映射**：
- Bardpill（两段式）= **discrete corrective submovements**：primary 与 corrective 速度峰之间有明显谷，可分离
- Zeonlo（流体）= **overlapping submovements**（Novak 2002）：corrective 与 primary 融合，单峰/连续减速

**指标改动**：用学术标准重建 submovement 切分；`FlickFairMetrics` 新增 `corrective_count`、`submovement_overlap`（corrective 与 initial 时间重叠比，高=流体，低=两段式）；advice 补 two_stage 规则用学术措辞。

### 6.3 Fitts's Law：peak_speed 的距离归一化（throughput）

**问题**：`peak_speed` 没做距离归一化，远距离 flick 天然更快，跨距离/场景比"快慢"不公平（与 `decel_smoothness` 同类公平性问题）。

**Fitts's Law**（Fitts 1954，Becker 核心引用）：

$$MT = a + b\cdot ID,\quad ID=\log_2(D/W+1)\ \text{(Shannon form)}$$

- D=运动距离，W=目标宽度，ID 单位 bits
- **Throughput TP = ID / MT**（bits/s）：速度-精度权衡的综合度量，**跨距离/跨设备可比**

**Effective target width**（MacKenzie/Zhai）：`We = 4.133·SDx`（端点空间标准差），用 We 算 IDe 更严谨，但需多次击中同类目标的端点分布。

**数据可行性**（flicking 场景）：
- MT = flick 时长（有，`segment_by_valleys`）
- D = flick 起终点角距离（有，`path_length_deg` 或起终点）
- W = 目标宽度 —— **`pan_tracker.detect_targets` 已能检测目标 bbox**，可取目标视角宽度作 W
- effective-width 版需端点分布 → 后续（属 PROGRESS [C]，需目标检测+多次样本）

**指标改动**：`FlickFairMetrics` 新增 `throughput`（nominal-W 版）`TP = log₂(D/W+1) / MT`；`compute_fair_metrics` 增加可选 `target_width_deg`；peak_speed 旁补 throughput，跨距离公平比较"发力能力"。

## 7. 来源

运动学：
- Minimum-jerk：[arXiv 2102.07459](https://arxiv.org/pdf/2102.07459)、[UCSD BENG221](https://isn.ucsd.edu/courses/beng221/problems/2011/project13.pdf)
- **Flash & Hogan 1985**（min-jerk 原始论文，速度 v(τ)∝30τ²(1-τ)²）：[J Neuroscience](https://www.jneurosci.org/content/jneuro/5/7/1688.full.pdf)、[Shadmehr Lab 推导](https://courses.shadmehrlab.org/Shortcourse/minimumjerk.pdf)
- Becker et al. 2020（aiming 减速段=成败最强信号）：[biorxiv](https://www.biorxiv.org/content/10.1101/2020.04.24.060533v1)
- **SPARC（运动平滑度金标准，频域弧长，无量纲）**：[Balasubramanian et al. 2012, IEEE TBME](https://pubmed.ncbi.nlm.nih.gov/22180502/)、[MATLAB 实现](https://github.com/siva82kb/smoothness/blob/master/matlab/SpectralArcLength.m)
- **Submovement 理论**：[Woodworth 1899 / Elliott 2001 百年综述, Psych Bull 127:342](https://doi.org/10.1037/0033-2909.127.3.342)、[Meyer et al. 1988 optimized submovements, Psych Rev 95:340](https://doi.org/10.1037//0033-295x.95.3.340)、[Novak et al. 2002 overlapping submovements, Exp Brain Res 144:351](https://doi.org/10.1007/s00221-002-1060-6)、[Schwartze/Rouse 2024 initial vs corrective 编码 + 切分标准, PMC11427045](https://pmc.ncbi.nlm.nih.gov/articles/PMC11427045/)
- **Fitts's Law / throughput**：[Fitts 1954, J Exp Psychol 47:381](https://doi.org/10.1037/h0055392)、[Wikipedia 综述](https://en.wikipedia.org/wiki/Fitts%27s_law)、[York/Mack effective target width](https://www.yorku.ca/mack/hhci2018.html)

社区共识：
- [r/FPSAimTrainer – Proper Static Clicking Form](https://www.reddit.com/r/FPSAimTrainer/comments/ycpcsg/proper_static_clicking_form/)（flick→micro）
- [r/FPSAimTrainer – Overflicking](https://www.reddit.com/r/FPSAimTrainer/comments/vap9uo/overflicking_even_after_constant_practice/)（降 sens 5–10%）
- [Voltaic Static Clicking Guide (VDIM)](https://www.youtube.com/watch?v=pOSQt1UEybM)（clean lines / deceleration）
- [Fluidity, Key to Static Clicking – Ep.6](https://www.youtube.com/watch?v=mOsBjfOGMuU)（流派）
- [Give me 12 Minutes to Fix your Flicking](https://www.youtube.com/watch?v=t_FrS6qpKpw)（speed management）
- [How To Flick Faster: Efficient Pathing – Aim Basics #13](https://www.youtube.com/watch?v=ZzoEu_MCHIA)（pathing）
- [CliffsNotes – Weakness-Specific Routines](https://www.cliffsnotes.com/study-notes/22370613)（1w4ts acc 90%+、场景分级）
- [Voltaic Scenarios DB](https://app.voltaic.gg/scenarios)

灵敏度：
- [Aiming.Pro – Best Sensitivity](https://aiming.pro/best-sensitivity-for-aiming)（28–43 cm/360）
- [r/FPSAimTrainer – sensitivity in aim trainers](https://www.reddit.com/r/FPSAimTrainer/comments/1jcanmw/)（precision 练习 -20% cm/360）
