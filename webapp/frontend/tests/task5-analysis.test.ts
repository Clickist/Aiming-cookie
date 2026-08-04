import assert from "node:assert/strict";
import { test } from "node:test";

import {
  getAnalysisViewState,
  presentAnalysisWorkspace,
} from "../lib/contracts";
import type {
  AnalysisResultV2,
  SessionStatus,
} from "../lib/types";

function result(overrides: Partial<AnalysisResultV2> = {}): AnalysisResultV2 {
  return {
    schema_version: "analysis_result.v2",
    analysis_id: "analysis:42",
    analysis_type: "flicking",
    input_mode: "multimodal",
    kovaak_run_ref: "run:7",
    evidence: {
      sources: {
        raw_input: {
          source: "raw_input",
          availability: "available",
          alignment: "aligned",
        },
        mp4: {
          source: "mp4",
          availability: "available",
          alignment: "aligned",
        },
      },
      provenance: {},
      availability: { raw_input: "available", mp4: "available" },
      alignment: { status: "aligned" },
      warnings: [],
    },
    deterministic: {
      support_status: "supported",
      diagnosis: {
        profile: {
          archetype_id: "decel-wave",
          label: "减速波动型",
          confidence: 0.78,
          secondary_tags: ["远距离过冲"],
        },
        issues: [{
          signal: "停枪控制不稳",
          severity: "fix",
          root_causes: [
            { level: "symptom", text: "接近目标时减速不足" },
            { level: "physical", text: "反向修正出现较晚" },
            { level: "training", text: "需要稳定停枪节奏" },
          ],
          prescriptions: [{ scenario: "1wall5targets_pasu", reason: "练习减速" }],
          priority: 1,
          priority_reason: "远距离目标持续出现",
        }],
        summary: {},
        comparison: null,
        meta: {},
      },
      metrics: {
        sparc: {
          key: "sparc",
          value: -4.21,
          unit: "score",
          availability: "available",
          coverage: 1,
          classification: "deterministic",
          metric_version: "sparc.v1",
          limitations: [],
          provenance: { kind: "derived", sources: ["raw_input"] },
        },
        visual_guess: {
          key: "visual_guess",
          value: 0.4,
          unit: "ratio",
          availability: "available",
          coverage: 0.4,
          classification: "experimental",
          metric_version: "visual_guess.v1",
          limitations: ["visual_quality_limited"],
          provenance: { kind: "derived", sources: ["mp4"] },
        },
      },
      timeline: [{
        frame: null,
        time_s: 0.8,
        relative_ms: 800,
        type: "corrective",
        label: "反向修正",
        source: "raw_input",
      }],
      limitations: [],
    },
    artifact_manifest: {
      schema_version: "artifact_manifest.v2",
      external_inputs: [],
      owned_outputs: [],
    },
    input_snapshot: {
      scenario: "1wall 6targets small",
      scenario_resolution: {
        schema_version: "scenario_resolution.v1",
        aim_family: "static_clicking",
        claim_ceiling: "family_specific",
        family_analyzer_dispatch: "allowed",
        limitations: [],
      },
      sources: {},
      trace: null,
    },
    created_at: "2026-07-25T06:32:00Z",
    completed_at: "2026-07-25T06:33:00Z",
    warnings: [],
    errors: [],
    normalization_issues: [],
    ...overrides,
  };
}

