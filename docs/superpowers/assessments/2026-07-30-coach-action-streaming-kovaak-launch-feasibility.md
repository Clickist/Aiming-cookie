# Coach Action、流式事件与 KovaaK 场景启动可行性评估

> 状态：只读 assessment / proposed ADR input；不是 PRD、Architecture、active spec 或 implementation plan，也不授权施工。
>
> 快照日期：2026-07-30。结论来自当前 dirty worktree 的代码与测试静态核对、本机 KovaaK/Steam 安装证据，以及官方平台资料。本轮没有启动 KovaaK、调用真实 Provider、修改产品数据库或做真实端到端验证。

## 1. 本次要回答的问题

OpenDesign 负责 Coach 侧栏的视觉与交互方案；本评估只提前冻结它背后的技术边界：

1. Coach 当前是否已经具备真流式文本、实时工具工作态、可点击 Evidence/页面跳转；
2. Coach、Training Plan、Diagnosis、History 是否应共用一种可点击产品动作；
3. “练习 1w6ts”能否在 KovaaK 已运行时直接切换、未运行时启动并切换；
4. 哪些能力可直接接现有合同，哪些必须等待真实 Smoke Gate。

产品上游已经明确：Coach 是产品的 Agent 操作层，不是 Report 旁的只读聊天页；最终交互由 agent run、工具事件和产品确认驱动。EvidenceSegment 是本地视频回放锚点，Raw/MP4/路径不得进入 Provider 或普通 Coach 事件。参见 `docs/PRD.md` 与 `docs/ARCHITECTURE.md`。

## 2. 结论先行

**当前 Coach 还不具备这些能力的完整可交付闭环。** 已经存在可靠的后端积木，但前端消费、实时传输和原生场景控制尚未闭合。

| 用户能力 | 当前事实 | 结论 |
|---|---|---|
| 回复文字逐步出现 | Run 只有整轮结束、失败或停止后才写入可见 `partial_text`；前端约每 700ms 拉完整 Run | **不是真流式** |
| “正在调用某工具”实时工作态 | Pi 有原生工具生命周期事件；当前 sidecar/后端在整轮返回后才持久化和显示 | **事件源存在，实时桥未接通** |
| 点击 Coach 中的证据片段并让左侧视频跳到开头 | 后端已有安全 `ui_event` seam，Analysis 有 owner-scoped EvidenceSegment seek anchor，Video 也能 seek；CoachPanel 尚未消费为统一动作 | **底层可复用，端到端未完成** |
| 点击文字跳到 Analysis/History/具体位置 | `navigation.open` 已返回白名单 `coach_ui_event.v1`；前端仍主要把工具结果当文字显示 | **后端可表达，统一前端 dispatcher 缺失** |
| 点击 “1w6ts” 打开训练 | Registry/manifest 能解析 exact scenario；KovaaK 官方公开了 `jump-to-scenario` Steam Deep Link，EVXL 正在使用附带 `mode=challenge` 的形式；仓库尚无 dispatcher/native command，也未跑本机冷/热验证 | **协议已确认，产品能力尚未实现和实测** |
| 停止、重试、确认、副作用幂等 | 已有 agent run stop/retry、confirmation、command journal 等基础合同 | **应复用，不能在富文本动作里另建一套** |

因此推荐同时推进两个不依赖 OpenDesign 视觉稿的架构输入：

- 用一套 `ProductActionDescriptor` 服务 Coach、Training Plan、Diagnosis 和 History；
- 用持久 Run event store 做真相源，以 SSE 做实时 tail，现有快照轮询做降级。

KovaaK 场景动作的后续能力应分两级，二者当前都还不是已实现的产品能力：

- 协议级：KovaaK 官方已确认 `steam://run/824270/?action=jump-to-scenario;name=<URL encoded name>`；EVXL 的实际链接额外使用 `mode=challenge`；
- 产品级：Aiming Cookie 仍需可信 URI 构造/dispatch、状态反馈，并让冷启动与已运行两条路径通过真实 Smoke Gate，才能宣称“已切换到目标 Challenge”。

## 3. 决策 A：统一产品动作，不做 Coach 专用链接协议

### 3.1 目标

