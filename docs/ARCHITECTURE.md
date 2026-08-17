# Aiming Cookie — 总体架构

> **定位：稳定系统合同。** 本文展开 [`PRD.md`](PRD.md) 的产品方向，定义系统边界、数据归属、依赖方向、安全边界与演进约束。当前完成度、测试数字和日期化交接写入 [`PROGRESS.md`](PROGRESS.md)，不在本文维护。

## 1. 架构结论

Aiming Cookie 的目标形态是 **Desktop-first local product**：分析、History、Coach 关系、长期 profile 和主要数据留在用户机器。产品不建立账号、登录、session/JWT、entitlement、用户鉴权服务器、云端 LLM 代理或账号型同步。

```text
Desktop Client (Next.js UI in Tauri)
  ├─ Native shell / file picker / media access
  ├─ Capture Coordinator (Windows Raw Input + bounded KovaaK replay buffer, opt-in)
  ├─ Local Analysis Runtime (Python API + worker)
  ├─ Coach Agent Runtime (Pi-based runtime + product tools)
  ├─ App-owned local Provider profile / credential store
  └─ Local canonical data and managed artifacts
             │
             ├── User-selected LLM Provider or local model
             └── Online Surfaces without user identity
                 ├─ landing / documentation
                 ├─ release distribution
                 └─ optional versioned equipment catalog
```

当前仓库也可以用 Web 方式开发和验证共享 UI/API，但 Web 验证形态不能反向引入产品账号或云端数据所有权，也不能替代 Desktop 发布验收。

### 1.1 五个职责域

| 职责域 | 负责 | 不负责 |
|---|---|---|
| **Domain Core** | 确定性的输入原生分析、可选视觉证据、视频 compatibility fallback、指标、诊断、处方、报告模型 | HTTP、UI、产品账号、队列、文件生命周期 |
| **Local Analysis Runtime** | Capture Coordinator、job、worker、KovaaK Run finalization、输入时间对齐、managed workspace、本地 History、分析合同 | 产品账号、Provider 推理、通用 Agent 行为 |
| **Coach Agent Runtime** | 本地长期 Coach 关系、Agent run/event、与本地 profile 能力对齐的产品命令编排、上下文衔接 | 重新定义确定性诊断、绕过本地 ownership/capability、直接拥有 `KovaaKRun` 或分析文件 |
| **Client Surfaces** | Desktop/Web UI、onboarding、交互状态、Provider 认证状态呈现、native bridge | 数据真相、业务规则、密钥持久化 |
| **Online Distribution Surfaces** | 无用户身份的 landing、文档、release 分发和可选外设目录 | 用户账号、credential、Coach、History、训练档案或 LLM 请求代理 |

依赖方向应面向领域合同：UI 和 runtime 适配 Domain Core；Provider 与在线分发表面通过明确边界接入，不让领域逻辑依赖 FastAPI、Tauri、具体 LLM provider 或远端身份。

## 2. 运行形态

### 2.1 Desktop

当前 Desktop 技术基线是 Tauri 2 + Next.js WebView + 本地 Node Coach sidecar + Python Analysis runtime。Tauri 管理本次启动的两个本地 runtime：WebView 的 Coach 会话、上下文和 Agent Run 直接调用 Node sidecar；普通 Analysis API、KovaaK ingestion 与 worker 仍由 Python runtime 承担。Tauri 通过 native command 向前端提供动态 loopback 连接信息。

这些属于已经采用的架构基线：

- 本地路径导入由 native picker 发起；源文件不被修改，运行时复制到 managed workspace；
- Desktop 可自动发现 KovaaK Stats / Performance，并将解析后的 `KovaaKRun` metadata 保存在本地 JSON 文件（`runs/{id}/meta.json`）；源文件仍由用户拥有；
- Node Coach product command 可以基于 owner-scoped `KovaaKRun` 原子预留 Analysis Session、冻结 Run 输入并将 Session 推入 `queued`；canonical Run finalization、Analysis 文件生命周期、确定性分析和 terminal result 仍归 Local Analysis Runtime / Python worker 所有；
- Windows Raw Input 由 Tauri native layer opt-in 启用，只在检测到 KovaaK 进程时采集相对 `dx/dy`、时间戳和鼠标按钮；非 Windows 明确返回 unsupported；
- native layer 不改变鼠标设备 polling rate。Windows 上报进入 canonical trace 前按整数毫秒归一化：同一毫秒内所有运动报告的 `dx/dy` 分别求和为至多一条运动记录，按钮状态边沿按接收顺序作为例外单独保留，因此 canonical 运动序列最高 1000 Hz；不补零、不做 deadzone/低通滤波，也不把亚毫秒路径形状写成产品事实；
- 自动采集启用后，Capture Coordinator 在 KovaaK 进程 gate 内持续采集 Raw，并将 WGC 的 KovaaK 窗口 GPU surface 交给同适配器的硬件编码器；压缩码流只保留最近 300 秒的有界瞬态回放缓冲。系统不假装拥有实时 Challenge start/end 事件，而是在稳定 Stats / Performance 到达后按 canonical Challenge wall window 事后生成 Run-owned evidence；仅 `Pause Count = 0` 的 normal/timescale-only Challenge 生成永久 Run-owned MP4，`Pause Count > 0` 的暂停局 fail closed，只保留 partial/unavailable evidence，不把 Raw/Performance 声明为 canonical aligned；
- L0 Raw Input trace 与私有 parser payload 不上传云端或进入 Coach 请求；run metadata 与本地解析结果只能通过版本化、字段白名单化且有预算上限的 L1-L3 投影进入用户已选择的 Provider；
- Desktop 模式的本地 API 只监听 loopback，并要求本次启动 token；
- token 不写普通日志；
- Tauri 退出时必须终止其管理的 runtime 进程树；
- Desktop UI 通过共享 versioned contracts 调用两个本地 runtime；桌面 Coach 产品路径不得再以 Python 作为 Node sidecar 的请求中转。

