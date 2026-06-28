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
| linearity | 低 | <0.12 | >0.15 减速抖动 |
| reverse 占比 | 0 | <0.18 | >0.22 减速段锯齿/反复修正 |

## 3. 诊断信号 → 处方（advice.py 核心规则）

社区共识来源：r/FPSAimTrainer、r/aimlab、Voltaic VDIM guide、Steam KovaaK discussions。

| 诊断信号 | 阈值（初步） | 诊断陈述 | 处方 |
|---|---|---|---|
| decfrac 高 | >0.65 | 急加速 + 长减速，减速段在"蹭" | 果断减速一次到位；pasu / 1w4ts 练完整加减速；意识：flick→confirm |
| linearity 高 | >0.13 | 减速段抖动，张力释放不平滑 | clean lines；pasu；降速练制动；减速段当一次动作 |
| reverse 高 | >0.20 | 减速段锯齿/反复修正 | 转流体派（减速段即修正）；pasu；别 readjust |
| two_stage 高 | >20% | 两段式（Bardpill），flick→停→micro 有延迟 | 流体派：flick 与 micro 连续，减速段即微调 |
| peak °/s 远低于参考 | <参考×0.7 | 甩得偏慢，发力不足/手腕主导 | 练 arm 发力与动态速度；Tile Frenzy / speed 类 |
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

## 6. 来源

运动学：
- Minimum-jerk：[arXiv 2102.07459](https://arxiv.org/pdf/2102.07459)、[UCSD BENG221](https://isn.ucsd.edu/courses/beng221/problems/2011/project13.pdf)
- Becker et al. 2020（aiming 减速段=成败最强信号）：[biorxiv](https://www.biorxiv.org/content/10.1101/2020.04.24.060533v1)

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
