"""zh-CN 诊断文案目录（现行中文逐字搬运；改动会破坏 golden 契约）。"""
from __future__ import annotations

from ..profiles import ARCHETYPES, ROOT_CAUSES

# advice.py 静态回退：signal -> plain_language_meaning。
PLAIN_MEANINGS = {
    "decel_frac high": "速度峰值后用了较长时间完成减速",
    "decel_frac low": "速度峰值后留给连续减速的时间较短",
    "linearity high": "减速阶段的速度下降节奏不够均匀",
    "sparc low": "减速阶段的速度轮廓含较多快速波动",
    "reverse_ratio high": "移动收尾时出现了较多反向修正",
    "submovement two-stage": "主要移动与后续修正更像两个分离动作",
    "peak_position low": "速度峰值出现得较早",
    "peak_position high": "速度峰值出现得较晚",
    "path_efficiency low": "实际移动路径比起终点直线距离更绕",
    "peak_speed below reference": "峰值速度低于当前比较参考",
    "throughput below reference": "速度与精度综合效率低于当前比较参考",
    "sensitivity high": "当前 cm/360 较小，是否影响控制仍需实验验证",
}

# advice.py 静态回退：signal -> Finding.diagnosis 模板（str.format 插值）。
FINDING_DIAGNOSES = {
    "decel_frac high": (
        "减速段占整个 flick 的 {decel_frac_pct:.0f}%——"
        "速度峰值后用了较长时间完成减速；该阈值仍需真实产品数据校准。"
    ),
    "decel_frac low": (
        "减速段只占 {decel_frac_pct:.0f}%，连续减速时间较短；"
        "是否属于制动问题仍需结合 settle/reverse 和个体历史验证。"
    ),
    "linearity high": (
        "减速段速度曲线偏离匀减速直线 {linearity:.2f}——"
        "速度下降节奏不够均匀。注：这度量的是制动节奏，不是抖动；"
        "抖动看 SPARC。"
    ),
    "sparc low": (
        "减速段平滑度 SPARC={sparc:.1f}——速度轮廓中的快速波动较多。"
        "SPARC 描述运动轮廓，不直接测量握持张力；绝对阈值仍需产品校准。"
    ),
    "reverse_ratio high": (
        "减速段有 {reverse_pct:.0f}% 的帧在反向加速，表现为反复修正。"
    ),
    "submovement two-stage": "主要移动和后续修正呈较分离的两个阶段。",
    "peak_position low": (
        "峰位 {peak_pos:.0f}%（偏前），速度峰值较早出现；"
        "具体动作原因未被输入数据直接测量。"
    ),
    "peak_position high": (
        "峰位 {peak_pos:.0f}%（偏后），速度峰值较晚出现；"
        "具体动作原因未被输入数据直接测量。"
    ),
    "path_efficiency low": (
        "flick 路径直线效率 {path_eff:.2f}，实际路径相对终点直线距离更绕；"
        "绝对阈值仍需产品校准。"
    ),
    "peak_speed below reference": (
        "甩枪角速度 {self_peak:.0f}°/s 只有参考的 {ratio_pct:.0f}%"
        "（{ref_peak:.0f}°/s）；峰值速度低于当前参考，身体原因未被输入数据直接测量。"
    ),
    "throughput below reference": (
        "Fitts throughput {self_tp:.1f} bits/s 只有参考的 {tp_ratio_pct:.0f}%"
        "（{ref_tp:.1f}）；速度-精度综合效率低于当前参考，身体原因未被输入数据"
        "直接测量（throughput 已按目标距离/宽度归一化，§6.3）。"
    ),
    "sensitivity high": (
        "当前灵敏度为 {cm_per_360:.1f} cm/360。较小 cm/360 可能放大控制输入，"
        "但不能单凭设置值判定动作问题，只能作为受控实验假设。"
    ),
}

