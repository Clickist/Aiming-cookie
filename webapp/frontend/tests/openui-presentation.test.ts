import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { createRequire } from "node:module";
import path from "node:path";
import { test } from "node:test";

import { createStreamingParser } from "@openuidev/react-lang";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

import { CoachPresentationText } from "../components/task7/CoachPresentationText";
import { parseRichText } from "../lib/rich-text";
import { coachOpenUILibrary, OPENUI_BLOCKLIST } from "../lib/openui/library";
import {
  COACH_OPENUI_LIBRARY_ID,
  COACH_OPENUI_LIBRARY_VERSION,
  MAX_OPENUI_CHARTS,
  MAX_OPENUI_SEGMENT_BYTES,
  asCoachPresentation,
  canUseOpenUIActions,
  countOpenUICharts,
  createCoachUiAction,
  deriveOpenUIRenderState,
  generationStatusFromMessage,
  generationStatusFromRunStatus,
  isStalePresentation,
  looksLikeOpenUIDSL,
  mergeCoachPartialSnapshot,
  openUISegmentBudgetIssue,
  presentationFromText,
  splitOpenUISegments,
  toAssistantMessageFromEvent,
  type CoachPresentationCause,
} from "../lib/openui/presentation";

const frontendRoot = new URL("../", import.meta.url);
const fixturePath = new URL(
  "../../coach-runtime/test/fixtures/presentation/split-fixtures.json",
  import.meta.url,
);

type Fixture = {
  name: string;
  input: string;
  generation_status: "streaming" | "completed" | "stopped" | "interrupted" | "failed";
  expected_segments:
    | { kind: "text"; text: string }[]
    | Array<
        { kind: "text"; text: string } | { kind: "openui"; source: string }
      >;
};

async function readFixtures(): Promise<Fixture[]> {
  return JSON.parse(await readFile(fixturePath, "utf8")).fixtures as Fixture[];
}

async function source(file: string): Promise<string> {
  return readFile(new URL(file, frontendRoot), "utf8");
}

function draftShape(drafts: ReturnType<typeof splitOpenUISegments>) {
  return drafts.map((draft) =>
    draft.kind === "text"
      ? { kind: "text" as const, text: draft.text }
      : { kind: "openui" as const, source: draft.source },
  );
}

test("分流器对全部 10 个冻结 fixture 逐字产出 expected_segments", async () => {
  const fixtures = await readFixtures();
  assert.equal(fixtures.length, 10);
  for (const fixture of fixtures) {
    assert.deepEqual(
      draftShape(splitOpenUISegments(fixture.input)),
      fixture.expected_segments,
      `${fixture.name} 切分与冻结 fixture 不一致`,
    );
  }
});

test("text 段进入受限 rich-text 解析，openui 段进入同一套 Renderer library", async () => {
  const fixtures = await readFixtures();
  const schema = coachOpenUILibrary.toJSONSchema();
  for (const fixture of fixtures) {
    const presentation = presentationFromText(fixture.input, {
      messageId: fixture.name,
      generationStatus: fixture.generation_status,
    });
    assert.equal(presentation.segments.length, fixture.expected_segments.length);
    for (const [index, expected] of fixture.expected_segments.entries()) {
      const segment = presentation.segments[index]!;
      assert.equal(segment.kind, expected.kind);
      if (segment.kind === "text" && expected.kind === "text") {
        assert.equal(segment.text, expected.text);
        const nodes = parseRichText(segment.text);
        if (segment.text.trim()) {
          assert.ok(nodes.length > 0, `${fixture.name} 的 text 段应产生 rich-text 节点`);
        }
        continue;
      }
      if (segment.kind === "openui" && expected.kind === "openui") {
        assert.equal(segment.source, expected.source);
        assert.equal(segment.library_id, COACH_OPENUI_LIBRARY_ID);
        assert.equal(segment.library_version, COACH_OPENUI_LIBRARY_VERSION);
        // 与 Renderer 使用同一 library schema：合法区段必须解析出 root。
        const parsed = createStreamingParser(schema, coachOpenUILibrary.root).set(
          segment.source,
          fixture.generation_status !== "streaming",
        );
        assert.ok(parsed?.root, `${fixture.name} 的 openui 段未解析出 root`);
        if (fixture.generation_status === "completed") {
          assert.deepEqual(parsed?.meta.errors ?? [], [], `${fixture.name} 不应有解析错误`);
        }
      }
    }
  }
});

