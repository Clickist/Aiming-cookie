# Coach 架构简化 — 交接

> **给 Codex 的上下文摘要，让下一轮对话能直接进入前端/后端的 E2E 工作。**

## 授权声明

以下所有改动由仓库所有者点点明确授意：
- 删除 Python → Node 的 HTTP tool bridge（安全围栏、bearer token、cursor 系统）
- 所有产品命令搬到 Node 直连 SQLite + 文件系统
- 前端 Coach 请求直连 Node sidecar（不再绕 Python）
- 删除 FORBIDDEN_KEYS / containsForbidden / redact_secrets 等安全演戏层

不要回退。有疑问问点点。

---

## 做了什么

### 总览（8 个 commit，21 文件，+8745 -159 行）

```
4c7e085  fix: review-found bugs — schema, column names, timestamps, fallback paths
370af4f  feat: port remaining commands — eloshapes, KovaaK API, analysis file ops, teaching lessons
b3ecc3e  feat: complete Phase 5 TODOs — enrichment, teaching, recovery, streaming
8ee4e0b  feat(phase5): frontend direct sidecar — eliminate Python middleman for Coach
ceb1c51  feat: native write commands with idempotency and audit
314b38f  feat: native evidence commands, eliminate bridge for all reads
86fbc90  feat: native SQLite reads, remove FORBIDDEN_KEYS security layer
6edce90  feat: add SQLite direct access infrastructure
```

### 架构变化

```

之前：Frontend → Python → Node sidecar → Provider
                         ← HTTP bridge ← tool calls

现在：Frontend → Node sidecar → Provider
                │
                ▼ direct SQLite + filesystem
```

**Node sidecar 现在**：直接打开 SQLite（WAL mode, read-write），直接读文件系统，直接调外部 API。Python bridge 只作 browser/dev fallback——桌面模式下不被调用。

### 新建文件（coach-runtime/src/ 下）

| 文件 | 职责 |
|---|---|
| `db.ts` | better-sqlite3 单例，WAL mode，nullable fallback |
| `product-commands-native.ts` | 13 个读命令 native 实现 |
| `product-commands-write.ts` | 19 个写命令 native 实现 + 幂等性 + 审计 |
| `evidence-native.ts` | 13 个证据命令 native 实现 + 字段目录端口 |
| `evidence-catalogs.ts` | processed event table builder + EvidenceKeyRegistry |
| `eloshapes-native.ts` | EloShape 查询（读 artifact JSON） |
| `kovaak-scores-native.ts` | KovaaK API 调用 |
| `task-manager.ts` | 后台异步任务管理（AbortController） |
| `agent-runs.ts` | Agent run 生命周期（create/stop/retry/poll/confirm） |
| `sidecar-coach-data.ts` | Sidecar HTTP 端点数据层（sessions, primary, contexts） |

### 改动文件（前端 + Rust）

| 文件 | 改动 |
|---|---|
| `src-tauri/runtime.rs` | `RuntimeConnection` 加了 `sidecar_url` 字段；`start_coach_sidecar` 收到 `DATABASE_URL` + `DATA_ROOT` |
| `lib/desktop.ts` | `DesktopRuntimeConnection` 加了 `sidecarUrl` |
| `lib/api.ts` | 新增 `apiFetchSidecar()`；Coach 端点路由到 sidecar |
| `sidecar-server.ts` | 新增 `/v1/agent-runs`、`/v1/sessions`、`/v1/context` 等 ~10 个端点 |

### 安全代码删除

| 文件 | 删除内容 |
|---|---|
| `product-command-tools.ts` | `FORBIDDEN_KEYS` / `containsForbidden` / `containsUnsafeResult` / `PATH_OR_URL_TEXT` (~60 行) |
| `turn.ts` | `ToolComplianceError` / `restrictTurnTools` / `scoreIntentExclusions` / `attachedAnalysisRefs` |
| `turn.ts` | `MANDATORY_POLICY` 里的安全条款（bridge tokens, paths, URLs, credentials） |
| `evidence-native.ts` | 4 处 `next_cursor: null` 死字段 |

### Python 后端未动的

- 分析管线（`kovaak_tracker/`、`queue.py`、`evidence_store.py`）——零改动
- Browser/dev fallback 路径全部保留——没有被删除

---

## 测试状态

