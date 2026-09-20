import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import { test } from "node:test";

// 0919 P2 缺陷（已实锤复现）：发送消息后刷新页面（停止按钮刚出现的窗口内），
// run 彻底从界面消失——没有停止按钮、没有流式、没有"生成中"提示；服务端回合
// 照常跑完、回复已写入 JSONL，用户必须切走再切回才能看到回复。
// 根因：run 状态只活在内存里，刷新后无人知道本会话还有 in-flight 回合，SSE
// 不续订、终态无人收敛。修复＝受理发送把 run_ref 钉进 localStorage，页面加载/
// 会话绑定时回读并以 GET /v1/agent-runs/:ref（queued/running）复接。
const root = path.resolve(import.meta.dirname, "..");

async function source(relativePath: string): Promise<string> {
  return readFile(path.join(root, relativePath), "utf8");
}

test("Coach pins in-flight runs so a reload can reattach them", async () => {
  const panel = await source("components/task6/CoachPanel.tsx");
  // 钉扎落盘/回读/解除三件套来自独立纯逻辑模块（lib/coach-run-resume.ts）。
  assert.match(panel, /pinActiveRunRef/);
  assert.match(panel, /activeRunRefForSession/);
  assert.match(panel, /clearActiveRunRef/);
  assert.match(panel, /from "@\/lib\/coach-run-resume"/);
  // 钉扎以 run 的会话与 ref 为键（两者都从 run 本体取）。
  assert.match(panel, /const activeRunRefKey = run\?\.run_ref \?\? null;/);
  assert.match(panel, /const activeRunSessionId = run\?\.session_id \?\? null;/);
  // 终态解除钉扎：queued/running 才钉，终态清（否则刷新会永远复接一个死 run）；
  // 成功归档会把 run 置回 null，此时同样按「当前会话」解除，不留死钉扎。
  const pinEffect = panel.match(
    /const pinnedRunRef = useRef<\{ sessionId: number; runRef: string \} \| null>\(null\);[\s\S]*?\}, \[activeRunRefKey, activeRunSessionId, activeRunStatus, sessionId\]\);/,
  );
  assert.ok(pinEffect, "run 状态变化时维护钉扎的效果必须存在");
  assert.match(pinEffect[0], /if \(activeRunStatus === "queued" \|\| activeRunStatus === "running"\) \{/);
  assert.match(pinEffect[0], /pinActiveRunRef\(window\.localStorage, activeRunSessionId, activeRunRefKey\);/);
  assert.match(pinEffect[0], /clearActiveRunRef\(window\.localStorage, pinnedRunRef\.current\.sessionId\);/);
  assert.match(pinEffect[0], /pinned && \(sessionId == null \|\| pinned\.sessionId === sessionId\)/);
});

