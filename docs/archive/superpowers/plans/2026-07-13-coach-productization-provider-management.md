# Coach Productization and Provider Management — Implementation Plan

> 状态：active（点点已在 2026-07-13 当前会话明确授权按本计划建议顺序推进，并确认 Task 1 可按审计建议扩展到真实 tracking 与 Python fallback 生产分支；执行时仍须一次只做一个 Task，并遵守 Allowed files、Tests first 与 Stop rule）。
> 依赖 spec：[`../specs/2026-07-13-coach-product-commands-explanations-provider-design.md`](../specs/2026-07-13-coach-product-commands-explanations-provider-design.md)
> 相关 spec：[`../specs/2026-07-13-analysis-evidence-coach-context-design.md`](../specs/2026-07-13-analysis-evidence-coach-context-design.md)、[`../specs/2026-07-13-frontend-product-reconstruction-design.md`](../specs/2026-07-13-frontend-product-reconstruction-design.md)

## 目标

把当前“单一 analysis summary tool + 环境变量 provider”推进为：

```text
确定性 input-native 指标 / History
  → 可追溯解释与训练处方
  → Pi knowledge + product command tools
  → 用户可配置 Provider/model/auth
  → 首次 Provider onboarding
  → 正式 Coach sidebar / Settings
```

MP4 在 input-native 路径中主要承担直观回放、问题定位和视觉 evidence；现有 video-fallback 保留兼容回归，不作为本计划新增算法重点。

## 冻结决策

1. Coach 产品命令与 UI 共用领域 handler，capability 与当前本地 profile 对齐，不是只读工具集合。
2. 用户本轮明确要求的普通可恢复动作可以直接执行；高后果、credential、外部状态和 Coach 自主推断的副作用动作需要确认。
3. Coach 不自行把指标解释成身体原因；claim level、evidence refs、limitations 和 source level 必须可审计。
4. 未校准指标可以用于值、分布和个体历史变化，不得硬写通用健康阈值。
5. 产品不提供账号、登录、session/JWT、entitlement 或鉴权服务器；首次启动以 Provider onboarding 为主路径，但可明确跳过进入本地分析。Provider 未配置或失败只影响 Coach，不影响确定性 Analysis、History 和报告。
6. pinned Pi built-in provider/model catalog 是完整产品 catalog，不另设 Aiming Cookie allow-list；所有 Pi 支持的 built-in 都要暴露并可用。
7. 自定义 OpenAI-compatible profile 必须包含 provider name、base URL、API key 和 model ID；当前 owner/profile 的 selected provider/model 是本地 canonical state。
8. API key 可以明文保存在 app-owned 本地 SQLite/config，secure store 不是 Gate；secret 仍不得进入 AnalysisResult、Coach context/message、普通日志、diagnostics 或 export。
9. `LLM_PROVIDER/providers.json` 只保留 compatibility，不是事实源；不得把 obsolete `deepseek-chat` 静默迁移为其它 model。
10. 只采纳 Pi provider/runtime capability，不把 Pi coding-agent 工具、filesystem/shell 权限自动带入产品。

## Task 1 — Explanation / claim / prescription contract

> 实施状态：completed（2026-07-13）。Flicking 与 Tracking 真实 producer、canonical Coach projection、Pi 可消费 context 以及 Python fallback sink 均已覆盖；未校准规则 fail-closed 为 `experimental/info`，并通过 raw/path/secret sentinel 回归。

### 目的

让 deterministic diagnosis 输出完整的“观察、解释、证据、claim level、训练、预期变化和复测”结构，并修正旧规则中过强的张力/身体/健康阈值措辞。

### Allowed files

- `kovaak_tracker/advice.py`
- `kovaak_tracker/advice_tracking.py`
- `kovaak_tracker/coach/diagnosis.py`
- `kovaak_tracker/coach/profiles.py`
- `kovaak_tracker/coach/knowledge.py`
- `kovaak_tracker/coach/agent_kb.py` only for source-level/wording corrections required by tests
- `kovaak_tracker/coach/agent.py`
- `kovaak_tracker/coach/agent_tools.py`
- `kovaak_tracker/coach/planning.py`
- `kovaak_tracker/coach/report.py`
- focused tests under `tests/coach/**`
- `webapp/backend/coach_context.py`
- `webapp/tests/test_coach_context.py`

