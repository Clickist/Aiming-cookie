# Coach Product Commands, Explanations, and Provider Settings — Design Contract

> 状态：active
> 目的：冻结 Coach 如何拥有与当前用户对齐的产品能力、如何把确定性运动学转成可行动解释，以及用户如何配置 LLM provider/model/auth。
> 授权更新：本文件 §2.3、§2.5 中与明确自然语言指令和二次确认有关的规则，已由较新且更具体的 [`2026-08-10-coach-product-operator-guided-workflows-design.md`](2026-08-10-coach-product-operator-guided-workflows-design.md) 覆盖；其余命令、解释和 Provider 边界继续有效。
> 上游：[`../../PRD.md`](../../PRD.md)、[`../../ARCHITECTURE.md`](../../ARCHITECTURE.md)、[`../../frontend-uiux-design.md`](../../frontend-uiux-design.md)
> 相关合同：[`2026-07-13-analysis-evidence-coach-context-design.md`](2026-07-13-analysis-evidence-coach-context-design.md)、[`2026-07-13-frontend-product-reconstruction-design.md`](2026-07-13-frontend-product-reconstruction-design.md)

## 1. 产品结论

1. Coach 是产品操作层，不是只读摘要助手；它通过与 UI 共用的稳定产品命令调用当前本地 profile 拥有的能力。
2. Coach 不得绕过本地 owner/profile、capability、数据范围或确认策略，也不通过 shell、任意文件访问或数据库直写绕过产品边界；Pi coding-agent/shell/filesystem tools 是单独能力边界，不自动暴露。
3. Aiming Cookie 不提供产品账号、登录、session/JWT、entitlement 或鉴权服务器；Coach 是否可用只取决于 Provider/model/auth/runtime 是否可用。
4. 首次启动以 Provider onboarding 为主路径，但允许用户明确跳过并进入本地分析；Provider 认证是用户与模型服务商的关系，不创建 Aiming Cookie 身份。
5. pinned Pi built-in provider/model catalog 是产品 catalog，不另设 Aiming Cookie allow-list；所有 Pi 支持的 built-in 都应暴露并可用，同时支持自定义 OpenAI-compatible profile。
6. 用户得到的不是指标列表，而是“观察 → 白话解释 → 证据 → 诊断 → 训练 → 预期变化 → 复测”的完整行动链。
7. input-native 是运动学主路径；MP4 主要用于直观回放、问题定位和视觉 evidence。`video_fallback` 作为 compatibility path 保留，不代表长期产品继续以视频为主事实源。

## 2. Coach 产品命令

### 2.1 共用命令层

UI 点击和 Coach tool call 必须进入同一领域命令或应用服务，统一执行：

- 本地 owner/profile 校验；
- capability availability；
- 输入验证和业务规则；
- 幂等、重试和错误语义；
- confirmation policy；
- audit event 与结果 reference。

不得为 Coach 复制一套行为不同的 History、Analysis 或 Training Plan 写路径。

### 2.2 能力范围

Coach 目标能力包括：

- 查询、筛选和比较 Run / Analysis / History；
- 打开页面、Analysis、诊断、指标、Flick、视频时间点与 evidence；
- 创建、重试和查看 Analysis；
- 生成、保存、激活、暂停、调整和回顾 Training Plan；
- 读取和更新经过确认的长期用户资料；
- 管理用户明确要求的普通产品设置；
- 返回每次命令的状态、失败域、可恢复操作和稳定结果引用。

删除、Provider credential、上传/分享和打开外部购买链接仍使用专用高风险命令，不与普通写操作混用。

### 2.3 授权与确认

动作分为三类：

| 类别 | 例子 | 规则 |
|---|---|---|
| 查询 / 导航 | 查 History、比较分析、打开 Analysis、定位 Flick、seek 视频 | 直接执行，不重复确认 |
| 用户明确要求的普通可恢复动作 | “分析刚才那局”、打开结果、应用可撤销筛选、生成训练计划草案 | 参数无歧义时直接执行；返回清晰结果 |
| 高后果或 Coach 自主推断的副作用 | 删除、覆盖计划、credential 变更、Provider OAuth 授权/撤销、上传/分享、打开外部购买链接、用户未明确要求的写操作 | 先显示动作、对象与后果，明确确认后执行 |

确认不得只问“确定吗”。确认 payload 至少包括：命令、目标对象、关键参数、将改变什么、不能自动恢复的部分和取消入口。

### 2.4 命令 envelope

