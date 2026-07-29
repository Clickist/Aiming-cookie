# Full Worktree 合同修复实施计划

> **状态：Completed；Task 1-16 已按授权流程收敛。** 点点已于 2026-07-30 授权后续问题继续按“独立复证 -> 最小 Task -> agent 实施 -> 根会话逐 hunk 复核 -> focused/交叉/aggregate 验证”流程收敛，并在全部可实现项完成后分批 commit、push。本计划只承接
> [`2026-07-30-full-worktree-recovery-audit.md`](../../../superpowers/assessments/2026-07-30-full-worktree-recovery-audit.md)
> 中已由主审和独立只读复核共同确认的缺陷。Task 7 仍必须先满足 Task 9 的 CAP-01 前置合同；授权包含本计划内的文档归档、按 recovery slicing 分批 stage/commit，以及最终 push，但不包含发布、部署、签名、真实凭据/产品数据库访问或改写产品决策。临时派工基线仅可在最终验收后送入回收站。

**目标：** 在保留当前 dirty worktree 组合状态的前提下，按互不重叠的最小 Task 修复已确认的分析质量门、Coach 定位、TeachingSession 删除生命周期、nullable reply、Analysis 信任标签和局部失败问题。

**实现边界：** 上游 PRD、Architecture 和 UI/UX 合同不变；代码回到既有合同。每个 executor 只执行点点明确指定的一个 Task，只修改 Allowed files，并先复现该 Task 的失败。共享 dirty 文件只能改授权 hunk，不得覆盖或整理既有改动。

**技术栈：** Python 3.11 repository venv / pytest、React 19 / Next 16 / TypeScript、Node test runner、Playwright。

## 依赖与并行顺序

- 第一批可并行：Task 1（AN-01）、Task 2（UX-01）、Task 3（COACH-01）。三者 Allowed files 互不重叠。
- Task 4（COACH-02）必须是独立 Coach sidecar Task，不与 Task 3 混改。
- Task 5（AN-03）与 Task 6（UX-02）等待 Task 1 的分析质量门结论合入当前工作区后再并行。
- Task 7（UX-03）等待 audit 中 CAP-01 的 capture-entry continuity 合同先完成；主审确认前置条件后才可执行，否则按 Stop rule 记录阻塞，不得绕过。
- `PERF-01 + MB-01` 由同批 completed [`2026-07-30-kovaak-connected-account-and-coach-lookup-v1.md`](2026-07-30-kovaak-connected-account-and-coach-lookup-v1.md) Task 5 承接，保留历史，只修 latest complete/available 读取和调用方。
- 第二批可并行：Task 9（CAP-01）、Task 10（SEC-01）和 score Task 5；Task 9 验收后才可恢复 Task 7。
- 第三批可并行：Task 11（EXT-01）、Task 12（TOOL-01）和 Task 13（TEST-01）。
- Task 14（GOV-01/02）只能在代码状态稳定后执行；Task 15 只收口 REL-01/02 的 release No-Go，不得伪造发行能力。
- 每批返回后由主审逐 hunk 复核，再运行 focused、交叉和 aggregate 验证；不得只接受 agent 自报结果。

## Task 1 - 保留 Switching episode projection 的上游质量门（AN-01）

### Allowed files

- `kovaak_tracker/visual_signals.py`
- `tests/test_visual_signals.py`
- `tests/test_target_switching_analysis.py`
- 本计划，仅记录 Task closeout

不得修改 analyzer schema、worker、其他 aim family、场景 Registry/Manifest、真实 Run/media 或产品文档。

### Tests first

1. 构造 `status="rejected"`、`enabled_metric_families=[]` 且带失败 limitation 的上游 visual quality；即使 episode 可用，projection 仍必须 rejected、保留 limitation，且不得启用 `target_switching`。
2. 构造 `status="limited"` 但上游未启用 Switching 的质量对象；projection 不得重新启用 family。
3. 将上述 projected quality 送入 `analyze_target_switching_v1`；结果必须为 `support_status="outcome_only"`，且不生成 processed metric rows。
4. 保留 accepted/limited 且已启用上游 `switching` 的正向控制；它仍可映射为 `target_switching`。

### Minimal implementation

- `project_visual_target_episodes_v1` 必须合并而不是替换上游 quality。
- episode projection 只能缩窄 status、enabled family 和 limitations，不能把 rejected 或 disabled family 扩成 accepted/enabled。
- 只有上游明确启用 `switching` 时才映射为 `target_switching`；episode limitations 追加到上游 limitations，并保持 `safe_summary` 与最终 quality 一致。

