# AI Aim Coach 理论底座

> 日期：2026-06-29
> 用途：AI aim coach 系统（`kovaak_tracker/coach/`）的**系统根基理论**。本文不是运动学指标理论（见 `aim-kinematics-research.md` §2/§6），而是支撑 coach **如何教学、如何设计反馈、如何编排训练**的跨域经典理论。
> 信源分级标准：**【学术·同行评审】** > **【学位论文/会议】** > **【权威社区共识】**（Voltaic 等有规模社群）> **【个人经验/视频】**（标「未经验证」）。
> 经久度标注：每条理论标「经久根基 / 可随版本更迭」。**只有学术根基的经典理论可作系统设计根基**；社区实操经验仅作内容填充，不进逻辑主干。

---

## 0. 与已有底座的关系（不重复，只引用）

`aim-kinematics-research.md` §6 已确立的运动学根基（**本文只引用、不重复**）：
- min-jerk（Flash & Hogan 1985）——减速段理想曲线
- Becker 2020——aiming 减速段是成败最强信号
- submovement 谱系（Woodworth 1899 / Meyer 1988 / Novak 2002）——两段式 vs 流体
- Fitts 1954——speed-accuracy tradeoff，throughput 跨距离归一化
- SPARC（Balasubramanian 2012）——运动平滑度频域金标准
- Schwartze/Rouse 2024——corrective vs initial submovement 神经编码差异

本文补的是**运动学习科学 + 反馈设计理论 + 训练编排理论**——回答的不是"瞄准动作本身怎么量化"，而是"系统该怎么教用户、反馈该怎么给、训练该怎么排"。

---

## 1. 运动技能习得：学习机制（支撑"系统该怎么理解进步"）

### 1.1 Fitts & Posner 三阶段模型（经典基底）+ Taylor & Ivry 2012 双过程修正

**Fitts & Posner 三阶段**【学术·同行评审·经久根基】（Fitts & Posner 1967, *Human Performance*）：
- **认知阶段（cognitive）**：学习者有意识地理解任务、构建策略，错误多、动作不稳。
- **联系阶段（associative）**：策略固化、错误减少、动作渐流畅，但仍有意识地调整。
- **自主阶段（autonomous）**：动作自动化，注意力可 freed 用于策略/情境判断。

**争议/异见（重要）**：Taylor & Ivry 2012【学术·同行评审·经久根基】提出**双过程并行模型**，挑战三阶段的"串行观"。核心论点：运动学习由**两个功能独立的并行过程**驱动，而非"策略阶段让位于自动阶段"的串行切换：

