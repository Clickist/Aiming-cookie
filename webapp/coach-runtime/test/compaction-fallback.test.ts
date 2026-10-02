import assert from "node:assert/strict";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

// Each run gets a fresh data root so the repo checkout stays clean.
const dataRoot = mkdtempSync(join(tmpdir(), "aiming-cookie-compaction-fallback-test-"));
process.env.DATA_ROOT = dataRoot;

const { registerCompactionFallback } = await import("../src/compaction-fallback.ts");
const { loadPiAgent } = await import("../src/pi-source.ts");
const { shouldCompactNow } = await import("../src/turn.ts");

test.after(() => {
  rmSync(dataRoot, { recursive: true, force: true });
});

// ── 测试脚手架 ────────────────────────────────────────────────────────────

interface HookEvent {
  preparation: Record<string, unknown>;
  signal?: AbortSignal;
  customInstructions?: string;
}

interface SummaryCall {
  promptText: string;
  systemPrompt: string | undefined;
}

/** fake models：completeSimple 记录每次摘要调用的 prompt；failOnCall 指定
 * 第几次调用返回 stopReason=error（模拟中途块失败）。 */
function createSummaryRecorder(replies: string[], failOnCall?: number) {
  const calls: SummaryCall[] = [];
  const models = {
    completeSimple: async (_model: unknown, context: { systemPrompt?: string; messages: unknown[] }) => {
      const index = calls.length;
      const text = (context.messages[0] as { content?: Array<{ type?: string; text?: string }> })
        ?.content?.[0]?.text;
      calls.push({ promptText: typeof text === "string" ? text : "", systemPrompt: context.systemPrompt });
      if (failOnCall !== undefined && index === failOnCall) {
        return { role: "assistant", content: [], stopReason: "error", errorMessage: "boom" };
      }
      return {
        role: "assistant",
        content: [{ type: "text", text: replies[Math.min(index, replies.length - 1)] ?? "摘要" }],
        stopReason: "stop",
        usage: { input: 100 + index, output: 10, cacheRead: 0, cacheWrite: 0, totalTokens: 110 + index },
      };
    },
  };
  return { models, calls };
}

/** 注册 hook 并捕获 handler，返回可直接调用的入口。 */
function captureHook(options: {
  models: unknown;
  estimateMessage: (message: unknown) => number;
  thresholdTokens?: number;
  chunkTokens?: number;
  model?: unknown;
  thinkingLevel?: unknown;
}) {
  let handler: ((event: HookEvent) => Promise<{ compaction?: Record<string, unknown> } | undefined>) | undefined;
  registerCompactionFallback(
    {
      on: (type: string, h: typeof handler) => {
        assert.equal(type, "session_before_compact");
        handler = h;
      },
    } as never,
    { model: { id: "fake" }, ...options } as never,
  );
  if (!handler) throw new Error("handler not captured");
  return (event: HookEvent) => handler!(event);
}

const messageOf = (text: string) => ({ role: "user", content: [{ type: "text", text }] });
// 估算器 stub：每条消息 50 "tokens"（与内容无关，便于构造块边界）。
const EST_50 = () => 50;

function preparationOf(messages: unknown[], overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    firstKeptEntryId: "kept-entry-1",
    messagesToSummarize: messages,
    turnPrefixMessages: [],
    isSplitTurn: false,
    tokensBefore: 123_456,
    previousSummary: undefined,
    fileOps: undefined,
    ...overrides,
  };
}

// ── 未超阈值：返回 undefined，走 pi 原生单次摘要 ──────────────────────────

test("below threshold the hook returns undefined so pi native single-call path stays intact", async () => {
  const { models, calls } = createSummaryRecorder(["不该被调用"]);
  const invoke = captureHook({ models, estimateMessage: EST_50, thresholdTokens: 1_000, chunkTokens: 400 });
  // 4 条 × 50 = 200 ≤ 1000 → undefined
  const result = await invoke({ preparation: preparationOf([messageOf("a"), messageOf("b"), messageOf("c"), messageOf("d")]) });
  assert.equal(result, undefined);
  assert.equal(calls.length, 0);
});

// ── 超阈值：分块链式摘要 ──────────────────────────────────────────────────