function session(overrides: Partial<SessionStatus> = {}): SessionStatus {
  return {
    id: 42,
    status: "done",
    result: result(),
    error: null,
    llm_cost_cny: null,
    created_at: "2026-07-25T06:32:00Z",
    attempts: 1,
    max_attempts: 2,
    worker_id: null,
    started_at: "2026-07-25T06:32:02Z",
    finished_at: "2026-07-25T06:33:00Z",
    analysis_type: "flicking",
    input_mode: "multimodal",
    kovaak_run_id: 7,
    history: {
      analysis_ref: "analysis:42",
      run_ref: "run:7",
      scenario: "1wall 6targets small",
      input_mode: "multimodal",
      source_availability: { stats: "available", performance: "available" },
      trace_quality: {
        state: "attached",
        availability: "available",
        alignment_status: "aligned",
        coverage: 1,
      },
      visual_replay: {
        kind: "seekable_mp4",
        available: true,
        seekable: true,
        endpoint: "/api/sessions/42/video",
        artifact_ref: "analysis:42:video",
        reason: null,
      },
      diagnosis_locator: { analysis_ref: "analysis:42", section: "diagnosis" },
      evidence_refs: [],
    },
    ...overrides,
  };
}

test("analysis state distinguishes loading, queue, running, retryable, failed, done, and deleted", () => {
  assert.equal(getAnalysisViewState({ loading: true }), "loading");
  assert.equal(getAnalysisViewState({ session: session({ status: "queued" }) }), "queued");
  assert.equal(getAnalysisViewState({ session: session({ status: "running" }) }), "running");
  assert.equal(getAnalysisViewState({ session: session({ status: "failed", error: { schema_version: "error.v1", category: "internal_unknown", code: "video_failed", message: "视频分析失败", retryable: true, trace_id: null, details: null } }) }), "retryable");
  assert.equal(getAnalysisViewState({ session: session({ status: "failed" }) }), "failed");
  assert.equal(getAnalysisViewState({ session: session() }), "done");
  assert.equal(getAnalysisViewState({ errorStatus: 404 }), "deleted-unavailable");
  assert.equal(getAnalysisViewState({ errorStatus: 503 }), "unavailable");
});

test("family status uses only frozen resolution and analyzer support", () => {
  assert.equal(presentAnalysisWorkspace(session())?.family.status, "supported");

  const descriptive = result();
  descriptive.input_snapshot.scenario_resolution = {
    ...descriptive.input_snapshot.scenario_resolution!,
    claim_ceiling: "descriptive_only",
    family_analyzer_dispatch: "none",
  };
  assert.equal(presentAnalysisWorkspace(session({ result: descriptive }))?.family.status, "descriptive");

  const outcome = result({ deterministic: { support_status: "outcome_only", metrics: {}, limitations: ["scenario_not_in_active_manifest"] } });
  assert.equal(presentAnalysisWorkspace(session({ result: outcome }))?.family.status, "outcome-only");

  const unresolved = result();
  unresolved.input_snapshot.scenario = "obviously flicking by name";
  delete unresolved.input_snapshot.scenario_resolution;
  assert.equal(presentAnalysisWorkspace(session({ result: unresolved }))?.family.status, "unavailable");
  assert.equal(presentAnalysisWorkspace(session({ result: unresolved }))?.family.code, "unknown");
});

test("workspace separates formal metrics from experimental or unavailable metrics", () => {
  const presentation = presentAnalysisWorkspace(session());
  assert.deepEqual(presentation?.metrics.formal.map((metric) => metric.key), ["运动平滑度（SPARC）"]);
  assert.deepEqual(presentation?.metrics.limited.map((metric) => metric.key), ["指标名称暂不可展示"]);
  assert.equal(presentation?.issues.length, 1);
  assert.equal(presentation?.issues[0]?.rootCauses.length, 3);
  assert.equal(presentation?.issues[0]?.prescriptions.length, 1);
  assert.equal(presentation?.issues[0]?.presentationKind, "legacy");
});

