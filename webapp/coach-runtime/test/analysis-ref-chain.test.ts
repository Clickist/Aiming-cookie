import assert from "node:assert/strict";
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

// Set DATA_ROOT before importing modules that call getDataRoot() (which caches).
const dataRoot = mkdtempSync(join(tmpdir(), "coach-analysis-ref-"));
process.env.DATA_ROOT = dataRoot;

const { ensureAppDataDirs, getConversationsDir } = await import("../src/app-data.ts");
const {
  createReadTool,
  createLsTool,
  explicitAnalysisRefsFromText,
  subscribeAnalysisReads,
} = await import("../src/fs-tools.ts");
const { ensureSession, readConversationMeta, updateConversationAnalysisIds } = await import("../src/session-repo.ts");
const { getCoachSessionDetail } = await import("../src/sidecar-coach-data.ts");

test.after(() => {
  rmSync(dataRoot, { recursive: true, force: true });
});

test("deep reads of analyses/N/ join the discussion list", async () => {
  // The model lectures by reading analyses/N/ with read/ls; those reads must
  // report engagement so 本次讨论 and @time links keep their analysis ref.
  // Non-analysis paths stay invisible.
  ensureAppDataDirs();
  const dir = join(dataRoot, "analyses", "1");
  mkdirSync(dir, { recursive: true });
  writeFileSync(join(dir, "overview.json"), "{}", "utf8");
  mkdirSync(join(dataRoot, "conversations"), { recursive: true });
  writeFileSync(join(dataRoot, "conversations", "x.txt"), "hi", "utf8");

  const seen: number[] = [];
  const unsubscribe = subscribeAnalysisReads((id) => seen.push(id));
  try {
    const read = await createReadTool(dataRoot);
    await read.execute("call", { path: "analyses/1/overview.json" });
    await read.execute("call", { path: join(dataRoot, "analyses", "1", "overview.json") });
    await read.execute("call", { path: "conversations/x.txt" });
    const ls = await createLsTool(dataRoot);
    await ls.execute("call", { path: "analyses/1" });
    assert.deepEqual(seen, [1, 1, 1]);
  } finally {
    unsubscribe();
  }
});

test("explicit analysis refs in user text pin the discussion subject", () => {
  assert.deepEqual(
    explicitAnalysisRefsFromText("请结合这份分析：analysis:6，讲讲主要问题和改进方向。"),
    [6],
  );
  assert.deepEqual(
    explicitAnalysisRefsFromText("对比 analysis:2 和 analysis:2 以及 analysis:0"),
    [2],
  );
  assert.deepEqual(explicitAnalysisRefsFromText("今天练什么"), []);
});

test("session meta unions engaged analysis ids", () => {
  ensureAppDataDirs();
  updateConversationAnalysisIds(1, [2]);
  updateConversationAnalysisIds(1, [1, 2]);
  const meta = readConversationMeta(1);
  assert.deepEqual(meta.analysis_session_ids, [1, 2]);
});

test("reference reads stay out of the discussion subject list", async () => {
  // 参照性读取（fs 深读等）只算 engagement，不进 analysis_refs；
  // 主题级上报（创建分析/evidence 视频打开）带 subject=true。
  const subjects: number[] = [];
  const engaged: number[] = [];
  const unsubscribe = subscribeAnalysisReads((id, subject) => {
    engaged.push(id);
    if (subject) subjects.push(id);
  });
  try {
    const { reportAnalysisRead } = await import("../src/fs-tools.ts");
    reportAnalysisRead(7);
    reportAnalysisRead(8, true);
    assert.deepEqual(engaged, [7, 8]);
    assert.deepEqual(subjects, [8]);
  } finally {
    unsubscribe();
  }
});

test("user-facing text extraction drops blank narration separators", async () => {
  // 模型 narration 块自带 \n\n 头尾：面向用户的拼接必须按块 trim、单换行
  // 连接并丢弃纯空白块，否则对话气泡出现连续空行。
  const { extractUserFacingText } = await import("../src/session-repo.ts");
  const content = [
    { type: "text", text: "\n\n我先查一下这局训练的记录，然后创建分析。\n\n" },
    { type: "text", text: "\n\n" },
    { type: "text", text: "\n\n这局有详细数据，我来创建分析。\n\n" },
    { type: "thinking", thinking: "invisible" },
  ];
  assert.equal(
    extractUserFacingText(content),
    "我先查一下这局训练的记录，然后创建分析。\n这局有详细数据，我来创建分析。",
  );
});