### Tests first

- issue explanation 包含 metric/event refs、plain-language meaning、claim level、limitations；
- prescription 包含 cue、purpose、target metric、expected direction 和 verification；
- `community_consensus` / `experimental` 不能序列化为 `measured`；
- SPARC/reverse 等运动学现象不得直接输出“已测得张力问题”；
- 未校准阈值不产生正式“健康/异常”或 severity；
- canonical Coach projection 保留安全解释字段，不泄漏 raw/path/secret。

### Stop rule

- 需要发明新的健康阈值、诊断因果或训练剂量；
- 需要修改本轮用户已有未提交研究文档；
- 需要把 experimental 结果升级为 deterministic；
- 需要改变 Analysis/Run ownership。

## Task 2 — Input-native flick segmentation and core metrics

> 实施状态：completed（2026-07-13）。分段以 Raw Input 左键 rising edge 为可审计锚点；真实毫秒 timing、raw-count geometry、修正/反向离散事实、SPARC 重采样、分布与 descriptive outlier refs 已进入 AnalysisResult v2 和 deterministic diagnosis。仍保留 Windows 实机、高 polling-rate 实物、SPARC 跨 polling 可比性、噪声地板和 target-relative facts 的发布限制。

### 目的

按 evidence-first 顺序实现 Flick event 和用户已确认的核心运动学指标，使解释层有真实数据可用。

### Allowed files

- `kovaak_tracker/native_flicking_analysis.py`
- `kovaak_tracker/performance_parser.py`
- `webapp/backend/worker.py`
- `webapp/backend/contracts.py`
- `webapp/backend/coach_context.py` only for projecting new safe distribution/outlier fields
- focused tests in `tests/test_native_flicking_analysis.py`, `tests/test_performance_parser.py`, `webapp/tests/test_worker.py`, `webapp/tests/test_contracts.py`
- `webapp/tests/test_coach_context.py` only for the new metric projection fields

### Tests first

- flick start/peak/end、movement/accel/decel/settle timing；
- peak speed、time-to-peak、decel fraction；
- corrective/reverse/submovement counts and overlap；
- SPARC/smoothness；
- path length/efficiency/straightness；
- per-flick distribution、outlier refs、coverage/quality/limitations；
- high polling-rate、same-ms、nonuniform sampling correctness；
- 无 target/video facts 时不生成 target-relative error 或 overshoot。

### Stop rule

- 需要采用未验证的分段阈值或绝对健康区间；
- fixture 无法区分算法正确性与经验调参；
- 需要 Windows 实机才能决定但没有可复现 capture fixture；
- 需要让 MP4 成为 input-native 成功前置。

## Task 3 — Full Pi catalog, local provider profiles, and selected turn routing

> 实施状态：completed（2026-07-13）。完整 pinned Pi catalog、owner-scoped local profile、custom OpenAI-compatible profile、本地明文 API key persistence、selected `coach_runtime_turn.v1`、sidecar catalog/status/test、Provider failure isolation 与 SQLite v9 migration 已接通；Python `423 passed, 3 skipped`，Pi runtime `24 passed`，TypeScript 检查与 `git diff --check` 通过。OAuth/device-code 与正式 Provider UI 留给后续 Task。2026-07-15 correctness correction：Coach 与 video-fallback narration 不再经过固定 DeepSeek backend/CNY budget；只使用 owner selected profile，未建立 provider-specific usage/currency contract 时 legacy cost 保持 0。

### 目的

把 pinned Pi provider/model catalog 直接作为动态产品 catalog，移除 Aiming Cookie allow-list；实现 custom OpenAI-compatible profile（provider name、base URL、API key、model ID）、本地明文 credential persistence 与 owner/profile selected provider/model。将选择状态贯通 Python service/engine、turn payload、Pi sidecar 和 readiness，并把 `LLM_PROVIDER/providers.json` 降为显式、幂等且不做 silent model substitution 的 compatibility migration。

