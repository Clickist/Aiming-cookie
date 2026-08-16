import assert from "node:assert/strict";
import test from "node:test";

import type { TeachingTurnContract } from "../src/contracts.ts";
import {
  fallbackForTeachingTurn,
  isTeachingPhase,
  isTeachingPhaseTransitionAllowed,
  parseTeachingProviderDraft,
  parseTeachingTurnContract,
  planTeachingTurn,
  teachingTurnRequiresLocalFallback,
  teachingEnvelopeInstruction,
  teachingTurnHoldsState,
} from "../src/teaching-policy.ts";

function contract(overrides: Partial<TeachingTurnContract> = {}): TeachingTurnContract {
  return {
    schema_version: "coach_teaching_turn.v1",
    session_ref: "teaching_session:0123456789abcdef0123456789abcdef",
    session_version: 3,
    phase: "await_teach_back",
    problem_id: null,
    problem_label: null,
    evidence_strength: "limited",
    supporting_evidence: [],
    counterevidence_status: "not_observed",
    counterevidence: [],
    observation: "目标减速时，当前移动常常继续前冲。",
    primary_candidate: "当前更值得先验证的是速度匹配。",
    alternatives: ["也可能与目标读取时机有关。"],
    cue: "看到目标减速时，让自己的移动也开始减速。",
    changed_variable: "移动减速时机",
    active_item_ref: null,
    prepared_plan_ref: null,
    prepared_item: null,
    next_recommendation: null,
    question_kind: "teach_back",
    question: "请用自己的话复述这一个注意点？",
    allowed_command: null,
    confirmation_intent: "none",
    retest: {
      intent: "none",
      comparability_required: false,
      comparability: "not_requested",
      revision_decision: null,
    },
    ratio_sources: [{ label: "目标内时间占比", value: 0.34 }],
    approved_dose: null,
    discriminator: null,
    soft_start: false,
    ...overrides,
  };
}

test("teaching contract is strict and ordinary turns remain optional", () => {
  const parsed = parseTeachingTurnContract(contract());
  assert.equal(parsed.phase, "await_teach_back");
  assert.throws(
    () => parseTeachingTurnContract({ ...contract(), alternatives: ["a", "b", "c"] }),
    /alternatives/i,
  );
  assert.throws(
    () => parseTeachingTurnContract({ ...contract(), session_ref: "C:\\private" }),
    /session_ref/i,
  );
  assert.throws(
    () => parseTeachingTurnContract({ ...contract(), unexpected: true }),
    /unsupported/i,
  );
  assert.throws(
    () => parseTeachingTurnContract({
      ...contract(),
      retest: {
        intent: "none",
        comparability_required: false,
        comparability: "not_requested",
        revision_decision: null,
        raw_trace: [],
      },
    }),
    /retest/i,
  );
  assert.throws(
    () => parseTeachingTurnContract({ ...contract(), cue: "analysis:7 的内部字段" }),
    /cue/i,
  );
});

test("a compiled problem keeps evidence, candidates, and one discriminator bounded", () => {
  const compiled = contract({
    phase: "intake",
    question_kind: "discriminator",
    question: "Does the late correction happen after the target slows?",
    problem_id: "tracking.speed_matching",
    problem_label: "Late speed matching",
    evidence_strength: "supported",
    supporting_evidence: [
      "Corrections begin after the target decelerates.",
      "The same pattern appears in a second comparable segment.",
    ],
    counterevidence_status: "not_observed",
    counterevidence: [],
    primary_candidate: "A possible explanation is late speed matching.",
    alternatives: ["It may instead reflect delayed target reading."],
    discriminator: {
      kind: "question",
      prompt: "Does the late correction happen after the target slows?",
    },
  });

  const parsed = parseTeachingTurnContract(compiled);
  assert.equal(parsed.problem_id, "tracking.speed_matching");
  assert.equal(parsed.evidence_strength, "supported");
  assert.equal(parsed.supporting_evidence.length, 2);
  assert.equal(parsed.discriminator?.kind, "question");
  assert.throws(
    () => parseTeachingTurnContract({ ...compiled, supporting_evidence: ["a", "b", "c", "d", "e"] }),
    /supporting_evidence/i,
  );
  assert.throws(
    () => parseTeachingTurnContract({ ...compiled, counterevidence_status: "observed", counterevidence: [] }),
    /evidence/i,
  );
  assert.throws(
    () => parseTeachingTurnContract({ ...compiled, evidence_strength: "limited", supporting_evidence: [] }),
    /evidence/i,
  );
  assert.throws(
    () => parseTeachingTurnContract({ ...compiled, primary_candidate: "This definitely is late speed matching." }),
    /primary_candidate/i,
  );
  assert.throws(
    () => parseTeachingTurnContract({
      ...compiled,
      discriminator: { kind: "question", prompt: "Is it late? Is it always late?" },
    }),
    /discriminator/i,
  );
});

