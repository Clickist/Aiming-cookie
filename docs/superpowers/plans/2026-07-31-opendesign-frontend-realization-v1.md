# OpenDesign Frontend Realization v1 Implementation Plan

> **Status: active; Task 8 blocked.** 点点于 2026-07-31 确认不增加成绩分类 Tab，并要求按已完成的 OpenDesign 交接继续推进、使用 Terra agents 处理可并行的独立切片，由根会话统一验收。Task 1-7 与前端/真实 WebView 验收已完成，但真实 Tauri 启动暴露 `get_or_create_primary_thread` 并发唯一约束错误；该修复超出 Task 8 Allowed files，因此按 Stop rule 暂不归档旧交接、不标 completed。每个 Task 仍须遵守本计划的 Allowed files、Tests first、冻结决策与 Stop rule；不提交、不推送。
>
> **For executors:** use the repository Code workflow and execute one bounded Task at a time. OpenDesign HTML is visual reference, not a data or product contract.

**Goal:** 将已经确认的深色主题、KovaaK 连接与成绩、Switching/Tracking/Flicking Data 呈现，以及 Coach“当前训练”落实到正式 Windows Desktop 前端，同时保持现有隐私、证据和 Training Plan 边界。

**Architecture:** 复用现有 Next.js/React 应用、FastAPI owner-scoped routes、SQLite stores、Analysis evidence artifact、managed video seek 和 Coach confirmation。新增能力只通过版本化、白名单化的只读投影进入前端；不让页面读取 artifact、内部 ref、坐标、Raw trace 或 Provider secret。视觉值先回写 `DESIGN-cursor.md`，再由唯一 executable token 入口消费。

**Tech stack:** Next.js 16、React 19、TypeScript、FastAPI/Pydantic、SQLite、Node test runner、pytest、Playwright、Tauri 2。

---

## Frozen decisions

- KovaaK 成绩使用一张可扫描列表。课程大类只作轻量分组；Easier/Medium 只出现在进度与列表内分组文案中，不增加 Tab、路由、导航或独立 Benchmark 页面。
- 已连接 Steam ID 只在本机 owner scope 保存，用于用户主动刷新；任何响应和界面都不回显 ID/URL，也不发送给 Coach Provider。OpenDesign 旧稿中“不会保存 Steam ID”的句子不得进入正式实现。
- 刷新失败保留上次成功快照；前端不得通过缓存或假数据伪造这一语义。
- `static_clicking` 的逐 Flick 呈现只适用于 input-native `native_flicking.v1`。旧 video-fallback 没有逐 Flick row，必须明确 unavailable。
- Switching 只呈现 Stats-kill-bounded 的 transition/acquire/settle/path 事实；不呈现或暗示目标选择、第一枪、首次伤害、持续目标身份或重新进入。
- Tracking 的 change-response、loss 和 reacquisition 都是观测时序，不命名为“人的反应时间”。
- “让 Coach 看看”“我练完了”“只完成一部分”“这项不太顺”“我想调整计划”等 UI 动作只把意图带入现有 Coach 对话/confirmation，不直接写执行、复测或计划修改。
- 不修改 analyzer、Registry、Advice、阈值、Benchmark store、Training Plan store schema、Provider contract、导航或产品范围。
- 不删除 OpenDesign 命名空间内的 HTML/preview；仓库内过期交接只在 Task 8 归档，且保留历史。

## Success criteria

1. 新 dark token 与 `DESIGN-cursor.md`、`ui/tokens.ts` 和主题合同测试逐值一致，正文/状态/事件色通过既有 AA Gate。
2. Onboarding 与 Settings 共用真实 KovaaK 连接能力；单一成绩列表无阶段 Tab，状态与失败保留语义由 API 证明。
3. 三个专项 Data 呈现只消费 `frontend_analysis_family_data.v1` 白名单投影，逐行可在受管视频可用时定位，缺失时诚实降级。
4. Coach 侧栏能只读展示 owner 当前训练；无计划、暂停、待确认、复测完成、Provider 不可用和窄窗均有确定状态。
5. focused tests、type-check、build、Playwright 截图/a11y 与真实 Tauri WebView smoke 通过；自动化与 release/field Gate 分开报告。
6. 过期 OpenDesign handoff 与已完成的旧 presentation plan 已归档，索引与 active contracts 不再声称 executable token 缺失。

## Task 1 - Adopt the approved dark token palette

### Allowed files

- `DESIGN-cursor.md`
- `docs/design-system.md`
- `webapp/frontend/ui/tokens.ts`
- `webapp/frontend/tests/task2-theme.test.ts`

### Tests first