尚未稳定的打包、签名、公证、自动更新、Python distribution 和跨平台生命周期策略属于 Roadmap/后续 plan，不在本文伪装成已解决。

### 2.2 Web 验证形态

Web 形态可以运行 Next.js + FastAPI + worker + Coach sidecar，用于共享功能开发、受控预览和回归，但不是需要产品账号的正式交付形态。公开预览必须置于 VPN、SSO 或可信反向代理等环境访问控制后；这类访问控制不建立 Aiming Cookie 用户身份。浏览器自行发送的 owner header 不能成为本地 ownership 的信任根；浏览器/localStorage 不得持久保存长期 Provider secret，受控本地 backend 仍遵循 Desktop 相同的 profile、credential 与 redaction 合同。

### 2.3 当前产品合同边界

新 Coach Run 由 Coach 自动选择一条证据等级路径，不向用户暴露 mode selector：

- `multimodal`：Stats + Performance + Raw Input + managed MP4 + canonical window；
- `input_native`：Stats + Performance + Raw Input + canonical window；没有视觉结论；
- `video_fallback`：Stats + managed MP4；没有 Raw Input provenance 或输入运动学测量。

服务端按 `multimodal > input_native > video_fallback` 选择最高可用路径；三者均不可用时不得创建 Analysis。所有路径都必须冻结 owner-scoped 输入快照，结果必须带 evidence provenance 和 limitations。历史旧结果仍可读，安装前孤立的 Stats/Performance 文件可导入、展示为历史 Run（缺 Raw/MP4，按 video_fallback 分析）。

暂停局是 v1 的明确 fail-closed 分支：当 Stats 表示 `Pause Count > 0` 时，不生成永久 MP4，不把暂停期间的 Raw/Performance 强行标为 canonical aligned，也不把该 Run 宣称为 ready；证据可以保留为 partial/unavailable 供诊断。normal 与 timescale-only（`Pause Count = 0`）继续使用当前永久 MP4 路径。

Raw Input 解决的是输入运动学事实源；目标/准星相对误差、视觉反应时刻和场景证据仍需经过本地 MP4 预处理、统一时间窗口和质量 Gate。首发目标覆盖 static/dynamic clicking、continuous tracking 与 target switching；各 family 只能消费其已验证的事实与指标。没有玩家移动遥测的 movement aiming 保持 outcome-only，不能由输入或结果反推移动机制。

场景 family 路由与精确 ScenarioProfile 是两层独立合同。任何场景都按多级识别进入大类管线（2026-08-15 决策）：exact reviewed hash 是已知图的精确加速通道；用户+Coach 确认的持久场景记忆（`scenario_override`，存于 app-data `config/scenario-overrides.json`，由 `scenario_memory.set` 在用户确认一次后按场景哈希写入，confidence confirmed 但只授予该 family 的 baseline）优先于 `.sce` 结构、挑战形态与名称各层；Run finalization 可以从同名本地 `.sce` 及已冻结的 Challenge 事实生成有界、无路径和无原文的 `scenario_behavior_descriptor.v1`，据此确认 static/dynamic family；由冻结 Stats/Raw 事实派生的挑战形态（`challenge_shape`，candidate 级统计判据，判据数值由代码与测试维护）在名称候选之上再给出一层大类候选；场景名关键词只产生标记为 `name_heuristic`/candidate 的大类候选，不构成场景身份；全部识别失败时落到 static clicking 基础分析并标记 `scenario_family_unresolved`。exact reviewed profile 仍是完整视觉/场景分析（目标相对误差、目标身份或速度、命中关联、场景处方）的唯一许可，不能因 family 相同跨 hash 复用；manifest gate 未激活的已审核 hash 与所有非 exact 识别都降级为该 family 的 baseline analyzer。baseline 只能消费 Raw Input、Stats、Performance 等原生已验证事实，必须显式保留缺少目标相对几何、命中关联、目标身份/速度和场景处方的 limitations；确无任何可分析数据（如 movement aiming 缺玩家移动遥测）的场景保持 outcome-only，而不是猜测。

Raw Input snapshot 的采样语义必须随格式版本识别。`ACRI v1` 保持历史逐报告 trace 的只读兼容；新的 1 ms canonical 运动归一化使用 `ACRI v2`。现存滚动 v1 snapshot 在继续采集前必须确定性迁移为 v2，迁移需保持每毫秒 X/Y 净位移、按钮边沿及其顺序；不得把 v1 与 v2 记录混装后标为单一语义。Analysis 可继续消费相同的 `timestamp_ms/dx/dy/buttons` 记录形状，但 provenance 必须保留实际 snapshot format version。

### Current Coach Run source gate

