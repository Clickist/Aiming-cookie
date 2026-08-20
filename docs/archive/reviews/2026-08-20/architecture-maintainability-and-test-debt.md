# 2026-08-20 架构可维护性与测试债评审

> **状态：只读评审快照，非当前合同。** 本文记录 2026-08-20 对当前代码的真实架构、耦合热点与测试债的核查结论。产品范围以 `PRD.md`、系统合同以 `ARCHITECTURE.md`、当前状态以 `PROGRESS.md` 为准。本文不构成实施授权，修复决策由点点单独批准。

## 1. 范围与方法

- 范围：`webapp/backend/`、`webapp/coach-runtime/`、`webapp/frontend/`、`kovaak_tracker/`、`webapp/frontend/src-tauri/`，对照 `docs/ARCHITECTURE.md` 与 `docs/DEVELOPMENT.md`。
- 方法：并行只读 scout 分域核查 + 主会话对关键证据逐条重读代码核实。所有行号均为核查当日事实，不是「应该是什么」。
- 已排除：2026-08-20 之前已修复并在前一批提交的项（Coach 丢回复、raw 坏 buffer 隔离+monitor 健康重启、CV 假设集 grab/retrieve、run 列表批量 count、process_one 不再每 job recover）。

## 2. 真实运行时拓扑（与文档对照）

```text
Tauri
  ├─ Capture Coordinator / Raw Input / WGC replay buffer（Rust）
  ├─ Python Analysis runtime（loopback FastAPI + worker，同进程）
  │    Run    = runs/{id}/meta.json + run-owned evidence（kovaak_run_store）
  │    Session= sessions/{id}.json（queue 持有 lease/claim/terminal result）
  │    渐进披露 = analyses/{session_id}/（analysis_output）
  │    CV 在 visual_worker_process 子进程隔离执行
  └─ Node Coach sidecar（Pi AgentHarness，loopback）
       直连 UI；read/ls/write @ app-data
       product commands 大多 native；仅创建分析出站打 Python
       会话 = conversations/--coach--/*.jsonl（Pi JsonlSessionRepo）
```

结论：五职责域边界（Domain Core / Local Analysis Runtime / Coach Agent Runtime / Client Surfaces / Online Surfaces）与 `ARCHITECTURE.md` §1.1 基本一致，本轮文档治理已把 §5 Coach 合同（受限 fs 工具、知识物化检索）对齐到实现。

## 3. 可维护性债（有代码证据，未修复）

### 3.1 worker 神模块 + 环依赖（最高优先）

- `worker.py` 3416 行、`kovaak_run_store.py` 2138 行、`routes.py` 1219 行、`visual_signals.py` 3803 行。
- `worker_family_analysis.py:162/303` 与 `visual_worker_process.py:75/84/129` 反向 `from . import worker` / `from .worker import _parse_frozen_stats_for_visual, run_visual_preprocessing`——Domain/Runtime 依赖方向要求单向，实际是环。
- 现象：拆分文件后仍然互相调用 `_` 私有符号，等于「拆文件没拆边界」。

### 3.2 store 门面穿墙

- `analysis_service.py:93` 调 `kovaak_run_store._get_analysis_count_for_run`；`queue.py` / `analysis_output.py` 直调 `run_store._load_run`、`file_store._sanitize`、`evidence_store._artifact_file` 等私有符号（BackendArchScout 记录）。
- `kovaak_run_store.py` 再导出 projection/evidence/codec 私有符号，projection 模块又回调 run_store `_*`——模块边界模糊，伪拆分。

### 3.3 Domain 私有符号被 Runtime 直接 import

- `analysis_service.py:261-264`：`from kovaak_tracker.scenario_profiles import _FAMILY_BASELINE_LIMITATIONS, _family_baseline_resolution`。Domain 稳定公开面被穿透。

### 3.4 前端手写镜像 schema

- `lib/types.ts` 1294 行、`lib/contracts.ts` 984 行，手写镜像 Python `analysis_result.v2` / session / run 形状；`lib/api.ts` 全量 `(await res.json()) as T`，无运行时校验。后端 schema 变更时漂移无保护。
- `lib/contracts.ts` 内嵌英文后端字符串 → 中文呈现映射（DIAGNOSIS/LIMITATION 等），与 `metric_definitions.py` 单事实源原则存在双源风险。

### 3.5 Coach 确认面是假的

