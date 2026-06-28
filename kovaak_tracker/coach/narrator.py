"""LLM narration of a CoachDiagnosis. Diagnosis is rule-engineered (deterministic);
the LLM only translates structured data into coach-voice prose. No diagnosis
reasoning is delegated to the LLM (anti-hallucination)."""
from __future__ import annotations

import json
from dataclasses import asdict

from .diagnosis import CoachDiagnosis
from .providers import LLMBackend

SYSTEM_PROMPT = (
    "你是一位 KovaaK's flicking 教练，擅长用运动学（min-jerk / Becker 减速段 / "
    "submovement / Fitts）诊断瞄准问题并给训练处方。"
    "你会收到一份结构化诊断（JSON）。请用中文写一段教练讲解（150-300 字），"
    "结构：先点出玩家的流派画像，再讲头号问题及其根因（症状→物理→训练），"
    "最后给最优先的训练建议。"
    "铁律：只基于提供的诊断数据讲解，不要编造任何指标数值或未给出的信息；"
    "如果某数据缺失，就略过不提。"
    "描述每个问题时用症状层（symptom 字段）的中文文本，不要照搬 issue 里的"
    "英文 signal 标识符（如 decel_frac high 应说成「减速段占比过高」）。"
    "语气具体、可执行，不空话。"
)


def generate_narration(diagnosis: CoachDiagnosis, backend: LLMBackend) -> str:
    return backend.generate(SYSTEM_PROMPT, build_user_prompt(diagnosis))


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
    "你是一位 KovaaK's flicking 教练。你会收到玩家的历史趋势 + 多基准对比数据（JSON）。"
    "请用中文写一段进步解读（150-300 字）：先总结进步方向（哪些指标改善了/退步了，"
    "引用趋势和对比 verdict），再结合基线/上次/高手参考定位当前水平，"
    "最后给下一阶段训练重点。"
    "铁律：只基于提供的数据讲解，不要编造任何指标数值或未给出的信息；数据缺失就略过。"
)


def generate_progress_narration(trend, comparison, backend) -> str:
    return backend.generate(PROGRESS_SYSTEM_PROMPT, _build_progress_user_prompt(trend, comparison))


def _build_progress_user_prompt(trend, comparison) -> str:
    payload = {"trend": {m: series for m, series in trend.items()}, "comparison": comparison}
    return json.dumps(payload, ensure_ascii=False, default=str)