### Allowed files

- `webapp/coach-runtime/src/pi-source.ts`
- `webapp/coach-runtime/src/contracts.ts`
- `webapp/coach-runtime/src/turn.ts`
- `webapp/coach-runtime/src/sidecar-server.ts`
- `webapp/coach-runtime/src/provider-models.ts` (new)
- `webapp/coach-runtime/src/provider-profile.ts` (new)
- `webapp/coach-runtime/test/**`
- `webapp/backend/config.py`
- `webapp/backend/db.py`
- `webapp/backend/provider_store.py` (new)
- `webapp/backend/provider_commands.py` (new; provider/profile selection and credential mutation entry)
- `webapp/backend/coach_runtime.py`
- `webapp/backend/coach_engine.py`
- `webapp/backend/coach_service.py`
- `webapp/backend/health.py`
- `webapp/backend/routes.py`
- `webapp/backend/schemas.py`
- `webapp/backend/worker.py` only for compatibility narration/provider failure fallback
- `kovaak_tracker/coach/providers.py` and `kovaak_tracker/coach/providers.json` only for compatibility adapter/migration; they must not remain source of truth
- `webapp/tests/test_coach_runtime.py`
- `webapp/tests/test_coach_runtime_status.py`
- `webapp/tests/test_routes_coach.py`
- `webapp/tests/test_db.py`
- `webapp/tests/test_health.py`
- new focused provider store/command/service tests

### Tests first

- built-in provider/model catalog matches the repository-pinned Pi catalog without an Aiming Cookie allow-list; every Pi built-in is listable/selectable and routed through its Pi provider path；
- custom OpenAI-compatible profile requires provider name、base URL、API key、model ID and validates URL/model identity without returning the API key；
- profile、credential configured state and selected provider/model persist per owner/local profile across restart; plaintext local SQLite/config persistence is accepted, while API/ordinary logs/diagnostics/export/context/message sentinel scans remain clean；
- provider command set/replace/delete/select/test enforces owner/profile and never returns secret；
- Coach service/engine builds each turn from the selected provider/model; sidecar and subprocess receive the same versioned turn contract and no fixed DeepSeek default；
- sidecar `/healthz` and backend Coach status distinguish process liveness from provider/model/credential readiness; Coach not-ready does not fail app `/readyz` or deterministic product readiness；
- `LLM_PROVIDER/providers.json` compatibility migration is explicit and idempotent; unknown or obsolete `deepseek-chat` remains unavailable/needs-selection rather than being silently remapped；
- provider/model/credential/sidecar failure does not block Analysis、History、deterministic report/prescription；
- Pi coding-agent、shell、filesystem tools are absent from the product tool registry。

### Stop rule

- 需要重新引入 Aiming Cookie provider/model allow-list、云端 catalog、产品账号、用户 session/JWT、entitlement、云端计费或鉴权服务器；
- 需要把 obsolete/unknown provider or model 静默替换为其它选择；
- 无法维持 owner/profile isolation、turn/sidecar contract 一致性或 secret redaction；
- 需要自动暴露 Pi coding-agent、shell、filesystem 权限；
- 需要在本 Task 实现 provider-specific OAuth/device-code 流程，而不是留给 Task 4。

## Task 4 — Pi auth/OAuth/device-code and optional credential UX

> 实施状态：completed（2026-07-14）。认证能力直接从 pinned Pi `Provider.auth` 动态投影；API-key/ambient、OAuth/device-code callbacks、refresh、cancel/timeout、owner/profile credential persistence、local-only revoke、readiness isolation 与 secret redaction 已接通。完成 credential 持久化到 SQLite v10；进行中的 auth operation 按冻结设计仅在内存中，重启后明确 interrupted。2026-07-15 correctness correction：pinned Pi `ModelsError("auth"|"oauth")` 在 profile status 中稳定映射为 `needs_reauth`，保留无 secret 的 profile/model 投影，不再误报 `unconfigured`；custom OpenAI-compatible `base_url` 在 canonical store 拒绝 URL userinfo，避免嵌入式凭据进入公开 profile/API；refresh 失败只可按 operation 捕获的 credential revision 标记 `needs_reauth`，旧 operation 不得污染后写入的新 credential。正式 onboarding/Settings UI 仍由 frontend reconstruction Task 3/6 接入。