```text
coach_product_command.v1
  command_id
  command_name
  actor = coach
  owner_scope
  parameters
  authorization
    source = explicit_user_request | confirmed | system_safe
    user_message_ref?
    confirmation_ref?
  risk = query | navigation | reversible_write | destructive | credential | external | external_purchase
  idempotency_key?
  created_at
```

```text
coach_product_command_result.v1
  command_id
  status = succeeded | failed | cancelled | needs_confirmation | unavailable
  result_ref?
  ui_event?
  warning_or_error?
  audit_ref
```

Tool response 不返回 secret、绝对路径、raw trace 或任意内部 payload。

### 2.5 Task 5 已冻结的产品行为（2026-07-14）

#### 产品采集器与 Coach 文件能力

- Reflek / Aiming Cookie 的本地采集器可以扫描产品已知或用户明确配置的工作区外游戏数据目录，用于发现 Performance、Stats CSV、Raw Input 和已支持的关联 MP4；扫描结果先登记为 owner-scoped Run / managed artifact。
- Coach 只能通过稳定 `run:*`、`analysis:*`、evidence/event refs 操作已登记产品对象；不得获得通用目录遍历、任意路径读取、shell 或 filesystem tool。
- Coach 可以请求刷新已配置的数据源、从已登记 Run 创建 Analysis，或打开导入界面；任意新路径仍由采集器或 UI 文件选择器解析，不进入模型参数、tool result、audit 或消息。

#### Training Plan lifecycle 与版本记录

```text
draft -> saved -> active <-> paused
```

- `generate_draft` 创建 owner-scoped、稳定 `plan:*` / version ref；draft 尚不是当前训练计划。
- `save` 只允许 `draft -> saved`；每个本地 profile 最多一个 `active` plan。
- `activate` 在不存在其它 active plan 时直接执行；若会替换 active plan，先返回 `needs_confirmation`，确认后把旧 plan 置为 `paused` 并激活目标 plan。
- `pause` 只允许 `active -> paused`。
- `adjust` 不原地覆盖 plan body，而是为同一 `plan_id` 创建递增版本；用户明确要求时直接执行，Coach 自主推断时先确认。每个版本保存 adjustment reason、依据的 Analysis/metric/knowledge refs 与 verification targets。
- `diagnostic_context` 的 `analysis_refs`、`metric_refs`、`diagnosis_refs`、`prescription_refs`、`knowledge_refs`、`evidence_refs` 必须分别匹配同名 ref kind；版本级 `evidence_refs` 只接受 Analysis/metric/knowledge refs，不能把一种 ref 填入另一种语义字段。
- `review` 是只读比较，不自动完成、暂停或调整计划；证据不可比或不足时返回 `insufficient_evidence`。
- Task 5 不新增 completed、archived、delete 或 retention 状态。旧版本只读保留；SQLite 中的 plan/version/transition/audit 记录是事实源，普通日志、临时文件或 Coach 文本不是计划事实源。
- verification targets 只能来自确定性 diagnosis 与已批准 prescription，包含 target metric、expected direction、comparability requirements、retest guidance 和 insufficient-evidence behavior；未校准指标不得生成绝对达标线。

#### Analysis retry 与幂等

- `error.retryable=false` 只禁止自动重试；用户明确要求手动重试且受管输入仍存在时可以重试。
- owner 已有其它 queued/running Analysis 时不启动第二个任务，返回 `unavailable` 和 active Analysis ref。
- write command 必须提供稳定 `idempotency_key`；UI HTTP 写入口通过标准 `Idempotency-Key` header 传递它，并与 Coach tool 共用同一 command journal。journal 使用 `(owner, command_name, idempotency_key)` 唯一约束：同 key + 同参数 digest replay 已持久化结果；同 key + 不同参数返回 `idempotency_conflict`。唯一行的参数 digest 一旦建立不得被后到 upsert 替换；应用层 stale lookup 不能绕过该约束。执行副作用前必须以 insert-only claim 持久化保守 reservation；若另一个 claimant 已抢先写入同 digest，后到请求 replay 已有 reservation/result，不得再次执行副作用。confirmed write 必须在同一事务内消费一次性 confirmation 并写入 reservation，reservation 与既有 digest 冲突时整个事务回滚，不能留下“confirmation 已消费但 journal 未占位”的窗口。若进程在最终结果落盘前中断，同 key 重试返回 `unavailable/idempotency_outcome_unknown`，不得自动重放可能已经完成的写操作。
- Pi turn 即使在最终回复阶段失败，也必须返回此前已完成的安全 `tool_events`；真实 sidecar 的非 200 failure body 也必须先按 versioned response 校验并传播这些事件。只有可证明请求尚未 dispatch 的连接/启动失败可以自动 fallback；sidecar 已接收但响应丢失或无效、已有工具事件，或 subprocess 已启动后失败时，不得把整轮请求重放到另一 runtime。此时保留 command audit/trace，返回可恢复失败，由用户检查当前状态后再显式重试。

