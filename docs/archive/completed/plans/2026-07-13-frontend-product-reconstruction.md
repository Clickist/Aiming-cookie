# Frontend Product Reconstruction — Implementation Plan

> **状态：completed，2026-07-26 归档。** Task 1–7 已由点点逐项授权并完成当前实现、Browser/Desktop E2E、截图与 accessibility 验收；真实 KovaaK、跨 GPU、Provider/OAuth、worker restart 与发布工程继续由 Roadmap Gate 管理。
>
> Task 1 已于 2026-07-13 获得点点明确授权和精确删除范围确认；Task 2–7 随后均由点点逐项授权并按对应 Allowed files、Tests first、冻结决策与 Stop rule 完成。以下正文保留获批时的执行合同和历史激活边界，不再作为新的施工入口。
>
> **本计划只覆盖正式产品前端重建。** 原 `/history`、`/analyze`、临时 App shell 和 prototype components 已在 Task 1 删除；当前只保留 capability adapters 与 Tauri/runtime。正式重建必须从上游产品与设计合同开始，不得恢复 prototype。
>
> **依赖：**
>
> - [`../../../PRD.md`](../../../PRD.md)
> - [`../../../ARCHITECTURE.md`](../../../ARCHITECTURE.md)
> - [`../../../frontend-uiux-design.md`](../../../frontend-uiux-design.md)
> - [`../../../../DESIGN-cursor.md`](../../../../DESIGN-cursor.md)
> - [`../../../design-system.md`](../../../design-system.md)
> - [`../../retired/opendesign-desktop-handoff.md`](../../retired/opendesign-desktop-handoff.md)：已退役的派生设计交接入口；只保留历史流程与验收背景，不覆盖上游合同，也不自动授权 Task
> - 已列为 active 的 [`../../../superpowers/specs/2026-07-13-frontend-product-reconstruction-design.md`](../../../superpowers/specs/2026-07-13-frontend-product-reconstruction-design.md)
> - 已有能力合同：[`../../../superpowers/specs/2026-07-13-kovaak-run-trace-lifecycle-design.md`](../../../superpowers/specs/2026-07-13-kovaak-run-trace-lifecycle-design.md)、[`../../../superpowers/specs/2026-07-13-analysis-evidence-coach-context-design.md`](../../../superpowers/specs/2026-07-13-analysis-evidence-coach-context-design.md)、[`../../../superpowers/specs/2026-07-17-automatic-run-capture-design.md`](../../../superpowers/specs/2026-07-17-automatic-run-capture-design.md)
>
> Frontend reconstruction spec 继续作为稳定局部设计合同；本计划 Task 1–7 已完成并归档。

## 1. 目标与非目标

### 目标

从产品文档和视觉合同重新建立高质量、可验证的桌面前端：

```text
              Aiming Coach
Run → Evidence → Analysis → Training → Retest
```

正式前端必须提供：

- Desktop-first App shell，而非网站式导航；
- 无账号条件启动、Provider-first onboarding、新建分析、任务中心、History、Analysis workspace、Settings；
- input-native、multimodal、video-fallback 三种模式的诚实可见表达；
- 自动采集状态、单局确认、多局选一条、待分析 Run、独立手动 fallback 与分类存储占用；
- Evidence availability、coverage、alignment、limitations 和 stable reference 的用户可理解呈现；
- 左侧主工作区 + 右侧可收起、可调宽度的 Coach 关系层；
- Browser 与 Desktop 的能力差异表达，而不是两套视觉产品；
- loading、empty、partial、offline、permission denied、source unavailable、alignment failed、queued、running、done、failed、retrying、deleted reference 等状态；
- light / dark / system 主题、键盘可用性、reduced motion、截图和真实数据 fixture 验收。

### 非目标

本计划不：

- 修改 PRD 的产品范围、付费关系或产品定位；
- 重定义 Architecture 的数据归属、安全边界、API 合同或后端生命周期；
- 实现或修复 input-native 的科学算法、Raw Input codec、Run ingestion、worker queue 或 AnalysisResult schema；
- 实现 Capture Coordinator、窗口录制、Run Finalizer、存储删除事务或其它尚无 active implementation Task 的后端能力；
- 定义新的在线 Benchmark provider、leaderboard、远端 trace sync 或 Provider credential/auth 后端语义；
- 恢复旧 Report、旧固定 Coach 页面、旧 StorageSettings、旧 ThemeController 或旧 Plotly 页面结构；
- 复制、移植或重绘 RefleK 的 UI、组件、CSS、资源或大段实现；
- 因为后端能力已经存在，就默认把它们全部暴露到第一版前端；
- 在点点尚未明确授权前删除当前前端代码。

## 2. 全局冻结决策

以下决策在执行任何 Task 前必须视为冻结；若上游文档或 active frontend reconstruction spec 与其冲突，应停止并请求点点裁决，而不是在 Task 内静默修改。

