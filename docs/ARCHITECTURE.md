# Aiming Cookie — 总体架构

> **定位：稳定系统合同。** 本文展开 [`PRD.md`](PRD.md) 的产品方向，定义系统边界、数据归属、依赖方向、安全边界与演进约束。当前完成度、测试数字和日期化交接写入 [`PROGRESS.md`](PROGRESS.md)，不在本文维护。

## 1. 架构结论

Aiming Cookie 的目标形态是 **Desktop-first hybrid product**：分析与主要本地数据留在用户机器，可信身份、LLM 代理、计量和可选同步由云端承担。

```text
Desktop Client (Next.js UI in Tauri)
  ├─ Native shell / file picker / media access
  ├─ Native Input Capture (Windows Raw Input, opt-in)
  ├─ Local Analysis Runtime (Python API + worker)
  ├─ Coach Agent Runtime (Pi-based runtime + product tools)
  └─ Local canonical data and managed artifacts
             │
             └── Cloud Services
                 ├─ verified identity
                 ├─ LLM proxy and metering
                 ├─ subscription / entitlement
                 └─ optional sync
```

当前仓库也可以用 Web 方式开发和验证共享 UI/API，但 Web 验证形态不能反向改变 Desktop-first 的产品边界。

### 1.1 五个职责域

| 职责域 | 负责 | 不负责 |
|---|---|---|
| **Domain Core** | 确定性的输入原生 / 多源 / 视频 fallback 分析、指标、诊断、处方、报告模型 | HTTP、UI、身份、队列、文件生命周期 |
| **Local Analysis Runtime** | job、worker、KovaaK Run ingestion、输入时间对齐、managed workspace、本地 History、分析合同 | 产品收费、云端身份、通用 Agent 行为 |
| **Coach Agent Runtime** | 长期 Coach 关系、Agent run/event、工具编排、上下文衔接 | 重新定义确定性诊断、直接拥有 `KovaaKRun` 或分析文件 |
| **Client Surfaces** | Desktop/Web UI、交互状态、native bridge | 数据真相、业务规则、密钥保存 |
| **Cloud Services** | 可信身份、LLM 代理、计量、订阅、可选同步 | 默认执行本地 CV、成为本地 History 的隐式唯一副本 |

依赖方向应面向领域合同：UI、runtime 和 cloud 适配 Domain Core，而不是让领域逻辑依赖 FastAPI、Tauri 或具体 LLM provider。

## 2. 运行形态

### 2.1 Desktop

当前 Desktop 技术基线是 Tauri 2 + Next.js WebView + 本地 Python runtime。Tauri 管理本次启动的 Python API/worker 生命周期，并通过 native command 向前端提供动态 loopback 地址和 launch-scoped token。

这些属于已经采用的架构基线：

- 本地路径导入由 native picker 发起；源文件不被修改，运行时复制到 managed workspace；
- Desktop 可自动发现 KovaaK Stats / Performance，并将解析后的 `KovaaKRun` metadata 保存在本地 SQLite；源文件仍由用户拥有；
- Windows Raw Input 由 Tauri native layer opt-in 启用，只在检测到 KovaaK 进程时采集相对 `dx/dy`、时间戳和鼠标按钮；非 Windows 明确返回 unsupported；
- Raw Input trace、run metadata 和本地解析摘要不上传云端，也不自动进入 Coach 请求；
- Desktop 模式的本地 API 只监听 loopback，并要求本次启动 token；
- token 不持久化、不写普通日志，也不应传播给无关子进程；
- Tauri 退出时必须终止其管理的 runtime 进程树；
- Desktop UI 通过共享 versioned contracts 调用本地 runtime。

尚未稳定的打包、签名、公证、自动更新、Python distribution 和跨平台生命周期策略属于 Roadmap/后续 plan，不在本文伪装成已解决。

### 2.2 Web 验证形态

Web 形态可以运行 Next.js + FastAPI + worker + Coach sidecar，用于共享功能开发、受控预览和回归。其身份边界必须由可信反向代理或真实 session/JWT 建立；浏览器自行发送的 owner header 不能成为正式信任根。

### 2.3 当前产品合同边界

共享分析合同必须能表达三种输入模式：

- **input-native**：KovaaK Run + Raw Input，直接计算输入运动学；
- **multimodal**：input-native + MP4，用视觉证据校验和增强；
- **video-fallback**：MP4 + Stats，沿用 CV pan trajectory。

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
- cloud network / LLM / entitlement；
- auth / ownership。

用户文案、可重试性和内部诊断信息分层，密钥、token 和本地敏感路径不得出现在用户响应或普通日志中。

### 3.4 Artifact Manifest

每个 artifact 至少声明稳定 id、类型、所属 analysis、生成状态和可用性。UI 不应根据约定文件名自行寻找产物；Coach 引用也应指向稳定 artifact/analysis 标识。

### 3.5 KovaaKRun 与证据来源

`KovaaKRun` 是独立于 `Analysis Session` 的本地训练记录：

```text
KovaaKRun
  ├─ Stats / Performance source references
  ├─ parsed scenario / challenge / event summaries
  ├─ optional local mouse trace
  └─ zero or more Analysis Session references
```

约束：

- Run 没有视频也可以存在；Analysis Session 可以引用 Run，但 Run 不反向拥有 Analysis Session；
- Stats / Performance 原始文件保持用户所有，本地数据库只保存绝对路径、解析摘要和稳定 source key；Aiming Cookie 不自动复制、搬迁或删除这些源文件；
- Raw Input trace 是 Run 的本地 managed artifact，不是云端 artifact；没有有效 Performance 时间锚时不得伪造配对；
- Domain Core 必须在结果中保留 evidence provenance 和缺失范围，不能把单一来源的推断序列化成另一来源的测量；
- Run metadata、trace 的删除、源文件失效、Run 与 Analysis 的解绑和 reconciliation 必须在独立 spec 中冻结后才能作为完整用户功能放行。