test("typed supporting evidence preserves kind and refs while strings remain compatible", () => {
  const compiled = contract({
    problem_id: "terminal_control",
    problem_label: "到点后的收尾修正偏多",
    supporting_evidence: [{
      kind: "measured",
      text: "两条规则化观察都出现了反向修正",
      refs: ["analysis:42", "context:terminal-control"],
    }],
  });

  assert.deepEqual(parseTeachingTurnContract(compiled).supporting_evidence, compiled.supporting_evidence);
  assert.throws(
    () => parseTeachingTurnContract({
      ...compiled,
      supporting_evidence: [{
        kind: "body_detected",
        text: "两条规则化观察都出现了反向修正",
        refs: ["analysis:42"],
      }],
    }),
    /supporting_evidence/i,
  );
});

test("an intake without a grounded candidate is a local no-lesson result", () => {
  const noLesson = contract({
    phase: "intake",
    observation: null,
    primary_candidate: null,
    alternatives: [],
    cue: null,
    changed_variable: null,
    question_kind: "discriminator",
    question: "内部问题不会显示给用户？",
  });

  const parsed = parseTeachingTurnContract(noLesson);
  const fallback = fallbackForTeachingTurn(parsed);

  assert.equal(teachingTurnRequiresLocalFallback(parsed), true);
  assert.equal(teachingTurnHoldsState(parsed), true);
  assert.equal(fallback.action, "pause");
  assert.match(fallback.text, /已经附加/);
  assert.match(fallback.text, /证据不足以形成训练处方/);
  assert.doesNotMatch(fallback.text, /解除这条分析/);
  assert.doesNotMatch(fallback.text, /[?？]|候选|fallback/i);
});

test("active item refs are required, nullable and bounded to safe plan item refs", () => {
  assert.equal(parseTeachingTurnContract(contract()).active_item_ref, null);
  assert.equal(
    parseTeachingTurnContract(contract({ active_item_ref: "plan-item:guided-loop@v1" })).active_item_ref,
    "plan-item:guided-loop@v1",
  );
  const { active_item_ref: _missing, ...missing } = contract();
  for (const raw of [
    missing,
    contract({ active_item_ref: "" }),
    contract({ active_item_ref: "item:guided-loop" }),
    contract({ active_item_ref: "plan-item:unsafe/path" }),
  ]) {
    assert.throws(() => parseTeachingTurnContract(raw), /active_item_ref/i);
  }
});

test("practice-ready item writes require one complete prepared command", () => {
  const prepared_item = {
    diagnosis_ref: "diagnosis:tracking-error@1",
    knowledge_ref: "knowledge:speed-matching@1",
    scenario_profile_ref: "scenario:tracking.smoothbot@1",
    baseline_metric_ref: "metric:tracking-error@v1",
    expected_direction: "lower_better" as const,
    practice_condition: "Repeat the reviewed tracking scenario.",
    cue: "看到目标减速时，让自己的移动也开始减速。",
    dose_guardrail: "先保持原场景，只改变这个注意点。",
    matched_retest_ref: "retest-spec:tracking-matched@1",
    near_transfer_retest_ref: "retest-spec:tracking-transfer@1",
    review_date: "after the next comparable practice run",
  };
  const unprepared = contract({
    phase: "practice_ready",
    question_kind: "none",
    question: null,
    allowed_command: null,
  });
  assert.equal(parseTeachingTurnContract(unprepared).allowed_command, null);
  assert.equal(teachingTurnHoldsState(unprepared), true);

  const prepared = contract({
    phase: "practice_ready",
    question_kind: "none",
    question: null,
    allowed_command: "training_plan.item.add",
    prepared_plan_ref: "plan:guided-loop",
    prepared_item,
  });
  assert.equal(parseTeachingTurnContract(prepared).allowed_command, "training_plan.item.add");
  assert.equal(teachingTurnHoldsState(prepared), false);
  assert.throws(
    () => parseTeachingTurnContract({ ...prepared, prepared_item: null }),
    /prepared command/i,
  );
  assert.throws(
    () => parseTeachingTurnContract({ ...prepared, prepared_item: { ...prepared_item, extra: true } }),
    /prepared_item/i,
  );
  assert.throws(
    () => parseTeachingTurnContract({
      ...prepared,
      phase: "teach",
      allowed_command: null,
    }),
    /prepared command/i,
  );
});