1. **Prototype 不具有产品权威。** 当前 frontend prototype 只提供能力接线证据；它的布局、视觉、路由和组件拆分不得作为正式前端迁移目标。
2. **不恢复旧 UI。** 旧 Report、固定 Coach、StorageSettings、ThemeController、Plotly 页面及旧 route 结构不恢复；需要的用户能力按新合同重新设计。
3. **不复制 RefleK UI。** RefleK 只提供已批准的能力参考；前端必须独立设计和实现，不复制其页面、组件、样式、资源或交互。
4. **保留底层能力边界。** 不删除或改写以下非 UI 层：
   - `webapp/frontend/lib/api.ts`
   - `webapp/frontend/lib/types.ts`
   - `webapp/frontend/lib/contracts.ts`
   - `webapp/frontend/lib/csv.ts`
   - `webapp/frontend/lib/desktop.ts`
   - `webapp/frontend/src-tauri/**`
   - 与这些 adapter/capability 直接对应的 backend、runtime、contract 和测试
5. **保留不等于盲目冻结。** adapter、types 和 contracts 可以在其自身合同允许的范围内被重新审计；不得为了适配 UI 偷改后端语义，也不得让 UI 猜测 input mode、evidence 或 owner。
6. **Input-native 在正式 UI 中标为 Preview / Experimental。** 在 flick segmentation、核心 fair metrics、high polling-rate 和 Windows 实机 Gate 未通过前，不得把它表达成已取代视频诊断的完整正式能力。
7. **Multimodal 视觉失败保留 native 结果。** native deterministic result 单独成立；视觉校验失败显示为 visual validation unavailable / alignment failed，并提供重试或重新选择 MP4，不把整个 Analysis 伪装成失败。
8. **Benchmark 不进入 v1 核心 UI。** 后端记录、存储和 API 可保留；第一版正式前端不放默认 History 主流程、不进入默认 Coach context、不做 leaderboard 或在线 provider。
9. **Run、Analysis、Run-owned evidence、用户源文件的删除语义分离。** 删除 Analysis 不删除 Run、Raw Input trace、自动 MP4 或 Stats/Performance 用户源文件；Storage 只可按 automatic capture spec 分别管理 Run-owned evidence 和未完成采集数据；Analysis-owned managed artifacts 按其合同处理；Coach 消息保留但引用变为 unavailable/deleted。精确删除事务未由 implementation Task 冻结前不得实现。
10. **Browser 与 Desktop 共享产品 IA 和视觉语言。** 能力可用性、文件选择和 managed video 形态可以不同，但不得维护两套产品界面。
11. **所有页面失败必须可解释。** 请求失败不得转换成空列表；未知状态不得默认成 available；颜色不得作为唯一状态表达。
12. **无产品账号。** 正式前端不得创建 `/login`、`/register`、Account 菜单、session/JWT、entitlement 或鉴权服务器依赖；Provider 可以无需认证；如需认证，只属于对应 Provider。
13. **执行不得反向改写合同。** Frontend reconstruction spec、UI/UX、视觉合同、design-system 与本计划已经完成本轮冲突检查；executor 不得在 Task 内通过修改这些文档来迁就实现。
14. **完整 Coach 当前只能消费有界 L1-L3。** L1 allow-listed canonical Stats/Performance facts、L2 derived evidence 和 L3 diagnosis/plan 可按预算进入 context/tool results；当前合同不把 L0 Raw/MP4/原始 CSV/protobuf/路径/私有 parser/unknown fields 放入 context/tool results。用户可本地播放 EvidenceSegment 对应视频，Coach 当前不读取视频内容；未来视觉模型读取受限片段必须另立版本化合同和实施授权。static/dynamic/tracking/switching 仅在相应 family Gate 通过后显示正式能力；未知与 movement aiming 只表达 outcome-only。

## 3. 正式前端边界与路由目标

Active frontend reconstruction spec 已冻结以下目标路由；本计划只引用其决策，不允许 executor 在实现 Task 中自行增删路由：

```text
/                            条件启动路由
/onboarding                  首次 Coach / Provider 激活
/analyze                     新建分析
/tasks                       全局任务中心
/history                     Run 与 Analysis 历史
/analysis/:analysisId        Analysis workspace
/settings                    Provider、Theme、Raw Input、Profile、Storage
```

`/` 的条件行为：

- onboarding 未完成 → `/onboarding`；
- onboarding 已完成、无 Run 且无 Analysis → `/analyze`；
- 已有 Run 或 Analysis → `/history`；
- 无法读取本地 onboarding 或产品状态 → 显示可恢复的 service unavailable，而不是静默跳转或伪造空态。

正式 App shell 目标：

