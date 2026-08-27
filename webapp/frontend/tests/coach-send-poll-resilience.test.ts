import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import { test } from "node:test";

// Coach 面板两个 P1 的源码断言（与 task6-source.test.ts 同款风格）：
// P1-A 一次 Enter 产生两个会话；P1-B 断线后聊天卡死半句。
const root = path.resolve(import.meta.dirname, "..");

async function source(relativePath: string): Promise<string> {
  return readFile(path.join(root, relativePath), "utf8");
}

test("Coach composer ignores Enter while an IME composition is active", async () => {
  const panel = await source("components/task6/CoachPanel.tsx");
  const keydownAt = panel.indexOf("onKeyDown={(event) => {");
  // 批 5 强化：isComposing + keyCode 229 双守卫（部分浏览器组合期事件只报 229）。
  const composingAt = panel.indexOf(
    "if (event.nativeEvent.isComposing || event.nativeEvent.keyCode === 229) return;",
  );
  // 中文输入法按 Enter 确认候选词时 isComposing 为 true，不应触发提交。
  assert.ok(keydownAt !== -1 && composingAt !== -1, "composer keydown handler must exist");
  assert.ok(composingAt > keydownAt, "isComposing guard must live in the composer keydown handler");
});

test("Coach send holds a synchronous re-entry lock across its awaits", async () => {
  const panel = await source("components/task6/CoachPanel.tsx");
  assert.match(panel, /const sendingRef = useRef\(false\);/);
  // 重入直接丢弃；锁在任何 await 之前同步置位。
  assert.match(panel, /if \(!content \|\| sendingRef\.current\) return false;/);
  assert.match(panel, /sendingRef\.current = true;/);
  // 成功失败都必须走 finally 释放锁。
  assert.match(panel, /\} finally \{\s*sendingRef\.current = false;\s*\}/);
});

test("Coach send failure only restores the draft when the user typed nothing new", async () => {
  const panel = await source("components/task6/CoachPanel.tsx");
  const sendChunk = panel.slice(
    panel.indexOf("const sendText = async"),
    panel.indexOf("const submitComposer"),
  );
  // 发送主路径（成功 setDraft("") 之外）不得无条件用原内容覆盖草稿；
  // 失败回填必须条件式——用户已在等待期重新输入则保留新草稿。
  assert.doesNotMatch(sendChunk, /setDraft\(content\)/);
  assert.match(panel, /setDraft\(\(current\) => \(current\.trim\(\) \? current : content\)\)/);
});

test("AppShell dedupes concurrent coach session creation through an in-flight promise", async () => {
  const shell = await source("components/task3/AppShell.tsx");
  assert.match(shell, /ensureSessionInFlightRef = useRef<Promise<number \| null> \| null>\(null\)/);
  assert.match(shell, /if \(ensureSessionInFlightRef\.current\) return ensureSessionInFlightRef\.current;/);
  // 完成/失败后清掉缓存，后续调用重新创建。
  assert.match(shell, /\} finally \{\s*ensureSessionInFlightRef\.current = null;\s*\}/);
  assert.match(shell, /ensureSessionInFlightRef\.current = promise;\s*return promise;/);
});

test("Coach polling fallback retries with backoff instead of dying after one failure", async () => {
  const panel = await source("components/task6/CoachPanel.tsx");
  assert.match(panel, /POLL_MAX_FAILURES = 8;/);
  assert.match(panel, /POLL_BASE_INTERVAL_MS = 1000;/);
  assert.match(panel, /POLL_MAX_INTERVAL_MS = 10_000;/);
  // 失败计数驱动指数退避：1s→2s→4s…上限 10s。
  assert.match(panel, /Math\.min\(POLL_BASE_INTERVAL_MS \* 2 \*\* pollFailures, POLL_MAX_INTERVAL_MS\)/);
  // 单次失败续拍而不是停止轮询；成功即清零恢复正常节奏。
  assert.match(panel, /let pollFailures = 0;/);
  assert.match(panel, /pollFailures \+= 1;\s*if \(pollFailures >= POLL_MAX_FAILURES\) \{\s*markInterrupted\(\);\s*return;\s*\}\s*schedulePoll\(\);/);
  assert.match(panel, /pollFailures = 0;/);
});

