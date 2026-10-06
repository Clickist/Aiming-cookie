import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import { test } from "node:test";

// 1006 点点报障：切换会话慢（~0.8s）且文字闪动。CDP 实测定罪两个叠加根因：
// ① rail 点击已 set 选中、router.push 未提交的窗口里，同步 effect 按旧路由
//    把选择弹回旧会话（新→旧→新三连选中），跟着并发三个 GET /v1/sessions/N，
//    切换总时长被最慢的废请求拖住，侧栏高亮/顶栏标题也跟着弹跳；
// ② refresh 在途时旧会话消息原地保留（有意设计，清空更闪），但没有任何
//    "加载中"标注——顶栏标题已切、正文还是旧会话，落位瞬间整表突变。
// 修复＝修A（AppShell 消除选中弹跳，一次切换只发一个 GET）＋修B（refresh
// abort 在途废请求 + 归属错位窗顶部过渡细条）。
const root = path.resolve(import.meta.dirname, "..");

async function source(relativePath: string): Promise<string> {
  return readFile(path.join(root, relativePath), "utf8");
}

test("AppShell does not bounce a rail click back to the stale route", async () => {
  const shell = await source("components/task3/AppShell.tsx");
  // 点击意图标记带「点击时的路由值」：effect 只在路由仍停在前值（=导航在途）
  // 时让位；路由提交后自然收敛，去往别处则放弃标记、路由权威照旧——
  // 返回/前进与 History 深链的回绑语义不受影响。
  assert.match(
    shell,
    /const userSelectedSessionRef = useRef<\{ id: number; prevRoute: number \| null \} \| null>\(null\);/,
  );
  assert.match(
    shell,
    /userSelectedSessionRef\.current = \{ id: Number\(session\.id\), prevRoute: routeSessionId \};/,
  );
  // 在途窗口内：选中===点击 id 且路由仍为前值 → 直接 return，不回绑旧会话。
  const guard = shell.match(
    /const userSelect = userSelectedSessionRef\.current;[\s\S]*?if \(routeSessionId === selectedCoachSessionId\) \{[\s\S]*?userSelectedSessionRef\.current = null;[\s\S]*?\} else if \(\s*userSelect !== null\s*&& selectedCoachSessionId === userSelect\.id\s*&& routeSessionId === userSelect\.prevRoute\s*\) \{[\s\S]*?return;/,
  );
  assert.ok(guard, "rail 点击在途守卫必须存在于路由绑定分支顶部");
  // 路由已追上或去了别处时必须消化标记，不能留到后续导航窗口误让位。
  assert.match(guard[0], /userSelectedSessionRef\.current = null;/);
});

test("CoachPanel refresh aborts stale session fetches and marks the switch window", async () => {
  const panel = await source("components/task6/CoachPanel.tsx");
  // 每次会话分支拉取先取消上一次在途请求：revision 守卫只保证结果不误上屏，
  // 废请求本身仍占连接跑完，切换时长被最慢者拖住（实测三并发最慢 792ms）。
  assert.match(panel, /const refreshAbortRef = useRef<AbortController \| null>\(null\);/);
  const fetchBlock = panel.match(
    /const revision = \+\+refreshRevisionRef\.current;[\s\S]*?await getCoachSession\(sessionId, \{ signal: controller\.signal \}\);/,
  );
  assert.ok(fetchBlock, "会话详情拉取必须带 AbortController signal");
  assert.match(fetchBlock[0], /refreshAbortRef\.current\?\.abort\(\);/);
  // 被新切换 abort 的在途请求不算加载失败，也不能把旧结果上屏。
  assert.match(panel, /if \(controller\.signal\.aborted \|\| revision !== refreshRevisionRef\.current\) return;/);
  assert.match(
    panel,
    /if \(!controller\.signal\.aborted && revision === refreshRevisionRef\.current\) setLoadError\(true\);/,
  );
  // refresh 身份变化（会话再切换）或卸载时同样取消在途拉取。
  assert.match(
    panel,
    /return \(\) => \{\s*if \(pollRef\.current\) clearTimeout\(pollRef\.current\);[\s\S]*?refreshAbortRef\.current\?\.abort\(\);/,
  );
  // 屏上消息归属追踪：三个清空/落位分支都要维护，切换错位窗才识别得出来。
  assert.match(panel, /setMessagesSessionId\("draft"\);/);
  assert.match(panel, /setMessagesSessionId\(sessionId\);/);
  assert.match(panel, /const \[messagesSessionId, setMessagesSessionId\] = useState<number \| "draft" \| null>\(null\);/);
  // 错位窗＝选中会话与屏上内容归属不一致且旧内容还挂着；拉取失败不算加载中
  //（条子撤下、旧内容原地保留），驱动顶部过渡细条。
  const switching = panel.match(
    /const switchingSession =\s*sessionId != null\s*&& messagesSessionId != null\s*&& messagesSessionId !== sessionId\s*&& messages\.length > 0\s*&& !loadError;/,
  );
  assert.ok(switching, "switchingSession 派生必须按归属错位判定");
  assert.match(panel, /\{switchingSession \? <div aria-hidden="true" className="task6-switch-bar" \/> : null\}/);
  assert.match(panel, /aria-busy=\{switchingSession \|\| undefined\}/);
});

test("Session switch progress bar ships with a reduced-motion fallback", async () => {
  const css = await source("components/task6/task6.css");
  assert.match(css, /\.task6-switch-bar \{/);
  assert.match(css, /@keyframes task6-switch-bar-slide \{/);
  const reduced = css.match(/@media \(prefers-reduced-motion: reduce\) \{\s*\.task6-switch-bar::before \{[\s\S]*?\}/);
  assert.ok(reduced, "reduced-motion 下过渡条必须退化为静态细条");
  assert.match(reduced[0], /animation: none;/);
});
