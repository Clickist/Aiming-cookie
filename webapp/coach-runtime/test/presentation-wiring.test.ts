import assert from "node:assert/strict";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

const dataRoot = mkdtempSync(join(tmpdir(), "coach-presentation-wiring-"));
process.env.DATA_ROOT = dataRoot;

import { createAgentRun, getAgentRun, subscribeAgentRun } from "../src/agent-runs.ts";
import { getCoachSessionDetail, listCoachSessions } from "../src/sidecar-coach-data.ts";
import { saveProfile } from "../src/provider-store.ts";
import type { StreamFn } from "../src/stream-openai-compatible.ts";
import { waitForTask } from "../src/task-manager.ts";
import { splitPresentation, type CoachPresentation } from "../src/presentation/index.ts";
import { streamAssistant } from "./pi-fake-stream.ts";

saveProfile({
  kind: "builtin",
  provider_id: "opencode-go",
  model_id: "deepseek-v4-flash",
  credential: { type: "api_key", key: "presentation-wiring-key" },
});

// 冻结讲解形态：文字 + 一个 openui 区段 + 文字结论。
const REPLY = [
  "这局过早点火占了失误的近一半——看下面的分布：",
  "",
  "```openui-lang",
  "root = Card([chart])",
  "chart = BarChart(labels, [series])",
  "labels = [\"过早点火\", \"追枪过头\"]",
  "series = Series(\"失误次数\", [23, 12])",
  "```",
  "",
  "所以下一步只练一件事：出掩体后停半拍再压。",
].join("\n");

function comparable(presentation: CoachPresentation) {
  return presentation.segments.map((segment) =>
    segment.kind === "openui"
      ? { kind: "openui" as const, source: segment.source }
      : { kind: "text" as const, text: segment.text },
  );
}

test("completed run carries the coach_presentation.v1 terminal snapshot (SSE/polling payload)", async () => {
  const streamFn: StreamFn = async () => streamAssistant([{ type: "text", text: REPLY }], "stop");
  const run = createAgentRun("presentation-owner", "分析一下这局", { sessionId: 9501, streamFn });
  await waitForTask(run.run_ref);

  const final = getAgentRun("presentation-owner", run.run_ref);
  assert.ok(final);
  assert.equal(final.status, "succeeded", JSON.stringify(final.error));
  assert.ok(final.partial_presentation, "completed state must carry partial_presentation");
  assert.equal(final.partial_presentation.generation_status, "completed");
  assert.equal(final.partial_presentation.schema_version, "coach_presentation.v1");
  assert.deepEqual(
    comparable(final.partial_presentation),
    comparable(splitPresentation(REPLY, "completed")),
    "completion projection must equal a fresh projection of the raw reply",
  );
  // 旧字段照旧：openui DSL 不进 partial_text。
  assert.ok(!(final.partial_text ?? "").includes("root = Card"));
  assert.ok((final.partial_text ?? "").includes("所以下一步只练一件事"));
});

test("history replay projects the same segments from the persisted JSONL raw text", async () => {
  const detail = await getCoachSessionDetail("presentation-owner", 9501);
  const assistant = detail.messages.find((message) => message.role === "assistant") as
    | { id: number; content: string; presentation?: CoachPresentation }
    | undefined;
  assert.ok(assistant, "assistant message must be persisted");
  assert.ok(assistant.presentation, "assistant message must carry history presentation");
  assert.equal(assistant.presentation.generation_status, "completed");
  assert.equal(assistant.presentation.message_id, assistant.id);
  assert.deepEqual(
    comparable(assistant.presentation),
    comparable(splitPresentation(assistant.content, "completed")),
    "history projection must be derived from the JSONL raw text with the same splitter",
  );

  // 侧栏预览也不能漏出 DSL。
  const list = await listCoachSessions("presentation-owner");
  const session = list.sessions.find((item) => item.id === 9501);
  assert.ok(session);
  assert.ok(!(session.last_message_preview ?? "").includes("root = Card"));
  assert.ok((session.last_message_preview ?? "").includes("所以下一步只练一件事"));
});

test("live partial stream delivers openui segments while legacy text stays DSL-free", async () => {
  let release: (() => void) | null = null;
  const gate = new Promise<void>((resolve) => { release = resolve; });
  const streamFn: StreamFn = async () => {
    await gate;
    return streamAssistant([{ type: "text", text: REPLY }], "stop");
  };
  const run = createAgentRun("presentation-owner", "分析一下这局", { sessionId: 9502, streamFn });
  const partials: Array<{ text: string; presentation: CoachPresentation | null }> = [];
  const unsubscribe = subscribeAgentRun("presentation-owner", run.run_ref, {
    onPartial: (text, _thinking, presentation) => {
      partials.push({ text, presentation: presentation ?? null });
    },
  });
  assert.ok(unsubscribe);
  release!();
  await waitForTask(run.run_ref);
  unsubscribe();

  const streaming = partials.find((item) => item.presentation?.segments.some((segment) => segment.kind === "openui"));
  assert.ok(streaming, `live partial must carry the openui segment: ${JSON.stringify(partials)}`);
  assert.equal(streaming.presentation?.generation_status, "streaming");
  assert.ok(streaming.presentation?.segments.some((segment) => segment.kind === "openui"));
  for (const partial of partials) {
    assert.ok(!partial.text.includes("root = Card"), "legacy partial text must not leak DSL");
  }
});

