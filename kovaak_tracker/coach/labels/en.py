"""en-US 诊断文案目录（B3 i18n；键集与 zh.py 完全一致，测试锁定）。

英文措辞对齐知识库口径：描述性观察 + 不超出证据的因果表述；处方场景名
（pasu / 1w4ts Voltaic 等）保持社群原名不译。
"""
from __future__ import annotations

# advice.py 静态回退：signal -> plain_language_meaning。
PLAIN_MEANINGS = {
    "decel_frac high": "Deceleration takes a long time after the peak speed",
    "decel_frac low": "Little time is left for continuous deceleration after the peak speed",
    "linearity high": "The deceleration-phase speed decline is not evenly paced",
    "sparc low": "The deceleration-phase speed profile contains many fast fluctuations",
    "reverse_ratio high": "Many reverse corrections appear at the end of the movement",
    "submovement two-stage": "The primary movement and the following correction look like two separate actions",
    "peak_position low": "The peak speed occurs early in the movement",
    "peak_position high": "The peak speed occurs late in the movement",
    "path_efficiency low": "The actual path is noticeably less direct than the start-to-end straight line",
    "peak_speed below reference": "Peak speed is below the current comparison reference",
    "throughput below reference": "Combined speed-and-accuracy efficiency is below the current comparison reference",
    "sensitivity high": "The current cm/360 is small; whether it affects control still needs an experiment",
}

# advice.py 静态回退：signal -> Finding.diagnosis 模板（str.format 插值）。
FINDING_DIAGNOSES = {
    "decel_frac high": (
        "Deceleration takes {decel_frac_pct:.0f}% of the whole flick — "
        "a long time is spent decelerating after the peak speed; the threshold "
        "still needs calibration with real product data."
    ),
    "decel_frac low": (
        "Deceleration takes only {decel_frac_pct:.0f}% and the continuous "
        "deceleration time is short; whether this is a braking issue still needs "
        "settle/reverse and individual history to verify."
    ),
    "linearity high": (
        "The deceleration-phase speed curve deviates {linearity:.2f} from a "
        "constant-deceleration line — the speed decline is not evenly paced. "
        "Note: this measures braking rhythm, not jitter; for jitter see SPARC."
    ),
    "sparc low": (
        "Deceleration smoothness SPARC={sparc:.1f} — the speed profile contains "
        "many fast fluctuations. SPARC describes the movement profile and does "
        "not directly measure grip tension; the absolute threshold still needs "
        "product calibration."
    ),
    "reverse_ratio high": (
        "{reverse_pct:.0f}% of deceleration-phase frames re-accelerate in the "
        "opposite direction, showing as repeated corrections."
    ),
    "submovement two-stage": (
        "The primary movement and the following correction appear as two "
        "fairly separated stages."
    ),
    "peak_position low": (
        "Peak position {peak_pos:.0f}% (early) — the peak speed occurs early; "
        "the specific movement cause is not directly measured by the input data."
    ),
    "peak_position high": (
        "Peak position {peak_pos:.0f}% (late) — the peak speed occurs late; "
        "the specific movement cause is not directly measured by the input data."
    ),
    "path_efficiency low": (
        "Flick path linearity {path_eff:.2f} — the actual path is noticeably "
        "less direct than the start-to-end straight line; the absolute "
        "threshold still needs product calibration."
    ),
    "peak_speed below reference": (
        "Flick angular speed {self_peak:.0f}°/s is only {ratio_pct:.0f}% of the "
        "reference ({ref_peak:.0f}°/s); peak speed is below the current "
        "reference, and the physical cause is not directly measured by the input data."
    ),
    "throughput below reference": (
        "Fitts throughput {self_tp:.1f} bits/s is only {tp_ratio_pct:.0f}% of "
        "the reference ({ref_tp:.1f}); combined speed-and-accuracy efficiency "
        "is below the current reference, and the physical cause is not directly "
        "measured by the input data (throughput is normalized by target "
        "distance/width, §6.3)."
    ),
    "sensitivity high": (
        "Current sensitivity is {cm_per_360:.1f} cm/360. A smaller cm/360 may "
        "amplify control input, but a setting value alone cannot establish a "
        "movement problem; treat it only as a controlled-experiment hypothesis."
    ),
}