同一个“去练这个场景”动作可能来自 Coach 回复、Training Plan、Diagnosis 或 History。它不应被实现为聊天 Markdown 的特殊 URL，也不应依赖从自然语言中正则解析项目名。

推荐的最小公开合同：

```text
ProductActionDescriptor v1
  kind:
    analysis.navigate
    evidence.play
    metric.locate
    diagnosis.locate
    scenario.open
  target:
    analysis.navigate -> analysis_ref + section?
    evidence.play     -> analysis_ref + evidence_ref
    metric.locate     -> analysis_ref + metric_ref
    diagnosis.locate  -> analysis_ref + diagnosis_ref
    scenario.open     -> scenario_profile_ref
  source:
    coach | training_plan | diagnosis | history
  action_ref:
    trusted UI/backend 生成的交互关联 ref；不是授权依据
```

```text
ProductActionOutcome v1
  action_ref
  status:
    ready | dispatching | succeeded | unavailable | deleted |
    expired | failed | cancelled | needs_confirmation
  safe_reason?
  audit_ref?
  confirmation_ref?
  effect?:
    navigated | playback_started | app_started | app_focused |
    scenario_activated | manual_selection_required
```

这里的 `expired` 只建议作为前端根据既有 confirmation `expires_at` 派生的展示状态；当前持久 confirmation 仍使用 `cancelled`，过期后执行仍返回既有 `invalid_confirmation`。除非未来 spec 明确修改 wire contract，否则不新增后端 `expired` 状态。

`source` 只用于界面归因和产品分析，不能改变权限。只有以下输入能进入 dispatcher：

- 可信 product command 返回并经过后端白名单验证的 `ui_event`；
- Training Plan、Diagnosis、History 从本地 owner-scoped、仍存在的稳定 ref 构建的 descriptor。

模型生成的普通文本、Markdown href、显示名称、Steam URI、文件路径、UGC ID 或时间戳都不能直接执行。

### 3.2 与现有能力的映射

| 新动作 | 复用点 | 必须补齐的边界 |
|---|---|---|
| `analysis.navigate` | `navigation.open` 的 analysis event | 前端统一路由、owner/existence 校验、删除态 |
| `evidence.play` | `frontend_evidence_segments.v1` + Video seek | 只传 `evidence_ref`；由本地接口解析真实相对 seek anchor |
| `metric.locate` | Analysis 快照中的 metric refs | target 必须存在后才切 tab/highlight |
| `diagnosis.locate` | 拟议的 canonical diagnosis identity | 当前前端只有 issue index，尚无正式 diagnosis ref resolver；必须先冻结 ref 格式、owner/existence resolver 和 frontend projection，不能由各入口拼 index |
| `scenario.open` | `scenario_profile_ref`、Registry、launch manifest、现有 desktop/native control 边界 | 原生 capability、可观察结果、失败态、实机 Gate |

当前 `navigation.open` 的 legacy `video_time` 确实允许模型提供非负 `time_ms`；本评估不把它描述成不存在，也不在没有 active spec 时删除它。它只能证明已有 UI event seam，在统一 Action 中不映射为正式 Evidence 点击。新的 `evidence.play` 必须解析 owner-scoped EvidenceSegment；Analysis/segment 已删除、MP4 不可用或 anchor 缺失时返回明确 unavailable/deleted，不伪造播放成功。未来 spec 还需明确 legacy `video_time` 是仅保留兼容、改为用户明确任意时间跳转，还是退役。

### 3.3 权限与确认

- 用户点击已经渲染的 `scenario.open`、Evidence 或导航动作，是一次明确请求；纯导航/播放不需要 confirmation。
- Coach 在用户未点击时主动启动应用或执行真实副作用，仍必须走现有 `needs_confirmation`。
- 写 Training Plan、execution、retest 等事实继续复用既有 confirmation 与 15 分钟 expiry；界面可按 `expires_at` 显示“已过期”，不改变后端 cancelled/invalid_confirmation 语义。
- 一次点击的 idempotency key 由可信 UI/command boundary 生成，并在重试中复用；模型不得提供。
- 不新增 Action Store、第二套 command journal、第二套 confirmation 或 Scenario Registry。