```text
┌──────────────────────────────────────────────────────────────┐
│ 静态 Logo  │ 历史 │ ＋新建分析 │              任务 │ Coach │ 设置 │
├───────────────────────────────────┬──────────────────────────┤
│                                   │                          │
│ 当前主工作区                      │ 可收起、可调宽 Coach     │
│                                   │                          │
└───────────────────────────────────┴──────────────────────────┘
```

Logo 只作品牌标识，不承担返回首页或营销页导航。Coach 是跨页面关系层，不是一个必须单独恢复的旧页面。

## 4. 执行协议

### 4.1 Tests first

每个 Task 必须先：

1. 读取该 Task 的上游合同、现有代码和当前未提交状态；
2. 定义会失败的测试、fixture、截图或可检查验收条件；
3. 先运行基线验证，区分既有失败和本 Task 失败；
4. 再做最小实现；
5. 重跑该 Task 的 focused checks，再运行全局 Gate。

“测试”可包含 unit、contract、route smoke、Playwright、Tauri/browser fixture、accessibility audit、截图比对和静态检查，但必须在 Task 开始前写清楚预期。

Task 3-6 以 Browser 作为快速实现和多数 UI 状态验收环境；每个已经具备可测试条件、且涉及 Desktop capability 的垂直流程，在对应 Task 收口前还必须运行 focused `tauri dev` smoke。完整 Browser/Desktop 矩阵仍由 Task 7 验收；Browser mock 或代理通过不能替代 Tauri file picker、per-launch runtime、managed media、窗口、CSP、启动/退出和后台恢复证据。无法运行真实 Desktop smoke 时必须明确报告，不得静默推迟或写成已通过。

### 4.2 Allowed files

每个 Task 的 Allowed files 是硬边界。需要扩大文件范围、修改上游文档、修改 API/contract、改变删除语义或改变视觉决策时，必须停止并由点点或架构负责人裁决；executor 不得自行修订本计划解锁自己。

### 4.3 激活与 Task 授权边界

- 本计划处于 active，只表示 Task 顺序、Allowed files、Tests first、冻结决策和 Stop rule 已可作为实施合同引用；
- plan 激活不自动授权任何 Task，也不授权删除或移动当前 frontend 文件；
- 后续必须由点点明确指定一个 Task，executor 每次只执行该 Task；
- Task 1 若包含删除 prototype，点点还必须明确确认具体删除范围；
- 未被当前 Task 列入 Allowed files 的 backend、adapter、src-tauri、spec、plan 或 index 均不得修改；
- 未经点点指示，不提交、不推送、不继续下一个 Task。

## Task 1 — Prototype inventory / 隔离或删除产品 UI，保留 adapters

### 目的

建立当前 frontend prototype 与 capability/adapter 层的边界；在获得单独删除授权后，隔离或删除产品 UI，使正式重建不继承临时页面结构。

### Allowed files

- `webapp/frontend/app/page.tsx`
- `webapp/frontend/app/analyze/page.tsx`
- `webapp/frontend/app/history/page.tsx`
- `webapp/frontend/app/layout.tsx`
- `webapp/frontend/app/not-found.tsx`
- `webapp/frontend/app/globals.css`
- `webapp/frontend/components/AppChrome.tsx`
- `webapp/frontend/components/NewAnalysisClient.tsx`
- `webapp/frontend/components/history/BenchmarkPanel.tsx`
- `webapp/frontend/components/history/HistoryClient.tsx`
- `webapp/frontend/app/**` 中符合 active frontend reconstruction spec §15.2、且经 inventory 明确登记为 product UI prototype 的页面文件
- `webapp/frontend/components/**` 中经 inventory 明确为 product UI prototype 的组件文件
- `webapp/frontend/lib/api.test.ts` 及为验证 adapter 边界所需的 focused frontend tests
- Task 1 生成的 frontend inventory fixture / manifest；仅限 `webapp/frontend/` 内

**明确不在 Allowed files：**

- `webapp/frontend/lib/api.ts`
- `webapp/frontend/lib/types.ts`
- `webapp/frontend/lib/contracts.ts`
- `webapp/frontend/lib/csv.ts`
- `webapp/frontend/lib/desktop.ts`
- `webapp/frontend/src-tauri/**`
- 任何 backend、Coach runtime、Python/Rust capability、spec/index/plan/index 文件

### Tests first

- 记录当前 workspace 的 frontend 路由、prototype 文件、adapter 文件和既有未提交状态；
- 对保留的 `lib/api.ts`、`types.ts`、`contracts.ts`、`csv.ts`、`desktop.ts` 建立 import/type smoke，证明产品 UI 删除不会删除 capability；
- 运行删除前基线：frontend type-check、frontend tests、frontend build；
- 以 fixture 验证删除/隔离后不存在从 adapter 反向依赖 prototype component 的路径；
- 验证不恢复旧 Report、固定 Coach、StorageSettings、ThemeController、Plotly UI；
- 若执行删除，使用可追踪、可回退的文件操作；不得 reset、checkout、覆盖点点已有改动。