test("SSR 冒烟：合法 openui 段渲染为真实 DOM，text 段保留且不展示裸 DSL", async () => {
  const fixtures = await readFixtures();
  const render = (fixture: Fixture): string =>
    renderToStaticMarkup(
      createElement(CoachPresentationText, {
        presentation: presentationFromText(fixture.input, {
          messageId: fixture.name,
          generationStatus: fixture.generation_status,
        }),
      }),
    );

  const prose = render(fixtures.find((fixture) => fixture.name === "prose-plus-one-chart")!);
  assert.match(prose, /coach-openui-root/);
  assert.match(prose, /openui-card/);
  assert.match(prose, /openui-bar-chart/);
  assert.match(prose, /这局过早点火占了失误的近一半/);
  assert.match(prose, /所以下一步只练一件事/);
  assert.doesNotMatch(prose, /root = Card/);
  assert.doesNotMatch(prose, /```openui-lang/);

  const plain = render(fixtures.find((fixture) => fixture.name === "plain-text-only")!);
  assert.doesNotMatch(plain, /coach-openui-root/);
  assert.match(plain, /回看/);

  const streaming = render(
    fixtures.find((fixture) => fixture.name === "streaming-unclosed-fence")!,
  );
  assert.match(streaming, /coach-openui-root/);
  assert.doesNotMatch(streaming, /root = Card/);
  assert.doesNotMatch(streaming, /```openui-lang/);
});

test("SSR 冒烟：DSL 字符串里的恶意 HTML 按文本转义，不生成 script/img/iframe", async () => {
  const fixtures = await readFixtures();
  const hostile = fixtures.find((fixture) => fixture.name === "dsl-with-hostile-chars")!;
  const html = renderToStaticMarkup(
    createElement(CoachPresentationText, {
      presentation: presentationFromText(hostile.input, { messageId: hostile.name }),
    }),
  );
  assert.match(html, /口径提醒/);
  assert.match(html, /&lt;script&gt;alert\(1\)&lt;\/script&gt;/);
  assert.doesNotMatch(html, /<script[\s>]/i);
  assert.doesNotMatch(html, /<iframe[\s>]/i);
  assert.doesNotMatch(html, /<img[\s>]/i);
  assert.doesNotMatch(html, /root = Card/);
});

test("SSR 失败呈现：预算/不支持 library 显示 cause 文案且不展示 DSL", () => {
  const longSource = `root = Stack([])\n${"x".repeat(MAX_OPENUI_SEGMENT_BYTES + 1)}`;
  const budget = renderToStaticMarkup(
    createElement(CoachPresentationText, {
      presentation: {
        schema_version: "coach_presentation.v1",
        message_id: "budget",
        run_id: null,
        revision: 1,
        generation_status: "completed",
        segments: [
          {
            id: "seg:0",
            kind: "openui",
            library_id: COACH_OPENUI_LIBRARY_ID,
            library_version: COACH_OPENUI_LIBRARY_VERSION,
            source: longSource,
          },
        ],
      },
    }),
  );
  assert.match(budget, /图表内容超出展示预算/);
  assert.doesNotMatch(budget, /root = Stack/);
  assert.doesNotMatch(budget, /x{100}/);

  const unsupported = renderToStaticMarkup(
    createElement(CoachPresentationText, {
      presentation: {
        schema_version: "coach_presentation.v1",
        message_id: "unsupported",
        run_id: null,
        revision: 1,
        generation_status: "completed",
        segments: [
          {
            id: "seg:0",
            kind: "openui",
            library_id: "other-library",
            library_version: "9.9.9",
            source: 'root = Card([t])\nt = TextContent("hi")',
          },
        ],
      },
    }),
  );
  assert.match(unsupported, /图表版本不受支持/);
  assert.doesNotMatch(unsupported, /root = Card/);
});

