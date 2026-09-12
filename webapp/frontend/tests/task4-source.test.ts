import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import { test } from "node:test";

const root = path.resolve(import.meta.dirname, "..");
async function source(relativePath: string): Promise<string> {
  return readFile(path.join(root, relativePath), "utf8");
}

test("history page is a light list and does not render full result payloads or benchmark UI", async () => {
  const value = await source("components/task4/HistoryClient.tsx");
  assert.match(value, /getHistorySessions/);
  assert.match(value, /runIssueText/);
  // 摘要 Dialog 已移除：不再按需加载完整分析投影。
  assert.doesNotMatch(value, /getHistoryAnalysisDetail|detail\.history|visual_replay/);
  assert.doesNotMatch(value, /Benchmark|Plotly|result\.deterministic|video_url/);
});

test("history preserves stale rows when refresh fails", async () => {
  const value = await source("components/task4/HistoryClient.tsx");
  assert.match(value, /refreshing|unavailable|旧内容|保留/);
});

test("history renders unavailable Run sources as semantic notices", async () => {
  const value = await source("components/task4/HistoryClient.tsx");
  assert.match(value, /function RunSectionState/);
  assert.match(value, /<Notice tone="warning" title={title}>/);
  assert.match(value, /runDiscovery === "browser_unavailable" \|\| runDiscovery === "service_unavailable"/);
});