### 冻结决策

- 当前 UI 只可被 inventory、隔离或删除，不可继续作为正式视觉基础；
- 只删除明确登记的产品 UI，不删除 adapter、types、contracts、Tauri bridge、backend 或相关测试；
- 不把当前 `globals.css` 或当前组件拆分迁移成新设计系统；
- Task 1 的删除动作必须在点点明确授权后才能执行；
- Task 1 可以有意让正式产品路由暂时不可用；不得为了维持一个误导性的可运行外壳而恢复 prototype 或创建未经合同定义的占位 UI。

### Stop rule

- active frontend reconstruction spec 被撤销，或与当前 UIUX/Design system 出现未裁决冲突；
- 无法区分 prototype 与 capability/adapter；
- 需要修改 backend/lib adapters/src-tauri 才能完成隔离；
- 需要恢复旧 UI 才能通过构建；
- 发现当前工作区有无法安全区分的用户改动；
- 删除范围或删除语义不明确；
- 任何人要求在本 Task 内顺手修复无关前端、后端或视觉债务。

## Task 2 — Executable tokens / theme / primitives

**历史激活状态：最初未授权，后由点点明确授权并完成。**

### 目的

在没有继承旧 `globals.css` 的前提下，建立遵循 `DESIGN-cursor.md` 与 `docs/design-system.md` 的可执行语义 token、主题控制器和基础 primitives。

### Allowed files

- `webapp/frontend/ui/**`：唯一的新 executable token、theme controller、primitive 与其 colocated tests 入口
- `webapp/frontend/app/layout.tsx`，仅用于接入根级 theme controller
- `webapp/frontend/app/not-found.tsx`，仅用于使用新 primitives
- `webapp/frontend/tests/**` 中仅属于 Task 2 的 theme/accessibility tests 与 fixtures
- Task 2 的 token audit / fixture 文件；仅限 `webapp/frontend/ui/**` 或 `webapp/frontend/tests/**`

**明确不在 Allowed files：** `package.json`、lockfile 和新依赖。若现有依赖无法完成 Task 2，停止并请求扩大范围，不得自行加包。

### Tests first

- light/dark token key 集完全一致；组件中无 raw hex/RGB/HSL；
- `system` 首次启动跟随系统、系统变化实时更新；显式 `light` / `dark` 不随系统变化；
- theme preference 只在本地 UI storage，不进入 Analysis、Provider auth 或 Coach payload；
- hydration 前无主题闪烁；无障碍 focus、disabled、outline、status 对比在两种主题下均可读；
- primitives 覆盖 Button、IconButton、Badge/Status、Field、Panel/Surface、Tabs、Drawer/Sheet、Toast/Notice、Loading/Empty/Error、Dialog 等实际页面所需基础能力；
- reduced motion 下不依赖动画表达状态；
- primitives 不在组件内部以 theme 分支改变产品结构。

### 冻结决策

- 组件只消费语义 token，不直接消费 palette 字面值；
- light/dark/system 是唯一主题 preference；
- `webapp/frontend/ui/**` 是唯一 executable token/theme/primitives 模块边界；primitive API 只实现 Task 3–6 已明确需要的最小集合，不建立通用组件库；
- 不恢复 `ThemeController.tsx`；新 theme controller 重新设计并由 design-system 规则治理；
- 不为了视觉方便引入单组件私有颜色、重阴影或大面积胶囊化。

### Stop rule

- `DESIGN-cursor.md`、`docs/design-system.md` 与 active frontend reconstruction spec 的语义角色或主题行为冲突；
- 需要 raw color 才能完成页面；
- 需要修改产品 IA 或页面状态；
- 需要恢复旧 `globals.css`、ThemeController 或旧组件 API；
- token key 集、主题持久化边界或无障碍验收无法测试。

## Task 3 — Provider onboarding / App shell / New Analysis / Tasks

### 目的

实现无账号首次启动、最小 Provider onboarding、正式应用壳、新建分析和全局任务中心，首先建立 Coach 激活、自动采集后的 Run 选择与三种 input mode 的用户可达流程。

### Allowed files

- `webapp/frontend/app/page.tsx`
- `webapp/frontend/app/onboarding/**`
- `webapp/frontend/app/analyze/**`
- `webapp/frontend/app/tasks/**`
- `webapp/frontend/app/layout.tsx`
- `webapp/frontend/components/**` 中仅属于 Provider onboarding、App shell、New Analysis、Tasks 的新组件
- `webapp/frontend/ui/**` 中 Task 2 已冻结并允许复用的 primitives
- `webapp/frontend/lib/api.ts`
- `webapp/frontend/lib/types.ts`
- `webapp/frontend/lib/contracts.ts`
- `webapp/frontend/lib/csv.ts`
- `webapp/frontend/lib/desktop.ts`
- 对应 frontend unit/component/route tests 和 fixtures