test("sidecar 预算降级成 text 时也不展示裸 DSL", () => {
  assert.equal(looksLikeOpenUIDSL('root = Stack([])\nt = TextContent("hi")'), true);
  assert.equal(looksLikeOpenUIDSL('Stack([TextContent("hi")])'), true);
  assert.equal(looksLikeOpenUIDSL("这局命中率不错，root = Stack([]) 只是举例"), false);
  const presentation = {
    schema_version: "coach_presentation.v1" as const,
    message_id: "budget-text",
    run_id: null,
    revision: 1,
    generation_status: "completed" as const,
    cause: "budget_exceeded" as const,
    segments: [
      {
        id: "seg:0",
        kind: "text" as const,
        text: 'root = Stack([])\nt = TextContent("hi")',
      },
    ],
  };
  const html = renderToStaticMarkup(
    createElement(CoachPresentationText, { presentation }),
  );
  assert.match(html, /图表内容超出展示预算/);
  assert.doesNotMatch(html, /root = Stack/);
  assert.doesNotMatch(html, /TextContent/);

  const leaked = renderToStaticMarkup(
    createElement(CoachPresentationText, {
      presentation: { ...presentation, cause: undefined },
    }),
  );
  assert.match(leaked, /图表无法显示/);
  assert.doesNotMatch(leaked, /root = Stack/);
});

test("单回复第 2 个 openui 区段按预算拒收（不静默展示）", () => {
  const segment = (id: string) => ({
    id,
    kind: "openui" as const,
    library_id: COACH_OPENUI_LIBRARY_ID,
    library_version: COACH_OPENUI_LIBRARY_VERSION,
    source: 'root = Card([t])\nt = TextContent("hi")',
  });
  const html = renderToStaticMarkup(
    createElement(CoachPresentationText, {
      presentation: {
        schema_version: "coach_presentation.v1",
        message_id: "two-openui",
        run_id: null,
        revision: 1,
        generation_status: "completed",
        segments: [segment("seg:0"), segment("seg:1")],
      },
    }),
  );
  assert.equal(html.match(/openui-card-card/g)?.length, 1);
  assert.match(html, /图表内容超出展示预算/);
});

test("SSR 冒烟：流式状态下 @ToAssistant 按钮渲染为 disabled", async () => {
  const source = [
    "root = Card([btn])",
    'btn = Button("继续看", @ToAssistant("继续看"))',
  ].join("\n");
  const html = renderToStaticMarkup(
    createElement(CoachPresentationText, {
      presentation: {
        schema_version: "coach_presentation.v1",
        message_id: "streaming-button",
        run_id: "run:test",
        revision: 1,
        generation_status: "streaming",
        segments: [
          {
            id: "seg:0",
            kind: "openui",
            library_id: COACH_OPENUI_LIBRARY_ID,
            library_version: COACH_OPENUI_LIBRARY_VERSION,
            source,
          },
        ],
      },
    }),
  );
  assert.match(html, /继续看/);
  assert.match(html, /<button[^>]*disabled/);
});

test("流式中途的半截 openui 围栏暂扣，不把 fence 记号泄进 text", () => {
  assert.deepEqual(draftShape(splitOpenUISegments("看这组\n```openui-la")), [
    { kind: "text", text: "看这组\n" },
  ]);
  assert.deepEqual(draftShape(splitOpenUISegments("看这组\n```openui-lang")), [
    { kind: "text", text: "看这组\n" },
  ]);
  assert.deepEqual(draftShape(splitOpenUISegments("看这组\n```")), [
    { kind: "text", text: "看这组\n" },
  ]);
});

test("plain text 逐字透传且只产生 paragraph 节点（不动空格/标点）", async () => {
  const fixtures = await readFixtures();
  const plain = fixtures.find((fixture) => fixture.name === "plain-text-only")!;
  const drafts = splitOpenUISegments(plain.input);
  assert.equal(drafts.length, 1);
  const nodes = parseRichText(plain.input);
  assert.ok(nodes.length > 0);
  assert.ok(nodes.every((node) => node.kind === "paragraph"));
  const joined = nodes
    .map((node) =>
      node.kind === "paragraph" ? node.segments.map((segment) => segment.text).join("") : "",
    )
    .join("\n\n");
  assert.equal(joined, plain.input);
});

test("组件黑名单制：仅 MarkDownRenderer 禁入，基底库全量放行", () => {
  for (const banned of OPENUI_BLOCKLIST) {
    assert.equal(
      coachOpenUILibrary.components[banned],
      undefined,
      `${banned} 不得进入 coach library（任意 HTML/脚本红线）`,
    );
  }
  // 抽查关键族全部在场：图表全家、表格、表单输入族、图片、动作、布局
  for (const required of [
    "BarChart",
    "LineChart",
    "PieChart",
    "ScatterChart",
    "ScatterSeries",
    "Point",
    "AreaChart",
    "RadarChart",
    "Table",
    "Image",
    "ImageGallery",
    "Form",
    "Input",
    "DatePicker",
    "Tabs",
    "Accordion",
    "Steps",
    "Modal",
    "Card",
    "Stack",
    "Series",
    "Button",
    "IconButton",
  ]) {
    assert.ok(coachOpenUILibrary.components[required], `${required} 应在全量库中`);
  }
  // 动作红线保持：Query/Mutation 不是组件（语句层由 hasForbiddenCalls 拒收）
  assert.equal(coachOpenUILibrary.root, "Stack");
});