## 4. 决策 B：持久事件为真相源，SSE 只负责实时传输

### 4.1 方案比较

| 方案 | 判断 |
|---|---|
| WebView 直连 Coach sidecar | 拒绝。绕过 owner 校验、持久恢复、审计、token/path/raw payload 过滤 |
| 继续只轮询完整 Run | 保留为 fallback；不能提供真实文字和工具生命周期更新 |
| sidecar 真流式 -> 后端安全投影/持久化 -> 前端 SSE tail | **推荐**。可重放、可恢复、兼容现有快照接口 |

推荐数据流：

```text
Pi Agent.subscribe()
  -> sidecar 安全投影、文字合并与背压控制
  -> backend 单 run writer 在同一事务持久化 revision event 与 partial_text
  -> GET /api/coach/agent-runs/{run_ref}/events/stream
  -> frontend fetch ReadableStream
  -> Coach 文本 revision + 工具工作态

断线/刷新
  -> 先读现有 Run snapshot
  -> 从最大 sequence 继续 SSE
  -> SSE 不可用时退回现有快照轮询
```

### 4.2 建议事件投影

Pi 已提供 `message_update`、`tool_execution_start/update/end`。产品层只投影安全、可恢复的事件：

```text
assistant.text_revision   revision + replace text
tool.started              allowlisted tool kind + display label
tool.completed            allowlisted status + safe summary
tool.failed               safe user-facing reason
run.completed
run.failed
run.stopped
```

这些是语义名，不是当前 SQLite `event_type` 的新增枚举。最小实现应映射到现有 `status/phase/tool/text/confirmation/error` event types，并用受白名单约束的 `code` 区分 revision/lifecycle；若未来要新增 DB event type，必须由 migration plan 单独批准。

文字以累计安全快照表达为 `replace` revision，不把 raw token delta 直接当产品真相。sidecar 只能处理 assistant text update，必须丢弃 thinking、tool-call update 和其它非文本 payload；每个累计 revision 都要等价执行现有 `safePartialReply()` 的脱敏与 grounding 校验，校验成功后才可发送。sidecar 必须合并更新；具体时间/字节阈值应由实现期 benchmark、交互延迟与背压测试决定，不能由本 assessment 预先冻结。grounding repair 修改草稿时发布更高 revision。最终持久 assistant message 仍是权威内容。

禁止流出：thinking、工具参数、原始工具结果、endpoint、launch token、路径、Raw/MP4/CSV/protobuf、Provider secret、未白名单字段。Pi `tool_execution_end.result` 绝不能直接投影；工具名、显示标签、状态和可选安全摘要必须由可信工具名到固定公开字段的映射生成。若某工具还没有映射，只显示通用 lifecycle，不显示详情。

### 4.3 恢复、停止与失败

- SSE 使用 run-local `sequence` 作为 event id，支持 `Last-Event-ID` 或 `after_sequence` 重放。
- 前端用 `fetch` 读取流，而不是原生 `EventSource`，因为当前 Web/Tauri 请求需要动态 owner 与 desktop token header。
- 浏览器订阅断开不停止 Run；刷新后按 cursor 去重继续。
- Stop 继续使用现有 `stop_requested` 与 sidecar abort；Retry 继续创建 child run，不在同一 run 重放可能有副作用的工具。
- 每个 Run 只有一个 terminal winner。writer 一旦提交 completed/failed/stopped 之一，就封口并拒绝后到的普通 text/tool event；stop 与 agent completion 并发时按冻结的状态机决定唯一结果。
- sidecar 首包前连接失败可以走现有 terminal fallback；流开始后中断必须保留已持久 partial 并明确失败，不能静默重新执行。
- 背压时可以替换尚未发送的文字 snapshot，不能丢工具生命周期或 terminal event；关键队列满应终止为可诊断的 retryable failure。

### 4.4 必须先修的并发风险

当前 `_append_event()` 通过 `MAX(sequence) + 1` 分配序号。真流式会让文字、工具、stop、terminal 写入更容易并发碰撞。实施前必须选择并冻结一种最小方案：

1. 每个 run 一个单 writer queue；或
2. 在同一短事务中原子分配并插入。

