# Aiming Cookie 当前进度

> **最后整理：2026-07-16。** 本文是当前快照，不是产品或架构事实源。详细研发流水见 [`archive/history/`](archive/history/)；产品、架构与 UI/UX 结论分别以 [`PRD.md`](PRD.md)、[`ARCHITECTURE.md`](ARCHITECTURE.md) 和 [`frontend-uiux-design.md`](frontend-uiux-design.md) 为准。

## 1. 当前结论

- 产品方向仍是 Desktop-first local-first、flicking 先行、确定性诊断主路径；Aiming Cookie 开源免费，不提供产品账号、登录或用户鉴权服务器；Coach 是 Provider 可用时的长期关系与产品操作层。pinned Pi built-in provider/model catalog 已确认为完整产品 catalog，不另设 Aiming Cookie allow-list。
- KovaaK Run 自动发现、Stats / Performance 解析、Windows Raw Input、AnalysisResult v2、History read model、Coach diagnostic context 与本地 Benchmark store 已形成不同成熟度的代码基础。
- RefleK active plan 的 Task 2–6A 已完成当前平台收口：v2 producer 与 frozen source identity fail-closed；三种 worker mode 已正式验收，managed MP4 在外部副作用前做 pre/post/final revision 校验；Coach diagnostic context 由单一 allow-list projector 生成，并在 Python/Pi/tool/store/API 边界重复校验；History/Run 使用轻列表与 lazy detail，并以严格 identity/coverage/alignment Gate 生成可比趋势。input-native 仍受真实 Windows、高 polling-rate、噪声地板与阈值校准 Gate 约束。
- **input-native 当前仍只能作为 Preview / Experimental 能力。** Raw Input 左键锚定 Flick、真实毫秒 timing、raw-count path/efficiency、修正/反向离散事实、trough-depth submovement proxy、SPARC 均匀重采样与 cutoff-normalized frequency axis、分布/outlier refs 与 deterministic diagnosis 已接入 worker 和 v2；native 已补齐 `submovement_overlap` 兼容键但明确携带非 temporal-overlap limitation；SPARC 已使用独立 v2 metric version，旧版值不参与同一趋势，v2 在真实数据校准前不套用 legacy 绝对阈值；但 Windows 实机、高 polling-rate 实物、噪声地板、SPARC 跨 polling 可比性和 target-relative facts 仍未闭合，不得描述为发布完成。
- multimodal 冻结为“native 结果是主事实、MP4 主要提供直观回放、问题定位和可验证视觉证据”；视觉阶段复用 native 阶段已冻结并解析的 Stats，不再按用户源路径重读另一 revision；视觉失败时保留 native 结果并显示视觉证据不可用。video-fallback 继续作为 compatibility path，不是长期主分析方向。
- Benchmark 后端能力保留，但不进入 v1 正式前端、不进入默认 Coach context、不启用在线 provider 或 leaderboard。
- Frontend reconstruction Task 1 已在点点确认精确范围后删除 History / Run / Evidence prototype、临时 App shell 与旧全局样式；当前只保留 capability adapters 与 Tauri/runtime，正式产品路由暂时不可用。
- 当前发布状态仍为 **No-Go**。
- Versioned Coach Knowledge Registry 已完成：Flicking、Tracking、身体/张力候选解释、settings 单变量实验、practice、verification 与 limitations 已迁入单一 canonical asset；Python/TypeScript 共用确定性检索，Draft 2020-12 `schema.v1.json` 已补齐完整 entry/source/claim structural contract 并对 canonical asset 做标准验证，Pi knowledge tool 与 product-command bridge 已解耦，历史 trace 只保存版本化引用。Windows developer/runtime compatibility 的 Task 1–2 自动化 Gate 已完成；正式前端仍按点点裁决最后处理。
- 2026-07-15 Windows 实机已闭合四个可重复开发阻断：Node loader 改用 file URL，Python subprocess 固定 pinned Pi source/tsconfig，knowledge parity 按平台选择 venv Python，Tauri 补齐由 tracked PNG 机械生成的 compile-time ICO。Python、Coach/Pi source、Pi AI、frontend adapters 与 Tauri MSVC 自动化 Gate 均通过；正式 frontend route、GUI、真实 KovaaK、Raw Input 与高 polling-rate 仍未闭合，因此发布状态保持 **No-Go**。
- 2026-07-16 Windows Desktop pre-frontend Task 1–2 已完成：无 override 时从 registry + `libraryfolders.vdf` + exact app manifest fail-closed 发现 E 盘 KovaaK，多安装要求显式消歧；每个 Stats/Performance 目录只发射最新 50 个 supported files；Coach fallback Node child 不再继承 Desktop launch token。点点已设后端前置硬终点，非启动/构建、核心路径、数据损坏或安全泄漏问题统一 deferred；正式前端具体 Task 尚未获得授权。

