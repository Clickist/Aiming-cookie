# Flicking 模块进度

> 最后更新：2026-07-12（Desktop local-first vertical slice 实施、回归与 review 交接）

## 当前交付罗盘（2026-07-11）

### 发布裁决

- **长期目标**：`docs/PRD.md` 定义的完整 Desktop hybrid 产品，范围不删减。
- **2026-07-13 至 2026-07-19**：只发布受控环境中的 **flicking-only 内部技术预览**，不称为完整产品 v1。
- **当前状态**：完整产品 v1 `No-Go`；内部技术预览完成 `docs/ROADMAP.md` §5 全部 P0 Gate 后才 `Go`。

### Desktop local-first vertical slice（本轮最新）

实施依据：`docs/archive/frozen/plans/2026-07-12-desktop-local-first-vertical-slice.md`。Sol 负责冻结合同、review 与放行；Terra executors 按 Task 实施。**代码施工和回归已完成，但 Gate C 尚未验收通过**，原因是 managed video / seek 当前没有用户可达入口。

**已完成并提交，均未 push：**

```text
9be14e6 feat(desktop-data): add local path import and managed storage
e3ed122 feat(desktop-runtime): add Tauri shell and local runtime lifecycle
3bde07e feat(desktop): connect native import and local media playback
81dcb1d feat(frontend): add governed light and dark themes
0ea354c feat(desktop): add managed storage settings
6df35e3 fix(desktop-runtime): make Tauri shell compile cleanly
73d232c fix(desktop-runtime): exit when shell pipe closes
1c6fc31 fix(desktop-runtime): shut down managed process trees gracefully
8fe841d fix(desktop-security): require launch token for local APIs
```

**已核验能力：**

- Desktop native MP4 / CSV path import，源文件保持不变，文件复制到 managed App Data session workspace；
- 本地 Python API + worker 由 Tauri 管理，动态 loopback 端口，launch token 仅保存在内存；Desktop 模式全部 `/api/` 请求要求 token，`/healthz` 保持无 token readiness；
- Ctrl-C / shell pipe EOF 后 Tauri、Next、Python 及残留进程组均退出，复验端口不再监听；
- managed storage accounting 与 done / failed session 删除入口；
- Light / Dark / System token 与主题控制已接入；
- Browser transport 保持相对 `/api`，不附 Desktop token。

**最新回归：**

- Python：**295 passed，3 skipped**；
- desktop runtime integration：**5 passed**；
- Rust / Tauri：`cargo fmt --check`、`cargo check --locked --all-targets`、`cargo test --locked --all-targets`（**4 passed**）、`cargo clippy --locked --all-targets -- -D warnings`；
- frontend：`npm run type-check`、`npm test`（**2 passed**）、`npm run build`；
- Coach runtime：**8 passed**；
- `git diff --check` 与 `git diff --cached --check`。

**进程审计：**

- 无孤儿 Playwright、Playwright Chromium、ChromeDriver、Tauri、Desktop Python runtime 或 Next 进程，也无 3000 / 8000 listener；
- 用户正常 Google Chrome 未终止；现存 `chrome-devtools-mcp` 进程均为当前 Codex 的直接子树，不属于孤儿；
- 发现无进程持有的旧 Playwright 临时 profile / artifacts，尚未擅自删除。

#### Review 遗留问题（下一 session 讨论，不代表已修复）

**High**

1. **Gate C 的 managed video / seek 用户不可达**：`sessions/[id]/coach/page.tsx` 当前重定向到全局 `/coach?analysis=<id>`，包含 `convertFileSrc`、`<video>`、seek 和时间戳锁定的 `CoachView.tsx` 没有页面渲染，Report 也无播放器。需决定播放器放 Report、恢复 session-scoped Coach，或在全局 Coach 增加视频工作区。
2. **导入崩溃可能永久卡在 `uploading`**：DB row 先创建，复制期间崩溃会留下 active session，阻塞后续导入；现有 stale lease recovery 只覆盖 `running`。需冻结 stale 时间、启动 recovery、失败状态和临时 workspace + atomic rename 合同。
3. **删除顺序缺少可回滚文件语义**：managed workspace 在 DB transaction commit 前永久删除；后续 Coach reference / DB 操作失败可 rollback 数据库，但无法恢复文件。需决定 tombstone rename、commit 后清理、rollback 恢复及启动 reconciliation。