### Tests first

- `/` 条件启动：onboarding 未完成进入 `/onboarding`；完成后无 Run/Analysis 进入 `/analyze`；已有 Run/Analysis 进入 `/history`；状态读取失败显示 service unavailable；
- `/onboarding` 说明 Aiming Cookie 开源免费、第三方 Provider 可能收费、连接后的 Coach 能力、无 Provider 可用的本地能力和默认数据边界；跳过只作为底部次级文字，hover/focus 明确跳过后没有任何 Coach 功能；
- onboarding 覆盖已批准的无认证直连/API key/OAuth-device-code/local/custom Provider、model 选择、connection test、ready、取消、失败恢复、稍后继续和明确跳过；secret 永不进入聊天或前端持久化；
- 跳过 Provider 后本地指标、确定性诊断、规则化提示与 History 可达；Coach 只显示可恢复的激活入口，不提供对话、解释、长期档案、训练计划或产品命令；
- App shell 无 Account 菜单，提供 Settings 明确入口；语义 landmarks、静态 Logo、键盘顺序、skip link、focus 和响应式三档；
- Browser/Desktop 能力差异：Run discovery、file picker、managed video、Raw Input 和 launch-token 状态不被伪造；
- Desktop New Analysis 覆盖自动采集未启用/待命/采集中/整理中/完成/失败；只有一条可分析 Run 时默认选中并等待确认，两条及以上时必须选择一条；
- 未选择 Run 保持 pending_analysis，不进入 Tasks、不合并、不自动删除；
- input-native：Stats + Performance + attached Raw Input + 对齐条件满足时可选；缺失时解释原因，并显示 Preview/Experimental；
- multimodal：native evidence 完整且 MP4 可用时可选；视觉分析失败不抹掉 native 结果；
- video-fallback：在独立手动 fallback 界面同时选择 MP4 + 对应 Stats CSV，不要求 Raw Input，不凭 MP4 猜测 CSV，不伪造 native provenance；
- Start 按钮只在已选中一条 Run 且满足 `Stats AND (MP4 OR (Raw + Performance))` 时按 contract/返回状态启用，不由前端猜测 evidence；
- `/tasks` 覆盖 importing、queued、running、done、failed、retryable、restarting、offline；失败不能变成空列表；
- 可离开页面后在任务中心找回，并能进入结果或重试；运行中不可删除；
- Browser/Desktop 共享 IA 与视觉结构，差异仅在能力状态和输入方式。

### 冻结决策

- 不创建 `/login`、`/register`、Account 菜单或产品鉴权；Provider 可无需认证；其可选认证也不等于产品登录；
- onboarding 使用结构化 Provider UI，不通过 Coach 对话收集 credential；
- `/tasks` 是独立全局入口，不链接到 `/history` 代替任务中心；
- 新建分析只消费后端合同，不复制业务判断到组件；
- native 视觉提示必须诚实标为 Preview/Experimental；
- multimodal 视觉失败保留 native 分析；
- video fallback 是正式可达的基础路径；
- multimodal 只用 Raw 计算输入运动学，MP4 只用于回放、视觉定位与 Coach 直观讲解；
- 不恢复旧上传页或旧 Processing/Report 页面，以新工作区合同重新表达相同产品能力。

### Stop rule

- route table、Provider onboarding、mode matrix 与 evidence state matrix 未在 active spec 冻结；
- Coach productization Provider Task 3/4 的 capability API、app-owned credential persistence、redaction 或 auth flow 未完成，导致 onboarding 只能伪造成功或由前端保存 secret；
- 任一模式需要前端猜测 source、owner、alignment、availability 或 provenance；
- backend/lib contract 与 UI 需要的状态不一致；
- Capture Coordinator / Run Finalizer / pending Run readiness capability 尚未实现或没有稳定接口，导致自动采集状态、单局/多局选择或 Run-owned MP4 只能由前端伪造；
- `/tasks` 需要新增未批准的 queue/retry/delete 语义；
- Desktop 与 Browser 只能通过两套不一致的视觉结构实现；
- 无法保留 MP4 + Stats fallback。

## Task 4 — History

### 目的

实现“待分析训练 → 其它 Run → Analysis”的轻列表 + lazy details History，清晰区分 Run、Evidence、Analysis 和长期趋势；不把所有详情和 Coach 堆回单页。

### Allowed files

- `webapp/frontend/app/history/**`
- `webapp/frontend/components/**` 中仅属于 History/Run inspector 的新组件
- `webapp/frontend/ui/**` 中已冻结 primitives
- `webapp/frontend/lib/api.ts`
- `webapp/frontend/lib/types.ts`
- `webapp/frontend/lib/contracts.ts`
- History focused tests、fixtures、route tests 和 screenshot specs