# advice.py 静态回退：signal -> ((scenario, reason), ...)。
FINDING_PRESCRIPTIONS = {
    "decel_frac high": (
        ("pasu", "Practice the full accelerate→decelerate arc and commit to the brake near the target"),
        ("1w4ts Voltaic", "Hold 90%+ accuracy and complete each flick's acceleration and deceleration"),
    ),
    "decel_frac low": (
        ("pasu", "Practice even deceleration and treat the braking phase as its own action"),
    ),
    "linearity high": (
        ("pasu", "Drill the deceleration phase into a clean, continuous brake"),
        ("1w4ts 30% larger", "Deceleration-phase precision work"),
    ),
    "sparc low": (
        ("pasu", "clean lines — let deceleration speed fall continuously instead of hard-stopping"),
        ("1w4ts 30% larger", "Deceleration-phase precision work"),
    ),
    "reverse_ratio high": (
        ("pasu", "Fold corrections into the deceleration instead of stopping and re-correcting"),
        ("Multiclick", "Landing precision with fewer second corrections"),
    ),
    "submovement two-stage": (
        ("pasu", "Keep the primary movement and the finishing correction connected instead of pausing between them"),
        ("Multiclick", "Landing precision with fewer second corrections"),
    ),
    "peak_position low": (
        ("pasu", "Balance acceleration and deceleration and move the peak toward the middle"),
    ),
    "peak_position high": (
        ("Tile Frenzy", "Practice committing to acceleration and building speed"),
    ),
    "path_efficiency low": (
        ("linetrace", "Practice straight flicks along the shortest path"),
        ("clean lines", "Intent: the flick travels a straight line, not an arc"),
    ),
    "peak_speed below reference": (
        ("Tile Frenzy", "Raise dynamic speed step by step under controllable accuracy"),
        ("speed scenarios", "Accelerate boldly — chase speed first, then reclaim accuracy"),
    ),
    "throughput below reference": (
        ("Tile Frenzy", "Raise dynamic speed step by step under controllable accuracy"),
        ("speed scenarios", "Chase speed first, then reclaim accuracy"),
    ),
    "sensitivity high": (
        ("lower sens 5-10% (cm/360 ↑)", "Braking-assist experiment; re-test whether linearity/reverse drop, and revert if not"),
    ),
}

# _finalize_uncalibrated_findings 填充的可比条件/复测/停止规则。
VERIFICATION = {
    "comparable_requirements": ["same scenario", "same settings", "same evidence quality"],
    "insufficient_evidence_behavior": (
        "With insufficient samples or comparability, record the observation only — "
        "do not judge improvement or regression"
    ),
    "retest_after": "Retest under the same scenario, settings, and evidence quality",
    "stop_or_adjust_rule": (
        "If the target metric does not improve or accuracy clearly worsens, stop "
        "adjusting and return to the previous practice"
    ),
}

# advice_tracking.py 回退：signal -> plain_language_meaning。
TRACKING_PLAIN_MEANINGS = {
    "accuracy low": "This recording spends a small share of time with the crosshair on target",
    "loss count high": "This recording has many tracking interruptions",
    "off target long": "Returning to the target radius takes a long time after each interruption",
    "avg error high": "The average offset of the crosshair from the target center is large in this recording",
    "speed mismatch high": "The average speed difference between target and crosshair is large on miss segments",
    "accel mismatch high": "The average acceleration difference between target and crosshair is large on miss segments",
    "ptc high": "Acceleration error is high relative to spatial error on miss segments",
}