### 目的

在 Task 3 的完整 Pi catalog、local profile 和 credential persistence 之上，接通 Pi 支持的 API key/ambient auth、OAuth 与 device-code 流程，并提供可选的 credential 管理 UX。secure store 可作为后续 hardening，但不是本 Task Gate；任何认证方式都不得要求 Aiming Cookie 账号、中心 broker 或产品 session。

### 冻结实现边界

- Pi `Provider.auth`、`AuthLoginCallbacks`、`CredentialStore` 与 OAuth refresh 是认证行为事实源；Aiming Cookie 不维护 provider/auth allow-list，也不按 provider ID 重写 OAuth/device-code 流程；
- Python backend 继续拥有 owner/profile 与完成后的 credential canonical persistence；TypeScript sidecar 只运行 Pi auth operation，并使用 request/operation-scoped credential store，不直接写 SQLite；
- 完成后的 API key/OAuth credential 按 owner/profile 明文保存在本地 SQLite；进行中的 login operation 仅保存在本地 sidecar/backend 内存，重启后明确变为 `interrupted` 并允许重试，不实现 OAuth PKCE/device-code 中间状态恢复；
- `device_code` 是 Pi login 期间动态发出的 interaction event，不是 Aiming Cookie 静态 provider allow-list 或独立 credential 类型；
- `revoke` 在本 Task 明确定义为删除 Aiming Cookie 本地 credential/logout，返回 `remote_revoked=false`；不声称已撤销 Provider 服务端 token；
- custom OpenAI-compatible profile 继续要求 API key；`local` / `auth_modes=none` 不在本 Task 静默扩展，只有 pinned Pi runtime 实际支持的 built-in no-auth/ambient 状态才能动态呈现；
- product timeout：interactive authorize 最长 15 分钟（Pi 返回更早 expiry 时采用更早值），refresh 与 connection test 最长 30 秒；terminal auth operation 在 sidecar 内保留 5 分钟供 backend 读取结果；
- status 只做 profile/model/credential/ambient readiness 解析，不发送 LLM completion；只有显式 connection test 才允许产生 Provider 请求和费用。

### Allowed files

- `webapp/coach-runtime/src/pi-source.ts`
- `webapp/coach-runtime/src/provider-models.ts`
- `webapp/coach-runtime/src/provider-profile.ts`
- `webapp/coach-runtime/src/provider-auth.ts` (new)
- `webapp/coach-runtime/src/contracts.ts`
- `webapp/coach-runtime/src/turn.ts`
- `webapp/coach-runtime/src/sidecar-server.ts`
- `webapp/coach-runtime/test/**`
- `webapp/backend/db.py`
- `webapp/backend/provider_store.py`
- `webapp/backend/provider_commands.py` (new)
- `webapp/backend/provider_auth.py` (new)
- `webapp/backend/coach_runtime.py`
- `webapp/backend/coach_service.py`
- `webapp/backend/coach_engine.py` only for shared credential-aware error redaction
- `webapp/backend/health.py`
- `webapp/backend/routes.py`
- `webapp/backend/schemas.py`
- focused Python/TypeScript tests and fixtures
- optional frontend credential controls only through the separately authorized frontend reconstruction Task; no mandatory Rust secure-store dependency

### Tests first