#### Typed navigation 与 MP4 evidence

- 模型不得提交 URL 或 route。`ui_event` 使用 allow-listed semantic target：History、Analysis section、Flick event、evidence ref 或 video time。
- Task 5 只生成 typed `ui_event`；正式前端在 Task 6 消费事件并切页、聚焦事件或 seek 视频。
- input-native / Performance / Stats 是计算与诊断事实源；MP4 是可选的视觉回放和 evidence locator。证据定位表示打开对应 Analysis/Flick/evidence，并在存在 MP4 时跳到已知时间点，不表示 Coach 重新从 MP4 推导运动学结论。

#### Deleted / unavailable refs

- 已附加到 Coach 的 Analysis ref 沿用现有 tombstone 区分 `deleted`。
- 对未附加且已不存在的对象返回 `unavailable/not_found`；Task 5 不新增全局删除 tombstone 或 retention lifecycle。

## 3. 从指标到训练行动的解释合同

### 3.1 分层职责

- **运动学计算层**生成 metric、event、distribution、quality 和 provenance；
- **确定性诊断层**根据已批准规则生成 observation、issue、priority 和允许的 prescription；
- **知识层**提供指标语义、研究锚点、社区实践、动作 cue 和适用限制；
- **Coach**负责按用户问题组织语言、补充上下文并调用产品命令，不自行发明测量或改写 deterministic result。

### 3.2 Explanation envelope

每个可进入 Coach 正式解释的问题至少投影为：

```text
coach_issue_explanation.v1
  issue_ref
  title
  priority
  observation
    metric_refs[]
    event_refs[]
    distribution_summary?
    history_comparison?
  plain_language_meaning
  diagnosis
    statement
    claim_level
    supporting_evidence_refs[]
    counterevidence_or_limitations[]
  training
    prescriptions[]
      scenario
      cue
      purpose
      dosage?
      target_metrics[]
      expected_direction[]
      retest_after?
      stop_or_adjust_rule?
      source_level
  expected_result
  verification
    comparable_requirements
    success_signals[]
    insufficient_evidence_behavior
```

### 3.3 Claim level

| claim_level | 可使用的措辞 | 禁止 |
|---|---|---|
| `measured` | “检测到”“本次为” | 越过 metric 定义推断身体原因 |
| `deterministic_rule` | “这些证据说明/更符合” | 省略规则适用条件或 limitation |
| `research_supported` | “研究支持该动作机制通常与……” | 把外部实验阈值直接冒充本产品健康区间 |
| `community_consensus` | “社区常见做法/可尝试” | 写成已测因果或唯一正确流派 |
| `experimental` | “一种待验证可能性” | 驱动正式 severity、History verdict 或强制处方 |

握持张力、身体状态、灵敏度原因和未验证 target inference 默认不能使用 `measured`。

### 3.4 必须支持的核心指标族

按验证顺序接入：

1. Flick segmentation、开始/峰值/结束和 movement timing；
2. peak speed、time-to-peak、acceleration/deceleration duration 与 `decel_frac`；
3. stopping/settle time、corrective count、reverse ratio；
4. submovement count/overlap；
5. SPARC 与其它平滑度指标；
6. path length、path efficiency、straightness；
7. 局内分布、稳定性、离群值；
8. 满足 comparability predicate 的历史变化。

指标未完成真实校准时可以展示值、定义、分布和个体历史变化，但不得硬标“健康/异常”或使用未经验证的绝对阈值驱动 severity。

## 4. Knowledge 接入

现有 `kovaak_tracker/coach/knowledge.py`、`agent_kb.py`、`advice.py`、`profiles.py` 和 `planning.py` 是能力基础，但不能原样无审查注入当前 Pi Coach。

接入规则：

- 保留按 signal/topic 渐进检索，不把全部研究和社区材料预加载到每轮 prompt；
- 保留 `source_ref` 与 `source_level`；
- 修正当前把张力、身体原因或初步阈值写得过于确定的文案；
- 社区层只提供 narrator vocabulary、cue 和候选训练方法，不单独产生 deterministic diagnosis；
- 每条处方必须能回答“练什么、注意什么、改善哪个指标、怎样复测”；
- 知识版本应可审计，历史 Coach message 保留当时实际使用的 source references。
- dynamic clicking、tracking 与 switching 的知识条目只能解释已由对应 analyzer/evidence 合同支持的 observation；目标身份、运动条件或关联不可观测时退化为 outcome-only / unavailable，知识不得补造机制、selection 或 target-relative claim。