test("retest comparability and revision decisions are strict", () => {
  for (const comparability of ["unresolved", "comparable", "not_comparable", "not_requested"] as const) {
    const revision_decision = comparability === "comparable" ? "retain" as const : null;
    const parsed = parseTeachingTurnContract(contract({
      retest: {
        intent: "immediate_matched",
        comparability_required: true,
        comparability,
        revision_decision,
      },
    }));
    assert.equal(parsed.retest.comparability, comparability);
    assert.equal(parsed.retest.revision_decision, revision_decision);
  }
  for (const revision_decision of [null, "retain", "lower", "reject"] as const) {
    const parsed = parseTeachingTurnContract(contract({
      retest: {
        intent: "immediate_matched",
        comparability_required: true,
        comparability: "comparable",
        revision_decision,
      },
    }));
    assert.equal(parsed.retest.revision_decision, revision_decision);
  }
  for (const retest of [
    {
      intent: "none",
      comparability_required: true,
      comparability: "not_requested",
      revision_decision: null,
    },
    {
      intent: "immediate_matched",
      comparability_required: true,
      comparability: "not_comparable",
      revision_decision: "retain",
    },
    {
      intent: "immediate_matched",
      comparability_required: true,
      comparability: "unknown",
      revision_decision: null,
    },
    {
      intent: "immediate_matched",
      comparability_required: true,
      comparability: "comparable",
      revision_decision: "promote",
    },
  ]) {
    assert.throws(() => parseTeachingTurnContract({ ...contract(), retest }), /retest/i);
  }
});

test("provider envelope contains only an action and user-facing text", () => {
  assert.deepEqual(
    parseTeachingProviderDraft('{"action":"ask_teach_back","text":"请复述这条提示？"}'),
    { action: "ask_teach_back", text: "请复述这条提示？" },
  );
  for (const raw of [
    "请复述这条提示？",
    '{"action":"ask_teach_back","text":"请复述这条提示？","phase":"practice_ready"}',
  ]) {
    assert.equal(parseTeachingProviderDraft(raw), null, raw);
  }
});

test("provider instruction includes the approved dose in natural language", () => {
  const instruction = teachingEnvelopeInstruction(contract({
    phase: "practice_ready",
    question_kind: "none",
    question: null,
    allowed_command: "training_plan.item.add",
    approved_dose: "练 2 分钟。",
  }));

  assert.match(instruction, /Approved dose: 练 2 分钟。/);
});

test("retest envelope exposes only the versioned TeachingSession result vocabulary", () => {
  const instruction = teachingEnvelopeInstruction(contract({
    phase: "await_retest_confirmation",
    question_kind: "none",
    question: null,
    allowed_command: "training_plan.retest.record",
    confirmation_intent: "retest",
    active_item_ref: "plan-item:guided-loop",
    retest: {
      intent: "immediate_matched",
      comparability_required: true,
      comparability: "unresolved",
      revision_decision: null,
    },
  }));
  for (const outcome of ["improved", "unchanged", "worsened", "mixed_or_inconclusive"]) {
    assert.match(instruction, new RegExp(`coach_retest_outcome\\.v1:${outcome}`));
  }
});