无论选择哪种方案，每个 text revision 对应的 event 与 Run `partial_text` 必须在同一事务提交，保证 snapshot 的文本与 replay cursor 一致。现有 `_append_event()` 与 `_set_run()` 分开提交，不能直接复用。也不能直接在 Pi listener 中每 token 写 SQLite，或保留现状后只增加 SSE endpoint。

## 5. 决策 C：使用官方 Steam Deep Link，产品端仍需冷/热验证

### 5.1 已验证的协议与本机静态事实（不是当前产品能力）

| 项目 | 证据 |
|---|---|
| Steam App ID | `824270` |
| KovaaK 安装 | `E:\SteamLibrary\steamapps\common\FPSAimTrainer` |
| 通用启动候选 | Windows 已注册 `steam:` protocol；Steam 文档支持 `steam://run/824270`，但本轮未实际启动验证 |
| KovaaK 官方场景 Deep Link | `steam://run/824270/?action=jump-to-scenario;name=<URL encoded scenario name>`；官方要求空格编码为 `%20` |
| EVXL 实际 Challenge 链接 | `steam://run/824270/?action=jump-to-scenario;name=VT%20Controlsphere%20Viscose%20Easier;mode=challenge` |
| exact Challenge | `1wall 6targets small`，常用别名 `1w6ts` |
| 产品身份 | `scenario:static.1wall_6targets_small@1` |
| 当前产品 game hash | `7378a811f430b6072d052a75896afb98` |
| Workshop UGC ID | `1337321696` |
| 本机 `.sce` SHA-256 | `028422E13FDC029C7B32BBC8D28C2B6F34D411FC132D28AB163F9A86ACF064CC` |
| 文件语义 | `Name=1wall 6targets small`、`IsChallenge=true`、`Timelimit=60.0` |

game hash 是产品 ScenarioProfile/manifest 的场景身份；文件 SHA-256 只是当前本机副本诊断值，二者不能混用。Workshop 项目目前可能不可用或不兼容，所以“按 UGC ID 自动下载/订阅”不能作为可靠 fallback；只能检查用户本机是否已有已验证场景。

官方 Deep Link 应是首选控制路径，让 Steam/KovaaK 负责冷启动或把 launch query 传给已运行实例，不应先手写 UI 自动化或键鼠搜索。现有 native capture 能发现 KovaaK PID 与可见 HWND，可用于等待/超时和诊断；只有 Deep Link dispatch 后仍需要聚焦降级时，才评估 `SetForegroundWindow`，且必须允许 Windows 返回 `focus_blocked`，不得强抢焦点。

### 5.2 尚未验证的事实

官方协议解决了“如何表达跳转”这一缺口，但以下产品行为仍未在本机验证：

- 当前安装的 KovaaK build 能否通过该 URI 冷启动并直接进入 `1wall 6targets small`；
- KovaaK 已运行时，Steam 是否会把 query 转发并稳定切换；
- EVXL 使用的 `mode=challenge` 是否在当前 build 中稳定进入 Challenge，而不是只打开场景页；该参数未出现在找到的 KovaaK 官方示例中；
- Aiming Cookie 如何可观察地确认场景加载完成、名称匹配，以及是否能核验 game hash。

游戏二进制中的 `NewLaunchQueryParameters`、`GetLaunchQueryParam`、`LoadScenario`、`StartScenarioClass` 等字符串现在只是对官方 Deep Link 的本机静态佐证，不再承担发现参数格式的证明责任。`mode=challenge` 仍以 EVXL 的实际链接为行为证据，必须通过 Smoke 后才能冻结为 Aiming Cookie 合同。

### 5.3 建议的可交付降级

稳定公开动作仍叫 `scenario.open`，只接收 `scenario_profile_ref`。当前 Registry/manifest 在可信本地边界解析 display name 与 expected game hash；模型和 Markdown 不提供 URI、scenario name 或执行参数。native adapter 必须从固定 scheme、固定 App ID、固定 action、Registry display name 的标准 URL 编码，以及通过 Smoke 后才允许的固定 `mode=challenge` 构造 URI，不能拼接任意用户文本。UGC ID 不参与这条官方 Deep Link。

对 1w6ts 的候选 URI 是：