test("above threshold messages are chunked and summaries chain via previousSummary", async () => {
  const { models, calls } = createSummaryRecorder(["块一摘要", "块二摘要", "块三摘要"]);
  const invoke = captureHook({ models, estimateMessage: EST_50, thresholdTokens: 100, chunkTokens: 110 });
  const result = await invoke({
    preparation: preparationOf([messageOf("m1"), messageOf("m2"), messageOf("m3"), messageOf("m4"), messageOf("m5")]),
  });

  // 5 条 × 50 = 250 > 100 → 分块；每块 2 条（2×50=100 ≤ 110，第 3 条开新块）→ 3 次调用
  assert.equal(calls.length, 3);
  // 第 1 块无前情；第 2/3 块 prompt 里带前块摘要（previousSummary 链式 UPDATE）
  assert.ok(!calls[0].promptText.includes("块一摘要"), "first chunk must not carry a previous summary");
  assert.ok(calls[1].promptText.includes("块一摘要"), "second chunk must chain first summary");
  assert.ok(calls[2].promptText.includes("块二摘要"), "third chunk must chain second summary");
  // 每块 prompt 只含本块消息
  assert.ok(calls[0].promptText.includes("m1") && calls[0].promptText.includes("m2"));
  assert.ok(!calls[0].promptText.includes("m3"));
  // CompactResult：末块摘要为最终产出，usage 为三块之和（110+111+112）
  assert.ok(result && result.compaction);
  assert.equal(result.compaction.summary, "块三摘要");
  assert.equal((result.compaction.usage as { totalTokens: number }).totalTokens, 333);
  assert.equal(result.compaction.firstKeptEntryId, "kept-entry-1");
  assert.equal(result.compaction.tokensBefore, 123_456);
});

// ── split-turn 的 turnPrefixMessages 并入分块链（保真降级路径不丢内容）───

test("split-turn prefix messages are folded into the chunk chain", async () => {
  const { models, calls } = createSummaryRecorder(["块一摘要", "块二摘要"]);
  const invoke = captureHook({ models, estimateMessage: EST_50, thresholdTokens: 100, chunkTokens: 110 });
  const result = await invoke({
    preparation: preparationOf([messageOf("m1"), messageOf("m2")], {
      turnPrefixMessages: [messageOf("prefix1"), messageOf("prefix2")],
      isSplitTurn: true,
    }),
  });
  // 4 条 × 50 = 200 > 100 → 两块；prefix 消息进链（不丢内容）
  assert.equal(calls.length, 2);
  assert.ok(calls[1].promptText.includes("prefix"), "prefix messages must be summarized, not dropped");
  assert.ok(result && result.compaction);
  assert.equal(result.compaction.summary, "块二摘要");
});

// ── 中途块失败：抛错（harness.compact 整体失败，对话不拦）─────────────────

test("a failing chunk rejects the handler so compaction fails loudly", async () => {
  const { models } = createSummaryRecorder(["块一摘要", "块二摘要"], 1);
  const invoke = captureHook({ models, estimateMessage: EST_50, thresholdTokens: 100, chunkTokens: 110 });
  await assert.rejects(
    invoke({ preparation: preparationOf([messageOf("m1"), messageOf("m2"), messageOf("m3"), messageOf("m4")]) }),
    /Chunked compaction summary failed \(chunk 2\/2\)/,
  );
});

// ── fileOps 镜像：summary 尾部追加文件清单标签 ────────────────────────────

test("fileOps ledger is mirrored onto the chunked summary", async () => {
  const { models } = createSummaryRecorder(["最终摘要"]);
  const invoke = captureHook({
    models,
    estimateMessage: () => 10_000, // 单块即超阈值
    thresholdTokens: 1_000,
    chunkTokens: 50_000,
  });
  const result = await invoke({
    preparation: preparationOf([messageOf("m1")], {
      fileOps: { read: new Set(["a.ts", "b.ts"]), edited: new Set(["c.ts"]) },
    }),
  });
  assert.ok(result && result.compaction);
  const compaction = result.compaction as { summary: string; details: { readFiles: string[]; modifiedFiles: string[] } };
  assert.equal(
    compaction.summary,
    "最终摘要\n\n<read-files>\na.ts\nb.ts\n</read-files>\n\n<modified-files>\nc.ts\n</modified-files>",
  );
  assert.deepEqual(compaction.details.readFiles, ["a.ts", "b.ts"]);
  assert.deepEqual(compaction.details.modifiedFiles, ["c.ts"]);
});

// ── 单条消息超块上限：块内截断兜底（业界防线）───────────────────────────

