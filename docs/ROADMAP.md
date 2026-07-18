# Aiming Cookie — 产品与工程路线图

> **定位：未来顺序与放行条件。** 产品范围以 [`PRD.md`](PRD.md) 为准，系统合同以 [`ARCHITECTURE.md`](ARCHITECTURE.md) 为准，当前完成度以 [`PROGRESS.md`](PROGRESS.md) 为准。本文不维护逐日流水、commit 列表或测试数字。

## 1. 当前发布定义

当前目标不是宣称完整 v1，而是逐步达到 **可由用户实际完成的 Desktop input-native flicking 闭环**：

```text
process-gated Raw + KovaaK window capture → post-hoc Run finalization / selection
→ recoverable input-native or video-fallback analysis → evidence-backed explanation / prescription
→ local history → provider-configured Coach actions
```

现阶段仍为 **No-Go**。KovaaKRun ingestion、Windows Raw Input、AnalysisResult v2 与三种 mode dispatch 已形成代码基础，Tauri vertical slice 也证明桌面运行形态可行；但统一 Capture Coordinator、KovaaK 窗口录制、Stats/Performance 事后 Run finalization、待分析选择和 Run-owned 存储管理尚未实现，input-native 也仍只能作为 Preview / Experimental。

完整 v1 保留 PRD 中的 Provider-first onboarding、本地长期 History、Coach、通知、失败处理、导入导出、透明联盟商业化和后续 tracking 路线；产品不再包含 Aiming Cookie 账号、登录、鉴权服务器或账号型云同步。

## 2. 当前施工优先级

### P0 — 恢复可用的 Desktop flicking 闭环

1. 先修复 Analysis/evidence correctness 阻塞，并保持 input-native Preview / Experimental；
2. 将自动采集的 Raw + 仅 KovaaK 窗口录像与 Stats/Performance 事后 Run finalization 作为 Desktop 主训练来源；单局确认、多局选一条，其余保留待分析；手动 MP4 + Stats 作为独立 fallback；
3. 建立 input-native / multimodal / video-fallback 的 versioned AnalysisResult 和 evidence provenance；
4. 完成 flick segmentation、核心 fair metrics、claim level、白话解释、训练 cue、预期变化与复测合同；
5. 已冻结的 Provider/model/auth、Training Plan、History/Analysis command、confirmation、audit/idempotency 与 Python↔Pi 边界由现有合同和回归保护；非硬阻断的额外真实数据/E2E 不再作为正式前端启动前置；
6. 让自动 MP4 成为 Run-owned 直观回放与视觉 evidence；multimodal 仍只用 Raw 计算运动学，录像失败时保留满足条件的 native 结果；
7. 在 Settings 显示分类存储占用，并允许用户分别手动管理 Run-owned 自动 MP4、Raw trace 和未完成采集数据；不启用静默自动清理；
8. 保持 native import、managed storage、launch token、runtime lifecycle、KovaaK watcher 和 Raw Input opt-in 的既有能力；Windows Steam 多库发现、50-file bound 与 launch-token descendant isolation 已完成自动化和本机 Gate；
9. 正式前端后续根据 [`frontend-uiux-design.md`](frontend-uiux-design.md) 与 active frontend reconstruction spec 重建应用骨架、首次 Provider onboarding、分析工作区和 Coach sidebar；跳过 Provider 后没有 Coach，只有本地确定性闭环；
10. Frontend reconstruction Task 1 已完成；Task 2–7 仍须由点点明确指定具体 Task 后才能执行，UI 只消费稳定 capability，不反向定义后端合同。

### P0 — 冻结本地数据可靠性合同

在继续扩大功能前，分别冻结并实现：

- import 中断/崩溃后的 partial workspace 与 stale session 恢复；
- DB transaction 与文件删除的可回滚/tombstone/reconciliation 顺序；
- Desktop CSV/FOV 的单一事实源；
- KovaaKRun source unavailable、重复发现、Run/trace 删除与 Analysis 引用关系；
- Raw Input 授权、禁用、未关联 buffer 清理、trace retention 与 orphan reconciliation；
- Raw Input / Performance / Stats / MP4 的时间对齐、证据冲突和 fallback 结果；
- Capture Coordinator 的 process gate、窗口录制、分段数据、延迟文件、事后 Run finalization 与幂等补全；
- Run-owned 自动 MP4 / Raw trace 的 storage accounting、用户手动删除、引用失效和崩溃恢复；
- runtime crash 后的 restart、fatal-state 或重建策略；
- launch token 对子进程的环境隔离。

这些涉及状态机、安全和数据保留，必须先写 spec/plan，不能由 executor 临场决定。

### P0 — 既有后端前置硬终点（已到达；新主路径例外）

Versioned Knowledge Registry、Analysis deletion/reconciliation、Windows developer/runtime compatibility、Steam 多库 KovaaK bounded discovery 与 launch-token descendant isolation 已完成。自 2026-07-16 起，只有以下四类问题可中断正式前端施工：启动或构建阻断、核心路径不可用、数据损坏、安全泄漏。2026-07-17 新确认的统一自动采集属于“核心路径变更”，因此 Capture Coordinator / Run Finalizer 是明确例外，不代表重新开放无边界后端审计。

其他已知缺口统一记录为 deferred，不再通过新增后端审计扩大正式前端前置范围。runtime restart/fatal-state、真实设备高 polling-rate、Raw Input 前台约束和更完整的跨进程真实数据 E2E 仍是发布或对应用户路径启用前 Gate，但不阻止 Frontend Task 2 建立 token、theme 与 primitives。