# advice_tracking.py 回退：signal -> Finding.diagnosis 模板与条件片段。
TRACKING_DIAGNOSES = {
    "accuracy low": (
        "On-target rate {on_target_pct:.1f}% is below the current experience "
        "reference of {threshold:.0f}% — this only says the share of on-target "
        "time is low in this recording; the reference line still needs product "
        "data calibration."
    ),
    "loss count high": (
        "This recording left the target {loss_count} times{per_loss} — many "
        "tracking interruptions; this metric alone cannot establish speed "
        "matching, visual reading, or physical control as the cause."
    ),
    "off target long": (
        "It takes an average of {off_per:.2f}s off target before returning — "
        "off-target durations are long in this recording; this alone cannot "
        "establish visual lock-on or reaction latency."
    ),
    "avg error high": (
        "Average error {avg_error_px:.1f}px{ctx} — the average offset of the "
        "crosshair from the target center is large in this recording; this "
        "alone cannot establish a physical or visual cause."
    ),
    "speed mismatch high": (
        "Average speed difference on miss segments {speed_mismatch:.0f} px/s — "
        "the target-vs-crosshair speed difference is large on miss segments; "
        "this alone cannot establish a physical or visual cause."
    ),
    "accel mismatch high": (
        "Average acceleration difference on miss segments {accel_mismatch:.0f} "
        "px/s² — the target-vs-crosshair acceleration difference is large on "
        "miss segments; this alone cannot establish a physical or visual cause."
    ),
    "ptc high": (
        "Miss-segment PTC={ptc:.0f} Hz² — it describes the ratio of "
        "acceleration error to spatial error; it does not directly measure "
        "muscle tension and cannot alone establish a physical cause."
    ),
}
TRACKING_PER_LOSS = ", {per_loss:.2f}s per return"
TRACKING_RATIO_CTX = " ({ratio:.0%} of target width)"
TRACKING_ABS_CTX = " (no ball_w; using the current uncalibrated absolute reference)"

TRACKING_PRESCRIPTIONS = {
    "accuracy low": (
        ("pasu", "Keep following the target's speed instead of chasing from behind it"),
        ("VT Multiclick 30% larger", "Prioritize a stable point of aim, then watch the on-target share"),
    ),
    "loss count high": (
        ("VT reactive tracking", "Keep continuous follow through direction changes; don't pre-guess the next direction"),
        ("Clover Raw Control", "Return with one continuous correction instead of oscillating compensation"),
    ),
    "off target long": (
        ("VT evasive tracking", "Return with one continuous movement instead of repeated stop-restart"),
        ("Clover Raw Control", "Recover continuous contact first, then raise speed"),
    ),
    "avg error high": (
        ("VT precise tracking", "Anchor on the target center and shrink the sustained offset first"),
        ("focus on crosshair gap", "Watch the crosshair-to-center gap and stop drifting to one side"),
    ),
    "speed mismatch high": (
        ("VT control tracking", "Follow the target's speed changes instead of sudden chases"),
        ("Clover Raw Control", "Stay glued with continuous movement instead of stop-then-accelerate"),
    ),
    "accel mismatch high": (
        ("VT reactive tracking", "Keep continuous follow through direction changes; don't pre-guess the next direction"),
    ),
    "ptc high": (
        ("exposure therapy: high sens + low FOV precise tracking", "Reduce continuous back-and-forth compensation; treat PTC changes as an exploratory signal only"),
    ),
}

TRACKING_VERIFICATION = {
    "comparable_requirements": [
        "same scenario",
        "same settings",
        "same recording duration",
        "same evidence quality",
    ],
    "insufficient_evidence_behavior": (
        "With insufficient samples or comparability, record the observation only — "
        "do not judge improvement or regression"
    ),
    "retest_after": (
        "Retest under the same scenario, settings, recording duration, and evidence quality"
    ),
    "stop_or_adjust_rule": (
        "If the target metric does not improve or on_target_pct clearly worsens, "
        "stop adjusting and return to the previous practice"
    ),
}

# diagnosis.py 硬编码（前端 contracts.ts 的 boilerplate 识别集合同步维护）。
PRIORITY_REASONS = {
    "watch": "Priority watch item for this run",
    "fix": "Priority fix item for this run",
}
UNCLASSIFIED = "Unclassified"