## 2. 能力成熟度

### 2.1 Implemented foundation（代码已有）

- Tauri 2 Desktop shell、本地 Python API/worker 生命周期、动态 loopback 地址与 launch-scoped Desktop token；
- native MP4/CSV path import、managed App Data workspace、storage accounting 与 terminal Analysis 删除基础；
- FastAPI queue、worker recovery、workspace/streaming upload、health/readiness；
- 既有视频 + Stats flicking CV、诊断、处方和报告领域逻辑；
- Pi-based Coach runtime、sidecar、服务编排与持久化基础；
- Versioned Coach Knowledge Registry、Python compatibility adapters、Pi `get_coach_knowledge`、source/claim/limitation discipline 与版本化安全 trace；
- `.perf` protobuf-wire parser、KovaaK Stats / Performance watcher、稳定文件判断与 Run upsert；
- SQLite `kovaak_runs`、Windows-only Raw Input、versioned snapshot 与 trace window extraction；
- AnalysisResult v2、三种 input mode dispatch、History/Run read models、Coach allow-list projection 与 Benchmark local store。

### 2.2 Contracted capability（产品/spec 已冻结）

- Run 独立于 Analysis；Stats、Performance、Raw Input、MP4 是不同 evidence source；
- input-native、multimodal、video-fallback 必须显式表达 mode、availability、alignment 和 limitations；
- Raw Input 默认关闭、仅 Windows、仅 KovaaK process gate、本地保留、默认不进入 Coach；
- History 采用轻列表 + lazy detail；Coach 使用结构化、用户可见的 diagnostic context，并通过与 UI 共用的产品命令调用当前用户能力，不限制为只读；
- Coach explanation 必须形成观察、白话解释、证据、claim level、训练 cue、预期变化和复测链；
- 当前不设付费墙；首次启动以 Provider onboarding 为主路径但允许明确跳过。Settings 使用完整 pinned Pi catalog，并支持 custom OpenAI-compatible profile；API key 可明文保存在本地 SQLite/config，secure store 不是 blocker，但所有外发/日志/诊断/导出边界继续 redaction；
- terminal Analysis、Run metadata、Run-owned trace、用户 Stats/Performance 源文件和 Analysis-managed MP4 副本具有不同 ownership 与删除影响。

### 2.3 User-reachable / release-ready（尚未完成）

- 原 prototype 已删除；当前没有正式用户路径，不能作为视觉或产品验收证据；
- 正式 Provider onboarding、App shell、New Analysis、Tasks、History、Analysis workspace、Coach sidebar、Settings 尚待按新 frontend spec/plan 重建；
- Coach productization Task 3–5 backend/runtime capability 已完成；首次 Provider onboarding、命令结果消费和完整 Coach sidebar 仍不可交付，因为正式 frontend Task 2/3/6 尚未授权和实现；
- input-native、multimodal、video-fallback 三条真实 Browser/Desktop E2E 未形成；
- Windows 实机、高 polling-rate 鼠标、真实 KovaaK 对齐与性能尚未通过发布 Gate；
- installer、正式 icon、签名、公证、updater 和可信云服务仍未完成。

## 3. 当前高优先级实现差距

### Analysis / evidence

1. Task 2/3 的当前平台合同与 snapshot revision correctness 已闭合；正式 angular/target-relative trajectory、噪声地板、SPARC 跨 polling 可比性和 Windows 实机阈值仍待校准；
2. Task 4 已正式收口三种 mode dispatch：input-native 不读取视频，multimodal 视觉失败保留 native result，video-fallback 不生成 Raw provenance；run-based 视频在 CV 前、provider/report 前和 terminal write 前校验 managed revision，变更时 fail-closed 且不泄露路径；v1/unversioned legacy 继续可读；
3. History trend 的 scenario identity / calibration / metric contract 已严格 fail-closed；当前 video-fallback coverage 为 `null`，真实 calibration 与完整 evidence 未满足时不会输出趋势，真实数据可比性仍需后续 Gate 验证。

### Reliability / lifecycle

1. 同 stem 同类型 KovaaK 文件冲突、并发 trace attach、partial import 和 orphan recovery 仍需更强状态机与测试；
2. terminal Analysis 的 commit-first logical delete、transient tombstone、managed workspace cleanup 与 startup ready 前 reconciliation 已完成；Run / trace / source 删除 UI 与长期 retention 仍不在本轮范围；
3. launch token 子进程隔离已闭合；Python runtime READY 后崩溃与正式浏览器 media identity 保持 deferred，Windows Python↔Pi subprocess 的 file URL、pinned tsconfig 与 venv path 自动化 Gate 已闭合。