## 5. Provider Settings 与认证

### 5.1 Provider profile

```text
llm_provider_profile.v2
  profile_id
  owner_id / local_profile
  kind = builtin | custom_openai_compatible
  provider_id?          # builtin Pi provider id
  provider_name
  base_url?             # required for custom_openai_compatible
  model_id
  auth_modes[] = api_key | oauth | ambient
  selected
  enabled
  status
  credential_configured
  credential_source?
  needs_reauth?
  last_test?
```

Built-in profile 的 provider/model metadata 从 pinned Pi catalog 动态读取；custom OpenAI-compatible profile 必填 provider name、base URL、API key 和 model ID。API/UI 不返回 credential 内容；API key 只允许 set/replace/delete，OAuth credential 只允许 authorize/refresh/revoke/status。

`auth_modes[]` 从 pinned Pi `Provider.auth` 动态投影，不由 Aiming Cookie 维护 provider ID allow-list。`device_code` 是 authorize 期间由 Pi `AuthEvent` 动态产生的交互，不是静态 auth mode；`none` 只在 pinned Pi runtime 实际解析为无需 credential 时作为状态呈现。本阶段不自行增加 `local` profile kind，也不放宽 custom OpenAI-compatible 的 API key 要求。

完成后的 credential 由 Python/backend 按 owner/profile 持久化；Pi TypeScript runtime 使用 operation-scoped `CredentialStore` 执行 login、ambient resolution 与 refresh。进行中的 browser/device-code operation 是本地临时状态，进程重启后标记为 interrupted 并允许重试，不持久化或重建 PKCE verifier、authorization code、device-code promise 等 provider-specific 中间状态。

### 5.2 Provider 状态

```text
unconfigured
configuring
testing
ready
auth_expired
needs_reauth
connection_failed
model_unavailable
disabled
```

`auth_expired` 与 `needs_reauth` 只适用于确实需要认证的 Provider；`auth_modes = none` 的 Provider 不得显示登录、授权或重新认证状态。

Provider failure 只影响 Coach；本地 Analysis、History、确定性 Diagnosis 和 Training Prescription 继续可用。

### 5.3 Pi 采纳边界

Pinned Pi 当前提供 provider factories、完整 model catalog、`Models` / `MutableModels`、可注入 `CredentialStore`、API key/ambient auth resolution、OAuth provider registry，以及 Anthropic、GitHub Copilot、OpenAI Codex OAuth/device-code 实现。

冻结边界：

- Pi 的 built-in provider/model catalog 直接作为产品 catalog；Aiming Cookie 不复制、不裁剪、不维护 allow-list，catalog 更新跟随仓库 pinned Pi 版本；
- 所有 Pi 支持的 built-in 必须可被列出、选择并通过对应 Pi provider/auth path 使用；缺少 credential、model 或连接失败通过 readiness/status 表达，不隐藏 catalog；
- Aiming Cookie 负责 custom profile、app-owned 本地 persistence、owner/profile selection、Settings/onboarding、turn/sidecar bridge、readiness、错误呈现、secret redaction、audit 和 connection test；
- `LLM_PROVIDER` 与 `kovaak_tracker/coach/providers.json` 只作为 compatibility input；迁移后不再是事实源，也不得把 obsolete `deepseek-chat` 静默迁移成其它 model；
- `LLM_DAILY_BUDGET_CNY`、固定 DeepSeek 单价估算与 `llm_cost_cny` 只保留 legacy 数据/配置兼容；selected-provider Coach turn 和 Analysis narration 不得被它们 gate 或写入伪精确 CNY cost。没有 provider-specific usage/currency contract 时 cost 保持 unknown/0；
- Pi provider/runtime capability 与 Pi coding-agent、shell、filesystem tools 分离；后者只有经过独立产品命令、权限和确认合同才能接入。
- 用户选定且 ready 的 Provider 可接收普通 Coach context，包括类型化 allow-listed Run facts、分页 timeline/events 和 bounded derived evidence；这不要求逐 Run consent，且不放宽 Raw、原始 CSV/protobuf、私有 parser、MP4 或路径的禁入边界。

### 5.4 Secret 与存储