- Pi API key/ambient auth resolution and provider-scoped isolation；
- OAuth browser/device-code authorization success、cancel、15-minute timeout、state mismatch、refresh、needs re-auth、local-only revoke；
- optional API key set/replace/delete/status UX never reads back secret and works with Task 3 local persistence；
- completed credential state persists per owner/profile；in-flight operation restart is explicit `interrupted` rather than pretending to resume provider-specific state；concurrent authorize/refresh/revoke/delete is serialized；
- auth/credential events、ordinary logs、diagnostics、Coach context/message and export contain no secret；
- no flow creates or requires Aiming Cookie account、session/JWT、central credential broker or cloud LLM proxy；
- auth unavailable/expired only changes Coach readiness and recovery action, not Analysis、History or deterministic report readiness；
- Provider status performs no completion request；explicit connection test uses a 30-second maximum；Pi catalog/auth capability projection contains no product allow-list or provider-specific branch。

### Stop rule

- Provider auth requires an Aiming Cookie product account、central credential broker、user auth server、paid entitlement or cloud LLM proxy；
- 实现要求把 OS secure store 变成 prerequisite，或禁止 Task 3 已确认的本地 SQLite/config credential persistence；
- Pi pinned source does not support the claimed provider auth flow and no explicit local adapter contract exists；
- credential UX cannot preserve owner/profile isolation、explicit consent、cancellation or secret redaction；
- 认证失败会阻塞 deterministic product paths or global app readiness。

## Task 5 — Pi knowledge tools and user-level product commands

**状态：完成（2026-07-14）。** 已实现按 topic 渐进 knowledge 种子能力、Pi 三工具 registry、owner-scoped 共享产品命令、Training Plan SQLite lifecycle/version、typed navigation/evidence locator、turn-scoped loopback bridge、confirmation、append-only audit 与独立 idempotency replay；验证见 [`../../PROGRESS.md`](../../PROGRESS.md)。2026-07-15 correctness correction：所有 app-owned SQLite connection 现在显式开启 foreign-key enforcement，使 Provider profile/credential 与 Training Plan/版本/转换的 DDL 关系实际生效，并新增 orphan child 写入失败回归；v11/v12 multi-statement schema helper 改为逐 statement 在调用方 transaction 内执行，不再由 `executescript()` 隐式提交并破坏 rollback；Training Plan diagnostic/context 与版本 evidence refs 现在强制匹配冻结的 ref kind；command journal 的 SQLite 写入现在原子拒绝同 key 不同 digest，同 digest 的后到 reservation claimant 只 replay 已有记录，confirmed reservation 冲突会回滚 confirmation 消费；Pi product-command bridge 与 Python command boundary 现在都对整段字符串检测嵌入式绝对路径/URL，模型参数 dispatch 与 backend result 入模均 fail-closed。Versioned Knowledge Registry 与 product-command bridge 解耦已由独立 completed plan 完成并归档；Task 5 当前消费 canonical Registry，稳定合同见 active Registry spec，当前验证状态见 [`../../PROGRESS.md`](../../PROGRESS.md)。Task 6 未开始。

### 目的

把现有 diagnosis/progress/planning knowledge capability 迁入当前 Pi Coach，并接入用户级产品命令。

### Allowed files

- `webapp/coach-runtime/src/turn.ts`
- `webapp/coach-runtime/src/contracts.ts`
- `webapp/coach-runtime/src/analysis-summary-tool.ts`
- `webapp/coach-runtime/src/knowledge-tools.ts` (new)
- `webapp/coach-runtime/src/product-command-tools.ts` (new)
- `webapp/coach-runtime/test/**`
- `webapp/backend/coach_context.py`
- `webapp/backend/coach_engine.py`
- `webapp/backend/coach_runtime.py`
- `webapp/backend/coach_service.py`
- `webapp/backend/coach_store.py`
- `webapp/backend/coach_commands.py` (new)
- `webapp/backend/training_plan_store.py` (new)
- `webapp/backend/routes.py`, `schemas.py`, `db.py`, `history_trends.py`, `kovaak_run_store.py`, `queue.py` only for shared command handlers and stable read/write models
- focused Coach/History/Analysis/plan tests

### Frozen decisions（点点于 2026-07-14 确认）

