# Aiming Cookie — 总体架构

> **状态**：Architecture Baseline v1 — **已定型**
> **建立日期**：2026-07-10
> **定型日期**：2026-07-11
> **适用范围**：flicking Alpha、后续 Web 正式化与 Desktop hybrid 演进
> **上游事实源**：`docs/PRD.md`
>
> 本文定义系统边界、依赖方向、稳定合同和迁移顺序。产品范围冲突时以 PRD 为准；具体实现步骤以 `docs/ROADMAP.md` 和对应 plan 为准。

## 0. 定型与变更控制

本架构已经定型，后续实现只能在以下边界内展开，不能由 implementation plan 或 executor 静默改变：

- 产品范围、用户语义或阶段目标变化：先更新 `docs/PRD.md`；
- 分层、数据归属、信任边界或稳定合同变化：由 Sol 更新本文，并同步 Roadmap 与相关 spec/plan；
- implementation plan 只能细化已批准边界，不能重新定义 schema 归属、删除语义、身份信任或 Web/Desktop 责任；
- executor 发现计划与本架构或当前代码不一致时必须停止，不得通过临时兼容层制造第二套架构。

“已定型”指系统边界和数据归属已冻结，不表示所有模块都已经实现；未完成项仍按 Roadmap 的 P0/P1/P2 施工。

## 1. 架构结论

Aiming Cookie 不重写现有核心链路，而是在已经可运行的 flicking 分析能力上补齐可靠运行时、History 和发布边界。

目标分为四个协作边界：

```text
Domain Core
- 指标计算、规则诊断、确定性分析产物
- 不依赖 FastAPI、队列、页面或云端身份

Local Analysis Runtime
- job、workspace、文件生命周期、恢复、History、趋势
- 当前由 Web backend/worker 承担，未来由 Desktop sidecar 复用

Coach Agent Runtime
- 用户级常驻 Coach 关系、agent loop、领域工具和流式交互
- 默认以完整 Pi 源码作为 Coach runtime 基线，由 Aiming Cookie 接管并允许直接修改；不要求持续兼容或跟随 Pi 上游升级
- 调用稳定的领域工具；连接本地 Runtime、Coach 持久化边界与云端 LLM proxy

Cloud Services
- verified auth、LLM proxy、usage/billing、可选同步
- 不承担默认 CV 计算，也不是本地 History 的唯一事实源
```

核心原则：

1. **确定性诊断是事实源**：LLM 只能解释、追问和组织表达，不能改写指标或制造新的诊断事实。
2. **CV local-first**：正式产品的 CPU 密集分析默认在用户设备运行；当前 Web worker 是验证壳和迁移前运行载体。
3. **History local-first**：本地数据库是 session、结果和趋势的 canonical store；JSONL 降为导入/导出格式，云同步后置。
4. **Web 与 Desktop 共用 Runtime 合同**：页面、FastAPI 和 sidecar 都通过同一组版本化 job/result/artifact 合同接入分析能力。
5. **先稳定合同，再选壳和 IPC**：Tauri/Electron、sidecar 进程协议、打包工具可以后置；结果合同和文件生命周期不能后置。
6. **长期云端密钥不进入 Desktop**：生产 LLM 调用经云端代理完成；离线时仍可使用 CV、规则诊断和本地 History。

## 2. 当前能力基线

以下为 2026-07-11 对当前工作区、测试和实现的只读对账快照。

### 2.1 已实现

- MP4 + KovaaK Stats CSV 上传；
- SQLite session queue，worker 可消费 queued job；
- flicking CV 和公平指标分析；
- deterministic diagnosis、处方和 report；
- 可选 LLM narration 与 Coach 多轮对话；
- processing、report、coach 页面；
- session ownership 比较和基础失败状态；
- versioned AnalysisResult/Error/Artifact contracts、legacy read adapters 和 TypeScript consumer adapter；
- worker lease、heartbeat、stale recovery、显式 retry 和 lease ownership 保护；
- Web History 列表、状态/摘要、详情回看、done/failed 删除和 `/history` 页面。

当前真实主链路：

```text
MP4 + Stats CSV
→ FastAPI upload
→ SQLite session queue
→ Python worker
→ flicking CV / metrics
→ deterministic diagnosis / report
→ optional LLM narration
→ Report / Coach UI
```

### 2.2 已验证

- 全仓单一 pytest 入口：`218 passed, 1 skipped`；
- 分开核验：Python core `116 passed`；Web backend `102 passed, 1 skipped`；
- 仓库真实 MP4 + CSV E2E：上传、enqueue、worker、CV、report、done result 已跑通；
- Frontend type-check 和 production build 通过。

这些验证证明核心技术链存在，但不等于产品发布闭环已经成立。

