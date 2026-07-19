# Aiming Cookie 当前进度

> **最后整理：2026-07-20。** 本文是当前快照，不是产品或架构事实源。详细研发流水见 [`archive/history/`](archive/history/)；产品、架构与 UI/UX 结论分别以 [`PRD.md`](PRD.md)、[`ARCHITECTURE.md`](ARCHITECTURE.md) 和 [`frontend-uiux-design.md`](frontend-uiux-design.md) 为准。

## 1. 当前结论

- 产品方向是 Desktop-first local-first、确定性诊断主路径；已确定的 launch scope 为 static/dynamic clicking、continuous tracking 与 target switching，movement aiming 缺少玩家移动遥测时保持 outcome-only。Aiming Cookie 开源免费，不提供产品账号、登录或用户鉴权服务器；Coach 是 Provider 可用时的长期关系与产品操作层。pinned Pi built-in provider/model catalog 已确认为完整产品 catalog，不另设 Aiming Cookie allow-list。
- KovaaK Run 自动发现、Stats / Performance 解析、Windows Raw Input、AnalysisResult v2、History read model、Coach diagnostic context 与本地 Benchmark store 已形成不同成熟度的代码基础。
- RefleK active plan 的 Task 2–6A 已完成当前平台收口：v2 producer 与 frozen source identity fail-closed；三种 worker mode 已正式验收，managed MP4 在外部副作用前做 pre/post/final revision 校验；Coach diagnostic context 由单一 allow-list projector 生成，并在 Python/Pi/tool/store/API 边界重复校验；History/Run 使用轻列表与 lazy detail，并以严格 identity/coverage/alignment Gate 生成可比趋势。input-native 仍受真实 Windows、高 polling-rate、噪声地板与阈值校准 Gate 约束。
- **input-native 当前仍只能作为 Preview / Experimental 能力。** Raw Input 左键锚定 Flick、真实毫秒 timing、raw-count path/efficiency、修正/反向离散事实、trough-depth submovement proxy、SPARC 均匀重采样与 cutoff-normalized frequency axis、分布/outlier refs 与 deterministic diagnosis 已接入 worker 和 v2；native 已补齐 `submovement_overlap` 兼容键但明确携带非 temporal-overlap limitation；SPARC 已使用独立 v2 metric version，旧版值不参与同一趋势，v2 在真实数据校准前不套用 legacy 绝对阈值；但 Windows 实机、高 polling-rate 实物、噪声地板、SPARC 跨 polling 可比性和 target-relative facts 仍未闭合，不得描述为发布完成。
- multimodal 冻结为“native 结果是主事实、MP4 主要提供直观回放、问题定位和可验证视觉证据”；视觉阶段复用 native 阶段已冻结并解析的 Stats，不再按用户源路径重读另一 revision；视觉失败时保留 native 结果并显示视觉证据不可用。video-fallback 继续作为 compatibility path，不是长期主分析方向。
- Benchmark 后端能力保留，但不进入 v1 正式前端、不进入默认 Coach context、不启用在线 provider 或 leaderboard。
- Frontend reconstruction Task 1 已在点点确认精确范围后删除 History / Run / Evidence prototype、临时 App shell 与旧全局样式；当前只保留 capability adapters 与 Tauri/runtime，正式产品路由暂时不可用。
- 当前发布状态仍为 **No-Go**。
- Versioned Coach Knowledge Registry 已完成：Flicking、Tracking、身体/张力候选解释、settings 单变量实验、practice、verification 与 limitations 已迁入单一 canonical asset；Python/TypeScript 共用确定性检索，Draft 2020-12 `schema.v1.json` 已补齐完整 entry/source/claim structural contract 并对 canonical asset 做标准验证，Pi knowledge tool 与 product-command bridge 已解耦，历史 trace 只保存版本化引用。Windows developer/runtime compatibility 的 Task 1–2 自动化 Gate 已完成；正式前端仍按点点裁决最后处理。
- 2026-07-15 Windows 实机已闭合四个可重复开发阻断：Node loader 改用 file URL，Python subprocess 固定 pinned Pi source/tsconfig，knowledge parity 按平台选择 venv Python，Tauri 补齐由 tracked PNG 机械生成的 compile-time ICO。Python、Coach/Pi source、Pi AI、frontend adapters 与 Tauri MSVC 自动化 Gate 均通过；正式 frontend route、GUI、真实 KovaaK、Raw Input 与高 polling-rate 仍未闭合，因此发布状态保持 **No-Go**。
- 2026-07-16 Windows Desktop pre-frontend Task 1–2 已完成：无 override 时从 registry + `libraryfolders.vdf` + exact app manifest fail-closed 发现 E 盘 KovaaK，多安装要求显式消歧；每个 Stats/Performance 目录只发射最新 50 个 supported files；Coach fallback Node child 不再继承 Desktop launch token。点点已设后端前置硬终点，非启动/构建、核心路径、数据损坏或安全泄漏问题统一 deferred；正式前端具体 Task 尚未获得授权。
- 2026-07-20 自动采集、Capture Coordinator、Run Finalizer、pending Run readiness、Run-owned Raw/MP4 lifecycle 与单证据 Storage removal 已合入当前代码基线并有自动化和 NVIDIA 字段证据；真实 Tauri product-path、AMD/Intel 物理硬件与完整 release Gate 仍未闭合，发布保持 **No-Go**。完整 Coach 的 canonical time、ScenarioProfile、bounded evidence broker、跨 family analyzer、画像/计划/复测与正式前端仍未实现，不得把它们写成当前可用。
- 2026-07-20 Complete Coach spec/plan 已在采集合入与上游文档协调后激活，后续从 Task 1 canonical time correctness 开始；激活只提供施工合同，不代表功能已实现。集成回归为 Python `820 passed, 3 skipped`，MSVC Rust `68 passed, 7 ignored`，`fmt/check/clippy` 与 Python compileall 通过；AMD/Intel 物理硬件及完整 Coach release Gate 仍未闭合。
- 2026-07-18 WGC Task 2 已通过 Windows MSVC 离线编译门槛与真实 KovaaK 窗口 smoke：同一 `HWND=0xC2081A`、`PID=23148`、`UnrealWindow` 在三种显示模式均持续出帧且 `SystemRelativeTime` 回退为 `0`；`Full screen windowed` 为 5 秒 `824` 帧、`1920x1080`，`Full screen` 为 `825` 帧、`1920x1080`，`Windowed` 为 `825` 帧、`1922x1112`（包含窗口非客户区）。Raw Input/ACRI v1 与产品时间轴未改变。窗口枚举在独占全屏/桌面隔离下使用只读提升权限完成，不代表产品运行时需要提升权限。
- 2026-07-18 WGC Task 3 已形成可运行的 CPU-backed Media Foundation H.264 recorder：重路径在 GPU readback 前固定限到 `60 FPS`，writer 使用容量 `4` 的 `try_send` 队列和硬件 transform preference；真实 `Full screen windowed` 5 秒 smoke 收到 `825` 个 WGC 元数据帧、向 writer 提交 `276` 帧、writer 丢帧 `0`、encoder error `0`。FFprobe 验证输出为 H.264/yuv420p、`1920x1080`、恒定 `60 FPS`、`301` 帧、`5.01665s`，抽帧确认画面清晰且只包含 KovaaK。当前 CPU readback 约占 `1.44` 个 CPU 核，尚未通过性能 Gate；自动降到 `30 FPS` 尚未实现，产品默认按点点裁决保持 `60 FPS`。
- 2026-07-18 点点冻结自动视频路线为 GPU-resident hardware encode + encoded-packet replay buffer：KovaaK 进程存在时持续维护最近 `300 秒`墙上时间的有界缓冲，Stats / Performance 到达后才为 pause-free normal/timescale-only Challenge 生成永久 Run MP4；`Pause Count > 0` 的暂停局按 2026-07-19 裁决 fail closed。CPU-backed writer 只保留为功能/性能基线，不再作为发布主路径。
- 2026-07-19 Hardware Replay Buffer Task 4 在用户开始 Challenge 前触发 Stop rule：真实 KovaaK 空闲窗口的目标提交节拍为 `60.000 FPS`，但硬件 MFT 仅接受约 `46.347 FPS`，`148.683s` 内出现 `2030` 次 backpressure drop；最近 5 秒 replay snapshot 明确返回 `coverageGap`。Raw ACRI v1 为 `1360` points、`0` drop、`0` snapshot failure。普通局、timescale、暂停、Restart、超 300 秒、finalization 后继续运行和 adapter failure 矩阵均未继续执行；发布状态保持 **No-Go**。
- 2026-07-19 经点点授权的 hardware replay backpressure repair Gate 已通过：worker 只在 MFT `NeedInput` permit 存在时取一帧，编码 PTS 使用稳定 60 Hz derived timeline，WGC source timestamp 保持不变。真实 KovaaK 空闲窗口约 `142.849s` 产出 `8572` packets（约 `60.007 FPS`）、`0` packet drop、`0` encoder error；最近 10 秒 replay export 成功且 capture 在导出后继续运行，Raw ACRI v1 为 `143` points、`0` drop、`0` snapshot failure。该结果只解除 backpressure blocker；Task 4 的 Challenge、暂停、Restart、超 300 秒和失败路径矩阵仍未执行，发布状态保持 **No-Go**。
- 2026-07-19 Hardware Replay Buffer Task 4 normal Challenge 行已通过：Stats `Challenge Start 13:03:36.667` 与 Performance `challenge_start_utc` 同秒一致，Performance 最末事件解析出 `59.905s` canonical window；对应 replay MP4 为 H.264 Constrained Baseline `1920x1080`、约 `60 FPS`、visible duration `59.905s`、`0` reencoded frame。完整 capture 为 `20219` packets、`0` packet drop、`0` encoder error，Raw canonical window 为 `40644` points、`0` drop；首帧是 Challenge `0.00` 转场，末帧仍在场内约剩 `0.11s`。timescale、暂停、Restart、adapter failure 与 AMD/Intel Gate 仍未完成，发布状态保持 **No-Go**。
- 2026-07-19 Task 4 timescale-extended Challenge 行已通过：`1wall5targets_pasu` Performance 为 `time_limit=60`、`timescale≈0.7`，末事件解析出 `85.694s` canonical window；对应 MP4 为 `1920x1080`、约 `60 FPS`、visible duration `85.694s`、`0` reencoded frame。完整 capture 为 `19414` packets、`0` packet drop、`0` encoder error，Raw canonical window 为 `78733` points、`0` drop；CPU 约 `0.0589` core、peak working set `224.83 MiB`、GPU Video Encode 平均 `15.6743%`。暂停、Restart、真实 adapter failure 与 AMD/Intel Gate 仍未完成，发布状态保持 **No-Go**。
- 2026-07-19 Task 4 short-pause 行触发 Stop rule：Stats 为 `Pause Count=1`、`Pause Duration=6`，但 Performance 最末事件仍为 `59.944s`，证明 event timestamp 不包含暂停 wall time，与当前 `time_alignment.v2` “event 已含 pause”假设冲突。按当前 v2 切出的末帧仍余 `6.57s`；简单加整数 `6s` 后仍余 `0.56s`，说明 Stats pause duration 的整数粒度不足以恢复毫秒级 canonical end。本场 capture/Raw 均 `0` drop，但没有可接受的 canonical MP4；Restart 与后续矩阵未继续，发布状态保持 **No-Go**。
- 2026-07-19 pause-aware time alignment assessment 已完成且未实施 repair：Performance wire 只有 active timestamp + `pauseCount=1`，没有 resume/duration/未知精确字段；Stats 原始 `Pause Duration` 确为整数 `6`。Stats/Performance kill count 可完整累计配对并证明 pause 前后 wall-active 偏移跳变约 `6.566s`，但 Performance 是约一秒聚合，不能给出精确毫秒值；Stats 文件 mtime 在 normal/timescale 只晚 active end `31ms/9ms`、pause 样本晚 `6442ms`，只提供诊断支持而非稳定游戏语义。没有合格的毫秒级来源，`time_alignment.py` 与 tests 保持不变，Task 4 继续停止。
- 2026-07-19 pause fail-closed repair 已完成：`time_alignment.v2` 在生成任何窗口前拒绝 Performance `pauseCount > 0`、Stats `Pause Count > 0`、非零/非有限暂停时长和畸形暂停证据；legacy `time_limit_ms` native snapshot 也经过同一 guard，Run ingestion 只转发 Stats pause count，不重建暂停墙上时长。normal/timescale 无暂停证据保持原窗口。focused time-alignment/native tests `43 passed`，Stats pause-count ingestion 定向测试 `1 passed`，compileall 与 `git diff --check` 通过；仓库外真实 `source-performance.perf` 离线解析出 `361` 个事件和 `pauseCount=1 @ 11.484201s`，resolver 返回 `pause_unsupported: pauseCount > 0` 且未生成窗口。Task 4 仍停在 Restart 前。
- 2026-07-19 Task 4 Restart-before-completed-attempt 行已通过：点点按实机协议在普通局开局约 10–15 秒后 Restart，并完整打完重启后的局；最终 Stats `Challenge Start 15:00:09.056`、`Pause Count 0` 与 Performance 同秒一致，末事件解析出 `59.894s` canonical window。MP4 首帧为重启后 Challenge `0.00`、末帧约余 `0.04s`，没有混入 Restart 前画面；Raw canonical window 为 `41940` points、视频为 `3654` packets，均完整覆盖且 capture/Raw `0` drop。请求 start 在导出时距 live head `296.818s` 仍成功，导出后 producer 又增长 `1271` packets，证明近 300 秒 retention edge 的 immutable export 未停止后续 capture。真实 adapter failure 与 AMD/Intel Gate 仍未完成，发布状态保持 **No-Go**。