The new Coach Run analysis contract uses the highest available evidence tier.
Before enqueue, the server validates the allow-listed sources for each tier and
returns only stable missing-source codes plus a bounded path-free summary:

- `multimodal`: Stats, Performance, Raw Input, managed KovaaK-window MP4, and canonical time window;
- `input_native`: Stats, Performance, Raw Input, and canonical time window;
- `video_fallback`: Stats and managed KovaaK-window MP4.

The server selects `multimodal > input_native > video_fallback` and rejects the
Run only when all three tiers are unavailable. Every created Analysis freezes an
owner-scoped snapshot and exposes its evidence provenance and limitations.

## 3. 稳定数据合同

合同必须 versioned、可序列化、可在 Web/Desktop 间复用；消费者不得依赖内部 Python 对象或未声明字段。

### 3.1 Analysis Result

最小语义：

```text
AnalysisResult
  contract_version
  analysis_type
  input_mode
  analysis_id
  kovaak_run_id (optional)
  owner_id / local_profile
  source metadata
  evidence provenance / availability
  status
  deterministic summary
  diagnosis / prescription
  artifact manifest
  timestamps
  warnings / errors
```

约束：

- measured/derived 数值、事件、时间、来源、质量和 limitations 是 Coach 不得改写或重算的事实输入；deterministic diagnosis/prescription 是规则层生成的可追溯候选观察与初始排序，不是不可挑战的最终因果结论。Coach 可结合完整动作级 processed data、反例、历史和知识重新排序、保留或拒绝候选解释，但不得伪造测量、覆盖正式指标或把假设写成事实；
- `analysis_type` 必须显式，不能靠字段猜测 flicking/tracking；
- 历史报告与新 Coach Run 的 `input_mode` 都必须显式区分 input-native / multimodal / video-fallback；新 Run 使用 source gate 冻结的选择，不能靠是否有 MP4 或 trace 在下游猜测；
- `analysis_id` 必须绑定所属 Analysis Session 的稳定引用（当前 wire format 为 `analysis:{session_id}`）；terminal write 必须同时校验 owner/local profile、`analysis_type`、`input_mode` 与可选 `kovaak_run_id/ref` 均匹配已 claim 的 request，结构合法但属于另一 request 的结果必须 fail-closed；
- multimodal 不得让视频重新定义已经成立的输入运动学；视觉校验失败时保留受限结果和 warning，不把该 Analysis 静默改标为另一种 input mode；
- 每个关键指标必须能追溯到 Raw Input、Performance、Stats、MP4 或融合计算；证据缺失时使用 warning/availability 表达；
- Coach 可获得版本化、类型化、字段白名单化的 L1-L3 facts/evidence/diagnosis；不得获得 L0 原始载体或私有 parser payload；
- artifact 通过 manifest/稳定引用暴露，不泄露任意文件系统路径；
- 新字段优先向后兼容；破坏性变化升级 contract version。

### 3.2 Job State

```text
created → uploading/importing → queued → running → done | failed
```

约束：

- 只有 terminal analysis (`done`/`failed`) 可被用户删除；
- 状态转换必须幂等并有 owner 校验；
- worker 租约、心跳或等价机制必须能恢复 stale `running`；
- `uploading/importing` 同样需要崩溃恢复合同，不能永久阻塞用户；
- retry 创建新 attempt 或显式记录 attempt，不静默复用损坏状态。

### 3.3 Error

错误至少区分：

- validation / unsupported input；
- local CV / worker；
- KovaaK Run ingestion / source alignment / Raw Input；
- storage / disk；
- Coach runtime / tool；
- Provider network / LLM / credential；
- local ownership / capability。

已冻结的 Analysis source snapshot 若在消费前缺失、不可读或 revision 不一致，属于
`input_validation / source_unavailable`，同一 snapshot 原样重试不可恢复；用户必须重新提交
以冻结新的输入 revision。该错误不得携带源文件绝对路径、底层异常文本或 traceback。

用户文案、可重试性和内部诊断信息分层，密钥、token 和本地敏感路径不得出现在用户响应或普通日志中。

### 3.4 Artifact Manifest

每个 artifact 至少声明稳定 id、类型、所属 analysis、生成状态和可用性。UI 不应根据约定文件名自行寻找产物；Coach 引用也应指向稳定 artifact/analysis 标识。

### 3.5 KovaaKRun 与证据来源

`KovaaKRun` 是独立于 `Analysis Session` 的本地训练记录：

```text
KovaaKRun
  ├─ Stats / Performance source references
  ├─ parsed scenario / challenge / event summaries
  ├─ optional Run-owned local mouse trace
  ├─ optional Run-owned automatic MP4
  ├─ capture / finalization / analysis-readiness state
  └─ zero or more Analysis Session references
```

约束：