### 2.3 部分实现或尚不存在

| 能力 | 当前状态 | 架构判断 |
|---|---|---|
| Runtime contracts | v1 contracts、legacy adapters 与集成验收已完成；plan 已归档 | 已定型运行基线，不得重复实施 |
| Coach 数据归属 | 当前 `chat_messages.session_id` 与删除时级联 chat 仍存在 | **内部预览 P0**：Pi assessment/Spike 后用新 plan 迁移 |
| History 增强 | 最小列表、回看、删除已实现；趋势/对比未做 | 趋势/对比为 P1，不阻塞最小 History P0 |
| 默认路由 | `/` 仍固定渲染 Upload，未按历史存在与否分流 | P1 独立 plan，不塞入 History Task |
| 身份 | 客户端可提供 `X-User-Id`，不是可信认证边界 | 仅可在受控环境临时使用 |
| Workspace / 文件生命周期 | 上传仍全量读内存；无显式 per-session workspace、TTL、quota、orphan scan | Runtime P0 |
| 测试统一入口 | 根目录单命令已通过：`218 passed, 1 skipped` | 已完成；后续 release gate 继续叠加真实素材与 browser E2E |
| 可运营部署 | 无 supervisor、health/readiness、metrics、告警和 CI release gate | 发布 P0 |
| Browser E2E | 尚未建立 | 发布 P0 |
| Desktop | 只有研究文档，无 shell、sidecar、IPC、installer 或 updater | 后续里程碑 |
| Tracking | core/CLI 存在，Web worker 未接通且有理论债 | 不阻塞 flicking Alpha |

## 3. 目标组件与职责

### 3.1 Domain Core

**职责**：

- 解析 flicking/tracking 分析输入；
- 计算指标和可解释的中间结果；
- 生成 deterministic diagnosis、处方和图表数据；
- 对同一输入和同一 `analysis_version` 产生稳定、可序列化的结果。

**不得承担**：

- HTTP、用户鉴权、页面路由；
- queue claim、worker heartbeat、retry；
- 隐式写入全局 `output/`；
- 直接决定云端同步、计费或保留策略；
- 把 LLM 文本作为指标和诊断的事实来源。

### 3.2 Local Analysis Runtime

**职责**：

- 创建和领取 job；
- 为每次分析创建显式 workspace；
- 管理输入文件、输出 artifact 和生命周期；
- 执行 Domain Core 并持久化 versioned result；
- lease、heartbeat、timeout、retry 和 stale recovery；
- History 的写入、读取、列表、删除、趋势、导出和导入；
- 向 Web API 或 Desktop IPC 暴露相同语义的接口。

建议逻辑接口：

```text
create_job(input_manifest) -> job_id
get_job(job_id) -> Job
retry_job(job_id) -> Job
list_sessions(filters) -> Session[]
get_session(session_id) -> SessionDetail
delete_session(session_id) -> deletion_result
export_history(range) -> archive
import_history(archive) -> import_result
```

当前 FastAPI + SQLite + worker 可以先实现这些语义；后续 Desktop sidecar 复用，不要求现在就拆出独立服务进程。

### 3.3 Coach Agent Runtime

**职责**：

- 承载用户级、持续存在的 Coach 关系，并编排 agent loop、领域工具和流式交互；
- 将分析、History、趋势、报告和后续应用操作接成稳定、versioned 的领域工具；
- 通过 Cloud Services 的 LLM proxy 推理，并向 UI 提供文本、工具活动、产品确认和结果/跳转意图；
- 保证 Coach 与 analysis session 解耦：分析是可选的 0～N 次上下文引用，不是对话所有权。

**Pi 源码接管边界（默认方案已固定，纳入范围待核验）**：

- 默认接管完整 Pi 源码作为 Coach runtime 起点，可删除、禁用或重写 coding-agent 专用能力；不以保持上游兼容或持续合并 Pi 更新为目标；
- assessment 不再比较“依赖上游还是 fork”，而是确认具体基线版本/package、源码与第三方许可证义务、需要保留的 runtime 能力、需要产品化改造的模块和可删除的 coding/TUI 能力；
- 最小 Spike 使用接管后的真实源码验证 LLM proxy、领域工具、事件流、恢复及 Node/Python 边界，但不提前修改正式 schema/API/UI；
- Pi 已有 workspace、权限和 sandbox/container 能力应先评估后决定保留或改造，不无证据重写；Aiming Cookie 的 owner/delete/billing 等产品语义仍由领域层负责。

**领域边界**：

- 业务工具必须执行 owner、state、删除、计费等领域规则，并只返回稳定的产品 projection；不得把 SQL、绝对路径或内部 HTTP endpoint 当作工具合同；
- Agent 不改写 deterministic diagnosis，也不以模型自然语言替代产品确认或前端导航；
- 现有 session-bound chat 是兼容遗留，不能作为该层的新依赖。