1. 在主题合同测试中逐值冻结 OpenDesign 已交付的 dark core roles：background/surface ladder/text/primary/tertiary/error/outline/event colors。
2. 保留现有完整 token key 集，并继续执行 light/dark key parity、visual contract parity 和正文对比度检查。
3. 将 `surface-dim/bright/variant` 映射到同一 dark surface ladder；未由新稿改变的 secondary/fixed/inverse roles 保持原值。

### Implementation

- 将 dark `background` 改为 `#141413`，`surface` 改为 `#1c1c1a`，surface ladder 依次使用 `#181816/#222220/#2a2a27/#33332f`。
- 将主要前景改为 `#eae8e3/#9e9a92`；primary、tertiary、error、outline 和四个 event roles 使用 OpenDesign `theme-demo.html` 的最终值。
- 更新两份 active visual contracts 的当前 executable token 路径，不再声称 `ui/tokens.ts` 尚不存在。

### Verification

```powershell
cd webapp/frontend
npm.cmd run test:contracts -- --test-name-pattern="theme"
npm.cmd run type-check
npm.cmd run build
```

### Stop rule

若任一既有正文/primary/error 对比度检查低于 WCAG AA，或需要新增 token key 才能匹配设计，停止并报告具体 role；不通过组件私有 raw color 绕过。

## Task 2 - Add connected KovaaK frontend adapters

### Allowed files

- `webapp/frontend/lib/types.ts`
- `webapp/frontend/lib/api.ts`
- `webapp/frontend/lib/api.test.ts`

### Tests first

1. GET/PUT/DELETE `/api/kovaak-connection`、POST `/api/kovaak-connection/refresh` 和 GET `/api/kovaak-scores` 的 method/body/response 合同。
2. 保存请求使用 `steam_profile + identity_consent`；公开类型没有 Steam ID/Profile 字段。
3. API error 保留现有自然错误投影，不把输入写入日志或错误文本。

### Implementation

- 新增 connection status/save/delete/refresh 类型与 adapter；保留旧 direct sync helper 只作已有兼容调用，不让新 UI 使用它。
- 不在 `localStorage`、URL、Coach message 或公开 DTO 保存/传递身份。

### Verification

```powershell
cd webapp/frontend
npm.cmd run test:unit
npm.cmd run type-check
```

### Stop rule

若后端响应回显身份，或 refresh 不能证明失败保留旧 snapshot，停止；不得在前端缓存身份或复制成绩 store 修补。

## Task 3 - Realize KovaaK connection and score surfaces

### Allowed files

- `webapp/frontend/components/kovaak/KovaaKConnectionPanel.tsx` (create)
- `webapp/frontend/components/kovaak/kovaak.css` (create)
- `webapp/frontend/components/task3/OnboardingFlow.tsx`
- `webapp/frontend/components/task3/task3.css`
- `webapp/frontend/components/task6/SettingsWorkspace.tsx`
- `webapp/frontend/components/task6/task6.css`
- `webapp/frontend/fixtures/task7-fixtures.ts`
- `webapp/frontend/tests/task3-source.test.ts`
- `webapp/frontend/tests/task6-contracts.test.ts`
- `webapp/frontend/tests/task6-source.test.ts`
- focused existing Browser E2E/screenshot files only when required

### Tests first

1. 未连接、无效输入、未同意、保存/刷新中、首次成功、部分完成、无成绩、刷新失败但旧成绩可用和移除连接。
2. Onboarding 可跳过成绩连接且不阻塞 Provider/capture completion；Settings 可后续连接、刷新和移除。
3. 成绩视图没有 Easier/Medium Tab；按课程大类轻量分组，每行显示阶段、名称、最高分、档位、训练侧重或未完成状态。
4. DOM、fixture 和文案不含 Steam ID/Profile；“让 Coach 看看”不直接调用 Training Plan/Execution/Retest 写接口。

### Implementation

- 创建一个无产品账号心智的可复用连接/成绩模块；Onboarding 将其作为可选步骤，Settings 作为完整管理区。
- 刷新时保留已加载成功成绩；失败文案明确“这次没有更新，上次成绩仍然可用”。
- “让 Coach 看看”只触发本地 UI intent event/打开 Coach 并预填安全项目名，不携带身份、分数归因或自动结论。

### Verification

```powershell
cd webapp/frontend
npm.cmd run test
npm.cmd run type-check
npm.cmd run build
npm.cmd exec playwright test e2e/browser-smoke.spec.ts e2e/accessibility.spec.ts
```

### Stop rule

若需要新增 Benchmark 页面、OAuth、后台刷新、身份回显、成绩历史曲线、score-only 诊断或新的 Coach write command，停止。

## Task 4 - Add bounded Analysis family-detail read model

### Allowed files

- `webapp/backend/read_models.py`
- `webapp/backend/schemas.py`
- `webapp/backend/routes.py`
- `webapp/tests/test_capability_contracts.py`
- `webapp/tests/test_routes.py`

### Tests first