**默认不在 Allowed files：** `BenchmarkPanel.tsx` 或任何 external Benchmark UI；若未来产品决策重新授权 Benchmark，必须另立 spec/Task。

### Tests first

- Run 与 Analysis 轻列表首屏不加载完整 events/trace，details lazy load；
- History 顶部显示满足 readiness 且尚未创建 Analysis 的 pending Run；单条确认、多条选一条，未选择项不进入 Tasks；
- History 显示 scenario identity、时间、input mode、evidence summary、quality 和状态；
- source unavailable、partial、unsupported、offline、permission denied、deleted reference 可区分；
- API refresh 失败不变成“没有记录”；原有数据可以保留并显示 refreshing/unavailable；
- Run 操作覆盖开始分析、查看来源、进入 Storage 管理 Run-owned evidence、查看已有 Analysis；手动补充 MP4 只在已冻结的 fallback/修复路径出现，按钮状态由 evidence contract 驱动；
- Analysis 记录进入 `/analysis/:analysisId`，而不是在 History 内混合完整视频、图表、Coach；
- History 趋势只比较满足 scenario、mode、metric、unit、calibration、quality 的记录；不足或不可比时不制造趋势；
- 不展示绝对路径、raw trace、secret、token 或未授权 provider 信息。

### 冻结决策

- History 是组织层，不是新的数据所有者；
- Analysis detail 不在 History 页面内继续膨胀为万能 inspector；
- Benchmark 第一版不进入核心 History UI；
- native-only 明确没有视觉回放，不显示伪造的视频结论；
- History 保持轻列表 + lazy details。
- pending Run 与 queued Analysis 不得混用同一状态或列表语义。

### Stop rule

- 需要改变 backend read model、ownership、retention 或 comparability 合同；
- 需要新增 Benchmark、leaderboard 或在线 provider；
- 需要把 raw trace 下载或注入 History payload；
- 无法区分 empty、offline、source unavailable 和 permission denied；
- 需要恢复当前 `HistoryClient.tsx` 的混合布局作为正式结构。

## Task 5 — Analysis workspace

### 目的

实现独立的 `/analysis/:analysisId` 工作区，按 Diagnosis、Video、Data 三种视图组织确定性结果、视频证据、指标和稳定引用。

### Allowed files

- `webapp/frontend/app/analysis/**`
- `webapp/frontend/components/**` 中仅属于 Analysis workspace、Diagnosis、Video、Data、timeline 的新组件
- `webapp/frontend/ui/**` 中已冻结 primitives
- `webapp/frontend/lib/api.ts`
- `webapp/frontend/lib/types.ts`
- `webapp/frontend/lib/contracts.ts`
- `webapp/frontend/lib/desktop.ts`
- Analysis focused tests、fixtures、route tests、Playwright specs 和 screenshot specs

### Tests first

- `/analysis/:analysisId` 根据真实 session 状态覆盖 loading、queued、running、done、failed、retryable、deleted/unavailable；
- Diagnosis 只展示 contract 允许的结论、warnings、limitations、evidence summary 和 stable references；
- workspace 显示 aim family 与 `supported / descriptive / unavailable / outcome-only`，不依据场景名或 Coach 推断升级；
- input-native：展示输入运动学/事件对齐，不展示视觉结论；Preview/Experimental 明确可见；
- multimodal：native facts 与 visual validation 分层；视觉失败保留 native；alignment failed 不跨来源合并结论；
- video-fallback：展示视频/CV 结论，明确没有 Raw Input provenance；
- Video 支持 Browser/Tauri managed URL、播放/暂停/seek、timeline 定位、事件标记、EvidenceSegment 本地片段回放和 source unavailable；native-only 明确无视频；
- Data 展示指标、unit、coverage、provenance、quality、comparability 和 limitations；不泄漏 raw trace/path/URI；
- Diagnosis ↔ Video ↔ Data ↔ Coach 的定位关系可键盘操作并可恢复；
- Analysis 删除后页面和 Coach 引用显示 unavailable/deleted，不把消息快照继续当作可用 evidence。

### 冻结决策

- Analysis workspace 是正式主工作区，不恢复旧 ReportView；
- Diagnosis、Video、Data 是同一 Analysis 的不同视图，不是三套互相独立的结果；
- 每个用户可见结论都能追溯到 evidence availability、coverage、alignment、stable reference 或 limitation；
- 不能把视频推断序列化为 Raw Input 测量；
- 不展示无法从当前 evidence 支持的 fair metric 或 root cause。

### Stop rule