### Frozen decisions

- analyzer 当前对 quality gate 的消费是正确的，不在 analyzer 内增加第二套补丁。
- episode 可用性不是独立的全局质量证明，不能覆盖上游 coverage、alignment 或 runtime rejection。
- 不改变 quality schema、threshold、family 命名或 outcome-only 合同。

### Stop rule

- 修复需要改 quality schema、阈值、其他 family gate、evidence retention 或 worker contract。
- 现有 active 产品合同明确允许 episode projection 覆盖 upstream rejection。
- 正向 accepted control 无法在不扩大范围的情况下保留。

### Verification

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_visual_signals.py tests/test_target_switching_analysis.py -q
.\.venv\Scripts\python.exe -m compileall -q kovaak_tracker/visual_signals.py
git diff --check -- kovaak_tracker/visual_signals.py tests/test_visual_signals.py tests/test_target_switching_analysis.py
```

## Task 2 - 只在 Analysis workspace 实际接收后报告 Coach 已定位（UX-01）

### Allowed files

- `webapp/frontend/components/task6/CoachPanel.tsx`
- `webapp/frontend/components/task5/AnalysisWorkspace.tsx`
- `webapp/frontend/tests/task5-source.test.ts`
- `webapp/frontend/tests/task6-source.test.ts`
- `webapp/frontend/e2e/interaction-polish.spec.ts`，仅增加 Coach locator 路径
- 本计划，仅记录 Task closeout

不得修改 backend locator schema、Coach context persistence、Analysis API、全局导航、设计系统或无关 frontend adapter。

### Tests first

1. 没有 receiver 或 locator 无效时，点击引用不得显示“已定位”，而要显示可重试的未定位反馈。
2. 合法 `{view:"video", relative_start_ms}` 被 workspace 接收后，tab 切到视频、playhead 更新，然后才显示“已定位”。
3. 合法 `diagnosis` / `data` locator 只切换对应现有 tab；未知 view、负数、非有限时间不得被确认。
4. listener mount/unmount 不重复注册；切换页面后旧 workspace 不再确认事件。

### Minimal implementation

- 复用一个 cancelable `CustomEvent`：Coach dispatch locator；workspace 只有在校验并实际应用 locator 后才调用 `preventDefault()` 作为 acknowledgement。
- `dispatchEvent()` 返回 `false` 才表示已被接收；否则显示未定位反馈。
- receiver 只操作现有 `tab` 与 `playheadMs` 状态；不建立 event bus、store 或第二套 locator 类型。

### Frozen decisions

- 不扩大 backend locator schema，也不从 label/kind 文本猜 target。
- “已定位”描述的是当前页面已完成定位，不是事件已经发出。
- 现有 Analysis workspace 是 receiver；Coach 不直接持有或复制 workspace 状态。

### Stop rule

- 精确定位必须新增 backend 字段、跨页面导航协议、全局 store 或改变 Analysis/Coach ownership。
- 当前 locator 无法在现有 `view` / `relative_start_ms` 合同内安全验证。
- focused E2E 需要重写 fixture、路由或整套 workspace。

### Verification

```powershell
Set-Location webapp/frontend
npm.cmd run type-check
node --import ../../third_party/pi/node_modules/tsx/dist/loader.mjs --test tests/task5-source.test.ts tests/task6-source.test.ts
npx.cmd playwright test e2e/interaction-polish.spec.ts
git diff --check -- components/task6/CoachPanel.tsx components/task5/AnalysisWorkspace.tsx tests/task5-source.test.ts tests/task6-source.test.ts e2e/interaction-polish.spec.ts
```

## Task 3 - 删除 Analysis 后撤销 source-backed TeachingSession 状态（COACH-01）

### Allowed files

- `webapp/backend/coach_agent_runs.py`
- `webapp/backend/coach_context_refs.py`，仅当现有状态读取不足以区分 active 与 deleted/unavailable source ref
- `webapp/tests/test_coach_agent_runs.py`
- `webapp/tests/test_queue.py`，仅增加真实删除生命周期集成回归
- 本计划，仅记录 Task closeout

不得修改 DB/schema/routes、TeachingSession store、Training Plan/confirmation、消息历史、删除事务、Provider runtime 或 frontend。

### Tests first

1. 通过真实 `queue.delete_session` 删除唯一 Analysis：下一次 `create_run` 不得继续使用旧 observation、candidate、alternatives、cue、changed variable 或 matched retest；planner 回到 `intake / unresolved`。
2. 无 source refs 的用户确认/普通 context-free lesson 在 `contexts=[]` 时保持现状，不得被批量清空。
3. source ref 仍 active、但本轮没有新增 Analysis 的既有教学继续原状态机。
4. 有无关 active context 时，不得用它替代已删除 source，也不得保留已删除 source-backed lesson。
5. 删除只使 evidence unavailable；历史消息、已确认 Training Plan/execution/retest facts 不被删除或改写。

### Minimal implementation

- 在 hydration 前使用现有 `coach_context_refs` 事实判断 session 中非空 source refs 是否仍可用。
- 仅当 source-backed teaching state 的 ref 已 deleted/unavailable 时，清空其派生教学字段并回到安全 intake/unresolved。
- 不把简单的 `bundle.contexts=[]` 当作删除证明；无 source ref 与仍 active 的 source-backed lesson 必须有独立控制。

### Frozen decisions

- Analysis 删除不级联删除 Coach session、消息或已确认训练事实。
- TeachingSession 不建立第二套 evidence 存活状态；复用现有 context ref status。
- Provider 不决定 evidence 是否可用，也不能从消息文本重建已删除 lesson。

### Stop rule

- 区分 deleted、detached 与 unavailable 必须新增 schema、route、状态机或改写删除事务。
- 修复会清除已确认的 Training Plan/execution/retest facts，或改变 owner/CAS/idempotency。
- 需要从历史消息、Provider 输出或 label 文本猜 source ref。

### Verification

```powershell
.\.venv\Scripts\python.exe -m pytest webapp/tests/test_coach_agent_runs.py webapp/tests/test_queue.py -q
.\.venv\Scripts\python.exe -m compileall -q webapp/backend/coach_agent_runs.py webapp/backend/coach_context_refs.py
git diff --check -- webapp/backend/coach_agent_runs.py webapp/backend/coach_context_refs.py webapp/tests/test_coach_agent_runs.py webapp/tests/test_queue.py
```

## Task 4 - 保留 Coach runtime failure 的 nullable reply（COACH-02）

### Allowed files

- `webapp/backend/coach_service.py`
- `webapp/tests/test_routes_coach.py`
- `webapp/tests/test_coach_commands.py`，仅当缺少 redactor 的 `None` / string 控制
- 本计划，仅记录 Task closeout

### Tests first

1. 先运行现有 `test_coach_runtime_pi_failure_no_fallback`，确认当前 `reply=""` 而合同要求 `None`。
2. runtime failure + no fallback 保留 `reply=None`；普通字符串 reply 仍执行 Steam profile redaction。
3. 不放宽 nullable API schema，不把 failure 改写为成功空文本。

### Minimal implementation

只在 `engine_result.reply is not None` 时调用字符串 redactor；`None` 原样投影。

### Stop rule

若保留 `None` 需要改变 schema、frontend、engine error contract 或 redactor 的其他调用者，停止并上报。

### Verification

```powershell
.\.venv\Scripts\python.exe -m pytest webapp/tests/test_routes_coach.py webapp/tests/test_coach_commands.py -q
git diff --check -- webapp/backend/coach_service.py webapp/tests/test_routes_coach.py webapp/tests/test_coach_commands.py
```

## Task 5 - 部分/描述性 family 不得标为正式指标（AN-03）

### Allowed files

- `webapp/frontend/lib/contracts.ts`
- `webapp/frontend/tests/task5-analysis.test.ts`
- `webapp/frontend/e2e/analysis-data.spec.ts`，仅增加信任标签断言
- 本计划，仅记录 Task closeout

### Tests first

1. `support_status="partial"` 或 `claim_ceiling="descriptive_only"` 的 deterministic metric 必须进入 limited/描述性组。
2. supported 且 claim ceiling 允许的 deterministic metric 保持 formal。
3. unavailable/outcome-only family 不因 metric 数值存在而升级。

### Minimal implementation

让 formal/limited metric grouping 复用现有 family trust predicate；不新增 coverage threshold 或第二套分类。

### Stop rule

若修复需要改 backend Analysis contract、阈值、持久化或 UI 信息架构，停止并上报。

### Verification

```powershell
Set-Location webapp/frontend
npm.cmd run type-check
node --import ../../third_party/pi/node_modules/tsx/dist/loader.mjs --test tests/task5-analysis.test.ts
npx.cmd playwright test e2e/analysis-data.spec.ts
```

## Task 6 - EvidenceSegments 失败不得伪装成空时间轴（UX-02）

### Allowed files

- `webapp/frontend/components/task5/VideoView.tsx`
- `webapp/frontend/e2e/failure-matrix.spec.ts`
- `webapp/frontend/tests/task5-source.test.ts`，仅补局部失败/重试合同
- 本计划，仅记录 Task closeout

### Tests first

1. 视频成功但 EvidenceSegments 404/503 时，播放器保留，片段区显示局部 unavailable 与 retry。
2. retry 成功后恢复片段，且不重置可用播放器。
3. 真正的空片段响应继续显示空片段语义，不与请求失败混同。

### Minimal implementation

为 segments 单独维护 loading/error state；其失败只影响时间轴片段区，不复用 video failure，也不投影为空数组。

### Stop rule

若需要后端 route/schema 变化、整页错误态或重建播放器，停止并上报。

### Verification

```powershell
Set-Location webapp/frontend
npm.cmd run type-check
node --import ../../third_party/pi/node_modules/tsx/dist/loader.mjs --test tests/task5-source.test.ts
npx.cmd playwright test e2e/failure-matrix.spec.ts
```

## Task 7 - History 选择的 Run 到达 Analyze（UX-03；Task 9 验收后完成）

### Allowed files

- `webapp/frontend/components/task3/AnalyzeClient.tsx`
- `webapp/frontend/tests/task3-source.test.ts`
- `webapp/frontend/e2e/interaction-polish.spec.ts`，仅增加 History-to-Analyze 连续性
- 本计划，仅记录 Task closeout

`HistoryClient.tsx` 当前正确生成 `/analyze?run=<run_ref>`，不在默认修改范围。

### Prerequisite

先由独立 active Task 修复并冻结 CAP-01 capture-entry continuity；未满足前不得执行本 Task。

### Tests first

1. 两条 pending Run 时，从 History 选择第二条，Analyze 必须选中 query 指定的第二条。
2. invalid/expired ref 不得选择错误 Run，继续现有未选择/单条默认行为。
3. query selection 只在真实 pending list 加载并验证后应用。

### Minimal implementation

读取 `run` search param，并只在已加载 pending Run 中 exact match；不从 label/index 猜测，不改变 Run ownership 或 capture lifecycle。

### Stop rule

CAP-01 未完成，或修复需要改变 History route、backend Run contract、capture state 或导航 IA 时，停止并上报。

## Task 8 - Aiming Profile 必须复核冻结 Scenario metric allowlist（EXT-02）

### Allowed files

- `webapp/backend/aiming_profile_store.py`
- `webapp/tests/test_aiming_profile_store.py`
- `webapp/tests/test_worker.py`，仅更新直连 Profile projector 的 result fixture
- 本计划，仅记录 Task closeout

不得修改 worker dispatch、result schema、Scenario Registry/Manifest、Profile DB schema、历史 contribution 或产品文档。

### Tests first

1. 构造一个 `analysis_result.v2`：`scenario` 与冻结 `input_snapshot.scenario_resolution` 均为 exact static scenario，且 allowlist 仅有 `static_clicking`；其中有 available deterministic `target_switching.transition_time_ms`。现有 projector 会错误生成该 dimension；修复后必须返回 `None`。
2. 保留 matching static metric 的正向控制，且要求 scenario ref、aim family、allowed analyzer 和 allowed metric family 与冻结 resolution 一致。
3. 缺少或无法证明冻结 resolution、analysis version/analyzer 或 metric family 关系的 modern result 必须 fail closed；不从 metric display name、analysis type 或 scenario label 猜测 family。
4. 既有 worker projector fixture 补齐同一份公开冻结 resolution，仍只投影 supported 且 evidence-backed 的 matching family metric。

### Minimal implementation

`build_contribution_from_analysis_result` 只消费已经随 result 发布的 `input_snapshot.scenario_resolution`：验证 exact scenario ref、aim family、analysis version/analyzer 与每个 metric namespace family 都被 resolution 显式授权后才生成 contribution。不得为此向 worker 投影增加字段，因为公共 frozen snapshot 已包含同一 resolution。

### Frozen decisions

- worker dispatch 仍是第一道 gate；Profile store 只增加下游 canonical defense in depth。
- 不新增或迁移 result/Profile schema，不改 Scenario Registry/Manifest，也不清理既有历史 contribution。
- native static 继续使用现有 namespace 适配；适配后的 family 仍必须由 frozen allowlist 明确授权。
- `partial` 结果不因本修复升级为正式 Profile evidence，仍沿用既有可用 deterministic metric 规则。

### Stop rule

- 公开 frozen snapshot 在真实结果中不含可验证的 `scenario_resolution`，因而必须修改 worker/schema、迁移持久化结果或重跑 Analysis。
- 需要新建 metric-family 映射或无法从当前 canonical metric namespace 得到 family。
- 现有兼容性合同明确要求缺少 resolution 的现代 result 继续写入 Profile。

### Verification

```powershell
.\.venv\Scripts\python.exe -m pytest webapp/tests/test_aiming_profile_store.py -q
.\.venv\Scripts\python.exe -m pytest webapp/tests/test_worker.py -q
.\.venv\Scripts\python.exe -m compileall -q webapp/backend/aiming_profile_store.py
git diff --check -- webapp/backend/aiming_profile_store.py webapp/tests/test_aiming_profile_store.py docs/superpowers/plans/2026-07-30-full-worktree-contract-remediation-v1.md
```

## Task 9 - Finalizer drain 后立即释放 capture session（CAP-01）

### Allowed files

- `webapp/backend/desktop_runtime.py`，仅撤销 pending 时跳过 observe 的 dirty hunk；若代码已回到 HEAD 正确路径则不制造等价改动
- `webapp/tests/test_desktop_runtime.py`，仅增加 drain-to-release 回归
- 本计划，仅记录 Task closeout

### Tests first

1. monitor 启动时存在 pending finalizer；它完成后，session 必须在下一个 poll 内释放，不得再等待完整 hard grace。
2. finalizer pending 时 monitor 仍可识别 finalizing session，但 release 必须等待 pending future drain。
3. 无 pending history 时继续保留 hard grace；KovaaK alive、status failure 和 shutdown 行为不变。

### Minimal implementation

让现有 release drain task 从 pending 阶段开始观察状态转换；若错误只来自 dirty hunk，则恢复已提交实现并只保留回归测试。不得增加第二套 capture 状态或缩短全局 hard grace。

### Frozen decisions

- 不改变 Run ownership、finalizer retention、native coordinator 状态机或 capture retry 语义。
- 自动化只证明本地时序；真实 KovaaK 退出/快速重启仍是未闭合 Windows field Gate。

### Stop rule

若必须修改 Rust coordinator、finalizer schema、release API、retention 或真实进程探测，停止并上报。

### Verification

```powershell
.\.venv\Scripts\python.exe -m pytest webapp/tests/test_desktop_runtime.py -q
.\.venv\Scripts\python.exe -m pytest webapp/tests/test_kovaak_ingest.py webapp/tests/test_native_capture_client.py webapp/tests/test_queue.py -q
git diff --check -- webapp/backend/desktop_runtime.py webapp/tests/test_desktop_runtime.py
```

## Task 10 - Coach sidecar 仅允许显式 loopback HTTP（SEC-01）

### Allowed files

- `webapp/backend/config.py`
- `webapp/tests/test_coach_runtime.py`
- `webapp/tests/test_provider_auth.py`，仅当现有 provider-auth sidecar 路径需要同一配置回归
- 本计划，仅记录 Task closeout

### Tests first

1. `http://127.0.0.1:<valid-port>`、`http://127.x.y.z:<valid-port>` 与 `http://[::1]:<valid-port>` 可用；现有 path prefix 保留。
2. hostname `localhost`、非 loopback IP、HTTPS、缺失/非法端口、userinfo、query、fragment 均在请求前 fail closed。
3. 受控假 HTTP client 证明拒绝配置不会收到 Provider credential、bridge bearer 或 Desktop launch token。