| 测试 | 结果 |
|---|---|
| coach-runtime TS（168 项）| 165 pass, 1 env fail (PI_SOURCE_DIR), 2 skip |
| Python Coach 相关（324+ 项）| 全绿，0 回归 |
| 真实 Provider 对话 | 通过（opencode-go / deepseek-v4-flash） |
| Evidence 命令 | 代码路径正确，优雅降级正确 |

---

## E2E 测试的关键信息

### Provider 配置

本节原来记录了某次本机开发 DB 的具体 Provider/credential 状态。该状态属于易变化的本机字段证据，不是架构合同，也不应作为自动 E2E 的隐式输入。真实 Provider 验证必须通过产品 UI 配置当前隔离 profile，并在当前运行中单独报告。

### Evidence artifact 缺失

当前 `desktop-local` 唯一的完成分析（id=223）是 video_fallback 模式，没有关联 KovaaK run，**没有 evidence artifact**。其他用户（owner-evidence-commands 等 57 条）有 artifact 记录在 DB 里但 artifact 文件在临时 DATA_ROOT 中被清除了。

**要对 evidence 命令做完整 E2E 测试需要**：启动 Python 分析管线对一条四源（Stats + Performance + Raw Input + MP4）KovaaK run 跑分析，生成 evidence artifact。分析管线未动过，正常工作。

### Sidecar 启动方式

```bash
set DATABASE_URL=sqlite+aiosqlite:///C:/Users/袜子/Desktop/Aiming-cookie/aiming_cookie_dev.db
set DATA_ROOT=C:/Users/袜子/Desktop/Aiming-cookie
cd webapp/coach-runtime && npx tsx src/start-sidecar.ts
```

端口通过 `COACH_SIDECAR_PORT` 设置（默认 8765），或设 `0` 用随机端口。就绪信号打印到 stderr：`coach sidecar listening on http://127.0.0.1:{port}`。

### 前端 key functions（api.ts）

| 函数 | 路由方式 | 目标 |
|---|---|---|
| `getCoachAgentRun` | `apiFetchSidecar` | Sidecar `/v1/agent-runs/{ref}` |
| `create/stop/retryCoachAgentRun` | `apiFetchSidecar` | Sidecar |
| `list/create/update/deleteCoachSession` | `apiFetchSidecar` | Sidecar |
| `getCoachPrimary` | `apiFetchSidecar` | Sidecar |
| `getCoachContexts` | `apiFetchSidecar` | Sidecar |
| `attachCoachContext` | `apiFetchSidecar` | Sidecar |
| `decideCoachConfirmation` | `apiFetchSidecar` | Sidecar |
| `getCoachRuntimeStatus` | `apiFetch` | Python |
| `attachCoachPrimaryAnalysis` | `apiFetch` | Python |

`startCoachAnalysisSoftStart` 后续确认没有产品调用者，已从前端 adapter 删除；Python route 仅保留兼容性。桌面 Coach 的会话、上下文与 Agent Run adapter 直连 Node，Python 继续拥有普通 Analysis API、ingestion 和 worker。

### Browser/dev fallback

`apiFetchSidecar()` 在非 Tauri 桌面模式下自动降级：`path.replace(/^\/v1\//, "/api/coach/")`，走 Python 路由。所以 browser 模式下仍走 Python → Node 旧路径。

### E2E 验证清单

- [ ] 前端能不能正常加载（Next.js 或 Tauri）
- [ ] Settings 页能不能看到已配置的 Provider（opencode-go/deepseek-v4-flash）
- [ ] Coach 能正常对话（"你好，帮我看看最近的训练"）
- [ ] 对话中能否看到工具调用事件（tool_events 出现在 agent-run response 中）
- [ ] 能创建/切换 Coach 会话
- [ ] 能查看分析历史
- [ ] 能导航到 Settings / History
- [ ] 删除确认流工作正常（创建分析 → 对话中删除 → 确认卡出现 → 确认后真正删除）
- [ ] Evidence 命令可用（需要先跑分析生成 artifact）
- [ ] 视频播放和证据跳转正常

---

## 约束

- 不要 reset/checkout/提交（除非确认验证通过且点点授意）
- 不要碰 `third_party/pi/`
- Python 分析管线未动过，不要怀疑是它的 bug
- 本文件只记录 2026-08-12 的历史交接快照；任何 worktree 状态都必须现场重新检查。
- 遵循 `CLAUDE.md` 的 Agent Contract（称呼点点、询问歧义、最小改动等）
