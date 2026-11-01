import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import {
  COACH_OPENUI_LIBRARY_ID,
  COACH_OPENUI_LIBRARY_VERSION,
  COACH_PRESENTATION_SCHEMA,
  PresentationSplitter,
  applyPresentationTransportBudget,
  splitPresentation,
  type CoachGenerationStatus,
  type CoachPresentation,
} from "../src/presentation/index.ts";

type ExpectedSegment =
  | { kind: "text"; text: string }
  | { kind: "openui"; source: string };

type Fixture = {
  name: string;
  description: string;
  generation_status: CoachGenerationStatus;
  input: string;
  expected_segments: ExpectedSegment[];
};

const fixturesPath = join(
  fileURLToPath(new URL(".", import.meta.url)),
  "fixtures",
  "presentation",
  "split-fixtures.json",
);
const fixtures = (JSON.parse(readFileSync(fixturesPath, "utf8")) as { fixtures: Fixture[] }).fixtures;

function comparable(presentation: CoachPresentation): ExpectedSegment[] {
  return presentation.segments.map((segment) =>
    segment.kind === "openui"
      ? { kind: "openui" as const, source: segment.source }
      : { kind: "text" as const, text: segment.text },
  );
}

function textOf(presentation: CoachPresentation): string {
  return presentation.segments
    .filter((segment): segment is Extract<typeof segment, { kind: "text" }> => segment.kind === "text")
    .map((segment) => segment.text)
    .join("");
}

// DSL 专用记号：任何时刻都不允许出现在 text 段（开围栏未判定时不得泄 DSL）。
const DSL_ONLY_TOKENS = [
  "root =",
  "Card(",
  "CardHeader(",
  "BarChart(",
  "LineChart(",
  "Series(",
  "MetricIndicatorInline(",
  "Callout(",
];

function assertNoDslLeak(presentation: CoachPresentation, label: string): void {
  const text = textOf(presentation);
  for (const token of DSL_ONLY_TOKENS) {
    assert.ok(!text.includes(token), `${label}: text segments leaked DSL token ${JSON.stringify(token)}: ${JSON.stringify(text)}`);
  }
}

test("frozen fixtures: every sample projects byte-for-byte (text text and openui source)", () => {
  assert.equal(fixtures.length, 9, "the frozen fixture file must keep all 9 samples");
  for (const fixture of fixtures) {
    const presentation = splitPresentation(fixture.input, fixture.generation_status);
    assert.equal(presentation.schema_version, COACH_PRESENTATION_SCHEMA, fixture.name);
    assert.equal(presentation.generation_status, fixture.generation_status, fixture.name);
    assert.deepEqual(
      comparable(presentation),
      fixture.expected_segments,
      `${fixture.name}: expected_segments must match byte-for-byte`,
    );
    for (const [index, segment] of presentation.segments.entries()) {
      assert.equal(segment.id, `seg:${index}`, `${fixture.name}: segment ids are stable by position`);
      if (segment.kind === "openui") {
        assert.equal(segment.library_id, COACH_OPENUI_LIBRARY_ID);
        assert.equal(segment.library_version, COACH_OPENUI_LIBRARY_VERSION);
      }
    }
  }
});

test("plain text replies are a single text segment identical to the normalized input", () => {
  const fixture = fixtures.find((item) => item.name === "plain-text-only")!;
  const presentation = splitPresentation(fixture.input, "completed");
  assert.equal(presentation.segments.length, 1);
  assert.deepEqual(comparable(presentation), fixture.expected_segments);
});

test("openui DSL is never format-cleaned; text segments still are", () => {
  const fixture = fixtures.find((item) => item.name === "dsl-with-hostile-chars")!;
  const openui = splitPresentation(fixture.input, "completed").segments.find(
    (segment) => segment.kind === "openui",
  );
  assert.ok(openui && openui.kind === "openui");
  assert.ok(openui.source.includes("<script>alert(1)</script>"));
  assert.ok(openui.source.includes("`反引号`"));
  assert.ok(openui.source.includes("## 标题形文本"));
  assert.ok(openui.source.includes("5<6"));

  // 同样字符在 text 段照旧剥 HTML / 标题记号 / 行内反引号。
  const textPresentation = splitPresentation("<b>粗</b>\n\n## 标题\n\n`code`", "completed");
  assert.equal(textOf(textPresentation), "粗\n\n标题\n\ncode");
});