# 施工单⑥两层拆分：本表只保留"信号→能力域处方"层——值是
# (能力域 id, 训练动作)，能力域 id 见 CAPABILITY_DOMAINS（capability-vocabulary
# 九域），**不指向具体图名**；具名场景清单层已移除，"能力域→标准答案场景"
# 由 coach/standard_anchors.py 锚点表按三路接力（本机已装→未装标注需订阅→
# 无对症走定制/search）承担。"降 sens"类是设置实验动作，不是场景，保持原样。
FINDING_PRESCRIPTIONS = {
    "decel_frac high": (
        ("static_positioning", "练完整的加速→减速，接近目标时果断完成制动"),
        ("static_positioning", "保持 90%+ 准确率，练完整 flick 的加减速"),
    ),
    "decel_frac low": (
        ("static_positioning", "练匀减速，把减速段当一次独立动作"),
    ),
    "linearity high": (
        ("static_positioning", "把减速段练成干净、连贯的制动"),
        ("micro_adjust", "减速段精度专项"),
    ),
    "sparc low": (
        ("static_positioning", "clean lines，让减速速度连续下降，避免突然硬停"),
        ("micro_adjust", "减速段精度专项"),
    ),
    "reverse_ratio high": (
        ("static_positioning", "把修正并入减速过程，避免停住后再二次修正"),
        ("micro_adjust", "落点精度，减少二次修正"),
    ),
    "submovement two-stage": (
        ("static_positioning", "尝试让主要移动和收尾修正保持衔接，减少停住后再单独修正"),
        ("micro_adjust", "落点精度，减少二次修正"),
    ),
    "peak_position low": (
        ("static_positioning", "平衡加减速，把峰往中段靠"),
    ),
    "peak_position high": (
        ("static_positioning", "练果断加速、提速"),
    ),
    "path_efficiency low": (
        ("static_positioning", "练直线 flick，走最短路径"),
        ("static_positioning", "意识：flick 走直线，不画弧"),
    ),
    "peak_speed below reference": (
        ("static_positioning", "在可控精度下逐步提高动态速度"),
        ("static_positioning", "大胆加速，先求速度再收精度"),
    ),
    "throughput below reference": (
        ("static_positioning", "在可控精度下逐步提高动态速度"),
        ("static_positioning", "先求速度再收精度"),
    ),
    "sensitivity high": (
        ("降 sens 5-10%（cm/360 ↑）", "制动辅助实验；复测 linearity/reverse 是否下降，没降就调回"),
    ),
}

# 能力域 id → 展示名（capability-vocabulary.md 九域；sce_reading.training.domains
# 用同一词表子集）。
CAPABILITY_DOMAINS = {
    "reactive_change": "变向响应（域1）",
    "smooth_tracking": "平滑跟枪（域2）",
    "static_positioning": "静态定位（域3）",
    "micro_adjust": "微调（域4）",
    "confirm_timing": "确认时机（域5）",
    "reset_management": "复位管理（域6）",
    "target_switching": "转火衔接（域7）",
    "target_reading": "读靶（域8）",
    "pressure_pacing": "压力与节奏（域9）",
}

# 施工单⑤速度吞吐判读档（reading_scope=speed_throughput）下的 reverse_ratio
# 改写文案。判读出处：registry.v14 community.aimwiki.metronome-pacing-method
# （节拍器配速法）+ 本表 "peak_position high"/"peak_speed below reference" 的
# 既有同向处方（练果断加速、提速）；此档下 settle/停稳类话术绝迹。
SPEED_PLAIN_MEANINGS = {
    "reverse_ratio high": "速度吞吐图上收尾的小幅反向修正，多属于快中带控的节奏代价",
}
SPEED_FINDING_DIAGNOSES = {
    "reverse_ratio high": (
        "减速段有 {reverse_pct:.0f}% 的帧在反向加速；在吞吐优先的图上，"
        "高命中率下的小幅收尾修正更可能是快中带控的节奏代价，不是欠控病灶。"
    ),
}
SPEED_FINDING_PRESCRIPTIONS = {
    "reverse_ratio high": (
        (
            "pressure_pacing",
            "节拍配速：先用真实成绩测自己的节奏（BPM=每秒击杀数×60），"
            "按命中率带加减 5-10，节拍是参照不是扳机，用它切掉确认段里塞进去的额外微调",
        ),
        ("static_positioning", "练果断加速、提速，把修正并入减速过程"),
    ),
}

# _finalize_uncalibrated_findings 填充的可比条件/复测/停止规则。
VERIFICATION = {
    "comparable_requirements": ["相同场景", "相同设置", "相同证据质量"],
    "insufficient_evidence_behavior": "样本或可比条件不足时只记录观察，不判定改善或退步",
    "retest_after": "在相同场景、设置和证据质量下复测",
    "stop_or_adjust_rule": "若目标指标未改善或准确率明显恶化，停止调整并恢复原练法",
}

# advice_tracking.py 回退：signal -> plain_language_meaning。
TRACKING_PLAIN_MEANINGS = {
    "accuracy low": "本次记录中准星位于目标范围内的时间比例较低",
    "loss count high": "本次记录中追踪中断次数较多",
    "off target long": "每次追踪中断后回到目标范围所需时间较长",
    "avg error high": "本次记录中准星相对目标中心的平均偏移较大",
    "speed mismatch high": "失手片段中的目标与准星平均速度差较大",
    "accel mismatch high": "失手片段中的目标与准星平均加速度差较大",
    "ptc high": "失手片段中的加速度误差相对空间误差较高",
}

