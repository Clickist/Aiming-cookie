# Aiming Cookie — 产品与工程路线图

> **定位：未来顺序与放行条件。** 产品范围以 [`PRD.md`](PRD.md) 为准，系统合同以 [`ARCHITECTURE.md`](ARCHITECTURE.md) 为准，当前完成度以 [`PROGRESS.md`](PROGRESS.md) 为准。本文不维护逐日流水、commit 列表或测试数字。

## 1. 当前发布定义

当前目标不是宣称完整 v1，而是逐步达到 **可由用户实际完成的 Desktop input-native flicking 闭环**：

```text
KovaaK Run discovery / compatibility fallback → recoverable input-native analysis
→ evidence-backed explanation / prescription → optional MP4 visual review
→ local history → provider-configured Coach actions
```

现阶段仍为 **No-Go**。KovaaKRun ingestion、Windows Raw Input、AnalysisResult v2 与三种 mode dispatch 已形成代码基础，Tauri vertical slice 也证明桌面运行形态可行；但 input-native 仍只能作为 Preview / Experimental，正式 fair metrics、用户可达高质量前端、数据恢复合同、Windows 实机验证和发布 packaging 尚未闭合。

完整 v1 保留 PRD 中的 Provider-first onboarding、本地长期 History、Coach、通知、失败处理、导入导出、透明联盟商业化和后续 tracking 路线；产品不再包含 Aiming Cookie 账号、登录、鉴权服务器或账号型云同步。

## 2. 当前施工优先级

### P0 — 恢复可用的 Desktop flicking 闭环

1. 先修复 Analysis/evidence correctness 阻塞，并保持 input-native Preview / Experimental；
2. 将已发现 KovaaK Run + Raw Input 作为新建分析的主训练来源；MP4 主要用于回放/视觉 evidence，同时保留 MP4 + Stats compatibility fallback；
3. 建立 input-native / multimodal / video-fallback 的 versioned AnalysisResult 和 evidence provenance；
4. 完成 flick segmentation、核心 fair metrics、claim level、白话解释、训练 cue、预期变化与复测合同；
5. 闭合 Provider/model/auth、Training Plan、History/Analysis command、confirmation、audit/idempotency 与 Python↔Pi sidecar 的剩余真实数据/E2E，不用前端补齐语义；
6. 让 managed video/seek 成为可选直观回放与视觉 evidence，而不是输入原生基础诊断的强制前置；
7. 保持 native import、managed storage、launch token、runtime lifecycle、KovaaK watcher 和 Raw Input opt-in 的既有能力，并通过 Desktop/runtime 与 Windows 实机 Gate；
8. 上述后端合同和真实 E2E 稳定后，才根据 [`frontend-uiux-design.md`](frontend-uiux-design.md) 与 active frontend reconstruction spec 重建正式应用骨架、首次 Provider onboarding、分析工作区和 Coach sidebar；连接 Provider 可明确跳过，不建立产品账号或鉴权服务器；
9. Frontend reconstruction plan 虽已 active，但除已完成的 Task 1 外，Task 2–7 暂后置到后端前置 Gate 闭合并由点点重新指定，不以 UI 反向定义后端合同。

### P0 — 冻结本地数据可靠性合同

在继续扩大功能前，分别冻结并实现：

- import 中断/崩溃后的 partial workspace 与 stale session 恢复；
- DB transaction 与文件删除的可回滚/tombstone/reconciliation 顺序；
- Desktop CSV/FOV 的单一事实源；
- KovaaKRun source unavailable、重复发现、Run/trace 删除与 Analysis 引用关系；
- Raw Input 授权、禁用、未关联 buffer 清理、trace retention 与 orphan reconciliation；
- Raw Input / Performance / Stats / MP4 的时间对齐、证据冲突和 fallback 结果；
- runtime crash 后的 restart、fatal-state 或重建策略；
- launch token 对子进程的环境隔离。

这些涉及状态机、安全和数据保留，必须先写 spec/plan，不能由 executor 临场决定。

### P0 — 正式前端前的 Coach 后端 Gate

Versioned Knowledge Registry、source/claim/limitation 验收和无 bridge knowledge tool 已完成。正式 frontend reconstruction Task 2–7 开始前，剩余至少完成：