test("workspace keeps deterministic metrics descriptive when family trust is not formal", () => {
  const partial = result({ deterministic: {
    ...result().deterministic,
    support_status: "partial",
  } });
  const descriptiveOnly = result({
    input_snapshot: {
      ...result().input_snapshot,
      scenario_resolution: {
        ...result().input_snapshot.scenario_resolution!,
        claim_ceiling: "descriptive_only",
        family_analyzer_dispatch: "none",
      },
    },
  });
  const outcomeOnly = result({ deterministic: {
    ...result().deterministic,
    support_status: "outcome_only",
  } });
  const unavailable = result({
    input_snapshot: {
      ...result().input_snapshot,
      scenario_resolution: {
        ...result().input_snapshot.scenario_resolution!,
        family_analyzer_dispatch: "none",
      },
    },
  });

  for (const value of [partial, descriptiveOnly, outcomeOnly, unavailable]) {
    const presentation = presentAnalysisWorkspace(session({ result: value }));
    assert.deepEqual(presentation?.metrics.formal, []);
    assert.deepEqual(
      presentation?.metrics.limited.map((metric) => metric.key),
        ["运动平滑度（SPARC）", "指标名称暂不可展示"],
    );
  }
});

test("workspace preserves only safe optional issue knowledge refs", () => {
  const withRefs = result();
  const issue = withRefs.deterministic.diagnosis?.issues[0];
  assert.ok(issue);
  issue.observation_ref = "event.flick";
  issue.knowledge_registry_version = "2026-07-29.v4";
  issue.knowledge_entry_refs = ["knowledge:static.flicking-terminal-control@2"];
  issue.plain_language_meaning = "This observation is a candidate explanation, not a confirmed mechanism.";
  issue.claim_level = "deterministic_rule";

  const projected = presentAnalysisWorkspace(session({ result: withRefs }))?.issues[0];
  assert.equal(projected?.observationRef, "event.flick");
  assert.equal(projected?.knowledgeRegistryVersion, "2026-07-29.v4");
  assert.deepEqual(projected?.knowledgeEntryRefs, ["knowledge:static.flicking-terminal-control@2"]);
  assert.equal(projected?.candidateExplanation, "This observation is a candidate explanation, not a confirmed mechanism.");
  assert.equal(projected?.claimLevel, "deterministic_rule");
  assert.equal(projected?.claimLabel, "规则化观察");
  assert.equal(projected?.presentationKind, "registry-backed");

  const malformed = result();
  const malformedIssue = malformed.deterministic.diagnosis?.issues[0];
  assert.ok(malformedIssue);
  malformedIssue.observation_ref = "C:\\Users\\point\\private";
  malformedIssue.knowledge_registry_version = "2026-07-29.v4";
  malformedIssue.knowledge_entry_refs = ["knowledge:static.flicking-terminal-control@0"];

  const filtered = presentAnalysisWorkspace(session({ result: malformed }))?.issues[0];
  assert.equal(filtered?.observationRef, null);
  assert.equal(filtered?.knowledgeRegistryVersion, null);
  assert.deepEqual(filtered?.knowledgeEntryRefs, []);
  assert.equal(filtered?.presentationKind, "legacy");

  const incomplete = result();
  const incompleteIssue = incomplete.deterministic.diagnosis?.issues[0];
  assert.ok(incompleteIssue);
  incompleteIssue.knowledge_registry_version = "2026-07-29.v4";
  incompleteIssue.knowledge_entry_refs = ["knowledge:static.flicking-terminal-control@2"];
  assert.equal(presentAnalysisWorkspace(session({ result: incomplete }))?.issues[0]?.presentationKind, "legacy");
});

