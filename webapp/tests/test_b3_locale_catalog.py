"""B3 诊断/指标文案目录化测试：zh/en 双目录、locale 选档、job locale 落盘。

覆盖范围（按 .zcode/i18n-backend-design-2026-09-20.md §3.2）：
- metric_definitions zh/en 键集一致 + 投影按请求 locale 选目录；
- mapping official.v1.en.json 平行变体可加载可校验，copy 全英文且占位符一致；
- labels 目录（advice/advice_tracking/profiles/diagnosis 回退）双语文案；
- read_models 训练卡 en 变体 + presentation_label 模板；
- worker timeline label + job locale（结果语言＝生成时语言）；
- 知识包 warnings / runtime-status message 按请求 locale 出。
"""

from __future__ import annotations

import json
import re

import pytest
from httpx import ASGITransport, AsyncClient

from kovaak_tracker import metric_definitions
from kovaak_tracker.coach import mapping_rules
from kovaak_tracker.coach.knowledge_active import load_active_mapping
from kovaak_tracker.coach.labels import en as en_labels
from kovaak_tracker.coach.labels import zh as zh_labels
from webapp.backend import read_models
from webapp.backend.app import app

_CJK = re.compile(r"[\u4e00-\u9fff]")


# ---- metric_definitions（B2/B3 样板） ----


def test_metric_catalogs_share_key_sets():
    assert set(metric_definitions.METRIC_DEFINITIONS) == set(
        metric_definitions.METRIC_DEFINITIONS_EN_US,
    )


def test_metric_definition_selects_catalog_by_locale():
    zh = metric_definitions.get_metric_definition("decel_frac")
    en = metric_definitions.get_metric_definition("decel_frac", "en-US")
    assert zh["name"] == "减速占比"
    assert en["name"] == "Deceleration fraction"
    # 未知 locale 回落 zh；en 目录缺键回落 zh 目录。
    assert metric_definitions.get_metric_definition("decel_frac", "fr-FR") is zh
    assert metric_definitions.get_metric_definition("missing.metric") is None
    assert (
        metric_definitions.get_metric_definition("missing.metric", "en-US") is None
    )


@pytest.mark.asyncio
async def test_session_status_projects_definitions_by_request_locale():
    from webapp.backend import queue

    sid = await queue.enqueue("u1", "", "")
    session = await queue.claim_next("w-test")
    assert session is not None and session["id"] == sid
    session["status"] = "done"
    # 无 schema_version/legacy 键：读侧原样透传（只测投影层接 locale，不测
    # result 合同校验——那是 test_contracts 的职责）。
    session["result"] = {
        "deterministic": {"metrics": {"decel_frac": {"value": 0.5}}},
    }
    queue._save_session(session)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://t",
    ) as client:
        for locale, expected in (
            ("zh-CN", "减速占比"),
            ("en-US", "Deceleration fraction"),
        ):
            resp = await client.get(
                f"/api/sessions/{sid}",
                headers={"X-User-Id": "u1", "X-Locale": locale},
            )
            assert resp.status_code == 200, resp.text
            body = resp.json()
            definition = body["result"]["deterministic"]["metrics"]["decel_frac"][
                "definition"
            ]
            assert definition["name"] == expected


# ---- mapping 平行变体 ----


def test_official_mapping_en_variant_loads_and_validates():
    doc_zh, _ = load_active_mapping("zh-CN")
    doc_en, _ = load_active_mapping("en-US")
    assert doc_zh is not None and doc_en is not None
    assert doc_en is not doc_zh
    assert _CJK.search(doc_zh["archetypes"][0]["label"])
    assert not _CJK.search(doc_en["archetypes"][0]["label"])
    mapping_rules.validate_mapping(
        doc_en,
        vocabulary=mapping_rules._load_vocabulary(),
        registry=mapping_rules.validate_mapping.__globals__[
            "knowledge_active"
        ].load_active_registry(),
    )
    # 规则结构（信号/条件/阈值）两侧一致，只有 copy 不同。
    assert [r["signal"] for r in doc_en["static_clicking"]] == [
        r["signal"] for r in doc_zh["static_clicking"]
    ]
    assert set(doc_en["root_causes"]) == set(doc_zh["root_causes"])


def test_static_fallback_advise_uses_locale_catalog():
    from kovaak_tracker import advice

    summary = {"decel_frac": {"med": 0.72}}
    zh = advice.advise(summary)
    en = advice.advise(summary, locale="en-US")
    assert zh[0].plain_language_meaning == "速度峰值后用了较长时间完成减速"
    assert en[0].plain_language_meaning == (
        "Deceleration takes a long time after the peak speed"
    )
    assert not _CJK.search(en[0].diagnosis)
    assert en[0].prescriptions[0].reason == (
        "Practice the full accelerate→decelerate arc and commit to the brake "
        "near the target"
    )
    # zh 输出保持迁移前口径（golden 在 tests/coach 已锁，此处锁 locale 维度）。
    assert _CJK.search(zh[0].diagnosis)


