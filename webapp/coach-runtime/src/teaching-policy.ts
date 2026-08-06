import {
  TEACHING_TURN_CONTRACT_SCHEMA,
  isRecord,
  type TeachingAllowedCommand,
  type TeachingCounterevidenceStatus,
  type TeachingDiscriminator,
  type TeachingEvidenceStrength,
  type TeachingEvidence,
  type TeachingNextRecommendation,
  type TeachingPhase,
  type TeachingPreparedPlanItem,
  type TeachingQuestionKind,
  type TeachingTurnContract,
} from "./contracts.ts";

export type TeachingAction =
  | "ask_discriminator"
  | "explain_candidate"
  | "teach"
  | "ask_teach_back"
  | "repair_teach_back"
  | "practice"
  | "await_execution_confirmation"
  | "prepare_retest"
  | "await_retest_confirmation"
  | "revise"
  | "ask_follow_up"
  | "pause"
  | "stop_for_discomfort";

export type TeachingPlan = { action: TeachingAction; question: string | null };
export type TeachingProviderDraft = { action: TeachingAction; text: string };
export type TeachingValidation = { ok: true } | { ok: false; reason: string };

const PHASES = new Set<TeachingPhase>([
  "intake", "hypothesize", "teach", "await_teach_back", "teach_back_repair",
  "practice_ready", "await_execution_confirmation", "retest_ready",
  "await_retest_confirmation", "revise", "follow_up", "paused", "stopped_for_discomfort",
]);
const QUESTION_KINDS = new Set<TeachingQuestionKind>([
  "none", "discriminator", "teach_back", "teach_back_repair", "follow_up",
]);
const ALLOWED_COMMANDS = new Set<TeachingAllowedCommand>([
  "training_plan.item.add", "training_plan.execution.record", "training_plan.retest.record",
]);
const RETEST_INTENTS = new Set(["none", "immediate_matched", "delayed_matched", "near_transfer"]);
const RETEST_COMPARABILITIES = new Set(["unresolved", "comparable", "not_comparable", "not_requested"]);
const REVISION_DECISIONS = new Set(["retain", "lower", "reject"]);
const TEACHING_RETEST_OUTCOMES = [
  "coach_retest_outcome.v1:improved",
  "coach_retest_outcome.v1:unchanged",
  "coach_retest_outcome.v1:worsened",
  "coach_retest_outcome.v1:mixed_or_inconclusive",
] as const;
const TEACHING_ACTIONS = new Set<TeachingAction>([
  "ask_discriminator", "explain_candidate", "teach", "ask_teach_back", "repair_teach_back",
  "practice", "await_execution_confirmation", "prepare_retest", "await_retest_confirmation",
  "revise", "ask_follow_up", "pause", "stop_for_discomfort",
]);
const ALLOWED_KEYS = new Set([
  "schema_version", "session_ref", "session_version", "phase", "observation", "primary_candidate",
  "alternatives", "cue", "changed_variable", "active_item_ref", "prepared_plan_ref", "prepared_item", "question_kind", "question", "allowed_command",
  "confirmation_intent", "retest", "ratio_sources", "approved_dose", "next_recommendation",
  "problem_id", "problem_label", "evidence_strength", "supporting_evidence", "counterevidence_status",
  "counterevidence", "discriminator", "soft_start",
]);
const LEGACY_CONTRACT_KEYS = [
  "schema_version", "session_ref", "session_version", "phase", "observation", "primary_candidate",
  "alternatives", "cue", "changed_variable", "active_item_ref", "prepared_plan_ref", "prepared_item", "question_kind", "question", "allowed_command",
  "confirmation_intent", "retest", "ratio_sources", "approved_dose", "next_recommendation",
] as const;
const DIAGNOSTIC_CONTRACT_KEYS = [
  "problem_id", "problem_label", "evidence_strength", "supporting_evidence", "counterevidence_status",
  "counterevidence", "discriminator", "soft_start",
] as const;
const EVIDENCE_STRENGTHS = new Set<TeachingEvidenceStrength>(["limited", "supported", "repeated"]);
const COUNTEREVIDENCE_STATUSES = new Set<TeachingCounterevidenceStatus>(["not_observed", "observed"]);
const EVIDENCE_KINDS = new Set(["measured", "self_reported", "observed", "inferred", "external"]);
const ITEM_REF = /^plan-item:[A-Za-z0-9._:@-]{1,159}$/;
const PLAN_REF = /^plan:[A-Za-z0-9._:@-]{1,159}$/;
const PREPARED_ITEM_FIELDS = [
  "diagnosis_ref", "knowledge_ref", "scenario_profile_ref", "baseline_metric_ref", "expected_direction",
  "practice_condition", "cue", "dose_guardrail", "matched_retest_ref", "near_transfer_retest_ref", "review_date",
] as const;
const PREPARED_ITEM_REF_PREFIXES: Record<
  "diagnosis_ref" | "knowledge_ref" | "scenario_profile_ref" | "baseline_metric_ref" | "matched_retest_ref" | "near_transfer_retest_ref",
  string