- Run 没有视频、没有 Raw 或 evidence 尚不完整时也可以存在；Analysis Session 可以引用 Run，但 Run 不反向拥有 Analysis Session；
- Stats / Performance 原始文件保持用户所有，本地 `runs/{id}/meta.json` 只保存绝对路径、解析摘要和稳定 source key；Aiming Cookie 不自动复制、搬迁或删除这些源文件；
- Raw Input trace 是 Run 的本地 managed artifact，不是云端 artifact；没有有效 Performance 时间锚时不得伪造配对；
- 自动录制并按 Challenge window 切出的 MP4 是 Run-owned managed artifact；它不随 terminal Analysis 删除。手动导入 MP4 仍按用户源文件与 Analysis-owned managed copy 的既有边界处理；
- 一条 canonical Performance Challenge window 只生成一条 Run；连续多局必须分别 finalization，重复 watcher observation 必须幂等；
- finalization 后满足最低条件但尚未创建 Analysis 的 Run 进入 `pending_analysis` 或等价稳定状态；未选择 Run 不进入 Analysis job queue；
- Domain Core 必须在结果中保留 evidence provenance 和缺失范围，不能把单一来源的推断序列化成另一来源的测量；
- Run-owned Raw / MP4 的手动存储管理遵守 active automatic capture spec；Run metadata 整体删除、源文件失效、Run 与 Analysis 的解绑、精确 tombstone 和 reconciliation 仍必须由独立 spec/implementation plan 冻结后才能放行。

## 4. 持久化与文件生命周期

### 4.1 Canonical store

- canonical 结构化数据以 JSON 文件直接保存在 `DATA_ROOT` 下：`runs/{id}/meta.json`
  （KovaaKRun）、`sessions/{id}.json`（Analysis Session/job）、`analyses/`（渐进式披露结果）、
  `training/`、`config/`、`profile.json`；不再使用 SQLite；
- JSON 文件是唯一事实源，不与任何第二份数据库双写；Coach 对话由 Pi `JsonlSessionRepo`
  持久化为 `conversations/{id}.jsonl`；
- Coach command journal 的 `(owner, command_name, idempotency_key)` 记录一旦建立，
  `parameters_digest` 不得被后到写入替换；应用层先查后写即使发生竞态也必须在写入点
  返回稳定 conflict；同 digest 的后到 reservation claimant 必须 replay 已有记录，
  不能再次执行副作用；confirmed write 的 reservation conflict 必须回滚 confirmation 消费；
- 不建立账号型云同步；跨设备迁移使用显式导出 / 导入，任何未来远端备份都必须先由新的产品决策重新冻结；
- 文件写入、删除与崩溃恢复必须可测试、幂等，并按 tombstone / reconciliation 顺序收敛，
  失败时不得留下半写入状态或 orphan workspace。

### 4.2 Managed workspace

受管根目录同时包含 Run-owned evidence、Analysis-owned workspace 和可恢复的未完成采集数据。导入与 capture write 必须流式/有界，检查磁盘余量，并保证路径解析后仍位于受管根目录内。

删除合同：

- 删除 terminal analysis 时删除其 managed inputs/artifacts；
- 不删除用户原始源文件；
- 删除 terminal Analysis 不删除 Run-owned Raw trace 或自动 MP4；
- 记录状态与文件删除必须具备可恢复顺序（例如提交后清理、tombstone 或 reconciliation）；
- 崩溃后可识别和回收 orphan/partial workspace；
- KovaaKRun 的源文件可能由用户在应用外移动或删除；UI 必须表达 source unavailable，不能把路径失效当作分析成功；
- Raw Input trace 与 Run metadata 的保留、删除和孤儿清理不能从当前实现默认值推导，必须有明确合同；
- Storage 必须能统计总占用，并至少区分 Run 录像、Raw trace、Analysis artifacts 与未完成采集数据；
- v1 由用户分别手动移除 Run-owned MP4、Run-owned Raw trace 或未完成采集数据，删除影响必须先说明；Run metadata、既有 Analysis 和用户源文件保留，相关 evidence 引用改为 unavailable；
- 不启用针对 Run-owned evidence 的静默自动 quota、TTL、按最旧优先清理或“一键清空所有数据”；低层 Raw / encoded-video capture buffer 不是已 finalization 的 Run evidence。encoded-video 按最近 300 秒墙上时间有界覆盖；Raw 的物理 retention 可以更长，但完整自动 Run 的共同保证只到 300 秒；
- 精确删除事务、tombstone、失败恢复和并发语义必须由 active implementation plan tests-first 冻结。

### 4.3 Coach 数据归属

Coach 是用户关系层，不属于某个 analysis session：

- 用户可拥有不绑定分析的 Coach 对话，也可引用 0～N 次分析；
- 对话、关系状态和用户可见 run/event 必须归属于 Coach 层；
- 删除分析只使引用变为不可用/已删除，不级联删除已发生的对话或长期档案；
- analysis session scoped chat 只能作为迁移兼容层，不可成为新功能依赖；
- schema、summary/换窗、长期档案和 Pi session 投影若需变化，必须由 active spec/plan 单独冻结。

Guided teaching 的持久状态也属于 Coach 层，但不替代 Training Plan 或训练事实：