### 3.4 Cloud Services

**职责**：

- verified auth 和用户身份；
- LLM API 代理与密钥保护；
- 真实 usage 计量、credits、订阅和限额；
- 可选的跨设备同步与备份；
- 发布渠道需要的远程配置和版本检查。

**不承担**：

- 默认视频 CV 计算；
- 用户本地 session 的唯一副本；
- 离线 deterministic diagnosis 的可用性依赖。

### 3.5 Client Surfaces

- **当前 Web**：产品行为和 Runtime 合同的验证壳；内部技术预览的交互入口。
- **未来 Desktop shell**：承载同一前端体验，连接本地 Runtime/sidecar，并访问云端身份和 LLM 代理。
- **CLI/测试**：继续作为 Domain Core 和 Runtime 的低成本验证入口，不定义产品范围。

## 4. 必须稳定的数据合同

History、重试和 Desktop 都依赖同一合同。进入这些功能前，至少稳定以下概念；具体字段以单独 implementation plan 为准。

### 4.1 Analysis Result

每份持久化结果必须包含：

- `schema_version`：序列化结构版本；
- `analysis_version`：指标/规则算法版本；
- `summary_type`：例如 flicking fair summary；
- 输入摘要和 profile 参数；
- deterministic metrics、diagnosis、prescriptions；
- 可选 narration，明确标记 provider/model/usage；
- artifact manifest；
- 创建时间和完成时间。

**规则**：

- JSON 不允许写入 `NaN`/`Infinity`；不可表示值使用 `null` 并保留原因；
- TypeScript 类型从同一 schema 生成或用契约测试校验，不能长期手写漂移；
- 新版本必须有读取旧版本的迁移或明确拒绝策略；
- LLM narration 变化不得改变 deterministic result 的身份。

### 4.2 Job State

最小状态机：

```text
queued → running → done
                 ↘ failed
failed → queued  (显式 retry)
running → queued/failed (lease 过期后的 recovery policy)
```

每个 job 至少需要：

- `id`、owner、state；
- `attempts`、`max_attempts`；
- `worker_id`、`lease_expires_at`、`heartbeat_at`；
- 结构化 error；
- 输入和输出 manifest；
- created/started/finished timestamps。

### 4.3 Error

错误至少分类为：

- input/validation；
- local CV/runtime；
- LLM/provider；
- network/cloud；
- storage/disk；
- internal/unknown。

错误合同必须包含稳定 code、用户可读 message、可重试性和诊断 trace id；前端不得依赖解析任意异常字符串。

### 4.4 Artifact Manifest

Runtime 只向上层暴露 artifact id、类型、大小、校验信息和生命周期，不把机器绝对路径写入长期合同。输入视频、CSV、timeline、图表数据和导出包都通过 manifest 管理。

## 5. 持久化与文件生命周期

### 5.1 Canonical store

- v1 本地 canonical store：SQLite；
- JSONL：历史兼容和导入/导出格式，不再作为并发运行时主存储；
- B 阶段云同步：本地仍可工作，云端存同步副本和冲突元数据；
- schema migration 必须随应用版本发布并可回滚或安全失败。

### 5.2 文件策略

每个 session 使用独立 workspace：

```text
sessions/<session_id>/
  input/
  artifacts/
  logs/
  manifest.json
```

产品必须定义并实现：

- 流式上传或分块写入，避免整文件常驻内存；
- 原视频**默认不按时间自动删除**；`done/failed` 分析由用户主动删除时，一并删除该 session 的输入与 artifacts；
- `queued/running` 分析不可删除；
- orphan scan 只清理未被有效记录引用的临时/遗留文件，不能把正常 History 当成过期数据删除；
- quota 和低磁盘保护优先拒绝新上传并给出可理解提示，不静默删除有效记录；
- History 删除与 artifact 删除保持一致。

如果未来要增加 TTL、分析后立即删除或其他自动清理默认，必须先更新 PRD，再修改本架构与实施计划。

### 5.3 Coach 持久化边界

Coach 是用户关系层，不属于 analysis session 或分析 workspace。以下是已固定的产品/架构不变量：

- 用户拥有一条主 Coach 关系；它可以没有分析引用，也可以引用 0～N 次已完成分析；
- 对话、关系状态和可恢复的用户可见交互必须归属 Coach 层，而不能只依赖可删除的 analysis session；
- 分析删除仍删除该 session 的输入与 artifacts；它只把相关引用变为不可用/已删除，不能抹掉已发生的对话或长期关系；
- 当前 `chat_messages.session_id` 及 `/sessions/{id}/chat` 是迁移前遗留接口，不可作为新功能的依赖或终局架构。