- 2026-07-19 Automatic Run Finalization v1 Tasks 1-6 automated Gate passed. The private capture coordinator, canonical Run finalizer, Run-owned Raw/MP4 readiness, automatic Analysis inputs, v15 classified storage, and single-evidence tombstone removal are implemented. Task 4 focused suites: `128 passed`; Task 5 focused suites: `122 passed`; Task 6 vertical/focused Gate: `25 passed, 1 skipped` (the skip requires explicit loopback subprocess integration). MSVC `fmt/check/clippy` passed and Rust tests were `60 passed, 6 ignored`; ignored tests require explicit hardware/live KovaaK smoke. Two consecutive normal/timescale sources produced two idempotent `pending_analysis` Runs in automation, and response-loss startup reconciliation recovered one published artifact before isolated video removal. This is automated evidence only: Task 7 Windows product-path field validation, AMD/Intel hardware Gates, and release readiness remain open; status stays **No-Go**.
- 2026-07-19 Automatic Run Finalization v1 Task 7 product-path field Gate partially passed and then triggered its Stop rule. The real Tauri/native/Python path produced one ordinary Run, two independent consecutive Runs, and only completed post-Restart attempts under one live NVIDIA hardware capture session with zero Raw/video drops or encoder errors. The operator-attested pause row nevertheless had Stats `Pause Count=0` / `Pause Duration=0` and no Performance pause event; native export detected `video_coverage_gap`, but the product retained permanent Raw and exposed `pending_analysis/input_native` instead of the required `incomplete_evidence`. Field evidence also exposed timer-profile misclassification from `bot_max_lives=[0,...]` and continuous missing-source retry writes (`sqlite_sequence +91` in 5 seconds with no new Run). Capture is disabled; interrupted-finalization reconciliation, Storage removal, and AMD/Intel field rows remain open. Status stays **No-Go**.
- 2026-07-20 Automatic Run Finalization v1 Task 8 automated repair Gate passed. Generic encoded-video `capture_coverage_gap` now atomically finalizes the Run as `incomplete_evidence`, clears canonical Raw/MP4 claims, and commit-first cleans managed Raw through the existing recoverable tombstone lifecycle without labeling the gap as pause; unrelated video failures still retain valid Raw. Timer-only profiles with all-zero `bot_max_lives` now use the normal/timescale timer window, while positive life/kill/damage limits still permit terminal Stats events. Stable Stats-only/Performance-only revisions are consumed once as `waiting_for_sources` without watcher retry/upsert/SQLite sequence churn, and counterpart arrival finalizes once. Focused Task 8 suites are `85 passed`; adjacent runtime/readiness suites are `85 passed, 1 skipped`; v15 DB regression is `36 passed`; Python compileall and scoped `git diff --check` pass. No new KovaaK field run was performed, so Task 7 resumption, interrupted-finalization/Storage rows, AMD/Intel Gates, and release readiness remain open; status stays **No-Go**.
- 2026-07-20 Task 8 repair field confirmation closed the three original blockers but exposed a new Task 7 Stop-rule defect. A real paused Run `52058` now correctly remains `incomplete_evidence` with `video_pause_unsupported`, no permanent Raw/MP4, no supported mode, no Analysis, unchanged sources, and no retry/sequence growth; a normal all-zero-`bot_max_lives` Run `52060` correctly used the full `60000ms timer_profile` window and produced a verified 60-second hardware H.264 MP4. However, its Run-owned Raw ended `6722ms` before the canonical end while the current live ACRI snapshot reached end minus `5ms`; the product therefore attached an incompletely refreshed Raw snapshot and publicly exposed Raw/multimodal readiness. Capture was disabled cleanly with zero Raw/video drops or encoder errors, and Task 7 stopped before interrupted-finalization/Storage rows. Status stays **No-Go**.
- 2026-07-20 Automatic Run Finalization v1 Task 9 automated repair Gate passed. Raw Input now establishes a capture-clock ordered barrier, drains queued Windows input, and places the barrier on the existing single-producer snapshot channel; the worker force-publishes ACRI v1 atomically before acknowledging `coveredThroughEpochMs`. The session-bound private protocol preserves typed Raw retry codes, and automatic attachment requires coverage through canonical end without using the last real mouse event or NTFS mtime. Retry cannot mix Raw from a new capture session with a pending MP4 from an old session, and a retention-expired trace cannot be flushed or resurrected by duplicate discovery. Python focused suites are `83 passed`, adjacent ingest/readiness suites are `89 passed`, compileall passes, and MSVC `fmt/check/clippy` plus native tests (`64 passed, 7 ignored`) pass. An explicitly invoked live-KovaaK idle Raw barrier smoke also passed without a Challenge or source/Run mutation. The ordinary Challenge field confirmation and remaining Task 7 matrix are still open; status stays **No-Go**.
- 2026-07-20 Task 9 normal-Challenge field confirmation passed. Real Run `52096` finalized once under capture session `f028aa35...a1ba1b0` with Stats/Performance/Raw/MP4 all available, `pending_analysis`, no Analysis auto-start, and no finalization error. Its 60-second canonical ACRI contains `41,363` points from start `+6ms` through end `-14ms`, closing the prior `6722ms` tail gap; the 60-second H.264 receipt is bound to the same session/window, contains `3619` packets and `0` reencoded frames, and its file digest matches. Start/middle/end frames show only the intended Challenge from `0.00` through about `0.11s` remaining. One-source/one-Run identity and SQLite sequence stayed unchanged over a five-second duplicate observation, and user source fingerprints still match. Capture/app/helpers shut down cleanly while KovaaK remains open. Interrupted-finalization reconciliation, Storage removal, and AMD/Intel rows remain open; status stays **No-Go**.
- 2026-07-20 Automatic Run Finalization v1 Task 7 interrupted-finalization and Storage rows passed. For real Run `52136`, the backend was deliberately suspended after the original capture session had atomically published its MP4 and receipt while SQLite still held `video_state=pending`; closing and reopening the same Tauri product binary reconciled that same source into the same Run exactly once. The recovered Run is `finalized` / `pending_analysis`, exposes Stats/Performance/Raw/MP4 with three supported modes, keeps `analysis_count=0`, and has no duplicate export. MP4, receipt, Raw, Stats, and Performance hashes are unchanged across restart. An isolated real desktop-runtime Storage fixture reported exact categorized usage, removed only its selected video bundle, reclaimed the expected bytes, and returned idempotent `already_unavailable/0` on repeat while Raw, linked Analysis, and source hashes remained unchanged. App/helpers shut down cleanly; Run `52096` was not touched. AMD/Intel field hardware remains unavailable, so release status stays **No-Go**.
- 2026-07-20 Windows Capture Compatibility and Lifecycle Repair v1 was closed at its evidence boundary. Task 1 lifecycle repair passed: orderly desktop shutdown drains finalizer futures before native capture release and database close, while a live session is not released per Run. Task 2 high-polling batching stopped as assessment-only because `GetRawInputBuffer` would require a larger message-loop rewrite to prove byte/order equivalence; canonical Raw remains unchanged and 1000 Hz is only an effective derived analysis bucket. Task 3 hardware compatibility stopped as assessment-only because exposing fine-grained MFT/D3D11/format reasons would expand the frozen status contract; hardware H.264 negotiation, adapter matching, bounded queues, Raw independence, and CPU-fallback rejection remain intact. Task 4 typed Win32/runtime failures passed after separating process-probe/data-read/registration failures from KovaaK absence, fixing monitor-start rollback, `WM_QUIT` termination, ready-then-exit cleanup, and release-path error propagation. MSVC `fmt/check/clippy` passed; native tests are `68 passed, 7 ignored`; focused Python finalizer/runtime/ingest/client tests are `51 passed, 1 skipped`; time-alignment tests are `12 passed`; compileall and `git diff --check` pass. Full webapp collection still has unrelated environment gaps (missing PI/Node modules and absent historical fixture); AMD/Intel physical validation remains external and release status stays **No-Go**.

## 2. 能力成熟度

### 2.1 Implemented foundation（代码已有）

- Tauri 2 Desktop shell、本地 Python API/worker 生命周期、动态 loopback 地址与 launch-scoped Desktop token；
- native MP4/CSV path import、managed App Data workspace、storage accounting 与 terminal Analysis 删除基础；
- FastAPI queue、worker recovery、workspace/streaming upload、health/readiness；
- 既有视频 + Stats flicking CV、诊断、处方和报告领域逻辑；
- Pi-based Coach runtime、sidecar、服务编排与持久化基础；
- Versioned Coach Knowledge Registry、Python compatibility adapters、Pi `get_coach_knowledge`、source/claim/limitation discipline 与版本化安全 trace；
- `.perf` protobuf-wire parser、KovaaK Stats / Performance watcher、稳定文件判断与 Run upsert；
- SQLite `kovaak_runs`、Windows-only Raw Input、versioned snapshot 与 trace window extraction；
- AnalysisResult v2、三种 input mode dispatch、History/Run read models、Coach allow-list projection 与 Benchmark local store。

### 2.2 Contracted capability（产品/spec 已冻结）