- 每个 owner 只有一份 guided teaching 状态（`teaching/session.json`，schema `coach_teaching_session.v1`）；它只保存当前阶段、当前 lesson 的受限字段（观察、候选解释、cue、单一变更变量、练习引用）、已完成课程历史和暂停原因，不保存 Raw、路径、Provider secret 或未经确认的训练结果；
- teaching session 的候选解释、cue、单一变更变量和 retest intent 是教学过程状态。Training Plan item、execution 和 retest 仍是独立、owner-scoped 的正式事实，只有绑定当前用户明确陈述的 trusted instruction grant 或现有 trusted confirmation 可写入；
- 教学流程由 `teaching` skill 在提示词层承载：闭环各阶段、单变量原则和推进纪律写在 SKILL.md，每步推进都通过 `teaching_session.update` 落盘。`TeachingTurnContract` 类型仅作字段参考，不要求 per-run 快照；
- session 的推进、暂停和不适停止由 `teaching_session.update` 写入口校验和执行：该命令是 coach-runtime 的 native 写命令，原子写 `teaching/session.json`，并按 teaching-policy 强制阶段转移合法性与 lesson 字段白名单；没有 planner、确认机制或合同快照。Provider 不能声明完成、绕过写入口校验选择状态转移或把候选机制升级为测量事实；
- Analysis/history 是 metric comparability 与 meaningful-change policy 的唯一事实源。没有按 exact metric/version/conditions 注册的重复测量误差、worthwhile change 与必要 guardrail 时，非零 delta 必须保持 inconclusive；Profile 只能将精确相等的可比值显示为 stable，不能把任意非零差异显示为 improving/deteriorating；
- ratio 的数学等价展示可以保留其原单位语义（例如有明确来源的 ratio 显示为百分比）。本地 validator 拦截的是无来源的语义扩展、好坏评价、发生频率或因果解释，而不是等价格式本身；
- 删除 Analysis 只会令 session 中对应 evidence ref unavailable；不会删除 session、消息或已经确认的训练事实。若 session 无法继续安全教学，教学流程必须回到 intake / unresolved，而非猜测替代证据。

### 4.3.1 有限 KovaaK 成绩同步与训练阶段进阶

- v1 只接入一份随产品发布并经过审核的 versioned course catalog。它冻结 39 组两阶段项目配对、来源课程分类和可选 exact local ScenarioProfile ref；它不是第二套 Scenario Registry，也不能用外部名称替代本地 hash。外部作者、课程代号和阶段名称属于内部 provenance，不作为用户侧功能名称；
- Steam Profile URL 或 17 位 ID 必须由用户明确提交并同意使用。用户可在本地 owner scope 保存一个规范化的本人 Steam ID，用于后续显式手动刷新；它独立于 Benchmark snapshot，删除连接不删除既有成绩。聊天中提交的临时 Profile 仅存于当前 turn 的内存绑定，既不写入 Benchmark、消息、trace、audit、confirmation 或 Training Plan，也不影响用户历史成绩。两类身份均不得进入 LLM Provider 请求、Coach context、普通日志、公开导出或遥测；临时输入在到达 LLM 前必须替换为只在 loopback bridge 内有效的 opaque ref。
- KovaaK 网页后端没有稳定公开 API 合同。同步必须设置超时，完整校验两个阶段各 39 个已知且无重复的场景，并原子写入现有 `training/scores.json`（benchmark records）。任一阶段失败时不写半份快照，保留上次成功数据，且不影响 Analysis、History 或 Coach 可用性；
- Coach 只接收 versioned、去身份、大小受限的成绩摘要：课程版本、同步时间、完成度、项目名称、最高分、项目档位和待检查顺序。分数只帮助选择先检查哪个项目或难度；具体动作问题仍必须来自当前 Analysis 和 Registry；
- 较低阶段仍按现有 exact Analysis、Training Plan item、执行确认与 matched retest 完成教学。只有已注册、带 exact metric/version/conditions 证据的 improvement policy 得出的确认复测结果，或用户明确确认一次主观结果为 improved 时，Coach 才可建议对应的更高阶段项目；当前 Analysis metric 的任意非零 delta 不自动构成 improved/worsened。该建议是下一次压力测试和新基线，不是迁移已成功；
- 更高阶段项目没有 reviewed exact hash / analyzer contract 时只能显示项目名称级建议，不能生成正式 Training Plan item、可比 Analysis 或迁移结论。获得 exact identity 后继续复用现有 11 字段 plan item 和 confirmation，不新增课程进度状态机。

### 4.4 Coach evidence boundary

- L0 原始载体与私有实现对象（Raw trace、MP4/frame、原始 Stats CSV、Performance protobuf、绝对路径、私有 parser payload 与未知字段）只留在本地 Runtime/受管 artifact，不能进入 Provider request、Coach tool result、message、trace、普通 API 或日志；
- L1 CanonicalSourceFacts、L2 DerivedEvidence 与 L3 diagnosis/profile/plan 必须版本化、类型化、字段白名单化并带 provenance、completeness 与 limitation；完整规范化 facts 不等于原始载体或 future unknown field；
- L2 必须保留 analyzer 定义的全部动作级 processed event rows：static 每次 flick/click、dynamic 每次 acquisition/click、tracking 每个 episode/change/loss/reacquisition 或固定分析窗口、switching 每条 leave-to-first-outcome 链。它们可以留在本地 artifact 并通过固定查询操作消费，但不能只剩若干代表片段或整局摘要；EvidenceSegment 主要是解释和本地视频回放锚点；
- 用户启用 Coach 并选择 Provider 后，L1-L3 的 bounded context/tool results 是普通 Coach turn 数据，不增加逐 Run consent。owner、capability、预算和审计边界仍在本地 bridge 强制；
- MP4 在当前合同中只由本地确定性预处理器生成数值 signals/events/confidence，Coach 引用 EvidenceSegment，用户在 UI 播放本地片段。未来视觉模型必须另立版本化合同，明确用户授权、限定片段、Provider、预算、retention 与 `model_inferred` 边界。

#### Backend-to-frontend handoff contract