> "Whereas traditional approaches have favored serial models in which an initial strategy-based phase gives way to more automatized forms of control, it now seems that strategic and adaptive processes operate with considerable independence throughout learning, although the relative weight given the two processes will shift with changes in performance."
> — Taylor & Ivry 2012, *Annals of the NY Academy of Sciences* 1251:1-12（[PMC4330992](https://pmc.ncbi.nlm.nih.gov/articles/PMC4330992/)）

| 过程 | 误差信号 | 性质 |
|---|---|---|
| **显式策略（explicit strategy）** | 目标/结果误差（goal error）| 快、灵活、可认知调控、有意识 |
| **隐式适应（implicit adaptation）** | 预测/瞄准误差（aiming error → 内部模型重校准）| 慢、自动、小脑依赖、无意识 |

两个过程**始终并行运行**，权重随熟练度变化但不"交接"。后续 McDougle, Bond & Taylor 2015（J Neurophysiol）强化此框架。

**对本系统的含义**：
- 进步闭环（`coach/progress.py`）看到的"指标改善"是**两个过程的合成**——显式策略改善（用户改变瞄准意识/姿势）可能快但脆弱，隐式适应改善慢但稳健。**趋势线斜率不能简单归因"练对了"**。
- 系统设计上，教练讲解（`narrator.py`）给的是显式策略层面的反馈（KP/KR，见 §2），而进步可能同时来自隐式适应——后者不需要语言反馈，靠重复量本身。
- 三阶段模型仍可作为**用户分层画像的粗框架**（新手认知阶段 vs 老手联系阶段），但不应假设老手"已经不需要策略层反馈"。

**保守处理**：本文将三阶段作为粗框架引用，将双过程并行作为修正并列呈现，不强行统一——两者解释不同层面（阶段=宏观行为描述；双过程=微观学习机制）。

---

### 1.2 Deliberate Practice 与训练量上限

**Ericsson, Krampe & Tesch-Römer 1993**【学术·同行评审·经久根基】（*Psychological Review* 100:363-406，[PDF](https://graphics8.nytimes.com/images/blogs/freakonomics/pdf/DeliberatePractice(PsychologicalReview).pdf)，10000+ 引用）的**可作系统根基的经久结论**：

- **有效刻意练习的日量有经验上限**：技能学习文献显示**每日超过 4 小时无明显收益，超过 2 小时收益递减**（Welford 1968; Woodworth & Schlosberg 1954）。精英小提琴手日记周的练习**session 平均时长 80 分钟**。
- **过度练习有负面风险**："staleness"（状态钝化）、"overtraining"（过度训练）、最终"burnout"（倦怠）。最优长期练习需平衡 effort 与 recovery（睡眠、小憩、休闲）。

> 原文（p.368）："These studies show essentially no benefit from durations exceeding 4 hr per day and reduced benefits from practice exceeding 2 hr (Welford, 1968; Woodworth & Schlosberg, 1954)... The mean duration of practice sessions during the diary week... averaged 80 min."

**争议/异见**：
- **Macnamara & Hambrick 批评**（2014, *Psychological Science*）：刻意练习解释的方差仅 14-26%（vs Ericsson 阵营主张的更高比例），强调基因/工作记忆等因素。**但这批评针对"练习 vs 天赋的重要性"**，不针对"训练量上限"这一描述性发现——训练上限的具体数字仍站得住。
- **措辞修正**："biologically limited"（生物学硬上限）是过度表述。Ericsson 把 4hr/2hr 定位为**经验观察的最优上限**（来自 Welford 1968 技能学习文献 + 精英音乐家自选行为），不是已证实的生物学硬界。Ericsson 本人在 2019 Frontiers 反驳文中警告不要用单一指标外推硬上限。
- 某些领域**强度可能比时长更重要**（MacInnis & Gibala 2017 interval training）。

**对本系统的含义**：
- 进步闭环**不应鼓励"练得越多越好"**。`build_progress_report` 的趋势讲解若显示用户某指标停滞，应把"练习量已超收益递减区"作为诊断假设之一，而非默认"练不够"。
- 单 session 分析（`build_report`）的 narrator 可基于此理论，在检测到用户高频复测（如一天内多次）时提示"休息也是训练的一部分"。
- 这条理论**经久**（1993 原文 + 2013-2024 运动科学过度训练文献持续印证，如 Meeusen 2013 ECSS/ACSM 共识），可作系统设计根基。

---

### 1.3 Contextual Interference（情境干扰）：交错练习优于块状练习（长期保留）

**核心发现**【学术·同行评审·经久根基】：高情境干扰（interleaved/交错）练习在**习得阶段表现更差**，但**长期保留更好**。经典实验（Schorn & Knowlton 2020/2021，[CogSci 2020 PDF](https://cognitivesciencesociety.org/cogsci20/papers/0469/0469.pdf)，n=83，两天 SRT 任务）：

- Day 1：交错组准确率显著**更低**（M=92.34）vs 块状组（M=94.22; t(81)=2.013, p=.047）。
- Day 2 差异分：交错组改善（M=−0.313，负=遗忘更少）vs 块状组遗忘（M=0.726）；F(1,75)=39.539, p<.001, h²=0.274。

**理论解释**（"desirable difficulty"框架）：交错迫使学习者每次**从记忆重构运动计划**，强化提取通路；块状练习允许死记式重复，无需深度处理。

**争议/异见（重要，不可过度外推）**：
- **2023 Ammar 等系统综述 + 2024 Czyż 等元分析**（*Scientific Reports*, [PMC11237090](https://pmc.ncbi.nlm.nih.gov/articles/PMC11237090/)）发现在**应用/运动场域**CI 效应**微弱且高度异质**（SMD=0.23, p=0.24, I²≈90%，59 项研究仅 3 项评为中高质量）。
- 但**同一元分析确认实验室精细运动任务中 CI 是稳健现象**（pooled SMD=0.92, p<.001）——SRT 恰属此类。**效应存在性不被证伪，只是应用场域需谨慎**。
- Schorn & Knowlton 论文 Discussion 自述"不提供定论"（小样本限制），所以"empirically demonstrated"略强于作者原措辞。

**对本系统的含义**：
- 训练编排建议可引用 CI：**不要只刷一个场景**（块状），交错多个场景/距离/方向（高 CI）虽短期"手感差"但长期保留更好。这是 Voltaic weakness-specific playlist 设计的**理论背书**（但 Voltaic 本身是社区共识，见 §3）。
- **谨慎边界**：CI 效应在"真实游戏/复杂技能"场域证据弱。系统给出的"交错多场景"建议应限定为"基于实验室精细运动证据的合理推论"，不夸大为已证实的 aiming 场域效应。
- 这条理论**经久**（1980s 提出 → 2020s 元分析仍在讨论其边界条件），核心现象稳健，应用外推是争议焦点。

---

## 2. AI Coach 反馈设计理论（支撑"反馈该怎么给"）

### 2.1 KR vs KP 分类法（反馈内容设计根基）

**Gentile 1972 + 后续标准化**【学术·同行评审·经久根基】（Gentile 1972, *Quest* 17(1):3-23；综述见 [PMC8681883](https://pmc.ncbi.nlm.nih.gov/articles/PMC8681883/)）：

augmented feedback（增强反馈）分两类，是设计教练反馈的**基础分类法**：

> "The two key variants of AF are knowledge of results (KR), which gives information about the desired outcome (success/failure), and knowledge of performance (KP), which informs the learner about the movement characteristics and its quality... if a golf instructor tells his student that his shot went straight into the right rough, it is KR; however, if he says that the student is short on his backswing, that is KP."
> — [PMC8681883](https://pmc.ncbi.nlm.nih.gov/articles/PMC8681883/)

| 类型 | 内容 | 本系统对应 |
|---|---|---|
| **KR（Knowledge of Results）** | 结果成功/失败 | 命中率、accuracy、flick 是否命中 |
| **KP（Knowledge of Performance）** | 动作质量特征 | linearity、sparc、peak_position、reverse_ratio、减速段形态 |

**经久度**：50+ 年教学经典，**教科书级标准化**（Schmidt & Lee 教材、UBC/Physiopedia 课程统一采用），无"过时"批判。是教练反馈设计的**最稳根基**。

**对本系统的含义**：
- `coach/diagnosis.py` 的根因链三层（症状→物理→训练）天然对应：**症状层=KR**（"你减速段占 75% 在蹭"是结果描述），**物理层=KP**（"张力释放不平滑"是动作质量诊断）。系统实质上**同时给 KR 和 KP**，这是优于纯 KR（如游戏自带命中率）的设计。
- `narrator.py` 的讲解应**两者都覆盖**：先 KR（用户能感知的结果）再 KP（用户感知不到的动作质量），这是运动学习理论支持的反馈结构。
- **争议**：KP 内容如何呈现有应用层辩论（descriptive vs prescriptive KP），但**分类法本身无争议**。

---

### 2.2 Guidance Hypothesis（引导假说）：反馈太频繁反而损害学习

**Salmoni, Schmidt & Walter 1984**【学术·同行评审·经久根基】（*J Exp Psychol* / *Canadian J Psychology* 38(3):225-260，[PubMed 6399752](https://pubmed.ncbi.nlm.nih.gov/6399752/)；综述见 [PMC1780106](https://pmc.ncbi.nlm.nih.gov/articles/PMC1780106/)）：

> "According to the guidance hypothesis, augmented information can have negative effects on motor skill learning if it is provided too frequently or in a form that is too easy to use... the learner becomes dependent on KR when it is presented too frequently... the learner performs effectively when KR is available but not when it is removed."
> — [PMC1780106](https://pmc.ncbi.nlm.nih.gov/articles/PMC1780106/)

**核心论点**：augmented feedback（KR）在习得期**提升表现**，但**太频繁会损害长期学习/保留**，因为学习者产生**依赖**，停止处理任务内在反馈（本体感觉、视觉）。

**机制**：KR 被当作任务的"必需部分"，学习者**当 KR 在时表现好，KR 移除时崩溃**——因为没发展出内部错误检测机制。

**争议/异见**：
- **Marschall, Bund & Wiemeyer 2007 元分析**（*Bewegung und Training*）警告 guidance hypothesis 可能被"基于单个结果过度解释"。但**其元分析本身确认核心效应存在**："increased feedback frequency results in immediate benefits during acquisition performance and decrements in performance during delayed retention tests"——批评的是**幅度/过度解释**，不是**存在性**。
- 本系统的场景是**异步分析**（用户练完→上传→分析→讲解），不是实时伴随反馈。实时反馈（如游戏内 HUD 实时显示 sparc）才最容易触发依赖；异步反馈风险较低。**但原理仍适用**：如果系统每次复测都给详尽 KP，用户可能依赖系统诊断而非发展自我觉察。

**对本系统的含义**：
- **反馈频次设计**：进步闭环（`build_progress_report`）不应鼓励高频复测。**指导用户间隔练习**（如每周 2-3 次而非每天）既是 §1.2 训练量上限的要求，也符合 guidance hypothesis——给隐式适应和自我觉察留空间。
- **反馈褪除（fading）**：长期设计上，系统应**逐渐减少详尽 KP、增加"你自己觉察到了什么"的引导**（Socratic 式，见 §2.3）。这是 guidance hypothesis 的直接应用——但当前系统是单向讲解（A 形态），褪除设计属多轮对话（B 形态）后续。
- 这条理论**经久**（1984 提出，2023-2025 文献持续引用），是**反馈设计的最稳根基之一**。

---

### 2.3 Socratic / 对话式教练（多轮对话 B 的理论支撑）

**运动学习 + HCI 跨域**【权威社区共识（教练学）/ 无单一经典学术源头·经久度中】：

Socratic 教练（苏格拉底式提问）在运动学习语境的核心思想：**教练通过提问引导学习者自己觉察问题、构建策略**，而非单向灌输答案。这对应：
- **Guidance hypothesis 的褪除延伸**（§2.2）：减少直接 KP → 增加提问。
- **双过程模型（§1.1）的显式策略层**：提问激活显式策略过程，直接告知答案则跳过策略构建。

**经久度与信源**：
- Socratic method 本身**经久**（哲学根基千年），但**"对话式运动教练如何设计"缺乏单一经典学术范式**——更多散见于教练学（pedagogy）文献与 HCI 的对话系统研究。
- 本系统的多轮对话（B 形态）设计**目前未实现**（spec 标注"需先补瞄准社区理论"）。本文不深入设计，仅标注理论锚点：**Socratic 教练 + guidance hypothesis 褪除**是 B 形态的理论根基。

**对本系统的含义**：
- 当前 A 形态（单向讲解）是**起点**，符合新手认知阶段（§1.1）需要明确指导的需求。
- B 形态（多轮对话）应**随用户熟练度增加 Socratic 比重**——这与双过程模型"权重随熟练度变化"一致：新手需要显式 KP，老手更需要被引导自我觉察（激活隐式适应的元认知监控）。
- **争议**：部分教练学派主张"永远直接给答案"（效率优先），Socratic 派主张"提问优于告知"（学习深度优先）。本系统不强行统一，**按熟练度自适应**是合理折中（符合 §1.1 双过程权重变化）。

---

## 3. 瞄准实操经验理论（方向1：社区为主，标"易过时"）

> **信源级别声明**：本节理论**几乎无硬学术支撑**，来自 Voltaic、r/FPSAimTrainer、KovaaK 社区等有规模社群的**共识**。标【权威社区共识·易随游戏版本更迭过时】或【个人经验/视频·未经验证】。**不进系统逻辑主干**，仅作 narrator 讲解内容和诊断画像标签（`coach/profiles.py` 典型集）的内容来源。

### 3.1 Voltaic 流派分类

【权威社区共识·易过时】来源：Voltaic VDIM guide、r/FPSAimTrainer 流派讨论、YouTube 教学视频。

| 流派 | 特征 | 学术映射（§1.1 `aim-kinematics-research.md` §6.2）|
|---|---|---|
| **Bardpill（两段式）** | flick→停→micro correction，discrete corrective submovements | discrete corrective submovements（primary 与 corrective 速度峰可分离，有明显谷）|
| **Zeonlo（流体派）** | corrective 与 primary 融合，连续减速，单峰 | overlapping submovements（Novak 2002），减速段即微调 |

**经久度评估**：流派词（Bardpill/Zeonlo）是**社区标签，随 KOL 更迭可能消失**；但其描述的运动学现象（两段式 vs 流体）有学术映射（submovement 理论），**现象经久，标签易过时**。本系统在 `coach/profiles.py` 用学术措辞（`two_stage` / `fluid`）而非社区词，正是此考虑。

**争议**：社区内两派各有拥趸，无定论孰优。学术层面 Novak 2002 显示 overlapping（流体）在快速运动中更常见，但**未证伪两段式**——两种都是合法运动控制策略，取决于精度需求与速度权衡。

### 3.2 Target Selection 策略 / Reaction / Overshoot 成因

【权威社区共识 + 个人经验·未经验证·易过时】：

- **1w6ts 路线规划 / cluster farming**【个人经验·未经验证】：社区玩家总结的目标选择优先级策略（如"先近后远""先 cluster 再散点"）。**无学术验证**，随场景设计变化。
- **reaction / anticipation 训练**【个人经验·未经验证】：社区认为反应速度可练，但**反应时的可训练性学术上有上限**（多数研究显示反应时训练收益小且有天花板）。
- **overshoot/undershoot 成因**【权威社区共识】：社区归因为 sens 过快 / 制动不足 / 视觉-运动延迟。**部分有学术映射**（§1.1 Fitts' law 的速度-精度权衡；§6.2 submovement corrective 不足），但社区归因常**过度简化**（如"降 sens 5-10%"作为万能处方，忽略发力-释放不对称的根因——见 `aim-kinematics-research.md` §4 灵敏度决策框架）。

**对本系统的含义**：
- 这些内容**仅作 narrator 讲解的"社区说法引用"层**，标注"未经验证/社区共识"，不作为诊断规则（`advice.py` 规则只用 §1-§2 学术理论 + `aim-kinematics-research.md` §2 黄金标准）。
- **target_selection / reaction / overshoot 指标当前未实现**（属 PROGRESS [C]，需目标检测），spec 明确列为后续二期。

### 3.3 灵敏度（cm/360）决策的进阶依据

【权威社区共识 + 部分生物力学推理·经久度中】：

社区通用推荐 **28-43 cm/360**（aiming.pro），tracking 偏快 20-25，arm flicking ~45，wrist ~24。**进阶依据**（社区+生物力学，非纯学术）：
- **wrist vs arm 发力**：低 sens（高 cm/360）依赖大肌肉群（arm）发力，疲劳高但控制精度高；高 sens 依赖 wrist，速度快但手抖放大。这是**生物力学常识**（杠杆原理 + 肌肉激活），但**"哪个最优"无硬学术答案**——取决于个人解剖、游戏类型、习惯。
- **sens 是放大器/缩小器，不是根因**（`aim-kinematics-research.md` §4 已确立）：制动失控的根因是发力-释放不对称的技术问题，调 sens 是辅助实验，**必须复测验证**。

**经久度**：生物力学推理（杠杆、肌肉激活）**经久**；具体推荐数值（28-43）**随游戏/外设生态变化**，属社区共识层。本系统 `meta.cm_per_360` 仅作上下文（narrator 讲"sens 48cm 偏快"），不作诊断根因。

---

## 4. 综合理论谱系图

```
运动学习机制（§1）
├── Fitts & Posner 三阶段 [宏观行为]  ←—— 修正 ——→  Taylor & Ivry 2012 双过程并行 [微观机制]
│                                                       ├── 显式策略 (goal error, 快, KP/KR 驱动)
│                                                       └── 隐式适应 (aiming error, 慢, 自动)
├── Deliberate Practice (Ericsson 1993)
│   └── 训练量上限 (4hr/2hr/80min) → 进步闭环不该鼓励过量练习
└── Contextual Interference (1980s→2024)
    └── 交错 > 块状（长期保留，实验室稳健/应用场域有争议）

反馈设计（§2）
├── KR vs KP 分类法 (Gentile 1972) ──→ 本系统根因链三层 = KR(症状) + KP(物理)
├── Guidance Hypothesis (Salmoni 1984)
│   └── 反馈太频繁损害学习 → 进步闭环间隔练习 + 未来 fading 褪除
└── Socratic 教练 (跨域, 无单一经典) ──→ B 形态多轮对话理论锚点（按熟练度增提问比重）

瞄准实操（§3，易过时，不进逻辑主干）
├── Voltaic 流派 (社区标签, 现象有学术映射) ──→ profiles.py 用学术措辞
├── Target selection/reaction/overshoot (未经验证) ──→ narrator 引用层, 非 diagnosis 规则
└── 灵敏度决策 (生物力学推理 + 社区数值) ──→ meta 上下文, 非根因

运动学根基 (已有, aim-kinematics-research.md §6, 本文引用)
├── min-jerk / Becker 2020 / SPARC / submovement / Fitts / Schwartze-Rouse 2024
└── → 指标设计与诊断信号 → advice.py 规则 → coach/diagnosis.py 根因链填充
```

---

## 被反驳的 claim（透明，对抗投票淘汰，未进文档主体）

deep-research 的 3 票对抗验证（2/3 反驳才淘汰）淘汰了 18 个 claim，多数是「过强版本」。关键几个（避免系统误用）：

- **多模态 > 单模态反馈**（0-3 全票淘汰）—— 系统不应默认「更多反馈渠道 = 更好」
- **同步（实时）> 终端反馈**（0-3 全票淘汰）—— 实时反馈非总更好（与 §2.2 guidance hypothesis 一致：实时反馈最易触发依赖）
- **Ericsson「单调收益假设」/「10 年规则」/ 精确方差百分比（29%/14%/61%）**（1-0 / 0-1 淘汰）—— 过强断言，不进系统
- **descriptive KP < KR alone**（0-0 未达共识）—— KP 内容设计（descriptive vs prescriptive）无定论
- **KR + prescriptive KP > KR alone（novice）**（0-1，方向支持但未达阈值）—— 趋势支持但证据不足

> **教训**：反馈理论里「更多 / 实时 / 多模态反馈总是更好」是过强断言，被证据否定。系统反馈设计应基于 §2 学术根基（KR/KP 分类 + guidance hypothesis），而非「更多即更好」。

---

## 5. 信源与经久度总表

| 理论 | 信源等级 | 经久度 | 争议 | 系统用途 |
|---|---|---|---|---|
| Fitts & Posner 三阶段 | 【学术·同行评审】 | 经久根基 | 双过程模型挑战串行观 | 用户粗分层（认知/联系/自主）|
| Taylor & Ivry 2012 双过程并行 | 【学术·同行评审】(PMC4330992) | 经久根基（2015 强化）| 串行 vs 并行 | 进步归因谨慎、反馈层设计 |
| Ericsson 1993 训练量上限 | 【学术·同行评审】(Psych Review, 10k+ 引) | 经久根基 | Macnamara 批评"重要性"，非"上限" | 进步闭环不过度鼓励练习量 |
| Contextual Interference | 【学术·同行评审】(CogSci 2020 + 2024 元分析) | 核心稳健，应用外推争议 | 实验室强/应用场域弱 | 训练编排（交错多场景）|
| KR vs KP (Gentile 1972) | 【学术·同行评审】 | 经久根基（50+ 年经典，无批判）| 无（应用层有 descriptive vs prescriptive 之辩）| 根因链三层 = KR+KP |
| Guidance Hypothesis (Salmoni 1984) | 【学术·同行评审】 | 经久根基（40+ 年，持续引用）| Marschall 2007 批幅度非存在性 | 反馈频次/fading 设计 |
| Socratic 教练 | 【跨域·无单一经典】 | 经久度中（哲学经久，运动教练范式散）| 直接告知 vs 提问引导 | B 形态多轮对话锚点 |
| Voltaic 流派 | 【权威社区共识】 | 标签易过时，现象经久 | 两派无定论 | profiles.py 用学术措辞 |
| Target selection/reaction/overshoot | 【个人经验·未经验证】 | 易过时 | 无学术验证 | narrator 引用层 |
| 灵敏度决策 | 【社区共识+生物力学】 | 数值易过时，原理经久 | 无定论 | meta 上下文 |

---

## 6. 来源

### 学术（同行评审）

- **Taylor & Ivry 2012** "The role of strategies in motor learning," *Annals NY Acad Sci* 1251:1-12：[PMC4330992](https://pmc.ncbi.nlm.nih.gov/articles/PMC4330992/) · [Ivry Lab PDF](https://ivrylab.berkeley.edu/files/organized_pubs_pdfs/2012_taylor_ivryannals_of_the_.pdf) · DOI 10.1111/j.1749-6632.2011.06430.x
  - 强化：McDougle, Bond & Taylor 2015, *J Neurophysiol*（显式/隐式 fast/slow process）
- **Ericsson, Krampe & Tesch-Römer 1993** "The Role of Deliberate Practice in the Acquisition of Expert Performance," *Psychological Review* 100:363-406：[NYTimes PDF](https://graphics8.nytimes.com/images/blogs/freakonomics/pdf/DeliberatePractice(PsychologicalReview).pdf)
  - 批评：Macnamara, Hambrick & Oswald 2014, *Psychological Science*（刻意练习解释方差 14-26%）
  - 反驳：Ericsson 2019, *Frontiers in Psychology*：[doi.org/10.3389/fpsyg.2019.02396](https://www.frontiersin.org/journals/psychology/articles/10.3389/fpsyg.2019.02396/full)
  - 过度训练印证：Meeusen 2013 ECSS/ACSM 共识；Kreher & Schwartz 2012 [PMC review](https://pmc.ncbi.nlm.nih.gov/articles/PMC3295983/)
- **Schorn & Knowlton 2021**（CogSci 2020 → *Memory & Cognition* 49:1436-1452）：[CogSci 2020 PDF](https://cognitivesciencesociety.org/cogsci20/papers/0469/0469.pdf) · DOI 10.3758/s13421-021-01168-z
  - 应用场域争议：Czyż et al. 2024 *Scientific Reports* [PMC11237090](https://pmc.ncbi.nlm.nih.gov/articles/PMC11237090/)；Ammar et al. 2023 *ScienceDirect*
- **Gentile 1972** "A Working Model of Skill Acquisition," *Quest* 17(1):3-23（KR/KP 分类法源头）
  - 综述：[PMC8681883](https://pmc.ncbi.nlm.nih.gov/articles/PMC8681883/)
- **Salmoni, Schmidt & Walter 1984** "Knowledge of results and motor learning: A review and critical reappraisal," *Canadian J Psychology* 38(3):225-260：[PubMed 6399752](https://pubmed.ncbi.nlm.nih.gov/6399752/)
  - 综述：[PMC1780106](https://pmc.ncbi.nlm.nih.gov/articles/PMC1780106/)
  - 批评（幅度非存在）：Marschall, Bund & Wiemeyer 2007, *Bewegung und Training*
- **Fitts & Posner 1967** *Human Performance*（三阶段模型经典源头）
- Harris & Wolpert 1998 "Signal-dependent noise determines motor planning," *Nature* 394:780-784（min-jerk 是派生后果，非 CNS 组织原则；信号依赖噪声下最小化末端方差）— 补充 `aim-kinematics-research.md` §6.1 的理论深化

### 社区共识（易过时，标注）

- [Voltaic VDIM Guide](https://www.youtube.com/watch?v=pOSQt1UEybM)（流派 / clean lines）
- [r/FPSAimTrainer](https://www.reddit.com/r/FPSAimTrainer/)（target selection / overshoot 成因）
- [Aiming.Pro – Best Sensitivity](https://aiming.pro/best-sensitivity-for-aiming)（cm/360 推荐）
- 详见 `aim-kinematics-research.md` §7 社区共识来源

### 个人经验/视频（最弱信源，未经验证）

- YouTube 瞄准教学视频（Fluidity Ep.6、Give me 12 Minutes、Aim Basics #13 等）——见 `aim-kinematics-research.md` §7