### Minimal implementation

在配置入口用标准 URL/IP parser 校验 `COACH_SIDECAR_URL`：scheme 必须是 `http`，host 必须是 IP literal loopback，port 必须在有效范围，且不得含 userinfo/query/fragment；允许既有 path prefix，不改调用方或秘密载荷。

### Frozen decisions

- 不接受 DNS 名称，即使名称是 `localhost`；避免名称解析和 hosts 文件扩大信任边界。
- 不改 Provider credential store、bridge token、OAuth、HTTP client 或 sidecar 协议。
- 无效显式配置必须启动时失败，不静默回退到另一个目标。

### Stop rule

若当前 Desktop 需要远程 sidecar、HTTPS/mTLS、Unix socket/named pipe 或动态端口发现，停止并转交架构决策。

## Task 11 - Knowledge prescription refs 必须解析为 active scenario（EXT-01）

### Allowed files

- `kovaak_tracker/scenario_profiles.py`
- `kovaak_tracker/coach/knowledge_registry.py`
- `webapp/backend/coach_agent_runs.py`，仅复用 canonical active-ref helper
- `tests/test_scenario_profiles.py`
- `tests/coach/test_knowledge_registry.py`
- `webapp/coach-runtime/src/knowledge-registry.ts`
- `webapp/coach-runtime/test/knowledge-registry.test.ts`
- 本计划，仅记录 Task closeout