正式前端只消费以下版本化后端边界，不读取 Analysis-owned artifact 文件、不解析原始载体，也不复制 Coach command 逻辑：

- `GET /api/sessions/{analysis_id}`：读取 owner-scoped、已校验的 `AnalysisResult` summary；
- `GET /api/sessions/{analysis_id}/evidence-segments`：读取 `frontend_evidence_segments.v1`，包含安全 EvidenceSegment metadata、coverage/confidence/limitations，以及相对该 Analysis canonical window 的 `evidence_segment_playback.v1` seek anchor；不包含 frame、MP4 bytes、路径或 parser payload；
- `GET /api/sessions/{analysis_id}/video`：仅在 owner-scoped managed MP4 可用时流式播放；前端用上一个接口的相对毫秒 anchor 定位；
- `/api/coach/tools/execute`：只给 Coach bridge 使用，所有 evidence 下钻仍受 bridge reachable refs、owner、cursor 和 budget 约束；
- `POST /api/benchmarks/sync/kovaaks`：用户明确同意后手动刷新有限 KovaaK 成绩；失败不覆盖上次成功快照，响应不回显 Steam Profile URL 或 ID；
- `/api/kovaak-connection`：本地 owner scope 的已连接账号状态、设置和移除；公开响应不回显 Steam Profile URL 或 ID；
- `POST /api/kovaak-connection/refresh`：使用已连接账号手动刷新有限 KovaaK 成绩；没有连接或上游失败不覆盖上次成功快照；
- `POST /api/training-plans/{plan_ref}/items`、`POST /api/training-plan-items/{item_ref}/executions`、`POST /api/training-plan-items/{item_ref}/retests`：显式用户写入训练事实，要求 `Idempotency-Key`。Coach bridge 可预填同一三类训练事实；绑定当前用户明确陈述的 trusted instruction grant 可直接写入，`coach_inferred` 调用只能返回 `needs_confirmation`，现有 trusted UI/backend confirmation 仍可写入。模型不得把推断、沉默或聊天语气伪装为已完成练习、主观反馈或复测结果。

Analysis 删除后，以上 Analysis/Evidence refs 返回 unavailable/deleted 语义；原有 Coach 消息、画像和训练历史不被级联删除。

### 4.5 Coach Knowledge Registry

- Coach 知识是随产品版本发布、受 Git review 的只读产品资产，不属于任何 owner、Analysis、对话或 Provider；
- 一份 versioned Registry 是 Python 与 Pi TypeScript runtime 的 canonical knowledge source，禁止在两种语言中各自维护正文副本；
- Markdown 研究、理论、社区和处方材料只作为来源证据与编辑审查输入，不在运行时由模型直接读取或整份注入上下文；
- Analysis 结果 JSON 只携带实际使用的 registry/entry/version/source refs，不复制 Registry 正文，也不与静态 asset 双写；
- metric 定义、运动学机制、诊断适用范围、学术研究、社区 cue、处方/verification、Tracking 和身体/张力候选假设均可进入 Registry，但必须保留 source level、最高 claim、limitations 与 counterevidence；
- 身体、张力、握持、灵敏度和硬件内容在没有直接传感器或可比实验时只能作为 `experimental` 候选假设，不得生成 measured/deterministic root cause；
- Registry capability 采用严格递增前缀：`explanation_only` → `diagnosis_support` → `candidate_experiment` → `scenario_prescription`。消费者必须显式请求所需 capability；Provider 可自然组织表达，但不得把低权限 entry 提升为诊断、实验或处方；
- `explanation_only` 不携带 cue、剂量或复测；`candidate_experiment` 必须携带可逆 cue、dose guardrail、matched retest 与 stop rule；只有 `scenario_prescription` 可以绑定 exact local scenario 和 near-transfer retest；
- Registry 版本、schema 与 entry ref 必须显式校验并 fail closed；entry 正文、字段或 capability 语义变化必须提升 `entry_version`，不得复用已有 `knowledge:<id>@<version>`；历史 v1/v2/v3 按原 version 精确可读，新版本只能通过同一 loader/query API 发布，禁止另建 community store、resolver 或 Provider authored knowledge state；
- 新 `analysis_result.v2` issue 可携带 `observation_ref` 与成对的 `knowledge_registry_version` / `knowledge_entry_refs`。producer 只能复用 Registry 已声明的 observation ref；没有精确覆盖的本地 observation 仍可展示，但不获得 Coach 教学或 Training Plan 写入授权；
- Coach 优先按 exact version、单一 entry ref 与 observation ref 解析，显式 `metric:*` 仅作一致性检查。未知、失活或不匹配的引用 fail closed；缺少 refs 的历史 signal 仅能作旧数据显示，不能由此编译新的 prepared Training Plan item；
- Analysis 不复制 Registry 的 definition/cue/dose/retest 正文。Provider 在取得通过 capability 的 Registry 内容后可以自然改写表达，但不得提升 capability 或生成新的 knowledge ref；
- 第一版只允许基于显式 topic/signal alias/metric/use 的 bounded deterministic retrieval；embedding、在线搜索或 LLM 相似度不得触发正式 diagnosis。

## 5. Coach Agent Runtime

Coach runtime 以项目内 Pi 源码基线为基础，由 Aiming Cookie 直接维护并产品化，不以持续兼容上游为约束。

边界：