test("Coach reattaches a pinned in-flight run when a session binds", async () => {
  const panel = await source("components/task6/CoachPanel.tsx");
  // 复接点：会话已绑定、非草稿、能力就绪时回读钉扎并 GET run 状态。
  assert.match(
    panel,
    /const pinned = activeRunRefForSession\(window\.localStorage, sessionId\);/,
  );
  assert.match(panel, /const restored = await getCoachAgentRun\(pinned, \{ sessionId \}\);/);
  // queued/running → setRun 复接（既有 liveRunRef 效果据此续订 SSE、终态收敛
  // 与落库消息接管全部复用）；终态 → 解除钉扎 + 刷新消息接管。
  assert.match(
    panel,
    /if \(\["queued", "running"\]\.includes\(restored\.status\)\) \{\s*stickToBottomRef\.current = true;\s*setRun\(restored\);/,
  );
  assert.match(panel, /clearActiveRunRef\(window\.localStorage, sessionId\);/);
  // 复接后仍然走同一条 SSE/轮询链路，不需要第二套收敛逻辑。
  assert.match(panel, /const liveRunRef = run && \["queued", "running"\]\.includes\(run\.status\) \? run\.run_ref : null;/);
  // 复接 GET 在飞时用户可能已发出新回合（钉扎被发送路径覆写成新 run）：
  // 落地后必须先复查钉扎现值，被覆写就放弃复接——否则旧 run 的 setRun 会把
  // 新回合顶出 run，变成无 SSE、无停止按钮的孤儿（石沉大海变体）。
  const guardIdx = panel.indexOf("if (activeRunRefForSession(window.localStorage, sessionId) !== pinned) return;");
  assert.notEqual(guardIdx, -1, "复接落地后必须有「钉扎现值被新回合覆写即放弃」的守卫");
  assert.ok(guardIdx > panel.indexOf("const restored = await getCoachAgentRun"), "守卫必须在 GET 落地之后");
  assert.ok(guardIdx < panel.indexOf("resumedRunKeyRef.current = resumeKey"), "守卫必须在置位/分支之前");
  // 404（run 已不存在，如 sidecar 重启）：解除钉扎交落库接管；其余错误保留
  // 钉扎留给下次会话绑定重试——两个分支都不能少。
  assert.match(panel, /if \(isMissingRunError\(error\)\) \{\s*clearActiveRunRef\(window\.localStorage, sessionId\);\s*void refresh\(\);/);
});

test("Coach pins the run before the handover window can lose it", async () => {
  const panel = await source("components/task6/CoachPanel.tsx");
  // 首条发送（draft 键 → session:N 迁移）与自动开讲两条来源都经 setRun 入位，
  // 钉扎挂在 run 状态上即天然覆盖两者；draft 会话不参与复接（还没有会话可绑）。
  const resumeEffect = panel.match(/const resumedRunKeyRef = useRef<string \| null>\(null\);[\s\S]*?\}, \[[^\]]+\]\);/);
  assert.ok(resumeEffect, "复接效果必须存在");
  // 草稿会话不参与复接（还没有会话可绑）；能力未就绪也不打无谓的请求。
  assert.match(resumeEffect[0], /if \(capability !== "ready" \|\| draftSession \|\| sessionId == null\) return undefined;/);
  assert.match(resumeEffect[0], /resumedRunKeyRef\.current === resumeKey/);
});

test("AppShell binds the route session without waiting for the slow session list", async () => {
  const shell = await source("components/task3/AppShell.tsx");
  // 0919 P2 的复接时机门：会话绑定若等 listCoachSessions 追平（大会话量下实测
  // 12–32s），刷新后整段窗口都落在空首页上——在途 run 没有指示、消息不显示。
  // 路由是用户意图的权威表达，直接绑；列表只补标题/摘要等元数据。
  assert.match(shell, /const coachSessionsLoadedRef = useRef\(false\);/);
  assert.match(shell, /coachSessionsLoadedRef\.current = true;/);
  assert.match(
    shell,
    /if \(routeSessionId !== null\) \{\s*if \(coachSessions\.some\(\(session\) => Number\(session\.id\) === routeSessionId\)\s*\|\| !coachSessionsLoadedRef\.current\) \{\s*setSelectedCoachSessionId\(routeSessionId\);\s*return;\s*\}/,
  );
  // 列表已加载且确认无此会话（死 id）：回空选择，绝不落 lastViewed/primary 兜底
  //（§12.5：回落会把顶栏/消息区闪成旧会话）。
  assert.match(shell, /setSelectedCoachSessionId\(\(current\) => \(current === routeSessionId \? null : current\)\);/);
  assert.doesNotMatch(shell, /if \(routeSessionId !== null\) return;/);
  // 交接窗钉扎与 lastViewed/primary 兜底必须原样保留：前者保首条发送不闪旧会话，
  // 后者只在路由无 sessionId 时生效（回落闪旧会话的根因不在路由绑定这条路径上）。
  assert.match(shell, /const pendingBind = pendingBindSessionIdRef\.current;/);
  assert.match(shell, /readLastCoachSessionId\(window\.localStorage\)/);
  assert.match(shell, /const primary = coachSessions\.find\(\(session\) => session\.kind === "primary"\)/);
});