- input-native 算法或 fair metrics 尚未满足产品可见性合同，需要 UI 伪造完整度；
- v2 evidence/provenance/stable reference contract 不足以安全呈现；
- managed video、timeline 或 Desktop URL 需要改变后端 ownership/retention；
- 需要恢复旧 ReportView、Plotly 页面或把当前 History inspector 直接搬进来；
- 无法在视觉失败时保留独立成立的 native 结果。

## Task 6 — Coach sidebar / Settings

### 目的

实现应用级 Coach 侧栏和完整 Settings，使 Coach 作为长期关系与产品操作层工作，同时接续 Task 3 的最小 onboarding，提供多 Provider/model/auth、主题、自动采集、Raw Input、Profile、Storage 的诚实状态管理。

### Allowed files

- `webapp/frontend/app/settings/**`
- `webapp/frontend/components/**` 中仅属于 Coach sidebar、Settings、Provider、Raw Input、Profile、Storage 的新组件
- `webapp/frontend/ui/**` 中已冻结 primitives
- `webapp/frontend/lib/api.ts`
- `webapp/frontend/lib/types.ts`
- `webapp/frontend/lib/contracts.ts`
- `webapp/frontend/lib/desktop.ts`
- Coach/Settings focused tests、fixtures、route tests、Playwright specs 和 screenshot specs

### Tests first

- Coach 在支持页面可收起、可调宽度；宽窗口并排、中窗口覆盖 drawer、极窄窗口全内容视图；第一次 Analysis 完成且 Provider ready 时自动展开一次，后续遵循已保存状态；
- Coach 跨页面保留会话、草稿、展开状态和当前 Analysis context；切换/移除 context 有明确反馈；
- 发送前可移除 context；默认 context/tool results 只允许有界 L1-L3：allow-listed canonical source facts、derived evidence 和 diagnosis/plan；不发送 Raw trace、绝对路径、MP4/video/frame、原始 CSV/protobuf、私有 parser/unknown fields、secret、token 或未验证 heuristic sentinel；
- EvidenceSegment 在 Coach 中可定位用户到对应本地视频段；Coach 当前不读取、上传或播放视频内容，未来视觉模型必须另有合同和实现授权；
- Analysis 删除后消息保留，引用显示 unavailable/deleted；停止生成、错误、重试、offline 状态可操作；
- `/settings` 覆盖完整 Provider/model/auth 管理、system/light/dark、Profile calibration、自动采集/Raw Input 多状态、分类 Storage 占用与手动管理，并与 onboarding 共用 capability/status/secret 组件；
- Provider UI 覆盖 API key set/replace/delete、已批准 OAuth/device-code 状态、local/custom OpenAI-compatible、model 选择、测试连接和默认 provider；secret 永不回显；
- 自动采集分开表达 platform support、permission、capture enabled、KovaaK process、hardware replay buffer、runtime health、Run finalization、trace/video attached 和 quality；
- Storage 显示总占用和 Run 录像、Raw trace、Analysis artifacts、未完成采集数据；只允许按 active capture spec 分别移除 Run-owned evidence 或未完成数据，不自动清理或一键清空；
- 删除/保留 UI 不越过 lifecycle spec；用户源 Stats/Performance 不被应用删除；
- Coach 和 Settings 在 Web/Desktop 共享产品结构，能力差异诚实表达。

### 冻结决策

- Coach 不是旧 `/coach` 页面，也不是 Analysis 底部小面板；它是 App shell 右侧关系层。onboarding 只负责结构化激活，不建立另一条 Coach 会话；
- 不恢复 `CoachClient.tsx`、session-bound Coach route 或旧 `StorageSettings` / `ThemeController`；
- L0 原始载体不进入 Coach context/tool results；用户已选择 Provider 时，有界 L1-L3 可作为普通 Coach turn 数据发送，不设置逐 Run 同意 Gate；
- Provider/model/auth 只实现 active [`../../../superpowers/specs/2026-07-13-coach-product-commands-explanations-provider-design.md`](../../../superpowers/specs/2026-07-13-coach-product-commands-explanations-provider-design.md) 与后端 capability API 已明确支持的状态，不根据 Pi 存在的接口自动宣称产品已支持；
- Benchmark 不进入默认 Coach context。

### Stop rule

- 完整 Coach context contract、EvidenceSegment 本地播放、删除后引用语义或 L0/L1-L3 boundary 尚未由对应 Task 稳定实现；
- Provider capability API、credential store 或 auth state 尚未由对应 active Task 实现，导致 UI 只能伪造成功或保存 secret；
- Settings 需要改变后端 ownership/retention 或未冻结的删除行为；
- Storage accounting 或 Run-owned evidence 手动删除缺少稳定 capability/恢复合同，导致 UI 只能猜测占用或删除影响；
- 无法测试 drawer focus trap、Escape、keyboard navigation、reduced motion 和错误恢复；
- 需要恢复旧 Coach/Storage/Theme UI 才能完成。