### Tests first

1. Python 与 TypeScript 对 packaged knowledge 中未注册、retired 或 manifest inactive prescription ref 都拒绝。
2. 当前 Registry/Manifest 的 active exact refs 继续通过，并保持两端 parity。
3. prepared-plan compiler 继续使用同一 active intersection；不得引入第二套 registry 或改变 Training Plan 状态。

### Minimal implementation

提取或复用 canonical active ScenarioProfile ref 集合，让 packaged knowledge validation 和 prepared-plan compiler 使用同一交集；仅增强 release/asset validation，不改 JSON、schema 或 migration。

### Stop rule

若需要修改 scenario/knowledge asset、schema、migration、处方内容、manifest activation 或 Training Plan 合同，停止并上报。

## Task 12 - 明确并强制 CPython 3.11 测试入口（TOOL-01）

### Allowed files

- `conftest.py`（新增）
- `docs/DEVELOPMENT.md`
- `webapp/README.md`
- 本计划，仅记录 Task closeout

### Tests first

1. 修复前 `py -3.9 -m pytest --collect-only -q webapp/tests/test_auth.py` 会正常 collection，证明入口未拒绝非支持版本；不得声称 Python 3.9 当前一定无法解析代码。
2. 修复后同一命令非零退出，并明确要求 CPython 3.11 与仓库 `.venv`。
3. `.\.venv\Scripts\python.exe -m pytest --collect-only -q tests/test_time_alignment.py webapp/tests/test_auth.py` 通过，证明两套测试目录都加载 preflight。

