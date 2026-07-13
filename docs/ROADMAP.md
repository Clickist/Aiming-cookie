# Aiming Cookie — 产品与工程路线图

> **定位：未来顺序与放行条件。** 产品范围以 [`PRD.md`](PRD.md) 为准，系统合同以 [`ARCHITECTURE.md`](ARCHITECTURE.md) 为准，当前完成度以 [`PROGRESS.md`](PROGRESS.md) 为准。本文不维护逐日流水、commit 列表或测试数字。

## 1. 当前发布定义

当前目标不是宣称完整 v1，而是逐步达到 **可由用户实际完成的 Desktop input-native flicking 闭环**：

```text
KovaaK Run discovery / manual fallback → recoverable local analysis
→ deterministic report → optional managed video/data review
→ local history → optional Coach sidebar
```

现阶段仍为 **No-Go**。KovaaKRun ingestion、Windows Raw Input、AnalysisResult v2 与三种 mode dispatch 已形成代码基础，Tauri vertical slice 也证明桌面运行形态可行；但 input-native 仍只能作为 Preview / Experimental，正式 fair metrics、用户可达高质量前端、数据恢复合同、Windows 实机验证和发布 packaging 尚未闭合。

完整 v1 仍保留 PRD 中的登录、长期 History、Coach、通知、失败处理、导入导出、商业化和后续 tracking 路线；这些不能因当前优先级而被删除。

## 2. 当前施工优先级

### P0 — 恢复可用的 Desktop flicking 闭环

1. 先修复 Analysis/evidence correctness 阻塞，并保持 input-native Preview / Experimental；
2. 根据 [`frontend-uiux-design.md`](frontend-uiux-design.md) 与 active frontend reconstruction spec，从文档重建正式应用骨架与分析工作区；
3. 将已发现 KovaaK Run 作为新建分析的优先训练来源，并保留 MP4 + Stats 手动 fallback；
4. 建立 input-native / multimodal / video-fallback 的 versioned AnalysisResult 和 evidence provenance；
5. 让 managed video、seek、诊断和数据视图成为用户可达的增强路径，而不是输入原生基础诊断的强制前置；
6. 以 Coach 侧栏恢复分析引用与页面定位，但 Raw Input trace 不自动进入 Coach；
7. 保持 native import、managed storage、launch token、runtime lifecycle、KovaaK watcher 和 Raw Input opt-in 的既有能力；
8. Frontend reconstruction plan 已 active；Task 1 只做 prototype inventory/删除与 adapter 边界保护，可在点点明确指定 Task 和删除范围后单独执行；Task 2–7 仍遵守本节顺序与相关 Gate。

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

### P1 — 预览与发布工程

- frontend static/bundle 策略；
- Python runtime distribution；
- installer、正式 icon、签名、公证和 updater；
- supervisor/structured logs/运行指标；
- 完整 browser/Desktop E2E 与真实素材 Gate；
- Windows 生命周期与 CI（若进入首发范围）。

### P1 — 产品闭环质量

- Windows Raw Input 设置、采集状态、隐私说明和非 Windows 降级体验；
- Run 列表/详情、来源完整度、source unavailable 和“添加视频并分析”；
- 输入轨迹、事件锚点与视频视觉证据的对照视图；
- History 趋势、对比、筛选、导入导出；
- 完成通知、明确失败态、重试和恢复反馈；
- Coach 长上下文衔接与表现档案；
- 窄窗口、空状态、错误状态和无 Coach 权限状态；
- 视觉 token 在新前端中的可执行落点。

### P2 — 验证后的扩展

- 完整 tracking：在输入原生运动学基础上，完成目标/准星/误差语义和真实阈值标定后接通；
- verified auth、entitlement、LLM proxy、计量与付费；
- 云端同步和跨设备冲突策略；
- 跨平台/通用采集、手部摄像头、多游戏等远期能力。

## 3. 下一可执行切片

当前 active implementation plans：

- [`superpowers/plans/2026-07-13-reflek-capability-adoption.md`](superpowers/plans/2026-07-13-reflek-capability-adoption.md)：Analysis/evidence correctness、Run/trace 与 Coach 结构化能力；
- [`superpowers/plans/2026-07-13-frontend-product-reconstruction.md`](superpowers/plans/2026-07-13-frontend-product-reconstruction.md)：正式前端重建；边界由 active [`superpowers/specs/2026-07-13-frontend-product-reconstruction-design.md`](superpowers/specs/2026-07-13-frontend-product-reconstruction-design.md) 冻结。Task 1 已完成 prototype 删除与 adapter 边界保护，Task 2–7 尚未授权。

下一可执行切片必须遵守：

1. 若下一步是清除 prototype，由点点明确指定 frontend reconstruction **Task 1** 和具体删除范围；Task 1 只允许 inventory/删除产品 UI 与保护 adapter 边界，不开始重建；
2. Analysis/evidence correctness 继续通过 RefleK active plan 的明确 Task 处理，不混入 frontend Task；
3. Frontend Task 2–7 只有在前序 Task、相关 capability contract 和本 Roadmap Gate 允许时逐个执行；
4. 每次只执行一个被点点指定的 Task；
5. 数据可靠性问题继续使用独立 plan，不与大范围前端重建混写。

## 4. Desktop Flicking Go/No-Go Gates

### 产品闭环

- 已发现 KovaaK Run 可创建 input-native 分析；无 Raw Input 或非 Windows 时，native MP4 + Stats CSV 可创建 video-fallback 分析；
- input-native 基础运动学不要求 MP4；选择 MP4 后进入 multimodal 增强而不是另一套产品；
- processing 可离开页面，完成/失败状态可被重新找到；
- deterministic report 在无 LLM 时完整；
- 报告显示 input mode、evidence provenance 和缺失范围，不把 Raw Input 解释成目标视觉测量；
- 有 MP4 时 managed video 可播放、seek，并与诊断/数据定位联动；
- History 可查看 KovaaK Run、来源完整度和 terminal analysis；
- Coach 可选且不会阻塞免费诊断主路径。

### 数据可靠性

- `uploading/importing/running` 均有明确崩溃恢复；
- watcher 重启、重复发现和 Stats/Performance 后到补全保持幂等；
- source file moved/deleted、Raw Input 缺失和多源时间对齐失败有确定状态与 fallback；
- 删除 terminal analysis 不删除用户源文件，也不删除 Coach 历史；
- Run metadata、mouse trace、Analysis 引用的删除和 reconciliation 合同已冻结并实现；
- DB 与 workspace 删除失败后可 reconciliation；
- 低磁盘、超限、损坏文件和重复操作有确定结果；
- owner/profile 边界在 Desktop/Web 各自成立。

### 安全与运行

- Desktop API 仅 loopback，所有受保护接口验证 launch token；
- Raw Input 默认关闭、显式 opt-in，只在 KovaaK process gate 内采集相对鼠标输入；
- Raw Input trace 只保存在本地，不进入云端、Coach 请求或普通日志；
- token 不持久化、不泄露日志、不传播给无关子进程；
- shell 退出或 runtime crash 不留下孤儿进程；
- Web 预览只在可信身份边界后开放；
- secrets 与敏感路径不进入普通响应和日志。

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
