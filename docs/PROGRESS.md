# Flicking 模块进度

> 最后更新：2026-07-12（`coach-startup-ux` Task 1–4 完工 + 全仓回归）

## 当前交付罗盘（2026-07-11）

### 发布裁决

- **长期目标**：`docs/PRD.md` 定义的完整 Desktop hybrid 产品，范围不删减。
- **2026-07-13 至 2026-07-19**：只发布受控环境中的 **flicking-only 内部技术预览**，不称为完整产品 v1。
- **当前状态**：完整产品 v1 `No-Go`；内部技术预览完成 `docs/ROADMAP.md` §5 全部 P0 Gate 后才 `Go`。

### 当前能力基线

**已核验**：

- 全仓单一入口：**274 passed，2 skipped**（2026-07-12 coach-startup-ux Task 4 回归，`.venv`）；
- 分开核验：core **116 passed**；`webapp/tests` **158 passed，2 skipped**（含 `test_coach_runtime_status`、health/auth、browser 骨架 skip、E2E_VIDEO skip）；
- 仓库真实 MP4 + Stats CSV E2E：上传 → enqueue → worker → CV → deterministic report → done 已通过；
- frontend production build 已通过（2026-07-12）；
- **Pi assessment/Spike 已完成**：`spikes/pi-coach-runtime/` 21 tests；assessment **CONDITIONAL GO** 已裁决（`docs/superpowers/assessments/2026-07-11-pi-assessment-decision.md`）。
- **线 A 常驻 Coach 数据归属**：schema v2、`coach_store`、删除不级联抹消息、`/coach` API+页面、相关 pytest — **已完工**（Task 1–5）。
- **线 B Pi Coach runtime 薄切片（Task 1–4 代码 + Task 5 回归）**：
  - `third_party/pi/` vendored（冻结 commit + `PROVENANCE.md`）；
  - `webapp/coach-runtime/` 单轮 Pi turn（产品 system prompt、无 coding tools、Node 测试）；
  - `webapp/backend/coach_runtime.py` subprocess 桥 + `coach_runtime_turn.v0`；
  - `POST /api/coach/primary/messages`（及 session chat 兼容）默认 **`COACH_RUNTIME=pi`**；失败时 **`COACH_RUNTIME_FALLBACK_PYTHON=1`** 回退 `chat_with_coach`；
  - **Sidecar（`2026-07-12-pi-sidecar-and-runtime-hardening.md` Task 1–3）**：loopback HTTP `POST /v0/turn` + `GET /healthz`；`run_pi_coach_turn` 优先 sidecar、可选 subprocess 回退；`./scripts/run-coach-sidecar.sh`；`webapp/coach-runtime/test` **8 passed**（import 边界 + sidecar server）；
  - **未做**：完整云代理账单、Desktop 沙箱、完整 browser E2E release gate；有 key 时 `/coach` 真机一轮仍建议手工记一条（非本 Task 强制）。
- **Coach 结构加固（`2026-07-12-coach-structure-hardening.md` Task 1–3 + Task 4 回归）**：
  - schema **v3**：`legacy_chat_message_id`、单一 `migrate_session_legacy_messages`、幂等迁移；
  - `delete_session`：**单事务**（lock → migrate → mark refs → delete rows；文件删除仍在 commit 后）；
  - **`coach_engine.py` + `coach_service.py`**：Pi/Python 引擎与一轮编排迁出 routes；`COACH_RUNTIME` + fallback 行为与现测一致；
  - **`routes.py` 约 572 行**（coach 大段 if pi/python 已搬出；目标 <600 已达成）。
- **Session workspace + 流式上传（`2026-07-12-session-workspace-streaming-upload.md` Task 1–3）**：
  - `{DATA_ROOT}/sessions/{id}/` 工作区；`delete_session` commit 后 `rmtree`；
  - `/api/analyze` 分块流式写入，超限中止并清理 session + 目录；
  - `MIN_FREE_DISK_BYTES`（默认 500MB，`MIN_FREE_DISK_BYTES` env）不足时上传 **507** 明确文案，不入队。