- 通过与 UI 共用的稳定产品命令查询、创建、修改和执行当前本地 profile 可用能力；Coach 不是只读投影，也不得绕过本地 ownership、capability 或确认策略；
- Coach 对诊断拥有综合判断权：应能检查完整 processed event table 的覆盖、条件分布、支持证据与反例，并明确接受、降低或拒绝规则层候选诊断；它不拥有正式指标重算权，也不能把聊天推断回写成测量事实；
- 工具调用、失败、确认和结果定位必须形成可见事件；
- knowledge tool 在所有 v1 turn 中作为只读产品工具可用，不依赖写命令 bridge；实际使用的 registry/entry/version/source refs 进入安全 trace；
- 不允许通用 coding-agent 权限无边界暴露给产品用户；
- workspace、filesystem、shell、network 和 secret 权限遵循最小授权；
- 无 LLM 或 Coach 不可用时，确定性诊断闭环仍完整。

### 5.1 Product command authority

- 用户当前消息明确、无歧义要求的已注册产品操作（包括删除等 consequential operation）直接执行，不因操作有后果而另加第二次确认；
- Coach 主动推断或提议、但用户当前消息没有明确要求的 consequential operation，必须先说明影响并获得确认；
- 命令处理器独立执行 ownership、capability、state transition、stable-ref reachability、idempotency 和 audit 校验；
- secret/credential 输入、OAuth/device-code 交互、系统/隐私权限、文件选择、现实训练和主观事实不进入 Agent execution，只能由可信 UI 接收并由 Coach 等待验证；
- 不存在 shell、filesystem、任意 HTTP、任意 Tauri invoke 或模拟输入等通用权限；产品操作通过已注册的 typed command 扩展。

Guidance 层只做确定性的产品编排（引导用户到正确的 UI 控件、预填非敏感值、等待并验证状态），不是第二个 Agent runtime。

### 5.2 Provider、model 与认证

Coach 是否可用取决于当前本地 profile 是否选择并连接了可工作的 LLM Provider/model。Provider 可以无需认证，也可以要求 API key、OAuth、device-code 或其它 Pi 支持的认证方式；认证只发生在用户与模型服务之间，不创建 Aiming Cookie 账号或产品 session。

稳定边界：

- pinned Pi 的 built-in provider/model catalog 是产品 catalog；Aiming Cookie 不维护第二份 provider/model allow-list，所有 Pi 支持的 built-in 必须被动态暴露并接通为可选项；
- 自定义 profile 使用显式 `OpenAI-compatible` 或 `Anthropic-compatible` 协议，至少保存 provider name、base URL、API key 配置状态和 model ID；协议不得从 URL 文本猜测。Anthropic-compatible 的 base URL 是服务根地址，Pi 负责附加 `/v1` 路径；历史输入末尾的 `/v1` 在保存和运行时规范化为根地址。连接前可以用对应协议短暂读取模型列表，但 API key 不得进入公开响应、日志或浏览器存储；当前 owner/profile 的 selected provider/model 是本地 canonical selection；
- API key 可以作为 local-first 权衡明文持久化在 app-owned 本地 `config/provider.json`；OS secure store 可以作为后续增强，但不是实现或发布前置条件；
- UI/API 允许 set/replace/delete/read credential；本地应用中 API key 可读回以便用户确认配置。仍返回 `configured`、`auth_mode`、`credential_source`、`needs_reauth`、`last_test` 等状态；
- auth/refresh operation 对 credential 状态的完成写入必须绑定其启动时 revision；旧 operation 的成功 credential 或失败 `needs_reauth` 标记都不得覆盖、污染用户随后替换的新 credential；
- `LLM_PROVIDER` 与 `kovaak_tracker/coach/providers.json` 只保留为旧环境/配置兼容入口，不得继续充当 provider/model 事实源；迁移必须保留显式选择，不能把 obsolete `deepseek-chat` 静默改写为其它 model；
- active Coach turn 只能使用 owner 当前 selected local profile；Analysis worker 不得加载 Provider 或生成 narration，新 `analysis_result.v2` 只保留 `not_requested` / `null` 兼容 envelope，旧 v1/unversioned narration 继续可读；固定 DeepSeek 单价估算、`LLM_DAILY_BUDGET_CNY` 和 legacy `llm_cost_cny` 不得 gate 或记账 selected-provider 请求，除非未来先建立 provider-specific usage/currency contract；
- provider/model 目录、API key/ambient auth、OAuth/device-code 以及 OpenAI-compatible / Anthropic-compatible 调用由 Pi 的 provider/model/auth 抽象承载；Aiming Cookie 负责本地 profile/credential persistence、owner/profile selection、turn/sidecar bridge、readiness、迁移、错误呈现和 redaction；
- 首次 onboarding 和每次创建 Analysis 前都必须存在已测试的 selected Provider；Provider 后续请求失败时保留已保存记录，由 Coach 显示错误并引导 Settings 修复，不转为本地无 Provider 分析；Provider-to-Provider fallback 暂不启用；
- Pi coding-agent、shell、filesystem 与通用 workspace tools 属于独立 capability boundary，不因采用 Pi provider/runtime 而自动注册或暴露；
- 首次启动以不可跳过的 Provider onboarding 为主路径；Provider 与 Windows Raw Input/窗口回放采集授权、启用都是进入主工作区的硬门槛。后续 Provider 失效不回退 onboarding，Coach 对话提供错误和 Settings 恢复入口。