test("analysis mounts keep an attached_at ledger in mount order (1002 串视频修复)", () => {
  ensureAppDataDirs();
  // 首挂 103；并集语义不变，台账按挂载时间记账。
  updateConversationAnalysisIds(77, [103], "2026-10-01T10:00:00.000Z");
  let meta = readConversationMeta(77);
  assert.deepEqual(meta.analysis_session_ids, [103]);
  assert.deepEqual(meta.analysis_refs, [{ id: 103, attached_at: "2026-10-01T10:00:00.000Z" }]);
  // 同回合再挂两个：按 attached_at 升序，同刻保持挂载先后。
  updateConversationAnalysisIds(77, [101, 102], "2026-10-01T11:00:00.000Z");
  meta = readConversationMeta(77);
  assert.deepEqual(meta.analysis_session_ids, [101, 102, 103]);
  assert.deepEqual(meta.analysis_refs, [
    { id: 103, attached_at: "2026-10-01T10:00:00.000Z" },
    { id: 101, attached_at: "2026-10-01T11:00:00.000Z" },
    { id: 102, attached_at: "2026-10-01T11:00:00.000Z" },
  ]);
  // 重挂旧分析：attached_at 刷新并移到台账末尾，之后的追问归它。
  updateConversationAnalysisIds(77, [101], "2026-10-01T12:00:00.000Z");
  meta = readConversationMeta(77);
  assert.deepEqual(meta.analysis_session_ids, [101, 102, 103]);
  assert.deepEqual(meta.analysis_refs, [
    { id: 103, attached_at: "2026-10-01T10:00:00.000Z" },
    { id: 102, attached_at: "2026-10-01T11:00:00.000Z" },
    { id: 101, attached_at: "2026-10-01T12:00:00.000Z" },
  ]);
});

test("session detail returns the mount ledger; legacy id-only metas synthesize empty stamps", async () => {
  ensureAppDataDirs();
  // detail 携带挂载台账（按 attached_at 升序）。
  await ensureSession(78);
  updateConversationAnalysisIds(78, [301], "2026-10-01T10:00:00.000Z");
  updateConversationAnalysisIds(78, [300], "2026-10-01T11:00:00.000Z");
  const detail = await getCoachSessionDetail("test-owner", 78);
  assert.deepEqual(detail.analysis_refs, [
    { id: 301, attached_at: "2026-10-01T10:00:00.000Z" },
    { id: 300, attached_at: "2026-10-01T11:00:00.000Z" },
  ]);
  // 升级前的旧 meta 只有并集列表：detail 合成空时间戳 refs，前端退回序数近似。
  await ensureSession(79);
  writeFileSync(
    join(getConversationsDir(), "79.meta.json"),
    JSON.stringify({
      id: 79,
      title: null,
      title_source: null,
      status: "active",
      created_at: "2026-09-01T09:00:00.000Z",
      updated_at: "2026-09-01T09:00:00.000Z",
      analysis_session_ids: [201, 202],
    }),
    "utf8",
  );
  const legacyDetail = await getCoachSessionDetail("test-owner", 79);
  assert.deepEqual(legacyDetail.analysis_refs, [
    { id: 201, attached_at: "" },
    { id: 202, attached_at: "" },
  ]);
  assert.deepEqual(legacyDetail.analysis_session_ids, [201, 202]);
  // 旧会话再挂载：台账先以空时间戳播种存量 id，新挂载带真时间追加。
  updateConversationAnalysisIds(79, [203], "2026-10-02T10:00:00.000Z");
  assert.deepEqual(readConversationMeta(79).analysis_refs, [
    { id: 201, attached_at: "" },
    { id: 202, attached_at: "" },
    { id: 203, attached_at: "2026-10-02T10:00:00.000Z" },
  ]);
});