- **预览可运营（`2026-07-12-preview-ops-health-identity-e2e.md` Task 1–4）**：
  - `GET /healthz`、`GET /readyz`（DB + 已配置 sidecar）；
  - `TRUST_PROXY_USER=1` 时只信 `X-Forwarded-User` / `Remote-User`（`webapp/backend/auth.py` + pytest）；
  - `webapp/tests/test_browser_smoke.py` Playwright 骨架（无 playwright/Chromium 时 skip；可选 `BROWSER_E2E_FRONTEND_URL`）；
  - `docs/deployment-guide.md` §5.1 探活/身份/sidecar 说明。

**当前阻塞**（预览仍 No-Go）：

1. 无自动 TTL、完整 quota/orphan 治理与主动删除产品化仍缺（workspace + 流式上传 + 删目录 + 低磁盘门槛已落地）；
2. 预览/生产需 `TRUST_PROXY_USER=1` + 可信反代才成立；本地 dev 仍可用 `X-User-Id`；
3. 无 supervisor、structured logs；browser E2E 仅有骨架（未装 Playwright 时 skip），非完整 release gate；
4. Desktop 只有研究，无 shell、sidecar、IPC、installer、签名或更新工程；
5. 有 LLM key 时 `/coach` 真机一轮 **未在本 Task 强制验收**（无 key 不阻塞；有 key 建议手工记一条到本文件）。

### 当前执行顺序

1. 线 A / 线 B 薄切片 / 结构加固 — **已完成**。
2. **当前队列（点点 2026-07-12 全做）**：
   1. `2026-07-12-pi-sidecar-and-runtime-hardening.md` — **completed**
   2. `2026-07-12-session-workspace-streaming-upload.md` — **completed**
   3. `2026-07-12-preview-ops-health-identity-e2e.md` — **completed**
   4. `2026-07-12-coach-startup-ux.md` — **completed**
3. **P1 / P2**：见 Roadmap。

### 2026-07-12 交接

- **Sidecar 白话**：挂在主服务旁边的常驻 Node 小助手，里面已经加载好 Pi；聊天时喊它，而不是每轮重新启动 Node。
- **Sidecar 手工验收（有 LLM key）**：`./scripts/run-coach-sidecar.sh` → 设 `COACH_SIDECAR_URL` → API `/coach` 一轮，确认无每轮冷启动 node（日志/耗时）。
- **本地 dev 推荐**：`./scripts/dev-up.sh`（sidecar + API）；另开终端 worker + `cd webapp/frontend && npm run dev`。
- **coach-startup-ux**：**completed**（runtime-status API + UI 横幅）。
- **Browser 骨架（可选）**：`pip install playwright && playwright install chromium` → `pytest webapp/tests/test_browser_smoke.py -v`。

### 执行模型分级

- **可交给较弱模型（已有精确 plan 时）**：pytest collection、health/readiness、小范围回归测试、已冻结规则后的默认路由或 UI 空状态。
- **可施工但必须由强模型先冻结合同**：session workspace、streaming upload、artifact 删除、无自动 TTL/orphan/quota、structured logging、browser E2E。
- **仅强模型裁决/规划**：Pi 源码纳入与产品化删改边界、Coach schema/migration/旧数据兼容、可信 owner 边界、quota/低磁盘阈值、Web/Desktop 路线。

Fast/executor 只能执行已批准 plan 的一个明确 Task。开工前必须回显 Task、Allowed files、Tests first、Frozen decisions 和 Stop rule；代码与 plan 不一致、需要扩大范围、测试不能运行或出现新 schema/default/migration 决策时立即停止。完成后报告 changed files、验证命令、未运行检查、偏差和 `git status`，不得自动继续下一 Task。

### 文档职责

- `docs/PRD.md`：产品目标、完整范围和阶段事实源；
- `docs/ARCHITECTURE.md`：系统边界、数据合同和演进顺序；
- `docs/ROADMAP.md`：发布定义、P0/P1/P2、2–4 周路线和 Gate；
- 本文件：当前状态和下方历史研发记录；
- `docs/design-system.md`：前端视觉治理。

## 历史记录

2026-06-27 至 2026-07-10 的逐日研发流水已移至 `docs/archive/history/PROGRESS-2026-06-27-to-2026-07-10.md`。该归档只用于追溯，不参与当前决策。