_LABEL_CATALOG_MEMBERS = (
    "PLAIN_MEANINGS", "FINDING_DIAGNOSES", "FINDING_PRESCRIPTIONS",
    "VERIFICATION", "TRACKING_PLAIN_MEANINGS", "TRACKING_DIAGNOSES",
    "TRACKING_PRESCRIPTIONS", "TRACKING_VERIFICATION", "PRIORITY_REASONS",
    "UNCLASSIFIED", "ARCHETYPE_LABELS", "ROOT_CAUSES",
)


def test_labels_catalogs_are_structurally_mirrored():
    for member in _LABEL_CATALOG_MEMBERS:
        zh_value = getattr(zh_labels, member, None)
        en_value = getattr(en_labels, member, None)
        assert zh_value is not None and en_value is not None, member
        if isinstance(zh_value, dict):
            assert set(zh_value) == set(en_value), member


def test_build_diagnosis_priority_reason_follows_locale():
    from kovaak_tracker.coach.diagnosis import build_diagnosis

    zh = build_diagnosis([], {}, None, {"summary_type": "flicking"})
    en = build_diagnosis([], {}, None, {"summary_type": "flicking", "locale": "en-US"})
    assert zh.profile.label == "流体精度型"
    assert en.profile.label == "Fluid-precision profile"


# ---- read_models：训练卡 + presentation_label ----


def test_current_training_catalogs_share_ref_keys():
    assert set(read_models._CURRENT_TRAINING_ZH_CN) == set(
        read_models._CURRENT_TRAINING_EN_US,
    )
    ref = "knowledge:static.flicking-terminal-control@3"
    assert read_models._CURRENT_TRAINING_ZH_CN[ref]["scenario_profile_ref"] == (
        read_models._CURRENT_TRAINING_EN_US[ref]["scenario_profile_ref"]
    )


def test_record_presentation_label_locale_templates():
    zh = read_models.build_record_presentation_label(
        scenario=None, training_at=None, analysis_completed_at=None,
    )
    en = read_models.build_record_presentation_label(
        scenario=None, training_at=None, analysis_completed_at=None, locale="en-US",
    )
    assert zh == "未命名场景 | 训练：训练时间未知 | 分析：分析尚未完成"
    assert en == (
        "Unnamed scenario | Trained: training time unknown | "
        "Analyzed: analysis not finished yet"
    )


# ---- worker：timeline label + job locale ----


def test_build_timeline_labels_by_locale():
    from webapp.backend import worker

    extras = {"fps": 60, "flicks": [{"peak_frame": 10}], "kill_frames": [20]}
    zh = worker._build_timeline(extras)
    en = worker._build_timeline(extras, "en-US")
    assert [e["label"] for e in zh] == ["速度峰值", "击杀"]
    assert [e["label"] for e in en] == ["Peak speed", "Kill"]


def test_job_locale_defaults_and_normalizes():
    from webapp.backend import worker

    assert worker._job_locale({}) == "zh-CN"
    assert worker._job_locale({"locale": "en-US"}) == "en-US"
    assert worker._job_locale({"locale": "fr-FR"}) == "zh-CN"


@pytest.mark.asyncio
async def test_enqueue_persists_job_locale():
    from webapp.backend import queue

    sid = await queue.enqueue("u1", "", "", locale="en-US")
    session = await queue.get_session(sid)
    assert session["locale"] == "en-US"


def test_native_diagnosis_follows_locale():
    from webapp.backend import worker

    metrics = {
        "decel_frac": {"availability": "available", "med": 0.72},
    }
    zh = worker._native_diagnosis(metrics)
    en = worker._native_diagnosis(metrics, locale="en-US")
    zh_issue = zh["issues"][0]
    en_issue = en["issues"][0]
    assert zh_issue["plain_language_meaning"] == "速度峰值后用了较长时间完成减速"
    assert en_issue["plain_language_meaning"] == (
        "Deceleration takes a long time after the peak speed"
    )
    assert zh_issue["priority_reason"] == "本次优先观察项"
    assert en_issue["priority_reason"] == "Priority watch item for this run"
    assert zh["profile"]["label"] == "急加速-长减速型"
    assert en["profile"]["label"] == "Hard-accel / long-decel profile"


# ---- 知识包 warnings / runtime-status ----


def test_pack_warnings_locale_catalog():
    from webapp.backend import routes

    assert _CJK.search(routes._pack_warning("sidecar_applied", "zh-CN"))
    assert routes._pack_warning("sidecar_applied", "en-US") == (
        "Knowledge base switched; the next conversation uses the new knowledge base"
    )


def test_runtime_status_messages_locale_catalog():
    from webapp.backend import health

    assert "教练引擎已就绪" == health._RUNTIME_STATUS_MESSAGES["ready"]["zh-CN"]
    assert health._RUNTIME_STATUS_MESSAGES["ready"]["en-US"] == "Coach engine is ready"
    assert set(health._RUNTIME_STATUS_MESSAGES["warming_up"]) == {"zh-CN", "en-US"}