test("a single message larger than one chunk is truncated in-chunk and the chain completes", async () => {
  const { models, calls } = createSummaryRecorder(["块一摘要", "块二摘要"]);
  // 估算器按文本字符折算（英文 chars/4 口径），贴近真实路径。
  const textOf = (m: unknown): number => {
    const content = (m as { content?: unknown }).content;
    if (typeof content === "string") return content.length;
    if (Array.isArray(content)) {
      return content.reduce((sum: number, b) => {
        const text = (b as { text?: unknown })?.text;
        return sum + (typeof text === "string" ? text.length : 0);
      }, 0);
    }
    return 0;
  };
  const estimator = (m: unknown) => Math.ceil(textOf(m) / 4);
  const invoke = captureHook({ models, estimateMessage: estimator, thresholdTokens: 100, chunkTokens: 60 });

  // 怪物正文（880 字符 ≈ 220 tokens ≫ 60 单块）+ 一条会开新块的普通消息。
  const head = "头部内容必须保留".repeat(30);
  const tail = "尾部内容必须被截掉".repeat(100);
  const monster = `${head}${tail}`;
  const normal = messageOf("普通消息继续分块链。".repeat(10));
  const preparation = preparationOf([messageOf(monster), normal]);

  const result = await invoke({ preparation });

  // 截断生效：第 1 块 prompt 保留头部、带截断标注、不含尾部。
  assert.equal(calls.length, 2, "truncated monster + next message should form two chunks");
  assert.ok(calls[0].promptText.includes(head.slice(0, 20)), "chunk keeps the head of the oversized message");
  assert.ok(calls[0].promptText.includes("message truncated"), "chunk carries the truncation marker");
  assert.ok(!calls[0].promptText.includes("尾部内容"), "chunk must not contain the dropped tail");
  // 链式正常走完：第 2 块带前块摘要，最终产出是末块摘要。
  assert.ok(calls[1].promptText.includes("块一摘要"), "chain continues after the oversized message");
  assert.ok(calls[1].promptText.includes("普通消息继续分块链"), "next message rides the following chunk");
  assert.ok(result && result.compaction);
  assert.equal(result.compaction.summary, "块二摘要");
  // 会话数据零改动：preparation 原消息仍是全量（截断只发生在摘要 prompt 副本上）。
  const original = (preparation.messagesToSummarize[0] as { content: Array<{ text: string }> }).content[0].text;
  assert.equal(original.length, monster.length);
});

// ── harness 层等效超长会话集成：怪物会话 → harness.compact() 分块救活 ────

test("harness-level monster session compacts via chunked summaries and leaves the folded view small", async () => {
  const { AgentHarness, InMemorySessionRepo } = (await loadPiAgent()) as {
    AgentHarness: new (opts: Record<string, unknown>) => {
      on: (type: string, handler: unknown) => unknown;
      compact: () => Promise<unknown>;
    };
    InMemorySessionRepo: new () => { create: () => Promise<any> };
  };

  // 怪物会话：40 对 × ~11K 中文字符 ≈ 896K 字符 ≈ 627K tokens（CJK 0.7）
  // ——对齐 0908 实测的 62 万 tokens 形态；末条 assistant 带滑窗时代失真
  // usage（~30K）。
  const STALE_WINDOW_USAGE = { input: 30_000, output: 500, cacheRead: 0, cacheWrite: 0, totalTokens: 30_500 };
  const chineseText = "教练对话历史模拟：瞄准训练复盘要兼顾灵敏度、场景识别与转火决策。".repeat(400);
  const session = await new InMemorySessionRepo().create();
  for (let i = 0; i < 40; i++) {
    await session.appendMessage({ role: "user", content: [{ type: "text", text: chineseText }] });
    await session.appendMessage({
      role: "assistant",
      content: [{ type: "text", text: chineseText }],
      usage: STALE_WINDOW_USAGE,
      stopReason: "stop",
    });
  }

  // 修复后的判定必须触发（旧代码在此返回 false → 直发全量 → 2.5MB+ 请求体）。
  assert.equal(await shouldCompactNow(session, 128_000), true);

  const { models, calls } = createSummaryRecorder(Array.from({ length: 24 }, (_, i) => `第${i + 1}块摘要`));
  const fakeModel = { id: "fake-model", maxTokens: 0 };
  const harness = new AgentHarness({ session, models, model: fakeModel, tools: [] });
  // 默认参数（96K 阈值 / 48K 每块）；估算器按块内消息数近似（每条 7K）→
  // 80 条 × 7K = 560K → ≥3 块。
  registerCompactionFallback(harness as never, { models, model: fakeModel, estimateMessage: () => 7_000 });

  await harness.compact();

  // compaction entry 已落库：摘要是末块产出，usage 是分块之和。
  const branch = await session.getBranch();
  const entry = branch[branch.length - 1] as {
    type: string;
    summary: string;
    usage?: { totalTokens: number };
  };
  assert.equal(entry.type, "compaction");
  assert.ok(entry.summary.length > 0);
  assert.ok(calls.length >= 3, `expected >=3 chunk calls, got ${calls.length}`);
  const fullText = 40 * 2 * chineseText.length;
  for (const call of calls) {
    assert.ok(call.promptText.length < fullText / 2, "each chunk prompt must be far smaller than the full history");
  }
  assert.ok((entry.usage?.totalTokens ?? 0) > 0, "compaction entry should carry summed chunk usage");

  // 压缩后折叠视图回落阈值以下：不会每轮重压缩（缓存护栏）。
  assert.equal(await shouldCompactNow(session, 128_000), false);
});
