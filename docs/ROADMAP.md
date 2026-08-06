# Aiming Cookie — 产品与工程路线图

> **定位：未来顺序与放行条件。** 产品范围以 [`PRD.md`](PRD.md) 为准，系统合同以 [`ARCHITECTURE.md`](ARCHITECTURE.md) 为准，当前完成度以 [`PROGRESS.md`](PROGRESS.md) 为准。本文不维护逐日流水、commit 列表或测试数字。

## 1. 当前发布定义

当前目标是逐步达到 **可由用户实际完成的 Desktop 完整 Coach 闭环**：

```text
process-gated Raw + KovaaK window capture → post-hoc Run finalization / selection
→ static/dynamic clicking, continuous tracking, or target-switching analysis
→ bounded evidence-backed Coach → local history, plan, and retest
```

现阶段仍为 **No-Go**。Capture Coordinator、KovaaK 窗口录制、Stats/Performance 事后 Run finalization、待分析选择与 Run-owned 存储管理已形成实现和自动化/字段验证基础；Raw Input 1000 Hz canonical 归一化已完成自动化与实测，后续数据采集核心通路未改动。当前发布仍受真实 Tauri product-path、Tracking 时延和发布工程约束。完整 Coach 的后端统一时间、场景、证据、专项 analyzer、画像/计划/复测和正式前端 Task 1–7 已形成并通过当前自动化/Focused Desktop 验证；input-native 仍只能作为 Preview / Experimental。Static、Dynamic、Tracking 与 Switching 当前各有一个 active exact scenario，但单机 NVIDIA 证据不构成 AMD/Intel 支持承诺；v1 不把未具备条件的 AMD/Intel 物理验证设为发布阻塞。

完整 v1 以 PRD 的 static/dynamic clicking、continuous tracking 与 target switching 为 launch scope；movement aiming 缺少玩家移动遥测时保持 outcome-only。产品保留 Provider-first onboarding、本地长期 History、Coach、通知、失败处理、导入导出与透明联盟商业化，不包含 Aiming Cookie 账号、登录、鉴权服务器或账号型云同步。

## 2. 当前施工优先级

### P0 — 完整 Coach launch 闭环

1. 保持已合入且已实测的自动采集、Run finalization、pending Run、Run-owned evidence 生命周期与 Raw Input 1000 Hz canonical 归一化；继续以真实 product-path 和当前声明支持的 NVIDIA 路径决定发布放行；AMD/Intel 保持未验证且不承诺支持；
2. 冻结并实现 canonical time、ScenarioProfile、私有 signal/evidence artifact、完整动作级 processed event table 与 bounded Coach evidence broker；broker 必须支持固定的精确读取、排序、筛选、聚合、共同出现、时序和反例比较，不能只暴露代表片段；
3. 建立 input-native / multimodal / video-fallback 的 versioned AnalysisResult 和 evidence provenance，并让 MP4 只在本地确定性预处理为数值证据；
4. 分别完成 static clicking、dynamic clicking、continuous tracking 与 target switching 的专项 analyzer、质量 Gate、知识/处方、画像、计划和复测；movement aiming 无移动遥测时只保留 outcome-only；
5. 已冻结的 Provider/model/auth、Training Plan、History/Analysis command、confirmation、audit/idempotency 与 Python↔Pi 边界由现有合同和回归保护；非硬阻断的额外真实数据/E2E 不再作为正式前端启动前置；
6. 让自动 MP4 成为 Run-owned 直观回放与视觉 evidence；multimodal 仍只用 Raw 计算运动学，录像失败时保留满足条件的 native 结果；
7. 在 Settings 显示分类存储占用，并允许用户分别手动管理 Run-owned 自动 MP4、Raw trace 和未完成采集数据；不启用静默自动清理；
8. 保持 native import、managed storage、launch token、runtime lifecycle、KovaaK watcher 和 Raw Input opt-in 的既有能力；Windows Steam 多库发现、50-file bound 与 launch-token descendant isolation 已完成自动化和本机 Gate；
9. 保持已完成的正式前端应用骨架、首次 Provider onboarding、分析工作区、Coach sidebar 与 Settings；继续用真实 product-path Gate 验证其消费的 capability，跳过 Provider 后没有 Coach，只有本地确定性闭环；
10. Frontend reconstruction Task 1–7 已完成并归档；后续 UI 功能或发布改动必须使用新的 active Task，不能复用已完成 plan 扩大范围，UI 仍只消费稳定 capability、不反向定义后端合同。
11. 完成有限 KovaaK 训练项目的本地手动成绩同步、去身份 Coach 摘要和现有 TeachingSession 的较低阶段→matched retest→较高阶段建议接线；本地已连接账号可供后续手动刷新，Coach 可做不持久化的单回合临时查询。前端只在新 Task 中实现 onboarding / Settings 输入和最小成绩视图，不建立独立 Benchmark 页面，也不在用户侧突出外部作者、课程代号或阶段体系。