**Medium**

1. Desktop CSV native picker 不会像 Browser 上传流程那样自动解析 FOV；当前通常发送输入框默认 `103`，后端只在 `fov=None` 时从 CSV fallback。
2. Python runtime READY 后若崩溃，Tauri `RuntimeState` 仍可能返回缓存的旧 base URL / token；尚无 restart 或 fatal-state 策略。
3. `AIMING_COOKIE_DESKTOP_TOKEN` 会被 Coach Node subprocess 继承；应在 Python 读取后移除，或创建 subprocess 环境时显式过滤。

**Low**

1. Browser 原生 `<video src>` 不能附当前 `apiFetch()` 的身份 header；可信反代 / 非默认用户模式下需 cookie、签名 URL 或受控 media transport。

#### 下一 session 需要先冻结的决定

1. managed video / seek 在重新设计后的信息架构中放在哪里；前端视觉已明确留到下一阶段整体重做，本轮不做局部视觉修补；
2. `uploading` crash recovery 与 session workspace 原子落盘合同；
3. 删除的 tombstone / transaction / reconciliation 合同；
4. Desktop CSV FOV 的 source-of-truth；
5. runtime crash 后自动重启、fatal UI 或按需重建三选一；
6. launch token 从子进程环境隔离的最小实现。

#### 已知发布 blockers（不属于本次修复）

- Next static frontend / package strategy；Python runtime bundling / distribution；
- `project_root()` 默认依赖编译期源码路径，默认 Python 不自动选择 `.venv`；
- `bundle.active: false`、正式品牌 icon、installer、signing / notarization、updater；
- Windows CI / process lifecycle tests 与 Windows Job Object；完整 Xcode / bundle toolchain；
- 当前前端视觉需要下一阶段整体重新设计。

#### 工作区交接

- 上述 Desktop commits 均只在本地，**未 push**；本次进度更新也尚未 commit；
- 本次只修改 `docs/PROGRESS.md`；
- 下列内容是进入本轮前已存在的用户工作区改动，未修改、未 stage：`output/frame_errors.csv`、`output/metrics.json`、`.firecrawl/`、`DESIGN.md`、`FPS Aiming Theory Evolution.md`、`aiming-theory-compendium.html`、`macos-vibrancy-style-pack/`。

### 此前 Web / Coach 能力基线

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

**Web / 内部技术预览阻塞**（Desktop slice 未使这些 Gate 自动通过）：

1. 无自动 TTL、完整 quota/orphan 治理与主动删除产品化仍缺（workspace + 流式上传 + 删目录 + 低磁盘门槛已落地）；
2. 预览/生产需 `TRUST_PROXY_USER=1` + 可信反代才成立；本地 dev 仍可用 `X-User-Id`；
3. 无 supervisor、structured logs；browser E2E 仅有骨架（未装 Playwright 时 skip），非完整 release gate；
4. Desktop shell、local runtime 与 native import vertical slice 已落地；installer、签名、更新和发布 bundling 仍未完成；
5. 有 LLM key 时 `/coach` 真机一轮 **未在本 Task 强制验收**（无 key 不阻塞；有 key 建议手工记一条到本文件）。

### 当前执行顺序

1. 线 A / 线 B 薄切片 / 结构加固 — **已完成**。
2. 2026-07-12 Web / Coach 队列（sidecar、workspace upload、preview ops、coach startup UX）— **已完成**。
3. `2026-07-12-desktop-local-first-vertical-slice.md` — **施工与回归完成，Gate C 未通过，等待上述 review 决策**。
4. 前端视觉重做 — **下一阶段，先讨论信息架构与 `DESIGN-cursor.md` / token 治理，不在当前代码上做局部修补**。
5. **P1 / P2**：见 Roadmap。

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