尚未冻结：底层表结构、迁移版本、Pi session 是否复用、run/tool-event 的持久化投影、上下文摘要/换窗及长期表现档案策略。它们必须在 Pi adoption assessment 和最小 Spike 后，由替代 implementation plan 统一决定；旧 persistent Coach design/plan 不得再作为 schema 或 API 实施合同。

## 6. 身份与安全边界

- `X-User-Id` 只可作为开发或可信反向代理注入的信息，不能接受浏览器自行声明；
- 内部技术预览必须置于 VPN、SSO 或其他可信外层访问控制后；
- 正式受邀 Web 或公开 Web 必须验证 session/JWT，并以服务端身份派生 owner；
- session、artifact、chat、History 的读写必须统一经过 owner 校验；
- Desktop 本地数据默认属于当前 OS 用户；云同步另做账号绑定和加密策略；
- API key、refresh token 和长期 LLM 密钥不得写入分析结果或普通日志。

## 7. 运行与可观测性

内部预览开始前最小运行基线：

- API 和 worker 都由 supervisor 管理；
- `/health` 反映进程存活，`/ready` 验证 DB、workspace 和必要配置；
- job/session/trace 使用关联 id 的 structured logs；
- 记录 queue depth、running age、failure count、analysis duration、disk usage；
- worker 异常退出后可恢复 stale job，不留下永久 `running`；
- CV、LLM 和 storage 错误分开统计；
- release gate 同时包含 core tests、backend tests、真实素材 E2E、browser E2E 和 frontend build。

## 8. 演进顺序

```text
现有 Web 验证壳
  ↓ 1. 收口 versioned contracts、worker recovery 与最小 History 的集成状态
  ↓ 2. Pi 源码接管 assessment + 隔离 Coach Spike（确认纳入/删改边界，不改正式 schema/API/UI）
  ↓ 3. 按已确认的源码基线和产品化边界编写并批准持久 Coach 的替代 implementation plan
  ↓ 4. 迁移 Coach 数据归属、旧 chat 与分析引用；验证删除分析不删 Coach
  ↓ 5. 补齐 workspace / 文件生命周期 / 运行基线 / 可信访问 / release gate
受控环境 flicking 内部技术预览
  ↓ 6. History 趋势/对比/导入导出和默认路由等 P1 闭环质量
  ↓ 7. 显式 runtime API + Desktop shell/sidecar spike
  ↓ 8. verified auth / LLM proxy / optional sync
正式 Desktop hybrid 产品
```

不得逆序推进：

- 不在 job/result 合同未稳定时并行实现多套 History；
- 不在 Runtime 仍依赖 FastAPI 隐式上下文时开始正式 sidecar 打包；
- 不用 Desktop 壳掩盖 worker 恢复、文件生命周期和身份边界问题；
- 不让 tracking 接通、计费或全面视觉重构阻塞 flicking Alpha 的可靠闭环。

## 9. 现在决定与后置决定

### 现在固定

- 四个协作边界：Domain Core / Local Analysis Runtime / Coach Agent Runtime / Cloud Services；
- deterministic diagnosis 为事实源；
- CV 与 History local-first；
- SQLite 为本地 canonical store，JSONL 为交换格式；
- Web 和 Desktop 共用 versioned Runtime contracts；
- 内部预览只在受控环境发布；
- 先完成 flicking 闭环，再接 tracking 和商业化；
- Coach 的产品归属已固定；完整 Pi 源码接管是默认 runtime 方案，不要求跟随上游升级；内部预览 Go 前仍须通过 assessment/Spike 固定纳入与删改边界，并完成数据归属迁移和删除语义验证。

### 暂不固定

- Tauri 或 Electron；
- 纳入项目的 Pi 基线版本/package、源码布局，以及 sidecar/RPC 或进程内运行边界；
- sidecar 使用 HTTP、stdio、socket 或其他 IPC；
- PyInstaller 或其他 Python 打包工具；
- 正式云数据库和同步冲突算法；
- installer、签名、公证、自动更新供应链；
- Windows/macOS 的最终首发组合。

这些选型应在 Runtime 合同稳定后通过小型 spike 决定，而不是现在写入不可逆实现。

## 10. 文档关系

```text
docs/PRD.md
  → 定义产品目标、阶段和范围

docs/ARCHITECTURE.md
  → 定义系统边界、合同和演进顺序

docs/ROADMAP.md
  → 定义交付优先级、里程碑和 Go/No-Go gate

docs/PROGRESS.md
  → 记录当前执行状态和历史研发事实

specs / plans
  → 展开单个功能或实施切片

mockups / Stitch / design HTML
  → 仅作设计参考
```