1. 新增 `GET /api/sessions/{session_id}/analysis-data/family`，返回 `frontend_analysis_family_data.v1`，并复用现有 owner/revision-bound artifact 读取。
2. 使用 persisted `result.analysis_type` 和 analysis version 分派；绝不从 field/key 猜 family。
3. Switching 只白名单 `switch_chain` 的相对时间、transition/acquire/settle 和四项指标。
4. Tracking 只白名单 fixed-window/loss/reacquisition/change-response 的相对时间、已验证数值和 limitations。
5. input-native static Flicking 只白名单 static_flick 的 start/peak/movement-end/settle-end 和有限指标；legacy video-fallback 返回 unavailable。
6. 分页使用 `limit`/`offset`、`total_count`/`next_offset`；不静默抽样完整动作表。
7. 响应不含坐标、trajectory、target/actor/source/artifact refs、绝对路径、Raw、原始 attributes 或未知字段。

### Implementation

- 新模型独立于现有 `frontend_analysis_data.v1`，不扩写或破坏通用 Data route。
- 所有时间转换为 canonical window 内的相对毫秒；越界、缺字段和无对应 rows 以 unavailable/limitation 返回。

### Verification

```powershell
$env:KOVAAK_INSTALL_DIR = Join-Path $env:TEMP "aiming-cookie-no-kovaak"
.\.venv\Scripts\python.exe -m pytest webapp/tests/test_capability_contracts.py webapp/tests/test_routes.py -q
.\.venv\Scripts\python.exe -m compileall -q webapp/backend/read_models.py webapp/backend/schemas.py webapp/backend/routes.py
```

### Stop rule

若任一展示字段需要修改 analyzer、读取 raw coordinate/trajectory、推断目标身份/第一枪/首次伤害/重新进入，或无法保持 owner/revision 绑定，停止。

## Task 5 - Realize Switching, Tracking and Flicking Data views

### Allowed files

- `webapp/frontend/lib/types.ts`
- `webapp/frontend/lib/api.ts`
- `webapp/frontend/components/task5/DataView.tsx`
- `webapp/frontend/components/task5/task5.module.css`
- `webapp/frontend/fixtures/task7-fixtures.ts`
- `webapp/frontend/lib/api.test.ts`
- `webapp/frontend/tests/task5-analysis.test.ts`
- `webapp/frontend/tests/task5-source.test.ts`
- `webapp/frontend/e2e/analysis-data.spec.ts`
- `webapp/frontend/e2e/screenshots.spec.ts` and focused snapshots only when required

### Tests first

1. 新 adapter 与 DTO 只接受 `frontend_analysis_family_data.v1`，分页与 unavailable 语义保持类型化。
2. Switching 每行一条完整 chain，显示四项白话指标和 kill -> transition -> acquire -> settle 相对时序。
3. Tracking 保留通用汇总/error-radius，再展示真实 fixed windows 与 loss/reacquisition/change-response；任何 timing 都不称人的反应时间。
4. Flicking 补齐 `static_clicking.*` 汇总指标标签，并用真实 static_flick 行展示 accel/decel/settle、peak/path/correction；legacy fallback 诚实 unavailable。
5. 只有 managed video 可播放时才启用逐行/片段 seek；否则保留数据并解释回放不可用。

### Implementation

- DataView 根据 Analysis 明确 family/version 请求 detail；不新增 Analysis 内分类 Tab。
- 延续现有 `onSelectTime(relative_ms)` 联动 Video，不改视频协议和 workspace tab 合同。

### Verification

```powershell
cd webapp/frontend
npm.cmd run test
npm.cmd run type-check
npm.cmd run build
npm.cmd exec playwright test e2e/analysis-data.spec.ts e2e/accessibility.spec.ts
```

### Stop rule

detail 不可用、分页失败、视频 unavailable 或 family unsupported 时显示 limitation；不补示例数字，不新增诊断、跨局趋势或目标视觉结论。

## Task 6 - Add current-training read projection

### Allowed files

- `webapp/backend/read_models.py`
- `webapp/backend/schemas.py`
- `webapp/backend/routes.py`
- `webapp/backend/training_plan_store.py`
- `webapp/tests/test_training_plan_store.py`
- `webapp/tests/test_routes.py`
- `webapp/frontend/lib/types.ts`
- `webapp/frontend/lib/api.ts`
- `webapp/frontend/lib/api.test.ts`

### Tests first

1. owner 当前没有 active plan、存在 active/paused plan、0-3 个 visible items、planned/active/completed/cancelled item 和跨 owner 隔离。
2. 只读响应白名单 display name/status/practice condition/cue/dose guardrail/observation/retest 文案；不泄漏内部 diagnosis/knowledge/metric refs。
3. scenario display name 只从现有 reviewed Scenario Registry 解析；解析失败显示 neutral unavailable，不暴露 raw ref。
4. GET 不改变 plan/item/TeachingSession/confirmation 状态。

