"""施工单⑥：能力域→标准答案锚点表与三路接力。

契约：
- 表覆盖九域、每域 1-3 张；每张带 domain/scenario/why/source/origin；
- 初版整体标 draft_pending_review（待点点审，选图可换）；
- 接力：本机已装 local_installed → 未装 workshop 登记的 needs_subscription →
  install_dir 缺失 unverified → 域内无锚 custom_fallback 提示；
- expand_prescriptions 只扩能力域 id 处方，非域动作（降 sens）原样透传。
"""
from __future__ import annotations

from dataclasses import asdict

from kovaak_tracker.advice import Prescription
from kovaak_tracker.coach.labels import catalog
from kovaak_tracker.coach.standard_anchors import (
    ANCHOR_TABLE_REVIEW_STATUS,
    ANCHOR_TABLE_VERSION,
    DOMAINS,
    anchor_status,
    anchors_for_domain,
    expand_prescriptions,
    resolve_domain_anchors,
)


def test_anchor_table_covers_all_nine_domains_with_one_to_three_each():
    assert set(DOMAINS) == set(catalog("zh-CN").CAPABILITY_DOMAINS)
    for domain in DOMAINS:
        anchors = anchors_for_domain(domain)
        assert 1 <= len(anchors) <= 3, domain
        for entry in anchors:
            assert entry["domain"] == domain
            assert entry["scenario"]
            assert entry["why"]
            assert entry["source"]
            assert entry["origin"] in ("local", "workshop")
            if entry["origin"] == "workshop":
                assert entry["workshop_id"].isdigit()


def test_anchor_table_marks_draft_review_status():
    assert ANCHOR_TABLE_REVIEW_STATUS == "draft_pending_review"
    assert ANCHOR_TABLE_VERSION == "standard_anchors.v0.1"
    # 状态标注可随解析结果带出（待点点审的提示不丢失）。
    resolved = resolve_domain_anchors("pressure_pacing", install_dir=None)
    assert resolved
    assert all(r["review_status"] == "draft_pending_review" for r in resolved)
    assert all(r["anchor_table_version"] == ANCHOR_TABLE_VERSION for r in resolved)


def test_relay_local_install_wins(tmp_path):
    scenarios = (
        tmp_path / "FPSAimTrainer" / "Saved" / "SaveGames" / "Scenarios"
    )
    scenarios.mkdir(parents=True)
    (scenarios / "Tile Frenzy.sce").write_bytes(b"Name=Tile Frenzy\n")
    assert anchor_status("Tile Frenzy", tmp_path) == "local_installed"
    resolved = resolve_domain_anchors("pressure_pacing", install_dir=tmp_path)
    by_name = {r["scenario"]: r for r in resolved}
    assert by_name["Tile Frenzy"]["status"] == "local_installed"
    assert by_name["Tile Frenzy"]["status_note"] == "本机已装"


def test_relay_workshop_entry_needs_subscription(tmp_path):
    empty = tmp_path / "kovaak"
    (empty / "FPSAimTrainer" / "Saved" / "SaveGames" / "Scenarios").mkdir(
        parents=True,
    )
    assert anchor_status("Tile Frenzy", empty) == "needs_subscription"
    resolved = resolve_domain_anchors("pressure_pacing", install_dir=empty)
    assert resolved[0]["status"] == "needs_subscription"
    assert resolved[0]["status_note"] == "需订阅创意工坊"
    assert resolved[0]["workshop_id"] == "1352676992"


def test_relay_unverified_when_install_dir_missing():
    assert anchor_status("Tile Frenzy", None) == "unverified"
    resolved = resolve_domain_anchors("pressure_pacing", install_dir=None)
    assert resolved[0]["status"] == "unverified"
    assert resolved[0]["status_note"] == "安装状态未验证"


def test_expand_prescriptions_expands_domains_and_passes_through_actions():
    domain_names = catalog("zh-CN").CAPABILITY_DOMAINS
    prescriptions = [
        Prescription("pressure_pacing", "节拍配速：按命中率带加减"),
        Prescription("降 sens 5-10%（cm/360 ↑）", "制动辅助实验"),
    ]
    expanded = expand_prescriptions(prescriptions, domain_names=domain_names)
    assert [p.scenario for p in expanded] == [
        a["scenario"] for a in anchors_for_domain("pressure_pacing")
    ] + ["降 sens 5-10%（cm/360 ↑）"]
    pacing = expanded[0]
    assert pacing.reason.startswith("节拍配速：按命中率带加减")
    assert "标准答案" in pacing.reason
    assert pacing.cue == "节拍配速：按命中率带加减"
    assert pacing.source_level == "coach_first_party"
    assert expanded[-1].source_level == "community_consensus"  # 原样透传


def test_expand_prescriptions_custom_fallback_when_domain_unanchored():
    domain_names = dict(catalog("zh-CN").CAPABILITY_DOMAINS)
    # 构造一个表内无锚点的域：临时移除 pressure_pacing 锚点不可行（常量表），
    # 改用一个只在 domain_names 中登记的假域验证 custom_fallback 分支语义。
    domain_names["custom_probe_domain"] = "探针域"
    expanded = expand_prescriptions(
        [Prescription("custom_probe_domain", "探针动作")],
        domain_names=domain_names,
    )
    assert len(expanded) == 1
    assert expanded[0].scenario == "custom_probe_domain"
    assert "无对症标准答案" in expanded[0].reason
    assert "sce_forge" in expanded[0].reason
    assert expanded[0].cue == "探针动作"


def test_advice_findings_carry_domain_level_prescriptions_only():
    """拆分契约：advice 产出的处方不再含具名场景（锚点扩展前的中间层）。"""
    from kovaak_tracker.advice import advise

    summary = {
        "reverse_ratio": {"med": 0.4},
        "peak_position_pct": {"med": 65.0},
        "peak_speed_deg": {"med": 5.0},
        "throughput": {"med": 5.0},
    }
    reference = {"peak_speed_deg": {"med": 10.0}, "throughput": {"med": 10.0}}
    findings = advise(summary, reference, None, "zh-CN")
    for finding in findings:
        for prescription in finding.prescriptions:
            assert prescription.scenario in (
                set(catalog("zh-CN").CAPABILITY_DOMAINS) | {"降 sens 5-10%（cm/360 ↑）"}
            ), (finding.signal, asdict(prescription))