### P0 — 冻结本地数据可靠性合同

在继续扩大功能前，分别冻结并实现：

- import 中断/崩溃后的 partial workspace 与 stale session 恢复；
- DB transaction 与文件删除的可回滚/tombstone/reconciliation 顺序；
- Desktop CSV/FOV 的单一事实源；
- KovaaKRun source unavailable、重复发现、Run/trace 删除与 Analysis 引用关系；
- Raw Input 授权、禁用、未关联 buffer 清理、trace retention 与 orphan reconciliation；
- Raw Input / Performance / Stats / MP4 的时间对齐、证据冲突和 fallback 结果；
- Capture Coordinator 的 process gate、GPU-resident 硬件编码、300 秒有界回放缓冲、延迟文件、暂停局 fail-closed、事后 Run finalization 与幂等补全；
- Run-owned 自动 MP4 / Raw trace 的 storage accounting、用户手动删除、引用失效和崩溃恢复；
- runtime crash 后的 restart、fatal-state 或重建策略；
- launch token 对子进程的环境隔离。

这些涉及状态机、安全和数据保留，必须先写 spec/plan，不能由 executor 临场决定。

### P0 — 既有后端前置硬终点（已到达；新主路径例外）

Versioned Knowledge Registry、Analysis deletion/reconciliation、Windows developer/runtime compatibility、Steam 多库 KovaaK bounded discovery 与 launch-token descendant isolation 已完成。自 2026-07-16 起，只有以下四类问题可中断正式前端施工：启动或构建阻断、核心路径不可用、数据损坏、安全泄漏。2026-07-17 新确认的统一自动采集属于“核心路径变更”，因此 Capture Coordinator / Run Finalizer 是明确例外，不代表重新开放无边界后端审计。

其他已知缺口统一记录为 deferred，不再通过新增后端审计扩大正式前端前置范围。runtime restart/fatal-state、Raw Input 前台约束和更完整的跨进程真实数据 E2E 仍是发布或对应用户路径启用前 Gate，但不阻止 Frontend Task 2 建立 token、theme 与 primitives。Raw Input 1000 Hz canonical 输出与负载已完成实测，不再列为当前缺口。

### P1 — 预览与发布工程

OpenDesign 桌面设计与 Frontend reconstruction Task 1–7 已完成，Raw Input 1000 Hz canonical 归一化及高 polling-rate 实测也已完成。发布工程继续按以下顺序推进：先完成真实 Tauri product-path、当前 NVIDIA 支持路径与 Tracking 时延 Gate；再生成真实产品截图与演示 MP4，设计和实现 Landing；最后完成 installer、版本、校验值、真实下载链接与发布验证。Worker/stale-job 恢复、Coach 对话继续/停止/失败轮次隔离均按已完成能力回归保护；OAuth/device-code 和 AMD/Intel 支持延后，不阻塞当前发布范围。Browser 通过不替代 Tauri 或 Windows release Gate。

- frontend static/bundle 策略；
- Python runtime distribution；
- installer、正式 icon、签名、公证和 updater；
- supervisor/structured logs/运行指标；
- 完整 browser/Desktop E2E 与真实素材 Gate；
- Windows 生命周期与 CI（若进入首发范围）。

### P1 — 产品闭环质量

- Windows Raw Input 设置、采集状态、隐私说明和非 Windows 降级体验；
- 自动采集待命/采集/整理/完成/失败状态与可关闭的低干扰悬浮/托盘提示；
- Run 列表/详情、待分析训练、来源完整度、source unavailable 和 Run-owned 视频回放；
- 输入轨迹、事件锚点与视频视觉证据的对照视图；
- History 待分析区、趋势、对比、筛选、导入导出；
- 完成通知、明确失败态、重试和恢复反馈；
- Coach 长上下文、表现档案、用户级产品命令、解释链与训练计划；
- 首次 Provider onboarding、完整 Provider/model/auth Settings、未配置/需重新认证/连接失败状态和 secret-safe credential 管理；
- Landing 在下载前说明 Coach 价值、Provider 成本、可选本地模式和数据边界，并提供带字幕/文字步骤的教学；
- 窄窗口、空状态、错误状态和 Provider/Coach runtime 不可用状态；
- 视觉 token 在新前端中的可执行落点。

### P2 — 验证后的扩展

- 本地档案的显式导出 / 导入、迁移与恢复，不建立账号型云同步；
- 经验证的外设目录、推荐解释、商业披露和联盟链接治理；
- 未来视觉模型、跨平台/通用采集、手部摄像头、多游戏等远期能力。