### Frontend reconstruction

1. Prototype 已删除，不得恢复；当前无 `app/` / `pages/` 导致 `next build` 失败，需在点点明确授权相应正式前端 Task 后处理；
2. 正式路由、低保真结构、页面状态矩阵、Desktop/Web capability 表和 accessibility Gate 已由 UI/UX 与 active reconstruction spec 冻结，但尚未实现；
3. executable token/theme/primitives 尚未建立；Task 2 尚未获得具体 Task 授权；
4. Benchmark 不进入 v1 正式前端；
5. Task 1 已完成 prototype 删除与 adapter 边界保护；Task 2–7 尚未获得具体 Task 授权。

### Coach / explanation / provider

1. 当前所有 v1 Pi turn 均注册 `get_analysis_summary` 与 `get_coach_knowledge`，仅有固定 loopback product bridge 时才增加 `run_product_command`；knowledge 按 signal/metric/topic/use 确定性检索最多三条完整 entry，不向 Coach 暴露 shell、任意文件、数据库或 coding-agent 工具；
2. Coach Provider Task 3–4 已接通完整 pinned Pi catalog、owner/profile selection、custom OpenAI-compatible profile、SQLite v10 type-tagged credential persistence、selected `coach_runtime_turn.v1`、动态 Pi auth capability、API-key/ambient、OAuth/device-code callbacks、refresh、local-only revoke、sidecar status/test 与 readiness isolation；`LLM_PROVIDER + providers.json` 只保留 legacy compatibility，不再是 provider/model 事实源；Coach 与 Analysis narration 也已移除固定 DeepSeek backend/CNY budget gate，未建立 provider-specific usage/currency contract 时不写伪精确 `llm_cost_cny`；
3. Coach productization Task 1 已完成：Flicking/Tracking explanation、claim、limitations、训练目标与复测合同已进入真实 producer，canonical context 与 Python fallback 均保留安全字段；未校准规则统一 fail-closed 为 `experimental/info`；
4. Coach 已能通过与 UI route 共用的 owner-scoped handler 查询 Run / History / Analysis、比较分析、生成 typed navigation/evidence/video-time event、创建或重试 Analysis，并生成、保存、激活、暂停、调整和 review 带版本理由与 verification targets 的 Training Plan；普通明确写操作直接执行，Coach 推断写操作和替换 active plan 走持久确认，命令具有 append-only audit 与独立 idempotency replay；正式前端尚未消费这些事件和确认；
5. API key/OAuth credential 明文本地 persistence 已按 local-first 决策完成；进行中的 auth operation 不持久化 provider-specific 中间状态，backend/sidecar 重启后明确 interrupted 并允许重试；正式前端 credential UX 仍待 frontend Task 3/6。
6. Versioned Knowledge Registry 已完成并作为唯一运行时知识正文：当前含 43 条 active canonical entries、19 个 canonical signals 与 67 条逐项 migration audit；覆盖 Flicking、Tracking、身体/张力候选假设、settings、practice、处方验证和反证限制。Python/TS parity、真实 Analysis signal/metric → Pi retrieval → SQLite trace E2E 已通过；trace 仅保存 registry/entry/version/source/claim refs，不保存知识正文、路径、secret 或 raw payload。
7. 点点提出后续 Coach 讲解需要更直观：先说人话，再按需展开指标，并探索图标、轨迹/阶段动画、问题区段标记和前后对比；这只是待讨论的产品/视觉方向，尚未冻结进 frontend contract，也未实施。

## 4. 下一步

> **新 session 接力点：** Windows Desktop pre-frontend Task 1–2 已完成并归档。正式前端具体 Task 尚未获得授权；未经指示不提交、不推送，也不把 GUI 或真实 KovaaK/Raw Input 发布 Gate 写成已通过。

1. Windows Desktop pre-frontend Task 1–2 已正式验收；当前没有新的已授权切片，等待点点明确指定；
2. 后端非硬阻断缺口统一 deferred；未来正式前端只消费已稳定 capability，不复制命令逻辑，也不反向定义后端语义。

## 5. 当前工作树与实施计划状态

2026-07-15 Windows 验证开始前，tracked worktree 为 clean；依赖安装在仓库生成未跟踪 `.venv/`。Task 1 修改 Coach/Pi 启动兼容、对应回归、sidecar 脚本与一行 Rust lint，并新增 placeholder ICO；Task 2 只更新开发指南与本进度快照；完成后的文档 closeout 将 plan 移入 completed archive，并同步文档入口、Roadmap 与 plan index。未 reset、checkout、覆盖、清理、提交或推送。