- Run 独立于 Analysis；Stats、Performance、Raw Input、MP4 是不同 evidence source；
- input-native、multimodal、video-fallback 必须显式表达 mode、availability、alignment 和 limitations；
- Analysis readiness 统一为 `Stats AND (MP4 OR (Raw + Performance))`；multimodal 只用 Raw 计算输入运动学，MP4 只负责回放、视觉定位和 Coach 直观讲解；
- Raw Input 默认关闭、仅 Windows、仅 KovaaK process gate、本地保留、默认不进入 Coach；
- 自动采集使用 process gate 连续 Raw + 300 秒 encoded-video replay buffer 和 Stats / Performance 事后切窗，不声称存在实时 Challenge start/end hook；连续多局生成独立 Run，未选择 Run 保持 pending_analysis；
- History 采用轻列表 + lazy detail；Coach 使用结构化、用户可见的 diagnostic context，并通过与 UI 共用的产品命令调用当前用户能力，不限制为只读；
- Coach explanation 必须形成观察、白话解释、证据、claim level、训练 cue、预期变化和复测链；
- 当前不设付费墙；首次启动以 Provider onboarding 为主路径但允许通过底部次级文字跳过。跳过后没有 Coach 对话、解释、长期档案、训练计划或产品命令，只有本地指标、确定性诊断、规则化提示和 History。Settings 使用完整 pinned Pi catalog，并支持 custom OpenAI-compatible profile；API key 可明文保存在本地 SQLite/config，secure store 不是 blocker，但所有外发/日志/诊断/导出边界继续 redaction；
- terminal Analysis、Run metadata、Run-owned trace、Run-owned 自动 MP4、用户 Stats/Performance 源文件和手动 fallback 的 Analysis-managed MP4 副本具有不同 ownership 与删除影响；Storage 先显示分类占用并由用户手动管理，不静默自动清理。

### 2.3 User-reachable / release-ready（尚未完成）

- 原 prototype 已删除；当前没有正式用户路径，不能作为视觉或产品验收证据；
- 正式 Provider onboarding、App shell、New Analysis、Tasks、History、Analysis workspace、Coach sidebar、Settings 尚待按新 frontend spec/plan 重建；
- Capture Coordinator、Run Finalizer、pending Run readiness、Run-owned MP4 和单证据 Storage 手动删除 capability 已通过自动化 Gate；正式用户入口、真实 Tauri product-path field Gate 与 AMD/Intel 硬件 Gate 尚未完成，因此不能作为用户可达或 release-ready 能力。GPU-resident Media Foundation H.264、300 秒 encoded replay ring 与无整窗重编码 MP4 mux 已通过合成硬件 smoke、真实 KovaaK 空闲窗口 repair Gate、normal Challenge、timescale-extended 与 Restart canonical export；short-pause assessment 未找到 frozen contract 可接受的毫秒级 pause wall duration source，当前 resolver/legacy/native ingestion 已改为 fail closed，不重建暂停墙上时长。
- Coach productization Task 3–5 backend/runtime capability 已完成；首次 Provider onboarding、命令结果消费和完整 Coach sidebar 仍不可交付，因为正式 frontend Task 2/3/6 尚未授权和实现；
- input-native、multimodal、video-fallback 三条真实 Browser/Desktop E2E 未形成；
- Windows 实机、高 polling-rate 鼠标、真实 KovaaK 对齐与性能尚未通过发布 Gate；
- installer、正式 icon、签名、公证、updater 和可信云服务仍未完成。

## 3. 当前高优先级实现差距

### Analysis / evidence

1. Task 2/3 的当前平台合同与 snapshot revision correctness 已闭合；正式 angular/target-relative trajectory、噪声地板、SPARC 跨 polling 可比性和 Windows 实机阈值仍待校准；
2. Task 4 已正式收口三种 mode dispatch：input-native 不读取视频，multimodal 视觉失败保留 native result，video-fallback 不生成 Raw provenance；run-based 视频在 CV 前、provider/report 前和 terminal write 前校验 managed revision，变更时 fail-closed 且不泄露路径；v1/unversioned legacy 继续可读；
3. History trend 的 scenario identity / calibration / metric contract 已严格 fail-closed；当前 video-fallback coverage 为 `null`，真实 calibration 与完整 evidence 未满足时不会输出趋势，真实数据可比性仍需后续 Gate 验证。

### Reliability / lifecycle

1. 同 stem 同类型 KovaaK 文件冲突、并发 trace attach、partial import 和 orphan recovery 仍需更强状态机与测试；
2. terminal Analysis 的 commit-first logical delete、transient tombstone、managed workspace cleanup 与 startup ready 前 reconciliation 已完成；Run-owned MP4 / trace 的单证据删除事务与恢复已通过自动化验证，但 UI、真实 product-path field Gate、Run metadata 整体删除与长期 retention 仍未完成；
3. launch token 子进程隔离已闭合；Python runtime READY 后崩溃与正式浏览器 media identity 保持 deferred，Windows Python↔Pi subprocess 的 file URL、pinned tsconfig 与 venv path 自动化 Gate 已闭合。
4. 新自动采集合同已有 active implementation plan，process-gated Raw/录像、延迟 Stats/Performance、事后切窗、幂等 Run finalization、未完成采集恢复、storage accounting 和 Run-owned evidence 单证据删除已通过自动化 Gate；当前代码仍不能替代真实 Tauri product-path field 证据。

### Frontend reconstruction

1. Prototype 已删除，不得恢复；当前无 `app/` / `pages/` 导致 `next build` 失败，需在点点明确授权相应正式前端 Task 后处理；
2. 正式路由、低保真结构、页面状态矩阵、Desktop/Web capability 表和 accessibility Gate 已由 UI/UX 与 active reconstruction spec 冻结，但尚未实现；
3. executable token/theme/primitives 尚未建立；Task 2 尚未获得具体 Task 授权；
4. Benchmark 不进入 v1 正式前端；
5. Task 1 已完成 prototype 删除与 adapter 边界保护；Task 2–7 尚未获得具体 Task 授权。
6. Frontend Task 3/4/6 已同步新的自动采集、待分析 History 和 Storage 合同；在对应稳定 capability 实现前必须按 Stop rule 停止，不能由前端伪造。

### Coach / explanation / provider

1. 当前所有 v1 Pi turn 均注册 `get_analysis_summary` 与 `get_coach_knowledge`，仅有固定 loopback product bridge 时才增加 `run_product_command`；knowledge 按 signal/metric/topic/use 确定性检索最多三条完整 entry，不向 Coach 暴露 shell、任意文件、数据库或 coding-agent 工具；
2. Coach Provider Task 3–4 已接通完整 pinned Pi catalog、owner/profile selection、custom OpenAI-compatible profile、SQLite v10 type-tagged credential persistence、selected `coach_runtime_turn.v1`、动态 Pi auth capability、API-key/ambient、OAuth/device-code callbacks、refresh、local-only revoke、sidecar status/test 与 readiness isolation；`LLM_PROVIDER + providers.json` 只保留 legacy compatibility，不再是 provider/model 事实源；Coach 与 Analysis narration 也已移除固定 DeepSeek backend/CNY budget gate，未建立 provider-specific usage/currency contract 时不写伪精确 `llm_cost_cny`；
3. Coach productization Task 1 已完成：Flicking/Tracking explanation、claim、limitations、训练目标与复测合同已进入真实 producer，canonical context 与 Python fallback 均保留安全字段；未校准规则统一 fail-closed 为 `experimental/info`；
4. Coach 已能通过与 UI route 共用的 owner-scoped handler 查询 Run / History / Analysis、比较分析、生成 typed navigation/evidence/video-time event、创建或重试 Analysis，并生成、保存、激活、暂停、调整和 review 带版本理由与 verification targets 的 Training Plan；普通明确写操作直接执行，Coach 推断写操作和替换 active plan 走持久确认，命令具有 append-only audit 与独立 idempotency replay；正式前端尚未消费这些事件和确认；
5. API key/OAuth credential 明文本地 persistence 已按 local-first 决策完成；进行中的 auth operation 不持久化 provider-specific 中间状态，backend/sidecar 重启后明确 interrupted 并允许重试；正式前端 credential UX 仍待 frontend Task 3/6。
6. Versioned Knowledge Registry 已完成并作为唯一运行时知识正文：当前含 43 条 active canonical entries、19 个 canonical signals 与 67 条逐项 migration audit；覆盖 Flicking、Tracking、身体/张力候选假设、settings、practice、处方验证和反证限制。Python/TS parity、真实 Analysis signal/metric → Pi retrieval → SQLite trace E2E 已通过；trace 仅保存 registry/entry/version/source/claim refs，不保存知识正文、路径、secret 或 raw payload。
7. 点点提出后续 Coach 讲解需要更直观：先说人话，再按需展开指标，并探索图标、轨迹/阶段动画、问题区段标记和前后对比；这只是待讨论的产品/视觉方向，尚未冻结进 frontend contract，也未实施。

## 4. 下一步

> **新 session 接力点：** Automatic Run Finalization v1 Task 9 ordered-barrier repair、普通 Challenge field confirmation，以及 Task 7 interrupted-finalization reconciliation 与 isolated Storage accounting/removal 已通过。Run `52096` 保持完整；新 Run `52136` 在 MP4/receipt 已发布而 DB 仍 pending 的精确中断点关闭应用，重启后原地恢复为唯一 finalized Run，所有证据哈希不变且未自动启动 Analysis。Aiming Cookie/coordinator/helper 已关闭，KovaaK 保持开启；AMD/Intel 仍需真实对应硬件，不能由 NVIDIA 结果替代。未经指示不提交、不推送。

> **采集计划收口：** completed [`archive/completed/plans/2026-07-19-automatic-run-finalization-v1.md`](archive/completed/plans/2026-07-19-automatic-run-finalization-v1.md) 的 Task 9 repair、普通 Challenge、interrupted-finalization 与 Storage field rows 已完成；AMD/Intel 物理硬件仍是 Roadmap 的外部发布 Gate，不属于继续扩大旧 plan 的内部 Task。任何新的真实采集操作仍必须重新执行 capture-ready → 明确“开始” → capture-started 握手。

1. AMD/Intel 仍是外部硬件 Gate，NVIDIA normal/recovery rows 通过不能替代其他 adapter 的发布证据；
2. Task 7 当前可执行 field matrix 已闭合；若不具备 AMD/Intel 硬件，下一步应由点点明确授权新的 active plan Task，而不是继续扩大本 Task；
3. Frontend Task 2 仍可独立建立 token/theme/primitives；正式前端只消费已稳定 capability，不复制命令逻辑，也不反向定义后端语义。

## 5. 当前工作树与实施计划状态

2026-07-20 本轮继续在既有 dirty worktree 上完成 Automatic Run Finalization v1 Task 7 的 interrupted-finalization reconciliation 与 Storage accounting/removal field rows。仓库内仅在证据存在后更新本 Progress；实机 Run `52136`、独立 Storage fixture 和辅助日志均位于仓库外，没有修改业务代码、测试、PRD、Architecture 或用户 Stats/Performance，没有提交、推送、reset、checkout、覆盖或清理既有改动。Run `52096` 保持不变。

2026-07-20 本轮继续在既有 dirty worktree 上完成点点授权的 Automatic Run Finalization v1 Task 9：修改 `raw_input.rs`、`capture_coordinator.rs`、native client/finalizer/Run store 与对应 focused tests，并在产生自动化、真实 idle barrier 和普通 Challenge field 证据后更新本 Progress；没有修改 ACRI/schema/DB/routes/PRD/Architecture，没有提交、推送、reset、checkout、覆盖或清理既有改动。Run `52096` 与用户 Stats/Performance 保留，未执行 Storage removal。

2026-07-20 本轮此前在同一 dirty worktree 上先完成点点授权的 Automatic Run Finalization v1 Task 8，再回到 Task 7 运行真实 Tauri/KovaaK repair field Gate。Task 7 只新增仓库外证据并更新本 Progress；没有修改业务代码、schema/native/routes/DTO，没有提交、推送、reset、checkout、覆盖或清理既有改动。Task 8 三个原始 blocker 已实盘闭合，但正常 Run Raw 尾部缺口触发新的 Stop rule。

