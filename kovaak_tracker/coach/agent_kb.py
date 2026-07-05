"""Agent knowledge base for progressive disclosure.

Sliced from 5 source docs. Each chunk has source_ref + source_level for anti-hallucination.
See docs/superpowers/specs/2026-07-05-aiming-coach-agent-design.md §4 for slicing convention.
"""
from __future__ import annotations
from typing import Literal, TypedDict


class KnowledgeChunk(TypedDict):
    topic: str
    signal: str | None
    source_ref: str
    source_level: Literal[
        "academic_peer_reviewed",
        "community_consensus",
        "personal_experience_unverified",
    ]
    text: str


KB: list[KnowledgeChunk] = [
    # ============================================================
    # 源文档 1：docs/aim-kinematics-research.md（运动学理论 + advice 阈值）
    # ============================================================
    {
        "topic": "kinematics_thresholds",
        "signal": None,
        "source_ref": "aim-kinematics-research.md §2",
        "source_level": "academic_peer_reviewed",
        "text": (
            "运动学黄金标准（advice.py 阈值的来源）：\n"
            "- Minimum-jerk trajectory（Flash & Hogan 1985）：点对点运动速度是对称钟形，峰在中点 50%。\n"
            "- Becker 2020 aiming 实测：aim 速度曲线不对称，减速段 > 加速段——因为有命中精度的末端需求，减速段运动学是预测成败的最强信号。\n\n"
            "指标健康区间（advice 阈值据此设定）：\n"
            "| 指标 | min-jerk 理想 | 健康 aim | 偏离 = 问题 |\n"
            "| peak_position % | 50 | 35–50 | <30 加速过急/减速过长；>60 加速拖沓 |\n"
            "| decfrac | 0.50 | 0.50–0.65 | >0.7 减速段蹭；<0.4 减速不足/撞 |\n"
            "| linearity（匀减速线性度）| 低 | <0.12 | >0.15 制动不匀（减速抖动看 sparc）|\n"
            "| sparc | 高（≈0）| >−0.5 | <−0.5 减速抖动、张力释放不平滑 |\n"
            "| reverse 占比 | 0 | <0.18 | >0.22 减速段锯齿/反复修正 |"
        ),
    },
    {
        "topic": "min_jerk_vs_uniform_decel",
        "signal": "linearity high",
        "source_ref": "aim-kinematics-research.md §6.1",
        "source_level": "academic_peer_reviewed",
        "text": (
            "linearity 与 SPARC 是两个独立构造，归因容易混淆：\n"
            "- min-jerk 速度曲线（Flash & Hogan 1985）对称钟形、峰在 τ=0.5，减速段是平滑曲线，不是直线。\n"
            "- 匀减速（直线拟合的隐含理想）= 恒定负加速度 = 加速度阶跃 = jerk 不连续，在平滑性谱系里反而较不平滑。\n"
            "- 所以 linearity 度量的是「制动线性度 / 接近匀减速的程度」，理论锚点是 constant-deceleration，不是 min-jerk。一个真正平滑的 min-jerk 减速反而会偏离匀减速直线，得到较差的 linearity 分数。\n"
            "- linearity 不适合作「减速抖动 / 张力释放不平滑」的代理——抖动应改用 SPARC（Balasubramanian 2012，频域弧长、无量纲、跨速度/跨人公平）。\n\n"
            "落地：advice.py 的 linearity 诊断语义是「制动不匀」，抖动由 sparc 判定。"
        ),
    },
    {
        "topic": "sparc",
        "signal": "sparc low",
        "source_ref": "aim-kinematics-research.md §6.1",
        "source_level": "academic_peer_reviewed",
        "text": (
            "SPARC（Spectral Arc Length, Balasubramanian 2012, IEEE TBME）是运动平滑度金标准：\n"
            "- 频域度量：速度幅度谱的归一化弧长。\n"
            "- 无量纲，跨速度/跨人公平——这是 decel_smoothness 与 peak_speed 强相关（corr=0.76）不公平问题的正解。\n"
            "- 对噪声鲁棒、敏感于平滑度变化，优于 dimensionless jerk (DLJ)；现代运动控制/康复的金标准。\n\n"
            "advice 阈值：sparc < −0.5 判为「减速段平滑度差、张力释放抖」。处方方向：clean lines、pasu，把减速段当一次独立动作。"
        ),
    },
    {
        "topic": "submovement",
        "signal": "two_stage",
        "source_ref": "aim-kinematics-research.md §6.2",
        "source_level": "academic_peer_reviewed",
        "text": (
            "Submovement 理论谱系（Becker 2020 核心引用）：\n"
            "- Woodworth 1899 两阶段：initial（ballistic，前馈）+ corrective（视觉反馈修正）。\n"
            "- Meyer 1988 optimized submovements：优化 primary+secondary 相对时长，最小化总运动时间保持精度 → 直接关联 Fitts' Law。\n"
            "- Novak 2002 overlapping submovements：快速运动中 submovements 重叠融合 = 流体；可分离 = 两段式。\n"
            "- Flash & Hogan 1985：每个 submovement 都是钟形速度曲线。\n"
            "- Schwartze/Rouse 2024：corrective 与 initial 在 M1 用不同神经子空间编码，corrective gain 更高（1.14–1.36×）。\n\n"
            "社区流派映射：Bardpill（两段式）= discrete corrective submovements；Zeonlo（流体）= overlapping submovements。指标上用 corrective_count 与 submovement_overlap（重叠比，高=流体，低=两段式）。"
        ),
    },
    {
        "topic": "fitts",
        "signal": "throughput low",
        "source_ref": "aim-kinematics-research.md §6.3",
        "source_level": "academic_peer_reviewed",
        "text": (
            "Fitts's Law（Fitts 1954）：MT = a + b·ID，ID = log₂(D/W+1)（Shannon form）。\n"
            "- D = 运动距离，W = 目标宽度，ID 单位 bits。\n"
            "- Throughput TP = ID / MT（bits/s）：速度-精度权衡的综合度量，跨距离/跨设备可比。\n"
            "- Effective target width（MacKenzie/Zhai）：We = 4.133·SDx，用 We 算 IDe 更严谨，但需多次击中同类目标的端点分布。\n\n"
            "为什么需要 throughput：peak_speed 没做距离归一化，远距离 flick 天然更快，跨距离/场景比「快慢」不公平（与 decel_smoothness 同类公平性问题）。throughput 在 flicking 场景数据可行——MT = flick 时长，D = 起终点角距离，W = 目标视角宽度（pan_tracker.detect_targets 已能检测）。"
        ),
    },
    {
        "topic": "sensitivity",
        "signal": None,
        "source_ref": "aim-kinematics-research.md §4",
        "source_level": "community_consensus",
        "text": (
            "灵敏度决策框架（cm/360）：\n"
            "- 通用推荐 28–43 cm/360（aiming.pro）；tracking 偏快 20–25（aimer7）；arm flicking ~45，wrist ~24。\n"
            "- <25（偏快 wrist）：制动难、手抖放大，过冲持续 → 建议 +5–10% cm/360。\n"
            "- 25–45（主流健康）：一般无需动。\n"
            "- >50（arm aimer）：控制精度高、过冲少；速度靠 arm 能力，慢则练发力。\n\n"
            "铁律：sens 是放大器/缩小器，不是根因。制动失控的根因是发力-释放不对称的技术问题；调 sens 是辅助实验，必须复测验证（降 sens 后 linearity/reverse 应下降，否则调回）。"
        ),
    },
    {
        "topic": "scenarios",
        "signal": None,
        "source_ref": "aim-kinematics-research.md §5",
        "source_level": "community_consensus",
        "text": (
            "场景处方库（Voltaic）：\n"
            "- 1w4ts Voltaic：整体 static + 减速 + pathing（benchmark），acc 90%+ 为目标。\n"
            "- 1w4ts 30% larger：减速精度，减速段质量专项。\n"
            "- Pasu：加速 + 减速完整度、干净，单目标切换，练完整 flick。\n"
            "- Multiclick：点击精度、micro correction，落点精度。\n"
            "- linetrace：直线 flick、path efficiency（path_efficiency 低时专项）。\n"
            "- Tile Frenzy：基本功、speed，速度/发力。\n"
            "- Voltaic weakness-specific playlist：综合诊断后选针对性 flick 弱点。\n\n"
            "核心口诀（Voltaic VDIM）：Clean lines. Clean movements. Deceleration after a big flick."
        ),
    },

    # ============================================================
    # 源文档 2：docs/coach-theory-foundation.md（教练理论）
    # ============================================================
    {
        "topic": "fitts_posner",
        "signal": None,
        "source_ref": "coach-theory-foundation.md §1.1",
        "source_level": "academic_peer_reviewed",
        "text": (
            "Fitts & Posner 1967 三阶段模型【学术·同行评审·经久根基】：\n"
            "- 认知阶段（cognitive）：有意识理解任务、构建策略，错误多、动作不稳。\n"
            "- 联系阶段（associative）：策略固化、错误减少、动作渐流畅，仍有意识调整。\n"
            "- 自主阶段（autonomous）：动作自动化，注意力可释放用于策略/情境判断。\n\n"
            "Taylor & Ivry 2012（PMC4330992）双过程并行修正【学术·同行评审·经久根基】：运动学习由两个功能独立的并行过程驱动，而非「策略阶段让位于自动阶段」的串行切换——\n"
            "- 显式策略（explicit strategy）：goal error 驱动，快、灵活、可认知调控、有意识。\n"
            "- 隐式适应（implicit adaptation）：aiming error → 内部模型重校准，慢、自动、小脑依赖、无意识。\n"
            "两个过程始终并行，权重随熟练度变化但不「交接」（McDougle, Bond & Taylor 2015 强化）。\n\n"
            "对系统的含义：进步闭环看到的指标改善是两个过程的合成——显式策略改善可能快但脆弱，隐式适应改善慢但稳健，趋势线斜率不能简单归因「练对了」。"
        ),
    },
    {
        "topic": "deliberate_practice",
        "signal": None,
        "source_ref": "coach-theory-foundation.md §1.2",
        "source_level": "academic_peer_reviewed",
        "text": (
            "Ericsson, Krampe & Tesch-Römer 1993（Psychological Review 100:363-406，10000+ 引用）的可作系统根基结论：\n"
            "- 有效刻意练习的日量有经验上限：每日超过 4 小时无明显收益，超过 2 小时收益递减（Welford 1968; Woodworth & Schlosberg 1954）。\n"
            "- 精英小提琴手日记周的练习 session 平均时长 80 分钟。\n"
            "- 过度练习有负面风险：staleness（状态钝化）、overtraining、最终 burnout（倦怠）。最优长期练习需平衡 effort 与 recovery（睡眠、小憩、休闲）。\n\n"
            "原文（p.368）：\"...essentially no benefit from durations exceeding 4 hr per day and reduced benefits from practice exceeding 2 hr... averaged 80 min.\"\n\n"
            "异见与措辞修正：Macnamara & Hambrick 2014 批评刻意练习解释方差仅 14-26%，但批评的是「重要性」非「上限」。「biologically limited」是过度表述——4hr/2hr 是经验观察的最优上限，非已证实的生物学硬界（Ericsson 本人在 2019 Frontiers 反驳文中警告不要外推）。\n\n"
            "系统含义：进步闭环不应鼓励「练得越多越好」。若指标停滞，应把「练习量已超收益递减区」作为诊断假设之一，而非默认「练不够」。"
        ),
    },
    {
        "topic": "contextual_interference",
        "signal": None,
        "source_ref": "coach-theory-foundation.md §1.3",
        "source_level": "academic_peer_reviewed",
        "text": (
            "Contextual Interference（情境干扰）【学术·同行评审·经久根基】：高情境干扰（interleaved/交错）练习在习得阶段表现更差，但长期保留更好。\n"
            "经典实验 Schorn & Knowlton 2020/2021（CogSci 2020, n=83，两天 SRT 任务）：\n"
            "- Day 1：交错组准确率显著更低（M=92.34）vs 块状组（M=94.22; t(81)=2.013, p=.047）。\n"
            "- Day 2 差异分：交错组改善（M=−0.313）vs 块状组遗忘（M=0.726）；F(1,75)=39.539, p<.001, h²=0.274。\n\n"
            "理论解释（desirable difficulty）：交错迫使每次从记忆重构运动计划，强化提取通路；块状允许死记式重复。\n\n"
            "应用边界（重要）：2024 Czyż 等元分析（Scientific Reports, PMC11237090）发现在应用/运动场域 CI 效应微弱且高度异质（SMD=0.23, p=0.24, I²≈90%），但实验室精细运动任务中是稳健现象（pooled SMD=0.92, p<.001）——SRT 恰属此类。\n\n"
            "系统含义：训练编排可引用 CI——不要只刷一个场景，交错多场景虽短期手感差但长期保留更好。但应限定为「基于实验室精细运动证据的合理推论」，不夸大为已证实的 aiming 场域效应。"
        ),
    },
    {
        "topic": "kr_kp",
        "signal": None,
        "source_ref": "coach-theory-foundation.md §2.1",
        "source_level": "academic_peer_reviewed",
        "text": (
            "KR vs KP 分类法（Gentile 1972, Quest 17(1):3-23；综述 PMC8681883）【学术·同行评审·经久根基】：\n"
            "augmented feedback（增强反馈）分两类——\n"
            "- KR（Knowledge of Results）：结果成功/失败。例：命中率、accuracy、flick 是否命中。Golf 教练说「球飞进右边长草区」是 KR。\n"
            "- KP（Knowledge of Performance）：动作质量特征。例：linearity、sparc、peak_position、reverse_ratio、减速段形态。Golf 教练说「你挥杆上杆不够」是 KP。\n\n"
            "经久度：50+ 年教学经典，教科书级标准化（Schmidt & Lee 教材、UBC/Physiopedia 统一采用），无「过时」批判。是教练反馈设计的最稳根基。\n\n"
            "系统含义：diagnosis.py 根因链三层（症状→物理→训练）天然对应——症状层=KR（「你减速段占 75% 在蹭」是结果描述），物理层=KP（「张力释放不平滑」是动作质量诊断）。系统实质上同时给 KR + KP，优于纯 KR（如游戏自带命中率）。narrator 应两者都覆盖：先 KR（用户能感知的结果）再 KP（用户感知不到的动作质量）。"
        ),
    },
    {
        "topic": "guidance_hypothesis",
        "signal": None,
        "source_ref": "coach-theory-foundation.md §2.2",
        "source_level": "academic_peer_reviewed",
        "text": (
            "Guidance Hypothesis（引导假说）Salmoni, Schmidt & Walter 1984（Canadian J Psychology 38(3):225-260，PubMed 6399752；综述 PMC1780106）【学术·同行评审·经久根基】：\n\n"
            "核心论点：augmented feedback（KR）在习得期提升表现，但太频繁会损害长期学习/保留——学习者产生依赖，停止处理任务内在反馈（本体感觉、视觉）。KR 被当作任务的「必需部分」，KR 在时表现好，KR 移除时崩溃，因为没发展出内部错误检测机制。\n\n"
            "原文：\"...augmented information can have negative effects on motor skill learning if it is provided too frequently or in a form that is too easy to use... the learner becomes dependent on KR...\"\n\n"
            "异见：Marschall, Bund & Wiemeyer 2007 元分析警告 guidance hypothesis 可能被「基于单个结果过度解释」，但其元分析本身确认核心效应存在：\"increased feedback frequency results in immediate benefits during acquisition performance and decrements in performance during delayed retention tests\"——批评的是幅度/过度解释，不是存在性。\n\n"
            "系统含义（异步分析场景风险较低但原理仍适用）：进步闭环不应鼓励高频复测；指导用户间隔练习（每周 2-3 次而非每天），既符合 Ericsson 训练量上限，也符合 guidance hypothesis——给隐式适应和自我觉察留空间。长期设计上应逐渐减少详尽 KP、增加「你自己觉察到了什么」的 Socratic 引导（fading 褪除）。"
        ),
    },
    {
        "topic": "socratic",
        "signal": None,
        "source_ref": "coach-theory-foundation.md §2.3",
        "source_level": "community_consensus",
        "text": (
            "Socratic 教练（苏格拉底式提问）【跨域·权威社区共识·经久度中】：\n"
            "核心思想：教练通过提问引导学习者自己觉察问题、构建策略，而非单向灌输答案。对应：\n"
            "- Guidance hypothesis 的褪除延伸（§2.2）：减少直接 KP → 增加提问。\n"
            "- 双过程模型（§1.1）的显式策略层：提问激活显式策略过程，直接告知答案则跳过策略构建。\n\n"
            "经久度与信源：Socratic method 本身经久（哲学根基千年），但「对话式运动教练如何设计」缺乏单一经典学术范式——散见于教练学（pedagogy）文献与 HCI 对话系统研究。\n\n"
            "异见：部分教练学派主张「永远直接给答案」（效率优先），Socratic 派主张「提问优于告知」（学习深度优先）。系统不强行统一，按熟练度自适应是合理折中——新手认知阶段需要明确 KP（A 形态单向讲解），老手联系/自主阶段更需要被引导自我觉察（激活隐式适应的元认知监控，B 形态多轮对话）。"
        ),
    },
    {
        "topic": "voltaic_community_layer",
        "signal": None,
        "source_ref": "coach-theory-foundation.md §3",
        "source_level": "community_consensus",
        "text": (
            "瞄准实操经验层（信源级别声明：几乎无硬学术支撑，来自 Voltaic、r/FPSAimTrainer、KovaaK 社区共识，标【易随版本更迭过时】，仅作 narrator 讲解内容和 profile 标签来源，不进诊断规则）：\n\n"
            "- Voltaic 流派：Bardpill（两段式）= discrete corrective submovements；Zeonlo（流体）= overlapping submovements。流派词是社区标签随 KOL 更迭可能消失，但描述的运动学现象有学术映射——profiles.py 用学术措辞（two_stage / fluid）而非社区词。\n"
            "- Target selection / cluster farming：社区总结的目标选择优先级（先近后远、先 cluster 再散点），无学术验证。\n"
            "- Reaction / anticipation 训练：社区认为可练，但反应时的可训练性学术上有上限。\n"
            "- Overshoot/undershoot 成因：社区归因为 sens 过快 / 制动不足 / 视觉-运动延迟，常过度简化（如「降 sens 5-10%」作万能处方，忽略发力-释放不对称的根因）。\n"
            "- cm/360 进阶依据：低 sens 依赖大肌肉群，疲劳高但控制精度高；高 sens 依赖 wrist，速度快但手抖放大——生物力学常识但「哪个最优」无硬学术答案。"
        ),
    },

    # ============================================================
    # 源文档 3：docs/coach-community-frontier.md（社区前沿）
    # ============================================================
    {
        "topic": "voltaic_s5",
        "signal": None,
        "source_ref": "coach-community-frontier.md §1.1",
        "source_level": "community_consensus",
        "text": (
            "Voltaic Season 5 分类法（截至 2025-2026）：3 支柱 × 3 子类 = 9 类，并新增第三类「hybrid」子类（linear=clicking, control=tracking, stability=switching）桥接传统子类。\n\n"
            "完整映射：\n"
            "- clicking = dynamic / static / linear\n"
            "- tracking = precise / control / reactive\n"
            "- switching = stability / evasive / speed\n\n"
            "信源【权威社区共识】：Voltaic 官方 S5 blog、r/Voltaic S5 benchmarks、KovaaK's S5 focus playlists。\n"
            "经久度：高（Voltaic 当前官方分类，S5 期内稳定）。\n"
            "系统含义：narrator 词汇用这套分类（clicking/tracking/switching + 子类），profile 标签可对齐。"
        ),
    },
    {
        "topic": "three_pillars",
        "signal": None,
        "source_ref": "coach-community-frontier.md §1.2",
        "source_level": "community_consensus",
        "text": (
            "三支柱 + 颜色编码（社区共识，无争议）：\n"
            "瞄准技能分类是三支柱——clicking / tracking / target-switching，Voltaic Weakness-Specific Routines 2.0 全程颜色编码：\n"
            "- 红 = clicking\n"
            "- 蓝 = tracking\n"
            "- 紫 = switching\n\n"
            "S5（2024-2025）仍沿用，少数异见把「Movement Aiming」当第四支柱。\n"
            "系统含义：UI/narrator 可用颜色约定，三支柱是社区通用语。"
        ),
    },
    {
        "topic": "top_player_split",
        "signal": None,
        "source_ref": "coach-community-frontier.md §1.3",
        "source_level": "personal_experience_unverified",
        "text": (
            "顶级玩家子技能分化（单样本自述 + Voltaic 分类排序佐证，经久度中）：\n\n"
            "Celestial 级世界 #1 Voltaic precise-tracking 记录保持者 VT Matty（Corporate Serf）自述是 smoothness/stability 型玩家，明确 static clicking 是其弱项。支持「smoothness/stability 瞄准 vs 爆发速度/reactive 瞄准是不同子技能，很少同时顶峰」。\n\n"
            "信源【个人经验/视频】：Corporate Serf YouTube \"How to Rank Up in Voltaic\"。\n"
            "系统含义：profile 可分子型（smoothness 型 vs speed 型）；解释为什么用户某类强某类弱是正常的——不强行全能。"
        ),
    },
    {
        "topic": "static_clicking_three_step",
        "signal": "two_stage",
        "source_ref": "coach-community-frontier.md §1.6",
        "source_level": "community_consensus",
        "text": (
            "经典 Voltaic static clicking 三步【权威社区共识，多年教学稳定 2022-2026】：每次目标交战分解为——\n"
            "1. big flick（arm 驱动）\n"
            "2. micro-correction（指尖/手腕）—— 区分 clean vs sloppy 的精度关键\n"
            "3. hit-confirm（修正落地后才点）\n\n"
            "异见：少数「one-flick purity」流派（HnA 场景）；主流共识是 micro-correction 是预期的，只有过度反复修正才是缺陷。社区把「clean vs sloppy」操作化为「一次果断修正 vs 反复抖动」。\n\n"
            "学术呼应：与 coach-theory-foundation.md 的 corrective submovement 直接对应——社区「micro-correction」= 学术「corrective submovement」。narrator 技术描述和处方理由可同时引用两层；two_stage 画像（discrete corrective）有社区对应。"
        ),
    },
    {
        "topic": "community_practice",
        "signal": None,
        "source_ref": "coach-community-frontier.md §实践手册",
        "source_level": "community_consensus",
        "text": (
            "static clicking 技术社区共识（r/FPSAimTrainer 多帖汇聚）：\n"
            "- 两阶段动作：fast flick（arm 驱动）→ slow micro-correction（wrist/指尖）。两动作先分开练到自动化，再合一。\n"
            "- micro 用 wrist/指尖（fine motor）—— arm 只负责到位，wrist 负责精修，不要再 arm 甩。\n"
            "- click 时机：micro-correction 落地后才点（hit-confirm），不要边甩边点。\n"
            "- 平滑：当 tracking 练——快接近、慢落地、再点，全程平滑。\n"
            "- 降 sens 助 micro：lower sens → smoother stop + faster microing（static 推荐 40+ cm/360）。\n"
            "- 张力：proper form 随时间自然降张力——不是硬放松，是技术对了张力自然降。\n"
            "- 流派分歧：underflick 派（欠冲再修上去）vs overflick 派（过冲收回），主流是「到位 + 微修」。\n"
            "- 常见错误：flick preparation 差 + 过度平滑掩盖大欠冲。"
        ),
    },
    {
        "topic": "vdim_orchestration",
        "signal": None,
        "source_ref": "coach-community-frontier.md §实践手册",
        "source_level": "community_consensus",
        "text": (
            "VDIM（Voltaic Daily Improvement Method）训练编排——Lowgravity56（VT 成员）创建：\n"
            "- 每日隔离一种技术：每天一个 playlist 集中练一类（clicking/tracking/switching 按天），不混合。\n"
            "- playlist 结构：6-7 个 playlist 按天轮转，每个含多场景，按子技能渐进（fingertip micro-corrections → precision off wide flicks → reactive/unplanned flicking → cluster-speed bursting → benchmark-specific multishot）。\n"
            "- 技能分层：Initiate → Intermediate → Advanced → Advanced Plus，按水平选层。\n"
            "- 平台：KovaaK's（主）+ Aimlabs（适配版）。\n"
            "- 理念：proactive（主动补弱项，在弱项显现前练）vs weakness-specific（reactive 补已显现弱项）—— VDIM 是 proactive 派。\n\n"
            "Voltaic Routines 2.0 weakness 框架四类：smoothness / precision / speed / static / reactivity——按弱项选场景。"
        ),
    },
    {
        "topic": "corporate_serf_method",
        "signal": None,
        "source_ref": "coach-community-frontier.md §高手方法",
        "source_level": "personal_experience_unverified",
        "text": (
            "Corporate Serf（Celestial 级，Voltaic precise tracking 世界 #1）训练方法，社区广泛引用：\n"
            "- structured progression：按 rank 分层（Novice→Expert→Master），严格按 progression 顺序练到 Masters 再扩其他。\n"
            "- click vs hold 分离：clicking 场景（flick+click）与 smoothness/dynamic（持续）分开练。\n"
            "- 5-10 runs/scenario/session：每场景 5-10 次，日练。自述极端个案单场景 3305 runs/55h 刷到顶峰，但那是异常值，常态 5-10 runs/session 持续。\n"
            "- smoothness 早期优先：smoothness drill（水平/垂直平滑）在 progression 早期。\n"
            "- Personal Best (PB) Method：刷 PB——单场景持续刷到突破 plateau。\n"
            "- underflick 练习法：故意欠冲再修，练 corrective submovement 速度（与学术 submovement 理论呼应）。"
        ),
    },

    # ============================================================
    # 源文档 4：docs/coach-prescription-manual.md（处方层）
    # ============================================================
    {
        "topic": "decel_termination_cost",
        "signal": "decel_frac high",
        "source_ref": "coach-prescription-manual.md §1.1",
        "source_level": "academic_peer_reviewed",
        "text": (
            "decel_frac 过高 / 减速段不规则 = 「制动代价」而非纯过冲修正。\n"
            "Fradet, Lee & Dounskaia 2008（Acta Psychologica, PMC2600723），3-0 票验证。\n\n"
            "机制：运动终止（motion termination）是与减速本身分离的控制成分——急速接近目标后需迅速将负加速度归零并稳定肢体，这一稳定过程在高速高负加速度动作中产生毛刺状速度波动（Type 1 子动作，速度过零）。长而不规则的减速段部分是高速接近的「制动代价」。\n\n"
            "原文：\"the limb stabilization may be accompanied with small fluctuations, specifically during fast movements that require high negative acceleration while approaching the target and quick reduction of this acceleration to zero when the target has been achieved.\"\n\n"
            "处方：\n"
            "- 场景：1wall 6targets small（小目标训练精细制动）、Multiclick（连续制动-再启动）、Tile Frenzy（短距离快速制动）。\n"
            "- 执行要点：训练有控制的制动而非更大力气；制动是一次性归零，不是「蹭着减速」。\n"
            "- 编排：交错（见 ci_interleave 片段）。"
        ),
    },
    {
        "topic": "submovement_types",
        "signal": "two_stage",
        "source_ref": "coach-prescription-manual.md §1.2",
        "source_level": "academic_peer_reviewed",
        "text": (
            "submovement two-stage 必须先分类（Type 1 vs Type 2/3）。\n"
            "Fradet et al. 2008 + Exp Brain Res 2024 \"Type 1 Submovement Conundrum\" + 老年研究 PMC2628348，2-1 票验证。\n\n"
            "机制——子动作至少三类、病因不同：\n"
            "- Type 1（速度过零，gross）= 运动终止/稳定，非精度修正；离散动作中富集、往复动作中稀少。\n"
            "- Type 2（加速度过零）+ Type 3（jerk 过零，fine）= 与精度需求及低速相关。\n\n"
            "诊断含义：不能笼统读作「修正过多」。必须先分类不规则性类型——是制动控制差（终止问题，Type 1）还是速度-精度权衡波动（修正问题，Type 2/3），否则会把慢速 flick 误诊为共济失调性。\n\n"
            "分支化处方：\n"
            "- 终止型（Type 1 主导）→ Multiclick / Tile Frenzy 制动控制。\n"
            "- 修正型（Type 2/3 主导）→ 1w6ts 小目标精度子动作。\n\n"
            "⚠ 落地前提：Type 1/2/3 分类在 <300ms flick 信号上的可靠实现未解决。当前 advice.py 的 submovement_overlap 只能给「两段式 vs 流体」的粗分类，分支化处方需等分类算法落地。"
        ),
    },
    {
        "topic": "sparc_low_prescription",
        "signal": "sparc low",
        "source_ref": "coach-prescription-manual.md §1.3 + §1.6",
        "source_level": "academic_peer_reviewed",
        "text": (
            "SPARC 低 / 减速抖动 = 低速下的发力控制问题。\n"
            "Fradet et al. 2008，3-0 票验证。\n\n"
            "机制：Fine 子动作（Type 2/3）发生率与峰值速度强负相关（Type 2: R²=0.71；Type 3: R²=0.75，p<0.05）——速度越低、不规则越多；Type 1 与速度无相关（R²=0.10）。机制为低速度下的运动单位放电变异（非必然是修正尝试）。\n\n"
            "处方：低速场景（1w6ts、Tile Frenzy 短距）的减速抖动可能是发力控制问题，处方应转向「发力控制 + 外部焦点」，而非「更多修正训练」。\n\n"
            "SPARC 作为减速段质量主指标的依据（§1.6）：Cornec et al. 2024 + Balasubramanian 2012/2015 + Bayle 2023，对不受控时长的 reaching，SPARC 优于时域指标（LDLJ、nSUB、NARJ）——ICC>0.9 可靠性最好、CoV<10% 测量误差最小、受运动时长污染远小于 TDSM。\n\n"
            "⚠ 范围警告：原始研究为卒中康复人群（~2-2.6s 自定速 reach），KovaaK flick 是 <300ms 健康人弹道动作，频域估计在极短信号上可能退化——可靠性数值不能直接迁移，SPARC 阈值只能作方向性支撑，需真实数据校准。"
        ),
    },
    {
        "topic": "reverse_ratio_trap",
        "signal": "reverse high",
        "source_ref": "coach-prescription-manual.md §1.4",
        "source_level": "academic_peer_reviewed",
        "text": (
            "reverse_ratio 高 / 子动作计数高 —— 时长偏倚陷阱。\n"
            "Cornec et al. 2024（J NeuroEng Rehabil, PMC11134951），2-0 票验证。\n\n"
            "陷阱：时域子动作指标（nSUB、NARJ）与运动时长强相关（r_Spearman>0.8）但与运动直线度无显著相关——高子动作计数/高反向计数很大程度上是更慢/更长动作的假象而非纯修正信号。\n\n"
            "处方含义：诊断 reverse_ratio 前必须归一化运动时长，否则会把慢 flick 误诊为共济失调性。\n\n"
            "⚠ advice.py 改进点：当前 reverse_ratio 阈值（0.20）未做时长归一化。理想是 reverse_ratio / duration 或限定同速度段比较。但 flicking.py 的切段已按 valley，时长差异有限——标为后续校准项，不阻塞动态处方。"
        ),
    },
    {
        "topic": "external_focus",
        "signal": "decel_frac high",
        "source_ref": "coach-prescription-manual.md §2.1",
        "source_level": "academic_peer_reviewed",
        "text": (
            "外部注意焦点（external focus）。\n"
            "Wulf 2013 综述 + Wulf et al. 2010（PMC3153799）+ Zachry 2005 + Vance 2004，2-1/3-0 票验证。\n\n"
            "机制：效果焦点（在运动效果上：准星/目标/命中点）相比内部焦点（在手/腕/前臂动作上）降低拮抗肌共收缩与 EMG、同时提升精度与峰值力/速度——更省力的运动，张力浪费更少。约束动作假说：外部焦点促进自动化控制，内部焦点引发有意识干预、产生多余肌肉活动。\n\n"
            "处方含义：高 TBR / decel_frac 高 / 过度握紧的诊断应给外部焦点提示——「看准目标」「顺过那个点」，可降低多余张力同时保住峰值速度与吞吐量。\n\n"
            "⚠ 任务依赖：外部焦点对离散/瞄准动作稳健，但对 <~200ms 纯弹道动作衰减（意识无时间调制已发射的运动程序）——提示应在准备/设定阶段施加，不能指望它挽救已发射的弹道段。"
        ),
    },
    {
        "topic": "meta_cognition",
        "signal": None,
        "source_ref": "coach-prescription-manual.md §2.2",
        "source_level": "academic_peer_reviewed",
        "text": (
            "对抗元认知过度自信（推交错时必带）。\n"
            "Simon & Bjork 2001（经 Lee 2004 引），3-0 票验证。\n\n"
            "机制：学习者系统性地是自身学习状态的差判官——块状练习者会感觉保留能力上「过度自信」，把「表现改善的感觉」误归为「学习进展的感觉」。\n\n"
            "处方含义：磨单一 KovaaK 场景的用户会感觉进步飞快、抵抗交错，但这种感觉高估了真实学习。AI 教练在推交错处方时必须主动对抗此偏置——明确告知「感觉进步快 ≠ 长期记住」。\n\n"
            "这是 ④ plan-adjustment 的 interleave adjustment 的 reason 必带措辞：用户感觉不可靠（块状过度自信），所以用客观指标判 stall 而非用户感知，默认 verdict 成立。"
        ),
    },
    {
        "topic": "ci_interleave",
        "signal": None,
        "source_ref": "coach-prescription-manual.md §4.1 + §4.2",
        "source_level": "academic_peer_reviewed",
        "text": (
            "交错 vs 块状编排（证据最强）。\n"
            "Lee & Simon 2004 + Schorn & Knowlton 2021（PMC8476370）+ Nature Sci Rep 2024 三级元分析 + Dang/Parvin/Ivry 2023 + Shea & Morgan 1979，多次 3-0 票验证。\n\n"
            "核心发现：交错（interleaved/random）练习在长期保留与迁移上稳定优于块状（blocked），即便全为内隐学习亦成立。块状在习得期表现更好、感觉进步更快，但学习更不灵活、在交错测试条件下失败。\n\n"
            "处方：单一 session 内交错多个点击场景（pasu → 1w6ts → Multiclick → Tile Frenzy）期望比磨单一场景产生更好长期保留/迁移，即便后者当下感觉更有效。\n\n"
            "渐进 hybrid（Lee & Simon 2004 + Al-Ameer & Toole 1993 + Simon et al. 2002，3-0 票）：存在混合编排兼顾块状习得收益与交错保留收益。最佳实践是从块状（新技能习得/热身）→ 递增交错（保留/迁移）的渐进。新接触某场景用块状几轮建立模式，随后转入跨场景交错。\n\n"
            "KovaaK 落在 CI 实验室域（Nature Sci Rep 2024，54 研究 2068 参与者）：CI 实验室域大且显著（SMD=0.92），应用域可忽略（SMD=0.23）。KovaaK 离散点击属受控简单离散刺激-响应任务，恰是 CI 稳健区间。"
        ),
    },
    {
        "topic": "feedback_fading",
        "signal": None,
        "source_ref": "coach-prescription-manual.md §5.1",
        "source_level": "academic_peer_reviewed",
        "text": (
            "Guidance hypothesis 处方应用：减 KR / 汇总反馈促保留。\n"
            "Salmoni, Schmidt & Walter 1984（PMID 6399752）+ Marschall/Bund/Wiemeyer 2007 元分析 + 2022 J Sports Sci 元分析，3-0 ×3 票验证。\n\n"
            "频繁/即时 KR 改善练习期表现但削弱无反馈时的长期保留（指导效应）。降低 KR 频率（每若干次 shot 才反馈、或 summary KR）促更稳健保留。\n\n"
            "处方：实时逐 shot 反馈 ≠ 学到；处方应安排无反馈的保留/迁移测试检验真实习得；块状练习后用汇总反馈优于逐 shot。\n\n"
            "任务依赖：对离散点击任务（pasu/1w6ts/Tile Frenzy）稳健，对复杂/连续任务及部分新手可能有害。\n\n"
            "外部焦点 KR 可高频（例外）：Wulf et al. 2010（PMC3153799），3-0 票。当反馈诱导外部注意焦点时，高频反馈（100% 试次）反而比低频（33%）更能促进运动形式的学习。⚠ 单研究、儿童足球掷界外球、N=48，效益在运动形式上而非精度，不应作 100% 频率定论。"
        ),
    },

    # ============================================================
    # 源文档 5：youtube doc/YouTube 瞄准训练内容综合.md（创作者方法论）
    # ============================================================
    {
        "topic": "bardoz_method",
        "signal": "two_stage",
        "source_ref": "YouTube 瞄准训练内容综合.md §2.1-2.2",
        "source_level": "personal_experience_unverified",
        "text": (
            "bardOZ 静态点击方法论的三段重构（顶级静态选手，瞄准应像练习乐器——追求速度之前，先以极慢速度建立 100% 准确率的完美动作逻辑）：\n\n"
            "1. 快速摆动（Flick）：手臂爆发发力，追求直线轨迹与最高初速度。这一段允许「猿化」（Ape）——可以为速度牺牲一些精确。\n"
            "2. 微调（Micro-correction）：准星着陆后的二次修正。原则是「宁可欠准（Underflick）也不要过载（Overflick）」——向前推移的微调比越过目标后的回拉更短、更符合生物力学效率。微调阶段绝不能盲目。\n"
            "3. 确认（Confirmation）：击发前极短的视觉确认。建议使用带中间空隙（Gap）的准星，注意力集中在空隙中心，确保目标处于「黑点位于准星中央」的状态后再点击。\n\n"
            "训练纪律（Cartoon、Viscose）：长期进步取决于能否抑制盲目提速的冲动。盲目提速（Aping）= 摆动与微调混为一谈、整体动作模糊、依靠运气、波动巨大；纪律性修正（Disciplined Flicking）= 明确的两段式：爆发摆动 → 精准微调、100% 准确率优先、通过减速消除错误记忆。为强化战术 FPS 精度，可加入比标准目标缩小 30% 的场景（Small 变体）磨练微调细腻度。"
        ),
    },
    {
        "topic": "tension_budget",
        "signal": "sparc low",
        "source_ref": "YouTube 瞄准训练内容综合.md §3.1-3.4",
        "source_level": "personal_experience_unverified",
        "text": (
            "张力预算（Tension Budget）三原则——Viscose 提出，应把手部张力视为一种有限的预算：\n"
            "1. 肌肉群独立性：手臂、手腕、指尖应具备独立的张力开关。大范围拉枪动用手臂，最后 5 像素的修正必须由放松的手臂配合微张的指尖完成。\n"
            "2. 动态释放（冲刺转慢跑）：在 Flick 即将结束时提前释放张力，利用惯性实现「平滑着陆」，而非靠死磕肌肉硬停鼠标。\n"
            "3. 锁定效应（Lockout）：张力超支时，手部会发生震颤并剥夺视觉读取能力——你看不清目标了。\n\n"
            "侧向挤压 vs 垂直按压（MattyOW）：用 2D 大型目标（如 Clover Raw Control）练习加减速，重点感知通过侧向挤压鼠标侧面来稳定准星，而非向下垂直按压。垂直按压 → 增加阻力 → 「粘滞感」；侧向压力 → 提供更纯粹的摩擦力控制。\n\n"
            "抖动（Jittering）三类成因：过度紧绷（Death-gripping）肌肉过度收缩反馈链路阻塞；运动范围边缘处于物理极限位置的应力性震颤；压力性响应神经信号过载导致协调性丧失。\n\n"
            "暴露疗法：解决抖动的最佳路径是高压暴露——在高灵敏度且低 FOV 的精准追踪场景下强化，放大任何微小的肌肉颤动，强迫大脑感知误差并修正张力分配。"
        ),
    },
    {
        # Tracking-side mirror of tension_budget (spec 2026-07-05-tracking-coach-design §4.4):
        # same source chunk, exposed under tracking's ptc_high signal so narrator retrieves it
        # when diagnosing tracking PTC. BY_SIGNAL is single-valued, so we duplicate the chunk
        # rather than extend the index to multi-valued mapping.
        "topic": "tension_budget_tracking",
        "signal": "ptc high",
        "source_ref": "YouTube 瞄准训练内容综合.md §3.1-3.4",
        "source_level": "personal_experience_unverified",
        "text": (
            "张力预算（Tension Budget）——tracking 场景的 PTC 解读锚点：\n"
            "PTC（Pure Tension Coefficient）= miss 段加速度密度 / 误差。把数字翻译成「玩家发力状态」是合理生物力学假设（miss 段发力追，加速度密度高 = 制动密集发力），但「Pure Tension」是修辞命名，不直接测肌肉张力——需 EMG / 手部摄像头验证。\n\n"
            "Viscose 张力预算三原则：肌肉群独立性（手臂/手腕/指尖独立张力开关）、动态释放（冲刺转慢跑，提前释放张力靠惯性着陆）、锁定效应（张力超支→震颤+剥夺视觉读取 lockout）。\n\n"
            "侧向挤压 vs 垂直按压（MattyOW）：用 Clover Raw Control 等大型 2D 目标练加减速，侧向挤压鼠标侧面稳定准星，而非向下垂直按压（增加粘滞）。\n\n"
            "暴露疗法：高 sens + 低 FOV 精准追踪放大微颤，逼大脑修正张力分配——这是 ptc high 处方的核心。\n\n"
            "（生物力学假设，未 EMG 验证；narrator 措辞用「可能/提示」，不作断言；severity=info。）"
        ),
    },
    {
        "topic": "tracking_three_kinds",
        "signal": None,
        "source_ref": "YouTube 瞄准训练内容综合.md §4.1-4.3",
        "source_level": "personal_experience_unverified",
        "text": (
            "追踪的三段式辨析（MattyOW / Voltaic 社区分类法，概念映射到真实运动控制域）：\n"
            "1. 纯平滑度（Raw Smoothness）：核心是速度匹配。用指尖的侧向压力对抗滑板的细微抖动。\n"
            "2. 反应式追踪（Reactive Tracking）：应对瞬时加速度。利用极短的张力爆发应对变向，随后立即释放以重新进入平滑读取状态。关键在手腕与前臂切换的平滑度——前臂负责大平稳位移，手腕抵消目标变向的微小误差。\n"
            "3. 控制追踪（Control Tracking）：处理极小、逃逸型（evasive）目标。关键生物力学技巧：垂直方向的微小追踪应通过弯曲/伸展指尖驱动鼠标位移，而不是动用整只前臂。用大肌肉群处理微小运动是低效且缺乏精度的。\n\n"
            "尺偏（Ulnar Deviation）与肌肉群交接：许多选手在长位移追踪中出现「锯齿状断层」，根源是手腕达到尺偏（向小指侧偏转，腕关节 ROM 约 30°-45°）的物理极限后，未能平滑地把控制权移交给前臂。这种机械性断档是准星失稳的主因。\n\n"
            "学术锚点（已核实）：速度匹配是公认运动控制概念（Kowler, Murphy & Steinman 1978）；SPARC 虽源于离散运动但对连续追踪有效；手内肌（intrinsic）专精精细运动，前臂外在肌（extrinsic）负责大幅运动与稳定腕——两者协同而非二选一（Lemon 2008）。"
        ),
    },
    {
        "topic": "fluidity",
        "signal": None,
        "source_ref": "YouTube 瞄准训练内容综合.md §5.3 + §2.3",
        "source_level": "personal_experience_unverified",
        "text": (
            "流动性（Fluidity）：精英与凡人的分水岭。\n\n"
            "Bulldog 的成功在于极佳的转换（Transition）效率——击杀 A 目标的瞬间已发起向 B 目标的 Flick。\n"
            "- 路径规划（Roadmapping）：永远领先 2-3 个目标进行视觉扫描。\n"
            "- 集群扫荡（Cluster Farming）：目标密集分布时，放弃标准两步走动作，改用平滑鼠标轨迹配合节奏性点击（双连击/三连击），将多个目标串联为一次流畅移动。\n\n"
            "学术锚点（已核实）：\n"
            "- Transition 效率 → overlapping submovements + parallel planning：CNS 可在前一动作未完成时启动下一个，减少 dead time（呼应 submovement / Novak 2002 概念）。\n"
            "- 路径规划 → anticipatory saccades / visual scan-path planning：proactive visual attention，提前采样下一目标坐标。\n"
            "- 集群扫荡 → motor chunking + sequence planning：把离散目标 chunk 成一个流畅运动程序，减少 task-switching cost。\n\n"
            "进阶：模糊决策边界。传统 Bardoz 循环是清晰的 Flick → Microcorrection → Confirm 三步。但顶尖层级，这种 1-2-3 的明确步骤本身是速度的杀手。真正专家会模糊决策边界：微调本身就是确认——不需要在目标上产生视觉停顿，停顿意味着节奏（Pacing）崩塌；微调融合——将拉准与微调融合为一个流体动作，减少肌肉在每个步骤间的启动/停止开销。"
        ),
    },
    {
        "topic": "vod_review",
        "signal": None,
        "source_ref": "YouTube 瞄准训练内容综合.md §6",
        "source_level": "personal_experience_unverified",
        "text": (
            "复盘方法论（VOD Review）——识别表现向量（Performance Vector）及其各组件受损情况的过程，不是简单的胜负记录。\n\n"
            "系统一与系统二：\n"
            "- 系统一（反射/自动）：原始准星控制、肌肉群协同、压力管理。实战中必须绝对自动化。\n"
            "- 系统二（战术/计算）：选位、信息处理、战术决策。\n"
            "专家准则：如果你在对战中思考「我该如何移动鼠标」，你的系统一已经失效。\n\n"
            "职业级复盘要看什么（不只看是否击中）：\n"
            "1. 锯齿状微调（Jagged Micros）：修正动作是否生硬、缺乏连贯性？\n"
            "2. 尾随微调（Trailing Micros）：准星是否始终落后于目标的运动矢量？\n"
            "3. 紧迫的开火时机（Rush Shot Timing）：是否因心理压力，在确认完成前过早扣动扳机？\n"
            "4. 表现向量组件：检查 Raw Aim / Movement / Pressure / Form / Health / Mental-Nerves 哪个环节是瓶颈。\n\n"
            "方向性偏差（Directional Bias）：真实对手会利用小幅晃动欺骗视觉。通过反应型场景训练学会识破「伪变向」，把注意力集中在目标的整体运动矢量上，而非被局部小动作干扰。"
        ),
    },
    {
        "topic": "setup_hardware",
        "signal": None,
        "source_ref": "YouTube 瞄准训练内容综合.md §7 + §8",
        "source_level": "personal_experience_unverified",
        "text": (
            "灵敏度矩阵（MattyOW 2025 场景化推荐）：\n"
            "- 静态点击：70-80 cm/360°（极低灵敏度最大程度过滤微抖动，优化落地精度）。\n"
            "- 反应式追踪：25-35 cm/360°（高灵敏度允许更快反应，应对目标瞬间变向）。\n\n"
            "为什么低灵敏度能提高精度（生物力学解释）：相同的游戏内位移需要更大的手部物理移动，带来两点好处——① 更精细的运动分辨率，每毫米手部移动对应的屏幕像素更少，微小颤抖被「稀释」，落点更可控；② 更丰富的本体感受反馈，关节与肌肉对大位移的感知比对微位移更清晰，闭环修正更准。\n\n"
            "Patching 策略（弱势肌肉群的「补丁」）：平台期的本质是神经路径进入「自动导航」盲区。如果你是过度依赖手腕的静态专家，手臂就是你的「懒惰肌肉」。补丁方案：强制使用超低灵敏度（如 50-60 cm/360）进行大规模追踪场景训练，强制大脑激活前臂与肩膀，直到这种调用成为本能。\n\n"
            "动态适应性训练（EliGE Amp 计划）：放弃静态任务，转而采用改变条件（Changing Conditions）的场景——通过动态难度和多变轨迹，强制大脑进行实时自适应训练。实战是不断变化的，只有在变化中磨练出的控制力才能转化为绝对统治力。\n\n"
            "20% 原则：投入 20% 时间专门进行「弱项轰炸」。External Focus of Attention：停止纠结握法、灵敏度等内部因素，把意识集中在屏幕的输出反馈上。"
        ),
    },
    {
        "topic": "neuroscience_foundations",
        "signal": None,
        "source_ref": "YouTube 瞄准训练内容综合.md §1.2 + §9",
        "source_level": "academic_peer_reviewed",
        "text": (
            "「肌肉记忆」的真义：传出神经副本与预测模型（Wolpert & Flanagan 2001, Motor prediction）。\n\n"
            "社区常说的「肌肉记忆」是个误称。运动学习的本质是神经塑性驱动的预测模型构建。大脑发出运动指令时会同步产生一份传出神经副本（Efference Copy）送到小脑，让大脑在视觉反馈到达之前预判运动结果并实时修正。\n\n"
            "两个推论：\n"
            "- 频繁更换灵敏度不会「破坏手感」——大脑会根据新的输入-输出映射迅速更新内部前向模型。所谓「肌肉记忆」其实是 CNS（小脑 + 运动皮层）的程序性记忆，肌肉本身只提供收缩力，不存储「移动多远」的信息。\n"
            "- 真正的挑战是适应性反馈的质量，而非动作序列的固化。\n\n"
            "三个该破除的认知误区：\n"
            "1. 握法决定论——握法没有「版本答案」，物理-机械界面的核心指标是舒适度、健康与长寿。强行模仿职业选手握法产生的肌肉压力，长期会演变为应力性损伤。\n"
            "2. 灵敏度一致性——认为练枪必须完全复刻游戏内灵敏度是误区，在练习器中改变灵敏度实际是在强化大脑对不同肌肉群的调用策略。\n"
            "3. 「肌肉记忆」泛化——控制力源于神经系统的动态调节，而非某种秘密配方。\n\n"
            "健康底线（MattyOW 曾因忽视健康险些结束职业生涯）：定时休息（每 45-60 分钟）、拉伸与肌腱/神经滑动（Wehbé 1987；Rozmaryn 1998 证实可作腕管保守治疗）、睡眠（运动记忆在睡眠中固化）、视觉休息（每小时远眺 10 分钟）。电竞医学实证（Forman et al.）：腕伸肌是 aim-training 主要负荷肌群（约 9.3% MVC），即使无明显掉表现也累积疲劳。"
        ),
    },
]

# 索引（导入时构建一次）
BY_TOPIC: dict[str, list[KnowledgeChunk]] = {}
BY_SIGNAL: dict[str, list[KnowledgeChunk]] = {}


def _build_indexes() -> None:
    for chunk in KB:
        BY_TOPIC.setdefault(chunk["topic"], []).append(chunk)
        if chunk["signal"]:
            BY_SIGNAL.setdefault(chunk["signal"], []).append(chunk)


_build_indexes()

__all__ = ["KB", "BY_TOPIC", "BY_SIGNAL", "KnowledgeChunk"]
