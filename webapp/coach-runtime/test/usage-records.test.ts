import assert from "node:assert/strict";
import { appendFileSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

// Each run gets a fresh data root so the repo checkout stays clean.
const dataRoot = mkdtempSync(join(tmpdir(), "aiming-cookie-usage-test-"));
process.env.DATA_ROOT = dataRoot;

const {
  ensureSession,
  openSession,
  readConversationMeta,
  writeConversationMeta,
} = await import("../src/session-repo.ts");
const { listCoachUsageRecords } = await import("../src/sidecar-coach-data.ts");

test.after(() => {
  rmSync(dataRoot, { recursive: true, force: true });
});

// 除「本月汇总」用例外，其余记录一律落在上个月（本地时区的上个月 15 日）：
// 本月汇总口径只统计当月，这样当前月聚合只由汇总用例自己控制，断言可精确到个位。
const NOW = new Date();
const PAST_MONTH = new Date(NOW.getFullYear(), NOW.getMonth() - 1, 15, 12, 0, 0 as number);
/** 上个月内偏移 minutes 的 ISO 时间戳。 */
function pastStamp(minutes: number): string {
  return new Date(PAST_MONTH.getTime() + minutes * 60_000).toISOString();
}
function localMonthKeyOf(date: Date): string {
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}`;
}

/** 真实 Pi JSONL 的 assistant 行（含 usage），落盘形状与线上文件一致。 */
function assistantLine(opts: {
  id: string;
  parentId: string | null;
  timestamp: string;
  model?: string | null;
  provider?: string | null;
  usage?: Record<string, unknown> | null;
}): string {
  const message: Record<string, unknown> = {
    role: "assistant",
    content: [{ type: "text", text: "OK" }],
    api: "openai-completions",
    stopReason: "stop",
  };
  if (opts.model !== null) message.model = opts.model ?? "deepseek-v4-flash";
  if (opts.provider !== null) message.provider = opts.provider ?? "relay-station";
  if (opts.usage !== null) {
    message.usage = opts.usage ?? {
      input: 1683,
      output: 2,
      cacheRead: 8192,
      cacheWrite: 0,
      reasoning: 0,
      totalTokens: 9877,
      cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 },
    };
  }
  return `${JSON.stringify({
    type: "message",
    id: opts.id,
    parentId: opts.parentId,
    timestamp: opts.timestamp,
    message,
  })}\n`;
}

/** 直接向会话 JSONL 追加原始行：不猜 pi appendMessage 支不支持额外字段，
 *  用真实文件的落盘形状构造数据最稳。 */
async function appendRaw(sessionId: number, line: string): Promise<void> {
  const session = await openSession(sessionId);
  assert.ok(session, `session ${sessionId} must exist`);
  appendFileSync((await session.getMetadata()).path, line, "utf8");
}

test("usage records parse assistant usage, sort by time desc and carry the title", async () => {
  await ensureSession(201);
  await appendRaw(201, assistantLine({ id: "a1", parentId: null, timestamp: pastStamp(0) }));
  await appendRaw(201, assistantLine({ id: "a2", parentId: "a1", timestamp: pastStamp(60) }));
  // 标题取自会话 meta（readConversationMeta）。
  const meta = readConversationMeta(201);
  meta.title = "跟枪局复盘";
  meta.title_source = "auto";
  writeConversationMeta(201, meta);

  const out = await listCoachUsageRecords(50);
  const mine = out.records.filter((record) => record.session_id === 201);
  assert.equal(mine.length, 2);
  // timestamp 降序：a2 在前。
  assert.deepEqual(mine.map((record) => record.timestamp), [pastStamp(60), pastStamp(0)]);
  assert.equal(mine[0].session_title, "跟枪局复盘");
  assert.equal(mine[0].model, "deepseek-v4-flash");
  assert.equal(mine[0].provider, "relay-station");
  assert.deepEqual(mine[0].usage, {
    input: 1683,
    output: 2,
    cache_read: 8192,
    cache_write: 0,
    reasoning: 0,
    total_tokens: 9877,
  });
  assert.equal(typeof out.generated_at, "string");
});

test("assistant entries without usage are skipped; unreported fields stay null", async () => {
  await ensureSession(202);
  // 无 usage 的 assistant 条目（老会话/无计费 provider）不产生记录。
  await appendRaw(202, assistantLine({ id: "b1", parentId: null, timestamp: pastStamp(120), usage: null }));
  // usage 只有部分字段：缺的记 null（provider 没报 ≠ 0）；缺 model/provider 同理。
  await appendRaw(202, assistantLine({
    id: "b2",
    parentId: "b1",
    timestamp: pastStamp(180),
    model: null,
    provider: null,
    usage: { input: 10 },
  }));
  const mine = (await listCoachUsageRecords(50)).records.filter((record) => record.session_id === 202);
  assert.equal(mine.length, 1);
  assert.deepEqual(mine[0].usage, {
    input: 10,
    output: null,
    cache_read: null,
    cache_write: null,
    reasoning: null,
    total_tokens: null,
  });
  assert.equal(mine[0].model, null);
  assert.equal(mine[0].provider, null);
});

test("a corrupt session file is skipped without breaking the whole list", async () => {
  await ensureSession(203);
  await appendRaw(203, assistantLine({ id: "c1", parentId: null, timestamp: pastStamp(240) }));
  const before = (await listCoachUsageRecords(50)).records.length;
  assert.ok(before > 0);

  // 坏会话甲：header 合法、正文有垃圾行——repo.list 列得到，读取 branch 时抛错。
  await ensureSession(204);
  const broken = await openSession(204);
  assert.ok(broken);
  writeFileSync(
    (await broken.getMetadata()).path,
    `${JSON.stringify({ type: "session", version: 3, id: "204", timestamp: pastStamp(300), cwd: "coach" })}\nnot-json{{{\n`,
    "utf8",
  );
  // 坏会话乙：文件被清空（连 header 都没了）——repo.list 直接跳过，openSession 回 null。
  await ensureSession(205);
  const headerless = await openSession(205);
  assert.ok(headerless);
  writeFileSync((await headerless.getMetadata()).path, "", "utf8");

  const out = await listCoachUsageRecords(50);
  assert.equal(out.records.length, before, "坏会话不得改变其余记录数");
  assert.ok(out.records.some((record) => record.session_id === 203));
  assert.ok(!out.records.some((record) => record.session_id === 204 || record.session_id === 205));
  assert.ok((await listCoachUsageRecords(50)).records.length > 0, "坏会话不得让整张列表变空");
});

test("limit truncates the record list but not the month aggregate", async () => {
  await ensureSession(206);
  for (let index = 0; index < 7; index++) {
    await appendRaw(206, assistantLine({
      id: `d${index}`,
      parentId: index === 0 ? null : `d${index - 1}`,
      timestamp: pastStamp(360 + index),
      usage: { input: 100, output: 10, cacheRead: 0, cacheWrite: 0, totalTokens: 110 },
    }));
  }
  const clipped = await listCoachUsageRecords(3);
  assert.equal(clipped.records.length, 3);
  // 降序后截断：最新的在前。
  assert.deepEqual(clipped.records.map((record) => record.timestamp), [
    pastStamp(366),
    pastStamp(365),
    pastStamp(364),
  ]);

  // 缺省 50、上界 clamp 200、下界 clamp 1。
  assert.ok((await listCoachUsageRecords(9999)).records.length <= 200);
  assert.equal((await listCoachUsageRecords(0)).records.length, 1);
  // 上月的 7 条不进入本月汇总（汇总只算当月，且不受 limit 影响）。
  assert.equal(clipped.month.count, 0);
});

test("month aggregate is empty with a null hit rate while no current-month usage exists", async () => {
  // 分母 0 → null：所有记录都落在上个月，本月无任何计量。
  const out = await listCoachUsageRecords(50);
  assert.equal(out.month.key, localMonthKeyOf(NOW));
  assert.equal(out.month.count, 0);
  assert.equal(out.month.input_tokens, 0);
  assert.equal(out.month.output_tokens, 0);
  assert.equal(out.month.cache_read_tokens, 0);
  assert.equal(out.month.cache_hit_rate, null);
});

test("month aggregate counts null as 0 and computes cache_hit_rate", async () => {
  const now = new Date();
  const thisMonth = new Date(now.getFullYear(), now.getMonth(), 15, 12, 0, 0);
  await ensureSession(207);
  await appendRaw(207, assistantLine({
    id: "e0",
    parentId: null,
    timestamp: thisMonth.toISOString(),
    usage: { input: 1000, output: 100, cacheRead: 3000, cacheWrite: 5, totalTokens: 4105 },
  }));
  // 全 null 记录：本月合计按 0 计入（计数照算），命中率分母仍 4000。
  await appendRaw(207, assistantLine({
    id: "e1",
    parentId: "e0",
    timestamp: new Date(thisMonth.getTime() + 60_000).toISOString(),
    usage: { input: null, output: null, cacheRead: null, cacheWrite: null, totalTokens: null },
  }));
  // 上个月的记录不进入本月汇总。
  await appendRaw(207, assistantLine({
    id: "e2",
    parentId: "e1",
    timestamp: PAST_MONTH.toISOString(),
    usage: { input: 777, output: 77, cacheRead: 777, cacheWrite: 0, totalTokens: 1631 },
  }));

  const out = await listCoachUsageRecords(50);
  assert.equal(out.month.key, localMonthKeyOf(new Date()));
  assert.equal(out.month.count, 2);
  assert.equal(out.month.input_tokens, 1000);
  assert.equal(out.month.output_tokens, 100);
  assert.equal(out.month.cache_read_tokens, 3000);
  // cache_read/(input+cache_read) = 3000/4000。
  assert.equal(out.month.cache_hit_rate, 0.75);
});

test("sessions never named come back with a null title (frontend i18n fallback)", async () => {
  // 从未命名的会话：readConversationMeta 兜底「新对话」，记录里必须是 null——
  // 否则 en-US 界面会漏出中文标题。
  await ensureSession(208);
  await appendRaw(208, assistantLine({ id: "f0", parentId: null, timestamp: pastStamp(480) }));
  const mine = (await listCoachUsageRecords(50)).records.filter((record) => record.session_id === 208);
  assert.equal(mine.length, 1);
  assert.equal(mine[0].session_title, null);
});

test("a session with no usage records at all contributes nothing", async () => {
  await ensureSession(209);
  const session = await openSession(209);
  assert.ok(session);
  appendFileSync(
    (await session.getMetadata()).path,
    `${JSON.stringify({
      type: "message",
      id: "g0",
      parentId: null,
      timestamp: pastStamp(540),
      message: { role: "user", content: [{ type: "text", text: "你好" }], timestamp: Date.now() },
    })}\n`,
    "utf8",
  );
  const out = await listCoachUsageRecords(50);
  assert.ok(!out.records.some((record) => record.session_id === 209));
});