- 现有 Analysis/History/Run/Training Plan/Provider 命令的 owner、confirmation、audit/idempotency 和 unavailable/deleted 语义稳定；
- Python↔Pi sidecar、SQLite restart/replay、真实 Analysis/History/Training Plan 数据与 secret/path/raw-payload sentinel E2E 通过；
- Windows Raw Input、高 polling-rate、KovaaK 文件发现与 Desktop runtime 关键 Gate 已有明确结论。

这些 Gate 未完成时，Frontend Task 2–7 保持后置；允许只维护 capability adapter 和测试，不实现依赖未冻结语义的正式页面。

### P1 — 预览与发布工程

- frontend static/bundle 策略；
- Python runtime distribution；
- installer、正式 icon、签名、公证和 updater；
- supervisor/structured logs/运行指标；
- 完整 browser/Desktop E2E 与真实素材 Gate；
- Windows 生命周期与 CI（若进入首发范围）。

### P1 — 产品闭环质量

- Windows Raw Input 设置、采集状态、隐私说明和非 Windows 降级体验；
- Run 列表/详情、来源完整度、source unavailable 和“添加可选视频回放”；
- 输入轨迹、事件锚点与视频视觉证据的对照视图；
- History 趋势、对比、筛选、导入导出；
- 完成通知、明确失败态、重试和恢复反馈；
- Coach 长上下文、表现档案、用户级产品命令、解释链与训练计划；
- 首次 Provider onboarding、完整 Provider/model/auth Settings、未配置/需重新认证/连接失败状态和 secret-safe credential 管理；
- Landing 在下载前说明 Coach 价值、Provider 成本、可选本地模式和数据边界，并提供带字幕/文字步骤的教学；
- 窄窗口、空状态、错误状态和 Provider/Coach runtime 不可用状态；
- 视觉 token 在新前端中的可执行落点。

### P2 — 验证后的扩展

- 完整 tracking：在输入原生运动学基础上，完成目标/准星/误差语义和真实阈值标定后接通；
- 本地档案的显式导出 / 导入、迁移与恢复，不建立账号型云同步；
- 经验证的外设目录、推荐解释、商业披露和联盟链接治理；
- 跨平台/通用采集、手部摄像头、多游戏等远期能力。

## 3. 下一可执行切片

当前 active implementation plans：

- [`superpowers/plans/2026-07-13-reflek-capability-adoption.md`](superpowers/plans/2026-07-13-reflek-capability-adoption.md)：Analysis/evidence correctness、Run/trace 与 Coach 结构化能力；
- [`superpowers/plans/2026-07-13-coach-productization-provider-management.md`](superpowers/plans/2026-07-13-coach-productization-provider-management.md)：解释/处方合同、input-native 核心指标、Pi provider/model/auth、用户级 Coach 命令与 Provider Settings；
- [`superpowers/plans/2026-07-13-frontend-product-reconstruction.md`](superpowers/plans/2026-07-13-frontend-product-reconstruction.md)：正式前端重建；边界由 active [`superpowers/specs/2026-07-13-frontend-product-reconstruction-design.md`](superpowers/specs/2026-07-13-frontend-product-reconstruction-design.md) 冻结。Task 1 已完成 prototype 删除与 adapter 边界保护，Task 2–7 尚未授权。

下一可执行切片必须遵守：

1. Coach productization Task 1–5 与 Versioned Knowledge Registry Task 1–6 已完成；继续按 active plan 处理 input-native 核心指标、Analysis/evidence correctness 和本地数据可靠性，不用 MP4、LLM 或 UI 掩盖算法与状态机缺口；
2. 补齐 Knowledge E2E 之外的 Python↔Pi、SQLite restart/replay、真实 History/Training Plan 数据和 Desktop/runtime/Windows Gate，形成不依赖正式前端的验收证据；
3. Coach productization Task 6 与 Frontend Task 2–7 整体最后处理，只有在后端合同稳定、相关 Gate 通过并由点点重新指定后才逐个执行；
4. 每次只执行一个被点点指定的 active plan Task；新增数据可靠性工作在 spec/plan 获批前不得直接施工。

