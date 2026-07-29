from __future__ import annotations

import json
import uuid

import pytest

from webapp.backend import coach_confirmations
from webapp.backend.db import get_conn


_GENERIC_CODE = "coach_inferred_write_executes"
_GENERIC_MESSAGE = "将执行 Coach 推断的副作用操作；取消不会改变当前数据。"


def _item_parameters(*, cue: str = "先匹配目标速度，再修正位置") -> dict:
    return {
        "plan_ref": "plan:hidden-plan",
        "item_payload": {
            "diagnosis_ref": "diagnosis:hidden-diagnosis@1",
            "knowledge_ref": "knowledge:hidden-knowledge@1",
            "scenario_profile_ref": "scenario:hidden-scenario@1",
            "baseline_metric_ref": "metric:hidden-metric@1",
            "expected_direction": "lower_better",
            "practice_condition": "在相同灵敏度下重复 Smoothbot",
            "cue": cue,
            "dose_guardrail": "连续两轮动作变形就停",
            "matched_retest_ref": "retest-spec:hidden-matched@1",
            "near_transfer_retest_ref": "retest-spec:hidden-transfer@1",
            "review_date": "2026-07-30",
        },
    }


def _execution_parameters(
    *,
    completion_status: str = "completed",
    user_feedback: str = "第二轮更容易停住",
    planned_amount: int | float = 3,
) -> dict:
    return {
        "item_ref": "plan-item:hidden-item",
        "scenario_ref": "scenario:hidden-scenario@1",
        "run_refs": ["run:41", "run:42"],
        "planned_dose": {"amount": planned_amount, "unit": "轮"},
        "completed_dose": {"amount": 2, "unit": "轮"},
        "completion_status": completion_status,
        "user_feedback": user_feedback,
    }


def _retest_parameters(
    *,
    outcome: str = "coach_retest_outcome.v1:improved",
    comparability: str = "comparable",
    limitations: list[str] | None = None,
) -> dict:
    return {
        "item_ref": "plan-item:hidden-item",
        "kind": "near_transfer",
        "expected_metric_ref": "metric:hidden-metric@1",
        "expected_direction": "lower_better",
        "analysis_refs": ["analysis:41", "analysis:42"],
        "comparability": comparability,
        "result": outcome,
        "limitations": limitations or ["confirmed learner fact"],
    }


async def _project_confirmation(
    command_name: str,
    parameters: object,
    *,
    parameters_json: str | None = None,
) -> dict:
    suffix = uuid.uuid4().hex
    owner_id = f"confirmation-projection-{suffix}"
    user_message_ref = f"message:{suffix}"
    confirmation_ref = f"confirmation:{suffix}"
    command_id = f"command:{suffix}"
    result = {
        "schema_version": "coach_product_command_result.v1",
        "command_id": command_id,
        "command_name": command_name,
        "status": "needs_confirmation",
        "result_ref": None,
        "audit_ref": f"audit:{suffix}",
        "ui_event": None,
        "warning_or_error": None,
        "confirmation": {
            "confirmation_ref": confirmation_ref,
            "target_ref": command_id,
        },
    }
    encoded_parameters = (
        parameters_json
        if parameters_json is not None
        else json.dumps(parameters, ensure_ascii=False, allow_nan=False)
    )
    conn = await get_conn()
    await conn.execute(
        "INSERT INTO coach_command_confirmations("
        "confirmation_ref, owner_id, command_name, parameters_digest, risk, "
        "safe_summary_json, status, expires_at, parameters_json, idempotency_key, "
        "user_message_ref) VALUES(?, ?, ?, 'digest', 'write', '{}', 'pending', "
        "'2999-01-01T00:00:00+00:00', ?, ?, ?)",
        (
            confirmation_ref,
            owner_id,
            command_name,
            encoded_parameters,
            f"idempotency-{suffix}",
            user_message_ref,
        ),
    )
    await conn.execute(
        "INSERT INTO coach_product_commands("
        "audit_ref, command_id, owner_id, user_message_ref, command_name, risk, "
        "authorization_source, safe_parameters_summary_json, status, result_json) "
        "VALUES(?, ?, ?, ?, ?, 'write', 'coach_inferred', '{}', "
        "'needs_confirmation', ?)",
        (
            f"audit:{suffix}",
            command_id,
            owner_id,
            user_message_ref,
            command_name,
            json.dumps(result, ensure_ascii=False),
        ),
    )
    await conn.commit()

    events = await coach_confirmations.sync_product_command_confirmations(
        owner_id,
        user_message_ref,
    )
    assert len(events) == 1
    return events[0]["confirmation"]