# profiles.py 冻结回退的画像 label 与根因三元组（en 翻译；zh 侧引用常量）。
ARCHETYPE_LABELS = {
    "long_decel": "Hard-accel / long-decel profile",
    "decel_jitter": "Deceleration-jitter profile",
    "two_stage": "Two-stage profile",
    "underpowered": "Below-reference speed-efficiency profile",
    "inefficient_path": "Inefficient-path profile",
    "fluid_precise": "Fluid-precision profile",
    "tension_locked": "High-PTC watch profile",
    "reactive_loser": "Reactive-lag profile",
    "precision_borderline": "Borderline-precision profile",
    "speed_overmatched": "Speed-overmatched profile",
    "fluid_tracker": "Fluid-tracking profile",
}
ROOT_CAUSES = {
    "decel_frac high": (
        "The deceleration phase is fairly long",
        "The evidence only shows the deceleration phase is fairly long",
        "Practice the full accelerate-decelerate arc and commit to the brake near the target",
    ),
    "decel_frac low": (
        "The deceleration share is low, with wall-hitting braking",
        "The input data shows a compressed deceleration phase but cannot alone establish insufficient or rough braking",
        "Practice even deceleration and treat the braking phase as its own action",
    ),
    "sparc low": (
        "The deceleration speed profile has many fast fluctuations",
        "The input data supports a non-continuous deceleration profile but cannot alone establish grip tension or other physical causes",
        "Deceleration-phase control stability",
    ),
    "reverse_ratio high": (
        "Repeated corrections in the deceleration phase",
        "The evidence only shows many reverse corrections",
        "Fold corrections into the deceleration instead of stopping and re-correcting",
    ),
    "submovement two-stage": (
        "The primary movement and the following correction form two fairly separated stages",
        "The evidence only shows the primary movement and correction are fairly separated",
        "Keep the primary movement and the finishing correction connected instead of pausing between them",
    ),
    "peak_speed below reference": (
        "Peak speed is below the current reference",
        "The specific movement cause is not directly measured by the input data",
        "Raise speed step by step under controllable accuracy",
    ),
    "throughput below reference": (
        "Combined speed-accuracy efficiency is below the current reference",
        "The input data shows below-reference efficiency but cannot alone establish a specific movement cause",
        "Practice speed-accuracy conversion in comparable scenarios",
    ),
    "linearity high": (
        "Uneven braking",
        "The input data shows uneven deceleration pacing but cannot alone establish a specific physical-control cause",
        "Even-paced braking practice",
    ),
    "path_efficiency low": (
        "The flick path wanders",
        "The input data shows low flick path efficiency but cannot alone establish a physical cause for the non-straight movement",
        "linetrace straight-line practice",
    ),
    "peak_position low": (
        "Peak position is early",
        "The input data shows the peak occurring early but cannot alone establish overly aggressive acceleration or a drawn-out deceleration",
        "Balance acceleration and deceleration",
    ),
    "peak_position high": (
        "Peak position is late",
        "The input data shows the peak occurring late but cannot alone establish insufficient acceleration or a slow start",
        "Commit to acceleration",
    ),
    "sensitivity high": (
        "The current cm/360 is small",
        "The input data records the current sensitivity but cannot alone establish it as the cause of a control problem",
        "Run only reversible sens experiments and retest",
    ),
    # --- tracking signals ---
    "accuracy low": (
        "Low hit rate",
        "The input data shows a low hit rate but cannot alone establish speed matching or fine-aim precision as the cause",
        "pasu + VT Multiclick landing work",
    ),
    "loss count high": (
        "Frequent off-target",
        "The input data shows many off-target events but cannot alone establish target-change reading or speed matching as the cause",
        "VT reactive tracking",
    ),
    "off target long": (
        "Slow return after going off target",
        "The input data shows long off-target durations but cannot alone establish delayed visual re-lock",
        "VT evasive + Clover Raw Control",
    ),
    "avg error high": (
        "Large error",
        "The input data shows a large average error but cannot alone establish a specific crosshair-control cause",
        "VT precise tracking + crosshair-gap awareness",
    ),
    "speed mismatch high": (
        "Misses on high-speed segments",
        "The input data shows rising error on high-speed segments but cannot alone establish a physical or visual cause of speed matching",
        "VT control tracking",
    ),
    "accel mismatch high": (
        "Misses on direction-change segments",
        "The input data shows rising error during acceleration changes but cannot alone establish a specific reactive-tracking cause",
        "VT reactive tracking",
    ),
    "ptc high": (
        "Possibly elevated tension",
        "The input data only shows elevated PTC; force density or tension is an unverified hypothesis without EMG",
        "Exposure therapy + lateral squeeze",
    ),
}