test("未知组件/无 root/组件抛错/超长 DSL/不支持的 library 各自落到独立 cause", () => {
  const base = {
    generationStatus: "completed" as const,
    libraryId: COACH_OPENUI_LIBRARY_ID,
    libraryVersion: COACH_OPENUI_LIBRARY_VERSION,
    source: "root = Card([chart])",
    hasParsed: true,
  };
  const cases: Array<{
    name: string;
    input: Parameters<typeof deriveOpenUIRenderState>[0];
    state: string;
    cause?: CoachPresentationCause;
  }> = [
    {
      name: "unknown component（root 仍在，保留有效内容）",
      input: { ...base, rootPresent: true, parseErrors: [{ code: "unknown-component" }] },
      state: "degraded",
      cause: "unknown_component",
    },
    {
      name: "unknown component（root 被丢弃）",
      input: { ...base, rootPresent: false, parseErrors: [{ code: "unknown-component" }] },
      state: "failed",
      cause: "unknown_component",
    },
    {
      name: "无 root",
      input: { ...base, rootPresent: false, parseErrors: [{ code: "no-root" }] },
      state: "failed",
      cause: "parse_error",
    },
    {
      name: "解析失败",
      input: { ...base, rootPresent: false, parseErrors: [{ code: "parse-failed" }] },
      state: "failed",
      cause: "parse_error",
    },
    {
      name: "组件渲染抛错（部分内容保留）",
      input: { ...base, rootPresent: true, renderErrors: [{ code: "render-error" }] },
      state: "degraded",
      cause: "render_error",
    },
    {
      name: "超长 DSL",
      input: { ...base, source: "x".repeat(MAX_OPENUI_SEGMENT_BYTES + 1), rootPresent: true },
      state: "failed",
      cause: "budget_exceeded",
    },
    {
      name: "图表数量超预算",
      input: {
        ...base,
        source: "BarChart(a,[s])\nBarChart(b,[s])\nLineChart(c,[s])",
        rootPresent: true,
      },
      state: "failed",
      cause: "budget_exceeded",
    },
    {
      name: "不支持的 library",
      input: { ...base, libraryId: "other", rootPresent: true },
      state: "failed",
      cause: "unsupported_library",
    },
    {
      name: "Query/Mutation 不在第一刀能力内，出现即拒收",
      input: { ...base, rootPresent: true, hasForbiddenCalls: true },
      state: "failed",
      cause: "parse_error",
    },
    {
      name: "流式中途的暂时错误不呈现失败",
      input: { ...base, generationStatus: "streaming", rootPresent: false, parseErrors: [{ code: "missing-required" }] },
      state: "rendering",
    },
    {
      name: "首帧解析未到不误报失败",
      input: { ...base, hasParsed: false, rootPresent: false },
      state: "rendering",
    },
    {
      name: "用户停止且保留半截内容",
      input: { ...base, generationStatus: "stopped", rootPresent: true },
      state: "degraded",
      cause: "stopped",
    },
    {
      name: "用户停止且无可用内容",
      input: { ...base, generationStatus: "stopped", rootPresent: false },
      state: "failed",
      cause: "stopped",
    },
    {
      name: "应用重启打断",
      input: { ...base, generationStatus: "interrupted", rootPresent: true },
      state: "degraded",
      cause: "process_interrupted",
    },
    {
      name: "生成失败且无可用内容",
      input: { ...base, generationStatus: "failed", rootPresent: false },
      state: "failed",
    },
    {
      name: "完全合法",
      input: { ...base, rootPresent: true },
      state: "ready",
    },
  ];

  for (const item of cases) {
    const derived = deriveOpenUIRenderState(item.input);
    assert.equal(derived.state, item.state, item.name);
    assert.equal(derived.cause, item.cause, item.name);
  }
});