test("lookalike and ordinary fences never become openui segments", () => {
  const lookalike = fixtures.find((item) => item.name === "lookalike-fences-not-matched")!;
  const presentation = splitPresentation(lookalike.input, "completed");
  assert.ok(presentation.segments.every((segment) => segment.kind === "text"));
  assert.deepEqual(comparable(presentation), lookalike.expected_segments);

  // openui-lang 出现在普通围栏内部时是代码内容，不是 UI 区段。
  const nested = "前\n```python\n```openui-lang\nroot = Card([])\n```\n后";
  const nestedPresentation = splitPresentation(nested, "completed");
  assert.ok(nestedPresentation.segments.every((segment) => segment.kind === "text"));
  assert.equal(textOf(nestedPresentation), "前\n后");
});

test("case sensitivity and exact back boundary: only 'openui-lang' opens a UI segment", () => {
  const bodies = ["```openui-langx\nroot = Card([])\n```", "```OpenUI-Lang\nroot = Card([])\n```", "```openui-Lang\nroot = Card([])\n```"];
  for (const body of bodies) {
    const presentation = splitPresentation(`前\n\n${body}\n\n后`, "completed");
    assert.ok(presentation.segments.every((segment) => segment.kind === "text"), body);
  }
  // 行尾空白不算标识的一部分：`openui-lang ` 仍是 UI 区段。
  const trailingSpace = splitPresentation("前\n\n```openui-lang \nroot = Card([])\n```\n\n后", "completed");
  assert.deepEqual(trailingSpace.segments.map((segment) => segment.kind), ["text", "openui", "text"]);
});

test("unclosed openui fence with a trailing newline drops the structural newline", () => {
  const presentation = splitPresentation("看：\n\n```openui-lang\nroot = Card([])\n", "streaming");
  assert.deepEqual(comparable(presentation), [
    { kind: "text", text: "看：\n\n" },
    { kind: "openui", source: "root = Card([])" },
  ]);
});

test("an incomplete fence marker is held back instead of leaking into text", () => {
  for (const partial of ["`", "``", "```", "```openui", "```openui-la", "```openui-lang"]) {
    const presentation = splitPresentation(`前文\n\n${partial}`, "streaming");
    assert.deepEqual(comparable(presentation), [{ kind: "text", text: "前文\n\n" }], partial);
  }
  // 已确定不是 openui 的前缀照常作为正文出现（随后被归一化连未闭合围栏剥除）。
  const notOpenui = splitPresentation("前文\n\n```openui-langs", "streaming");
  assert.deepEqual(comparable(notOpenui), [{ kind: "text", text: "前文" }]);
});

test("character split-point traversal: incremental chunks converge to full-input projection", () => {
  for (const fixture of fixtures) {
    const expected = comparable(splitPresentation(fixture.input, fixture.generation_status));
    for (let split = 0; split <= fixture.input.length; split++) {
      const splitter = new PresentationSplitter();
      splitter.push(fixture.input.slice(0, split));
      const prefixSnapshot = splitter.snapshot(fixture.generation_status);
      // 关键防泄漏断言：任一前缀快照的 text 段都不得含 DSL。
      assertNoDslLeak(prefixSnapshot, `${fixture.name} @${split}`);
      splitter.push(fixture.input.slice(split));
      const finalSnapshot = splitter.snapshot(fixture.generation_status);
      assert.deepEqual(
        comparable(finalSnapshot),
        expected,
        `${fixture.name}: split at ${split} must converge to the full-input projection`,
      );
    }
  }
});

test("character-by-character streaming delivers the same final projection and never leaks DSL", () => {
  for (const fixture of fixtures) {
    const expected = comparable(splitPresentation(fixture.input, fixture.generation_status));
    const splitter = new PresentationSplitter();
    for (const character of fixture.input) {
      splitter.push(character);
      assertNoDslLeak(splitter.snapshot(fixture.generation_status), `${fixture.name}: char snapshot`);
    }
    assert.deepEqual(comparable(splitter.snapshot(fixture.generation_status)), expected, fixture.name);
  }
});

test("secret redaction applies to text and openui segments without formatting the DSL", () => {
  const secret = "sk-presentation-secret";
  const input = `提到 ${secret} 就结束\n\n\`\`\`openui-lang\nroot = Card([note])\nnote = TextContent(\"token=${secret}；## 保持原样\")\n\`\`\``;
  const presentation = splitPresentation(input, "completed", { secrets: [secret] });
  assert.ok(!JSON.stringify(presentation).includes(secret), "secret must not survive redaction");
  const openui = presentation.segments.find((segment) => segment.kind === "openui");
  assert.ok(openui && openui.kind === "openui");
  assert.ok(openui.source.includes("[REDACTED]"));
  assert.ok(openui.source.includes("## 保持原样"));
  assert.equal(openui.source.startsWith("root = Card([note])"), true);
});

