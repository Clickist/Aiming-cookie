from __future__ import annotations

from copy import deepcopy

import pytest

from webapp.backend.coach_context import project_coach_diagnostic_context


def _v1_context() -> dict:
    return project_coach_diagnostic_context(
        {
            "schema_version": "analysis_result.v1",
            "analysis_id": "analysis:legacy",
            "summary_type": "flicking",
            "deterministic": {
                "diagnosis": {
                    "profile": {},
                    "issues": [],
                    "summary": {"legacy_metric": {"value": 1.0, "unit": "ratio"}},
                    "comparison": None,
                    "meta": {},
                }
            },
            "artifact_manifest": {"inputs": []},
        }
    )


def _v2_context() -> dict:
    return {
        "schema_version": "coach_diagnostic_context.v2",
        "analysis_ref": {
            "analysis_id": "analysis:42",
            "analysis_result_version": "analysis_result.v2",
            "analysis_type": "flicking",
            "input_mode": "input_native",
        },
        "scenario": {
            "scenario_profile_ref": None,
            "analyzer_refs": ["native_flicking.v1"],
            "support_status": "supported",
            "limitations": [],
        },
        "run_facts": {
            "mode": "unavailable",
            "limitations": ["facts_not_available"],
        },
        "diagnosis": {
            "profile": {},
            "issues": [],
            "summary": {},
            "comparison": None,
            "meta": {},
        },
        "evidence_summary": {
            "availability": {"stats": "available"},
            "alignment": {"status": "aligned"},
            "segment_refs": [],
        },
        "trends": [],
        "training": {"active_plan_ref": None, "recent_retest_ref": None},
        "limitations": [],
    }


def _v3_context() -> dict:
    context = deepcopy(_v2_context())
    context["schema_version"] = "coach_diagnostic_context.v3"
    context["processed_events"] = {
        "mode": "table_refs",
        "tables": [{
            "schema_version": "processed_event_table.v1",
            "table_ref": "analysis:42:table:static_flick",
            "analysis_ref": "analysis:42",
            "analyzer_ref": "native_flicking.v1",
            "family": "static_clicking",
            "event_kind": "static_flick",
            "row_count": 2,
            "included_count": 2,
            "excluded_count": 0,
            "completeness": "complete",
            "field_catalog": [{
                "field_key": "corrective_count",
                "role": "metric",
                "value_type": "number",
                "unit": "count",
                "metric_key": "static_clicking.corrective_count",
                "metric_version": "native_flicking.v1",
                "expected_direction": "comparison_only",
                "limitations": [],
            }],
            "index_fields": ["corrective_count"],
            "rows_ref": "analysis:42:table:static_flick",
            "limitations": [],
        }],
        "query_capabilities": [
            "analysis.events.list",
            "analysis.events.get",
            "analysis.events.rank",
            "analysis.events.filter",
            "analysis.events.aggregate",
            "analysis.events.co_occurrence",
            "analysis.events.sequence",
            "analysis.evidence.compare",
        ],
        "limitations": [],
    }
    return context


@pytest.mark.asyncio
async def test_store_keeps_v1_v2_exact_and_accepts_v3_without_cross_version_upgrade():
    from webapp.backend import coach_store

    legacy = _v1_context()
    current = _v2_context()
    processed = _v3_context()
    thread = await coach_store.get_or_create_primary_thread("context-version-owner")

    await coach_store.append_message(
        int(thread["id"]), "user", "legacy context", context=legacy
    )
    await coach_store.append_message(
        int(thread["id"]), "assistant", "current context", context=current
    )
    await coach_store.append_message(
        int(thread["id"]), "assistant", "processed context", context=processed
    )

    messages = await coach_store.load_messages(int(thread["id"]))

    assert [message["context"] for message in messages] == [legacy, current, processed]
    assert messages[0]["context"]["schema_version"] == "coach_diagnostic_context.v1"
    assert "run_facts" not in messages[0]["context"]
    assert messages[1]["context"]["schema_version"] == "coach_diagnostic_context.v2"
    assert messages[2]["context"]["schema_version"] == "coach_diagnostic_context.v3"