- local-first 产品接受 API key 明文保存在 app-owned 本地 SQLite/config；secure store 可以作为后续 hardening/UX 选项，但不是 Task、onboarding 或发布 blocker；
- 浏览器/localStorage 不保存长期 secret；Web 受控预览若使用本地 backend persistence，必须遵循与 Desktop 相同的 owner/profile、command 和 redaction 边界；
- 环境变量与旧配置仅作为兼容 credential source，UI 只能显示来源状态；
- API key、OAuth access/refresh token 和其它 secret 不进入 AnalysisResult、Coach context/message、普通日志、diagnostics、crash report、export 或用户 Markdown 仓库；
- credential set/replace/delete 与 OAuth authorize/refresh/revoke 使用专用 provider command，返回状态而不返回 secret；本阶段 `revoke` 仅表示删除 Aiming Cookie 本地 credential/logout，并明确返回 Provider 远端 token 未被通用合同保证撤销；
- OAuth/device-code 使用 Pi 支持的 Desktop/local callback 或 Provider 官方流程；若某种方式要求 Aiming Cookie 用户账号、中心 credential broker 或鉴权服务器，则不采用该方式。

### 5.5 首次 Provider onboarding

首次 onboarding 与完整 Settings 使用同一 Provider profile、credential 命令和状态机，但只暴露最小成功路径：

```text
价值 / 成本 / 数据说明
  → 选择已验证连接方式
  → Provider 认证与 model 选择
  → connection test
  → ready | 可恢复失败 | 明确跳过进入本地模式
```

冻结规则：

- 不要求 Aiming Cookie 注册或登录；onboarding completion 和本地模式选择保存在本地 profile；
- 在连接前说明 Aiming Cookie 开源免费、第三方 Provider 可能收费、连接后可获得的 Coach 能力，以及无 Provider 仍可用的本地能力；
- API key、OAuth/device-code 和 local/custom Provider 使用结构化 UI，credential 永不进入 Coach 对话；
- 展示完整 pinned Pi built-in catalog；每个 profile 的 credential/model/connection readiness 单独表达，不用 Aiming Cookie allow-list 隐藏选项；
- 用户可以跳过、取消、恢复和稍后重试；`unconfigured` 或失败不得阻塞 Analysis、History、Diagnosis 和 Training Prescription；
- 连接成功后引导用户建立第一条训练记录，不打开没有证据上下文的空聊天框；
- 第一次 Analysis 完成且 Provider ready 时，前端自动展开一次 Coach；后续启动遵循用户保存的展开状态。

## 6. 必要测试

- UI 与 Coach 调用同一命令 handler，本地 owner/profile、validation 和 result 一致；
- explicit user request、needs confirmation、cancel、idempotent retry 和 audit result；
- Coach 无法调用未注册产品命令、shell、任意文件和 coding-agent tools；
- explanation 必须含 evidence refs、claim level、training target 和 verification；
- community/experimental sentinel 不进入 measured/deterministic claim；
- 未校准指标不产生正式健康/异常 verdict；
- Provider catalog 与 pinned Pi built-ins/models 一致，不经过产品 allow-list；
- custom OpenAI-compatible profile 校验 provider name、base URL、API key、model ID，并按 owner/profile 持久化 selected provider/model；
- API key 可明文保存在本地 SQLite/config，但 Provider API、AnalysisResult、Coach context/message、普通日志、diagnostics 和 export 不返回或泄露 API key、access token、refresh token 或环境变量值；
- `LLM_PROVIDER/providers.json` compatibility migration 幂等，obsolete `deepseek-chat` 不被静默改写；
- API key set/replace/delete，OAuth authorize/cancel/timeout/refresh/revoke，local/no-auth provider test；
- app-owned SQLite connection 开启 foreign-key enforcement；Provider credential 不能脱离 profile，
  Training Plan version/transition 不能脱离对应 plan/version，直接写入 orphan child row 必须失败；
- v11/v12 schema helper 必须服从调用方 transaction，不得用隐式 commit 破坏 rollback；模拟失败后
  不能留下半创建的 Training Plan 或 Coach command 表；
- 首次 onboarding 覆盖价值/成本/数据说明、ready、跳过、取消、恢复和连接失败；不出现产品登录或账号依赖；
- Provider/model/credential/sidecar unavailable 不影响 deterministic Analysis/History/report；
- MP4 缺失/失败不影响 input-native diagnosis；video-fallback legacy 回归继续通过。

## 7. 非目标

- 不实现 Aiming Cookie 产品账号、登录、session/JWT、entitlement、鉴权服务器、付费墙、额度、套餐或结账；
- 不把 Coach 变成通用 coding agent；
- 不默认发送 raw trace 或完整原始 payload；
- 不把 Aiming Cookie provider/model allow-list、云端 catalog 或 silent model substitution 引入产品；
- 不取消 video-fallback compatibility；
- 不在没有产品数据校准时直接采用研究或社区的绝对阈值。
