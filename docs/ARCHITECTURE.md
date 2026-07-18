# Aiming Cookie — 总体架构

> **定位：稳定系统合同。** 本文展开 [`PRD.md`](PRD.md) 的产品方向，定义系统边界、数据归属、依赖方向、安全边界与演进约束。当前完成度、测试数字和日期化交接写入 [`PROGRESS.md`](PROGRESS.md)，不在本文维护。

## 1. 架构结论

Aiming Cookie 的目标形态是 **Desktop-first local product**：分析、History、Coach 关系、长期 profile 和主要数据留在用户机器。产品不建立账号、登录、session/JWT、entitlement、用户鉴权服务器、云端 LLM 代理或账号型同步。

```text
Desktop Client (Next.js UI in Tauri)
  ├─ Native shell / file picker / media access
  ├─ Capture Coordinator (Windows Raw Input + KovaaK window recording, opt-in)
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

当前 Desktop 技术基线是 Tauri 2 + Next.js WebView + 本地 Python runtime。Tauri 管理本次启动的 Python API/worker 生命周期，并通过 native command 向前端提供动态 loopback 地址和 launch-scoped token。

这些属于已经采用的架构基线：

- 本地路径导入由 native picker 发起；源文件不被修改，运行时复制到 managed workspace；
- Desktop 可自动发现 KovaaK Stats / Performance，并将解析后的 `KovaaKRun` metadata 保存在本地 SQLite；源文件仍由用户拥有；
- Windows Raw Input 由 Tauri native layer opt-in 启用，只在检测到 KovaaK 进程时采集相对 `dx/dy`、时间戳和鼠标按钮；非 Windows 明确返回 unsupported；
- 自动采集启用后，Capture Coordinator 在 KovaaK 进程 gate 内同时分段录制仅 KovaaK 窗口；系统不假装拥有实时 Challenge start/end 事件，而是在稳定 Stats / Performance 到达后按 canonical Challenge window 事后切出 Run-owned MP4 与 Raw trace；
- Raw Input trace、run metadata 和本地解析摘要不上传云端，也不自动进入 Coach 请求；
- Desktop 模式的本地 API 只监听 loopback，并要求本次启动 token；
- token 不持久化、不写普通日志，也不应传播给无关子进程；
- Tauri 退出时必须终止其管理的 runtime 进程树；
- Desktop UI 通过共享 versioned contracts 调用本地 runtime。

尚未稳定的打包、签名、公证、自动更新、Python distribution 和跨平台生命周期策略属于 Roadmap/后续 plan，不在本文伪装成已解决。

### 2.2 Web 验证形态

Web 形态可以运行 Next.js + FastAPI + worker + Coach sidecar，用于共享功能开发、受控预览和回归，但不是需要产品账号的正式交付形态。公开预览必须置于 VPN、SSO 或可信反向代理等环境访问控制后；这类访问控制不建立 Aiming Cookie 用户身份。浏览器自行发送的 owner header 不能成为本地 ownership 的信任根；浏览器/localStorage 不得持久保存长期 Provider secret，受控本地 backend 仍遵循 Desktop 相同的 profile、credential 与 redaction 合同。

### 2.3 当前产品合同边界

共享分析合同必须能表达三种输入模式：

- **input-native**：KovaaK Run + Raw Input，直接计算输入运动学；
- **multimodal**：input-native + MP4；native 仍是输入运动学主事实，MP4 用于直观回放、问题定位和可验证的视觉证据；
- **video-fallback**：MP4 + Stats，作为非 Windows、未开启 Raw Input 和旧工作流的 compatibility fallback，沿用 CV pan trajectory。

所有 Analysis 创建统一遵守 `Stats AND (MP4 OR (Raw + Performance))`。自动 MP4 必须已由 Run Finalizer 对齐到当前 Challenge；手动 fallback 必须由用户同时选择明确对应的 MP4 与 Stats，系统不能仅凭 MP4 猜测 Stats / Performance。

Raw Input 接通不等于完整 tracking 接通。它解决的是输入运动学事实源；目标/准星相对误差、视觉反应时刻和场景证据仍需 Performance/Stats 或 MP4。tracking 产品化仍需要完成指标命名、目标/准星语义和真实阈值标定。

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

- deterministic summary、diagnosis 和 prescription 是 Coach 的事实输入，不由 LLM 改写；
- `analysis_type` 必须显式，不能靠字段猜测 flicking/tracking；
- `input_mode` 必须显式区分 input-native / multimodal / video-fallback；报告和 UI 不能靠是否有 MP4 或 trace 猜测；
- `analysis_id` 必须绑定所属 Analysis Session 的稳定引用（当前 wire format 为 `analysis:{session_id}`）；terminal write 必须同时校验 owner/local profile、`analysis_type`、`input_mode` 与可选 `kovaak_run_id/ref` 均匹配已 claim 的 request，结构合法但属于另一 request 的结果必须 fail-closed；
- multimodal 不得让视频重新定义已经成立的输入运动学；视觉失败只降低回放/视觉证据 availability，不抹掉 native 结果；
- 每个关键指标必须能追溯到 Raw Input、Performance、Stats、MP4 或融合计算；证据缺失时使用 warning/availability 表达；
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
- Stats / Performance 原始文件保持用户所有，本地数据库只保存绝对路径、解析摘要和稳定 source key；Aiming Cookie 不自动复制、搬迁或删除这些源文件；
- Raw Input trace 是 Run 的本地 managed artifact，不是云端 artifact；没有有效 Performance 时间锚时不得伪造配对；
- 自动录制并按 Challenge window 切出的 MP4 是 Run-owned managed artifact；它不随 terminal Analysis 删除。手动导入 MP4 仍按用户源文件与 Analysis-owned managed copy 的既有边界处理；
- 一条 canonical Performance Challenge window 只生成一条 Run；连续多局必须分别 finalization，重复 watcher observation 必须幂等；
- finalization 后满足最低条件但尚未创建 Analysis 的 Run 进入 `pending_analysis` 或等价稳定状态；未选择 Run 不进入 Analysis job queue；
- Domain Core 必须在结果中保留 evidence provenance 和缺失范围，不能把单一来源的推断序列化成另一来源的测量；
- Run-owned Raw / MP4 的手动存储管理遵守 active automatic capture spec；Run metadata 整体删除、源文件失效、Run 与 Analysis 的解绑、精确 tombstone 和 reconciliation 仍必须由独立 spec/implementation plan 冻结后才能放行。

## 4. 持久化与文件生命周期

### 4.1 Canonical store

- 本地 SQLite 是 Desktop v1 的 canonical structured store；
- app-owned SQLite connection 必须开启 foreign-key enforcement；Provider profile/credential、
  Training Plan/version/transition 等声明的关系约束不能只停留在 DDL 文本中，也不能接受
  新的 orphan child row；
- Coach command journal 的 `(owner, command_name, idempotency_key)` 记录一旦建立，
  `parameters_digest` 不得被后到写入替换；应用层先查后写即使发生竞态也必须在 SQLite
  写入点返回稳定 conflict；同 digest 的后到 reservation claimant 必须 replay 已有记录，
  不能再次执行副作用；confirmed write 的 reservation conflict 必须回滚 confirmation 消费；
- JSON/JSONL 只作为交换、调试或兼容格式，不与 SQLite 形成双写事实源；
- 不建立账号型云同步；跨设备迁移使用显式导出 / 导入，任何未来远端备份都必须先由新的产品决策重新冻结；
- migration 必须可测试、幂等，并由批准的 plan 冻结；事务型 migration helper 不得使用会
  隐式提交外层事务的执行方式，失败时必须保留原异常并允许真实 rollback，不能留下半迁移状态。

### 4.2 Managed workspace

受管根目录同时包含 Run-owned evidence、Analysis-owned workspace 和可恢复的未完成采集数据。导入与 capture write 必须流式/有界，检查磁盘余量，并保证路径解析后仍位于受管根目录内。

删除合同：

- 删除 terminal analysis 时删除其 managed inputs/artifacts；
- 不删除用户原始源文件；
- 删除 terminal Analysis 不删除 Run-owned Raw trace 或自动 MP4；
- DB 状态与文件删除必须具备可恢复顺序（例如 commit 后清理、tombstone 或 reconciliation）；
- 崩溃后可识别和回收 orphan/partial workspace；
- KovaaKRun 的源文件可能由用户在应用外移动或删除；UI 必须表达 source unavailable，不能把路径失效当作分析成功；
- Raw Input trace 与 Run metadata 的保留、删除和孤儿清理不能从当前实现默认值推导，必须有明确合同；
- Storage 必须能统计总占用，并至少区分 Run 录像、Raw trace、Analysis artifacts 与未完成采集数据；
- v1 由用户分别手动移除 Run-owned MP4、Run-owned Raw trace 或未完成采集数据，删除影响必须先说明；Run metadata、既有 Analysis 和用户源文件保留，相关 evidence 引用改为 unavailable；
- 不启用静默自动 quota、TTL、按最旧优先清理或“一键清空所有数据”；低层有界瞬态 capture buffer 不是已 finalization 的 Run evidence，继续由其专门 lifecycle contract 管理；
- 精确删除事务、tombstone、失败恢复和并发语义必须由 active implementation plan tests-first 冻结。

### 4.3 Coach 数据归属

Coach 是用户关系层，不属于某个 analysis session：

- 用户可拥有不绑定分析的 Coach 对话，也可引用 0～N 次分析；
- 对话、关系状态和用户可见 run/event 必须归属于 Coach 层；
- 删除分析只使引用变为不可用/已删除，不级联删除已发生的对话或长期档案；
- analysis session scoped chat 只能作为迁移兼容层，不可成为新功能依赖；
- schema、summary/换窗、长期档案和 Pi session 投影若需变化，必须由 active spec/plan 单独冻结。

### 4.4 Coach Knowledge Registry

- Coach 知识是随产品版本发布、受 Git review 的只读产品资产，不属于任何 owner、Analysis、对话或 Provider；
- 一份 versioned Registry 是 Python 与 Pi TypeScript runtime 的 canonical knowledge source，禁止在两种语言中各自维护正文副本；
- Markdown 研究、理论、社区和处方材料只作为来源证据与编辑审查输入，不在运行时由模型直接读取或整份注入上下文；
- SQLite 只持久化历史对话实际使用的 registry/entry/version/source refs，不复制 Registry 正文，也不与静态 asset 双写；
- metric 定义、运动学机制、诊断适用范围、学术研究、社区 cue、处方/verification、Tracking 和身体/张力候选假设均可进入 Registry，但必须保留 source level、最高 claim、limitations 与 counterevidence；
- 身体、张力、握持、灵敏度和硬件内容在没有直接传感器或可比实验时只能作为 `experimental` 候选假设，不得生成 measured/deterministic root cause；
- 第一版只允许基于显式 topic/signal alias/metric/use 的 bounded deterministic retrieval；embedding、在线搜索或 LLM 相似度不得触发正式 diagnosis。

## 5. Coach Agent Runtime

Coach runtime 以项目内 Pi 源码基线为基础，由 Aiming Cookie 直接维护并产品化，不以持续兼容上游为约束。

边界：

- 通过与 UI 共用的稳定产品命令查询、创建、修改和执行当前本地 profile 可用能力；Coach 不是只读投影，也不得绕过本地 ownership、capability 或确认策略；
- 工具调用、失败、确认和结果定位必须形成可见事件；
- knowledge tool 在所有 v1 turn 中作为只读产品工具可用，不依赖写命令 bridge；实际使用的 registry/entry/version/source refs 进入安全 trace；
- 不允许通用 coding-agent 权限无边界暴露给产品用户；
- workspace、filesystem、shell、network 和 secret 权限遵循最小授权；
- 无 LLM 或 Coach 不可用时，确定性诊断闭环仍完整。

### 5.1 Provider、model 与认证

Coach 是否可用取决于当前本地 profile 是否选择并连接了可工作的 LLM Provider/model。Provider 可以无需认证，也可以要求 API key、OAuth、device-code 或其它 Pi 支持的认证方式；认证只发生在用户与模型服务之间，不创建 Aiming Cookie 账号或产品 session。

稳定边界：

- pinned Pi 的 built-in provider/model catalog 是产品 catalog；Aiming Cookie 不维护第二份 provider/model allow-list，所有 Pi 支持的 built-in 必须被动态暴露并接通为可选项；
- 自定义 OpenAI-compatible profile 至少保存 provider name、base URL、API key 配置状态和 model ID；当前 owner/profile 的 selected provider/model 是本地 canonical selection；
- API key 可以作为 local-first 权衡明文持久化在 app-owned 本地 SQLite/config；OS secure store 可以作为后续增强，但不是实现或发布前置条件；
- UI/API 只允许 set/replace/delete credential，并返回 `configured`、`auth_mode`、`credential_source`、`needs_reauth`、`last_test` 等状态，不得读回 secret；
- auth/refresh operation 对 credential 状态的完成写入必须绑定其启动时 revision；旧 operation 的成功 credential 或失败 `needs_reauth` 标记都不得覆盖、污染用户随后替换的新 credential；
- `LLM_PROVIDER` 与 `kovaak_tracker/coach/providers.json` 只保留为旧环境/配置兼容入口，不得继续充当 provider/model 事实源；迁移必须保留显式选择，不能把 obsolete `deepseek-chat` 静默改写为其它 model；
- active Coach turn 与 Analysis narration 只能使用 owner 当前 selected local profile；固定 DeepSeek 单价估算、`LLM_DAILY_BUDGET_CNY` 和 legacy `llm_cost_cny` 不得 gate 或记账 selected-provider 请求，除非未来先建立 provider-specific usage/currency contract；Provider 不可用时 deterministic Analysis 仍完成，narration 标为 not requested / unavailable；
- provider/model 目录、API key/ambient auth、OAuth/device-code 和 OpenAI-compatible 调用由 Pi 的 provider/model/auth 抽象承载；Aiming Cookie 负责本地 profile/credential persistence、owner/profile selection、turn/sidecar bridge、readiness、迁移、错误呈现和 redaction；
- Provider/model/credential/sidecar 失败只影响 Coach readiness，不得阻塞 Analysis、History 或 deterministic report/prescription；
- Pi coding-agent、shell、filesystem 与通用 workspace tools 属于独立 capability boundary，不因采用 Pi provider/runtime 而自动注册或暴露；
- 首次启动以 Provider onboarding 为主路径，但允许用户明确跳过并进入本地分析；未配置 Provider 时没有 Coach 对话、AI 解释、长期档案维护、训练计划或 Coach 产品命令，只有本地指标、确定性诊断、规则化提示、History 和可恢复的 Provider 配置入口。

## 6. 本地归属与安全

- Aiming Cookie 不提供产品账号、注册、登录、session/JWT、entitlement 或用户鉴权服务器；
- Desktop 本地数据默认属于当前 OS 用户/本地 profile；内部 `owner/profile` 字段表达本地数据隔离和稳定引用，不代表云端用户身份；
- Windows Raw Input 默认关闭，首次启用必须有明确 opt-in 和采集范围说明；
- Raw Input 只允许 KovaaK process gate 内的相对鼠标输入；不得采集键盘、桌面绝对坐标或其它应用的后台输入；
- 自动录屏只允许捕获 KovaaK 应用窗口，不得捕获完整桌面、其它应用窗口或系统通知；
- Raw Input trace 不进入 Provider 请求或普通日志；如果未来 Coach 要引用 trace，必须增加单独的用户确认和 evidence contract；
- Desktop loopback API 继续使用高熵、launch-scoped token，并限制 host/origin/接口暴露；这是本地进程安全，不是用户登录；
- Web 预览只允许在受控环境访问，不把外部 VPN/SSO/代理访问控制包装成产品账号；
- 所有 artifact、Coach 和 History 读写统一校验本地 profile、稳定引用和 capability；
- Provider API key、OAuth access/refresh token、Desktop launch token 和其它 secret 即使允许保存在 app-owned 本地 SQLite/config，也不得进入 AnalysisResult、Coach context/message、普通日志、diagnostics、crash report 或 export；
- 查询、导航和用户在当前指令中明确要求的普通可恢复产品动作可以直接执行；
- 删除、覆盖、credential 变更、Provider OAuth 授权/撤销、上传/分享、打开外部购买链接，或 Coach 自主推断而非用户明确要求的副作用动作，必须先说明影响并获得确认；所有 Agent 操作都要保留可审计结果。

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

1. 先保证 Capture Coordinator、Stats/Performance 事后 Run finalization、Raw/MP4 Run-owned evidence、用户选择与手动存储管理可靠；再保证输入原生 flicking、确定性解释/处方、History 和 Analysis 删除语义；
2. 完成完整 Pi catalog、selected provider/model、本地 credential persistence、必要的 Pi auth/OAuth/device-code 与首次 onboarding，再恢复用户可达的分析工作区、训练记录选择、视频/数据联动、完整 Provider Settings 和 Coach 侧栏；
3. 冻结并实现 source unavailable、Run/trace 删除、import/delete/runtime crash 等恢复合同；
4. 完成 Desktop packaging、Windows 实机验证、静态 Landing/release 分发和发布链；
5. 用视频与更多 tracking 数据增强输入原生指标，在目标/准星语义和真实阈值标定后接通完整 tracking；
6. 显式导出/导入、跨平台采集、外设推荐和远期硬件扩展在核心闭环验证后展开。

不得用 UI 重做、Desktop 壳或远端服务掩盖本地数据生命周期问题；也不得让 tracking、推荐生态或远期平台扩展阻塞当前 flicking 闭环。

## 9. 文档关系

- PRD 决定产品目标与范围；本文只展开系统合同。
- Roadmap 决定实施顺序与发布 Gate；Progress 记录当前事实。
- active spec 只冻结局部设计；active plan 才能授权 executor 修改代码。
- 代码和测试可证明“已实现什么”，不能静默修改本文定义的长期边界。
