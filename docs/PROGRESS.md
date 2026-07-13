# Aiming Cookie 当前进度

> **最后整理：2026-07-13。** 本文是当前快照，不是产品或架构事实源。详细研发流水见 [`archive/history/`](archive/history/)；产品、架构与 UI/UX 结论分别以 [`PRD.md`](PRD.md)、[`ARCHITECTURE.md`](ARCHITECTURE.md) 和 [`frontend-uiux-design.md`](frontend-uiux-design.md) 为准。

## 1. 当前结论

- 产品方向仍是 Desktop-first、flicking 先行、确定性诊断主路径、Coach 为可选长期关系层。
- KovaaK Run 自动发现、Stats / Performance 解析、Windows Raw Input、AnalysisResult v2、History read model、Coach diagnostic context 与本地 Benchmark store 已形成不同成熟度的代码基础。
- **input-native 当前只能作为 Preview / Experimental 能力。** 现有 adapter 已接入 worker 和 v2 结果，但尚未完成正式 flick segmentation、核心 fair metrics、高 polling-rate correctness 与 Windows 实机 Gate，不得描述为可发布的完整输入原生诊断。
- multimodal 冻结为“native 结果是主事实、MP4 是可选视觉校验”；视觉校验失败时保留 native 结果并显示视觉证据不可用。
- Benchmark 后端能力保留，但不进入 v1 正式前端、不进入默认 Coach context、不启用在线 provider 或 leaderboard。
- Frontend reconstruction Task 1 已在点点确认精确范围后删除 History / Run / Evidence prototype、临时 App shell 与旧全局样式；当前只保留 capability adapters 与 Tauri/runtime，正式产品路由暂时不可用。
- 当前发布状态仍为 **No-Go**。
- 当前 active implementation plans 为 [`superpowers/plans/2026-07-13-reflek-capability-adoption.md`](superpowers/plans/2026-07-13-reflek-capability-adoption.md) 与 [`superpowers/plans/2026-07-13-frontend-product-reconstruction.md`](superpowers/plans/2026-07-13-frontend-product-reconstruction.md)。Frontend Task 1 已完成，Task 2–7 尚未获得执行授权。

## 2. 能力成熟度

### 2.1 Implemented foundation（代码已有）

- Tauri 2 Desktop shell、本地 Python API/worker 生命周期、动态 loopback 地址与 launch-scoped Desktop token；
- native MP4/CSV path import、managed App Data workspace、storage accounting 与 terminal Analysis 删除基础；
- FastAPI queue、worker recovery、workspace/streaming upload、health/readiness；
- 既有视频 + Stats flicking CV、诊断、处方和报告领域逻辑；
- Pi-based Coach runtime、sidecar、服务编排与持久化基础；
- `.perf` protobuf-wire parser、KovaaK Stats / Performance watcher、稳定文件判断与 Run upsert；
- SQLite `kovaak_runs`、Windows-only Raw Input、versioned snapshot 与 trace window extraction；
- AnalysisResult v2、三种 input mode dispatch、History/Run read models、Coach allow-list projection 与 Benchmark local store。

### 2.2 Contracted capability（产品/spec 已冻结）

- Run 独立于 Analysis；Stats、Performance、Raw Input、MP4 是不同 evidence source；
- input-native、multimodal、video-fallback 必须显式表达 mode、availability、alignment 和 limitations；
- Raw Input 默认关闭、仅 Windows、仅 KovaaK process gate、本地保留、默认不进入 Coach；
- History 采用轻列表 + lazy detail；Coach 只读取结构化、用户可见的 diagnostic context；
- terminal Analysis、Run metadata、Run-owned trace、用户 Stats/Performance 源文件和 Analysis-managed MP4 副本具有不同 ownership 与删除影响。

### 2.3 User-reachable / release-ready（尚未完成）

- 原 prototype 已删除；当前没有正式用户路径，不能作为视觉或产品验收证据；
- 正式 App shell、New Analysis、Tasks、History、Analysis workspace、Coach sidebar、Settings 尚待按新 frontend spec/plan 重建；
- input-native、multimodal、video-fallback 三条真实 Browser/Desktop E2E 未形成；
- Windows 实机、高 polling-rate 鼠标、真实 KovaaK 对齐与性能尚未通过发布 Gate；
- installer、正式 icon、签名、公证、updater 和可信云服务仍未完成。

## 3. 当前高优先级实现差距

### Analysis / evidence

1. native adapter 尚未完成 flick segmentation、核心 fair metrics 与正式 angular/physical trajectory；
2. History trend 的 scenario identity / calibration / metric contract 与真实结果仍不闭合；
3. v2 validator 和 queue terminal write 尚未完整落实 evidence 与 artifact ownership 语义。

### Reliability / lifecycle

1. 同 stem 同类型 KovaaK 文件冲突、并发 trace attach、partial import 和 orphan recovery 仍需更强状态机与测试；
2. Run / trace / source / Analysis 的删除 UI、tombstone 与长期 retention 尚未完成完整产品放行；
3. Python runtime READY 后崩溃、launch token 子进程隔离和正式浏览器 media identity 仍未闭合。

### Frontend reconstruction