### Minimal implementation

根 `conftest.py` 只用 stdlib/pytest 做 session preflight，接受 CPython 3.11.x；文档用 `py -3.11` / `python3.11` 创建 venv，所有 Python 安装、运行和测试命令显式使用 venv interpreter 与 `-m`。

### Frozen decisions

- 不锁 patch 版本，不新增/升级/锁定依赖，不加版本管理器、CI 或 release matrix。
- 不改 Tauri `AIMING_COOKIE_PYTHON`/PATH fallback、sidecar、Rust 或业务代码。

### Stop rule

若需要支持其他 Python minor、加入 lock/constraints/CI，或改变 Desktop runtime 探测，停止并转交 REL-01。

## Task 13 - 默认 frontend gate 覆盖 unit、contracts 与独立 E2E（TEST-01）

### Allowed files

- `webapp/frontend/package.json`
- `docs/DEVELOPMENT.md`
- 本计划，仅记录 Task closeout

### Tests first

1. fast unit、Task contract/source tests 与 Playwright 必须有三个显式命令，且默认 `npm test` 不再遗漏 `tests/*.test.ts`。
2. E2E 保持独立命令，不隐藏在 unit/contract runner 内；release Gate 显式运行 type-check、unit/contracts、production build 和 E2E。
3. 现有 Windows `npm.cmd` 约束与 pinned Pi tsx loader 保持不变。