test("失败呈现组件存在 per-block error boundary，失败分支不渲染 DSL", async () => {
  const component = await source("components/task7/CoachPresentationText.tsx");
  assert.match(component, /class OpenUISegmentBoundary extends Component/);
  assert.match(component, /static getDerivedStateFromError/);
  assert.match(component, /componentDidCatch/);
  assert.match(component, /<OpenUISegmentBoundary/);
  assert.match(component, /resetKey=\{segment\.source\}/);
  // 失败分支必须在 <Renderer> 之前返回，且只呈现 notice 文案。
  assert.match(
    component,
    /if \(state === "failed"\) \{\s*return <div className="coach-openui-root">\{failureNotice\}<\/div>;\s*\}/,
  );
  assert.match(component, /data-cause=/);
  assert.doesNotMatch(component, /dangerouslySetInnerHTML/);
});

test("Query/Mutation 是解析器内建调用，展示层必须显式拒收而不是静默执行", () => {
  const schema = coachOpenUILibrary.toJSONSchema();
  const query = createStreamingParser(schema, coachOpenUILibrary.root).set(
    'root = Stack([q])\nq = Query("evil", {}, {rows: []})',
    true,
  );
  const mutation = createStreamingParser(schema, coachOpenUILibrary.root).set(
    'root = Stack([m])\nm = Mutation("evil", {})',
    true,
  );
  assert.ok((query?.queryStatements.length ?? 0) > 0);
  assert.ok((mutation?.mutationStatements.length ?? 0) > 0);
  const derived = deriveOpenUIRenderState({
    generationStatus: "completed",
    libraryId: COACH_OPENUI_LIBRARY_ID,
    libraryVersion: COACH_OPENUI_LIBRARY_VERSION,
    source: 'root = Stack([q])\nq = Query("evil", {}, {rows: []})',
    hasParsed: true,
    rootPresent: true,
    hasForbiddenCalls: (query?.queryStatements.length ?? 0) > 0,
  });
  assert.equal(derived.state, "failed");
  assert.equal(derived.cause, "parse_error");
});

test("预算与图表计数按合同阈值执行", () => {
  assert.equal(openUISegmentBudgetIssue("x".repeat(MAX_OPENUI_SEGMENT_BYTES)), null);
  assert.equal(
    openUISegmentBudgetIssue("x".repeat(MAX_OPENUI_SEGMENT_BYTES + 1)),
    "budget_exceeded",
  );
  assert.equal(
    countOpenUICharts("BarChart(a, [s])\nLineChart(b, [s])"),
    2,
  );
  assert.equal(MAX_OPENUI_CHARTS, 2);
  assert.equal(
    openUISegmentBudgetIssue("BarChart(a,[s])\nBarChart(b,[s])\nLineChart(c,[s])"),
    "budget_exceeded",
  );
});

test("@ToAssistant 构造 coach_ui_action.v1，其它动作一律拒绝", () => {
  const action = createCoachUiAction({
    messageId: 42,
    segmentId: "seg:1",
    message: "继续看这一组",
  });
  assert.equal(action.schema_version, "coach_ui_action.v1");
  assert.equal(action.kind, "to_assistant");
  assert.equal(action.message_id, 42);
  assert.equal(action.segment_id, "seg:1");
  assert.equal(action.message, "继续看这一组");
  assert.ok(action.interaction_id.length > 0);
  assert.notEqual(
    createCoachUiAction({ messageId: 42, segmentId: "seg:1", message: "x" }).interaction_id,
    action.interaction_id,
  );

  assert.equal(
    toAssistantMessageFromEvent({
      type: "continue_conversation",
      humanFriendlyMessage: "  继续  ",
    }),
    "继续",
  );
  assert.equal(
    toAssistantMessageFromEvent({ type: "open_url", humanFriendlyMessage: "打开" }),
    null,
  );
  assert.equal(
    toAssistantMessageFromEvent({ type: "custom_action", humanFriendlyMessage: "执行" }),
    null,
  );
  assert.equal(
    toAssistantMessageFromEvent({ type: "continue_conversation", humanFriendlyMessage: "   " }),
    null,
  );
});

test("动作权限：只有 completed + ready 可用；流式/降级/中断/失败全部禁用", () => {
  assert.equal(canUseOpenUIActions("completed", "ready"), true);
  assert.equal(canUseOpenUIActions("completed", "degraded"), false);
  assert.equal(canUseOpenUIActions("completed", "failed"), false);
  assert.equal(canUseOpenUIActions("streaming", "rendering"), false);
  assert.equal(canUseOpenUIActions("stopped", "degraded"), false);
  assert.equal(canUseOpenUIActions("interrupted", "degraded"), false);
  assert.equal(canUseOpenUIActions("failed", "failed"), false);
});