```text
steam://run/824270/?action=jump-to-scenario;name=1wall%206targets%20small;mode=challenge
```

```text
用户点击 scenario.open
  -> 校验 active Registry + active launch manifest + 本地场景可用性
  -> 从可信 Registry display name 构造固定 KovaaK Steam Deep Link
  -> 通过 native OS protocol adapter dispatch，等待进程/HWND 与加载结果
  -> dispatch 或加载无法确认：
       返回 manual_selection_required 或明确失败态
       UI 显示/复制精确名称 "1wall 6targets small"
  -> 只有 adapter 观察到目标确实加载后：
       返回 scenario_activated
```

因此产品文案必须按实际结果区分：

- “已打开 KovaaK，请选择 1wall 6targets small”；
- “已聚焦 KovaaK，请选择 1wall 6targets small”；
- “已切换到 1wall 6targets small”仅在验证成功后出现。

最小安全失败状态：

```text
desktop_unavailable
steam_unavailable
not_installed
launch_timeout
window_not_found
focus_blocked
scenario_unmapped
scenario_not_local
locator_unsupported
deep_link_dispatch_failed
scenario_load_timeout
scenario_switch_unverified
scenario_mismatch
switch_failed
cancelled
```

Web、非 Windows、目标 ref 不存在、场景不在本机或 hash 不匹配时必须 fail closed，不降级为拼 shell、打开任意 URL 或宣称成功。

### 5.4 需明确授权的真实集成 Gate

启动 Steam/KovaaK 会改变外部应用运行状态、缓存或最近运行记录，不属于只读检查。需要点点另行明确许可，并按两阶段执行：

1. **协议 Smoke**：记录被测 KovaaK build、安装状态与目标 ScenarioProfile；使用上述固定 1w6ts URI 分别验证冷启动与已运行实例，观察 `mode=challenge` 是否直接进入 Challenge。
2. **结果核验**：从真实 UI/运行状态核验 exact scenario name；研究 `session.sav` 或其它只读本地状态能否可靠确认加载完成与 game hash。参数被忽略、停在菜单或目标不匹配都判失败。
3. **adapter 后产品 E2E Gate**：新的 native adapter 经 active plan 批准并实现后，再验证 dispatch、进程/HWND 等待、窗口丢失、冷启动、超时、降级和错误映射。
4. 只有冷、热两条都重复通过，才可把 scenario activation 冻结为正式 native adapter 合同。

## 6. OpenDesign 需要遵守的技术输入

OpenDesign 可以自由决定视觉层级、排版、动效和 Coach 侧栏密度，但交互稿至少应覆盖这些真实状态：

- 流式草稿可被 revision 替换，不能假设只有 append；
- 工具工作态至少有 queued/running/succeeded/failed/stopped，且不展示原始参数；
- 文本中的 action 由结构化 descriptor 渲染，不是任意 Markdown URL；
- Evidence action 有可用、已删除、无视频、播放失败状态；
- Scenario action 有启动中、聚焦成功、焦点被阻止、需手动选择、已切换、不可用状态；
- action 必须可键盘聚焦，执行中不能重复触发，状态变化不能造成侧栏布局跳动；
- 同一种 Scenario/Evidence action 在 Coach、Training Plan、Diagnosis、History 中使用同一语义和反馈，不做 Coach 专属变体。

视觉稿不能新增后端执行参数、授权语义、场景身份或“已切换成功”的判定规则。若视觉方案需要合同外状态，应先回到产品/Architecture 审核，而不是由前端临时推断。

## 7. 建议实施顺序（不是 active plan）

1. **统一动作基础**：前端 dispatcher + `analysis.navigate` / `evidence.play` / metric/diagnosis locate；验证 owner、删除态和精确 seek。
2. **真流式事件**：sidecar 安全投影、单 writer、SSE replay、前端 revision/工具态、轮询降级。
3. **KovaaK 启动/聚焦**：复用现有 native capture control family 及其独立的 per-start `AIMING_COOKIE_NATIVE_CAPTURE_CONTROL_SECRET` 边界，不把本地 API launch token 当成 native control secret，也不新建平行 IPC。
4. **场景 activation 实验**：取得许可后实机 Smoke；失败则长期保留 manual selection 降级。
5. **合并 OpenDesign**：点点审阅视觉稿后，再由新的 active spec/plan 冻结组件、Allowed files、测试与任务拆分。

