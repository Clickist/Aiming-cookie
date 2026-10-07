"""能力域 → 标准答案场景锚点表（施工单⑥；初版**待点点审**，选图可换）。

背景（白名单审计病灶2 + coach 链条审计环5）：advice 静态处方层自施工单⑥起
只保留"信号→能力域处方"层（labels.FINDING_PRESCRIPTIONS/TRACKING_PRESCRIPTIONS，
不指向具体图名）；"能力域→具体练什么图"由本表承担，替换旧的十几个写死
场景名。

选图准则 = 三路接力（coach-system 检索纪律的数据面）：
1. 本机已装的标准答案优先（local Scenarios 有 <名字>.sce → local_installed）；
2. 未装的推荐并标注需订阅创意工坊（needs_subscription）；
3. 无对症标准答案 → 保留能力域处方并提示走 sce_forge 定制或 scenario.search
   （custom_fallback）。

数据出处（每张锚点：能力域/场景名/一句话为什么是标准答案/出处/工坊或本机
标识）：场景与 origin 取自研究语料指纹库
``.zcode/route-mining/full_extract/_index.json``（rel_path 前缀 local/ 或
workshop/<id>/）；"为什么"与链号取自
``.zcode/route-mining/capability-vocabulary.md`` v1.3 坐实链与 registry.v14。

fail-open：install_dir 缺失/探测失败 → status="unverified"，绝不抛异常、
绝不伪造"本机已装"。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

ANCHOR_TABLE_VERSION = "standard_anchors.v0.1"
# 初版人工从指纹库+词汇表挑选，未经点点逐张审定：选图可换，审后升正式。
ANCHOR_TABLE_REVIEW_STATUS = "draft_pending_review"

# 能力域 id 与 labels.CAPABILITY_DOMAINS / sce_reading.training.domains 同词表。
DOMAINS = (
    "reactive_change",
    "smooth_tracking",
    "static_positioning",
    "micro_adjust",
    "confirm_timing",
    "reset_management",
    "target_switching",
    "target_reading",
    "pressure_pacing",
)

# status 取值（三路接力的可见结果）。
STATUS_LOCAL_INSTALLED = "local_installed"
STATUS_NEEDS_SUBSCRIPTION = "needs_subscription"
STATUS_UNVERIFIED = "unverified"          # install_dir 不可用，未探测
STATUS_CUSTOM_FALLBACK = "custom_fallback"  # 域内无锚点，走定制/search

# 锚点条目字段：domain / scenario / why（一句话为什么是标准答案）/
# source（链 ID 或官方文档）/ origin（local | workshop）/ workshop_id。
STANDARD_ANCHORS: tuple[dict[str, str], ...] = (
    {
        "domain": "reactive_change",
        "scenario": "Humanoid Strafe",
        "why": "变向密度十档递进（每档 -0.1s）的标准载体，域1 处方语法的逐值复核样本",
        "source": "capability-vocabulary 域1【坐实链 C60】【档位统计：Humanoid Strafe 十档 1.2→0.4】",
        "origin": "local",
        "workshop_id": "",
    },
    {
        "domain": "smooth_tracking",
        "scenario": "Centering I 180 no strafes",
        "why": "LR 60-60 长周期档删除变向变量，误差只来自速度匹配与张力（可归因面最小），r10 细靶放大张力",
        "source": "capability-vocabulary 域2【坐实链 C51 同构/MO04 坐实键】",
        "origin": "local",
        "workshop_id": "",
    },
    {
        "domain": "smooth_tracking",
        "scenario": "Smooth Thin Strafe Always Right",
        "why": "C51 坐实链本体：LR 1000-1000 单向长滑，速度匹配误差直接变命中流稀密",
        "source": "capability-vocabulary 域2【坐实链 C51 Smooth Thin LR1000-1000】",
        "origin": "workshop",
        "workshop_id": "2816737380",
    },
    {
        "domain": "static_positioning",
        "scenario": "Wide Wall 3 Targets",
        "why": "r120 建速档：建速→收精度两步处方的标准起点（同族只动尺寸一个变量）",
        "source": "capability-vocabulary 域3【坐实链 C34/C17/C32】",
        "origin": "local",
        "workshop_id": "",
    },
    {
        "domain": "micro_adjust",
        "scenario": "1wall 6targets extra small",
        "why": "r8 微靶 + 宽分布（span 1856uu）把微调与视线分配变显性主任务",
        "source": "capability-vocabulary 域3/域4【坐实链 KL02/C22】",
        "origin": "local",
        "workshop_id": "",
    },
    {
        "domain": "micro_adjust",
        "scenario": "Amare GarfMicroTS",
        "why": "r24+多发杀(hp50)+慢漂：微调窗口被计分结构强制存在的纯指尖场",
        "source": "capability-vocabulary 域4【坐实链 C93】",
        "origin": "local",
        "workshop_id": "",
    },
    {
        "domain": "confirm_timing",
        "scenario": "1w6ts reload",
        "why": "自毁 250–500ms 六档把确认窗口从自觉变机制，确认显性化的标准阶梯",
        "source": "capability-vocabulary 域5【坐实链 C42（1w6ts 六档）】",
        "origin": "workshop",
        "workshop_id": "1700302825",
    },
    {
        "domain": "reset_management",
        "scenario": "1w2ts reload",
        "why": "回中基准图：两靶墙图同压微调+长距停稳+切换，复位域诊断位的标准载体",
        "source": "capability-vocabulary 域6【对照坐实 KL13】",
        "origin": "local",
        "workshop_id": "",
    },
    {
        "domain": "target_switching",
        "scenario": "2t Chain Mc",
        "why": "巡逻链+限弹点击把转火衔接（甩→接→再甩）的连续性写成计分",
        "source": "capability-vocabulary 域7【坐实链 C31/KL23】",
        "origin": "workshop",
        "workshop_id": "3376315733",
    },
    {
        "domain": "target_reading",
        "scenario": "Ground Plaza",
        "why": "10 bot 可分类行为库，可读性递进路线（Ground Plaza→STRAFETRACK→blink…）的起点",
        "source": "capability-vocabulary 域8【坐实链 C60/KL12/MO02】",
        "origin": "workshop",
        "workshop_id": "1541094512",
    },
    {
        "domain": "pressure_pacing",
        "scenario": "Tile Frenzy",
        "why": "30s 大靶速死换位：吞吐节奏（BPM=每秒击杀×60 配速法）的天然基准载体",
        "source": "registry.v14 community.aimwiki.metronome-pacing-method + 指纹库 workshop/1352676992",
        "origin": "workshop",
        "workshop_id": "1352676992",
    },
)

# 域 → 锚点元组（保持表内顺序即优先序）。
_BY_DOMAIN: dict[str, tuple[dict[str, str], ...]] = {
    domain: tuple(e for e in STANDARD_ANCHORS if e["domain"] == domain)
    for domain in DOMAINS
}

_STATUS_NOTE = {
    "zh-CN": {
        STATUS_LOCAL_INSTALLED: "本机已装",
        STATUS_NEEDS_SUBSCRIPTION: "需订阅创意工坊",
        STATUS_UNVERIFIED: "安装状态未验证",
        STATUS_CUSTOM_FALLBACK: "无对症标准答案：可走 sce_forge 定制或 scenario.search",
    },
    "en-US": {
        STATUS_LOCAL_INSTALLED: "installed locally",
        STATUS_NEEDS_SUBSCRIPTION: "workshop subscription required",
        STATUS_UNVERIFIED: "install status unverified",
        STATUS_CUSTOM_FALLBACK: (
            "no matching standard anchor: use sce_forge customization or scenario.search"
        ),
    },
}


def anchors_for_domain(domain: str) -> tuple[dict[str, str], ...]:
    """域 → 锚点条目（保持表内优先序；无锚点域返回空）。"""
    return _BY_DOMAIN.get(domain, ())


def _local_scenario_dir(install_dir: str | Path | None) -> Path | None:
    if not install_dir:
        return None
    return (
        Path(install_dir) / "FPSAimTrainer" / "Saved" / "SaveGames" / "Scenarios"
    )


def anchor_status(scenario: str, install_dir: str | Path | None) -> str:
    """三路接力第 1/2 步的探测（fail-open）。

    本机 local Scenarios 存在同 .sce → local_installed；否则按表的 origin
    登记（workshop → needs_subscription）；探测不可用 → unverified。
    只查文件存在性，不解析 .sce（轻量、绝不抛异常）。
    """
    scenarios = _local_scenario_dir(install_dir)
    if scenarios is None:
        return STATUS_UNVERIFIED
    try:
        if (scenarios / f"{scenario}.sce").exists():
            return STATUS_LOCAL_INSTALLED
    except OSError:
        return STATUS_UNVERIFIED
    entry = next(
        (e for e in STANDARD_ANCHORS if e["scenario"] == scenario), None,
    )
    if entry is not None and entry["origin"] == "workshop":
        return STATUS_NEEDS_SUBSCRIPTION
    return STATUS_UNVERIFIED


def resolve_domain_anchors(
    domain: str,
    *,
    install_dir: str | Path | None = None,
    locale: str = "zh-CN",
) -> list[dict[str, Any]]:
    """能力域 → 带接力状态的标准答案列表（消费端面向用户的最小单元）。"""
    notes = _STATUS_NOTE.get(locale) or _STATUS_NOTE["zh-CN"]
    resolved: list[dict[str, Any]] = []
    for entry in anchors_for_domain(domain):
        status = anchor_status(entry["scenario"], install_dir)
        resolved.append({
            "domain": entry["domain"],
            "scenario": entry["scenario"],
            "why": entry["why"],
            "source": entry["source"],
            "origin": entry["origin"],
            **({"workshop_id": entry["workshop_id"]} if entry["workshop_id"] else {}),
            "status": status,
            "status_note": notes.get(status, status),
            "anchor_table_version": ANCHOR_TABLE_VERSION,
            "review_status": ANCHOR_TABLE_REVIEW_STATUS,
        })
    return resolved


def expand_prescriptions(
    prescriptions: list[Any],
    *,
    install_dir: str | Path | None = None,
    locale: str = "zh-CN",
    domain_names: dict[str, str] | None = None,
) -> list[Any]:
    """把能力域级处方按锚点表扩成场景级处方（三路接力落地）。

    消费端：``coach.report.build_report``。规则：
    - ``prescription.scenario`` 命中能力域 id（在 domain_names 里）→ 按域扩成
      1-3 张标准答案（Prescription.scenario=场景名，reason=原动作+为什么+状态，
      source_level="coach_first_party"）；
    - 域内无锚点 → 保留原处方，reason 追加"无对症标准答案：走定制/search"；
    - 非"降 sens"等非域能力动作原样透传。
    """
    from ..advice import Prescription

    names = domain_names or {}
    notes = _STATUS_NOTE.get(locale) or _STATUS_NOTE["zh-CN"]
    expanded: list[Any] = []
    for prescription in prescriptions:
        domain = getattr(prescription, "scenario", None)
        if domain not in names:
            expanded.append(prescription)
            continue
        action = prescription.reason
        domain_label = names.get(domain, domain)
        anchors = resolve_domain_anchors(domain, install_dir=install_dir, locale=locale)
        if not anchors:
            expanded.append(Prescription(
                scenario=domain,
                reason=(
                    f"{action}（{domain_label}；{notes[STATUS_CUSTOM_FALLBACK]}）"
                ),
                cue=action,
                purpose=prescription.purpose,
                target_metrics=list(prescription.target_metrics),
                expected_direction=list(prescription.expected_direction),
                retest_after=prescription.retest_after,
                stop_or_adjust_rule=prescription.stop_or_adjust_rule,
                source_level=prescription.source_level,
            ))
            continue
        for anchor in anchors:
            expanded.append(Prescription(
                scenario=anchor["scenario"],
                reason=(
                    f"{action}（{domain_label}标准答案：{anchor['why']}；"
                    f"{anchor['status_note']}）"
                ),
                cue=action,
                purpose=prescription.purpose,
                target_metrics=list(prescription.target_metrics),
                expected_direction=list(prescription.expected_direction),
                retest_after=prescription.retest_after,
                stop_or_adjust_rule=prescription.stop_or_adjust_rule,
                source_level="coach_first_party",
            ))
    return expanded


__all__ = [
    "ANCHOR_TABLE_REVIEW_STATUS",
    "ANCHOR_TABLE_VERSION",
    "DOMAINS",
    "STANDARD_ANCHORS",
    "anchor_status",
    "anchors_for_domain",
    "expand_prescriptions",
    "resolve_domain_anchors",
]