## 3. 下一可执行切片

当前 active implementation plans：

- [`superpowers/plans/2026-07-20-complete-coach-analysis-context-v1.md`](superpowers/plans/2026-07-20-complete-coach-analysis-context-v1.md)：完整 Coach 的 Run facts、processed event tables、visual producer、跨 family analyzer、Knowledge Registry v2、画像/计划/复测与 release Gate；
- [`superpowers/plans/2026-07-13-reflek-capability-adoption.md`](superpowers/plans/2026-07-13-reflek-capability-adoption.md)：Analysis/evidence correctness、Run/trace 与 Coach 结构化能力；
- [`superpowers/plans/2026-07-13-coach-productization-provider-management.md`](superpowers/plans/2026-07-13-coach-productization-provider-management.md)：解释/处方合同、input-native 核心指标、Pi provider/model/auth、用户级 Coach 命令与 Provider Settings；

下一可执行切片必须遵守：

1. RefleK Task 6A backend History/evidence read model 与 comparability 已完成；Task 6B 与正式 frontend 继续 deferred；
2. Pi coding-agent、AgentHarness/skills/prompt/filesystem harness 的上游 Windows 全仓失败不属于当前产品 Gate；若未来采纳对应 capability，必须另立 active Task；
3. Frontend reconstruction Task 1–7 已完成并归档；新的 UI、Landing 或发布工程必须先建立并授权新的 active Task，不能继续执行已归档 plan；
4. Analysis deletion/reconciliation Task 1–3 已完成并归档；terminal Analysis 的 SQLite logical delete、managed workspace cleanup 与 startup/API Gate 已闭合；
5. Windows Desktop pre-frontend Task 1–2 已完成并归档；当前没有新的已授权切片，等待点点明确指定；
6. 每次只执行一个被点点指定的 active plan Task；新增数据可靠性工作在 spec/plan 获批前不得直接施工。
7. 自动采集局部合同已由 [`superpowers/specs/2026-07-17-automatic-run-capture-design.md`](superpowers/specs/2026-07-17-automatic-run-capture-design.md) 冻结；Capture Coordinator、Run Finalizer、Run-owned evidence、pending Run readiness 与 Storage/recovery 的实施证据见 completed [`archive/completed/plans/2026-07-19-automatic-run-finalization-v1.md`](archive/completed/plans/2026-07-19-automatic-run-finalization-v1.md)。AMD/Intel 保持未验证且不在 v1 承诺支持；正式前端已实现的 capability 消费继续由合同测试和真实 product-path Gate 保护。
8. 完整 Coach spec/plan 已在采集集成、上游文档和状态索引协调后由点点批准为 active；Task 1-5 后先补 processed event table 的 Coach 消费与真实 event/segment comparison，再进入 visual producer 和其它 family analyzer；每次只执行一个被授权 Task。

## 4. Desktop Complete Coach Go/No-Go Gates

### 产品闭环

- 自动采集在 KovaaK 进程 gate 内获得 Raw 与仅 KovaaK 窗口的 300 秒硬件编码回放缓冲，并在 Stats/Performance 到达后把连续 Challenge 事后切成独立 Run；normal/timescale-only 生成永久 MP4，`Pause Count > 0` 的暂停局 fail closed；
- 单局默认选中并等待确认，多局要求选择一条；其余 Run 保留在 History 顶部待分析，不进入 Tasks、不合并、不自动删除；
- 满足 `Stats AND (MP4 OR (Raw + Performance))` 的 Run 可创建对应模式 Analysis；自动来源不足或非 Windows 时，手动 MP4 + Stats CSV 可创建 video-fallback；
- input-native 基础运动学不要求 MP4；选择 MP4 后进入 multimodal 增强而不是另一套产品；
- processing 可离开页面，完成/失败状态可被重新找到；
- deterministic report 在无 LLM 时完整；
- 报告显示 input mode、evidence provenance 和缺失范围，不把 Raw Input 解释成目标视觉测量；
- 有 MP4 时 managed video 可播放、seek，并作为直观回放/视觉 evidence 与诊断、数据和 Coach 引用联动；
- History 可查看待分析 Run、其它训练记录、来源完整度和 terminal analysis；
- 首次启动无需产品账号，先说明 Provider 的价值、第三方费用和数据边界；用户可选择、连接并测试 Provider，在 Provider 要求时完成认证，也可明确跳过进入本地分析；
- Provider 可选择、配置、连接、测试和恢复；只有要求认证的 Provider 才显示认证步骤，未配置或失败不阻塞确定性诊断；
- 第一次分析完成且 Provider 可用时自动展开 Coach；Coach 能把指标转成证据、白话解释、训练 cue、预期变化与复测，并可调用本地 profile 拥有的产品命令。
- static clicking、dynamic clicking、continuous tracking 与 target switching 各自至少有通过 ScenarioProfile、analyzer、knowledge、fixture、必要视觉质量和真实 Run Gate 的 launch scenario；movement aiming 无移动遥测时只显示 outcome-only。
- Coach 可读取 bounded L1-L3 规范化 facts/evidence/diagnosis，包括整局完整动作级 processed event table 的固定查询结果；不可读取 Raw、MP4、原始 CSV/protobuf、私有 parser payload 或未知字段。Coach 能检查支持证据和反例后独立综合候选诊断；当前 MP4 只由本地确定性预处理器消费。