RefleK active plan 当前成熟度：

| Task | 当前状态 | 未闭合验收 |
|---|---|---|
| Task 1 Raw Input / ingestion | current-platform foundation 已实现 | Windows 实机、完整 Windows target 与高 polling-rate Gate 未通过 |
| Task 2 AnalysisResult v2 contract | **completed（当前平台）** | producer/persistence Gate 已强制 owner、analysis version、metric provenance/coverage/limitations、artifact ownership/managed/local-only/version，并将 analysis/run/type/mode identity 绑定到已 claim session；旧未版本化 v2 可读但不可作为新 terminal result 写入 |
| Task 3 input-native adapter | **completed（当前平台）** | Stats、Performance、Raw trace revision 在提交时冻结；worker 读取一次并以同一组 bytes 完成 fingerprint 校验和 parse/decode，不再通过路径二次打开；source 缺失、不可读或 revision 不一致稳定归类为不可重试的 `input_validation / source_unavailable`，需重新提交新 snapshot，且不泄露本地路径或底层异常。click-anchored Flick、timing、raw geometry、修正/反向、trough-depth `submovement_overlap` proxy、SPARC cutoff-normalized frequency axis、分布/outlier 与 diagnosis 已接入；SPARC 公式修正使用独立 v2 metric version，旧版不混入趋势，v2 绝对阈值暂不启用。Windows 实机、高 polling 实物、噪声地板、跨设备可比性和 angular/target-relative facts 仍属发布 Gate |
| Task 4 worker mode dispatch | **completed（2026-07-15）** | 三模式均写 v2；input-native 无视频依赖，multimodal 保留 native 主事实，video-fallback 无 Raw provenance；managed MP4 在消费与外部 provider 副作用前校验，legacy fallback/read 保留 |
| Task 5 Coach diagnostic context | **completed（2026-07-15）** | 单一 allow-list projection、跨 Python/Pi/tool/store/API 结构一致、v2 deterministic classification、stable ref、raw/path/URL/payload/duplicate-key sentinel 与 deleted→unavailable 均已验收 |
| Task 6 History / evidence replay | **Task 6A completed（2026-07-16）；6B frontend deferred** | backend light list/lazy detail、frozen availability/trace、evidence locator、共享 replay Gate 与严格 comparability 已验收；正式 History UI 最后处理 |
| Task 7 Benchmark local domain | local store / API 已实现；历史 prototype UI 已由 Frontend Task 1 删除 | 不进入 v1 正式前端、默认 Coach 或在线 provider；正式产品化仍 deferred |

Frontend reconstruction plan 已 active。Task 1 已在点点确认 10 个文件的精确范围后完成；inventory 为 `webapp/frontend/prototype-inventory.json`，adapter boundary test 为 `webapp/frontend/lib/prototype-boundary.test.ts`。Task 2–7 尚未获得具体 Task 授权。

Coach productization plan 已 active。Task 1 explanation contract、Task 2 input-native core metrics、Task 3 dynamic full Pi catalog / custom profile / selected turn routing、Task 4 Pi auth/OAuth/device-code / local credential commands 与 Task 5 Pi knowledge / user-level product commands 已完成；Task 6 尚未开始，且受 frontend reconstruction Task 2–5 前置约束。

Versioned Coach Knowledge Registry plan 的 Task 1–6 已全部完成并归档；active spec 继续作为 Registry 版本、检索、claim 与 trace 的稳定局部合同。

## 6. 最近验证记录

2026-07-16 Windows Desktop pre-frontend Task 1–2 与正式前端转场：

- Steam discovery tests-first 最终 `38 passed, 1 skipped`；本机无 override 解析到 `E:\SteamLibrary\steamapps\common\FPSAimTrainer`，Stats/Performance 均存在，首次 scan 为 `50 / 6`；missing/malformed `libraryfolders.vdf` 对对应 root fail closed；
- launch-token isolation 红基线 `1 failed`，focused `45 passed`；普通 caller env 与 pinned Pi paths 保留，Node fallback child env 不含 `AIMING_COOKIE_DESKTOP_TOKEN`；
- 完整 Windows Python `744 passed, 3 skipped`；Pi AI `473 passed, 733 skipped`；Coach runtime `63 passed`；frontend type-check 与 `4 passed`；Rust MSVC fmt/check/test/clippy 通过，Rust tests `16 passed`；
- frontend `next build` 因 prototype 删除后尚无 `app/` / `pages/` 失败；该失败归类为正式前端启动/构建硬阻断，待点点明确授权相应 frontend Task 后解决，不回退 prototype、不扩大后端审计；
- Luna 最终只读 review 的唯一 finding（VDF 损坏时可能绕过多安装消歧）已修复。Windows Desktop pre-frontend plan 已完成并归档。