test("planner selects one action and cannot advance an incorrect teach-back", () => {
  assert.deepEqual(planTeachingTurn(contract()), {
    action: "ask_teach_back",
    question: "请用自己的话复述这一个注意点？",
  });
  assert.deepEqual(
    planTeachingTurn(contract({ phase: "teach_back_repair", question_kind: "teach_back_repair" })),
    { action: "repair_teach_back", question: "请用自己的话复述这一个注意点？" },
  );
  assert.notEqual(planTeachingTurn(contract({ phase: "teach_back_repair", question_kind: "teach_back_repair" })).action, "practice");
});

test("revision scope follows immediate, delayed and near-transfer intent", () => {
  const cases = [
    ["immediate_matched", /当下有帮助/, /还不能说已经稳定掌握/],
    ["delayed_matched", /隔一段时间/, /保留下来/],
    ["near_transfer", /相近任务/, /不代表主游戏/],
  ] as const;
  for (const [intent, expected, boundary] of cases) {
    const revision = fallbackForTeachingTurn(contract({
      phase: "revise",
      question_kind: "none",
      question: null,
      active_item_ref: "plan-item:guided-loop",
      retest: {
        intent,
        comparability_required: true,
        comparability: "comparable",
        revision_decision: "retain",
      },
    }));
    assert.match(revision.text, expected);
    assert.match(revision.text, boundary);
  }
});

test("a revise turn without a deterministic decision holds its lesson state", () => {
  for (const comparability of ["unresolved", "comparable", "not_comparable"] as const) {
    assert.equal(teachingTurnHoldsState(contract({
      phase: "revise",
      question_kind: "none",
      question: null,
      active_item_ref: "plan-item:guided-loop",
      retest: {
        intent: "immediate_matched",
        comparability_required: true,
        comparability,
        revision_decision: null,
      },
    })), true, comparability);
  }
});

test("teaching session phase transitions follow the guided loop", () => {
  assert.ok(isTeachingPhase("intake"));
  assert.ok(!isTeachingPhase("resting"));
  assert.ok(!isTeachingPhase(null));

  const loop = [
    "intake", "hypothesize", "teach", "await_teach_back", "practice_ready",
    "await_execution_confirmation", "retest_ready", "await_retest_confirmation", "revise",
  ] as const;
  for (let i = 0; i < loop.length - 1; i++) {
    assert.ok(
      isTeachingPhaseTransitionAllowed(loop[i], loop[i + 1]),
      `${loop[i]} -> ${loop[i + 1]}`,
    );
  }

  // No skipping ahead, no invented states.
  assert.ok(!isTeachingPhaseTransitionAllowed("intake", "practice_ready"));
  assert.ok(!isTeachingPhaseTransitionAllowed("intake", "revise"));
  assert.ok(!isTeachingPhaseTransitionAllowed("teach", "await_retest_confirmation"));
  assert.ok(!isTeachingPhaseTransitionAllowed("revise", "await_teach_back"));

  // Lesson-only updates keep the current phase.
  assert.ok(isTeachingPhaseTransitionAllowed("teach", "teach"));

  // One step back to re-teach, follow-up questions and repair stay local.
  assert.ok(isTeachingPhaseTransitionAllowed("await_teach_back", "teach_back_repair"));
  assert.ok(isTeachingPhaseTransitionAllowed("teach_back_repair", "teach"));
  assert.ok(isTeachingPhaseTransitionAllowed("practice_ready", "teach"));
  assert.ok(isTeachingPhaseTransitionAllowed("revise", "follow_up"));

  // Any active phase may pause; a paused session may resume to any active phase.
  for (const phase of loop) {
    assert.ok(isTeachingPhaseTransitionAllowed(phase, "paused"), `${phase} -> paused`);
    assert.ok(isTeachingPhaseTransitionAllowed("paused", phase), `paused -> ${phase}`);
  }
  assert.ok(isTeachingPhaseTransitionAllowed("paused", "paused"));
  assert.ok(!isTeachingPhaseTransitionAllowed("paused", "stopped_for_discomfort"));

  // Discomfort only restarts the loop.
  assert.ok(isTeachingPhaseTransitionAllowed("practice_ready", "stopped_for_discomfort"));
  assert.ok(isTeachingPhaseTransitionAllowed("stopped_for_discomfort", "intake"));
  assert.ok(!isTeachingPhaseTransitionAllowed("stopped_for_discomfort", "practice_ready"));
});