test("workspace presents only known Switching identifiers as natural user text", () => {
  const switching = result();
  switching.deterministic.metrics = {
    "target_switching.transition_time_ms": {
      key: "target_switching.transition_time_ms",
      value: 180,
      unit: "ms",
      availability: "available",
      coverage: 1,
      classification: "deterministic",
      metric_version: "target_switching.v1",
      limitations: [],
      provenance: { kind: "derived", sources: ["stats"] },
    },
    "target_switching.transition_distance_px": {
      key: "target_switching.transition_distance_px",
      value: 320,
      unit: "px",
      availability: "available",
      coverage: 1,
      classification: "deterministic",
      metric_version: "target_switching.v1",
      limitations: [],
      provenance: { kind: "derived", sources: ["stats"] },
    },
    "target_switching.path_efficiency": {
      key: "target_switching.path_efficiency",
      value: 0.84,
      unit: "ratio",
      availability: "available",
      coverage: 1,
      classification: "deterministic",
      metric_version: "target_switching.v1",
      limitations: [],
      provenance: { kind: "derived", sources: ["stats"] },
    },
    "target_switching.settle_duration_ms": {
      key: "target_switching.settle_duration_ms",
      value: 95,
      unit: "ms",
      availability: "available",
      coverage: 1,
      classification: "deterministic",
      metric_version: "target_switching.v1",
      limitations: [],
      provenance: { kind: "derived", sources: ["stats"] },
    },
    "unknown.safe_metric": {
      key: "unknown.safe_metric",
      value: 1,
      unit: "count",
      availability: "available",
      coverage: 1,
      classification: "deterministic",
      metric_version: "unknown.v1",
      limitations: [],
      provenance: { kind: "derived", sources: ["stats"] },
    },
  };
  switching.deterministic.diagnosis!.issues = [{
    signal: "switch transition slow",
    severity: "watch",
    priority: 1,
    priority_reason: "target_switching.transition_time_ms",
    metric_refs: ["target_switching.transition_time_ms"],
    event_refs: ["event.switch_chain"],
  }, {
    signal: "switch arrival error high",
    severity: "watch",
    priority: 2,
    priority_reason: "unknown.safe_reason",
    metric_refs: ["target_switching.settle_duration_ms"],
    event_refs: ["event.settle"],
  }];

  const presentation = presentAnalysisWorkspace(session({ result: switching }));
  assert.deepEqual(
    presentation?.metrics.formal.map((metric) => [metric.key, metric.referenceKey]),
    [
      ["切换耗时", "target_switching.transition_time_ms"],
      ["切换距离", "target_switching.transition_distance_px"],
      ["路径效率", "target_switching.path_efficiency"],
      ["稳定耗时", "target_switching.settle_duration_ms"],
      ["指标名称暂不可展示", "unknown.safe_metric"],
    ],
  );
  assert.equal(presentation?.issues[0]?.signal, "切换耗时高于可比基线");
  assert.equal(presentation?.issues[0]?.priorityReason, "切换耗时");
  assert.deepEqual(presentation?.issues[0]?.metricRefs, ["target_switching.transition_time_ms"]);
  assert.deepEqual(presentation?.issues[0]?.eventRefs, ["event.switch_chain"]);
  assert.equal(presentation?.issues[1]?.signal, "到达后稳定耗时高于可比基线");
  assert.equal(presentation?.issues[1]?.priorityReason, "当前合同未提供可展示的优先级理由");
  assert.deepEqual(presentation?.issues[1]?.metricRefs, ["target_switching.settle_duration_ms"]);
  assert.deepEqual(presentation?.issues[1]?.eventRefs, ["event.settle"]);
  assert.doesNotMatch(presentation?.headline ?? "", /target_switching\./);
});

test("native-only and multimodal visual failure remain honest without erasing native results", () => {
  const native = result({ input_mode: "input_native" });
  assert.equal(presentAnalysisWorkspace(session({ input_mode: "input_native", result: native }))?.input.preview, true);
  assert.equal(presentAnalysisWorkspace(session({ input_mode: "input_native", result: native }))?.video.kind, "native-only");

  const visualUnavailable = session();
  visualUnavailable.history!.visual_replay = {
    kind: "unavailable",
    available: false,
    seekable: false,
    endpoint: null,
    artifact_ref: "analysis:42:video",
    reason: "run_owned_video_unavailable",
  };
  const presentation = presentAnalysisWorkspace(visualUnavailable);
  assert.equal(presentation?.partial, true);
  assert.deepEqual(presentation?.metrics.formal.map((metric) => metric.key), ["运动平滑度（SPARC）"]);
});