2026-07-16 Analysis deletion/reconciliation Task 3 — startup reconciliation 与 API outcome：

- FastAPI lifespan 在 `init_schema()` 后、API ready 前恰好运行一次 tombstone reconciliation；pending/failed workspace cleanup 在启动时幂等重试，普通 Windows workspace `OSError` 保留 tombstone、记录 aggregate counts 与稳定 `workspace_cleanup_failed` code，但不阻止启动；
- tombstone finalize 的意外 SQLite/Schema 错误不再被误分类为 workspace failure：共享连接 rollback 后异常向上传播，lifespan 不进入 ready；日志不含绝对路径、底层异常文本、`OSError` 或 traceback；
- DELETE response envelope 保持 `deleted/id/files_removed/cleanup_failed`；删除后 Analysis detail/video/timeline 为 unavailable，Coach messages/context 保留，wire ref 为 `unavailable`；`desktop_runtime.py` 未重复执行 reconciliation；
- tests-first 红基线 `3 failed, 2 passed`；Task 2+3 合并 focused `131 passed`；完整 Windows Python `719 passed, 3 skipped`；compileall、全仓 `git diff --check` 与 Agent contract byte parity 通过；
- Luna 最终只读 review 无剩余 finding。Analysis deletion/reconciliation implementation plan 已完成并归档，active spec 继续作为长期局部合同。

2026-07-16 Analysis deletion/reconciliation Task 2 — commit-first delete 与幂等 cleanup：

- `queue.delete_session` 的 Phase A 仅在同一 SQLite 事务内迁移 legacy Coach message、标记 Analysis ref deleted、写 pending tombstone 并删除 session/chat；commit 后才清 managed workspace；
- workspace cleanup 的 absent/success、partial/OSError、cleanup 后 crash、tombstone finalize commit failure 与 failed-state 写入 failure/cancellation 均可确定收敛；普通 finalize DB failure 保持 `deleted=true` API 语义，进程级中断传播前清理共享连接事务；
- reconciliation 只处理 tombstone；workspace `OSError` 逐项隔离且幂等，tombstone finalize DB failure 则 fail-closed；Run row、Run-owned trace 和用户 Stats/Performance/source MP4 保持不变；
- tests-first 红基线 `67 passed, 7 failed`；Task 3 集成返工后的 Task 2 focused `79 passed`；最终完整 Windows Python `719 passed, 3 skipped`；compileall、全仓 `git diff --check` 与 Agent contract byte parity 通过；
- Luna 最终只读 review 无剩余 finding。Task 3 已完成，见上方最新验证记录。

2026-07-16 Analysis deletion/reconciliation Task 1 — SQLite v13 transient tombstone：

- schema v12→v13 新增单用途 `analysis_deletion_tombstones`；exact DDL 约束 positive Analysis id、non-empty owner、pending/failed state 与 attempt/error code 一致性，不保存 path，不建 sessions FK、state index 或 completed retention；
- v13 DDL 不进入事务前 `SCHEMA`；fresh 与 v12 upgrade 通过同一 helper 在外层 `BEGIN IMMEDIATE` 中创建 table 并提交 user_version。完整 failure injection 验证 table 创建后异常会同时 rollback table 与 user_version；v13 self-heal 不覆盖既有 tombstone；
- tests-first 红基线 `18 passed, 16 failed`；实现后 focused DB `34 passed`；完整 Windows Python `697 passed, 3 skipped`；compileall、scoped `git diff --check` 与 Agent contract byte parity 通过；
- Luna 最终只读 review 无 finding。Task 2 后续已完成，见上方最新验证记录。

2026-07-16 RefleK Task 6A History / Run read models、evidence replay 与 comparable trends：