OpenDesign 返回前，不需要等待即可审阅第 1-4 节的合同方向；但没有点点批准的新 active plan，不进入实现。

## 8. 验证 Gate

### Product Action

- 后端：每种 target 的 owner/existence/extra-field/invalid-ref/deleted/unavailable；模型不能向新的 Product Action 注入 source、路径、token、任意时间或伪造 outcome；legacy `video_time` 在迁移决策前单独保持现状并做回归。
- 前端：Evidence 点击后切到正确 tab 并 seek/highlight；Metric/Diagnosis 精确定位；失败不改变当前目标；重复点击遵守幂等。
- 跨入口：Coach、Training Plan、Diagnosis、History 对同一 descriptor 呈现一致结果。

### Streaming

- Pi fake stream：文字 revision、工具开始/完成/失败、grounding replace、stop 和 terminal。
- sidecar：事件顺序、合并、背压、abort；token/path/raw payload 永不出现。
- backend：revision event + `partial_text` 同事务、原子 sequence、并发 stop、唯一 terminal winner、封口后拒绝普通事件、cursor replay、重复/乱序、owner 隔离和 terminal close。
- frontend：snapshot + SSE 去重、重连无丢字/重字、SSE 失败回 polling、刷新保留 partial、stop/retry。

### KovaaK

- 单元/合同：strict native request schema、active profile/manifest/hash resolver、超时/失败映射、Web/non-Windows fail closed。
- Windows 实机：已运行聚焦、冷启动、窗口丢失、焦点拒绝、场景缺失、目标不匹配。
- 冷/热 scenario activation Smoke 未通过前，任何测试数字都不能支持“已自动切换 Challenge”的产品宣称。

## 9. 关键代码证据

- Coach 产品命令与 UI event：`webapp/backend/coach_commands.py`、`webapp/backend/coach_runtime.py`、`webapp/coach-runtime/src/product-command-tools.ts`
- Agent Run 与事件序号：`webapp/backend/coach_agent_runs.py`、`webapp/backend/db.py`
- 当前 Coach UI：`webapp/frontend/components/task6/CoachPanel.tsx`
- Evidence 播放：`webapp/frontend/components/task5/VideoView.tsx`、`docs/ARCHITECTURE.md` 的 backend-to-frontend handoff
- Scenario 身份：`knowledge/scenarios/registry.v1.json`、`knowledge/scenarios/launch-manifest.v1.json`、`kovaak_tracker/scenario_profiles.py`
- Desktop/native 边界：`webapp/frontend/src-tauri/src/capture_coordinator.rs`、`webapp/frontend/src-tauri/src/lib.rs`、`webapp/backend/native_capture_client.py`
- Pi 流事件：`third_party/pi/packages/agent/src/agent.ts`、`third_party/pi/packages/agent/src/types.ts`

外部边界资料：

- [KovaaK FAQ](https://kovaaks.com/kovaaks/faq)
- [KovaaK Scenarios](https://kovaaks.com/kovaaks/scenarios)
- [KovaaK's Update 3.0.0 - Deep Linking](https://store.steampowered.com/news/posts/?appids=824270&enddate=1652119547&feed=steam_community_announcements)
- [Microsoft SetForegroundWindow](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-setforegroundwindow)
- [Steamworks ISteamApps](https://partner.steamgames.com/doc/api/isteamapps)
- [Steam Workshop item 1337321696](https://steamcommunity.com/sharedfiles/filedetails/?id=1337321696)

## 10. Stop rule

本评估到此停止。未经点点明确批准：

- 不把 proposed descriptor/SSE/native adapter 写入 PRD 或 Architecture；
- 不创建 implementation plan、schema、migration、route、Tauri command 或 UI 组件；
- 不启动 KovaaK 做真实 Smoke；
- 不把“官方 URI 已 dispatch”或 PE 字符串当成目标 Challenge 已加载的本机产品证据；必须通过冷/热 Smoke 与可观察结果核验。