1. Prototype 已删除，不得恢复；正式产品路由等待后续 Task 重建；
2. 正式路由、低保真结构、页面状态矩阵、Desktop/Web capability 表和 accessibility Gate 已由 UI/UX 与 active reconstruction spec 冻结，但尚未实现；
3. executable token/theme/primitives 尚未建立；
4. Benchmark 不进入 v1 正式前端；
5. Task 1 已完成 prototype 删除与 adapter 边界保护；Task 2–7 必须继续按 active frontend plan 逐个授权。

## 4. 下一步

1. 此前前端文档 Gate 已完成：UI/UX、视觉/设计系统、active reconstruction spec 与 active plan 已对齐；该文档治理轮没有删除或修改业务代码，后续 RefleK 工作树改动另见 §5；
2. Frontend reconstruction Task 1 已完成；`lib/api.ts`、`lib/types.ts`、`lib/contracts.ts`、`lib/csv.ts`、`lib/desktop.ts` 与 `src-tauri/**` 已保留并由 boundary test 保护；下一步需由点点明确指定 Task 2；
3. AnalysisResult v2 path-safety、无 Run fallback envelope 和 native 时间采样阻塞已修复；其余 Analysis/evidence correctness 继续通过 RefleK active plan 的明确 Task 处理，input-native 在 Gate 通过前保持 Preview / Experimental；
4. Frontend Task 2–7 逐个建立 executable tokens、正式页面和 E2E，不从 prototype 继承 IA 或视觉；
5. 以 Browser + Tauri 真实流程、宽/中/窄截图和 accessibility checklist 作为前端放行标准。

## 5. 当前工作树与实施计划状态

2026-07-13 一致性审计确认：当前能力基线仍是一个**未集成工作树**，不能等同于当前 `HEAD` 或远端可复现状态。

- `main` 比 `origin/main` 超前 21 个本地提交；Task 1 完成后仍有 80 个 tracked changes、其中 30 个删除，以及 50 个 untracked status entries；
- `native_flicking_analysis.py`、KovaaK ingestion/run store、Raw Input、Coach context、History trends、Benchmark store、active specs/plans 等关键文件仍包含未跟踪内容；
- 当前测试结果证明的是该工作树，不证明 clean checkout、当前 `HEAD` 或 `origin/main` 已包含这些能力；
- `.firecrawl/`、临时脚本、理论草稿、style pack、`output/` 运行产物与产品代码必须在后续 review/commit 中分开处理；本轮未清理、覆盖或重置任何现有改动。

RefleK active plan 当前成熟度：

| Task | 当前状态 | 未闭合验收 |
|---|---|---|
| Task 1 Raw Input / ingestion | current-platform foundation 已实现 | Windows 实机、完整 Windows target 与高 polling-rate Gate 未通过 |
| Task 2 AnalysisResult v2 contract | 本轮阻塞已修复 | 合法 path metric 可通过；无 Run 的 v2 可省略 `kovaak_run_ref`；更完整 evidence/artifact ownership 验证仍待后续 Task |
| Task 3 input-native adapter | Preview correctness 前进 | 同毫秒记录不再从派生时间指标丢失，非均匀采样改为 duration-weighted；flick segmentation、核心 fair metrics 与正式 angular/physical trajectory 仍未完成 |
| Task 4 worker mode dispatch | 本轮合同已闭合 | input-native、multimodal、Run-based 与无 Run video-fallback 新结果均写 v2；旧 v1 仍可读取 |
| Task 5 Coach diagnostic context | foundation 已实现并有 allow-list 测试 | 正式前端 Coach sidebar 与真实产品 E2E 未完成 |
| Task 6 History / evidence replay | backend/read model + prototype 已实现 | 正式 History/workspace、完整 comparability contract 与真实 replay E2E 未完成 |
| Task 7 Benchmark local domain | local store + prototype UI 已实现 | 不进入 v1 正式前端、默认 Coach 或在线 provider；正式产品化仍 deferred |

Frontend reconstruction plan 已 active。Task 1 已在点点确认 10 个文件的精确范围后完成；inventory 为 `webapp/frontend/prototype-inventory.json`，adapter boundary test 为 `webapp/frontend/lib/prototype-boundary.test.ts`。Task 2–7 尚未授权。

## 6. 最近验证记录

2026-07-13 全量 review 后记录：

- Python：`373 passed, 3 skipped`；
- Frontend：prototype 删除并清理旧 `.next` 后，`npm run type-check` 通过，`npm test` 为 `3 passed`；`next build --webpack` 通过且只生成自动 `/404`，当前无正式产品路由；
- Coach runtime：设置正确 `PI_SOURCE_DIR` 后 `9 passed`；
- Rust：`cargo fmt --check`、`cargo check --locked --all-targets`、`cargo clippy --locked --all-targets -- -D warnings` 通过；`cargo test` 为 `15 passed, 1 failed`，失败点是受限环境的 descendant process inspection `PermissionDenied`；
- Windows target condition check 仍被缺少 `icons/icon.ico` 阻塞；Windows Raw Input 实机、真实三模式 E2E 与高 polling-rate 性能仍未验证。

Desktop vertical slice 的历史回归、commit 和进程审计保存在：

- [`archive/history/PROGRESS-2026-07-12-desktop-slice.md`](archive/history/PROGRESS-2026-07-12-desktop-slice.md)

2026-06-27 至 2026-07-10 的历史流水保存在：

- [`archive/history/PROGRESS-2026-06-27-to-2026-07-10.md`](archive/history/PROGRESS-2026-06-27-to-2026-07-10.md)