- `agent-runs.ts:559-566`：`decideConfirmation` 恒 `return null`（注释「Confirmations are not used in the file-based architecture」）。
- 但 HTTP 路由 `/v1/confirmations/:ref/decision` 与前端 `CoachConfirmationV1` 类型仍存在；`contracts.ts:13-16` 的 `FORBIDDEN_TOOL_NAMES` 含 `read/write` 却全仓零引用。
- `ARCHITECTURE.md` §5.1 推断性 consequential 操作需确认的合同仍在——实现与合同差距已记入 PROGRESS，未改合同。

### 3.6 残留死路径

- `tool_bridge.v1` 仍在 turn 合同与 validateBridge 中，产品路径不传 bridge（agent-runs 路径 bridge=null），bridge 未覆盖的命令直接失败——死分支误导。
- `contracts.ts` `FORBIDDEN_TOOL_NAMES`（含 read/write）与实装 fs 工具矛盾，零引用。

## 4. 测试债（有代码证据）

### 4.1 source 断言规模

- 前端 `*-source` 系列实测 **644 条** `assert.match|doesNotMatch`（`task6` 199、`task3` 135、`task5` 133、`task7` 46、`task4` 35 等，2026-08-20 `grep -c` 实计）。
- 模式：本地复制 `async function source()` ×9 文件；钉 JSX 标识符、CSS 字面、中文文案、精确选择器、负向死符号（`CoachSidebar`）。

### 4.2 最死板文件

1. `task6-source.test.ts` ~199 钉：Coach/Settings/CSS/文案/fixture 自测
2. `task5-source.test.ts` ~133 钉：独立 Analysis 页组件——路由已 `redirect("/history")`（`app/analysis/*/page.tsx`），组件未挂载，测的是卸下来的实现
3. `task3-source.test.ts` ~135 钉：AppShell/onboarding 实现细节
4. `coach-runtime/test/teaching-policy.test.ts`：整份 TeachingTurnContract 合同，产品路径已是 `teaching/session.json`
5. `coach-runtime/test/knowledge-registry.test.ts`：锁死 v1–v5 历史 registry 正文，现物化路径 v8

### 4.3 合同已变仍在测旧 IA

- `analysis-auto-teach.test.ts:53-56` 钉 AnalysisWorkspace 活体 done→event；主路径是 AppShell 列表转换。
- `task4-source.test.ts:45` 正断言 `data-coach-open="true"` CSS，而 `task3-source` 禁 `data-coach-open`、AppShell 无该属性——测死 CSS。
- `e2e/interaction-polish.spec.ts` 仍 `goto /analysis/42` + coach-open localStorage（旧 IA）。
- `test_worker.py:2153+` 多处仍在 process_one 场景 patch `recover_stale_jobs`——样板耦合（合同本身已对齐，见 `:1167`）。
- `test_capability_contracts.py:720-773` 仍测 `/api/tasks`，前端已把 Tasks 页 redirect。

## 5. 修复优先级建议（未执行）

1. 断 `worker` ↔ `worker_family_analysis` ↔ `visual_worker_process` 环：公开一组 child-process/family 的稳定 API，禁止 `_` 私有 import。
2. 收 `kovaak_run_store` 门面：projection/evidence/codec 私有符号收敛，调用方走公开函数。
3. 前端：删已下线 Analysis 页相关镜像（task5 页级）；schema 漂移先加启动期/CI 契约校验，再考虑代码生成；不一步到位。
4. Coach：删 `/v1/confirmations` 假面与 `FORBIDDEN_TOOL_NAMES`，或恢复确认实现——两者择一，不留双轨。
5. 测试：source 测试只留路由/安全负向；UI 改行为测试；删 task5-source 整页、TeachingTurn 双轨、knowledge 历史 registry 缩成 migrate 烟测。

## 6. 已在本评审同批完成的文档治理（独立提交）

- 归档 `2026-08-13-architecture-rewrite.md` → `archive/completed/plans/`；`docs/README.md` 不再把 ARCHITECTURE 标成历史参考。
- `ARCHITECTURE.md` §5 对齐：受限 app-data 文件工具、知识物化 index 检索、受管状态走 product command。
- `DEVELOPMENT.md` 代码入口补 `webapp/coach-runtime/`、`knowledge/`、`analysis_output.py`、`capture_coordinator.rs`，删除不存在的 `provider_store.py`。
- `PROGRESS.md` 记 2026-08-20 文档快照；未改 PRD，PROGRESS「安装前 Stats 不导入」与 ARCHITECTURE/ROADMAP「可导入为历史 Run」的产品冲突未在本评审重开。