## Task 7 — Browser/Desktop E2E / screenshots

### 目的

用真实 fixture 和截图证明正式前端在 Browser、Tauri/Desktop、主题和响应式状态下符合 UIUX、视觉、无障碍和 capability 边界。

### Allowed files

- `webapp/frontend/**` 中为 E2E、fixtures、screenshot baseline 和 accessibility test 明确允许的文件
- `webapp/frontend/e2e/**`
- `webapp/frontend/tests/**`
- `webapp/frontend/fixtures/**`
- `webapp/frontend/playwright.config.*`
- `webapp/frontend/src-tauri/**` 仅在 Desktop E2E harness 或既有 bridge contract 需要 focused test 时；不得借此修改 Raw Input/backend 语义
- `docs/PROGRESS.md` 仅在点点另行授权回写验证结果时

### Tests first

- Browser smoke：首次使用、Run 列表、单局/多局选择、独立手动 fallback、三种 input mode、任务中心、History、Analysis workspace、Coach、Settings；
- Browser failure matrix：offline、service unavailable、partial/source unavailable、permission denied、alignment failed、queued/running/failed/retryable；
- Tauri/Desktop smoke：进程 gate 自动采集状态、连续多局事后切 Run、文件选择、Raw Input/窗口回放缓冲状态、Run-owned managed video URL、窗口尺寸、存储占用和任务恢复；
- 关键页面宽/中/窄三档截图；system/light/dark 截图；核心空态、失败态、partial 态截图；
- accessibility：landmarks、focus order、visible focus、skip link、drawer/dialog focus trap、Escape、ARIA live、目标尺寸：主操作 ≥40px、工具栏控件 ≥36px、IconButton ≥32×32px（桌面指针产品，不支持触控；WCAG 2.2 AA 24×24px 下限全部满足；原生系统控件，如 Windows 原生文件选择器，不在此目标尺寸验收范围内）、200% zoom、reduced motion、视频键盘控制、图表文本替代；
- screenshot review 不只看像素差异，还检查信息层级、状态可解释性、密度、主题对比和不泄漏路径/trace；
- 在无 Windows 实机或 Tauri 环境时明确报告未运行，不得把 mock 通过写成真实 Desktop Gate 通过。

### 冻结决策

- screenshot baseline 只从正式新前端生成，不以当前 prototype 为视觉基线；
- Browser 与 Desktop 的 shared IA、主题和组件语义必须一致；
- Windows Raw Input、高 polling-rate、真实 KovaaK 和真实 Tauri 视频播放仍是 release Gate，不由浏览器 mock 替代；
- 任何截图或 E2E 失败都先判断是产品合同、视觉合同、实现回归还是环境限制，不得直接放宽验收。

### Stop rule

- 关键页面没有真实 fixture 或无法稳定复现状态；
- Browser 通过但 Desktop/Windows 能力没有验证却想宣布 release-ready；
- 截图显示 token、密度、层级或状态表达与设计合同冲突；
- accessibility 或 reduced-motion 失败；
- E2E 需要修改 backend/adapter/src-tauri 合同而未单独授权；
- 发现 frontend reconstruction spec、UIUX 或 design-system 与实现仍有未裁决冲突。

## 5. 全局验证 Gate

在 frontend reconstruction plan 的所有 Task 完成、且点点明确授权发布评估前，至少需要：

- frontend type-check、unit/component tests、route tests、build；
- Browser Playwright E2E 与 failure matrix；
- Tauri/Desktop smoke；
- accessibility audit、keyboard/reduced-motion/200% zoom checks；
- light/dark/system screenshot review；
- Browser/Desktop managed video、任务恢复和三种 input mode 的真实或明确标注的 fixture 验证；
- backend/lib adapter/src-tauri regression checks，确认前端重建没有删除或改变底层能力；
- source path、raw trace、secret、token、provider credential 不进入用户 API/UI/Coach sink；
- Windows real-device / high polling-rate / KovaaK verification before any input-native release claim；
- `git diff --check`；
- `git status --short`，区分本次改动与原有工作区改动；
- 每个 Task 的 changed files、验证命令、未运行检查、偏差和剩余风险报告。

## 6. Executor 交付格式

每个完成的 Task 必须报告：

```text
Task: [编号与名称]

Changed files:
- [实际修改文件]

Tests first:
- [先定义/先运行的失败测试或验收条件]

Validation:
- [命令 / E2E / screenshot / accessibility 结果]

Not run:
- [未运行检查及原因]

Deviations:
- [与计划的偏差；没有则写 none]

Remaining risks:
- [剩余风险]

Git status:
- [本次改动]
- [原有改动]
```

未经点点明确授权，不提交、不推送、不继续下一个 Task，不自行修改本计划、spec 或任何 index。