### 数据可靠性

- `uploading/importing/running` 均有明确崩溃恢复；
- watcher 重启、重复发现和 Stats/Performance 后到补全保持幂等；
- source file moved/deleted、Raw Input 缺失和多源时间对齐失败有确定状态与 fallback；
- delayed Stats/Performance、窗口录制失败、切窗失败、partial finalization 和重复 watcher observation 有确定状态与幂等恢复；
- 删除 terminal analysis 不删除用户源文件，也不删除 Coach 历史；
- Run metadata、mouse trace、Analysis 引用的删除和 reconciliation 合同已冻结并实现；
- DB 与 workspace 删除失败后可 reconciliation；
- 低磁盘、超限、损坏文件和重复操作有确定结果；
- Storage 可显示总量与分类占用，用户手动移除 Run-owned evidence 时保留 Run/Analysis metadata 并正确更新 unavailable 引用；
- 本地 profile/ownership 边界在 Desktop/Web 各自成立，不依赖产品账号、session/JWT 或 entitlement。

### 安全与运行

- Desktop API 仅 loopback，所有受保护接口验证 launch token；
- Raw Input 默认关闭、显式 opt-in，只在 KovaaK process gate 内采集相对鼠标输入；
- Raw Input trace、MP4、原始 CSV/protobuf 和私有 parser payload 只留在本地，不进入 Coach 请求或普通日志；L1-L3 bounded normalized context 在用户启用 Coach 并选择 Provider 后可作为普通 Coach turn 数据；
- 自动录像只捕获 KovaaK 窗口，不捕获完整桌面、其它应用或系统通知；
- token 不持久化、不泄露日志、不传播给无关子进程；
- shell 退出或 runtime crash 不留下孤儿进程；
- Web 预览只在 VPN、SSO 或可信代理等环境访问边界后开放；该边界不是 Aiming Cookie 产品账号；
- secrets 与敏感路径不进入普通响应和日志。
- provider secret 不进入前端持久化、AnalysisResult、Coach context/message、普通响应、日志、诊断或导出；app-owned 本地 SQLite/config 可按 local-first 合同明文持久化，但 API/UI 不得读回 secret，OAuth/API key 状态必须可恢复且可审计。

### 构建与验证

- 相关 core/backend/frontend/Rust 测试通过；
- frontend production build 与 Desktop compile/test 通过；
- Windows 实机验证 Raw Input 注册、持续采集、进程 gate、启停、快照更新和退出清理；
- 真实 4K/8K 鼠标输入下验证 canonical 运动输出不超过 1000 Hz、每毫秒 X/Y 净位移与按钮边沿保持、ring buffer、snapshot I/O、内存和分析时延；
- 在当前声明支持的 NVIDIA 硬件上验证 GPU capture/encode、1080p60、CPU/GPU 占用、回放缓冲上限和硬件不可用降级；
- AMD/Intel 明确标为未验证且 v1 不承诺支持，不把当前无法执行的跨 GPU 物理验证列为 No-Go 条件；
- 至少一条真实素材端到端路径通过；
- 至少验证单局、连续多局、暂停局 fail-closed（不生成永久 MP4）、超过 300 秒拒绝、延迟 Performance、Raw-only、video-only、multimodal 和手动 fallback 真实路径；
- browser 与 Desktop 的关键交互有自动化或明确手工 Gate；
- installer/签名/公证/更新达到目标平台的发布要求。

未满足任一 P0 Gate 时，不把 vertical slice 描述为可发布产品。

## 5. 维护规则

- Roadmap 只写未来优先级、里程碑和 Gate；完成后把结果写入 Progress，并将 plan 归档。
- 日期只有在存在真实外部承诺时才进入 Roadmap；探索性内部目标优先写顺序和 Gate。
- 新工作必须先核对 PRD/Architecture；发生产品冲突时先更新 PRD，而不是在 Roadmap 绕过。
- active plan/Task 才能授权 executor；Roadmap 条目本身不是实施合同。