> = {
  diagnosis_ref: "diagnosis:",
  knowledge_ref: "knowledge:",
  scenario_profile_ref: "scenario:",
  baseline_metric_ref: "metric:",
  matched_retest_ref: "retest-spec:",
  near_transfer_retest_ref: "retest-spec:",
};
const PREPARED_DIRECTIONS = new Set<TeachingPreparedPlanItem["expected_direction"]>([
  "lower_better", "higher_better", "target_band", "descriptive_only", "comparison_only",
]);
const NEXT_RECOMMENDATION_FIELDS = ["scenario_name", "scenario_profile_ref", "message"] as const;
const PATH_OR_URL = /(?:https?:\/\/|file:(?:\/\/)?|(?:^|[\s"'`([{=,:])[A-Za-z]:[\\/]|\\\\)/i;
const UNSAFE_CONTRACT_TEXT = /\b(?:api[_-]?key|authorization|credential|token|raw_trace|payload)\b/i;
const INTERNAL_VOCABULARY = /\b(?:TeachingSession|TeachingTurnContract|session_ref|session_version|active_item_ref|question_kind|allowed_command|confirmation_intent|schema_version|phase|coach_retest_outcome(?:\.v\d+)?)\b|\b(?:table|field|cursor)\b/i;
const INTERNAL_ITEM_REF = /\bplan-item:[A-Za-z0-9._:@-]{1,159}\b/i;
const RAW_REFERENCE = /\b(?:analysis|run|event|segment|table|metric):/i;
const PROBLEM_ID = /^[a-z][a-z0-9._-]{0,95}$/;
const CANDIDATE_LANGUAGE = /(?:可能|也许|候选|假设|待验证|先验证|值得先验证|may|might|possible|likely)/i;
const SOFT_START_ADVANCEMENT = /(?:已(?:经)?进入|现在进入|开始).{0,8}(?:训练|练习)|(?:安排|加入).{0,8}(?:训练计划|练习)|(?:training|practice).{0,16}(?:starts|has started|is scheduled)|(?:scheduled|added).{0,16}(?:training|practice)/i;
const COMPLETION_CLAIM = /(?:已经|已)(?:完成|记录|确认|改善|提高|进步)|(?:证明|说明).{0,12}(?:学会|改善|提高)|(?:复测|训练).{0,8}(?:证明|确认).{0,12}(?:改善|提高)/;
const DOSE = /(?:\d+(?:\.\d+)?|[一二三四五六七八九十]+)\s*(?:分钟|分(?!之)(?:钟)?|秒|次|组|轮|局|runs?)/i;
const RATIO_SEMANTIC_EXPANSION = /(?:发生(?:频率|次数)?|频率|次数|片段比例|经常|常常|偏高|偏低|较好|较差|优秀|糟糕|问题|导致|因为|造成|证明|说明)/;
const UNSUPPORTED_CAUSAL_CLAIM = /(?:这是因为|因为.{0,32}(?:导致|造成)|这说明.{0,32}(?:能力差|能力不足|紧张|疲劳|握力)|(?:手部紧张|肌肉紧张|reading 能力).{0,24}(?:导致|造成|说明))/i;
const CAUSAL_DENIAL_OR_CANDIDATE = /(?:不能|无法|不应).{0,12}(?:说明|证明|归因)|(?:当作|作为).{0,12}(?:候选|假设)/;
const REVISION_LANGUAGE = /\b(?:retain|lower|reject)\b|(?:保留|降低|拒绝|继续沿着|继续用|往后放|不沿着.{0,8}练|训练方向(?:改成|调整为))/i;
const PROTOCOL_LANGUAGE = /待验证(?:的)?\s*候选|\bunresolved\b|本地\s*fallback|\bfallback\b|这次不能比较，?暂时不下结论/i;
const DISCOMFORT_TERMS = [/(?:疼痛|疼)/, /(?:麻木|发麻|手麻)/, /无力/];

function requireRecord(raw: unknown, name: string): Record<string, unknown> {
  if (!isRecord(raw)) throw new Error(`${name} must be an object`);
  return raw;
}

function boundedText(value: unknown, name: string, nullable = false): string | null {
  if (value === null && nullable) return null;
  if (typeof value !== "string" || value.length === 0 || value.length > 480 ||
      PATH_OR_URL.test(value) || UNSAFE_CONTRACT_TEXT.test(value) ||
      INTERNAL_VOCABULARY.test(value) || RAW_REFERENCE.test(value)) {
    throw new Error(`${name} is invalid`);
  }
  return value;
}

function requiredText(value: unknown, name: string): string {
  const parsed = boundedText(value, name);
  if (parsed === null) throw new Error(`${name} is invalid`);
  return parsed;
}

function diagnosticText(value: unknown, name: string, nullable = false): string | null {
  const parsed = boundedText(value, name, nullable);
  if (parsed !== null && parsed.length > 240) throw new Error(`${name} is invalid`);
  return parsed;
}

function parseProblemId(value: unknown): string | null {
  if (value === null) return null;
  const problemId = requiredText(value, "problem_id");
  if (!PROBLEM_ID.test(problemId)) throw new Error("problem_id is invalid");
  return problemId;
}

function parseDiagnosticList(value: unknown, name: string, maximum: number): string[] {
  if (!Array.isArray(value) || value.length > maximum) throw new Error(`${name} is invalid`);
  return value.map((item) => {
    const parsed = diagnosticText(item, name);
    if (parsed === null) throw new Error(`${name} is invalid`);
    return parsed;
  });
}

function parseSupportingEvidence(value: unknown): Array<string | TeachingEvidence> {
  if (!Array.isArray(value) || value.length > 4) throw new Error("supporting_evidence is invalid");
  return value.map((item) => {
    if (typeof item === "string") {
      const parsed = diagnosticText(item, "supporting_evidence");
      if (parsed === null) throw new Error("supporting_evidence is invalid");
      return parsed;
    }
    if (!isRecord(item) || Object.keys(item).length !== 3 ||
        Object.keys(item).some((key) => !["kind", "text", "refs"].includes(key)) ||
        typeof item.kind !== "string" || !EVIDENCE_KINDS.has(item.kind) ||
        !Array.isArray(item.refs) || item.refs.length > 8 ||
        item.refs.some((ref) => typeof ref !== "string" || !/^[a-z][a-z0-9_-]{0,31}:[A-Za-z0-9._:@-]{1,159}$/.test(ref))) {
      throw new Error("supporting_evidence is invalid");
    }
    const text = diagnosticText(item.text, "supporting_evidence.text");
    if (text === null) throw new Error("supporting_evidence is invalid");
    return { kind: item.kind as TeachingEvidence["kind"], text, refs: item.refs as string[] };
  });
}

function parseDiscriminator(value: unknown): TeachingDiscriminator | null {
  if (value === null) return null;
  if (!isRecord(value) || Object.keys(value).length !== 2 ||
      Object.keys(value).some((key) => key !== "kind" && key !== "prompt") ||
      (value.kind !== "question" && value.kind !== "experiment")) {
    throw new Error("discriminator is invalid");
  }
  const prompt = diagnosticText(value.prompt, "discriminator.prompt");
  if (prompt === null) throw new Error("discriminator is invalid");
  const questionCount = (prompt.match(/[?？]/g) ?? []).length;
  if ((value.kind === "question" && questionCount !== 1) ||
      (value.kind === "experiment" && questionCount !== 0)) {
    throw new Error("discriminator is invalid");
  }
  return { kind: value.kind, prompt };
}

function phaseQuestionKind(phase: TeachingPhase): TeachingQuestionKind {
  switch (phase) {
    case "intake": return "discriminator";
    case "await_teach_back": return "teach_back";
    case "teach_back_repair": return "teach_back_repair";
    case "follow_up": return "follow_up";
    default: return "none";
  }
}

function phaseCommand(phase: TeachingPhase, hasPreparedItem: boolean): TeachingAllowedCommand | null {
  switch (phase) {
    case "practice_ready": return hasPreparedItem ? "training_plan.item.add" : null;
    case "await_execution_confirmation": return "training_plan.execution.record";
    case "await_retest_confirmation": return "training_plan.retest.record";
    default: return null;
  }
}

function preparedReference(value: unknown, name: keyof typeof PREPARED_ITEM_REF_PREFIXES): string {
  if (typeof value !== "string" || value.length === 0 || value.length > 480 ||
      PATH_OR_URL.test(value) || UNSAFE_CONTRACT_TEXT.test(value)) {
    throw new Error(`prepared_item.${name} is invalid`);
  }
  const parsed = value;
  if (!parsed.startsWith(PREPARED_ITEM_REF_PREFIXES[name])) throw new Error(`prepared_item.${name} is invalid`);
  return parsed;
}

function parsePreparedItem(value: unknown): TeachingPreparedPlanItem | null {
  if (value === null) return null;
  if (!isRecord(value) || Object.keys(value).length !== PREPARED_ITEM_FIELDS.length ||
      Object.keys(value).some((key) => !PREPARED_ITEM_FIELDS.includes(key as typeof PREPARED_ITEM_FIELDS[number]))) {
    throw new Error("prepared_item is invalid");
  }
  if (typeof value.expected_direction !== "string" ||
      !PREPARED_DIRECTIONS.has(value.expected_direction as TeachingPreparedPlanItem["expected_direction"]) ||
      typeof value.review_date !== "string") {
    throw new Error("prepared_item is invalid");
  }
  return {
    diagnosis_ref: preparedReference(value.diagnosis_ref, "diagnosis_ref"),
    knowledge_ref: preparedReference(value.knowledge_ref, "knowledge_ref"),
    scenario_profile_ref: preparedReference(value.scenario_profile_ref, "scenario_profile_ref"),
    baseline_metric_ref: preparedReference(value.baseline_metric_ref, "baseline_metric_ref"),
    expected_direction: value.expected_direction as TeachingPreparedPlanItem["expected_direction"],
    practice_condition: requiredText(value.practice_condition, "prepared_item.practice_condition"),
    cue: requiredText(value.cue, "prepared_item.cue"),
    dose_guardrail: requiredText(value.dose_guardrail, "prepared_item.dose_guardrail"),
    matched_retest_ref: preparedReference(value.matched_retest_ref, "matched_retest_ref"),
    near_transfer_retest_ref: preparedReference(value.near_transfer_retest_ref, "near_transfer_retest_ref"),
    review_date: requiredText(value.review_date, "prepared_item.review_date"),
  };
}

function parseNextRecommendation(value: unknown): TeachingNextRecommendation | null {
  if (value === null) return null;
  if (!isRecord(value) || Object.keys(value).length !== NEXT_RECOMMENDATION_FIELDS.length ||
      Object.keys(value).some((key) => !NEXT_RECOMMENDATION_FIELDS.includes(key as typeof NEXT_RECOMMENDATION_FIELDS[number]))) {
    throw new Error("next_recommendation is invalid");
  }
  const scenarioName = requiredText(value.scenario_name, "next_recommendation.scenario_name");
  const scenarioProfileRef = value.scenario_profile_ref;
  if (scenarioProfileRef !== null && (typeof scenarioProfileRef !== "string" ||
      !scenarioProfileRef.startsWith("scenario:") || PATH_OR_URL.test(scenarioProfileRef) ||
      UNSAFE_CONTRACT_TEXT.test(scenarioProfileRef))) {
    throw new Error("next_recommendation.scenario_profile_ref is invalid");
  }
  const message = requiredText(value.message, "next_recommendation.message");
  if (!message.includes(scenarioName) || !message.includes("压力测试") ||
      !message.includes("新的基线") || !message.includes("不证明迁移")) {
    throw new Error("next_recommendation message is invalid");
  }
  return { scenario_name: scenarioName, scenario_profile_ref: scenarioProfileRef, message };
}

export function parseTeachingTurnContract(raw: unknown): TeachingTurnContract {
  const value = requireRecord(raw, "teaching_turn");
  for (const key of Object.keys(value)) {
    if (!ALLOWED_KEYS.has(key)) throw new Error(`teaching_turn contains unsupported field: ${key}`);
  }
  const hasDiagnosticFields = DIAGNOSTIC_CONTRACT_KEYS.every((key) => key in value);
  const expectedKeys = hasDiagnosticFields
    ? [...LEGACY_CONTRACT_KEYS, ...DIAGNOSTIC_CONTRACT_KEYS]
    : LEGACY_CONTRACT_KEYS;
  if (value.schema_version !== TEACHING_TURN_CONTRACT_SCHEMA) throw new Error("teaching_turn schema_version is invalid");
  const sessionRef = requiredText(value.session_ref, "session_ref");
  if (!/^teaching_session:[a-f0-9]{32}$/.test(sessionRef)) throw new Error("session_ref is invalid");
  if (value.active_item_ref !== null && (typeof value.active_item_ref !== "string" ||
      !ITEM_REF.test(value.active_item_ref))) throw new Error("active_item_ref is invalid");
  if (value.prepared_plan_ref !== null && (typeof value.prepared_plan_ref !== "string" ||
      !PLAN_REF.test(value.prepared_plan_ref))) throw new Error("prepared_plan_ref is invalid");
  if (Object.keys(value).length !== expectedKeys.length ||
      expectedKeys.some((key) => !(key in value))) {
    throw new Error("teaching_turn has incomplete diagnosis fields");
  }
  const preparedItem = parsePreparedItem(value.prepared_item);
  const nextRecommendation = parseNextRecommendation(value.next_recommendation);
  if ((value.prepared_plan_ref === null) !== (preparedItem === null)) {
    throw new Error("prepared command is incomplete");
  }
  const sessionVersion = value.session_version;
  if (typeof sessionVersion !== "number" || !Number.isSafeInteger(sessionVersion) || sessionVersion < 0) {
    throw new Error("session_version is invalid");
  }
  if (typeof value.phase !== "string" || !PHASES.has(value.phase as TeachingPhase)) throw new Error("phase is invalid");
  const phase = value.phase as TeachingPhase;
  if (preparedItem !== null && phase !== "practice_ready") {
    throw new Error("prepared command is invalid for phase");
  }
  if (typeof value.question_kind !== "string" || !QUESTION_KINDS.has(value.question_kind as TeachingQuestionKind) ||
      value.question_kind !== phaseQuestionKind(phase)) throw new Error("question_kind is invalid for phase");
  const expectedQuestion = phaseQuestionKind(phase) !== "none";
  const question = boundedText(value.question, "question", true);
  if (expectedQuestion !== (question !== null) || (question !== null && (question.match(/[?？]/g) ?? []).length !== 1)) {
    throw new Error("question is invalid for phase");
  }
  const problemId = hasDiagnosticFields ? parseProblemId(value.problem_id) : null;
  const problemLabel = hasDiagnosticFields ? diagnosticText(value.problem_label, "problem_label", true) : null;
  if ((problemId === null) !== (problemLabel === null)) throw new Error("problem fields are invalid");
  const evidenceStrength = hasDiagnosticFields ? value.evidence_strength : "limited";
  const supportingEvidence = hasDiagnosticFields
    ? parseSupportingEvidence(value.supporting_evidence) : [];
  const counterevidenceStatus = hasDiagnosticFields ? value.counterevidence_status : "not_observed";
  const counterevidence = hasDiagnosticFields
    ? parseDiagnosticList(value.counterevidence, "counterevidence", 2) : [];
  const discriminator = hasDiagnosticFields ? parseDiscriminator(value.discriminator) : null;
  const softStart = hasDiagnosticFields ? value.soft_start : false;
  if (typeof evidenceStrength !== "string" || !EVIDENCE_STRENGTHS.has(evidenceStrength as TeachingEvidenceStrength) ||
      (problemId !== null && supportingEvidence.length === 0) ||
      typeof counterevidenceStatus !== "string" ||
      !COUNTEREVIDENCE_STATUSES.has(counterevidenceStatus as TeachingCounterevidenceStatus) ||
      (counterevidenceStatus === "observed") !== (counterevidence.length > 0) ||
      typeof softStart !== "boolean") {
    throw new Error("diagnostic evidence is invalid");
  }
  if (discriminator !== null && discriminator.kind === "question" &&
      (phase !== "intake" || question !== discriminator.prompt)) {
    throw new Error("discriminator is invalid for phase");
  }
  if (value.allowed_command !== null && (typeof value.allowed_command !== "string" ||
      !ALLOWED_COMMANDS.has(value.allowed_command as TeachingAllowedCommand))) throw new Error("allowed_command is invalid");
  if (value.allowed_command !== phaseCommand(phase, preparedItem !== null)) throw new Error("allowed_command is invalid for phase");
  const expectedConfirmation = phase === "await_execution_confirmation" ? "execution" :
    phase === "await_retest_confirmation" ? "retest" : "none";
  if (value.confirmation_intent !== expectedConfirmation) throw new Error("confirmation_intent is invalid for phase");
  if (softStart && (phase !== "intake" || value.allowed_command !== null || expectedConfirmation !== "none")) {
    throw new Error("soft_start is invalid for phase");
  }
  if (!isRecord(value.retest) || Object.keys(value.retest).length !== 4 ||
      Object.keys(value.retest).some((key) => ![
        "intent", "comparability_required", "comparability", "revision_decision",
      ].includes(key)) ||
      typeof value.retest.intent !== "string" ||
      !RETEST_INTENTS.has(value.retest.intent) || typeof value.retest.comparability_required !== "boolean" ||
      value.retest.comparability_required !== (value.retest.intent !== "none") ||
      typeof value.retest.comparability !== "string" || !RETEST_COMPARABILITIES.has(value.retest.comparability) ||
      (value.retest.revision_decision !== null && (typeof value.retest.revision_decision !== "string" ||
        !REVISION_DECISIONS.has(value.retest.revision_decision))) ||
      (value.retest.comparability !== "comparable" && value.retest.revision_decision !== null)) {
    throw new Error("retest is invalid");
  }
  if (nextRecommendation !== null && (
    phase !== "revise" || value.retest.comparability !== "comparable" ||
    value.retest.revision_decision !== "retain"
  )) {
    throw new Error("next_recommendation is invalid for phase");
  }
  if (!Array.isArray(value.alternatives) || value.alternatives.length > 2 ||
      value.alternatives.some((item) => boundedText(item, "alternatives") === null)) throw new Error("alternatives is invalid");
  const primaryCandidate = boundedText(value.primary_candidate, "primary_candidate", true);
  if (problemId !== null && primaryCandidate !== null && !CANDIDATE_LANGUAGE.test(primaryCandidate)) {
    throw new Error("primary_candidate is invalid");
  }
  if (problemId !== null && value.alternatives.some((item) =>
      !CANDIDATE_LANGUAGE.test(requiredText(item, "alternatives")))) {
    throw new Error("alternatives is invalid");
  }
  if (!Array.isArray(value.ratio_sources) || value.ratio_sources.length > 3) throw new Error("ratio_sources is invalid");
  const ratioSources = value.ratio_sources.map((item) => {
    if (!isRecord(item) || Object.keys(item).some((key) => key !== "label" && key !== "value") ||
        typeof item.value !== "number" || !Number.isFinite(item.value) || item.value < 0 || item.value > 1) {
      throw new Error("ratio_sources is invalid");
    }
    return { label: requiredText(item.label, "ratio_sources.label"), value: item.value };
  });
  return {
    schema_version: TEACHING_TURN_CONTRACT_SCHEMA,
    session_ref: sessionRef,
    session_version: sessionVersion,
    phase,
    problem_id: problemId,
    problem_label: problemLabel,
    evidence_strength: evidenceStrength as TeachingEvidenceStrength,
    supporting_evidence: supportingEvidence,
    counterevidence_status: counterevidenceStatus as TeachingCounterevidenceStatus,
    counterevidence,
    observation: boundedText(value.observation, "observation", true),
    primary_candidate: primaryCandidate,
    alternatives: value.alternatives.map((item) => requiredText(item, "alternatives")),
    cue: boundedText(value.cue, "cue", true),
    changed_variable: boundedText(value.changed_variable, "changed_variable", true),
    active_item_ref: value.active_item_ref as string | null,
    prepared_plan_ref: value.prepared_plan_ref as string | null,
    prepared_item: preparedItem,
    next_recommendation: nextRecommendation,
    question_kind: value.question_kind as TeachingQuestionKind,
    question,
    allowed_command: value.allowed_command as TeachingAllowedCommand | null,
    confirmation_intent: expectedConfirmation,
    retest: {
      intent: value.retest.intent as TeachingTurnContract["retest"]["intent"],
      comparability_required: value.retest.comparability_required,
      comparability: value.retest.comparability as TeachingTurnContract["retest"]["comparability"],
      revision_decision: value.retest.revision_decision as TeachingTurnContract["retest"]["revision_decision"],
    },
    ratio_sources: ratioSources,
    approved_dose: boundedText(value.approved_dose, "approved_dose", true),
    discriminator,
    soft_start: softStart,
  };
}

export function planTeachingTurn(contract: TeachingTurnContract): TeachingPlan {
  if (teachingTurnRequiresLocalFallback(contract)) {
    return { action: "pause", question: null };
  }
  switch (contract.phase) {
    case "intake": return { action: "ask_discriminator", question: contract.question };
    case "hypothesize": return { action: "explain_candidate", question: null };
    case "teach": return { action: "teach", question: null };
    case "await_teach_back": return { action: "ask_teach_back", question: contract.question };
    case "teach_back_repair": return { action: "repair_teach_back", question: contract.question };
    case "practice_ready": return { action: "practice", question: null };
    case "await_execution_confirmation": return { action: "await_execution_confirmation", question: null };
    case "retest_ready": return { action: "prepare_retest", question: null };
    case "await_retest_confirmation": return { action: "await_retest_confirmation", question: null };
    case "revise": return { action: "revise", question: null };
    case "follow_up": return { action: "ask_follow_up", question: contract.question };
    case "paused": return { action: "pause", question: null };
    case "stopped_for_discomfort": return { action: "stop_for_discomfort", question: null };
  }
}

export function teachingTurnRequiresLocalFallback(contract: TeachingTurnContract): boolean {
  return contract.phase === "intake" && contract.primary_candidate === null;
}

function fallbackText(contract: TeachingTurnContract, plan: TeachingPlan): string {
  const sentence = (value: string): string => /[。.!！?？]$/.test(value) ? value : `${value}。`;
  const evidenceText = (value: TeachingTurnContract["supporting_evidence"][number]): string =>
    typeof value === "string" ? value : value.text;
  const observation = contract.observation ? sentence(contract.observation) : "";
  const candidate = contract.primary_candidate ? sentence(contract.primary_candidate) : "";
  const problem = contract.problem_label ? sentence(`我先看${contract.problem_label}`) : "";
  const supportingEvidence = contract.supporting_evidence.length > 0
    ? sentence(`目前看到${evidenceText(contract.supporting_evidence[0])}`) : "";
  const alternatives = contract.alternatives.length > 0
    ? sentence(contract.alternatives[0])
    : "";
  const cue = contract.cue ? `这组只记住一件事：${sentence(contract.cue)}` : "";
  const dose = contract.approved_dose ? sentence(contract.approved_dose) : "";
  switch (plan.action) {
    case "ask_discriminator": return `${problem}${observation}${supportingEvidence}${candidate}${alternatives}${plan.question}`.trim();
    case "explain_candidate": return `${observation}${candidate}${alternatives}`.trim();
    case "teach": return `${observation}${candidate}${cue}`.trim();
    case "ask_teach_back": return `${cue}${plan.question}`.trim();
    case "repair_teach_back": return `${cue}${plan.question}`.trim();
    case "practice": return `${cue}${dose}这组只改${contract.changed_variable ?? "这一条提醒"}，其他条件先不动。`.trim();
    case "await_execution_confirmation": return "已准备好记录这次练习。请在确认界面核对事实后选择确认或取消。";
    case "prepare_retest":
      if (contract.retest.intent === "immediate_matched") {
        return "接下来按同一个场景和设置复测，只看这条提醒有没有帮到这一轮。";
      }
      if (contract.retest.intent === "delayed_matched") {
        return "下次按同一个场景和设置复测，看看这次调整能不能保留下来。";
      }
      if (contract.retest.intent === "near_transfer") {
        return "接下来换一个相近任务，只改一个条件，看看这条提醒还能不能用。这个结果不能直接代表主游戏表现。";
      }
      return "现在还缺能对得上的复测结果，训练方向先不改。";
    case "await_retest_confirmation": return "已准备好记录这次复测。请在确认界面核对事实后选择确认或取消。";
    case "revise":
      if (contract.retest.comparability === "not_comparable") {
        return "这次的场景、设置或记录条件和之前没对齐，分数直接放一起看会误导。训练方向先不改，再按原条件重新复测。";
      }
      if (contract.retest.revision_decision === "retain") {
        if (contract.next_recommendation !== null) {
          return `这次同条件复测支持这条提示当下有帮助，先继续用。之后再做延迟检查，现在还不能说已经稳定掌握。${contract.next_recommendation.message}`;
        }
        if (contract.retest.intent === "delayed_matched") {
          return "隔一段时间后再按同条件复测，结果仍支持这个方向，说明这条提醒保留下来了，先继续用。";
        }
        if (contract.retest.intent === "near_transfer") {
          return "换到相近任务并只改一个条件后，这条提醒仍然有帮助，可以先继续用；这不代表主游戏表现已经提升。";
        }
        return "这次同条件复测支持这条提醒当下有帮助，先继续用。之后再做延迟检查，现在还不能说已经稳定掌握。";
      }
      if (contract.retest.revision_decision === "lower") {
        if (contract.retest.intent === "delayed_matched") {
          return "隔一段时间后的同条件复测没有稳定保留下来，先把这个方向往后放。";
        }
        if (contract.retest.intent === "near_transfer") {
          return "这条提醒在相近任务里没有稳定复现，先把这个方向往后放；这不代表主游戏表现。";
        }
        return "这次同条件复测没有明显支持这个方向，先把它往后放。";
      }
      if (contract.retest.revision_decision === "reject") {
        if (contract.retest.intent === "delayed_matched") {
          return "隔一段时间后的同条件复测不支持这个方向，先不沿着它练，回头看另外几个可能。";
        }
        if (contract.retest.intent === "near_transfer") {
          return "相近任务的结果不支持这个方向，先不沿着它练；这不代表主游戏表现。";
        }
        return "这次同条件复测不支持这个方向，先不沿着它练，回头看另外几个可能。";
      }
      return "这次结果还不够明确，训练方向先不改。";
    case "ask_follow_up": return String(plan.question);
    case "pause":
      if (teachingTurnRequiresLocalFallback(contract)) {
        return "这次分析的证据还不足以形成教学结论，所以先不把它编成训练。你可以解除这条分析后继续普通对话，或换一条有明确问题的分析。";
      }
      return "那这组先不练，训练计划不改。你准备继续时再告诉我。";
    case "stop_for_discomfort": return "那先别练这组了，休息一下，别硬撑。";
  }
}

export function fallbackForTeachingTurn(contract: TeachingTurnContract): TeachingProviderDraft {
  const plan = planTeachingTurn(contract);
  return { action: plan.action, text: fallbackText(contract, plan) };
}

export function teachingTurnHoldsState(contract: TeachingTurnContract): boolean {
  return contract.soft_start || teachingTurnRequiresLocalFallback(contract) ||
    contract.phase === "paused" || contract.phase === "stopped_for_discomfort" ||
    (contract.phase === "revise" && contract.retest.revision_decision === null) ||
    (contract.phase === "practice_ready" && contract.prepared_item === null);
}

export function teachingEnvelopeInstruction(contract: TeachingTurnContract): string {
  const plan = planTeachingTurn(contract);
  const approved = {
    action: plan.action,
    question: plan.question,
    problem_id: contract.problem_id,
    problem_label: contract.problem_label,
    evidence_strength: contract.evidence_strength,
    supporting_evidence: contract.supporting_evidence,
    counterevidence_status: contract.counterevidence_status,
    counterevidence: contract.counterevidence,
    observation: contract.observation,
    primary_candidate: contract.primary_candidate,
    alternatives: contract.alternatives,
    cue: contract.cue,
    changed_variable: contract.changed_variable,
    active_item_ref: contract.active_item_ref,
    prepared_plan_ref: contract.prepared_plan_ref,
    prepared_item: contract.prepared_item,
    next_recommendation: contract.next_recommendation?.message ?? null,
    approved_dose: contract.approved_dose,
    ratio_sources: contract.ratio_sources,
    retest: contract.retest,
    discriminator: contract.discriminator,
    soft_start: contract.soft_start,
  };
  const retestWrite = contract.allowed_command === "training_plan.retest.record"
    ? `For training_plan.retest.record, result must be exactly one of ${TEACHING_RETEST_OUTCOMES.join(", ")}; ` +
      `not_comparable or unavailable must use coach_retest_outcome.v1:mixed_or_inconclusive. `
    : "";
  const preparedItemWrite = contract.allowed_command === "training_plan.item.add" &&
      contract.prepared_plan_ref !== null && contract.prepared_item !== null
    ? `For training_plan.item.add, use exactly these tool parameters without changing any key or value: ${JSON.stringify({ plan_ref: contract.prepared_plan_ref, item_payload: contract.prepared_item })}. `
    : "";
  return `Internal teaching contract: reply with exactly one JSON object {"action":"${plan.action}","text":"..."}. ` +
    `Do not add keys, Markdown, tool names, phase changes, completion claims, unapproved dose, causes, or another question. ` +
    `Treat every candidate as a possibility to check, never as an established cause. ` +
    (contract.soft_start ? "This is a soft start: do not claim that training has advanced, started, or been scheduled. " : "") +
    `Use active_item_ref only as the exact item_ref tool parameter; never put its field name or value in user-visible text. ` +
    preparedItemWrite +
    retestWrite +
    `Keep the stage-required approved content, but do not list every supporting evidence item or alternative in the first explanation. ` +
    `For an abstract movement explanation, you may use one short analogy. It may help understanding, but it is not evidence and cannot prove a cause. ` +
    `Naturalize only this approved content: ${JSON.stringify(approved)}`;
}

export function parseTeachingProviderDraft(raw: string): TeachingProviderDraft | null {
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return null;
  }
  if (!isRecord(parsed) || Object.keys(parsed).length !== 2 ||
      Object.keys(parsed).some((key) => key !== "action" && key !== "text") ||
      typeof parsed.action !== "string" || !TEACHING_ACTIONS.has(parsed.action as TeachingAction) ||
      typeof parsed.text !== "string") {
    return null;
  }
  return { action: parsed.action as TeachingAction, text: parsed.text };
}

function questionCount(value: string): number {
  return (value.match(/[?？]/g) ?? []).length;
}

const DIRECT_FOLLOW_UP_REQUEST = /(?:告诉我|反馈|说说).{0,32}(?:完成情况|主观感受|感受|练习(?:结果|情况)|反馈)/;

function ratioAliasAllowed(value: number, text: string): boolean {
  const percent = value * 100;
  const percentages = [String(percent), percent.toFixed(1), percent.toFixed(2)]
    .map((item) => item.replace(/\.0+$/, ""));
  return percentages.some((item) => text.includes(`${item}%`) || text.includes(`${item}％`));
}

function hasUnsupportedRatioMeaning(contract: TeachingTurnContract, text: string): boolean {
  const mentionsRatio = contract.ratio_sources.some(({ label, value }) =>
    text.includes(label) || text.includes(String(value)) || ratioAliasAllowed(value, text),
  );
  return mentionsRatio && RATIO_SEMANTIC_EXPANSION.test(text);
}

function hasUnapprovedChineseRatioFraction(contract: TeachingTurnContract, text: string): boolean {
  return contract.ratio_sources.length > 0 &&
    /(?:约|大约|大概)?(?:二分之一|三分之一|三分之二|四分之一|四分之三|一半|半数)/.test(text);
}

function hasUnsupportedCausalClaim(text: string): boolean {
  return !CAUSAL_DENIAL_OR_CANDIDATE.test(text) && UNSUPPORTED_CAUSAL_CLAIM.test(text);
}

function matchesRevisionDecision(text: string, decision: TeachingTurnContract["retest"]["revision_decision"]): boolean {
  if (decision === "retain") {
    if (/(?:不|未|没有|并未|不该|不应|不要|别).{0,8}(?:支持|继续用|保留)|往后放|降低|下调|不沿着|放弃|拒绝/.test(text)) return false;
    return /继续用|保留(?:这个|当前)?方向/.test(text);
  }
  if (decision === "lower") {
    if (/(?:不|未|没有|并未|不该|不应|不要|别).{0,8}(?:往后放|降低|下调)|继续用|保留(?:这个|当前)?方向|不沿着|放弃|拒绝/.test(text)) return false;
    return /往后放|(?:降低|下调).{0,8}(?:优先级|信心|置信)/.test(text);
  }
  if (decision === "reject") {
    if (/(?:不|未|没有|并未|不该|不应|不应该|不要|别).{0,8}(?:拒绝|放弃)|不是.{0,6}不沿着|继续用|保留(?:这个|当前)?方向|往后放|降低|下调/.test(text)) return false;
    return /不(?:再)?沿着.{0,8}练|放弃(?:这个|当前)?方向|拒绝(?:这个|当前)?方向/.test(text);
  }
  return false;
}

function matchesRetestIntentBoundary(contract: TeachingTurnContract, text: string): boolean {
  if (contract.retest.intent === "immediate_matched") {
    const durableClaim = /(?:已经)?(?:稳定掌握|长期(?:改善|提升|进步)|学会)|保留下来/.test(text);
    const durableDenial = /(?:不能|还不能|不代表|无法).{0,12}(?:稳定掌握|长期(?:改善|提升|进步)|学会|保留下来)/.test(text);
    return /(?:这次|本轮|这一轮|当下)/.test(text) && (!durableClaim || durableDenial);
  }
  if (contract.retest.intent === "delayed_matched") {
    return /(?:隔一段时间|过一段时间|延迟|下次|之后)/.test(text) &&
      /(?:保留|保持|稳定|仍)/.test(text);
  }
  if (contract.retest.intent === "near_transfer") {
    return /(?:相近任务|近迁移)/.test(text) &&
      /(?:不代表|不能代表|不能直接代表).{0,12}(?:主游戏|实战)/.test(text);
  }
  return false;
}


type RequiredTeachingContent = {
  value: string;
  exact: boolean;
  minimumAnchors: number;
  kind: "problem" | "evidence" | "observation" | "candidate" | "cue" | "dose";
};

function requiredTeachingContent(contract: TeachingTurnContract, plan: TeachingPlan): RequiredTeachingContent[] {
  const values: Array<RequiredTeachingContent | null> = [];
  const semantic = (
    value: string | null,
    minimumAnchors: number,
    kind: RequiredTeachingContent["kind"],
  ): RequiredTeachingContent | null =>
    value === null ? null : { value, exact: false, minimumAnchors, kind };
  const exact = (value: string | null): RequiredTeachingContent | null =>
    value === null ? null : { value, exact: true, minimumAnchors: 0, kind: "dose" };
  switch (plan.action) {
    case "ask_discriminator":
      values.push(
        semantic(contract.problem_label, 1, "problem"),
        semantic(contract.observation, 2, "observation"),
        contract.supporting_evidence.length === 0 ? null : semantic(
          typeof contract.supporting_evidence[0] === "string"
            ? contract.supporting_evidence[0]
            : contract.supporting_evidence[0].text,
          1,
          "evidence",
        ),
        semantic(contract.primary_candidate, 1, "candidate"),
        semantic(contract.alternatives[0] ?? null, 1, "candidate"),
      );
      break;
    case "explain_candidate":
      values.push(
        semantic(contract.observation, 2, "observation"),
        semantic(contract.primary_candidate, 1, "candidate"),
        semantic(contract.alternatives[0] ?? null, 1, "candidate"),
      );
      break;
    case "teach":
      values.push(
        semantic(contract.primary_candidate, 1, "candidate"),
        semantic(contract.cue, 2, "cue"),
      );
      break;
    case "ask_teach_back":
    case "repair_teach_back":
      values.push(semantic(contract.cue, 2, "cue"));
      break;
    case "practice":
      values.push(semantic(contract.cue, 2, "cue"), exact(contract.approved_dose));
      break;
  }
  return values.filter((value): value is RequiredTeachingContent => value !== null);
}

function normalizedTeachingText(value: string): string {
  return value.normalize("NFKC").toLowerCase().replace(/[^\p{L}\p{N}]+/gu, "");
}

function numericUnitTokens(value: string): string[] {
  return value.normalize("NFKC").match(
    /(?:\d+(?:\.\d+)?|[一二三四五六七八九十]+)\s*(?:分钟|秒钟|秒|次|组|轮|局|runs?)/giu,
  ) ?? [];
}

function candidateCore(value: string): string {
  return value
    .replace(/^(?:当前更值得先验证的是|我先从|也可能(?:与|和))/, "")
    .replace(/(?:这个方向查起|有关)[。.!！]?$/, "")
    .trim();
}

function exactContractAnchors(value: string): string[] {
  const normalized = normalizedTeachingText(value);
  const anchors = new Set<string>();
  for (let index = 0; index < normalized.length - 3; index += 1) {
    anchors.add(normalized.slice(index, index + 4));
  }
  return [...anchors];
}

function hasRequiredExactContractAnchors(value: string, text: string, minimumAnchors: number): boolean {
  const normalizedValue = normalizedTeachingText(value);
  const normalizedText = normalizedTeachingText(text);
  let matched = 0;
  let nextEligibleIndex = 0;
  for (let index = 0; index < normalizedValue.length - 3; index += 1) {
    if (index < nextEligibleIndex) continue;
    if (normalizedText.includes(normalizedValue.slice(index, index + 4))) {
      matched += 1;
      nextEligibleIndex = index + 4;
      if (matched >= minimumAnchors) return true;
    }
  }
  return false;
}

function preservesRequiredTeachingContent(required: RequiredTeachingContent, text: string): boolean {
  const value = required.kind === "candidate" ? candidateCore(required.value) : required.value;
  const normalizedValue = normalizedTeachingText(value);
  const normalizedText = normalizedTeachingText(text);
  if (normalizedText.includes(normalizedValue)) return true;
  if (required.exact || numericUnitTokens(value).some((token) => !normalizedText.includes(normalizedTeachingText(token)))) {
    return false;
  }
  return hasRequiredExactContractAnchors(value, text, required.minimumAnchors);
}

function negatesRequiredTeachingContent(required: RequiredTeachingContent, text: string): boolean {
  if (required.kind === "dose") return false;
  const value = required.kind === "candidate" ? candidateCore(required.value) : required.value;
  const normalizedText = normalizedTeachingText(text);
  const normalizedValue = normalizedTeachingText(value);
  if (normalizedText.includes(normalizedValue)) {
    const escapedValue = normalizedValue.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    const externalNegation = new RegExp(
      `(?:不是|并非|没有|没|不再|不要|别).{0,6}${escapedValue}|` +
      `${escapedValue}.{0,6}(?:不是|并非|没有|不对)`,
    );
    if (!externalNegation.test(normalizedText)) return false;
  }
  return exactContractAnchors(value).some((anchor) => {
    const escaped = anchor.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    return new RegExp(`(?:不是|并非|没有|没|不再|不要|别).{0,6}${escaped}|${escaped}.{0,6}(?:不是|并非|没有|不对)`).test(normalizedText);
  });
}

function discriminatorChoices(contract: TeachingTurnContract, plan: TeachingPlan): string[] {
  if (contract.question_kind !== "discriminator" || plan.question === null) return [];
  const normalizedQuestion = normalizedTeachingText(plan.question);
  return [contract.primary_candidate, ...contract.alternatives]
    .filter((value): value is string => value !== null)
    .map(candidateCore)
    .filter((value) => {
      const normalized = normalizedTeachingText(value);
      return normalized.length >= 4 && normalizedQuestion.includes(normalized);
    });
}

function preservesContractQuestion(contract: TeachingTurnContract, plan: TeachingPlan, text: string): boolean {
  if (plan.question === null) return true;
  if (normalizedTeachingText(text).includes(normalizedTeachingText(plan.question))) return true;
  if (contract.question_kind === "teach_back" || contract.question_kind === "teach_back_repair") {
    return /(?:复述|说说|讲讲|描述|解释|怎么做)/.test(text) &&
      /(?:提示|提醒|注意点|动作|做法|内容)/.test(text);
  }
  if (contract.question_kind === "discriminator") {
    const choices = discriminatorChoices(contract, plan);
    return /(?:哪|哪个|是否|还是|更常|更容易|更明显)/.test(text) &&
      choices.every((choice) => hasRequiredExactContractAnchors(choice, text, 1)) &&
      exactContractAnchors(plan.question).some((anchor) => normalizedTeachingText(text).includes(anchor));
  }
  return exactContractAnchors(plan.question).some((anchor) => normalizedTeachingText(text).includes(anchor));
}

export function validateTeachingDraft(contract: TeachingTurnContract, draft: TeachingProviderDraft): TeachingValidation {
  const plan = planTeachingTurn(contract);
  if (draft.action !== plan.action) return { ok: false, reason: "action is outside the teaching contract" };
  if (typeof draft.text !== "string" || draft.text.length === 0 || draft.text.length > 1200 || PATH_OR_URL.test(draft.text)) {
    return { ok: false, reason: "text is invalid" };
  }
  if (INTERNAL_VOCABULARY.test(draft.text) || INTERNAL_ITEM_REF.test(draft.text) ||
      (contract.active_item_ref !== null && draft.text.includes(contract.active_item_ref))) {
    return { ok: false, reason: "text exposes internal vocabulary" };
  }
  if (PROTOCOL_LANGUAGE.test(draft.text)) return { ok: false, reason: "text exposes protocol language" };
  if (contract.phase === "stopped_for_discomfort" &&
      DISCOMFORT_TERMS.filter((pattern) => pattern.test(draft.text)).length > 1) {
    return { ok: false, reason: "text adds a discomfort checklist" };
  }
  if (COMPLETION_CLAIM.test(draft.text)) return { ok: false, reason: "text claims an unconfirmed fact" };
  if (contract.soft_start && SOFT_START_ADVANCEMENT.test(draft.text)) {
    return { ok: false, reason: "soft start advances the teaching session" };
  }
  if (contract.approved_dose === null && DOSE.test(draft.text)) {
    return { ok: false, reason: "text adds an unapproved dose" };
  }
  if (hasUnsupportedRatioMeaning(contract, draft.text)) return { ok: false, reason: "text extends ratio semantics" };
  if (hasUnapprovedChineseRatioFraction(contract, draft.text)) {
    return { ok: false, reason: "text converts a ratio to an unapproved Chinese fraction" };
  }
  if (hasUnsupportedCausalClaim(draft.text)) return { ok: false, reason: "text makes an unsupported causal claim" };
  if (contract.phase === "revise" && contract.retest.comparability !== "comparable" &&
      REVISION_LANGUAGE.test(draft.text)) {
    return { ok: false, reason: "text adds a revision decision without a comparable retest" };
  }
  const requiredContent = requiredTeachingContent(contract, plan);
  if (requiredContent.some((value) =>
    !preservesRequiredTeachingContent(value, draft.text) || negatesRequiredTeachingContent(value, draft.text))) {
    return { ok: false, reason: "text omits required teaching content" };
  }
  if (contract.problem_id !== null && requiredContent.some((value) => value.kind === "candidate") &&
      !CANDIDATE_LANGUAGE.test(draft.text)) {
    return { ok: false, reason: "text states a candidate as a fact" };
  }
  if (contract.phase === "revise") {
    const decision = contract.retest.revision_decision;
    if (decision !== null && !matchesRevisionDecision(draft.text, decision)) {
      return { ok: false, reason: "text omits the revision decision" };
    }
    if (decision !== null && !matchesRetestIntentBoundary(contract, draft.text)) {
      return { ok: false, reason: "text exceeds the retest intent boundary" };
    }
    if (contract.next_recommendation !== null && !draft.text.includes(contract.next_recommendation.message)) {
      return { ok: false, reason: "text omits the next recommendation" };
    }
  }
  const expectedQuestions = plan.question === null ? 0 : 1;
  if (questionCount(draft.text) !== expectedQuestions) return { ok: false, reason: "text has an invalid question count" };
  if (!preservesContractQuestion(contract, plan, draft.text)) {
    return { ok: false, reason: "text does not preserve the contract question" };
  }
  if (expectedQuestions === 1 && contract.question_kind !== "discriminator" &&
      /(?:还是|以及|并且|同时).{0,32}[?？]/.test(draft.text)) {
    return { ok: false, reason: "text contains a compound question" };
  }
  return { ok: true };
}

/**
 * A user may interrupt the lesson to question an explanation or correct Coach.
 * Keep that answer within the same safety boundaries, but do not force it to
 * repeat the phase's scripted question or advance the TeachingSession.
 */
export function validateTeachingDirectResponse(
  contract: TeachingTurnContract,
  text: string,
): TeachingValidation {
  if (typeof text !== "string" || text.length === 0 || text.length > 1200 || PATH_OR_URL.test(text)) {
    return { ok: false, reason: "text is invalid" };
  }
  if (INTERNAL_VOCABULARY.test(text) || INTERNAL_ITEM_REF.test(text) ||
      (contract.active_item_ref !== null && text.includes(contract.active_item_ref))) {
    return { ok: false, reason: "text exposes internal vocabulary" };
  }
  if (PROTOCOL_LANGUAGE.test(text) || COMPLETION_CLAIM.test(text)) {
    return { ok: false, reason: "text exposes protocol language or claims an unconfirmed fact" };
  }
  const allowedDoseTokens = new Set(
    numericUnitTokens(contract.approved_dose ?? "").map(normalizedTeachingText),
  );
  const responseDoseTokens = numericUnitTokens(text).map(normalizedTeachingText);
  if (responseDoseTokens.some((token) => !allowedDoseTokens.has(token))) {
    return { ok: false, reason: "text adds an unapproved dose" };
  }
  if (hasUnsupportedRatioMeaning(contract, text) ||
      hasUnapprovedChineseRatioFraction(contract, text) ||
      hasUnsupportedCausalClaim(text)) {
    return { ok: false, reason: "text exceeds the evidence boundary" };
  }
  if (contract.phase === "revise" && contract.retest.comparability !== "comparable" &&
      REVISION_LANGUAGE.test(text)) {
    return { ok: false, reason: "text adds a revision decision without a comparable retest" };
  }
  if (questionCount(text) !== 0) {
    return { ok: false, reason: "direct answer adds a follow-up question" };
  }
  if (DIRECT_FOLLOW_UP_REQUEST.test(text)) {
    return { ok: false, reason: "direct answer adds an imperative follow-up" };
  }
  return { ok: true };
}