2026-07-19 本轮在既有 dirty worktree 上执行点点授权的 hardware replay backpressure repair Task，修改 `window_capture.rs` inline tests/调度实现、active plan Task 合同，并在产生仓库外真实证据后更新 Progress；没有提交、推送、reset、checkout、覆盖或清理既有改动。

2026-07-19 Task 4 Restart 实机行只产生仓库外运行时证据并更新本 Progress；未修改业务代码、测试、PRD 或 Architecture，没有提交、推送、reset、checkout、覆盖或清理既有改动。

RefleK active plan 当前成熟度：

| Task | 当前状态 | 未闭合验收 |
|---|---|---|
| Task 1 Raw Input / ingestion | current-platform foundation 已实现 | Windows 实机、完整 Windows target 与高 polling-rate Gate 未通过 |
| Task 2 AnalysisResult v2 contract | **completed（当前平台）** | producer/persistence Gate 已强制 owner、analysis version、metric provenance/coverage/limitations、artifact ownership/managed/local-only/version，并将 analysis/run/type/mode identity 绑定到已 claim session；旧未版本化 v2 可读但不可作为新 terminal result 写入 |
| Task 3 input-native adapter | **completed（当前平台）** | Stats、Performance、Raw trace revision 在提交时冻结；worker 读取一次并以同一组 bytes 完成 fingerprint 校验和 parse/decode，不再通过路径二次打开；source 缺失、不可读或 revision 不一致稳定归类为不可重试的 `input_validation / source_unavailable`，需重新提交新 snapshot，且不泄露本地路径或底层异常。click-anchored Flick、timing、raw geometry、修正/反向、trough-depth `submovement_overlap` proxy、SPARC cutoff-normalized frequency axis、分布/outlier 与 diagnosis 已接入；SPARC 公式修正使用独立 v2 metric version，旧版不混入趋势，v2 绝对阈值暂不启用。Windows 实机、高 polling 实物、噪声地板、跨设备可比性和 angular/target-relative facts 仍属发布 Gate |
| Task 4 worker mode dispatch | **completed（2026-07-15）** | 三模式均写 v2；input-native 无视频依赖，multimodal 保留 native 主事实，video-fallback 无 Raw provenance；managed MP4 在消费与外部 provider 副作用前校验，legacy fallback/read 保留 |
| Task 5 Coach diagnostic context | **completed（2026-07-15）** | 单一 allow-list projection、跨 Python/Pi/tool/store/API 结构一致、v2 deterministic classification、stable ref、raw/path/URL/payload/duplicate-key sentinel 与 deleted→unavailable 均已验收 |
| Task 6 History / evidence replay | **Task 6A completed（2026-07-16）；6B frontend deferred** | backend light list/lazy detail、frozen availability/trace、evidence locator、共享 replay Gate 与严格 comparability 已验收；正式 History UI 最后处理 |
| Task 7 Benchmark local domain | local store / API 已实现；历史 prototype UI 已由 Frontend Task 1 删除 | 不进入 v1 正式前端、默认 Coach 或在线 provider；正式产品化仍 deferred |

Frontend reconstruction plan 已 active。Task 1 已在点点确认 10 个文件的精确范围后完成；inventory 为 `webapp/frontend/prototype-inventory.json`，adapter boundary test 为 `webapp/frontend/lib/prototype-boundary.test.ts`。Task 2–7 尚未获得具体 Task 授权。

Coach productization plan 已 active。Task 1 explanation contract、Task 2 input-native core metrics、Task 3 dynamic full Pi catalog / custom profile / selected turn routing、Task 4 Pi auth/OAuth/device-code / local credential commands 与 Task 5 Pi knowledge / user-level product commands 已完成；Task 6 尚未开始，且受 frontend reconstruction Task 2–5 前置约束。

Versioned Coach Knowledge Registry plan 的 Task 1–6 已全部完成并归档；active spec 继续作为 Registry 版本、检索、claim 与 trace 的稳定局部合同。

## 6. 最近验证记录

2026-07-20 Automatic Run Finalization v1 Task 7 interrupted-finalization + Storage field rows（passed）：

- 实机精确中断：普通 Challenge 生成唯一新 Run `52136`，source 为 `1wall 6targets small - challenge - 2026.07.20-02.52.07`，canonical window 为 `[1784487067873,1784487127873)`。在原 capture session `32575f90...48afba0` 已发布 MP4/receipt、Run 仍为 `video_state=pending` 时 suspend backend PID `22580`，监控结果为 `interrupted_pending_with_published_artifacts`；随后通过主窗口关闭应用，Tauri 及两个 Python child 均退出。
- 使用同一 product binary 与 worktree/Python runtime 重新启动后，startup reconciliation 将同一 source 原地恢复为同一 Run `52136`；公开 DTO 为 `finalized`、`pending_analysis`、Stats/Performance/Raw/MP4 全 available、`input_native/multimodal/video_fallback` 可用、`analysis_count=0`、无 limitation/finalization error。同 Run id 与同 source 均只有一行，Run 目录只有一个 Raw、一个 MP4 和一个 receipt，没有 partial 或重复导出。
- 重启前后 MP4 SHA-256 均为 `3104717da7dfaec01d9c6990b927ca72402d5cca461373b4bb6bf4182c8945ef`，receipt 均为 `8703b988241be4c5c571e2f12650312ac3677ab6615cb3eafd673b07a4b17173`；Raw 为 `691837d116df479ca5607122ae4c5f7c21f00cfef163395f11f04fd04bb03a45`。Stats 为 `d1a6c8d1a805edbd637857a7cf2771a28175ee8f24f1493af52194fff084fb68`，Performance 为 `7ecfe29333372811dc3b4f6f8ca0511c376bd3fd39e8d247e41a53206809277e`，均未改变。receipt 仍绑定原 session/request/window，含 `3650` packets、`60000ms` visible duration 和 `0` reencoded frames；重启没有改写 MP4/receipt mtime。
- 独立 `DATA_ROOT`/AppData 的真实 desktop-runtime HTTP API Storage fixture 在删除前精确为 Analysis `32` bytes、Run MP4+receipt `742`、Run Raw `52`、incomplete recovery `38`，总计 `864`。首次只删除 video 返回 `completed` / `742` bytes，重复调用返回 `already_unavailable` / `0`；删除后分类为 `32/0/52/38`，总计 `122`。视频与 receipt 消失，Raw、linked Analysis、Stats/Performance fixture 仍存在且哈希不变，公开响应不包含本地路径。
- 实机证据包为 `E:\DevCache\temp\aiming-cookie-task7-interrupt-20260720-024345`；isolated Storage 证据包为 `C:\Users\袜子\.codex\runtime-validation\task7-storage-field-20260720`。重启恢复后 Coordinator 重新进入 60 FPS hardware capture，最终应用、backend、status receiver 与静态 web helper 均正常关闭；KovaaK 保持开启。该 Gate 不替代 AMD/Intel 硬件验证。

2026-07-20 Automatic Run Finalization v1 Task 9 normal Challenge field confirmation（passed）：

- 按显式 ready/start/capture-started 协议启动真实 Tauri/native/Python product path；Coordinator 进入 `capturing`，session `f028aa350a4682a648ecd7d8ac8f93d3e3aa34b5f76e4fc7c7b666963a1ba1b0`，Raw/video 均为 capturing，NVIDIA `vendor:10de;device:2560` 使用 Media Foundation hardware H.264。点点随后完整完成一局 pause-free、无 Restart 的普通 Challenge。
- 唯一新 Run `52096` 使用 source `1wall 6targets small - challenge - 2026.07.20-02.25.06`，canonical `[1784485446797,1784485506797)`、`60000ms`、start=`stats_challenge_start`、end=`timer_profile`。公开 DTO 为 Stats/Performance/Raw/MP4 全 available、`pending_analysis`、三种 mode 可用、`analysis_count=0`、无 limitation/finalization error。
- 永久 ACRI v1 为 `41,363` points、`827,272` bytes、SHA-256 `271efd6cdfbf156a05f2cec1a73020f793f1dec65a9970aad97e60619ca06fe4`；首点为 start `+6ms`，末点为 end `-14ms`。这直接闭合 Task 8 field 的 `6722ms` tail gap，且 attachment 只能在 ordered barrier coverage 越过 canonical end 后发生。
- MP4/receipt 的 capture session 与 Run 一致，window 精确为同一 canonical 60 秒；receipt 为 `3619` packets、`32,485,217` encoded bytes、`0` reencoded frame，容器为 H.264 Constrained Baseline `1920x1080`、约 `60 FPS`、`60.000s`、`3619` 帧、`32,500,652` bytes，文件 SHA-256 `5b01bed3453adfea29fe74d1285dd57c65a5823b958fb61ec2d78d2195eb1163` 且与 receipt digest 一致。
- 抽取首帧、30 秒和 `59.8s`：分别为 Challenge `0.00`、局内约余 `29s`、局内约余 `0.11s`；没有等待/结算、桌面泄漏或黑帧。Stats SHA-256 `d15a403c23ba4229b1938fa258ab64ed5590495ff143c4657fa26680c2de3242` 与 Performance SHA-256 `bd787ee31d1220298bddac1e1f7474303caeec55d13d7168552e1516a9b59931` 均与 persisted source fingerprint 匹配。
- 同 source 在五秒观察中始终只有 Run `52096`，finalized/attached 状态、`updated_at` 与 `sqlite_sequence` 不变；没有重复 Run/export 或 watcher churn。验证结束后主窗口收到正常 close，Desktop runtime child 与 helper 全部退出，KovaaK 保持开启。
- 仓库外证据包为 `E:\DevCache\temp\aiming-cookie-task9-field-20260720-021334\validation-summary.json`，SHA-256 `b570958c3c191b73d80987ed3847f52eaa14225f93174cafaa31951682da9d42`。该行证明 NVIDIA normal Challenge ordered-barrier finalization，不替代 interrupted-finalization/Storage 或 AMD/Intel field rows。

2026-07-20 Automatic Run Finalization v1 Task 9 ordered-barrier repair（automated + non-interactive live idle pass；Challenge field pending）：

- Raw capture thread 使用容量 `1` 的非阻塞 control channel；收到请求时先记录 `coveredThroughEpochMs`，再 drain 当前 Windows message queue，并在同一 point/snapshot channel 中排入 barrier。snapshot worker 即使 ring clean 也调用 atomic ACRI v1 publication，只有 `MoveFileExW(REPLACE_EXISTING | WRITE_THROUGH)` 成功后才返回 receipt；busy、timeout、write failure 和 unavailable 保留 typed retry code。
- Python automatic path 只有在 barrier coverage `>= canonical end` 时 attach Raw；局末静止不会被末 Raw point 误伤，低 coverage 在 300 秒 snapshot retention 内保持 `trace_waiting_snapshot`，超过后固定为 `trace_snapshot_stale`。response-loss 后若 pending MP4 属于旧 capture session，新 session 不会 flush/attach Raw；已 stale 的 trace 在 duplicate/app restart 后不会复活。
- `KOVAAK_INSTALL_DIR` 强制到不存在路径后的 focused client/finalizer/Run suites 为 `83 passed`，相邻 ingest/routes/history/Coach readiness suites 为 `89 passed`；Python compileall、scoped `git diff --check` 通过。MSVC `cargo fmt --check`、`cargo check --locked --all-targets`、`cargo clippy --locked --all-targets -- -D warnings` 通过；完整 native tests 为 `64 passed, 7 ignored`。
- 显式运行 `live_kovaak_raw_snapshot_barrier_smoke` 在当前真实 KovaaK 进程上通过：Raw producer 被观察为 healthy，一次 ordered barrier 成功发布并解码 ACRI v1，receipt 的 coverage/snapshot clock 为 `utc_epoch_ms+qpc` / `time_alignment.v2`，随后 capture 与临时文件均关闭/清理。该 smoke 不要求鼠标操作，不创建或修改 Stats/Performance/Run，也不证明 Challenge canonical attachment。
- MSVC field binary 已生成在仓库外 `E:\DevCache\temp\aiming-cookie-task9-automated\target-msvc\debug\aiming-cookie-desktop.exe`，SHA-256 `F87B20B9F0879BC40525562E59DCCA9EDC3CE1BFF56B4C6FCE62384D37E56E96`；path-free、token-memory-only field harness 已准备在 `E:\DevCache\temp\aiming-cookie-task9-field-20260720-021334`，尚未启动。下一步必须等待点点明确开始普通 Challenge field confirmation。