test("Button 只在用户点击时触发；流式/降级禁用；历史重放不自动执行", async () => {
  const library = await source("lib/openui/library.tsx");
  assert.match(library, /const disabled = isStreaming \|\| !actionsEnabled \|\| !actionAllowed \|\| !isCoachUiActionMessage\(actionMessage\);/);
  assert.match(library, /plan\.steps\[0\]\?\.type === ACTION_STEPS\.ToAssistant/);
  assert.match(library, /disabled=\{disabled\}/);
  assert.match(library, /onClick=\{\(\) => \{/);
  assert.match(library, /if \(disabled\) return;/);
  assert.match(library, /triggerAction\(label, undefined, props\.action as ActionPlan \| undefined\)/);
  assert.match(library, /CoachOpenUIActionsEnabledContext/);
  assert.match(library, /createContext\(false\)/);
  assert.match(library, /steps\.ToAssistant\.ref\.optional\(\)/);

  const component = await source("components/task7/CoachPresentationText.tsx");
  assert.match(component, /onAction=\{handleAction\}/);
  assert.match(component, /isStreaming=\{generationStatus === "streaming"\}/);
  // 动作只能由 click 事件进入 onAction；不得在 effect/挂载期自动调用。
  const effectBodies = [...component.matchAll(/useEffect\(\(\) => \{([\s\S]*?)\}, \[/g)].map(
    (match) => match[1]!,
  );
  assert.ok(effectBodies.length > 0);
  assert.ok(effectBodies.every((body) => !body.includes("onToAssistant")));
});

test("sidecar 投影运行时校验：合法快照接收，旧版/畸形快照回退", () => {
  const valid = presentationFromText("hi", { messageId: 1, generationStatus: "streaming" });
  assert.deepEqual(asCoachPresentation(valid), valid);
  assert.equal(asCoachPresentation(null), null);
  assert.equal(asCoachPresentation({}), null);
  assert.equal(asCoachPresentation({ ...valid, schema_version: "coach_presentation.v0" }), null);
  assert.equal(asCoachPresentation({ ...valid, revision: "1" }), null);
  assert.equal(asCoachPresentation({ ...valid, segments: [{ id: "s", kind: "image" }] }), null);
  assert.equal(
    asCoachPresentation({
      ...valid,
      segments: [{ id: "s", kind: "openui", source: "root = Stack([])" }],
    }),
    null,
  );
  assert.deepEqual(
    asCoachPresentation({
      ...valid,
      segments: [
        {
          id: "s",
          kind: "openui",
          library_id: COACH_OPENUI_LIBRARY_ID,
          library_version: COACH_OPENUI_LIBRARY_VERSION,
          source: "root = Stack([])",
        },
      ],
    })?.segments,
    [
      {
        id: "s",
        kind: "openui",
        library_id: COACH_OPENUI_LIBRARY_ID,
        library_version: COACH_OPENUI_LIBRARY_VERSION,
        source: "root = Stack([])",
      },
    ],
  );
});

test("SSE partial 接收图表快照，拒绝旧 revision 与其它 run 帧", () => {
  const current = { run_ref: "coach-run:1", partial_text: null };
  const projection = presentationFromText("```openui-lang\nroot = Stack([])\n```", {
    runId: current.run_ref, revision: 3, generationStatus: "streaming",
  });
  const next = mergeCoachPartialSnapshot(current, { text: "", presentation: projection });
  assert.equal(next.partial_text, "");
  assert.equal(next.partial_presentation, projection);
  assert.equal(projection.segments[0]?.kind, "openui");
  assert.equal(mergeCoachPartialSnapshot(next, {
    text: "stale", presentation: { ...projection, revision: 2 },
  }), next);
  assert.equal(mergeCoachPartialSnapshot(next, {
    text: "wrong", presentation: { ...projection, run_id: "coach-run:2" },
  }), next);
  assert.deepEqual(mergeCoachPartialSnapshot(next, { text: "legacy", presentation: {} }), {
    ...next, partial_text: "legacy",
  });
});

test("revision 单调：旧快照不覆盖新状态", () => {
  const newer = presentationFromText("text", { messageId: 1, revision: 3 });
  const older = presentationFromText("old", { messageId: 1, revision: 2 });
  assert.equal(isStalePresentation(newer, older), true);
  assert.equal(isStalePresentation(older, newer), false);
  const otherMessage = presentationFromText("old", { messageId: 2, revision: 1 });
  assert.equal(isStalePresentation(newer, otherMessage), false);
});

test("生成状态映射：run 状态与历史消息标志分开解释", () => {
  assert.equal(generationStatusFromRunStatus("queued"), "streaming");
  assert.equal(generationStatusFromRunStatus("running"), "streaming");
  assert.equal(generationStatusFromRunStatus("succeeded"), "completed");
  assert.equal(generationStatusFromRunStatus("stopped"), "stopped");
  assert.equal(generationStatusFromRunStatus("failed"), "failed");
  assert.equal(generationStatusFromMessage({}), "completed");
  assert.equal(generationStatusFromMessage({ stopped: true }), "stopped");
  assert.equal(generationStatusFromMessage({ interrupted: true, stopped: true }), "interrupted");
});

test("依赖树单实例：react 与 @openuidev/react-lang 各只有一份", async () => {
  const nodeRequire = createRequire(import.meta.url);
  const uiEntry = nodeRequire.resolve("@openuidev/react-ui");
  const uiDir = path.dirname(uiEntry);
  const appReact = nodeRequire.resolve("react");
  const appReactLang = nodeRequire.resolve("@openuidev/react-lang");
  assert.equal(
    nodeRequire.resolve("react", { paths: [uiDir] }),
    appReact,
    "react-ui 必须解析到与宿主相同的 react 实例",
  );
  assert.equal(
    nodeRequire.resolve("@openuidev/react-lang", { paths: [uiDir] }),
    appReactLang,
    "react-ui 必须解析到与宿主相同的 react-lang 实例",
  );
  assert.equal(
    (nodeRequire("react/package.json") as { version: string }).version,
    "19.2.7",
  );
  const reactLangPackage = JSON.parse(
    await readFile(path.resolve(path.dirname(appReactLang), "..", "package.json"), "utf8"),
  ) as { version: string };
  assert.equal(reactLangPackage.version, "1.0.2");
});

test("library 身份与预算常量与 sidecar 冻结值一致（单一来源守卫）", async () => {
  const sidecarTypes = await readFile(
    new URL("../../coach-runtime/src/presentation/types.ts", import.meta.url),
    "utf8",
  );
  assert.match(sidecarTypes, /COACH_OPENUI_LIBRARY_ID = "coach-openui"/);
  assert.match(sidecarTypes, /COACH_OPENUI_LIBRARY_VERSION = "1\.1\.0"/);
  assert.match(sidecarTypes, /MAX_OPENUI_SEGMENT_BYTES = 64 \* 1024/);
  assert.match(sidecarTypes, /OPENUI_FENCE_LANG = "openui-lang"/);
  assert.equal(COACH_OPENUI_LIBRARY_ID, "coach-openui");
  assert.equal(COACH_OPENUI_LIBRARY_VERSION, "1.1.0");
});

test("主题映射覆盖关键 --openui-* token，并接入 layered CSS", async () => {
  const theme = await source("lib/openui/openui-theme.css");
  for (const token of [
    "--openui-background",
    "--openui-foreground",
    "--openui-text-neutral-primary",
    "--openui-text-neutral-secondary",
    "--openui-text-neutral-tertiary",
    "--openui-border-default",
    "--openui-interactive-accent-default",
  ]) {
    assert.match(theme, new RegExp(`${token}:`));
  }
  assert.match(theme, /var\(--on-surface/);
  assert.match(theme, /var\(--background/);
  const layout = await source("app/layout.tsx");
  assert.match(layout, /@openuidev\/react-ui\/layered\/styles\/index\.css/);
  assert.match(layout, /lib\/openui\/openui-theme\.css/);
});

test("安全适配：TextContent/Callout 不经过 react-ui MarkDownRenderer，无 HTML 注入", async () => {
  const library = await source("lib/openui/library.tsx");
  assert.doesNotMatch(library, /react-ui\/genui-lib\/MarkDownRenderer/);
  assert.doesNotMatch(library, /components\/MarkDownRenderer/);
  assert.match(library, /<CoachMessageText text=\{/);
  assert.match(library, /props: baseTextContent\.props/);
  assert.match(library, /props: baseCallout\.props/);
  assert.doesNotMatch(library, /dangerouslySetInnerHTML/);
});