### Minimal implementation

保留快速 unit 子命令，增加 contracts 子命令，并让默认 test 聚合二者；文档 release Gate 另行调用现有 build-producing Playwright 命令。

### Stop rule

若需要新增 CI provider、修改 fixture/server 架构、安装浏览器或改写测试内容，停止并转交 REL-01。

## Task 14 - Active plan 与 Progress 单一当前状态（GOV-01 / GOV-02）

### Allowed files

- `docs/superpowers/plans/README.md`
- `docs/README.md`
- `docs/PROGRESS.md`
- 经逐份 header/Task 复核确认 completed/blocked 的现有 `docs/superpowers/plans/*.md`
- 对应 `docs/archive/completed/plans/`、`docs/archive/blocked/plans/` 与 `docs/archive/history/` 目标
- 本计划，仅记录 Task closeout

### Tests first

1. Active 索引每项都必须仍有可执行 Task；completed/blocked 项只在对应 archive 索引/路径出现。
2. Progress 只保留一个当前快照；被当前结论推翻的日期化块移入 history 并由摘要链接追溯。
3. 所有移动同步更新 `docs/README.md` 和 plan index，且不得读取、移动、stage 或引用审计账本明确排除的未知 assessment。

### Minimal implementation

只对状态已由 plan 自身和当前代码/验证共同证明的条目执行可追踪 `Move-Item`；不重写归档正文，不把 release Gate 或未完成 Task 伪标为完成。