2026-07-20 Automatic Run Finalization v1 Task 8 repair field confirmation（partial pass；new Task 7 Stop rule triggered）：

- missing-source 启动基线观察 5 秒，`kovaak_runs` 行数与 `sqlite_sequence` 均为 `delta=0`，证明 stable missing-source watcher 不再产生 retry/upsert churn。真实暂停 Run `52058` 的 Stats 为 `Pause Count=1`、`Pause Duration=6`；最终状态为 `video_pause_unsupported` / `incomplete_evidence`，无 managed Run root、Raw、MP4、supported mode 或 Analysis。重复观察后 Run/sequence 仍无增长，Stats/Performance SHA-256、size 与 mtime 均未改变。
- 普通 Run `52060` 的 Performance profile 为 `bot_max_lives=[0,0,0,0,0,0]`；`time_alignment.v2` 正确解析 canonical `[1784479608984,1784479668984)`、`duration=60000ms`、`end_source=timer_profile`。Run-owned MP4 为 H.264 Constrained Baseline `1920x1080`、`60.000s`、`3652` 帧、`33,735,906` bytes，receipt 的 window/fingerprint 与 DB/文件一致；公开 API 为 `pending_analysis`、三种输入 mode 可用、`analysis_count=0`，无 private path。
- 同一普通 Run 的永久 ACRI v1 只有 `37833` points，末点距 canonical end `6722ms`、末 button event 距 end `6923ms`；稍后 current live ACRI 对同一窗口有 `42629` points，末点距 end 仅 `5ms`、末 button event 距 end `415ms`。这证明底层 Raw producer 覆盖了局末，但 finalization 过早接受了尚未刷新到 canonical tail 的 snapshot；公开 Raw/multimodal claim 因此不成立，满足 Task 7 “eligible normal row unexpected missing coverage” Stop rule。
- Stop rule 后通过产品命令关闭 Coordinator，最终 `phase=disabled`、Raw/video `enabled=false`。最终 capture 为 NVIDIA `vendor:10de;device:2560`、Media Foundation hardware H.264、`91383` submitted packets、`0` packet drop、`0` encoder error；Raw 为 `0` dropped point、`0` snapshot failure。Aiming Cookie、DevTools、cargo 与临时 web/receiver 已停止，KovaaK 保持开启；未继续 deliberately interrupted finalization、Storage 或 AMD/Intel 行。
- 仓库外证据包为 `E:\DevCache\temp\aiming-cookie-task7-repair-field-20260720-003317\validation-summary.json`，SHA-256 `492695b6a1bd2056d263c6dfbdbf60ead97adff2ce15110ec457ea28ff7b453f`；summary JSON 可解析，引用的 10 份证据 SHA-256 全部匹配。

2026-07-19 Automatic Run Finalization v1 Task 7 product-path field Gate（partial pass；Stop rule triggered）：

- 真实产品路径使用 MSVC 编译的 `aiming-cookie-desktop.exe` 启动同一 Tauri setup、Python Desktop runtime、Stats/Performance watcher 与 private native control plane；由于正式 frontend `app/pages` 尚不存在，本次只在仓库外提供空白 WebView validation page，并通过已注册 Tauri IPC 显式启用 Coordinator，没有修改业务代码。Coordinator 进入 `capturing`，session `5f9e415b...c9121`、KovaaK `PID=30056` / `HWND=15599888`、NVIDIA `vendor:10de;device:2560`、Media Foundation hardware H.264，Raw 与 video 同时健康。
- 普通局生成唯一 Run `14046`，公开状态为 `pending_analysis`、`analysis_count=0`，Stats/Performance/Raw/MP4 均 available；MP4 为 H.264 Constrained Baseline `1920x1080`、`59.893s`、`3642` 帧、约 `60 FPS`，SHA-256 `1719ddeb460b12194ac316e7e87188cf72ede2b1957920312eb9fa90c129d7cd`。连续两局生成独立 Run `24850` / `26128`，窗口不重叠且 capture session 不变；Restart protocol 之后只为两条实际完成局生成 Run `30692` / `32408`，废弃尝试没有 Stats/Performance、Run 或永久 evidence。
- 暂停行按 operator protocol 在普通局约 10 秒后暂停约 5 秒再完成，但落盘 Stats 明确为 `Pause Count=0` / `Pause Duration=0`，Performance 也没有 pause event。Run `38987` 的 native replay export 返回 `video_coverage_gap` 且没有永久 MP4；Task 7 当时的独立降级逻辑仍写入 `836752` bytes canonical Raw，并通过公开 API 暴露 `pending_analysis`、`input_native`、Raw available。这违反 Task 7 的暂停行预期 `incomplete_evidence` 且无 canonical Raw/MP4，满足 Stop rule。
- native capture status 在停止前仍为 Raw `0` dropped point / `0` snapshot failure、video `0` dropped packet / `0` encoder error / `0` metadata drop、hardware encoder 不变。该 `coverage_gap` 与本地 drop counter 不冲突：WGC 未交付 frame 的墙上间隙不会由编码器伪造补帧；但当前 receipt/Run 没有保留 gap 位置，generic gap 本身不能证明原因必然是 pause。
- 同场发现两个独立 correctness/resource blocker：`bot_max_lives=[0,0,0,0,0,0]` 被 `bool(list)` 误判为 event-terminated，timer-only 普通局错误采用最后 Stats kill 截尾（Run `38987` 为 `59.330s` 而非 `60s timer_profile`）；两个目录 watcher 对长期缺少配对源的历史文件持续 retry/upsert，5 秒内 `kovaak_runs` 行数不变但 SQLite sequence 增加 `91`，`updated_at` 继续前进。
- 发现 Stop rule 后已调用产品命令关闭 Coordinator，并确认最终 `phase=disabled`、Raw/video 均 `disabled`；没有继续 deliberately interrupted finalization、app restart reconciliation、Storage accounting/removal 或 AMD/Intel 行。仓库外证据包为 `E:\DevCache\temp\aiming-cookie-task7-product-path-20260719-204113\validation-summary.json`，SHA-256 `33eb843698fc270549e8fc4a5c3d8343375b64067f52177627b7bf102e8cd419`。

2026-07-19 Hardware Replay Buffer Task 4 Restart before completed attempt（passed；remaining hardware matrix pending）：

- 点点在明确收到 capture-started 指令后进入普通 60 秒 Challenge，开局约 10–15 秒后 Restart，并完整打完重启后的局且未暂停。KovaaK 只为最终完成局落盘同 stem Stats / Performance；Stats `Challenge Start 15:00:09.056`、`Pause Count 0`、`Pause Duration 0`，Performance `challenge_start_utc=1784444409000`、`time_limit=60`、`timescale=1`，370 个事件的末事件为 `59.893909s`。TimeAlignment v2 解析 canonical `[1784444409056,1784444468950)`，start source 为 `stats_challenge_start`，end source 为 `performance_event`。
- capture 最终 WGC metadata `107999` 帧、H.264 packet `34801`、`0` packet drop、`0` encoder error、`0` metadata drop；总 encoded timeline 约 `579.998s`。请求 canonical start 在 snapshot 时距 live head `296.818106s`，仍在 300 秒墙上时间边界内成功导出；visible duration `59.894s`、decode preroll `0.9973694s`、`3654` packet、`31,751,093` encoded bytes、`0` reencoded frame。
- FFprobe 为 H.264 Constrained Baseline、`1920x1080`、约 `60 FPS`、start `0`、duration `59.894s`。首帧明确显示重启后 Challenge `0.00`，一秒后为在局内 `0:58`，末帧仍在局内且约余 `0.04s`；可见窗口未混入 Restart 前尝试、等待、结算、黑帧、桌面泄漏或错误裁切。Restart 点击本身属于点点按指令完成的 operator-attested field protocol；KovaaK 不为中止尝试另写 Stats / Performance。
- ACRI v1 source `59861` points 覆盖 `[1784444147084,1784444726334]`；canonical window 提取 `41940` points，首点为 start 后 `3ms`，末点为 end 前 `2ms`，`0` dropped point、`0` snapshot failure。导出 snapshot 完成后 live producer 从 `33530` 增长到 `34801` packets 且仍为 `0` drop，证明 immutable export/finalization 未阻断 KovaaK 保持开启时的后续 capture。
- 仓库外证据为 `E:\DevCache\temp\aiming-cookie-task4-restart-20260719-145546\validation-summary.json`（SHA-256 `80C46F2285EF416BBCD8E24D004EADA9297FD3DF0B23F988D49B812C34831A0F`）。该结果只关闭 NVIDIA Restart 与 export 后 producer continuation 行；真实 adapter mismatch/unavailable、AMD/Intel、Capture Coordinator、Run Finalizer、Run storage 和用户可达自动 MP4 仍未验证。

2026-07-19 Pause-aware time alignment assessment（completed；repair not applied）：

- Performance protobuf wire 在 normal、timescale、pause 三份样本中均只含已解析 top/header/profile/event 字段；pause 样本唯一新增 payload 为 hex `0d4abf37416a020801`，语义是 active timestamp `11.484201s` + `pauseCount=1`，没有 resume timestamp、duration 或可用未知字段。Stats 原始 CSV 行为精确文本 `Pause Duration:,6`，不存在隐藏小数。
- Stats kill 总数与 Performance `kills` count 累计值在 paused sample 均为 `102`，可按约一秒 Performance bucket 完整配对；pause 前 wall-active offset 中位数 `-363.532ms`，pause 后 `6202.264ms`，跳变 `6565.796ms`。这证明 pause 约为 6.5 秒，但 bucket 内单次击杀没有 active timestamp，不能恢复精确毫秒 duration。
- 文件 mtime 对比：normal Stats mtime 晚 active end `31ms`，timescale 晚 `9ms`，pause 晚 `6442ms`；该结果与 WGC diagnostic 末帧一致，并证明 Stats 整数 `6s` 丢失亚秒信息。但 mtime 是 OS write-completion time，不是稳定游戏语义字段，按 frozen contract 不能成为 correctness source。
- production 调用链另有范围阻塞：当前 `parse_stats_csv` 对三份真实 field Stats 均未取到 `Challenge Start`；`ingest_discovery` 仅为 event-terminated profile 传 `stats_event_times_seconds`，timer-limited path 也未传 `pause_duration_seconds`。修复至少需要 `kovaak_tracker/csv_parser.py` 与 `webapp/backend/kovaak_run_store.py`，超出本 Task Allowed files；即使扩大范围，现有数据仍没有被证明的精确 pause duration source。
- 按授权 Stop rule，未修改 `kovaak_tracker/time_alignment.py` 或 `tests/test_time_alignment.py`；现有 focused baseline `8 passed`。assessment 证据已补入 `E:\DevCache\temp\aiming-cookie-task4-pause-20260719-133540\validation-summary.json`；该 assessment 当时不恢复 Restart，后续以 pause fail-closed repair 和新的实机授权解除此停点。

2026-07-19 Hardware Replay Buffer Task 4 short-pause semantics（Stop rule triggered）：