test("history loading and empty states use the local panel treatment", async () => {
  const client = await source("components/task4/HistoryClient.tsx");
  const styles = await source("components/task4/task4.css");
  // 区块共用空态（RunSectionState）+ 分析记录空态 + watcher 引导卡片两张
  // + 场景名过滤无结果空态三处（待分析/分析/训练记录），共 7 处。
  assert.equal(client.match(/className="task4-panel task4-state-panel"/g)?.length, 7);
  assert.match(styles, /\.task4-state-panel\s*{[\s\S]*min-height:\s*88px;[\s\S]*padding:\s*var\(--space-4\) var\(--space-5\);/);
});

test("history keeps refresh and Coach return without batch attach", async () => {
  const client = await source("components/task4/HistoryClient.tsx");
  const styles = await source("components/task4/task4.css");
  // 0911 点点第二批：刷新文字按钮换成「更新于 N 前」状态行，点击仍触发 loadHistory。
  assert.match(client, /className="task4-refresh-status"/);
  assert.match(client, /更新于 \$\{relativeUpdatedAt/);
  assert.match(client, /onClick=\{\(\) => void loadHistory\(\)\}/);
  assert.doesNotMatch(client, /variant="ghost">刷新</);
  assert.doesNotMatch(client, /新建分析/);
  assert.doesNotMatch(client, /attachCoachContext|publishCoachIntent|batch-analysis/);
  assert.doesNotMatch(styles, /@media \(min-width: 840px\) and \(max-width: 1159px\)[\s\S]*\.task3-workspace\[data-coach-open="true"\] \.task4-page-head[\s\S]*width:\s*calc\(100% - var\(--task3-coach-width, 360px\)\);[\s\S]*flex-wrap:\s*wrap;/);
});

test("history hands multi-selected runs and analyses to the Coach via the pending-intent draft", async () => {
  const value = await source("components/task4/HistoryClient.tsx");
  // 待分析、训练记录、分析记录共用选择集（上限 5）
  assert.match(value, /const MAX_SELECTED_RUNS = 5;/);
  assert.match(value, /selectedCount >= MAX_SELECTED_RUNS/);
  assert.match(value, /const selectedCount = selectedRunIds\.length \+ selectedAnalysisIds\.length;/);
  // 无任何可用 tier 的训练记录禁用勾选；未完成的分析不可选
  assert.match(value, /disabled=\{run\.supported_input_modes\.length === 0\}/);
  assert.match(value, /disabled=\{session\.status !== "done"\}/);
  // 「让 Coach 分析」拼话术交给 Coach 输入框，用户发送后逐条处理
  assert.match(value, /buildCoachAnalysisDraft\(\{/);
  assert.match(value, /sessionStorage\.setItem\(COACH_PENDING_INTENT_KEY/);
  assert.match(value, /让 Coach 分析/);
  // 详情抽屉与摘要弹窗已移除：入口按钮不再存在
  assert.doesNotMatch(value, /查看 Run/);
  assert.doesNotMatch(value, /查看摘要/);
  assert.doesNotMatch(value, /RunInspector/);
  assert.doesNotMatch(value, /getHistoryAnalysisDetail/);
});

test("history sections order pending first, analyses second, run records last", async () => {
  const value = await source("components/task4/HistoryClient.tsx");
  const pendingAt = value.indexOf('id="pending-title"');
  const analysisAt = value.indexOf('id="analysis-title"');
  const runsAt = value.indexOf('id="runs-title"');
  assert.ok(pendingAt !== -1 && analysisAt !== -1 && runsAt !== -1, "all three sections must exist");
  assert.ok(pendingAt < analysisAt && analysisAt < runsAt, "section order must be pending → analysis → runs");
});

test("history never promotes an analysis summary into the scenario title", async () => {
  const value = await source("components/task4/HistoryClient.tsx");
  assert.match(value, /presentRecordLabel\(\{[\s\S]*scenario: session\.scenario/);
  assert.doesNotMatch(value, /scenario:\s*session\.summary_label/);
});

test("History keeps Analysis consumption local", async () => {
  const value = await source("components/task4/HistoryClient.tsx");
  assert.doesNotMatch(value, /analysisHref\(/);
  assert.doesNotMatch(value, /href=\{[^}]*\/analysis/);
  assert.doesNotMatch(value, /onLoadDetail/);
});

test("History polls incomplete runs and desktop empty states, but not browser empty states", async () => {
  const value = await source("components/task4/HistoryClient.tsx");
  // 终态集合之外（discovered/pending/capturing/finalizing/retryable…）证据还会变化。
  assert.match(value, /new Set\(\["finalized", "source_unavailable", "unavailable"\]\)/);
  assert.match(value, /isDesktopRuntime\(\) && \(runs\.length === 0 \|\| runs\.some\(\(run\) => !RUN_FINALIZED_STATES\.has\(run\.finalization_state\)\)\)/);
  assert.match(value, /if \(!shouldPollHistory\) return undefined;/);
  // 「更新于 N 前」的显示刷新搭既有轮询便车（nowTick），不新增定时器。
  assert.match(value, /setInterval\(\(\) => \{ setNowTick\(Date\.now\(\)\); void loadHistory\(\); \}, 5000\)/);
  assert.match(value, /clearInterval\(timer\)/);
});

test("history distinguishes desktop empty states with watcher guidance instead of new timers", async () => {
  const value = await source("components/task4/HistoryClient.tsx");
  // watcher 健康搭既有 loadHistory 轮询的便车，不得引入新的定时器。
  assert.match(value, /getKovaaKLocalDirectories/);
  assert.doesNotMatch(value, /setInterval\((?!.*loadHistory)/);
  // 只有桌面版且全部列表为空、且判定为问题态时才显示；未知/正在摄取不提示。
  assert.match(
    value,
    /runDiscovery === "available" && allListsEmpty && \(watcherStatus === "no_candidates" \|\| watcherStatus === "not_exporting"\)/,
  );
  // 状态 a：未找到目录 → 引导去 设置 → KovaaK 本地目录。
  assert.match(value, /未找到你的 KovaaK 训练数据/);
  assert.match(value, /settings#kovaak-directories/);
  // 状态 b： KovaaK 的实际选项是 Challenge Completion（不存在 "Always"）。
  assert.match(value, /KovaaK 未在导出训练数据/);
  assert.match(value, /请在 KovaaK 中打开 设置 → 其他 → 统计数据输出，选择 Challenge Completion，然后完成一局挑战。/);
  assert.doesNotMatch(value, /Always/);
});

test("run rows show match time from training_at and keep titles to scenario names (0911 审计 §12.6 + 0911 点点第一档)", async () => {
  const value = await source("components/task4/HistoryClient.tsx");
  const contracts = await source("lib/contracts.ts");
  // 训练时间优先 training_at（source_key 解析），created_at 只是兜底。
  assert.match(value, /const trainingAt = run\.training_at \?\? run\.created_at;/);
  // 行标题瘦身为纯场景名（0911 点点第一档）：时间只在副行出现一次，标题
  // 不再裸露 ISO 时间戳；presentRecordLabel 默认行为保持不变，titleOnly
  // 只供历史页标题，其它调用方（Coach 话术、任务卡）不受影响。
  assert.match(value, /presentRecordLabel\(\{ scenario: run\.scenario, titleOnly: true \}\)/);
  assert.match(value, /presentRecordLabel\(\{ scenario: session\.scenario, titleOnly: true \}\)/);
  assert.match(contracts, /if \(input\.titleOnly\) return scenario;/);
  // 0911 点点五轮：分析行单行化——摘要/双时间收敛进整行悬停提示，无内容时无 title。
  assert.match(value, /session\.summary_label \? `摘要：\$\{session\.summary_label\}` : null/);
  // telemetry_multimodal 与 multimodal 界面统一叫「多源模式」。
  assert.match(value, /telemetry_multimodal: "多源模式"/);
});

test("history merges the drag band and page head into one 56px solid topbar (0911 点点第三批 A + 第四轮)", async () => {
  const client = await source("components/task4/HistoryClient.tsx");
  const styles = await source("components/task4/task4.css");
  // 独立透明拖拽带取消：顶栏自身就是那条带（实底 56px）。
  assert.doesNotMatch(client, /task4-topbar-band/);
  assert.doesNotMatch(styles, /task4-topbar-band/);
  assert.ok(client.includes('className="task4-page-head" onMouseDown={startWindowDraggingOnBackground}'));
  // 第四轮：顶栏升到 56px（内容离窗顶远一点），内容垂直居中。
  assert.match(styles, /\.task4-page-head\s*\{[^}]*height:\s*56px/);
  assert.match(styles, /\.task4-page-head\s*\{[^}]*position:\s*sticky/);
  assert.match(styles, /\.task4-page-head\s*\{[^}]*background:\s*var\(--surface-container\)/);
  // 0911 点点：顶栏整组居中后右端无内容，150px 三键让位 padding 退役。
  assert.doesNotMatch(styles, /\.task4-page-head\s*\{[^}]*padding-inline-end:\s*150px/);
  // 左组紧凑排列：返回键 → 标题 → 场景名筛选框 →「更新于 N 前」→ 胶囊按钮。
  assert.match(client, /className="task4-topbar-left"/);
  const leftAt = client.indexOf('className="task4-topbar-left"');
  const backAt = client.indexOf('label="返回 Coach"');
  const titleAt = client.indexOf('task4-page-title">历史');
  const filterAt = client.indexOf('className="task4-filter-input"');
  const refreshAt = client.indexOf('className="task4-refresh-status"');
  const pillAt = client.indexOf('className="task4-coach-pill"');
  assert.ok(leftAt !== -1 && backAt !== -1 && titleAt !== -1 && filterAt !== -1 && refreshAt !== -1 && pillAt !== -1, "topbar left group must exist");
  assert.ok(leftAt < backAt && backAt < titleAt && titleAt < filterAt && filterAt < refreshAt && refreshAt < pillAt, "left group order must be back → title → filter → refresh → coach pill");
  // 标题降为顶栏级文字（600 字重 text-title/ui，不再是 20px display）。
  assert.match(styles, /\.task4-page-title\s*\{[^}]*font:\s*600 var\(--text-title\)\/1\.2 var\(--font-ui\)/);
  // 「让 Coach 分析」常驻胶囊（点点第四轮）：未勾选 disabled 灰胶囊；勾选后
  // primary 橙 + 计数；旧右端条件渲染组退役。
  assert.match(client, /disabled=\{selectedCount === 0\}/);
  assert.match(client, /data-active=\{selectedCount > 0 \|\| undefined\}/);
  assert.match(client, /\{selectedCount > 0 \? `让 Coach 分析（\$\{selectedCount\}）` : "让 Coach 分析"\}/);
  assert.doesNotMatch(client, /task4-page-actions/);
  assert.doesNotMatch(styles, /task4-page-actions/);
  assert.match(styles, /\.task4-coach-pill\s*\{[^}]*height:\s*30px/);
  assert.match(styles, /\.task4-coach-pill\s*\{[^}]*border-radius:\s*999px/);
  assert.match(styles, /\.task4-coach-pill:disabled\s*\{[^}]*cursor:\s*not-allowed/);
  assert.match(styles, /\.task4-coach-pill\[data-active="true"\]\s*\{[^}]*background:\s*var\(--primary\)/);
});

test("history content column narrows to 520px while the topbar stays full width (0911 点点第四轮)", async () => {
  const client = await source("components/task4/HistoryClient.tsx");
  const styles = await source("components/task4/task4.css");
  // 0911 点点：滚动容器全宽（滚动条贴窗口右缘、从顶栏下填满），520px 列在 .task4-col。
  assert.match(styles, /\.task4-col\s*\{[^}]*max-width:\s*580px/);
  assert.match(styles, /\.task4-col\s*\{[^}]*margin-inline:\s*auto/);
  assert.doesNotMatch(styles, /\.task4-page\s*\{[^}]*max-width/);
  // 顶栏移出滚动列：主分支顶栏先开（内含刷新状态行），滚动列在顶栏闭合后才
  // 打开——顶栏全宽，滚动条仍贴 520px 列右缘。
  const refreshAt = client.indexOf('className="task4-refresh-status"');
  const headAt = client.lastIndexOf('className="task4-page-head" onMouseDown={startWindowDraggingOnBackground}');
  const pageAt = client.indexOf('className="task4-page"', refreshAt);
  assert.ok(refreshAt !== -1 && headAt !== -1 && pageAt !== -1, "topbar and scroll column must exist");
  assert.ok(headAt < refreshAt && refreshAt < pageAt, "topbar must close before the scroll column opens (head outside .task4-page)");
});

test("run and analysis rows return to the wireframe 9px vertical rhythm (0911 点点第四轮)", async () => {
  const styles = await source("components/task4/task4.css");
  assert.match(styles, /\.task4-rowline\s*\{[^}]*padding:\s*9px var\(--space-4\)/);
  // Run 行不再单独覆盖 padding（随 .task4-rowline 统一），行视觉高约 46px。
  assert.doesNotMatch(styles, /\.task4-run-row\s*\{[^}]*padding/);
});

test("analysis records section folds like run records, expanded by default with persisted state (0911 点点第四轮)", async () => {
  const value = await source("components/task4/HistoryClient.tsx");
  // key 命名跟随 aiming-cookie.* 惯例；默认展开，存储值 "1" = 收起。
  assert.match(value, /"aiming-cookie\.ui\.history-analysis-collapsed"/);
  assert.match(value, /const \[analysisRecordsOpen, setAnalysisRecordsOpen\] = useState\(true\)/);
  assert.match(value, /window\.localStorage\.getItem\(ANALYSIS_COLLAPSED_KEY\) !== "1"/);
  assert.match(value, /window\.localStorage\.setItem\(ANALYSIS_COLLAPSED_KEY/);
  // 折叠头样式同「训练记录」：区块名 + 条数 + 箭头，整头可点 + aria-expanded。
  assert.match(value, /className="task4-sec-head task4-sec-collapsible" onClick=\{toggleAnalysisRecords\}/);
  assert.match(value, /aria-expanded=\{analysisRecordsOpen\}/);
  assert.match(value, /aria-label=\{analysisRecordsOpen \? "收起分析记录" : "展开分析记录"\}/);
});

test("run rows collapse to a single line with score, time and issue columns (0911 点点第三批 B)", async () => {
  const client = await source("components/task4/HistoryClient.tsx");
  const styles = await source("components/task4/task4.css");
  // 修饰类区分 Run 行与 Analysis 行，分割线互不污染。
  assert.match(client, /className="task4-rowline task4-run-row"/);
  assert.match(client, /className="task4-rowline task4-analysis-row"/);
  // 行内时间只留时分（日期由组头表达），去掉「训练时间：」前缀。
  assert.match(client, /function trainingTimeLabel/);
  assert.doesNotMatch(client, /训练时间：/);
  // 分数渲染在时间左侧；score 缺失/null 不渲染不报错。
  assert.match(client, /\{run\.score != null \? <span className="task4-run-score">\{formatScore\(run\.score\)\}<\/span> : null\}/);
  assert.match(client, /className="task4-run-time"/);
  // 分割线只挂在 Analysis 行之间；Run 行组内不画线（组间靠组头 margin）。
  assert.match(styles, /\.task4-analysis-row \+ \.task4-analysis-row\s*\{[^}]*border-top:\s*1px solid var\(--outline-variant\)/);
  assert.doesNotMatch(styles, /\.task4-rowline \+ \.task4-rowline/);
  // 组头加重：「9月2日 · N条」格式，600 字重主文字色，组间 margin 分层。
  assert.match(client, /\{group\.label\} · \{group\.items\.length\}条/);
  assert.match(styles, /\.task4-day-label\s*\{[^}]*font:\s*600 var\(--text-caption\)/);
  assert.match(styles, /\.task4-day-label\s*\{[^}]*color:\s*var\(--on-surface\)/);
  assert.match(styles, /\.task4-day-label\s*\{[^}]*margin-top:\s*var\(--space-3\)/);
  // ✓ 证据齐全微字退役。
  assert.doesNotMatch(client, /证据齐全/);
});

test("run lists show the first 5 entries with an expand toggle persisted to localStorage (0911 点点第三批 D)", async () => {
  const value = await source("components/task4/HistoryClient.tsx");
  // 默认只显示前 5 条；底部「显示全部 N 条 / 收起」；状态存 localStorage。
  assert.match(value, /const MAX_VISIBLE_RUNS = 5;/);
  assert.match(value, /"aiming-cookie\.ui\.history-run-expand"/);
  assert.match(value, /slice\(0, MAX_VISIBLE_RUNS\)/);
  assert.match(value, /显示全部 \$\{filteredSections\.pendingRuns\.length\} 条/);
  assert.match(value, /显示全部 \$\{filteredSections\.runRecords\.length\} 条/);
  assert.match(value, /toggleRunExpand\("pending"\)/);
  assert.match(value, /toggleRunExpand\("records"\)/);
  assert.match(value, /toggleRunExpand\("analysis"\)/);
  assert.match(value, /显示全部 \$\{filteredSections\.analysisRecords\.length\} 条/);
  // 勾选集合跨折叠保留：计数与话术仍取全量数据，不取可见切片。
  assert.match(value, /runs\.filter\(\(run\) => selectedRunIds\.includes\(run\.id\)\)/);
});

test("run rows surface the backend score with thousand separators (0911 点点第三批 E)", async () => {
  const value = await source("components/task4/HistoryClient.tsx");
  const types = await source("lib/types.ts");
  const backend = await source("../backend/kovaak_run_projection.py");
  // 类型透出可选 score；前端千分位格式化，最多 1 位显示小数。
  assert.match(types, /score\?: number \| null;/);
  assert.match(value, /function formatScore/);
  assert.match(value, /toLocaleString\("en-US", \{ maximumFractionDigits: 1 \}\)/);
  // 后端从 Stats summary 块 Score 投影，缺失为 None 不硬造。
  assert.match(backend, /def _run_score/);
  assert.match(backend, /"score": _run_score\(run\),/);
});

test("abnormal runs show a red exclamation mark with a human-readable hover title (0911 点点第三批 F)", async () => {
  const client = await source("components/task4/HistoryClient.tsx");
  const icons = await source("ui/icons.tsx");
  const styles = await source("components/task4/task4.css");
  // 圆圈感叹号图标（Icon 包装模式）；行内 14px、红色 var(--error)。
  assert.match(icons, /export function IconAlertCircle/);
  assert.match(client, /<IconAlertCircle height=\{14\} width=\{14\} \/>/);
  assert.match(styles, /\.task4-run-issue\s*\{[^}]*color:\s*var\(--error\)/);
  // 触发条件：limitations 非空，或来源不可用终态；悬停 title + aria-label 人话。
  assert.match(client, /function runIssueText/);
  assert.match(client, /run\.finalization_state === "source_unavailable" \|\| run\.finalization_state === "unavailable"/);
  assert.match(client, /训练来源已不可用/);
  assert.match(client, /run\.limitations\.map\(limitationLabel\)/);
  assert.match(client, /title=\{issue\}/);
  assert.match(client, /aria-label=\{`训练异常：\$\{issue\}`\}/);
  // 证据 chips 墙与「证据不完整」「来源不可用」badge 同步退役；「已分析」保留。
  assert.doesNotMatch(client, /task4-ev|EvidenceChip|evidenceChipState|证据不完整/);
  assert.doesNotMatch(styles, /task4-ev/);
  assert.match(client, /已分析/);
  assert.doesNotMatch(client, /task4-badge-warn">来源不可用|task4-badge-err">证据不完整/);
});

test("history selection notice renders inside the sticky page head (0911 点点第二批 5A)", async () => {
  const value = await source("components/task4/HistoryClient.tsx");
  const styles = await source("components/task4/task4.css");
  // selectionNotice 随页头吸顶常驻视口，不再挂在「待分析训练」区块标题下。
  const headAt = value.indexOf('className="task4-page-head" onMouseDown={startWindowDraggingOnBackground}');
  const noticeAt = value.indexOf('<Notice className="task4-head-notice" tone="info">{selectionNotice}</Notice>');
  const pendingAt = value.indexOf('id="pending-title"');
  assert.ok(headAt !== -1 && noticeAt !== -1 && pendingAt !== -1, "page head, notice and pending section must exist");
  assert.ok(headAt < noticeAt && noticeAt < pendingAt, "notice must live inside the page head, before the sections");
  assert.doesNotMatch(value, /<Notice tone="info">\{selectionNotice\}<\/Notice>/);
  // 顶栏化后改为挂载式浮层：绝对定位在 56px 顶栏下缘，随顶栏常驻视口。
  assert.match(styles, /\.task4-head-notice\s*\{[^}]*position:\s*absolute/);
  assert.match(styles, /\.task4-head-notice\s*\{[^}]*top:\s*calc\(100% \+ 14px\)/);
});

test("history rows toggle from the whole row with a custom drawn checkbox (0911 点点第二批 6+10)", async () => {
  const value = await source("components/task4/HistoryClient.tsx");
  const styles = await source("components/task4/task4.css");
  // 整行可点（RunRow 与 AnalysisRow），disabled 行不接管点击。
  assert.match(value, /data-interactive=\{interactive \|\| undefined\}/);
  assert.match(value, /onClick=\{interactive && onToggle \? \(\) => onToggle\(run\) : undefined\}/);
  assert.match(value, /onClick=\{interactive && onToggle \? \(\) => onToggle\(session\) : undefined\}/);
  // 勾选框自身点击止于 row-actions：onChange 已切换一次，冒泡到整行会二次切换。
  assert.match(value, /className="task4-row-actions" onClick=\{\(event\) => event\.stopPropagation\(\)\}/);
  // hover 高亮跟随面板内角（面板 overflow:hidden 统一裁剪）。
  assert.match(styles, /\.task4-rowline\[data-interactive="true"\]:hover\s*\{[^}]*background:\s*var\(--surface-container-low\)/);
  assert.match(styles, /\.task4-panel\s*\{[^}]*overflow:\s*hidden/);
  // 自绘勾选点（0911 点点第三批 C）：原生 input 保留语义与键盘，appearance:none
  // 换皮——14px 正圆；未选中描边空心圆，选中 primary 实心圆（不加对勾）。
  assert.match(value, /className="task4-check"/);
  assert.match(styles, /\.task4-check\s*\{[^}]*appearance:\s*none/);
  assert.match(styles, /\.task4-check\s*\{[^}]*width:\s*14px/);
  assert.match(styles, /\.task4-check\s*\{[^}]*border-radius:\s*50%/);
  assert.match(styles, /\.task4-check\s*\{[^}]*border:\s*1\.5px solid var\(--outline\)/);
  assert.match(styles, /\.task4-check:checked\s*\{[^}]*background-color:\s*var\(--primary\)/);
  assert.doesNotMatch(styles, /\.task4-check:checked\s*\{[^}]*background-image/);
  assert.match(styles, /\.task4-check:focus-visible\s*\{[^}]*outline:\s*2px solid var\(--primary\)/);
  assert.match(styles, /\.task4-check:disabled\s*\{[^}]*cursor:\s*default/);
  // 顺手清理旧死样式：.task4-sel-dot 系列（含媒体查询引用）全部移除。
  assert.doesNotMatch(styles, /task4-sel-dot|task4-row-select/);
});

test("history filters runs and sessions by scenario name (0911 点点第二批 7a)", async () => {
  const value = await source("components/task4/HistoryClient.tsx");
  assert.match(value, /placeholder="按场景名筛选…"/);
  assert.match(value, /const normalizedFilter = scenarioFilter\.trim\(\)\.toLocaleLowerCase\(\)/);
  assert.match(value, /toLocaleLowerCase\(\)\.includes\(normalizedFilter\)/);
  // 过滤只影响渲染：勾选与「让 Coach 分析」话术仍取全量数据。
  assert.match(value, /runs\.filter\(\(run\) => selectedRunIds\.includes\(run\.id\)\)/);
  // 过滤后某区块无结果显示专用空态。
  assert.match(value, /title="没有匹配的记录"/);
});

test("history groups run and analysis lists by day (0911 点点第二批 7b + 五轮对齐)", async () => {
  const value = await source("components/task4/HistoryClient.tsx");
  // 纯函数放 HistoryClient 内：今天/昨天/M月D日 与 formatHistoryDate 文案同源。
  assert.match(value, /function trainingDayLabel/);
  assert.match(value, /function groupByDay/);
  assert.match(value, /className="task4-day-label"/);
  // 0911 点点五轮：三个区块全部按日分组（1 处定义 + 3 处调用）。
  assert.equal(value.match(/groupByDay\(/g)?.length, 3);
  assert.match(value, /今天/);
  assert.match(value, /昨天/);
});

test("history run records section collapses by default with persisted state (0911 点点第二批 8)", async () => {
  const value = await source("components/task4/HistoryClient.tsx");
  // key 命名跟随 aiming-cookie.* 惯例；默认折叠，展开状态持久化。
  assert.match(value, /"aiming-cookie\.ui\.history-runs-collapsed"/);
  assert.match(value, /const \[runRecordsOpen, setRunRecordsOpen\] = useState\(false\)/);
  assert.match(value, /window\.localStorage\.getItem\(RUNS_COLLAPSED_KEY\) === "1"/);
  assert.match(value, /window\.localStorage\.setItem\(RUNS_COLLAPSED_KEY/);
  // 折叠切换有键盘可达的箭头按钮（aria-expanded），区块头整体也可点。
  assert.match(value, /aria-expanded=\{runRecordsOpen\}/);
  assert.match(value, /className="task4-sec-head task4-sec-collapsible" onClick=\{toggleRunRecords\}/);
});

test("history sticky page head fades rows out at its lower edge (0911 点点第二批 11)", async () => {
  const styles = await source("components/task4/task4.css");
  assert.match(styles, /\.task4-page-head::after\s*\{[^}]*linear-gradient\(to bottom, var\(--surface-container\), transparent\)/);
  assert.match(styles, /\.task4-page-head::after\s*\{[^}]*pointer-events:\s*none/);
});

test("history updated-at status row avoids layout jitter (0911 点点第二批 12)", async () => {
  const value = await source("components/task4/HistoryClient.tsx");
  const styles = await source("components/task4/task4.css");
  // 时间基点 = 最近一次成功/部分成功读取完成时刻；粒度 秒/分钟/小时。
  assert.match(value, /function relativeUpdatedAt/);
  assert.match(value, /setLastLoadedAt\(loadedAt\)/);
  assert.match(value, /\$\{Math\.floor\(minutes \/ 60\)\} 小时前/);
  // 防抖动：文字固定 min-width，主按钮位置纹丝不动。
  assert.match(styles, /\.task4-refresh-text\s*\{[^}]*min-width:\s*96px/);
  assert.match(styles, /\.task4-refresh-text\s*\{[^}]*white-space:\s*nowrap/);
  // 轮询进行中箭头旋转；陈旧超 2 分钟转轻提醒色；减少动态偏好下关闭动画。
  assert.match(styles, /\.task4-refresh-icon\[data-loading="true"\]\s*\{[^}]*animation:\s*task4-refresh-spin/);
  assert.match(styles, /\.task4-refresh-status\[data-stale="true"\] \.task4-refresh-text\s*\{[^}]*color:\s*var\(--event-peak\)/);
  assert.match(styles, /@media \(prefers-reduced-motion: reduce\)\s*\{[\s\S]*?\.task4-refresh-icon\[data-loading="true"\]\s*\{[^}]*animation:\s*none/);
  assert.match(value, /data-stale=\{updatedStale \|\| undefined\}/);
  assert.match(value, /aria-label=\{lastLoadedAt === null \? "刷新（正在读取）" : `刷新（上次更新 \$\{relativeUpdatedAt\(nowTick, lastLoadedAt\)\}）`\}/);
});