- Analysis/Run list 不返回完整 result/summary，且只做 stat availability；detail 才 lazy load summary、evidence locator 与完整 SHA revision。Analysis trace 状态只从 session frozen snapshot 推导，不受 Run 当前状态变化影响；
- `input_native` 固定为 native-only；v2 managed MP4、v1 manifest 与 unversioned legacy read 通过同一 workspace/replay Gate，视频 endpoint 复用该 Gate；视觉 evidence unavailable 不会错误禁用仍完整的 managed MP4 replay；
- comparability 要求 v2、相同 analysis/scenario identity/input mode、完整 deterministic metric/evidence coverage、允许的 alignment、相同 metric version/unit 与显式 calibration；任一 malformed 或缺失均 fail-closed，不输出 delta/percent；
- Luna 最终只读审阅发现并闭合三项边界：公开 source ref 只接受完整 64 位 hex SHA-256，Run detail 缺失/畸形 fingerprint 报 invalid，comparability identity 只接受安全非空字符串与冻结 input mode；
- focused History/Run hardening `66 passed`；Task 6A 扩展 backend 回归此前为 `255 passed`；完整 Windows Python `681 passed, 3 skipped`；Python compileall、app import、`git diff --check` 与 Agent contract byte parity 通过；
- 未运行正式 frontend/Rust/Coach runtime 重验，因为本 Task 未修改对应实现；正式 History UI、真实 Windows KovaaK/Raw Input、高 polling-rate 与真实三模式 E2E 仍未验证。

2026-07-15 RefleK Task 5 Coach diagnostic context：

- canonical context 在 Python projector/runtime、Pi request、TypeScript tool、SQLite message 与 Coach API 使用同一结构；stored context 写读均重投影，legacy message 保留正文但不复制未验证 trace；
- v2 metric/comparison 必须显式 deterministic；v1 legacy metric 可读但 mode 为 `unknown`。stable Analysis ref 校验 version/type/mode/id，并拒绝路径、`file:`、任意 `scheme://`、credential、raw sample/timestamp、payload、heuristic、Benchmark 与 duplicate JSON key；
- Analysis 删除后内部 ref 保持 `deleted`，Coach wire 为 `unavailable`，历史消息/context 保留且不可重新附加；Primary POST 返回 context 与实际存储一致；
- Task 5 focused Python `131 passed`；完整 Windows Python `635 passed, 3 skipped`；Coach runtime `63 passed`；strict TypeScript、Biome、Python compileall 与 `git diff --check` 通过；
- 范围外残余：`queue.delete_session` 在 DB commit 前删除 workspace，尚无 tombstone/reconciliation；已转入下一轮数据可靠性，不将其描述为删除可靠性已完整闭合。

2026-07-15 RefleK Task 4 worker mode dispatch：

- tests-first 补齐 queued 后 managed MP4 缺失、截断、同尺寸替换、CV 期间变化、缺 frozen identity、source/managed mtime 差异、input-native 忽略视频、multimodal 成功/失败语义、fallback 路径安全和 v1/unversioned legacy read；
- run-based multimodal / video-fallback 以线程内流式 SHA-256 + size 做 pre/post/final 校验；fallback 在 provider lookup / report 前完成 post-check，避免对已变更视频产生外部请求或费用；普通 decoder/CV 异常只记录异常类型，不泄露本地路径；
- `webapp/tests/test_worker.py` 为 `42 passed`；worker/contracts/queue/routes 相邻回归 `126 passed`；MP4 freeze/copy 定向回归 `6 passed`；
- 完整 Windows Python 仓库 `610 passed, 3 skipped`，`compileall` 与 `git diff --check` 通过；
- 残余风险是 path-based CV 重复打开固有的 change-and-restore TOCTOU；当前 app-managed workspace 加 pre/post/final hash 已满足 Task 4 合同，更强字节级绑定需要改变 CV 输入边界并超出本 Task。

2026-07-15 Windows developer/runtime compatibility Task 1–2：

- Python：`.venv/Scripts/python.exe -m pytest -q` 为 `591 passed, 3 skipped`；`compileall` 通过；
- Coach runtime：显式使用 Windows `PYTHON_BIN`、绝对 `PI_SOURCE_DIR`、pinned `TSX_TSCONFIG_PATH` 与 loader file URL，`59 passed`；不依赖或生成 Pi `dist`；
- Pi `packages/ai`：`69` 个 test files passed、`25` skipped，`473 passed, 733 skipped`；npm 仅报告 `min-release-age` 将在下一主版本停止支持的非阻断 warning；未运行非产品边界的 AgentHarness、coding-agent、filesystem/shell 套件；
- frontend：`npm.cmd run type-check` 通过，adapter tests `4 passed`；`npm.cmd run build` 因不存在 `app/` 或 `pages/` route 而 exit `1`，按冻结决策记录为正式 frontend 未闭合，不伪装为 Windows Gate 通过；
- Tauri MSVC：`fmt --check`、`check --locked --all-targets`、`clippy --locked --all-targets -- -D warnings` 通过，Rust tests `16 passed`；test link 阶段有一条来自本地化 MSVC stdout“正在创建库”的非阻断 `linker_messages` warning；
- 仓库检查：active plan/spec index 链接与目录登记一致，`AGENTS.md` / `CLAUDE.md` 逐字节一致；GUI、真实 KovaaK、Raw Input、高 polling-rate、正式 installer/签名/updater 均未运行或未闭合。