- 点点在 capture-started 指令后完成 `1wall 6targets small`，约在开局 10 秒后暂停再继续。Stats / Performance 同 stem 于 `13:37:56` 落盘；Stats `Challenge Start 13:36:49.777`、`Pause Count 1`、`Pause Duration 6`。Performance `challenge_start_utc=1784439409000`、`time_limit=60`、`timescale=1`，pauseCount event 位于 `11.484201s`，361 个事件的末事件仍为 `59.944450s`。
- 当前 TimeAlignment v2 按 Performance 末事件解析 duration `59.944s`，与 filename coarse end hint `66.223s` 相差约 `6.279s`；该事实直接反驳“Performance event timestamps already include pause wall time”的当前实现假设。将 Stats 整数 `Pause Duration=6` 相加得到 `65.944s`，仍无法证明毫秒级 wall end。
- 两个 diagnostic-only replay export 均成功且 capture 继续运行：未加 pause 的 `59.944s` MP4 末帧仍余 `6.57s`；加整数 6 秒的 `65.944s` MP4 末帧仍余 `0.56s`。因此两者都不是可接受的 canonical MP4，不得保存为 Run-owned final evidence。
- capture 最终 WGC metadata `62651` 帧、H.264 packet `19198`、`0` packet drop、`0` encoder error、`0` metadata drop；Raw ACRI v1 `44821` points、`0` dropped point、`0` snapshot failure。约 `286.735s` 性能样本为 `0.0658` CPU core、`248.0 MiB` peak working set；GPU Video Encode 三次采样平均 `15.7060%`。失败只属于 pause alignment semantics，不属于 capture performance/coverage。
- 仓库外证据为 `E:\DevCache\temp\aiming-cookie-task4-pause-20260719-133540\validation-summary.json`。Task 4 按 pause-source conflict Stop rule 停止，Restart、真实 adapter failure 和 AMD/Intel 行未继续；未经新 repair Task 授权不得修改 `time_alignment.py` 或恢复 field matrix。

2026-07-19 Hardware Replay Buffer Task 4 timescale-extended Challenge（passed；pause/Restart pending）：

- 点点在 capture-started 指令后完成 `1wall5targets_pasu`，Stats / Performance 同 stem 于 `13:21:41` 落盘。Stats `Challenge Start 13:20:16.265`、`Pause Count 0`、`Pause Duration 0`；Performance `challenge_start_utc=1784438416000`、`time_limit=60`、`timescale=0.699999988`，496 个事件的末事件为 `85.694008s`。TimeAlignment v2 解析 canonical `[1784438416265,1784438501959)`，start source 为 `stats_challenge_start`，end source 为 `performance_event`。
- capture 最终 WGC metadata `66933` 帧、H.264 packet `19414`、`0` packet drop、`0` encoder error、`0` metadata drop；canonical export visible duration `85.694s`、decode preroll `0.9811117s`、`5201` packet、`49,184,903` encoded bytes、`0` reencoded frame，且导出后 producer 继续运行。
- FFprobe 为 H.264 Constrained Baseline、`1920x1080`、约 `60 FPS`、start `0`、duration `85.694s`。首帧为 `1wall5targets_pasu` Challenge `0.00`，末帧仍在游戏内且约余 `0.14s`，未混入前置等待、结算界面、黑帧、桌面泄漏或错误裁切。
- ACRI v1 source `82800` points 完整覆盖 canonical window；窗口内 `78733` points，首点为 start 后 `5ms`，末点为 end 前 `5ms`，`0` dropped point、`0` snapshot failure。约 `284.874s` 性能样本为 `0.0589` CPU core、`224.83 MiB` peak working set；GPU Video Encode 三次采样平均 `15.6743%`，低于 `384 MiB` frozen byte ceiling 对应的整体资源风险边界。
- 仓库外证据为 `E:\DevCache\temp\aiming-cookie-task4-timescale-20260719-131856\validation-summary.json`。该结果只关闭 timescale-extended 行；短暂停、Restart、真实 adapter failure、AMD/Intel、Capture Coordinator 和 Run storage 仍未验证。

2026-07-19 Hardware Replay Buffer Task 4 normal Challenge（passed；remaining matrix pending）：

- 点点在明确收到 capture-started 指令后完成 `1wall 6targets small` 普通 60 秒 Challenge；Stats / Performance 同 stem 于 `13:04:36` 落盘。Stats `Challenge Start 13:03:36.667`、`Pause Count 0`、`Pause Duration 0`；Performance `challenge_start_utc=1784437416000`、`time_limit=60`、`timescale=1`，363 个事件的末事件为 `59.904930s`。TimeAlignment v2 解析 canonical `[1784437416667,1784437476572)`，start source 为 `stats_challenge_start`，end source 为 `performance_event`。
- capture 持续约 `336.965s`，最终 WGC metadata `65197` 帧、H.264 packet `20219`、`0` packet drop、`0` encoder error、`0` metadata drop；对 canonical window 的 immutable export 成功，visible duration `59.905s`、decode preroll `0.8731559s`、`3647` packet、`31,192,963` encoded bytes、`0` reencoded frame，且导出后 producer 继续运行。
- FFprobe 为 H.264 Constrained Baseline、`1920x1080`、约 `60 FPS`、start `0`、duration `59.905s`。首帧显示 Challenge `0.00` 开始转场；末帧仍在游戏内且倒计时约余 `0.11s`，未混入前置等待、结算界面、黑帧、桌面泄漏或错误裁切。
- ACRI v1 source 为 `52583` points，覆盖 `[1784437335748,1784437672022]`；canonical window 提取 `40644` points，首点为 start 后 `58ms`，末点为 end 前 `3ms`，`0` dropped point、`0` snapshot failure。请求 `301s` replay window 明确返回 `WindowTooLong`；硬件 failure kind 区分与 automatic CPU fallback denied 测试通过。
- 仓库外证据为 `E:\DevCache\temp\aiming-cookie-task4-normal-20260719-130214\validation-summary.json`。该结果只关闭 normal Challenge、`>300s` fail-closed 与既有诊断 policy 行；timescale、短暂停、Restart、真实 adapter failure、AMD/Intel、Capture Coordinator 和 Run storage 仍未验证。

2026-07-19 Hardware replay backpressure repair Gate（passed；Task 4 Challenge matrix pending）：

- 先以 inline tests 固定两条合同：165 Hz WGC 输入每秒只保留 60 个 frame 且 derived encoded PTS 等间隔；MFT 没有 `NeedInput` permit 时 worker 不从有界 channel 取走 frame。实现保持 `FrameSample.system_relative_time_100ns` 为真实 WGC source timestamp，只把 derived PTS 交给编码器；hardware loop 使用 1 ms 非忙等轮询，每个 permit 最多提交一帧，只有 producer channel 满才计 backpressure。
- focused MSVC Rust tests 为 `28 passed, 6 ignored`；`cargo check --locked --lib` 和 focused clippy `-D warnings` 通过。显式 synthetic hardware smoke 收齐 `120 / 120` packets、`0` dropped packet、`0` coverage gap；synthetic replay MP4 smoke 同样为 `120` packets、`0` gap，FFprobe/边界色检查通过。
- 真实 KovaaK `PID=30056`、`HWND=15599888`、NVIDIA `vendor:10de;device:2560` 空闲窗口 capture 的 encoded timeline 为 `142.849s`：WGC 元数据 `23554` 帧，H.264 packet `8572`（约 `60.007 FPS`），`0` dropped packet、`0` encoder error、`0` metadata drop；末 packet derived PTS 比对应末 WGC source PTS 早 `15.5854ms`，未超前或改写 source clock。
- 对 ring 请求最近 10 秒 immutable snapshot 成功：visible duration `10.000s`、decode preroll `0.4666248s`、`628` packet、`9,604,831` encoded bytes、`0` reencoded frame。FFprobe 为 H.264 Constrained Baseline、`1920x1080`、约 `60 FPS`、容器时长 `10.000s`；首尾抽帧均为同一 KovaaK Sandbox Browser，未见黑帧、桌面泄漏或错误裁切。导出后 live producer 从 `6313` 增长到 `8572` packets，证明 export 未停止 capture。
- Raw Input 同期为 ACRI v1 `143` points、`2872` bytes、`0` dropped point、`0` snapshot failure，SHA-256 `906D4ECDF93C5C59073284F7D02E9D4CCD3EBD4F4CC22F0FCA434F821CA10914`。约 `106.221s` 性能样本为 `0.0559` CPU core、`155.93 MiB` peak working set；GPU Video Encode 三次采样平均 `15.9429%`。
- 仓库外证据为 `E:\DevCache\temp\aiming-cookie-hardware-repair-20260719\idle-live\validation-summary.json`；临时 harness 已停止，KovaaK 保持开启。该 Gate 只证明 backpressure 修复和空闲窗口 replay 完整性，不证明 Challenge 对齐、暂停语义、Restart、Run storage 或 AMD/Intel release Gate。

2026-07-19 Hardware Replay Buffer Task 4 Windows field Gate（Stop rule triggered）：

- 点点已打开 KovaaK，但尚未开始任何 Challenge；临时验证 harness 位于仓库外，通过 native coordinator boundary 启动同一 `HWND=15599888` 的 Raw + WGC hardware capture，不修改业务代码。
- NVIDIA adapter `vendor:10de;device:2560`、Media Foundation hardware H.264 Baseline 路径持续运行 `148.683s`：WGC 元数据 `23558` 帧，60 FPS limiter 产生约 `60.000` 次/秒提交尝试，但只产出 `6892` 个 packet（约 `46.347 FPS`），另有 `2030` 次 backpressure drop（`22.75%`）；encoder error 为 `0`，最后失败分类为 `backpressure`。
- 对 ring 最近约 5 秒请求 immutable replay snapshot，返回 `coverageGap: replay snapshot failed: CoverageGap`，未创建 MP4。该结果已满足 Task 4 “任一来源缺少完整 `[start,end)` coverage 即停止”的条件，因此没有让点点开始普通局，也没有继续 timescale、短暂停、Restart、超 300 秒、finalization 后 KovaaK 保持开启或 adapter failure 矩阵。
- Raw Input 同期保持健康：ACRI v1 snapshot `27212` bytes、`1360` points、跨度 `147699ms`、`0` dropped points、`0` snapshot failures；SHA-256 为 `4B1844CAAB116354DE4EB104C9921D18EFA98658E36CD20BEFE208264FC7491B`。
- 性能采样仍证明 GPU 路径本身轻量：约 `0.0604` CPU core、`152.59 MiB` peak working set、GPU Video Encode 平均 `12.584%` / 最大 `12.72%`、GPU 3D 平均 `0.361%`。问题是 producer/MFT input-permit 节奏造成的完整性失败，不是 CPU-backed 性能回退；不得将低 CPU 误写成 Task 4 通过。
- 证据包保存在仓库外 `E:\DevCache\temp\aiming-cookie-task4-1784430920662\bundle\validation-summary.json`；未经指示未提交、未推送。

2026-07-18 GPU Replay Buffer 决策与暂停证据：

- 点点选择保留“全自动 + 不漏正常开局”，不再把倒计时、HUD 或其它视觉启发式作为正确性来源；自动视频采用持续硬件编码与有界压缩码流回放缓冲。
- 当时冻结的 v1 完整自动采集支持窗口为 `300 秒`墙上时间，包含短暂停；该历史决策已被 2026-07-19 的暂停局 fail-closed 裁决 supersede：`Pause Count > 0` 不生成永久 MP4。超过该窗口、长时间中断或 coverage gap 明确降级，不支持拼接猜测。
- 实盘 Stats `1wall 6targets small - Challenge - 2026.07.18-20.57.21 Stats.csv` 含 `Challenge Start: 20:56:21.971`、`Pause Count: 0`、`Pause Duration: 0`。暂停字段存在已确认，但尚无真实暂停后继续样本验证 Stats / Performance event timestamp 与 WGC/Raw wall timeline；该 Gate 保持未闭合。
- 本机历史 Performance 样本均为 `time_limit=60`；六条 timescale `1.0` 的最后事件约 `59.896–59.988s`，一条 timescale `0.7` 的最后事件为 `85.700s`。当前样本支持 300 秒预算，但不能替代超限 fail-closed 测试。