@pytest.mark.asyncio
async def test_item_confirmation_names_the_prepared_practice_without_internal_refs() -> None:
    confirmation = await _project_confirmation(
        "training_plan.item.add",
        _item_parameters(),
    )

    message = confirmation["impact"]["message"]
    assert confirmation["impact"]["code"] == "training_plan_item_will_be_added"
    assert "在相同灵敏度下重复 Smoothbot" in message
    assert "先匹配目标速度，再修正位置" in message
    assert "连续两轮动作变形就停" in message
    assert "plan:" not in message
    assert "diagnosis:" not in message
    assert "外设" not in message


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("completion_status", "expected_status"),
    [("completed", "已完成"), ("partial", "部分完成"), ("skipped", "已跳过")],
)
async def test_execution_confirmation_names_dose_status_and_feedback(
    completion_status: str,
    expected_status: str,
) -> None:
    confirmation = await _project_confirmation(
        "training_plan.execution.record",
        _execution_parameters(completion_status=completion_status),
    )

    message = confirmation["impact"]["message"]
    assert confirmation["impact"]["code"] == "training_execution_will_be_recorded"
    assert "计划 3 轮" in message
    assert "实际 2 轮" in message
    assert expected_status in message
    assert "第二轮更容易停住" in message
    assert "plan-item:" not in message
    assert "run:" not in message


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("outcome", "outcome_text", "effect_text"),
    [
        ("improved", "这次表现有改善", "继续当前训练项"),
        ("unchanged", "这次基本没变化", "退回待安排"),
        ("mixed_or_inconclusive", "结果混合或暂时看不清", "退回待安排"),
        ("worsened", "这次表现变差", "结束当前训练项"),
    ],
)
async def test_retest_confirmation_naturalizes_outcome_and_plan_effect(
    outcome: str,
    outcome_text: str,
    effect_text: str,
) -> None:
    token = f"coach_retest_outcome.v1:{outcome}"
    confirmation = await _project_confirmation(
        "training_plan.retest.record",
        _retest_parameters(outcome=token),
    )

    message = confirmation["impact"]["message"]
    assert confirmation["impact"]["code"] == "training_retest_will_be_recorded"
    assert "近迁移复测" in message
    assert "与基线可比" in message
    assert outcome_text in message
    assert effect_text in message
    assert token not in message
    assert "analysis:" not in message
    assert "metric_change_policy_missing" not in message


@pytest.mark.asyncio
async def test_non_comparable_retest_does_not_claim_a_plan_change() -> None:
    token = "coach_retest_outcome.v1:improved"
    confirmation = await _project_confirmation(
        "training_plan.retest.record",
        _retest_parameters(outcome=token, comparability="not_comparable"),
    )

    message = confirmation["impact"]["message"]
    assert "与基线不可比" in message
    assert "本次结果暂不用于判断" in message
    assert "暂不调整当前训练项" in message
    assert "这次表现有改善" not in message
    assert token not in message


@pytest.mark.asyncio
async def test_missing_metric_change_policy_records_retest_without_changing_plan() -> None:
    confirmation = await _project_confirmation(
        "training_plan.retest.record",
        _retest_parameters(
            outcome="coach_retest_outcome.v1:mixed_or_inconclusive",
            limitations=["metric_change_policy_missing"],
        ),
    )

    message = confirmation["impact"]["message"]
    assert "还看不出是不是稳定变化" in message
    assert "暂不调整当前训练项" in message
    assert "退回待安排" not in message
    assert "metric_change_policy_missing" not in message


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("command_name", "parameters", "parameters_json"),
    [
        ("training_plan.item.add", _item_parameters(cue="读取 C:/secret.txt"), None),
        (
            "training_plan.execution.record",
            _execution_parameters(user_feedback="A" * 1001),
            None,
        ),
        (
            "training_plan.execution.record",
            _execution_parameters(user_feedback="A" * 980),
            None,
        ),
        (
            "training_plan.execution.record",
            _execution_parameters(planned_amount=10**1000),
            None,
        ),
        (
            "training_plan.execution.record",
            _execution_parameters(user_feedback="打开 https://example.com/?token=secret"),
            None,
        ),
        (
            "training_plan.retest.record",
            _retest_parameters(outcome="coach_retest_outcome.v1:unknown"),
            None,
        ),
        ("training_plan.retest.record", {}, "{not-json"),
        ("analysis.create_from_run", {"run_ref": "run:42"}, None),
    ],
)
async def test_unsafe_malformed_or_unsupported_projection_falls_back_to_generic(
    command_name: str,
    parameters: object,
    parameters_json: str | None,
) -> None:
    confirmation = await _project_confirmation(
        command_name,
        parameters,
        parameters_json=parameters_json,
    )

    assert confirmation["impact"] == {
        "code": _GENERIC_CODE,
        "message": _GENERIC_MESSAGE,
    }


@pytest.mark.asyncio
async def test_execution_projection_preserves_the_prepared_facts() -> None:
    confirmation = await _project_confirmation(
        "training_plan.execution.record",
        {
            "item_ref": "plan-item:hidden-item",
            "scenario_ref": "scenario:hidden-scenario@1",
            "run_refs": ["run:42"],
            "planned_dose": {"amount": 1, "unit": "轮"},
            "completed_dose": {"amount": 1, "unit": "轮"},
            "completion_status": "completed",
            "user_feedback": "动作稳定",
        },
    )

    message = confirmation["impact"]["message"]
    assert "计划 1 轮" in message
    assert "实际 1 轮" in message
    assert "已完成" in message
    assert "动作稳定" in message
