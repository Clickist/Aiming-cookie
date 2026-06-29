"""LLM narration of a CoachDiagnosis. Diagnosis is rule-engineered (deterministic);
the LLM only translates structured data into coach-voice prose. No diagnosis
reasoning is delegated to the LLM (anti-hallucination).

渐进式上下文：system prompt = 常驻框架(BASE_SYSTEM_PROMPT) + 本次诊断触发的
signal 对应的社区知识(build_system_prompt 按需拼装)，而非全量预加载。
知识源见 :mod:`kovaak_tracker.coach.knowledge`。
"""
from __future__ import annotations

import json
from dataclasses import asdict

from .diagnosis import CoachDiagnosis
from .knowledge import KNOWLEDGE
from .providers import LLMBackend

# 常驻框架：角色 + 讲解规则 + 铁律 + 最核心理论锚。
# signal-specific 的社区知识由 build_system_prompt 按诊断结果渐进式注入。
BASE_SYSTEM_PROMPT = (
    "你是一位 KovaaK's flicking 教练，精通运动学理论（min-jerk / Becker 减速段 / "
    "submovement / Fitts / SPARC）+ Voltaic 社区实践。\n\n"
    "核心理论锚：减速段是命中成败最强信号（Becker）；flick = 初始甩枪 + corrective 修正（submovement）；"
    "SPARC 度量减速平滑度；Fitts 速度-精度权衡。\n\n"
    "【讲解规则】：\n"
    "1. 中文，150-300 字。结构：流派画像 → 头号问题 + 根因（症状→物理→训练）→ 最优先训练建议\n"
    "2. **英文术语必须配人话解释**——首次出现写成「中文（英文）」并一句话说清。例："
    "「减速段占比过高（decel_frac）——flick 后刹车那段拖太长」"
    "「减速平滑度差（SPARC 低）——速度降得不顺、有抖动」"
    "「两段式（two-stage）——甩过去后停一下再单独微调，不是一气呵成」\n"
    "3. 铁律：只基于提供的诊断数据讲解，不要编造任何指标数值或未给出的信息；数据缺失就略过。"
    "下方【相关社区知识】仅供解释已给出的诊断、让建议更具体权威——禁止用它反推未给出的指标或信号\n"
    "4. 语气具体、可执行，不空话。可用比喻（如「蹭着瞄」「拖刹车」）但每条建议落到具体动作/场景"
)


def build_system_prompt(diagnosis: CoachDiagnosis) -> str:
    """渐进式组装 system prompt：BASE 框架 + 本次诊断触发的 signal 对应社区知识。

    只有 ``diagnosis.issues`` 实际出现的 signal 的知识进入 prompt（规则驱动
    检索），而非全量预加载——prompt 大小 ∝ 触发信号数，不 ∝ 知识库总量。
    signal 字符串契约见 :data:`kovaak_tracker.coach.knowledge.KNOWLEDGE`。
    """
    blocks = []
    for issue in diagnosis.issues:
        k = KNOWLEDGE.get(issue.signal)
        if not k:
            continue
        part = f"[{issue.signal}] {k['community']}"
        cues = k.get("cues")
        if cues:
            part += " 可参考：" + "；".join(cues) + "。"
        blocks.append(part)
    if not blocks:
        return BASE_SYSTEM_PROMPT
    return (
        BASE_SYSTEM_PROMPT
        + "\n\n【相关社区知识】（仅本次诊断触发的信号，讲解时可引用）：\n"
        + "\n".join(blocks)
    )


def generate_narration(diagnosis: CoachDiagnosis, backend: LLMBackend) -> str:
    return backend.generate(build_system_prompt(diagnosis), build_user_prompt(diagnosis))


def build_user_prompt(diagnosis: CoachDiagnosis) -> str:
    payload = {
        "profile": asdict(diagnosis.profile),
        "issues": [
            {
                "priority": i.priority, "signal": i.signal, "severity": i.severity,
                "priority_reason": i.priority_reason,
                "root_causes": [{"level": rc.level, "text": rc.text} for rc in i.root_causes],
                "prescriptions": [{"scenario": p.scenario, "reason": p.reason}
                                  for p in i.prescriptions],
            }
            for i in diagnosis.issues
        ],
        "comparison": diagnosis.comparison,
        "meta": diagnosis.meta,
    }
    return json.dumps(payload, ensure_ascii=False)


PROGRESS_SYSTEM_PROMPT = (
    "你是一位 KovaaK's flicking 教练，精通运动学理论（min-jerk / Becker 减速段 / "
    "submovement / Fitts / SPARC）+ Voltaic 社区实践。\n"
    "你会收到玩家的历史趋势 + 多基准对比数据（JSON）。请用中文写一段进步解读（150-300 字）："
    "先总结进步方向（哪些指标改善了/退步了，引用趋势和对比 verdict），"
    "再结合基线/上次/高手参考定位当前水平，最后给下一阶段训练重点。\n"
    "**英文术语必须配人话解释**——首次出现写成「中文（英文）」并一句话说清。例："
    "「减速段占比（decel_frac）改善——flick 后刹车那段不再拖太长」"
    "「减速平滑度（SPARC）变好——速度降得更顺、抖动减少」\n"
    "可参考社区实践：static clicking 用 arm flick + wrist micro + hit-confirm；"
    "pasu 练完整加减速循环；VDIM 5-10 runs/scenario；static 推荐 40+ cm/360。\n"
    "铁律：只基于提供的数据讲解，不要编造任何指标数值或未给出的信息；数据缺失就略过。"
)
# 注：progress narration 消费的是趋势数据而非逐条 signal，暂保持静态 prompt；
# 未来若按趋势变化的 signal 做检索，可复用 build_system_prompt 的渐进式模式。


def generate_progress_narration(trend, comparison, backend) -> str:
    return backend.generate(PROGRESS_SYSTEM_PROMPT, _build_progress_user_prompt(trend, comparison))


def _build_progress_user_prompt(trend, comparison) -> str:
    payload = {"trend": {m: series for m, series in trend.items()}, "comparison": comparison}
    return json.dumps(payload, ensure_ascii=False, default=str)