## 6. 本地归属与安全

- Aiming Cookie 不提供产品账号、注册、登录、session/JWT、entitlement 或用户鉴权服务器；
- Desktop 本地数据默认属于当前 OS 用户/本地 profile；内部 `owner/profile` 字段表达本地数据隔离和稳定引用，不代表云端用户身份；
- Windows Raw Input 默认关闭，首次启用必须有明确 opt-in 和采集范围说明；
- Raw Input 只允许 KovaaK process gate 内的相对鼠标输入；不得采集键盘、桌面绝对坐标或其它应用的后台输入；
- 自动录屏只允许捕获 KovaaK 应用窗口，不得捕获完整桌面、其它应用窗口或系统通知；
- 自动视频主路径必须保持 WGC surface、颜色转换和硬件编码在 GPU 路径内；硬件编码不可用、适配器不匹配或视频队列背压时独立降级；两级硬件编码枚举（全局与采集适配器 LUID）均空或均不适配时，自动路径受控降级到第三级软件编码（Microsoft H264 Encoder MFT，SYNCMFT|SORTANDFILTER）——encoder path 记入诊断包、回放缓冲保持有界、已知边界为编码尾延迟约 267ms；除此之外不得静默回退到其它持续 CPU 编码；
- Raw Input trace、MP4、原始 CSV/protobuf 和私有 parser payload 不进入 Provider 请求或普通日志；Coach 只可在 L1-L3 合同内消费本地 broker 返回的 bounded 规范化结果；
- Desktop loopback API 限制为 host/origin 暴露；每次启动由 Tauri 生成随机高熵 launch-scoped token（`AIMING_COOKIE_DESKTOP_TOKEN`），不持久化、不写普通日志；
- Web 预览只允许在受控环境访问，不把外部 VPN/SSO/代理访问控制包装成产品账号；
- 所有 artifact、Coach 和 History 读写统一校验本地 profile、稳定引用和 capability；
- provider secret 不进入 AnalysisResult、Coach context/message、普通日志、诊断或导出。app-owned 本地 `config/provider.json` 按 local-first 合同明文持久化，API key 可读回以便用户确认。OAuth/API key 状态必须可恢复且可审计。
- 查询、导航和用户在当前指令中明确要求的已注册产品操作可以直接执行，不因删除、覆盖或其它 consequential classification 另加第二次确认；
- Coach 主动推断或提议、但用户当前消息没有明确要求的 consequential operation，必须先说明影响并获得确认；
- credential/secret 输入、Provider OAuth/device-code 交互、系统与隐私权限、文件选择、现实训练和主观事实不注册为 Agent 可执行操作，只能由可信 UI 接收并由 Coach 等待验证；所有 Agent 操作和 guidance 结果都要保留安全、可审计的结果。

## 7. 运行与可观测性

稳定发布基线应覆盖：

- API、worker、Coach runtime 和 Desktop child process 的生命周期管理；
- liveness/readiness；
- 带 correlation id 的 structured logs；
- queue depth、running age、failure、duration、disk usage；
- stale job、partial import、orphan workspace 和 runtime crash 的恢复；
- Capture Coordinator / Run Finalizer 状态、未完成采集数据、分类型 disk usage；
- process detection、window capture、Raw Input、Stats/Performance、finalization/alignment、CV、storage、Coach、LLM、Provider auth 和本地 capability 错误分开统计；
- core/backend/frontend/Desktop/真实素材/E2E 的分层 Gate。

具体当前缺口与最近验证结果只写 `PROGRESS.md`。

## 8. 演进约束

顺序原则：

1. 先保证 Capture Coordinator、Stats/Performance 事后 Run finalization、Raw/MP4 Run-owned evidence、用户选择与手动存储管理可靠；再冻结统一时间、场景、规范化 evidence 和 bounded Coach broker，接通 static/dynamic clicking、continuous tracking 与 target switching 的专项 analyzer；
2. 完成完整 Pi catalog、selected provider/model、本地 credential persistence、首发支持的 Provider 认证方式与首次 onboarding，再恢复用户可达的分析工作区、训练记录选择、视频/数据联动、完整 Provider Settings 和 Coach 侧栏；OAuth/device-code 可以后续接入，不阻塞首发闭环；
3. 冻结并实现 source unavailable、Run/trace 删除、import/delete/runtime crash 等恢复合同；
4. 完成 Desktop packaging、Windows 实机验证、静态 Landing/release 分发和发布链；
5. 完成跨 family 的质量、fixture、真实 Run 与 Coach usefulness Gate；没有移动遥测的 movement aiming 继续只保留 outcome-only；
6. 显式导出/导入、跨平台采集、外设推荐、未来视觉模型和远期硬件扩展在核心闭环验证后展开。

不得用 UI 重做、Desktop 壳或远端服务掩盖本地数据生命周期问题；也不得让推荐生态、未来视觉模型或远期平台扩展阻塞首发完整 Coach 闭环。

## 9. 文档关系

- PRD 决定产品目标与范围；本文只展开系统合同。
- Roadmap 决定实施顺序与发布 Gate；Progress 记录当前事实。
- 设计材料与历史 plan 只作为可选参考；它们不授权 executor，也不构成开发流程门槛。
- 代码和测试可证明“已实现什么”，不能静默修改本文定义的长期边界。