## 4. Desktop Flicking Go/No-Go Gates

### 产品闭环

- 已发现 KovaaK Run 可创建 input-native 分析；无 Raw Input 或非 Windows 时，native MP4 + Stats CSV 可创建 video-fallback 分析；
- input-native 基础运动学不要求 MP4；选择 MP4 后进入 multimodal 增强而不是另一套产品；
- processing 可离开页面，完成/失败状态可被重新找到；
- deterministic report 在无 LLM 时完整；
- 报告显示 input mode、evidence provenance 和缺失范围，不把 Raw Input 解释成目标视觉测量；
- 有 MP4 时 managed video 可播放、seek，并作为直观回放/视觉 evidence 与诊断、数据和 Coach 引用联动；
- History 可查看 KovaaK Run、来源完整度和 terminal analysis；
- 首次启动无需产品账号，先说明 Provider 的价值、第三方费用和数据边界；用户可选择、连接并测试 Provider，在 Provider 要求时完成认证，也可明确跳过进入本地分析；
- Provider 可选择、配置、连接、测试和恢复；只有要求认证的 Provider 才显示认证步骤，未配置或失败不阻塞确定性诊断；
- 第一次分析完成且 Provider 可用时自动展开 Coach；Coach 能把指标转成证据、白话解释、训练 cue、预期变化与复测，并可调用本地 profile 拥有的产品命令。

### 数据可靠性

- `uploading/importing/running` 均有明确崩溃恢复；
- watcher 重启、重复发现和 Stats/Performance 后到补全保持幂等；
- source file moved/deleted、Raw Input 缺失和多源时间对齐失败有确定状态与 fallback；
- 删除 terminal analysis 不删除用户源文件，也不删除 Coach 历史；
- Run metadata、mouse trace、Analysis 引用的删除和 reconciliation 合同已冻结并实现；
- DB 与 workspace 删除失败后可 reconciliation；
- 低磁盘、超限、损坏文件和重复操作有确定结果；
- 本地 profile/ownership 边界在 Desktop/Web 各自成立，不依赖产品账号、session/JWT 或 entitlement。

### 安全与运行

- Desktop API 仅 loopback，所有受保护接口验证 launch token；
- Raw Input 默认关闭、显式 opt-in，只在 KovaaK process gate 内采集相对鼠标输入；
- Raw Input trace 只保存在本地，不进入云端、Coach 请求或普通日志；
- token 不持久化、不泄露日志、不传播给无关子进程；
- shell 退出或 runtime crash 不留下孤儿进程；
- Web 预览只在 VPN、SSO 或可信代理等环境访问边界后开放；该边界不是 Aiming Cookie 产品账号；
- secrets 与敏感路径不进入普通响应和日志。
- provider secret 不进入前端持久化、AnalysisResult、Coach context/message、普通响应、日志、诊断或导出；app-owned 本地 SQLite/config 可按 local-first 合同明文持久化，但 API/UI 不得读回 secret，OAuth/API key 状态必须可恢复且可审计。

### 构建与验证

- 相关 core/backend/frontend/Rust 测试通过；
- frontend production build 与 Desktop compile/test 通过；
- Windows 实机验证 Raw Input 注册、持续采集、进程 gate、启停、快照更新和退出清理；
- 高 polling-rate 鼠标下验证 ring buffer、snapshot I/O、内存和分析时延；
- 至少一条真实素材端到端路径通过；
- 至少各一条 input-native、multimodal 和 video-fallback 真实 Run 路径通过；
- browser 与 Desktop 的关键交互有自动化或明确手工 Gate；
- installer/签名/公证/更新达到目标平台的发布要求。

未满足任一 P0 Gate 时，不把 vertical slice 描述为可发布产品。

## 5. 维护规则

- Roadmap 只写未来优先级、里程碑和 Gate；完成后把结果写入 Progress，并将 plan 归档。
- 日期只有在存在真实外部承诺时才进入 Roadmap；探索性内部目标优先写顺序和 Gate。
- 新工作必须先核对 PRD/Architecture；发生产品冲突时先更新 PRD，而不是在 Roadmap 绕过。
- active plan/Task 才能授权 executor；Roadmap 条目本身不是实施合同。