### P1 — 预览与发布工程

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

- 完整 tracking：在输入原生运动学基础上，完成目标/准星/误差语义和真实阈值标定后接通；
- 本地档案的显式导出 / 导入、迁移与恢复，不建立账号型云同步；
- 经验证的外设目录、推荐解释、商业披露和联盟链接治理；
- 跨平台/通用采集、手部摄像头、多游戏等远期能力。

## 3. 下一可执行切片

当前 active implementation plans：

- [`superpowers/plans/2026-07-13-reflek-capability-adoption.md`](superpowers/plans/2026-07-13-reflek-capability-adoption.md)：Analysis/evidence correctness、Run/trace 与 Coach 结构化能力；
- [`superpowers/plans/2026-07-13-coach-productization-provider-management.md`](superpowers/plans/2026-07-13-coach-productization-provider-management.md)：解释/处方合同、input-native 核心指标、Pi provider/model/auth、用户级 Coach 命令与 Provider Settings；
- [`superpowers/plans/2026-07-13-frontend-product-reconstruction.md`](superpowers/plans/2026-07-13-frontend-product-reconstruction.md)：正式前端重建；边界由 active [`superpowers/specs/2026-07-13-frontend-product-reconstruction-design.md`](superpowers/specs/2026-07-13-frontend-product-reconstruction-design.md) 冻结。Task 1 已完成，Task 2–7 尚未获得具体 Task 授权。

下一可执行切片必须遵守：

1. RefleK Task 6A backend History/evidence read model 与 comparability 已完成；Task 6B 与正式 frontend 继续 deferred；
2. Pi coding-agent、AgentHarness/skills/prompt/filesystem harness 的上游 Windows 全仓失败不属于当前产品 Gate；若未来采纳对应 capability，必须另立 active Task；
3. Frontend Task 2–7 尚未授权；获得具体 Task 授权后继续遵守 active plan 的顺序、Allowed files、Tests first 与 Stop rule；
4. Analysis deletion/reconciliation Task 1–3 已完成并归档；terminal Analysis 的 SQLite logical delete、managed workspace cleanup 与 startup/API Gate 已闭合；
5. Windows Desktop pre-frontend Task 1–2 已完成并归档；当前没有新的已授权切片，等待点点明确指定；
6. 每次只执行一个被点点指定的 active plan Task；新增数据可靠性工作在 spec/plan 获批前不得直接施工。
7. 自动采集局部合同已由 [`superpowers/specs/2026-07-17-automatic-run-capture-design.md`](superpowers/specs/2026-07-17-automatic-run-capture-design.md) 冻结，但没有 implementation plan；Frontend Task 2 可独立建立 tokens/primitives，Task 3/4/6 不得伪造 Capture Coordinator、pending Run readiness 或 Storage 删除 capability。

## 4. Desktop Flicking Go/No-Go Gates

### 产品闭环

- 自动采集在 KovaaK 进程 gate 内获得 Raw 与仅 KovaaK 窗口录像，并在 Stats/Performance 到达后把连续 Challenge 事后切成独立 Run；
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

### 数据可靠性

- `uploading/importing/running` 均有明确崩溃恢复；
- watcher 重启、重复发现和 Stats/Performance 后到补全保持幂等；
- source file moved/deleted、Raw Input 缺失和多源时间对齐失败有确定状态与 fallback；
- delayed Stats/Performance、窗口录制失败、切窗失败、partial finalization 和重复 watcher observation 有确定状态与幂等恢复；
- 删除 terminal analysis 不删除用户源文件，也不删除 Coach 历史；
- terminal Analysis 的 logical delete、Analysis-owned managed workspace cleanup 与 startup reconciliation 已实现；Run metadata、Run-owned Raw / MP4 删除、引用失效与精确 reconciliation 尚未实现；
- DB 与 workspace 删除失败后可 reconciliation；
- 低磁盘、超限、损坏文件和重复操作有确定结果；
- Storage 可显示总量与分类占用，用户手动移除 Run-owned evidence 时保留 Run/Analysis metadata 并正确更新 unavailable 引用；
- 本地 profile/ownership 边界在 Desktop/Web 各自成立，不依赖产品账号、session/JWT 或 entitlement。

### 安全与运行

- Desktop API 仅 loopback，所有受保护接口验证 launch token；
- Raw Input 默认关闭、显式 opt-in，只在 KovaaK process gate 内采集相对鼠标输入；
- Raw Input trace 只保存在本地，不进入云端、Coach 请求或普通日志；
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
- 高 polling-rate 鼠标下验证 ring buffer、snapshot I/O、内存和分析时延；
- 至少一条真实素材端到端路径通过；
- 至少验证单局、连续多局、延迟 Performance、Raw-only、video-only、multimodal 和手动 fallback 真实路径；
- browser 与 Desktop 的关键交互有自动化或明确手工 Gate；
- installer/签名/公证/更新达到目标平台的发布要求。

未满足任一 P0 Gate 时，不把 vertical slice 描述为可发布产品。

## 5. 维护规则

- Roadmap 只写未来优先级、里程碑和 Gate；完成后把结果写入 Progress，并将 plan 归档。
- 日期只有在存在真实外部承诺时才进入 Roadmap；探索性内部目标优先写顺序和 Gate。
- 新工作必须先核对 PRD/Architecture；发生产品冲突时先更新 PRD，而不是在 Roadmap 绕过。
- active plan/Task 才能授权 executor；Roadmap 条目本身不是实施合同。