### Stop rule

状态仍有歧义、目标文件已存在、链接所有权不清或需要改写 PRD/Architecture 时，保留原位并记录未决，不猜测。

## Task 15 - Release 风险显式 No-Go 收口（REL-01 / REL-02）

### Allowed files

- `docs/ROADMAP.md`
- `docs/PROGRESS.md`
- 本计划，仅记录 assessment-only closeout

### Required conclusion

1. REL-01 没有已批准的 Python lock/constraints、Node/Rust version matrix 或 CI 所有权，不在本轮发明版本/依赖政策；Task 12/13 只修当前入口与 gate 可见性。
2. REL-02 继续是发行 No-Go；不得通过 `bundle.active=true`、源码相对路径或本机现成 Python 伪装为可分发产品。
3. installer、sidecar/resource packaging、签名、更新、hash/download、clean-machine 与真实网络/硬件 field matrix 继续列为未闭合 Gate。

### Stop rule

任何实现都需要新的 distribution architecture、版本/依赖 ownership 与逐 Task 授权；本 Task 不修改 Tauri config/runtime，不 build bundle、不签名、不部署。

## Task 16 - 复核 960px partial Analysis screenshot drift

### Allowed files

- `webapp/frontend/e2e/screenshots.spec.ts-snapshots/analysis-partial-960-light-win32.png`，仅在视觉复核证明当前渲染正确时更新
- 本计划，仅记录 Task closeout

### Tests first

1. 在最新 production build 上单独复现 `analysis partial 960 light` screenshot；不得使用 Task 13 并发期间的 stale `.next` 结果。
2. 比较 expected、actual 与 diff，确认没有文字溢出、遮挡、不可解释位移、错误断点或缺失内容。
3. 若差异仅来自已验收的 Analysis 信任标签/局部失败语义导致页面高度变化，定向更新这一张 baseline 并再跑一次通过。

### Stop rule

若存在实际 UI 缺陷、需要修改 component/CSS/fixture/spec、或更新会连带改写其他 snapshot，停止并上报新的最小 UI Task；不得用 snapshot update 掩盖回归。

## 执行记录