2026-07-15 correctness review 增量闭环：

- frozen Analysis source 消失、不可读或 revision 变化不再落入 `internal_unknown / analysis_failed`；worker 现在写入不可重试的 `input_validation / source_unavailable`，错误对象和普通日志均不包含底层异常中的本地绝对路径；
- run-based path import 现在先冻结 MP4 SHA-256 / size / mtime，再验证 Analysis managed copy；源内容/mtime 变化、复制前消失与同一路径换版复用 idempotency key 均 fail-closed，result snapshot 和 artifact manifest 保留 fingerprint/checksum，audit/result 不包含原始绝对路径；
- app-owned SQLite connection 现在显式启用 `PRAGMA foreign_keys=ON`；此前 v10–v12 DDL 中 Provider profile/credential、Training Plan/version/transition 的外键只是声明但未执行，现已增加 orphan child 写入失败回归；v11/v12 multi-statement migration helper 也不再通过 `executescript()` 隐式提交调用方事务，rollback 回归确认失败不会留下半创建 schema；
- Training Plan `diagnostic_context` 的各 ref list 现在强制匹配 analysis/metric/diagnosis/prescription/knowledge/evidence kind，版本级 `evidence_refs` 只接受冻结的 Analysis/metric/knowledge 依据，不再允许语义错位 ref 入库；
- Coach command journal 不再允许 stale lookup 后的 upsert 用同 key 不同 digest 覆盖既有 reservation/result；同 digest 的后到 reservation claimant 现在 replay 已有 unknown/final 结果而不重复执行副作用；普通 digest 冲突返回稳定 `idempotency_conflict`，confirmed reservation 冲突在同一 SQLite 事务内回滚 confirmation 消费；
- Pi product-command tool 与 Python product-command boundary 的路径/URL 防线不再只检查字符串开头；嵌在普通文本中的 `https://...`、POSIX/Windows/UNC 路径现在在 backend dispatch 前和 tool result 入模前同时 fail-closed；
- Provider profile status 现在把 pinned Pi `ModelsError("auth"|"oauth")` 映射为 `needs_reauth`，保留安全的 profile/model 投影并继续 redaction credential，不再误报不可重试的 `unconfigured/profile_status_failed`；
- custom OpenAI-compatible profile 的 canonical store 现在拒绝带 URL userinfo 的 `base_url`；`https://user:secret@host/...` 不再被接受、持久化或通过公开 profile API 回显；
- OAuth refresh 失败写入 `needs_reauth` 现在绑定 operation 启动时的 credential revision，并在 Provider store write transaction 内条件更新；旧 refresh 失败不能再把用户随后替换的新 API key 错误标成需要重新认证；
- source-error 定向回归：`webapp/tests/test_worker.py`、`tests/test_native_flicking_analysis.py`、`webapp/tests/test_queue.py`、`webapp/tests/test_routes.py` 共 `109 passed`；MP4 correction 相关 `webapp/tests/test_kovaak_runs.py`、`webapp/tests/test_coach_commands.py`、`webapp/tests/test_worker.py`、`webapp/tests/test_queue.py`、`webapp/tests/test_routes.py` 共 `134 passed`。
- SQLite relation / migration correction 相关 `webapp/tests/test_db.py`、Provider store/auth/routes、Training Plan store、Coach command 与 queue 回归共 `118 passed`。
- Training Plan typed-ref correction 相关 store、Coach command/tool runtime 与 Coach routes 回归共 `72 passed`。
- Coach command journal、Python embedded path/URL、confirmation rollback、DB migration 与 Coach route/runtime 定向回归共 `90 passed`。
- Pi product-command 嵌入式 path/URL 边界定向回归 `5 passed`。
- Pi Provider catalog/model/profile/auth status 定向回归 `14 passed`。
- Provider store/auth/routes 与相邻 Coach route 定向回归 `66 passed`。
- 当前完整 Python 仓库 `589 passed, 3 skipped`，`compileall` 通过；frontend `npm run type-check`、`npm test`（`4 passed`）和 `npm run build` 通过，正式路由仍按已批准重建顺序只生成 `/404`；
- Coach runtime 全量 `59 passed`，strict TypeScript 通过；其中 sidecar HTTP tests 在受限沙箱内监听 `127.0.0.1` 会得到 `EPERM`，沙箱外复跑全部通过；
- Rust `cargo fmt --check`、`cargo check --locked --all-targets`、`cargo clippy --locked --all-targets -- -D warnings` 与 `cargo test` 通过（`18 passed`）；descendant process inspection 同样需在沙箱外复跑；
- 全仓 `git diff --check`、项目文档本地链接、active plan/spec 索引一致性、`AGENTS.md` / `CLAUDE.md` byte parity 均通过。