## 4. 持久化与文件生命周期

### 4.1 Canonical store

- 本地 SQLite 是 Desktop v1 的 canonical structured store；
- JSON/JSONL 只作为交换、调试或兼容格式，不与 SQLite 形成双写事实源；
- 云同步是显式后续能力，不能让未完成的云模型污染本地所有权；
- migration 必须可测试、幂等，并由批准的 plan 冻结。

### 4.2 Managed workspace

每次分析拥有受控 workspace。导入必须流式/有界，检查磁盘余量，并保证路径解析后仍位于受管根目录内。

删除合同：

- 删除 terminal analysis 时删除其 managed inputs/artifacts；
- 不删除用户原始源文件；
- DB 状态与文件删除必须具备可恢复顺序（例如 commit 后清理、tombstone 或 reconciliation）；
- 崩溃后可识别和回收 orphan/partial workspace；
- KovaaKRun 的源文件可能由用户在应用外移动或删除；UI 必须表达 source unavailable，不能把路径失效当作分析成功；
- Raw Input trace 与 Run metadata 的保留、删除和孤儿清理不能从当前实现默认值推导，必须有明确合同；
- quota、TTL 和主动清理策略必须显式，不从实现默认值推导产品承诺。

### 4.3 Coach 数据归属

Coach 是用户关系层，不属于某个 analysis session：

- 用户可拥有不绑定分析的 Coach 对话，也可引用 0～N 次分析；
- 对话、关系状态和用户可见 run/event 必须归属于 Coach 层；
- 删除分析只使引用变为不可用/已删除，不级联删除已发生的对话或长期档案；
- analysis session scoped chat 只能作为迁移兼容层，不可成为新功能依赖；
- schema、summary/换窗、长期档案和 Pi session 投影若需变化，必须由 active spec/plan 单独冻结。

## 5. Coach Agent Runtime

Coach runtime 以项目内 Pi 源码基线为基础，由 Aiming Cookie 直接维护并产品化，不以持续兼容上游为约束。

边界：

- 只通过稳定产品工具读取分析、History、趋势、报告和用户确认后的操作；
- 工具调用、失败、确认和结果定位必须形成可见事件；
- 不允许通用 coding-agent 权限无边界暴露给产品用户；
- workspace、filesystem、shell、network 和 secret 权限遵循最小授权；
- 无 LLM 或 Coach 不可用时，确定性诊断闭环仍完整。

## 6. 身份与安全

- Desktop 本地数据默认属于当前 OS 用户/本地 profile；
- Windows Raw Input 默认关闭，首次启用必须有明确 opt-in 和采集范围说明；
- Raw Input 只允许 KovaaK process gate 内的相对鼠标输入；不得采集键盘、桌面绝对坐标或其它应用的后台输入；
- Raw Input trace 不进入云端、Coach 请求或普通日志；如果未来 Coach 要引用 trace，必须增加单独的用户确认和 evidence contract；
- Desktop loopback API 使用高熵、launch-scoped token，并限制 host/origin/接口暴露；
- Web 预览使用可信代理身份；正式服务使用服务端验证的 session/JWT；
- 所有 session、artifact、Coach 和 History 读写统一做 owner 校验；
- API key、refresh token、桌面 launch token 和长期 LLM 密钥不得进入分析结果、前端持久化或普通日志；
- 改变数据、付费、权限或删除状态的 Agent 操作需要明确用户确认和可审计结果。

## 7. 运行与可观测性

稳定发布基线应覆盖：

- API、worker、Coach runtime 和 Desktop child process 的生命周期管理；
- liveness/readiness；
- 带 correlation id 的 structured logs；
- queue depth、running age、failure、duration、disk usage；
- stale job、partial import、orphan workspace 和 runtime crash 的恢复；
- CV、storage、Coach、LLM、auth 错误分开统计；
- core/backend/frontend/Desktop/真实素材/E2E 的分层 Gate。

具体当前缺口与最近验证结果只写 `PROGRESS.md`。

## 8. 演进约束

顺序原则：

1. 先保证 KovaaK Run / Raw Input 可选的输入原生 flicking、MP4 + Stats fallback、确定性报告、History 和删除语义可靠；
2. 再恢复用户可达的分析工作区、训练记录选择、视频/数据联动和 Coach 侧栏；
3. 冻结并实现 source unavailable、Run/trace 删除、import/delete/runtime crash 等恢复合同；
4. 完成 Desktop packaging、Windows 实机验证、可信云服务和发布链；
5. 用视频与更多 tracking 数据增强输入原生指标，在目标/准星语义和真实阈值标定后接通完整 tracking；
6. 计费、同步、跨平台采集和远期硬件扩展在核心闭环验证后展开。

不得用 UI 重做、Desktop 壳或云同步掩盖本地数据生命周期问题；也不得让 tracking、计费或远期平台扩展阻塞当前 flicking 闭环。

## 9. 文档关系

- PRD 决定产品目标与范围；本文只展开系统合同。
- Roadmap 决定实施顺序与发布 Gate；Progress 记录当前事实。
- active spec 只冻结局部设计；active plan 才能授权 executor 修改代码。
- 代码和测试可证明“已实现什么”，不能静默修改本文定义的长期边界。