test("Coach marks interrupted runs terminal on 404 or exhausted polls", async () => {
  const panel = await source("components/task6/CoachPanel.tsx");
  // 404（run 已不存在）立即收敛为中断终态。
  assert.match(panel, /error instanceof Error && error\.name === "ApiError_404"/);
  assert.match(panel, /const markInterrupted = \(\) => \{/);
  assert.match(panel, /status: "failed",/);
  assert.match(panel, /code: "run_interrupted"/);
  assert.match(panel, /retryable: true,/);
  assert.match(panel, /notify\("回复已中断"\)/);
  // 终态确认失败也要回落到轮询兜底，不能一次失败即丢。
  assert.match(panel, /finalizeRun = async \(\) => \{[\s\S]*?catch \(error\) \{[\s\S]*?isMissingRunError\(error\)[\s\S]*?schedulePoll\(\);/);
});

test("Coach contracts declare the reserved deep-read fields", async () => {
  const types = await source("lib/types.ts");
  assert.match(types, /deep_read_analysis_refs\?: string\[\];/);
  assert.match(types, /deep_read_analysis_session_ids\?: number\[\];/);
});

test("Coach @time links fall back through topic refs then deep-read refs", async () => {
  const panel = await source("components/task6/CoachPanel.tsx");
  // 四级兜底链：主题 run refs → 会话主题 ids → run 深读 refs（取最后一个）→ 会话深读 ids（取最后一个）。
  assert.match(panel, /defaultAnalysisRef = topicRunRef \?\? topicSessionRef \?\? deepReadRunRef \?\? deepReadSessionRef;/);
  assert.match(panel, /const topicRunRef = run\?\.analysis_refs\?\.length \? run\.analysis_refs\[0\] : null;/);
  assert.match(panel, /const topicSessionRef = analysisSessionIds\.length \? `analysis:\$\{analysisSessionIds\[0\]\}` : null;/);
  // 深读兜底取列表末尾（最近一次深读），不是首个。
  assert.match(
    panel,
    /const deepReadRunRef = run\?\.deep_read_analysis_refs\?\.length\s*\?\s*run\.deep_read_analysis_refs\[run\.deep_read_analysis_refs\.length - 1\]/,
  );
  assert.match(
    panel,
    /const deepReadSessionRef = deepReadAnalysisSessionIds\.length\s*\?\s*`analysis:\$\{deepReadAnalysisSessionIds\[deepReadAnalysisSessionIds\.length - 1\]\}`/,
  );
  // 会话层深读 ids 有独立 state，并在 refresh 中随 detail 写入/清空。
  assert.match(panel, /const \[deepReadAnalysisSessionIds, setDeepReadAnalysisSessionIds\] = useState<number\[\]>\(\[\]\);/);
  assert.match(panel, /setDeepReadAnalysisSessionIds\(detail\.deep_read_analysis_session_ids \?\? \[\]\);/);
  assert.match(panel, /setDeepReadAnalysisSessionIds\(\[\]\);\s*setLoadError\(false\);/);
});

test("Coach discussion bar mounts only topic refs, never deep-read refs", async () => {
  const panel = await source("components/task6/CoachPanel.tsx");
  // 「本次讨论」挂载条的数据源 memo 只做主题两级合并，不含任何深读字段。
  const memo = panel.match(/const discussionAnalysisIds = useMemo\(\(\) => \{[\s\S]*?\}, \[[^\]]+\]\);/);
  assert.ok(memo, "discussionAnalysisIds memo must exist");
  assert.doesNotMatch(memo[0], /deep_read/i);
  // 源码顺序上挂载条在前、@time 链接渲染在后；二者之间不得出现深读消费或 defaultAnalysisRef。
  const barAt = panel.indexOf('aria-label="本次讨论的分析"');
  const linkAt = panel.indexOf("analysisRef={defaultAnalysisRef}");
  assert.ok(barAt !== -1 && linkAt !== -1 && linkAt > barAt, "bar precedes @time-link rendering");
  const barChunk = panel.slice(barAt, linkAt);
  assert.doesNotMatch(barChunk, /deep_read/i);
  assert.doesNotMatch(barChunk, /defaultAnalysisRef/);
});