2026-07-14 RefleK Task 2–3 当前平台收口：

- Task 2：真实 `analysis_result.v2` producer 补齐 owner/local profile、analysis version、evidence source/version/coverage、metric provenance/version/limitations，以及 artifact ownership/managed/local-only/status/format；queue 在写入 terminal result 前验证 session owner、完整 producer metadata，以及 `analysis_id` / `analysis_type` / `input_mode` / `kovaak_run_ref` 与已 claim request 一致；旧未版本化 v2 继续按 legacy draft 读取，但不能新写为 terminal result；
- Task 3：Analysis input snapshot 现在冻结 Stats、Performance 与 Raw trace 的 SHA-256 / size / mtime revision；worker 在解析前重新计算并逐一比较，文件被修改、替换、删除、不可读或旧 trace 缺少 fingerprint 时 fail-closed，不静默使用另一份数据；SPARC 对选中频段做 frequency-span normalization，native/video-fallback 写独立 v2 metric version，避免采样间隔污染与跨公式趋势混用；
- focused：Task 3 相关 `71 passed`；相邻 contracts/queue/routes/worker 回归 `97 passed`；
- Python 完整仓库：`522 passed, 3 skipped`；Python compileall 与 `git diff --check` 通过；
- 未运行正式 frontend/Rust 重验，因为本轮未修改对应实现；Windows Raw Input 实机、高 polling-rate、真实 KovaaK 三模式和 release license review 仍未验证。

2026-07-14 Versioned Coach Knowledge Registry 最终验收：

- Registry：43 条 canonical entries、19 个 canonical signals、10 个 category；37 Python chunks + 19 legacy signals + 11 TS seeds 共 67 条 migration audit 完整；
- Python：完整仓库 `509 passed, 3 skipped`；包括 Registry validator/retrieval、legacy adapters、trace fail-closed 与真实 Analysis → Pi Knowledge → SQLite refs-only E2E；
- Coach runtime：`56 passed`；Python/TypeScript query parity、v1 bridge 解耦、最多三条、无全库 fallback、safe event refs 与 sidecar loopback 全部通过；
- strict TypeScript、`git diff --check` 与 `AGENTS.md` / `CLAUDE.md` byte parity 通过；正式前端、Windows 实机与真实素材三模式 Gate 不在本计划范围，仍未验证。
- 2026-07-15 correctness correction：原 `schema.v1.json` 的 entry `additionalProperties: false` 未声明任何 entry properties，标准 validator 会拒绝所有合法 Registry entry；现已补齐 Draft 2020-12 properties/enum/length/uniqueness/source-claim condition，并新增 canonical Registry + fixture 标准验证与非法 shape 回归。

2026-07-14 Task 5 主线程验收后记录：

- Python：完整仓库 `466 passed, 3 skipped`；新增覆盖 SQLite Training Plan lifecycle/version、共享产品命令、owner、确认、并发幂等、append-only audit、bridge 生命周期与 tool trace secret sentinel；
- Frontend：prototype 删除并清理旧 `.next` 后，`npm run type-check` 通过，`npm test` 为 `3 passed`；`next build --webpack` 通过且只生成自动 `/404`，当前无正式产品路由；
- Coach runtime：`48 passed`；严格 TypeScript 检查通过；knowledge progressive retrieval、精确三工具 registry、非法 analysis context fail-closed、固定 loopback bridge、禁止 authority/path/URL/credential/raw payload 与 bridge secret redaction 已覆盖；真实 TS product tool → loopback HTTP → Python shared command → SQLite audit 联调通过；
- Rust：`cargo fmt --check`、`cargo check --locked --all-targets`、`cargo clippy --locked --all-targets -- -D warnings` 通过；`cargo test` 为 `15 passed, 1 failed`，失败点是受限环境的 descendant process inspection `PermissionDenied`；
- Windows target condition check 仍被缺少 `icons/icon.ico` 阻塞；Windows Raw Input 实机、真实三模式 E2E 与高 polling-rate 性能仍未验证。

Desktop vertical slice 的历史回归、commit 和进程审计保存在：

- [`archive/history/PROGRESS-2026-07-12-desktop-slice.md`](archive/history/PROGRESS-2026-07-12-desktop-slice.md)

2026-06-27 至 2026-07-10 的历史流水保存在：

- [`archive/history/PROGRESS-2026-06-27-to-2026-07-10.md`](archive/history/PROGRESS-2026-06-27-to-2026-07-10.md)