2026-07-18 WGC Task 3 Media Foundation writer 与真实 MP4 smoke：

- 新增 CPU-backed BGRA readback、Media Foundation H.264 writer、固定 `60 FPS` pre-readback gate、容量 `4` 的独立 writer channel、hardware-transform preference，以及 `capturedFrames` / `writerSubmittedFrames` / `writerDroppedFrames` / `encoderErrors` 分离诊断；writer 在自己的线程内创建和销毁，不跨线程移动 COM 对象。
- focused MSVC capture tests 为 `8 passed, 2 ignored`；合成 MP4 ignored smoke 显式运行通过，Windows Shell 识别为 H.264、`320x240`、`60 FPS`。
- 真实 `Full screen windowed` MP4 smoke：5 秒 `825` 个 WGC 元数据帧、`276` 个 writer submission、writer drop `0`、encoder error `0`；FFprobe 为 H.264/yuv420p、`1920x1080`、`60 FPS`、`301` 帧、`5.01665s`、`3,662,453` bytes。第 2 秒抽帧显示清晰 KovaaK 设置界面，无黑帧、错误裁剪或桌面泄漏。
- recorder process 在同机 smoke 中约使用 `7.688 CPU-seconds / 5.326s = 144.3%`，约 `1.44` 个 CPU 核；手动 `30 FPS` A/B 为 `69.3%`，但默认已按点点裁决恢复 `60 FPS`，且没有自动降帧机制。该结果保留为 DXGI/staging 优化基线，不得写成性能 Gate 已通过。
- 本机通过 winget 安装 FFmpeg `8.1.2` 仅用于开发验证，不是仓库/runtime 依赖。尚未验证 Raw/MP4 correlation、Challenge 切窗、真实 Run storage，亦未验证三种显示模式的完整 MP4 输出。

2026-07-18 WGC Task 2 与真实 KovaaK 窗口 smoke：

- `windows 0.61.3` 直接依赖、WGC HWND producer、D3D11 bridge、`CreateFreeThreaded` frame pool、`FrameArrived` 和 `SystemRelativeTime` 已接入；视频队列有界，满载时丢帧并计数，不阻塞 Raw Input。
- MSVC `cargo check --target x86_64-pc-windows-msvc --lib` 通过；`cargo test --target x86_64-pc-windows-msvc --lib window_capture` 为 `5 passed`；新增文件 rustfmt 通过；`cargo metadata --locked` 与 `git diff --check` 通过。
- 真实窗口 smoke：KovaaK `UnrealWindow` HWND `0xC2081A` 在 `Full screen windowed`、`Full screen`、`Windowed` 三种模式均通过 5 秒探针；帧数分别为 `824`、`825`、`825`，尺寸分别为 `1920x1080`、`1920x1080`、`1922x1112`，首末 `SystemRelativeTime` 均单调且回退为 `0`。`Windowed` 的尺寸包含窗口非客户区，后续 writer/crop 合同必须明确是否只编码客户区。尚未验证 MP4、Raw/MP4 PTS correlation 或 Challenge 切窗。

2026-07-17 自动 Run 采集合同同步（文档-only）：

- 新增 active `2026-07-17-automatic-run-capture-design.md`，并同步 PRD、Architecture、UI/UX、Roadmap、active frontend spec/plan、evidence/deletion lifecycle spec 与索引；
- 跨文档检索确认 readiness、单局/多局选择、待分析 History、Provider skip、Run-owned MP4 和 Storage 手动管理语义均有主责任落点；
- `git diff --check` 通过；14 个 changed Markdown 文件的相对文件链接检查全部通过；
- 未运行代码测试、build 或真实 Desktop/KovaaK 验证，因为本轮没有修改或实现业务代码；自动采集能力继续标记为未实现。

2026-07-16 Windows Desktop pre-frontend Task 1–2 与正式前端转场：

- Steam discovery tests-first 最终 `38 passed, 1 skipped`；本机无 override 解析到 `E:\SteamLibrary\steamapps\common\FPSAimTrainer`，Stats/Performance 均存在，首次 scan 为 `50 / 6`；missing/malformed `libraryfolders.vdf` 对对应 root fail closed；
- launch-token isolation 红基线 `1 failed`，focused `45 passed`；普通 caller env 与 pinned Pi paths 保留，Node fallback child env 不含 `AIMING_COOKIE_DESKTOP_TOKEN`；
- 完整 Windows Python `744 passed, 3 skipped`；Pi AI `473 passed, 733 skipped`；Coach runtime `63 passed`；frontend type-check 与 `4 passed`；Rust MSVC fmt/check/test/clippy 通过，Rust tests `16 passed`；
- frontend `next build` 因 prototype 删除后尚无 `app/` / `pages/` 失败；该失败归类为正式前端启动/构建硬阻断，待点点明确授权相应 frontend Task 后解决，不回退 prototype、不扩大后端审计；
- Luna 最终只读 review 的唯一 finding（VDF 损坏时可能绕过多安装消歧）已修复。Windows Desktop pre-frontend plan 已完成并归档。

2026-07-16 Analysis deletion/reconciliation Task 3 — startup reconciliation 与 API outcome：

- FastAPI lifespan 在 `init_schema()` 后、API ready 前恰好运行一次 tombstone reconciliation；pending/failed workspace cleanup 在启动时幂等重试，普通 Windows workspace `OSError` 保留 tombstone、记录 aggregate counts 与稳定 `workspace_cleanup_failed` code，但不阻止启动；
- tombstone finalize 的意外 SQLite/Schema 错误不再被误分类为 workspace failure：共享连接 rollback 后异常向上传播，lifespan 不进入 ready；日志不含绝对路径、底层异常文本、`OSError` 或 traceback；
- DELETE response envelope 保持 `deleted/id/files_removed/cleanup_failed`；删除后 Analysis detail/video/timeline 为 unavailable，Coach messages/context 保留，wire ref 为 `unavailable`；`desktop_runtime.py` 未重复执行 reconciliation；
- tests-first 红基线 `3 failed, 2 passed`；Task 2+3 合并 focused `131 passed`；完整 Windows Python `719 passed, 3 skipped`；compileall、全仓 `git diff --check` 与 Agent contract byte parity 通过；
- Luna 最终只读 review 无剩余 finding。Analysis deletion/reconciliation implementation plan 已完成并归档，active spec 继续作为长期局部合同。

2026-07-16 Analysis deletion/reconciliation Task 2 — commit-first delete 与幂等 cleanup：

- `queue.delete_session` 的 Phase A 仅在同一 SQLite 事务内迁移 legacy Coach message、标记 Analysis ref deleted、写 pending tombstone 并删除 session/chat；commit 后才清 managed workspace；
- workspace cleanup 的 absent/success、partial/OSError、cleanup 后 crash、tombstone finalize commit failure 与 failed-state 写入 failure/cancellation 均可确定收敛；普通 finalize DB failure 保持 `deleted=true` API 语义，进程级中断传播前清理共享连接事务；
- reconciliation 只处理 tombstone；workspace `OSError` 逐项隔离且幂等，tombstone finalize DB failure 则 fail-closed；Run row、Run-owned trace 和用户 Stats/Performance/source MP4 保持不变；
- tests-first 红基线 `67 passed, 7 failed`；Task 3 集成返工后的 Task 2 focused `79 passed`；最终完整 Windows Python `719 passed, 3 skipped`；compileall、全仓 `git diff --check` 与 Agent contract byte parity 通过；
- Luna 最终只读 review 无剩余 finding。Task 3 已完成，见上方最新验证记录。

2026-07-16 Analysis deletion/reconciliation Task 1 — SQLite v13 transient tombstone：

- schema v12→v13 新增单用途 `analysis_deletion_tombstones`；exact DDL 约束 positive Analysis id、non-empty owner、pending/failed state 与 attempt/error code 一致性，不保存 path，不建 sessions FK、state index 或 completed retention；
- v13 DDL 不进入事务前 `SCHEMA`；fresh 与 v12 upgrade 通过同一 helper 在外层 `BEGIN IMMEDIATE` 中创建 table 并提交 user_version。完整 failure injection 验证 table 创建后异常会同时 rollback table 与 user_version；v13 self-heal 不覆盖既有 tombstone；
- tests-first 红基线 `18 passed, 16 failed`；实现后 focused DB `34 passed`；完整 Windows Python `697 passed, 3 skipped`；compileall、scoped `git diff --check` 与 Agent contract byte parity 通过；
- Luna 最终只读 review 无 finding。Task 2 后续已完成，见上方最新验证记录。

2026-07-16 RefleK Task 6A History / Run read models、evidence replay 与 comparable trends：

- Analysis/Run list 不返回完整 result/summary，且只做 stat availability；detail 才 lazy load summary、evidence locator 与完整 SHA revision。Analysis trace 状态只从 session frozen snapshot 推导，不受 Run 当前状态变化影响；
- `input_native` 固定为 native-only；v2 managed MP4、v1 manifest 与 unversioned legacy read 通过同一 workspace/replay Gate，视频 endpoint 复用该 Gate；视觉 evidence unavailable 不会错误禁用仍完整的 managed MP4 replay；
- comparability 要求 v2、相同 analysis/scenario identity/input mode、完整 deterministic metric/evidence coverage、允许的 alignment、相同 metric version/unit 与显式 calibration；任一 malformed 或缺失均 fail-closed，不输出 delta/percent；
- Luna 最终只读审阅发现并闭合三项边界：公开 source ref 只接受完整 64 位 hex SHA-256，Run detail 缺失/畸形 fingerprint 报 invalid，comparability identity 只接受安全非空字符串与冻结 input mode；
- focused History/Run hardening `66 passed`；Task 6A 扩展 backend 回归此前为 `255 passed`；完整 Windows Python `681 passed, 3 skipped`；Python compileall、app import、`git diff --check` 与 Agent contract byte parity 通过；
- 未运行正式 frontend/Rust/Coach runtime 重验，因为本 Task 未修改对应实现；正式 History UI、真实 Windows KovaaK/Raw Input、高 polling-rate 与真实三模式 E2E 仍未验证。

2026-07-15 RefleK Task 5 Coach diagnostic context：

- canonical context 在 Python projector/runtime、Pi request、TypeScript tool、SQLite message 与 Coach API 使用同一结构；stored context 写读均重投影，legacy message 保留正文但不复制未验证 trace；
- v2 metric/comparison 必须显式 deterministic；v1 legacy metric 可读但 mode 为 `unknown`。stable Analysis ref 校验 version/type/mode/id，并拒绝路径、`file:`、任意 `scheme://`、credential、raw sample/timestamp、payload、heuristic、Benchmark 与 duplicate JSON key；
- Analysis 删除后内部 ref 保持 `deleted`，Coach wire 为 `unavailable`，历史消息/context 保留且不可重新附加；Primary POST 返回 context 与实际存储一致；
- Task 5 focused Python `131 passed`；完整 Windows Python `635 passed, 3 skipped`；Coach runtime `63 passed`；strict TypeScript、Biome、Python compileall 与 `git diff --check` 通过；
- 范围外残余：`queue.delete_session` 在 DB commit 前删除 workspace，尚无 tombstone/reconciliation；已转入下一轮数据可靠性，不将其描述为删除可靠性已完整闭合。