test("openui-only reply still streams a projection even though legacy text is empty", async () => {
  const chartOnly = "```openui-lang\nroot = Card([chart])\nchart = BarChart(labels, [series])\n```";
  const streamFn: StreamFn = async () => streamAssistant([{ type: "text", text: chartOnly }], "stop");
  const run = createAgentRun("presentation-owner", "画个图", { sessionId: 9504, streamFn });
  const partials: Array<{ text: string; presentation: CoachPresentation | null }> = [];
  const unsubscribe = subscribeAgentRun("presentation-owner", run.run_ref, {
    onPartial: (text, _thinking, presentation) => {
      partials.push({ text, presentation: presentation ?? null });
    },
  });
  await waitForTask(run.run_ref);
  unsubscribe?.();

  const final = getAgentRun("presentation-owner", run.run_ref);
  assert.ok(final);
  assert.equal(final.status, "succeeded", JSON.stringify(final.error));
  assert.equal(final.partial_presentation?.segments.length, 1);
  assert.equal(final.partial_presentation?.segments[0]?.kind, "openui");
  assert.ok(
    partials.some((item) => item.presentation?.segments.some((segment) => segment.kind === "openui")),
    "a chart-only reply must still deliver streaming UI frames",
  );
});

test("failed turn relabels the last streaming snapshot as failed without inventing text", async () => {
  const partialReply = "这局的核心问题：\n\n```openui-lang\nroot = Card([m1])\nm1 = MetricIndicatorInline(\"23%\"";
  const streamFn: StreamFn = async () => streamAssistant([{ type: "text", text: partialReply }], "error");
  const run = createAgentRun("presentation-owner", "分析这局", { sessionId: 9503, streamFn });
  await waitForTask(run.run_ref);

  const final = getAgentRun("presentation-owner", run.run_ref);
  assert.ok(final);
  assert.equal(final.status, "failed");
  assert.ok(final.partial_presentation, "failed state must keep the partial projection");
  assert.equal(final.partial_presentation.generation_status, "failed");
  assert.ok(
    final.partial_presentation.segments.some((segment) => segment.kind === "openui"),
    "unclosed openui segment must survive into the failed snapshot",
  );
});

// ── coach_ui_action.v1（§5.3 动作权限）───────────────────────────────────

test("parseCoachUiActionV1 accepts well-formed gestures and rejects malformed ones", async () => {
  const { parseCoachUiActionV1 } = await import("../src/presentation/index.ts");
  const valid = {
    schema_version: "coach_ui_action.v1",
    kind: "to_assistant",
    interaction_id: "msg-12:3",
    message_id: "msg-12",
    segment_id: "seg-1",
    message: "逐发分析这 23 发过早点火",
  };
  const parsed = parseCoachUiActionV1(valid);
  assert.ok(parsed);
  assert.equal(parsed.message, "逐发分析这 23 发过早点火");
  assert.equal(parsed.kind, "to_assistant");
  assert.equal(parseCoachUiActionV1({ ...valid, message_id: 42 })?.message_id, 42);
  assert.equal(parseCoachUiActionV1({ ...valid, message_id: null })?.message_id, null);
  for (const messageId of [undefined, "", -1, 1.5, NaN, {}, true]) {
    assert.equal(parseCoachUiActionV1({ ...valid, message_id: messageId }), null);
  }

  assert.equal(parseCoachUiActionV1(null), null);
  assert.equal(parseCoachUiActionV1("x"), null);
  assert.equal(
    parseCoachUiActionV1({ ...valid, schema_version: "coach_ui_action.v2" }),
    null,
    "wrong schema_version must be rejected",
  );
  assert.equal(
    parseCoachUiActionV1({ ...valid, kind: "open_url" }),
    null,
    "non to_assistant kinds are out of the v1 action whitelist",
  );
  assert.equal(parseCoachUiActionV1({ ...valid, message: "   " }), null, "empty message");
  assert.equal(parseCoachUiActionV1({ ...valid, message: "标".repeat(2001) }), null, "over-length message");
  assert.equal(parseCoachUiActionV1({ ...valid, segment_id: "" }), null, "empty segment_id");
});

test("createAgentRun records a coach_ui_action audit event and leaves turn input untouched", async () => {
  const streamFn: StreamFn = async () => streamAssistant([{ type: "text", text: "好，按发拆给你看。" }], "stop");
  const run = createAgentRun("presentation-owner", "逐发分析这 23 发过早点火", {
    sessionId: 9505,
    streamFn,
    uiAction: {
      schema_version: "coach_ui_action.v1",
      kind: "to_assistant",
      interaction_id: "msg-9:1",
      message_id: "msg-9",
      segment_id: "seg-0",
      message: "逐发分析这 23 发过早点火",
    },
  });
  await waitForTask(run.run_ref);

  const final = getAgentRun("presentation-owner", run.run_ref);
  assert.ok(final);
  const audit = final.events.find(
    (event) => event.type === "interaction" && event.code === "coach_ui_action",
  );
  assert.ok(audit, "ui_action gesture must leave a visible audit event");
  assert.equal(audit.phase, "queued");
  assert.equal((audit.payload as Record<string, unknown>).segment_id, "seg-0");
  // 回合输入语义不受影响：模型收到的就是按钮 message 文本本身。
  assert.equal(final.partial_text, "好，按发拆给你看。");

  const plain = createAgentRun("presentation-owner", "普通输入", { sessionId: 9506, streamFn });
  await waitForTask(plain.run_ref);
  const plainFinal = getAgentRun("presentation-owner", plain.run_ref);
  assert.ok(plainFinal);
  assert.ok(
    !plainFinal.events.some((event) => event.code === "coach_ui_action"),
    "plain input must not carry a ui_action audit event",
  );
});