- 本地采集器可扫描产品已知或用户配置的工作区外游戏数据目录；Coach 只操作采集器/UI 已登记的 Run、Analysis 和 artifact ref，不获得任意路径、shell 或 filesystem tool。
- Training Plan lifecycle 固定为 `draft -> saved -> active <-> paused`；每 owner 最多一个 active plan；替换 active plan 必须确认；adjust 创建递增版本并保存理由、证据 refs 和 verification targets；review 只读；本 Task 不新增 completed/archive/delete/retention。
- 用户明确要求的普通可恢复动作直接执行；Coach 自主推断写操作与 destructive/credential/external 动作返回 `needs_confirmation`。
- 手动 retry 可覆盖 `retryable=false`，但必须保留受管输入；已有其它 queued/running Analysis 时返回 `unavailable`；write command 按 owner + command + idempotency key replay 或 conflict。
- `ui_event` 是 allow-listed semantic target，不接受模型提供的 URL/path；Task 5 生成事件，Task 6 前端消费。
- MP4 是可选视觉回放/evidence locator；input-native、Performance 和 Stats 是计算与诊断主事实源。
- Training Plan 版本、状态转换、理由和引用使用 SQLite 稳定记录，不以临时文件或 Coach 文本作为事实源。

### Tests first

- 按当前 issue/topic 渐进检索 knowledge，不全量预载；
- source_ref/source_level/claim discipline 进入最终回答约束；
- History/query/navigation 与 UI 共用 handler；
- explicit user request 创建 Analysis 不重复确认；
- destructive/credential/external/Coach-inferred write 返回 `needs_confirmation`；
- save/activate/pause/adjust Training Plan 与 verification targets；
- Training Plan diagnostic context 各 typed ref 字段拒绝错 kind，版本 evidence 只接受 Analysis/metric/knowledge refs；
- command idempotency、owner、audit、unavailable/deleted refs；
- 工具白名单不含 bash/read/write/edit/coding-agent。

### Stop rule

- 需要新增未冻结的删除/retention 或 Training Plan lifecycle；
- 需要 Coach 直接写数据库或调用 shell；
- 需要把 KB 社区内容当 deterministic ground truth；
- 需要跳过 owner/confirmation/audit。

## Task 6 — Provider Settings and Coach sidebar wiring

**状态：后置。** 点点于 2026-07-14 明确裁决正式前端最后处理；先完成 Roadmap 中的版本化 Knowledge Registry、Coach/Analysis/data 后端真实 E2E 与 Desktop/runtime Gate。前置未闭合且未重新授权前，不执行本 Task。

### 目的

在正式前端 Task 3 已建立最小 Provider onboarding 后，接入完整 Provider Settings、Coach 解释引用、产品命令状态和训练计划操作。

### Allowed files

使用 active frontend reconstruction plan Task 6 的 Allowed files；本 Task 不单独扩大前端范围。

### Tests first

- Provider list/status/add/edit/test/select/API key replace-delete/OAuth/device-code/local provider；
- secret 永不回显或进入前端持久化；
- Provider 未配置、needs re-auth、connection failed 可恢复；无需认证的 Provider 不显示认证状态；Settings 与 onboarding 共用状态，第一次 Analysis 完成且 Provider ready 时 Coach 自动展开一次；
- Coach explanation 展示可点击 evidence、claim level、training target 和 verification；
- query/navigation/direct action/confirmation/result/error event；
- provider 故障不影响本地 Analysis/History；
- wide/medium/narrow、keyboard、focus、reduced motion 和 screenshot Gate。

### Stop rule

- frontend reconstruction Task 2–5 前置未完成；
- Task 3/4/5 capability API 尚未实现；
- UI 需要伪造 provider/auth/command 成功；
- 需要恢复旧 CoachClient、Settings 或 session-bound route。

## 全局验证 Gate

- Python full suite；
- Pi coach-runtime tests；
- frontend type/test/build；
- Rust fmt/check/clippy/test；
- secret/path/raw trace sentinel audit；
- active docs local-link check；
- Browser + Desktop provider/Coach/Analysis real flow；
- Windows Raw Input real-machine and high-rate fixture Gate before removing Preview。