# advice_tracking.py 回退：signal -> Finding.diagnosis 模板与条件片段。
TRACKING_DIAGNOSES = {
    "accuracy low": (
        "命中率 {on_target_pct:.1f}% 低于当前经验参考线 {threshold:.0f}%——"
        "这只说明本次记录的在靶时间比例较低；参考线仍需产品数据校准。"
    ),
    "loss count high": (
        "本次记录脱靶 {loss_count} 次{per_loss}——"
        "追踪中断次数较多；该指标不能单独确定是速度匹配、视觉读取或身体控制造成。"
    ),
    "off target long": (
        "每次脱靶平均 {off_per:.2f}s 才回到目标范围——"
        "本次记录的离靶持续时间较长；该指标不能单独证明视觉锁定或反应延迟。"
    ),
    "avg error high": (
        "平均误差 {avg_error_px:.1f}px{ctx}——"
        "本次记录中准星相对目标中心的平均偏移较大；该指标不能单独确定身体或视觉原因。"
    ),
    "speed mismatch high": (
        "miss 段平均速度差 {speed_mismatch:.0f} px/s——"
        "失手片段中的目标与准星速度差较大；该指标不能单独确定身体或视觉原因。"
    ),
    "accel mismatch high": (
        "miss 段平均加速度差 {accel_mismatch:.0f} px/s²——"
        "失手片段中的目标与准星加速度差较大；该指标不能单独确定身体或视觉原因。"
    ),
    "ptc high": (
        "miss 段 PTC={ptc:.0f} Hz²——"
        "它描述加速度误差相对空间误差的比值，不直接测量肌肉张力，"
        "也不能单独确定身体原因。"
    ),
}
TRACKING_PER_LOSS = "，每次回位 {per_loss:.2f}s"
TRACKING_RATIO_CTX = "（{ratio:.0%} 目标宽）"
TRACKING_ABS_CTX = "（无 ball_w，使用当前未校准绝对参考线）"

# 施工单⑥两层拆分：同 FINDING_PRESCRIPTIONS——只保留"信号→能力域处方"层，
# 具名场景清单移除（场景选择走 standard_anchors 锚点表三路接力）。
TRACKING_PRESCRIPTIONS = {
    "accuracy low": (
        ("smooth_tracking", "持续跟随目标速度，避免在目标后方连续追赶"),
        ("confirm_timing", "优先保持落点稳定，再观察在靶比例"),
    ),
    "loss count high": (
        ("reactive_change", "目标变向时保持连续跟随，不提前猜下一次方向"),
        ("smooth_tracking", "脱靶后用一次连续修正回到目标，避免来回补偿"),
    ),
    "off target long": (
        ("reactive_change", "脱靶后保持一次连续回位，不连续急停重启"),
        ("smooth_tracking", "回到目标后先恢复连续贴合，再提高速度"),
    ),
    "avg error high": (
        ("micro_adjust", "以目标中心为参照，优先缩小持续偏移"),
        ("micro_adjust", "观察准星与目标中心的间距变化，减少长期偏在一侧"),
    ),
    "speed mismatch high": (
        ("smooth_tracking", "跟随目标速度变化，避免突然追赶"),
        ("smooth_tracking", "用连续移动贴合目标，减少急停后重新加速"),
    ),
    "accel mismatch high": (
        ("reactive_change", "目标变向时保持连续跟随，不提前猜下一次方向"),
    ),
    "ptc high": (
        ("pressure_pacing", "暴露疗法：高 sens + 低 FOV 精准追踪，减少连续来回补偿，只把 PTC 变化当探索信号"),
    ),
}

TRACKING_VERIFICATION = {
    "comparable_requirements": [
        "相同场景",
        "相同设置",
        "相同记录时长",
        "相同证据质量",
    ],
    "insufficient_evidence_behavior": "样本或可比条件不足时只记录观察，不判定改善或退步",
    "retest_after": "在相同场景、设置、记录时长和证据质量下复测",
    "stop_or_adjust_rule": "若目标指标未改善或 on_target_pct 明显恶化，停止调整并恢复原练法",
}

# diagnosis.py 硬编码。
PRIORITY_REASONS = {
    "watch": "本次优先观察项",
    "fix": "本次优先处理项",
}
UNCLASSIFIED = "未分类"

# profiles.py 冻结回退的画像/根因（zh 侧引用常量，不复制第二份）。
ARCHETYPE_LABELS = {entry["id"]: entry["label"] for entry in ARCHETYPES}
ROOT_CAUSES = dict(ROOT_CAUSES)