test("public presentation drops path, raw trace, secret, and internal stack fields", () => {
  const unsafe = result();
  Object.assign(unsafe, {
    path: "C:\\Users\\private\\video.mp4",
    raw_trace: [{ dx: 4 }],
    token: "provider-secret",
    stack: "internal traceback",
  });
  const serialized = JSON.stringify(presentAnalysisWorkspace(session({ result: unsafe })));
  assert.doesNotMatch(serialized, /C:\\|Users|raw_trace|provider-secret|traceback|video\.mp4/);
});

test("workspace projects partial Session 22 findings as descriptive Chinese observations", () => {
  const partial = result({
    deterministic: {
      ...result().deterministic,
      support_status: "partial",
      limitations: [
        "target_relative_facts_unavailable",
        "alignment_partial",
        "alignment_partial",
        "Exact reviewed scenario hash only; other hashes with the same display name remain unclassified.",
        "Input-native metrics do not establish target-relative error, overshoot, or undershoot.",
        "unknown_internal_limitation",
      ],
      diagnosis: {
        profile: { archetype_id: "two_stage", label: "两段式型", confidence: 1, secondary_tags: [] },
        issues: [
          {
            signal: "decel_frac high",
            severity: "info",
            priority: 1,
            priority_reason: "[experimental] 观察项排序第 1",
            claim_level: "experimental",
            metric_refs: ["decel_frac"],
            root_causes: [
              { level: "symptom", text: "减速段占比过高，在「蹭」" },
              { level: "physical", text: "输入数据能观察到减速段偏长，但不能单独证明是制动释放不果断" },
              { level: "training", text: "减速一次到位的意识" },
            ],
            prescriptions: [{ scenario: "pasu", reason: "练完整的加速→减速，减速果断一次到位" }],
          },
          {
            signal: "reverse_ratio high",
            severity: "info",
            priority: 2,
            priority_reason: "[experimental] 观察项排序第 2",
            claim_level: "experimental",
            metric_refs: ["reverse_ratio"],
            root_causes: [
              { level: "symptom", text: "减速段反复修正" },
              { level: "physical", text: "输入数据能观察到反向修正偏多，但不能单独证明制动方向不稳的身体原因" },
              { level: "training", text: "单次制动 + 流体修正" },
            ],
            prescriptions: [{ scenario: "pasu", reason: "转流体派：减速段即微调，别 readjust" }],
          },
          {
            signal: "submovement two-stage",
            severity: "info",
            priority: 3,
            priority_reason: "[experimental] 观察项排序第 3",
            claim_level: "experimental",
            metric_refs: ["submovement_overlap"],
            root_causes: [
              { level: "symptom", text: "flick→急停→独立 micro" },
              { level: "physical", text: "输入数据能观察到 corrective 与 primary 分离，但不能单独证明其由某种身体原因造成" },
              { level: "training", text: "转流体派（overlapping submovements）" },
            ],
            prescriptions: [{ scenario: "pasu", reason: "转流体派：corrective 与 primary 重叠，减速段即微调" }],
          },
        ],
        summary: {},
        comparison: null,
        meta: {},
      },
      metrics: {
        decel_frac: { key: "decel_frac", value: 0.66, unit: "ratio", availability: "available", coverage: 1, classification: "deterministic", metric_version: "native.v1", limitations: [], provenance: { kind: "derived", sources: ["raw_input"] } },
        reverse_ratio: { key: "reverse_ratio", value: 0.36, unit: "ratio", availability: "available", coverage: 1, classification: "deterministic", metric_version: "native.v1", limitations: [], provenance: { kind: "derived", sources: ["raw_input"] } },
        submovement_overlap: { key: "submovement_overlap", value: 0.01, unit: "ratio", availability: "available", coverage: 1, classification: "deterministic", metric_version: "native.v1", limitations: [], provenance: { kind: "derived", sources: ["raw_input"] } },
        unavailable: { key: "unavailable", value: 0, availability: "unavailable", classification: "deterministic", metric_version: "native.v1", limitations: [], provenance: { kind: "derived", sources: ["raw_input"] } },
      },
    },
  });

  const presentation = presentAnalysisWorkspace(session({ result: partial }));
  assert.equal(presentation?.family.status, "descriptive");
  assert.equal(presentation?.metrics.summaryMode, "descriptive");
  assert.deepEqual(presentation?.metrics.summary.map((metric) => metric.referenceKey), ["decel_frac", "reverse_ratio", "submovement_overlap"]);
  assert.deepEqual(presentation?.metrics.formal, []);
  assert.equal(presentation?.profile?.description, "主要移动和后续修正看起来分为两段。");
  assert.deepEqual(presentation?.issues.map((issue) => issue.signal), ["减速阶段偏长", "反向修正偏多", "主要移动与后续修正较分离"]);
  assert.deepEqual(presentation?.issues.map((issue) => issue.priorityReason), [
    null,
    null,
    null,
  ]);
  assert.deepEqual(presentation?.issues.map((issue) => issue.claimLabel), ["探索性观察", "探索性观察", "探索性观察"]);
  assert.deepEqual(presentation?.issues[0]?.metricRefs, ["decel_frac"]);
  assert.deepEqual(presentation?.issues[0]?.rootCauses.map((cause) => cause.text), [
    "速度达到峰值后，减速阶段持续得较久。",
    "证据只能说明减速阶段偏长。",
    "减速尽量一次完成。",
  ]);
  assert.equal(presentation?.issues[0]?.prescriptions[0]?.reason, "练习完整的加速和减速，减速尽量一次完成。");
  assert.deepEqual(presentation?.issues[1]?.rootCauses.map((cause) => cause.text), [
    "减速阶段出现较多反向修正。",
    "证据只能说明反向修正偏多。",
    "单次制动后做连续微调。",
  ]);
  assert.equal(presentation?.issues[1]?.prescriptions[0]?.reason, "在减速阶段微调，减少来回修正。");
  assert.deepEqual(presentation?.issues[2]?.rootCauses.map((cause) => cause.text), [
    "主要移动后出现一次相对独立的微调。",
    "证据只能说明主要移动和后续修正较分离。",
    "主要移动和后续微调重叠衔接。",
  ]);
  assert.equal(presentation?.issues[2]?.prescriptions[0]?.reason, "让修正与主动作更连贯地衔接，在减速阶段微调。");
  assert.deepEqual(presentation?.limitations, [
    "缺少目标位置证据，不能判断过冲、欠冲或目标误差。",
    "输入与事件为部分对齐；指标可描述本局，但不应用通用好坏阈值。",
    "仅适用于已审核的精确场景；同名其他场景不在此分类中。",
    "当前合同未提供可展示的限制说明",
  ]);
  assert.doesNotMatch(JSON.stringify(presentation), /\[experimental\]|decel_frac high|reverse_ratio high|submovement two-stage|unknown_internal_limitation|Input-native metrics|不能据此判断|这只描述动作模式|不代表好坏/);
});

test("diagnosis summary follows explicit keys and remains empty without formal family support", () => {
  const supported = result();
  supported.deterministic.diagnosis!.summary = { sparc: { value: -4.21, classification: "deterministic" } };
  const formal = presentAnalysisWorkspace(session({ result: supported }));
  assert.equal(formal?.metrics.summaryMode, "formal");
  assert.deepEqual(formal?.metrics.summary.map((metric) => metric.referenceKey), ["sparc"]);

  const outcome = result({ deterministic: { ...result().deterministic, support_status: "outcome_only" } });
  const outcomePresentation = presentAnalysisWorkspace(session({ result: outcome }));
  assert.equal(outcomePresentation?.metrics.summaryMode, "empty");
  assert.deepEqual(outcomePresentation?.metrics.summary, []);
});