test("oversized openui segment degrades to text with cause=budget_exceeded", () => {
  const huge = "root = Card([chart])\n" + `value = [${"1,".repeat(40_000)}1]`;
  const input = `看图：\n\n\`\`\`openui-lang\n${huge}\n\`\`\`\n\n完。`;
  const presentation = splitPresentation(input, "completed");
  assert.equal(presentation.cause, "budget_exceeded");
  assert.deepEqual(presentation.segments.map((segment) => segment.kind), ["text", "text", "text"]);
  const degraded = presentation.segments[1]!;
  assert.ok(degraded.kind === "text");
  assert.ok(degraded.text.includes("root = Card([chart])"), "degraded segment must keep the raw source");
  assert.equal(presentation.segments.some((segment) => segment.kind === "openui"), false);
});

test("a second openui fence degrades to text with cause=budget_exceeded", () => {
  const input = [
    "前",
    "",
    "```openui-lang",
    "root = Card([a])",
    "```",
    "",
    "中",
    "",
    "```openui-lang",
    "root = Card([b])",
    "```",
    "",
    "后",
  ].join("\n");
  const presentation = splitPresentation(input, "completed");
  assert.equal(presentation.cause, "budget_exceeded");
  assert.deepEqual(presentation.segments.map((segment) => segment.kind), ["text", "openui", "text", "text", "text"]);
  const first = presentation.segments[1]!;
  assert.ok(first.kind === "openui" && first.source === "root = Card([a])");
  const degraded = presentation.segments[3]!;
  assert.ok(degraded.kind === "text" && degraded.text === "root = Card([b])");
});

test("completed/streaming states carry no failure cause; stopped/interrupted do", () => {
  const input = "看：\n\n```openui-lang\nroot = Card([])";
  assert.equal(splitPresentation(input, "completed").cause, undefined);
  assert.equal(splitPresentation(input, "streaming").cause, undefined);
  assert.equal(splitPresentation(input, "failed").cause, undefined);
  assert.equal(splitPresentation(input, "stopped").cause, "stopped");
  assert.equal(splitPresentation(input, "interrupted").cause, "process_interrupted");
});

test("transport budget truncates text segments but keeps openui segments intact", () => {
  const longText = "a".repeat(20_000);
  const dsl = "root = Card([chart])\nchart = BarChart(labels, [series])";
  const presentation: CoachPresentation = {
    schema_version: COACH_PRESENTATION_SCHEMA,
    message_id: null,
    run_id: null,
    revision: 1,
    generation_status: "streaming",
    segments: [
      { id: "seg:0", kind: "text", text: longText },
      {
        id: "seg:1",
        kind: "openui",
        library_id: COACH_OPENUI_LIBRARY_ID,
        library_version: COACH_OPENUI_LIBRARY_VERSION,
        source: dsl,
      },
    ],
  };
  const bounded = applyPresentationTransportBudget(presentation);
  assert.equal(bounded.segments[0]!.kind === "text" && bounded.segments[0]!.text.length, 12_000);
  const openui = bounded.segments[1]!;
  assert.ok(openui.kind === "openui" && openui.source === dsl);
});

test("transport budget degrades an oversized openui segment instead of truncating it", () => {
  const oversized = `root = Card([])\n${"x".repeat(70 * 1024)}`;
  const presentation: CoachPresentation = {
    schema_version: COACH_PRESENTATION_SCHEMA,
    message_id: null,
    run_id: null,
    revision: 1,
    generation_status: "streaming",
    segments: [
      {
        id: "seg:0",
        kind: "openui",
        library_id: COACH_OPENUI_LIBRARY_ID,
        library_version: COACH_OPENUI_LIBRARY_VERSION,
        source: oversized,
      },
    ],
  };
  const bounded = applyPresentationTransportBudget(presentation);
  assert.equal(bounded.cause, "budget_exceeded");
  assert.equal(bounded.segments[0]!.kind, "text");
  assert.ok(bounded.segments[0]!.kind === "text" && bounded.segments[0]!.text === oversized);
});