### Implementation

- 新增 `current_training.v1` owner-scoped GET；复用 `list_plans/list_plan_items` 和 reviewed scenario profile，不新增 store/schema。
- 最多返回当前周期 3 项；超出时返回计数和 limitation，不静默声称完整。

### Verification

```powershell
$env:KOVAAK_INSTALL_DIR = Join-Path $env:TEMP "aiming-cookie-no-kovaak"
.\.venv\Scripts\python.exe -m pytest webapp/tests/test_training_plan_store.py webapp/tests/test_routes.py -q
cd webapp/frontend
npm.cmd run test:unit
npm.cmd run type-check
```

### Stop rule

若需要新 Training Plan store/migration、默认剂量、自动完成/升级/改计划、从 ref 猜用户文案或绕过 confirmation，停止。

## Task 7 - Realize Coach current-training surface

### Allowed files

- `webapp/frontend/components/task6/CoachPanel.tsx`
- `webapp/frontend/components/task6/CoachSidebar.tsx`
- `webapp/frontend/components/task6/task6.css`
- `webapp/frontend/fixtures/task7-fixtures.ts`
- `webapp/frontend/tests/task6-contracts.test.ts`
- `webapp/frontend/tests/task6-source.test.ts`
- focused Coach Browser E2E/screenshot files only when required

### Tests first

1. 无计划、进行中、已暂停、等待确认、复测完成、Provider 不可用与窄窗状态。
2. 摘要默认回答练什么、练多少、注意、观察和复测；展开最多 3 项，completed/active/planned 层级清楚。
3. 快捷意图只写入 Coach draft/发送普通 turn，不调用 execution/retest/plan mutation route。
4. Provider 不可用时本地当前训练仍可读，只有 Coach-dependent actions disabled。

### Implementation

- 在 Coach 内容顶部加入紧凑、可展开的当前训练区；复用现有 context、run streaming、stop/retry 和 confirmation。
- 复杂分析只提供定位入口，不复制图表到侧栏。

### Verification

```powershell
cd webapp/frontend
npm.cmd run test
npm.cmd run type-check
npm.cmd run build
npm.cmd exec playwright test e2e/interaction-polish.spec.ts e2e/accessibility.spec.ts e2e/screenshots.spec.ts
```

### Stop rule

若任何 intent 会直接记录执行/复测、改变 plan 或生成未经确认的训练事实，停止。

## Task 8 - Integrated acceptance and documentation closeout

### Allowed files

- `docs/PROGRESS.md`
- `docs/README.md`
- `docs/opendesign-desktop-handoff.md`（历史路径，已退役）
- `docs/archive/retired/opendesign-desktop-handoff.md`（当前归档位置）
- `docs/archive/completed/plans/2026-07-13-frontend-product-reconstruction.md` (repair moved-handoff link only)
- `docs/superpowers/plans/README.md`
- `docs/superpowers/plans/2026-07-29-kovaak-score-and-analysis-presentation-closeout-v1.md` (move only)
- `docs/archive/completed/plans/2026-07-29-kovaak-score-and-analysis-presentation-closeout-v1.md` (moved file)
- `docs/superpowers/specs/2026-07-29-viscose-s2-sync-coach-progression-design.md`
- `docs/superpowers/specs/2026-07-30-kovaak-connected-account-and-coach-lookup-design.md`
- this plan (status only)

### Tests first

1. 确认旧 presentation plan 的 Task 1-4 已完成且无后续 executable Task。
2. 确认 OpenDesign handoff 的耐久决策已存在于 PRD/Architecture/frontend UIUX/DESIGN/design-system/本计划，不丢失唯一事实。
3. 确认 `docs/README.md` 现有未提交 assessment 索引改动被保留。

### Verification

```powershell
$env:KOVAAK_INSTALL_DIR = Join-Path $env:TEMP "aiming-cookie-no-kovaak"
.\.venv\Scripts\python.exe -m pytest -q
cd webapp/frontend
npm.cmd run test
npm.cmd run type-check
npm.cmd run build
npm.cmd run test:e2e
cd ..\..
git diff --check
```

随后按 `docs/DEVELOPMENT.md` 运行真实 Tauri smoke，检查实际 WebView 的 onboarding、Settings、三类 Analysis Data、Coach 当前训练、light/dark、1280x820 和 960x640。Browser 通过不能替代该 smoke；真实 KovaaK、高 polling-rate、Tracking `<=130s`、installer/signing/updater/download 仍单独报告。

### Stop rule

若任何代码 Task 未完成、验证失败、Markdown 链接无法闭合或旧 handoff 仍承载唯一 active 决策，不归档、不标 completed。未经点点明确要求不 commit、不 push。