2026-07-15 RefleK Task 4 worker mode dispatch：

- tests-first 补齐 queued 后 managed MP4 缺失、截断、同尺寸替换、CV 期间变化、缺 frozen identity、source/managed mtime 差异、input-native 忽略视频、multimodal 成功/失败语义、fallback 路径安全和 v1/unversioned legacy read；
- run-based multimodal / video-fallback 以线程内流式 SHA-256 + size 做 pre/post/final 校验；fallback 在 provider lookup / report 前完成 post-check，避免对已变更视频产生外部请求或费用；普通 decoder/CV 异常只记录异常类型，不泄露本地路径；
- `webapp/tests/test_worker.py` 为 `42 passed`；worker/contracts/queue/routes 相邻回归 `126 passed`；MP4 freeze/copy 定向回归 `6 passed`；
- 完整 Windows Python 仓库 `610 passed, 3 skipped`，`compileall` 与 `git diff --check` 通过；
- 残余风险是 path-based CV 重复打开固有的 change-and-restore TOCTOU；当前 app-managed workspace 加 pre/post/final hash 已满足 Task 4 合同，更强字节级绑定需要改变 CV 输入边界并超出本 Task。

2026-07-15 Windows developer/runtime compatibility Task 1–2：

- Python：`.venv/Scripts/python.exe -m pytest -q` 为 `591 passed, 3 skipped`；`compileall` 通过；
- Coach runtime：显式使用 Windows `PYTHON_BIN`、绝对 `PI_SOURCE_DIR`、pinned `TSX_TSCONFIG_PATH` 与 loader file URL，`59 passed`；不依赖或生成 Pi `dist`；
- Pi `packages/ai`：`69` 个 test files passed、`25` skipped，`473 passed, 733 skipped`；npm 仅报告 `min-release-age` 将在下一主版本停止支持的非阻断 warning；未运行非产品边界的 AgentHarness、coding-agent、filesystem/shell 套件；
- frontend：`npm.cmd run type-check` 通过，adapter tests `4 passed`；`npm.cmd run build` 因不存在 `app/` 或 `pages/` route 而 exit `1`，按冻结决策记录为正式 frontend 未闭合，不伪装为 Windows Gate 通过；
- Tauri MSVC：`fmt --check`、`check --locked --all-targets`、`clippy --locked --all-targets -- -D warnings` 通过，Rust tests `16 passed`；test link 阶段有一条来自本地化 MSVC stdout“正在创建库”的非阻断 `linker_messages` warning；
- 仓库检查：active plan/spec index 链接与目录登记一致，`AGENTS.md` / `CLAUDE.md` 逐字节一致；GUI、真实 KovaaK、Raw Input、高 polling-rate、正式 installer/签名/updater 均未运行或未闭合。

2026-07-15 correctness review 增量闭环：

- frozen Analysis source 消失、不可读或 revision 变化不再落入 `internal_unknown / analysis_failed`；worker 现在写入不可重试的 `input_validation / source_unavailable`，错误对象和普通日志均不包含底层异常中的本地绝对路径；
- run-based path import 现在先冻结 MP4 SHA-256 / size / mtime，再验证 Analysis managed copy；源内容/mtime 变化、复制前消失与同一路径换版复用 idempotency key 均 fail-closed，result snapshot 和 artifact manifest 保留 fingerprint/checksum，audit/result 不包含原始绝对路径；
- app-owned SQLite connection 现在显式启用 `PRAGMA foreign_keys=ON`；此前 v10–v12 DDL 中 Provider profile/credential、Training Plan/version/transition 的外键只是声明但未执行，现已增加 orphan child 写入失败回归；v11/v12 multi-statement migration helper 也不再通过 `executescript()` 隐式提交调用方事务，rollback 回归确认失败不会留下半创建 schema；
- Training Plan `diagnostic_context` 的各 ref list 现在强制匹配 analysis/metric/diagnosis/prescription/knowledge/evidence kind，版本级 `evidence_refs` 只接受冻结的 Analysis/metric/knowledge 依据，不再允许语义错位 ref 入库；
- Coach command journal 不再允许 stale lookup 后的 upsert 用同 key 不同 digest 覆盖既有 reservation/result；同 digest 的后到 reservation claimant 现在 replay 已有 unknown/final 结果而不重复执行副作用；普通 digest 冲突返回稳定 `idempotency_conflict`，confirmed reservation 冲突在同一 SQLite 事务内回滚 confirmation 消费；
- Pi product-command tool 与 Python product-command boundary 的路径/URL 防线不再只检查字符串开头；嵌在普通文本中的 `https://...`、POSIX/Windows/UNC 路径现在在 backend dispatch 前和 tool result 入模前同时 fail-closed；
- Provider profile status 现在把 pinned Pi `ModelsError("auth"|"oauth")` 映射为 `needs_reauth`，保留安全的 profile/model 投影并继续 redaction credential，不再误报不可重试的 `unconfigured/profile_status_failed`；
- custom OpenAI-compatible profile 的 canonical store 现在拒绝带 URL userinfo 的 `base_url`；`https://user:secret@host/...` 不再被接受、持久化或通过公开 profile API 回显；
- OAuth refresh 失败写入 `needs_reauth` 现在绑定 operation 启动时的 credential revision，并在 Provider store write transaction 内条件更新；旧 refresh 失败不能再把用户随后替换的新 API key 错误标成需要重新认证；
- source-error 定向回归：`webapp/tests/test_worker.py`、`tests/test_native_flicking_analysis.py`、`webapp/tests/test_queue.py`、`webapp/tests/test_routes.py` 共 `109 passed`；MP4 correction 相关 `webapp/tests/test_kovaak_runs.py`、`webapp/tests/test_coach_commands.py`、`webapp/tests/test_worker.py`、`webapp/tests/test_queue.py`、`webapp/tests/test_routes.py` 共 `134 passed`。
- SQLite relation / migration correction 相关 `webapp/tests/test_db.py`、Provider store/auth/routes、Training Plan store、Coach command 与 queue 回归共 `118 passed`。
- Training Plan typed-ref correction 相关 store、Coach command/tool runtime 与 Coach routes 回归共 `72 passed`。
- Coach command journal、Python embedded path/URL、confirmation rollback、DB migration 与 Coach route/runtime 定向回归共 `90 passed`。
- Pi product-command 嵌入式 path/URL 边界定向回归 `5 passed`。
- Pi Provider catalog/model/profile/auth status 定向回归 `14 passed`。
- Provider store/auth/routes 与相邻 Coach route 定向回归 `66 passed`。
- 当前完整 Python 仓库 `589 passed, 3 skipped`，`compileall` 通过；frontend `npm run type-check`、`npm test`（`4 passed`）和 `npm run build` 通过，正式路由仍按已批准重建顺序只生成 `/404`；
- Coach runtime 全量 `59 passed`，strict TypeScript 通过；其中 sidecar HTTP tests 在受限沙箱内监听 `127.0.0.1` 会得到 `EPERM`，沙箱外复跑全部通过；
- Rust `cargo fmt --check`、`cargo check --locked --all-targets`、`cargo clippy --locked --all-targets -- -D warnings` 与 `cargo test` 通过（`18 passed`）；descendant process inspection 同样需在沙箱外复跑；
- 全仓 `git diff --check`、项目文档本地链接、active plan/spec 索引一致性、`AGENTS.md` / `CLAUDE.md` byte parity 均通过。

2026-07-14 RefleK Task 2–3 当前平台收口：

- Task 2：真实 `analysis_result.v2` producer 补齐 owner/local profile、analysis version、evidence source/version/coverage、metric provenance/version/limitations，以及 artifact ownership/managed/local-only/status/format；queue 在写入 terminal result 前验证 session owner、完整 producer metadata，以及 `analysis_id` / `analysis_type` / `input_mode` / `kovaak_run_ref` 与已 claim request 一致；旧未版本化 v2 继续按 legacy draft 读取，但不能新写为 terminal result；
- Task 3：Analysis input snapshot 现在冻结 Stats、Performance 与 Raw trace 的 SHA-256 / size / mtime revision；worker 在解析前重新计算并逐一比较，文件被修改、替换、删除、不可读或旧 trace 缺少 fingerprint 时 fail-closed，不静默使用另一份数据；SPARC 对选中频段做 frequency-span normalization，native/video-fallback 写独立 v2 metric version，避免采样间隔污染与跨公式趋势混用；
- focused：Task 3 相关 `71 passed`；相邻 contracts/queue/routes/worker 回归 `97 passed`；
- Python 完整仓库：`522 passed, 3 skipped`；Python compileall 与 `git diff --check` 通过；
- 未运行正式 frontend/Rust 重验，因为本轮未修改对应实现；Windows Raw Input 实机、高 polling-rate、真实 KovaaK 三模式和 release license review 仍未验证。

2026-07-14 Versioned Coach Knowledge Registry 最终验收：

- Registry：43 条 canonical entries、19 个 canonical signals、10 个 category；37 Python chunks + 19 legacy signals + 11 TS seeds 共 67 条 migration audit 完整；
- Python：完整仓库 `509 passed, 3 skipped`；包括 Registry validator/retrieval、legacy adapters、trace fail-closed 与真实 Analysis → Pi Knowledge → SQLite refs-only E2E；
- Coach runtime：`56 passed`；Python/TypeScript query parity、v1 bridge 解耦、最多三条、无全库 fallback、safe event refs 与 sidecar loopback 全部通过；
- strict TypeScript、`git diff --check` 与 `AGENTS.md` / `CLAUDE.md` byte parity 通过；正式前端、Windows 实机与真实素材三模式 Gate 不在本计划范围，仍未验证。
- 2026-07-15 correctness correction：原 `schema.v1.json` 的 entry `additionalProperties: false` 未声明任何 entry properties，标准 validator 会拒绝所有合法 Registry entry；现已补齐 Draft 2020-12 properties/enum/length/uniqueness/source-claim condition，并新增 canonical Registry + fixture 标准验证与非法 shape 回归。

2026-07-14 Task 5 主线程验收后记录：

- Python：完整仓库 `466 passed, 3 skipped`；新增覆盖 SQLite Training Plan lifecycle/version、共享产品命令、owner、确认、并发幂等、append-only audit、bridge 生命周期与 tool trace secret sentinel；
- Frontend：prototype 删除并清理旧 `.next` 后，`npm run type-check` 通过，`npm test` 为 `3 passed`；`next build --webpack` 通过且只生成自动 `/404`，当前无正式产品路由；
- Coach runtime：`48 passed`；严格 TypeScript 检查通过；knowledge progressive retrieval、精确三工具 registry、非法 analysis context fail-closed、固定 loopback bridge、禁止 authority/path/URL/credential/raw payload 与 bridge secret redaction 已覆盖；真实 TS product tool → loopback HTTP → Python shared command → SQLite audit 联调通过；
- Rust：`cargo fmt --check`、`cargo check --locked --all-targets`、`cargo clippy --locked --all-targets -- -D warnings` 通过；`cargo test` 为 `15 passed, 1 failed`，失败点是受限环境的 descendant process inspection `PermissionDenied`；
- Windows target condition check 仍被缺少 `icons/icon.ico` 阻塞；Windows Raw Input 实机、真实三模式 E2E 与高 polling-rate 性能仍未验证。

Desktop vertical slice 的历史回归、commit 和进程审计保存在：

- [`archive/history/PROGRESS-2026-07-12-desktop-slice.md`](archive/history/PROGRESS-2026-07-12-desktop-slice.md)

2026-06-27 至 2026-07-10 的历史流水保存在：

- [`archive/history/PROGRESS-2026-06-27-to-2026-07-10.md`](archive/history/PROGRESS-2026-06-27-to-2026-07-10.md)
