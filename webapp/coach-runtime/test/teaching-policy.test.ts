import assert from "node:assert/strict";
import test from "node:test";

import type { TeachingTurnContract } from "../src/contracts.ts";
import {
  fallbackForTeachingTurn,
  parseTeachingProviderDraft,
  parseTeachingTurnContract,
  planTeachingTurn,
  teachingTurnRequiresLocalFallback,
  teachingEnvelopeInstruction,
  teachingTurnHoldsState,
  validateTeachingDirectResponse,
  validateTeachingDraft,
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

test("legacy v1 teaching turns normalize diagnosis fields and a soft start holds state", () => {
  const { problem_id, problem_label, evidence_strength, supporting_evidence, counterevidence_status,
    counterevidence, discriminator, soft_start, ...legacy } = contract();
  const parsedLegacy = parseTeachingTurnContract(legacy);
  assert.deepEqual(parseTeachingTurnContract(parsedLegacy), parsedLegacy);
  assert.equal(parsedLegacy.problem_id, null);
  assert.equal(parsedLegacy.evidence_strength, "limited");
  assert.deepEqual(parsedLegacy.supporting_evidence, []);

  const softStart = parseTeachingTurnContract(contract({
    phase: "intake",
    question_kind: "discriminator",
    question: "Does the late correction happen after the target slows?",
    problem_id: "tracking.speed_matching",
    problem_label: "Late speed matching",
    evidence_strength: "limited",
    supporting_evidence: ["Corrections begin after the target decelerates."],
    primary_candidate: "A possible explanation is late speed matching.",
    alternatives: ["It may instead reflect delayed target reading."],
    discriminator: {
      kind: "question",
      prompt: "Does the late correction happen after the target slows?",
    },
    soft_start: true,
  }));
  const fallback = fallbackForTeachingTurn(softStart);
  assert.equal(teachingTurnHoldsState(softStart), true);
  assert.equal(fallback.action, "ask_discriminator");
  assert.match(fallback.text, /possible explanation/i);
  assert.equal((fallback.text.match(/[?？]/g) ?? []).length, 1);
  assert.deepEqual(validateTeachingDraft(softStart, fallback), { ok: true });
  assert.notDeepEqual(validateTeachingDraft(softStart, {
    action: "ask_discriminator",
    text: `${fallback.text} Training starts now.`,
  }), { ok: true });
});

test("diagnostic fallback keeps the first explanation brief without emptying the evidence contract", () => {
  const intake = contract({
    phase: "intake",
    question_kind: "discriminator",
    question: "你更常先冲过目标，还是到点后才开始回拉？",
    problem_id: "terminal_control",
    problem_label: "到点后的收尾修正偏多",
    evidence_strength: "supported",
    supporting_evidence: [
      "到点后出现了反向修正。",
      "同一份分析里还出现了两段式收尾。",
      "另一段可比记录也出现了相同模式。",
    ],
    primary_candidate: "可能是减速与停枪时机没有配合好，需要继续验证。",
    alternatives: [
      "也可能是目标大小或距离变化造成的类似表现。",
      "也可能是你为了准确率主动放慢了节奏。",
    ],
    discriminator: {
      kind: "question",
      prompt: "你更常先冲过目标，还是到点后才开始回拉？",
    },
    soft_start: true,
  });

  const fallback = fallbackForTeachingTurn(intake);
  assert.deepEqual(validateTeachingDraft(intake, fallback), { ok: true });
  assert.match(fallback.text, /我先看到点后的收尾修正偏多/);
  assert.match(fallback.text, /到点后出现了反向修正/);
  assert.match(fallback.text, /可能是减速与停枪时机/);
  assert.match(fallback.text, /目标大小或距离变化/);
  assert.doesNotMatch(fallback.text, /两段式收尾|另一段可比记录|主动放慢了节奏/);
  assert.doesNotMatch(fallback.text, /当前先排查|当前支持它的迹象|初步判断/);
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

test("a direct teaching answer can explain a focus mismatch without advancing the lesson", () => {
  const result = validateTeachingDirectResponse(
    contract({
      phase: "intake",
      question_kind: "discriminator",
      question: "你更接近速度匹配时机还是目标读取时机？",
    }),
    "页面的第一项仍是减速阶段偏长。当前先谈反向修正，是因为它有已经核对过的练习提示；这不表示它比第一项更严重，也不能据此确认具体原因。",
  );

  assert.deepEqual(result, { ok: true });
  assert.notDeepEqual(
    validateTeachingDirectResponse(contract(), "做 3 组后再说。"),
    { ok: true },
  );
  assert.notDeepEqual(
    validateTeachingDirectResponse(contract(), "你还想问什么？"),
    { ok: true },
  );
  assert.notDeepEqual(
    validateTeachingDirectResponse(contract(), "今天先保持这个动作提示。练完告诉我完成情况和主观感受。"),
    { ok: true },
  );
  assert.deepEqual(
    validateTeachingDirectResponse(contract(), "今天先保持这个动作提示，不需要额外改变训练方向。"),
    { ok: true },
  );
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

test("paired Medium recommendation is bounded and cannot prepare a plan item", () => {
  const recommendation = {
    scenario_name: "Medium paired",
    scenario_profile_ref: null,
    message: "下一项可以尝试 Medium paired（Medium），把它当作更难的压力测试和新的基线；它本身不证明迁移。",
  };
  const revision = contract({
    phase: "revise",
    question_kind: "none",
    question: null,
    next_recommendation: recommendation,
    retest: {
      intent: "immediate_matched",
      comparability_required: true,
      comparability: "comparable",
      revision_decision: "retain",
    },
  });

  const parsed = parseTeachingTurnContract(revision);
  assert.deepEqual(parsed.next_recommendation, recommendation);
  assert.equal(parsed.allowed_command, null);
  assert.equal(parsed.prepared_item, null);
  const fallback = fallbackForTeachingTurn(parsed);
  assert.match(fallback.text, /Medium paired/);
  assert.match(fallback.text, /压力测试.*新的基线/);
  assert.match(fallback.text, /不证明迁移/);
  assert.deepEqual(validateTeachingDraft(parsed, fallback), { ok: true });
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

test("provider instruction treats one short analogy as explanation rather than evidence", () => {
  const instruction = teachingEnvelopeInstruction(contract({
    phase: "hypothesize",
    question_kind: "none",
    question: null,
  }));
  assert.match(instruction, /one short analogy/i);
  assert.match(instruction, /not evidence/i);

  const hypothesis = contract({
    phase: "hypothesize",
    question_kind: "none",
    question: null,
  });
  const withAnalogy = {
    action: "explain_candidate" as const,
    text: "目标减速时，当前移动仍会前冲。可以把它想成进弯时收油晚了，最后还要修方向。可能是速度匹配偏晚；也可能和目标读取时机有关。",
  };
  assert.deepEqual(validateTeachingDraft(hypothesis, withAnalogy), { ok: true });
  assert.equal(validateTeachingDraft(hypothesis, {
    action: "explain_candidate",
    text: "可以把它想成进弯时收油晚了，最后还要修方向。",
  }).ok, false);
});

test("provider instruction preserves the approved dose and rejects only unapproved doses", () => {
  const instruction = teachingEnvelopeInstruction(contract({
    phase: "practice_ready",
    question_kind: "none",
    question: null,
    allowed_command: "training_plan.item.add",
    approved_dose: "练 2 分钟。",
  }));

  assert.match(instruction, /approved_dose/);
  assert.match(instruction, /unapproved dose/i);
});

test("provider envelope exposes the active item only for exact tool parameters", () => {
  const instruction = teachingEnvelopeInstruction(contract({ active_item_ref: "plan-item:guided-loop" }));
  assert.match(instruction, /active_item_ref/);
  assert.match(instruction, /plan-item:guided-loop/);
  assert.match(instruction, /tool parameter/i);
  assert.equal(
    validateTeachingDraft(contract({ active_item_ref: "plan-item:guided-loop" }), {
      action: "ask_teach_back",
      text: "当前训练项是 plan-item:guided-loop，请用自己的话复述这一个注意点？",
    }).ok,
    false,
  );
  assert.equal(
    validateTeachingDraft(contract({ active_item_ref: "plan-item:guided-loop@" }), {
      action: "ask_teach_back",
      text: "当前训练项是 plan-item:guided-loop@，请用自己的话复述这一个注意点？",
    }).ok,
    false,
  );
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
  assert.match(instruction, /not_comparable.*mixed_or_inconclusive/);
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

test("a Provider draft cannot replace the planner-owned question", () => {
  assert.equal(
    validateTeachingDraft(contract(), {
      action: "ask_teach_back",
      text: "你觉得这条提示有用吗？",
    }).ok,
    false,
  );
});

test("a Provider may paraphrase approved Chinese teaching content without changing its stage", () => {
  const hypothesis = contract({
    phase: "hypothesize",
    question_kind: "none",
    question: null,
  });
  assert.deepEqual(validateTeachingDraft(hypothesis, {
    action: "explain_candidate",
    text: "目标减速时，当前移动仍会前冲。先检查速度匹配；也可能是目标读取的时机偏晚。",
  }), { ok: true });

  assert.deepEqual(validateTeachingDraft(contract(), {
    action: "ask_teach_back",
    text: "当目标减速时，鼠标移动也开始减速。你能讲讲这条提醒该怎么做吗？",
  }), { ok: true });
});

test("a shared short term cannot replace a required field or reverse its cue", () => {
  const hypothesis = contract({
    phase: "hypothesize",
    question_kind: "none",
    question: null,
  });
  assert.equal(validateTeachingDraft(hypothesis, {
    action: "explain_candidate",
    text: "目标减速时，当前移动仍会前冲。速度很重要。",
  }).ok, false);
  assert.equal(validateTeachingDraft(contract(), {
    action: "ask_teach_back",
    text: "目标减速时，移动继续加速。请用自己的话复述这一个注意点？",
  }).ok, false);
  assert.equal(validateTeachingDraft(hypothesis, {
    action: "explain_candidate",
    text: "目标减速时，当前移动仍会前冲。速度匹配其实不对；目标读取时机也不是问题。",
  }).ok, false);
});

test("a paraphrased discriminator keeps every candidate named by the planner question", () => {
  const intake = contract({
    phase: "intake",
    question_kind: "discriminator",
    question: "这次前冲时，你更明显感觉到速度匹配还是目标读取时机？",
  });
  assert.deepEqual(validateTeachingDraft(intake, {
    action: "ask_discriminator",
    text: "目标减速时，当前移动仍会前冲。你更明显感觉到速度匹配还是目标读取时机？",
  }), { ok: true });
  assert.equal(validateTeachingDraft(intake, {
    action: "ask_discriminator",
    text: "目标减速时，当前移动仍会前冲。你觉得哪个更明显？",
  }).ok, false);
});

test("a paraphrase still preserves an approved numeric dose exactly", () => {
  const teaching = contract({
    phase: "practice_ready",
    question_kind: "none",
    question: null,
    allowed_command: "training_plan.item.add",
    approved_dose: "练 2 分钟。",
  });
  assert.equal(validateTeachingDraft(teaching, {
    action: "practice",
    text: "目标一减速就让手也开始减速。练 3 分钟。",
  }).ok, false);
});

test("practice keeps the Registry-backed dose guardrail with the approved cue", () => {
  const teaching = contract({
    phase: "practice_ready",
    question_kind: "none",
    question: null,
    allowed_command: "training_plan.item.add",
    cue: "看到目标减速时，让自己的移动也开始减速。",
    approved_dose: "先保持原场景，只改变这个注意点。",
  });
  const exact = {
    action: "practice" as const,
    text: `${teaching.cue}${teaching.approved_dose}`,
  };

  assert.deepEqual(validateTeachingDraft(teaching, exact), { ok: true });
  assert.equal(validateTeachingDraft(teaching, {
    action: "practice",
    text: teaching.cue!,
  }).ok, false);
  assert.match(
    fallbackForTeachingTurn(teaching).text,
    /先保持原场景，只改变这个注意点/,
  );
});

test("source-preserving percentage display is allowed without semantic expansion", () => {
  const teaching = contract({ phase: "teach", question_kind: "none", question: null });
  const required = `${teaching.primary_candidate}${teaching.cue}`;
  assert.deepEqual(
    validateTeachingDraft(teaching, { action: "teach", text: `${required}目标内时间占比是 34%。` }),
    { ok: true },
  );
  assert.equal(
    validateTeachingDraft(teaching, { action: "teach", text: `${required}目标内时间占比约三分之一。` }).ok,
    false,
  );
  for (const text of [
    "目标内时间占比经常发生，所以这是个问题。",
    "目标内时间占比偏差，说明你因为手部紧张导致失误。",
  ]) {
    assert.equal(validateTeachingDraft(teaching, { action: "teach", text }).ok, false, text);
  }
});

test("policy rejects a second or compound question, dose, internal vocabulary and completion claim", () => {
  for (const text of [
    "你能复述这条提示吗？还是想先练习？",
    "请用自己的话复述这条提示，以及说说感觉？",
    "请练习 3 组后复述这条提示？",
    "当前 TeachingSession 已确认你完成了，请复述这条提示？",
    "active_item_ref 是 plan-item:guided-loop，请复述这条提示？",
    "你已经完成并改善了，请复述这条提示？",
  ]) {
    assert.equal(
      validateTeachingDraft(contract(), { action: "ask_teach_back", text }).ok,
      false,
      text,
    );
  }
});

test("policy rejects unsupported causal claims but keeps denial and candidate language", () => {
  const teaching = contract({ phase: "teach", question_kind: "none", question: null });
  for (const text of [
    "这是因为手部紧张导致的。",
    "这说明 reading 能力差。",
  ]) {
    assert.equal(validateTeachingDraft(teaching, { action: "teach", text }).ok, false, text);
  }
  for (const text of [
    `${teaching.primary_candidate}${teaching.cue}这不能说明手部紧张。`,
    `${teaching.primary_candidate}${teaching.cue}先把手部紧张当作候选。`,
  ]) {
    assert.deepEqual(validateTeachingDraft(teaching, { action: "teach", text }), { ok: true }, text);
  }
});

test("valid teaching drafts must carry the planner-approved lesson content", () => {
  const hypothesis = contract({
    phase: "hypothesize",
    question_kind: "none",
    question: null,
  });
  assert.equal(
    validateTeachingDraft(hypothesis, {
      action: "explain_candidate",
      text: "先试试这个方向。",
    }).ok,
    false,
  );
  const fallback = fallbackForTeachingTurn(hypothesis);
  assert.equal(validateTeachingDraft(hypothesis, fallback).ok, true);
  assert.match(fallback.text, /也可能/);
  assert.doesNotMatch(fallback.text, /待验证|候选|备选|TeachingSession|fallback/i);
});

test("user-facing teaching text rejects protocol phrasing without forcing one script", () => {
  const teaching = contract({ phase: "teach", question_kind: "none", question: null });
  for (const text of [
    `${teaching.primary_candidate}${teaching.cue}这是待验证候选。`,
    `${teaching.primary_candidate}${teaching.cue}这是一个待验证的候选解释。`,
    `${teaching.primary_candidate}${teaching.cue}当前保持 unresolved。`,
    `${teaching.primary_candidate}${teaching.cue}这是本地 fallback 回复。`,
  ]) {
    assert.equal(validateTeachingDraft(teaching, { action: "teach", text }).ok, false, text);
  }

  const stopped = contract({
    phase: "stopped_for_discomfort",
    question_kind: "none",
    question: null,
  });
  assert.equal(validateTeachingDraft(stopped, {
    action: "stop_for_discomfort",
    text: "出现疼痛、麻木或无力时必须停止当前训练。",
  }).ok, false);
  for (const text of [
    "先停一下，休息会儿，别勉强。",
    "既然手已经发麻了，这组就先停在这里，缓一缓再说。",
  ]) {
    assert.deepEqual(validateTeachingDraft(stopped, {
      action: "stop_for_discomfort",
      text,
    }), { ok: true }, text);
  }

  const paused = contract({
    phase: "paused",
    question_kind: "none",
    question: null,
  });
  assert.deepEqual(validateTeachingDraft(paused, {
    action: "pause",
    text: "训练已暂停，请稍后恢复。",
  }), { ok: true });
  for (const text of [
    "行，那今天先到这里。训练计划先保持原样，等你想继续时再接上。",
    "可以，这次先不练。之后想继续了，我们就从这里往下走。",
  ]) {
    assert.deepEqual(validateTeachingDraft(paused, {
      action: "pause",
      text,
    }), { ok: true }, text);
  }

  const notComparable = contract({
    phase: "revise",
    question_kind: "none",
    question: null,
    active_item_ref: "plan-item:guided-loop",
    retest: {
      intent: "immediate_matched",
      comparability_required: true,
      comparability: "not_comparable",
      revision_decision: null,
    },
  });
  assert.equal(validateTeachingDraft(notComparable, {
    action: "revise",
    text: "这次不能比较，暂时不下结论。",
  }).ok, false);
  for (const text of [
    "这两次条件没对齐，分数放一起看会误导。先别改训练方向，按原来的条件重新测。",
    "场景和设置对不上，这个结果先别和之前比较。训练方向保持不变，重新按原条件验证。",
  ]) {
    assert.deepEqual(validateTeachingDraft(notComparable, {
      action: "revise",
      text,
    }), { ok: true }, text);
  }
});

test("stage fallback is natural Chinese, stage-specific and has one question only when required", () => {
  const teachBack = fallbackForTeachingTurn(contract());
  assert.equal(teachBack.action, "ask_teach_back");
  assert.match(teachBack.text, /自己的话复述/);
  assert.equal((teachBack.text.match(/[？?]/g) ?? []).length, 1);
  assert.doesNotMatch(teachBack.text, /TeachingSession|session_ref|question_kind/i);

  const stopped = fallbackForTeachingTurn(contract({
    phase: "stopped_for_discomfort",
    question_kind: "none",
    question: null,
  }));
  assert.equal(stopped.action, "stop_for_discomfort");
  assert.equal(stopped.text, "那先别练这组了，休息一下，别硬撑。");
  assert.equal((stopped.text.match(/[？?]/g) ?? []).length, 0);

  const paused = fallbackForTeachingTurn(contract({
    phase: "paused",
    question_kind: "none",
    question: null,
  }));
  assert.equal(paused.action, "pause");
  assert.equal(paused.text, "那这组先不练，训练计划不改。你准备继续时再告诉我。");
  assert.equal((paused.text.match(/[？?]/g) ?? []).length, 0);
  assert.doesNotMatch(paused.text, /TeachingSession|active_item_ref|已经完成/i);

  const notComparable = fallbackForTeachingTurn(contract({
    phase: "revise",
    question_kind: "none",
    question: null,
    active_item_ref: "plan-item:guided-loop",
    retest: {
      intent: "immediate_matched",
      comparability_required: true,
      comparability: "not_comparable",
      revision_decision: null,
    },
  }));
  assert.equal(notComparable.action, "revise");
  assert.match(notComparable.text, /放一起看会误导/);
  assert.match(notComparable.text, /训练方向先不改/);
  assert.doesNotMatch(notComparable.text, /不可比|未解决|候选|unresolved/i);
  assert.doesNotMatch(notComparable.text, /retain|lower|reject|保留|降低|拒绝/i);
  assert.doesNotMatch(notComparable.text, /(?:\d+|[一二三四五六七八九十]+)\s*(?:组|次|轮|局)/);
  assert.equal(
    validateTeachingDraft(contract({
      phase: "revise",
      question_kind: "none",
      question: null,
      retest: {
        intent: "immediate_matched",
        comparability_required: true,
        comparability: "not_comparable",
        revision_decision: null,
      },
    }), {
      action: "revise",
      text: "这次条件没对齐，但还是继续沿着这个方向练。",
    }).ok,
    false,
  );

  for (const [intent, expected] of [
    ["immediate_matched", /按同一个场景和设置复测/],
    ["delayed_matched", /下次按同一个场景和设置复测/],
    ["near_transfer", /换一个相近任务/],
  ] as const) {
    const retest = fallbackForTeachingTurn(contract({
      phase: "retest_ready",
      question_kind: "none",
      question: null,
      active_item_ref: "plan-item:guided-loop",
      retest: {
        intent,
        comparability_required: true,
        comparability: "unresolved",
        revision_decision: null,
      },
    }));
    assert.match(retest.text, expected);
    assert.doesNotMatch(retest.text, /(?:再跑|缺)\s*(?:[一二三四五六七八九十\d]+)?\s*(?:组|次|轮|局)/);
  }

  for (const [revision_decision, expected] of [
    ["retain", /继续用/],
    ["lower", /往后放/],
    ["reject", /不沿着它练/],
  ] as const) {
    const revision = fallbackForTeachingTurn(contract({
      phase: "revise",
      question_kind: "none",
      question: null,
      active_item_ref: "plan-item:guided-loop",
      retest: {
        intent: "immediate_matched",
        comparability_required: true,
        comparability: "comparable",
        revision_decision,
      },
    }));
    assert.match(revision.text, expected);
    assert.equal(validateTeachingDraft(contract({
      phase: "revise",
      question_kind: "none",
      question: null,
      active_item_ref: "plan-item:guided-loop",
      retest: {
        intent: "immediate_matched",
        comparability_required: true,
        comparability: "comparable",
        revision_decision,
      },
    }), revision).ok, true);
  }
});

test("revision validation rejects negated or conflicting decision language", () => {
  const cases = [
    {
      decision: "retain" as const,
      valid: "这次同条件结果支持这个方向，先继续用。",
      invalid: "这次结果不支持这个方向，但先继续用。",
    },
    {
      decision: "lower" as const,
      valid: "这次结果没有明显支持，先把这个方向往后放。",
      invalid: "这次结果明确支持，先不降低这个方向的优先级。",
    },
    {
      decision: "reject" as const,
      valid: "这次结果不支持这个方向，先不沿着它练。",
      invalid: "这次不是不支持，先不拒绝这个方向。",
    },
  ];
  for (const item of cases) {
    const revision = contract({
      phase: "revise",
      question_kind: "none",
      question: null,
      active_item_ref: "plan-item:guided-loop",
      retest: {
        intent: "immediate_matched",
        comparability_required: true,
        comparability: "comparable",
        revision_decision: item.decision,
      },
    });
    assert.deepEqual(validateTeachingDraft(revision, {
      action: "revise",
      text: item.valid,
    }), { ok: true }, item.decision);
    assert.equal(validateTeachingDraft(revision, {
      action: "revise",
      text: item.invalid,
    }).ok, false, item.decision);
  }
  const insertedNegations = [
    ["retain", "这次结果没有充分支持这个方向，但先继续用。"],
    ["lower", "这次结果明确支持，所以不该降低这个方向的优先级。"],
    ["reject", "这次结果支持，所以不应该拒绝这个方向。"],
  ] as const;
  for (const [decision, text] of insertedNegations) {
    assert.equal(validateTeachingDraft(contract({
      phase: "revise",
      question_kind: "none",
      question: null,
      active_item_ref: "plan-item:guided-loop",
      retest: {
        intent: "immediate_matched",
        comparability_required: true,
        comparability: "comparable",
        revision_decision: decision,
      },
    }), { action: "revise", text }).ok, false, text);
  }
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

test("Provider revision wording must preserve the retest intent boundary", () => {
  const cases = [
    {
      intent: "immediate_matched" as const,
      invalid: "这次结果支持这个方向，说明已经稳定掌握，先继续用。",
    },
    {
      intent: "delayed_matched" as const,
      invalid: "这次结果支持这个方向，先继续用。",
    },
    {
      intent: "near_transfer" as const,
      invalid: "这次同条件结果支持这个方向，先继续用。",
    },
  ];
  for (const item of cases) {
    const revision = contract({
      phase: "revise",
      question_kind: "none",
      question: null,
      active_item_ref: "plan-item:guided-loop",
      retest: {
        intent: item.intent,
        comparability_required: true,
        comparability: "comparable",
        revision_decision: "retain",
      },
    });
    const fallback = fallbackForTeachingTurn(revision);
    assert.deepEqual(validateTeachingDraft(revision, fallback), { ok: true }, item.intent);
    assert.equal(validateTeachingDraft(revision, {
      action: "revise",
      text: item.invalid,
    }).ok, false, item.intent);
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
