"""Coach 社区知识库：按诊断 signal 索引，narrator 按需检索注入 prompt。

渐进式上下文设计——只有本次诊断实际触发的 signal 对应的知识进入 LLM
prompt，而非全量预加载进 SYSTEM_PROMPT。内容来自 YouTube 创作者方法论
(``youtube doc/YouTube 瞄准训练内容综合.md``) + Voltaic 社区共识。

契约：每个 KEY 必须与 :func:`kovaak_tracker.advice.advise` 输出的
``Finding.signal`` 完全一致——这是规则驱动检索的入口（诊断是确定性规则
引擎，LLM 只翻译；signal 是规则引擎产物，由此决定取哪些知识，不交给 LLM
自行检索，见 narrator.py 的 anti-hallucination 铁律）。

每条含：
- ``community``：社区归因/说法（narrator 可引用，让诊断解释更权威具体）
- ``cues``：可操作提示（落到具体动作/意识，喂给处方讲解）
"""
from __future__ import annotations

# signal -> {community, cues}。KEY 与 advice.advise() 的 Finding.signal 对齐。
KNOWLEDGE = {
    "sparc low": {
        "community": "MattyOW/Viscose 张力预算：手部张力是有限预算，超支会震颤并剥夺视觉读取（lockout——你看不清目标了）。减速抖动多源于三类成因：死握(death-grip)、运动范围边缘（如尺偏终点）、压力响应。",
        "cues": [
            "暴露疗法：高灵敏 + 低 FOV 的精准追踪，放大任何微小颤抖，逼大脑感知并修正张力分配",
            "侧向挤压鼠标侧面而非向下垂直按压——侧向给纯摩擦力控制，垂直按压只增加粘滞",
            "Flick 即将结束时提前释放张力，靠惯性平滑着陆，别死磕肌肉硬停",
        ],
    },
    "reverse_ratio high": {
        "community": "锯齿/反复修正多是张力锁定的表现——减速段没能一次到位。bardOZ 的纪律：微调宁可欠准(underflick)也别过载(overflick)，向前推的修正比越过目标后的回拉更短、更省。",
        "cues": [
            "underflick 原则：瞄到目标前侧，靠前推微调收尾，别甩过头再往回拉",
            "侧向挤压稳住准星，减少来回 readjust",
        ],
    },
    "submovement two-stage": {
        "community": "bardOZ：顶尖层会模糊决策边界——微调即确认，不需要在目标上做视觉停顿（停顿=节奏崩塌）。两段式（甩过去→停→单独微调）是速度杀手。警惕'得分刷子'：靠极慢换 100% 准确率，实战里会被先击杀。",
        "cues": [
            "微调融合：把甩枪与微调融成一个流体动作，减少每步启停开销",
            "在技术形式不崩的前提下推高速度，别为分数牺牲速度上限",
        ],
    },
    "decel_frac high": {
        "community": "静态点击社区共识：arm flick 到位 → wrist/指尖 micro-correction → hit-confirm（落地才点，别边甩边点）。减速段拖太长=在'蹭'，效率低。",
        "cues": [
            "当 tracking 练：快接近、慢落地，减速果断一次到位",
            "落地确认后再点，别在减速途中急促连点",
        ],
    },
    "decel_frac low": {
        "community": "减速不足/撞墙式制动——减速段被压缩，没给精度留时间。",
        "cues": [
            "把减速段当一次独立动作，匀减速着陆",
            "pasu 练完整的加速→减速循环",
        ],
    },
    "linearity high": {
        "community": "制动节奏不匀（恒定制动线性度差）。注意：这度量的是节奏匀不匀，不是抖动——抖动看 SPARC。",
        "cues": [
            "clean lines：减速段走匀速制动，一次到位",
            "1w4ts 30% larger：减速段精度专项",
        ],
    },
    "peak_position low": {
        "community": "峰位偏前=加速过急、减速段拖沓。",
        "cues": ["平衡加减速，把峰往中段靠（aim 健康 35-50%）"],
    },
    "peak_position high": {
        "community": "峰位偏后=加速拖沓、来不及减速。",
        "cues": ["Tile Frenzy：练果断加速、提速"],
    },
    "path_efficiency low": {
        "community": "flick 路径绕，没走直线。MattyOW 流动性：路径规划(roadmapping)——永远领先 2-3 个目标做视觉扫描。",
        "cues": [
            "flick 走最短直线，不画弧",
            "linetrace 练直线 flick 路径",
        ],
    },
    "peak_speed below reference": {
        "community": "甩枪发力不足。arm 发力是动态速度的来源——先求速度再收精度。",
        "cues": [
            "Tile Frenzy：练 arm 发力与动态速度",
            "大胆加速，先求速度再收精度",
        ],
    },
    "throughput below reference": {
        "community": "跨距离发力能力不足（Fitts throughput，已按目标距离/宽度归一化）。",
        "cues": [
            "Tile Frenzy：练 arm 发力与跨距离动态速度",
            "先求速度再收精度",
        ],
    },
    "sensitivity high": {
        "community": "灵敏度偏高（flicking 主流 28-43 cm/360）。生物力学：低 sens=相同游戏内位移需更大手部移动→更精细的运动分辨率 + 更丰富的本体感受反馈。sens 是放大器，非根因——调 sens 必须复测指标。",
        "cues": [
            "降 sens 5-10%（cm/360 ↑）做制动辅助实验",
            "复测 linearity/reverse 是否下降，没降就调回——别迷信 sens 一致性",
        ],
    },
    # ============================================================
    # Tracking signals (spec 2026-07-05-tracking-coach-design §4.3)
    # ============================================================
    "accuracy low": {
        "community": "Voltaic tracking benchmark 健康线 70%+；命中率是 tracking 金标准量。",
        "cues": ["pasu 练连续追踪基础", "VT Multiclick 30% larger 练落点精度"],
    },
    "loss count high": {
        "community": "频繁断追踪 = 速度匹配跟不上目标变向（MattyOW reactive tracking 概念）。",
        "cues": ["VT reactive tracking 应对瞬时加速度", "Clover Raw Control 速度匹配 + 侧向挤压稳准星"],
    },
    "off target long": {
        "community": "脱靶后回位慢 = 视觉重新锁定延迟（catch-up saccade 文献）。",
        "cues": ["VT evasive tracking 练视觉读取", "锁定目标整体运动矢量，不被局部小动作干扰"],
    },
    "avg error high": {
        "community": "临界命中（Fitts：精度 = 1/目标宽）。bardOZ 推荐带 gap 准星，注意力锁中心。",
        "cues": ["VT precise tracking 专项", "用带 gap 准星，注意力集中在空隙中心"],
    },
    "speed mismatch high": {
        "community": "smooth pursuit 速度匹配（Kowler 1978）——目标屏幕速度快时失手多。",
        "cues": ["VT control tracking 持续中速追踪", "前臂大平稳位移 + 手腕抵消微小误差"],
    },
    "accel mismatch high": {
        "community": "reactive tracking（Voltaic S5 子类）——目标瞬时变向时失手。",
        "cues": ["VT reactive tracking 应对瞬时加速度", "极短张力爆发应对变向，随后立即释放"],
    },
    "ptc high": {
        "community": "张力预算（Viscose）：手部张力是有限预算，超支会震颤并剥夺视觉读取（lockout）。",
        "cues": [
            "暴露疗法：高 sens + 低 FOV 精准追踪放大微颤，逼大脑修正张力分配",
            "侧向挤压鼠标侧面而非向下垂直按压——侧向给纯摩擦力控制",
            "（生物力学假设，未 EMG 验证；作提示性诊断，需结合 SPARC / 反向修正一起读）",
        ],
    },
}


__all__ = ["KNOWLEDGE"]