- Task 1（AN-01）：完成。主审确认 episode projection 不再扩大上游质量门，且 projection 到 analyzer 的 rejected 控制保持 `outcome_only`、无 processed rows。根会话验证：`89 passed`，compile/diff check 通过。
- Task 2（UX-01）：完成。主审确认只有活动 Analysis workspace 实际应用合法 locator 后才 acknowledgement；真实 CoachPanel 点击覆盖成功和未接收路径。根会话验证：type-check、source tests `10 passed`、Playwright `6 passed / 1 skipped`，diff check 通过。
- Task 3（COACH-01）：完成。主审确认 deleted/detached source-backed lesson 回到 intake，context-free、active source-backed lesson 和已确认 Training Plan facts 保留，无关 active context 不会替代已删除 source。根会话验证：`132 passed`，compile/diff check 通过。
- Task 4（COACH-02）：完成。主审确认 runtime failure 的 `reply=None` 原样保留，普通字符串 reply 继续执行临时 Steam Profile redaction。根会话验证：指定 Python suite `117 passed`，aggregate suite 通过，diff check 通过。
- Task 5（AN-03）：完成。主审确认只有 family trust 为 supported 的 available deterministic metric 进入 formal；partial、descriptive-only、outcome-only 和 unavailable 均保留在 limited。根会话验证：type-check、direct tests `8 passed`、Playwright `3 passed`，aggregate suite 通过，diff check 通过。
- Task 6（UX-02）：完成。主审确认 EvidenceSegments 使用独立 loading/error/retry 状态，失败不卸载播放器，retry 恢复片段，成功空响应保留空语义。根会话验证：type-check、与 Task 5 合并的 direct tests `14 passed`、相关 Playwright `13 passed`，aggregate suite 通过，diff check 通过。
- Task 7（UX-03）：Task 9 验收后恢复并完成。Analyze 只在 pending Run 列表加载后精确匹配 `?run=`；第二条 Run 与 invalid ref 的 focused Playwright 均通过。
- Task 8（EXT-02）：完成。Profile store 复核 frozen scenario/analyzer/metric-family allowlist；未知且不可投影的 metric 继续忽略，不否决同一结果中的合法 dimension。根会话 focused `10 passed`，含 worker 交叉 `96 passed`。
- Task 9（CAP-01）：完成。撤销 pending 状态跳过 observe 的回归，finalizer drain 后立即释放 capture session；Python desktop runtime `20 passed, 1 skipped`，capture/ingest/queue 交叉 `83 passed`。真实 KovaaK 快速退出/重启仍是 field Gate。
- Task 10（SEC-01）：完成。sidecar URL 只接受显式端口的 loopback IP literal HTTP，拒绝 DNS/localhost、远程 IP、HTTPS、userinfo、query 与 fragment。根会话相关验证 `73 passed`。
- Task 11（EXT-01）：完成。Python/TypeScript Registry validation 使用 Registry 与 launch manifest 的 active exact-ref 交集，prepared-plan compiler 复用 canonical helper。Python `154 passed`，Node parity `15 passed`。
- Task 12（TOOL-01）：完成。根 `conftest.py` 在 collection 前强制 CPython 3.11.x，开发命令使用显式 venv interpreter；Python 3.9 按合同 exit 4，Python 3.11 双目录 `21 passed`。
- Task 13（TEST-01）：完成。frontend 默认 `test` 聚合 unit 与 contracts，最终为 `58 passed`；type-check、production build 与完整 Playwright 通过。
- Task 14（GOV-01/02）：完成。8 份 completed plan 与 1 份 blocked plan 已按自身状态归档；4 份状态仍歧义的 plan 保留原位并列为 `Unresolved`、不可执行。Progress 已收敛为单一当前快照，旧日期化记录移入 history；根会话修复移动产生的 3 条相对链接后，15 份相关文档链接检查为 `0` broken。
- Task 15（REL-01/02）：assessment-only 完成。没有发明 Python lock/constraints、Node/Rust matrix 或 CI ownership；installer、sidecar/resource packaging、签名、更新、hash/download、clean-machine 与真实网络/硬件 field matrix 继续是 release No-Go。
- Task 16（screenshot drift）：完成。根会话与独立 Agent 均确认 `analysis partial 960 light` 的 8px 高度变化只来自已验收的 Analysis 信任/局部失败文案；仅更新该 baseline，focused 与最终完整 screenshot suite 均通过。

最终 aggregate：repository Python `1555 passed, 5 skipped`；Python compileall 通过；Coach runtime `172 passed`；Pi AI `473 passed, 733 skipped`；frontend type-check 与默认 tests `58 passed`；production build + Playwright `55 passed, 3 skipped`；MSVC Rust fmt/check/clippy 通过，tests `73 passed, 7 ignored`；全仓 `git diff --check`、Agent contract byte parity 和相关文档链接检查通过。所有 skip/ignored 项均继续受真实 Tauri、KovaaK、硬编或显式 field 条件约束。

## 最终交叉验证

1. repository Python 3.11 full suite 已通过，COACH-02 不再产生 aggregate failure；
2. frontend type-check、unit/contracts、production build、完整 Playwright 和 screenshot baseline 已通过；
3. Coach runtime、Pi AI 与 MSVC Rust fmt/check/test/clippy 已通过各自产品 Gate；
4. 根会话已逐批检查 staged path、diff summary 与 `git diff --check`，并保留排除文件；
5. 最终 push 后另行报告本地/远端 HEAD 与仍被保留的未跟踪 evidence/unknown 文件。

点点已授权在全部可实现项、治理收口和最终交叉验证完成后，按 recovery slicing 逐 hunk 分批 stage/commit 并 push；不得把 `.firecrawl/**`、`artifacts/**`、研究材料、未知 assessment、真实媒体/凭据或未验证发行声明混入产品提交。本计划专用临时派工基线可在最终验收后送入回收站。
