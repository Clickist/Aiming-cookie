# Persistent Coach Data Ownership — 可执行施工图（线 A）

> **状态：active。点点 2026-07-11 授权尽快开工。**  
> **范围**：canonical Coach 数据归属 + 删除语义 + 最小 `/coach` 入口 + 旧 chat 迁移。  
> **不在范围**：Pi `third_party` vendor、Node sidecar、新 agent loop、cloud auth、billing、tracking、Desktop。  
> **上游**：`docs/PRD.md`、`docs/ARCHITECTURE.md`、`docs/superpowers/specs/2026-07-10-persistent-coach-design.md`、`docs/superpowers/assessments/2026-07-11-pi-assessment-decision.md`。  
> **明确不执行**：`docs/archive/retired/plans/2026-07-10-persistent-coach-migration.md`。

## 0. Executor 开工口令

收到点点给出的 **一个 Task 编号** 后，先原样回显：

```text
Task: <编号 + 标题>
Depends on: <已完成 Task>
Allowed files: <逐项列出>
Tests first: <精确测试>
Frozen decisions: 见 §2；不得重选 schema/default/删除语义
Stop rule: 源码/测试不符、需扩大范围或新架构决策 → 立即停止
```

每次只做一个 Task；完成后停止，不自动进下一 Task，不 commit/push 除非点点要求。

---

## 1. Goal

```text
user
  └─ coach_threads (primary, 每 user 一条)
       ├─ coach_messages (对话权威源)
       └─ coach_analysis_refs (0..N；可 deleted)
```

成功后：删除 `done/failed` analysis **不再** `DELETE FROM chat_messages`；Coach 消息与关系保留；引用显示已删除；用户可从 `/coach` 继续聊（有/无分析上下文均可）。

## 2. Frozen decisions（全 Task 生效）

### 2.1 Schema：`PRAGMA user_version` 1 → 2

```sql
CREATE TABLE IF NOT EXISTS coach_threads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'primary',  -- 本阶段仅 'primary'
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_id, kind)
);

CREATE TABLE IF NOT EXISTS coach_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    thread_id INTEGER NOT NULL,
    role TEXT NOT NULL,                    -- user | assistant | system
    content TEXT NOT NULL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    trace_json TEXT,
    legacy_session_id INTEGER,             -- 迁移来源；可为 NULL
    FOREIGN KEY (thread_id) REFERENCES coach_threads(id)
);
CREATE INDEX IF NOT EXISTS idx_coach_messages_thread
    ON coach_messages(thread_id, id);

CREATE TABLE IF NOT EXISTS coach_analysis_refs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    thread_id INTEGER NOT NULL,
    analysis_session_id INTEGER,           -- sessions.id；删除后仍可保留 id 痕迹
    status TEXT NOT NULL DEFAULT 'active', -- active | deleted
    attached_at TEXT DEFAULT CURRENT_TIMESTAMP,
    deleted_at TEXT,
    FOREIGN KEY (thread_id) REFERENCES coach_threads(id)
);
CREATE INDEX IF NOT EXISTS idx_coach_refs_thread
    ON coach_analysis_refs(thread_id, id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_coach_refs_thread_session_active
    ON coach_analysis_refs(thread_id, analysis_session_id)
    WHERE status = 'active' AND analysis_session_id IS NOT NULL;
```

- 拒绝 `user_version > 2` 静默降级（沿用现有模式）。
- **不**在本 plan 加 run/event/tool 表。
- 旧表 `chat_messages` 保留；迁移后只读兼容，新权威写入走 `coach_messages`。

### 2.2 删除语义

`delete_session(session_id, user_id)`：

1. 校验 owner；`queued/running` → 不可删（现有行为）。
2. **禁止** `DELETE FROM chat_messages WHERE session_id=?`。
3. 将所有 `coach_analysis_refs` 中 `analysis_session_id = session_id` 且 `status='active'` 标为 `deleted`，写 `deleted_at`。
4. 删除 session 行与 video/csv 等输入文件（现有文件清理逻辑保留/收紧，不扩大 TTL 范围）。
5. **不**删除 `coach_messages` / `coach_threads`。
6. 若仍存在仅挂在旧 `chat_messages` 且尚未迁移的行：迁移 Task 保证先迁后删路径；delete 时若发现未迁移 legacy 消息，先迁入 primary thread 再删 session（防数据丢）。

### 2.3 API（最小）

| Method | Path | 行为 |
|---|---|---|
| GET | `/api/coach/primary` | 返回/惰性创建 primary thread + messages + refs（含 deleted 摘要） |
| POST | `/api/coach/primary/messages` | body: `{ content, analysis_session_id? }`；无分析也可聊 |
| POST | `/api/coach/primary/attach` | body: `{ analysis_session_id }`；幂等 attach done 分析 |
| GET/POST | `/api/sessions/{id}/chat` 与 coach | 兼容：映射到 primary + 可选 attach 该 session |

Owner：继续用现有 `X-User-Id` 预览边界（可信身份是另一 plan，本 plan 不升级 auth）。

### 2.4 Runtime

- 仍调用 `kovaak_tracker.coach.agent.chat_with_coach`。
- 有 active ref 且 session 仍存在且 `done`：注入该 analysis 的 deterministic diagnosis（现有逻辑）。
- 无可用分析：明确无指标上下文，不得编造。
- 已删除 ref：不得再注入该分析的 result/路径/视频。

### 2.5 前端最小

- 新增 `/coach` 页：消息列表 + 输入 + refs 芯片（active / 已删除）。
- Report「跟教练深聊」→ `/coach?analysis=<id>`（attach 后打开）。
- 旧 `/sessions/[id]/coach` 可 redirect 或 thin wrapper 到 `/coach?analysis=<id>`。
- **不做** 多 thread、趋势、IA 大改、新视觉体系。

### 2.6 迁移

启动或显式 `migrate_legacy_chat_messages()`（在 `init_schema` v2 后调用一次逻辑，幂等）：

1. 对每个在 `chat_messages` 出现过的 `session_id`，解析 `sessions.user_id`。
2. 确保该 user 有 primary thread。
3. 按 `chat_messages.id` 顺序插入 `coach_messages`（`legacy_session_id` 填充）；已存在相同 legacy 映射则跳过。
4. 为该 session 建 `coach_analysis_refs`（session 仍在 → active；若 session 已不存在 → deleted）。
5. **不**在本 plan 物理 DROP `chat_messages`。

## 3. Tasks

# Task 1 — Schema v2 + repository primitives

## Goal

`user_version` 1→2；三表；get-or-create primary thread；消息 append/load；ref attach/mark-deleted；legacy 迁移函数骨架（可测）。

## Depends on

无。

## Allowed files

- `webapp/backend/db.py`
- `webapp/tests/test_db.py`
- 仅当需导出 helper：`webapp/backend/coach_store.py`（新建）

## Tests first

在 `webapp/tests/test_db.py`（或同目录新测）先写并失败：

1. `init_schema` 从 v1 → v2，三表存在，`user_version=2`。
2. v2 再 init 幂等。
3. `user_version=3` 拒绝。
4. `get_or_create_primary_thread(user)` 唯一。
5. append + load messages 按 id 序。
6. attach ref 幂等；mark deleted 后 status/deleted_at。
7. legacy 迁移：旧 `chat_messages` → `coach_messages`，重复调用不复制。

## Implementation

按 §2.1–2.6 实现；删除语义的 queue 改动留给 Task 2。

## Verification

```bash
source .venv/bin/activate
python -m pytest webapp/tests/test_db.py -q
```

## Acceptance

- [ ] 上述测试全绿
- [ ] 仅改 Allowed files
- [ ] 报告 `git status --short` 后停止

## Stop

扩大到 routes/queue/frontend；或改表结构与 §2.1 不符。

---

# Task 2 — delete_session 新语义

## Goal

删除分析不删 Coach 消息；refs → deleted；未迁移 legacy 先迁。

## Depends on

Task 1。

## Allowed files

- `webapp/backend/queue.py`
- `webapp/tests/test_queue.py`
- `webapp/tests/test_history.py`（若覆盖删除）
- Task 1 已有 store 文件（仅调用，不改 schema）

## Tests first

1. 有 coach_messages + active ref 时 delete done session → session 没了、messages 仍在、ref deleted。
2. queued/running 仍不可删。
3. 仅有 legacy `chat_messages` 时 delete → 消息出现在 coach_messages，session 删除。
4. 跨 user forbidden。

## Verification

```bash
python -m pytest webapp/tests/test_queue.py webapp/tests/test_history.py -q
```

---

# Task 3 — Coach HTTP API

## Goal

`/api/coach/primary`、messages、attach；session chat/coach 兼容映射。

## Depends on

Task 2。

## Allowed files

- `webapp/backend/routes.py`
- `webapp/backend/schemas.py`
- `webapp/tests/test_routes_coach.py`
- `webapp/tests/test_routes_chat.py`
- store 文件（调用）

## Tests first

1. GET primary 惰性创建。
2. POST message 无 analysis。
3. POST message + analysis（done，owner 匹配）。
4. attach 幂等；deleted 分析不可当 active 上下文。
5. 旧 session chat 写入进入 primary thread。

## Verification

```bash
python -m pytest webapp/tests/test_routes_coach.py webapp/tests/test_routes_chat.py -q
```

---

# Task 4 — 前端 `/coach` + Report 入口

## Goal

最小 `/coach` UI；Report 深聊跳转；旧 session coach 兼容。

## Depends on

Task 3。

## Allowed files

- `webapp/frontend/app/coach/**`（新建）
- `webapp/frontend/lib/api.ts`
- `webapp/frontend/lib/types.ts`
- `webapp/frontend/app/sessions/[id]/report/ReportView.tsx`
- `webapp/frontend/app/sessions/[id]/coach/**`（redirect/wrapper 最小改）
- `webapp/frontend/app/history/**`（若需链到 coach，最小）

## Tests first

```bash
cd webapp/frontend && npx tsc --noEmit
```

手工验收清单写入 PR 说明：打开 `/coach`、发无分析消息、从 report attach。

## Verification

```bash
cd webapp/frontend && npx tsc --noEmit && npm run build
```

---

# Task 5 — 全仓回归 + 文档收口

## Goal

全仓 pytest；更新 PROGRESS/ROADMAP 勾选数据归属；确认线 B 仍待 plan。

## Depends on

Task 4。

## Allowed files

- `docs/PROGRESS.md`
- `docs/ROADMAP.md`（仅状态勾选，不改优先级定义）
- `docs/superpowers/plans/README.md`
- 测试修复若回归失败：仅失败相关文件，扩大前停止上报

## Verification

```bash
python -m pytest -q
cd webapp/frontend && npx tsc --noEmit
```

---

## 4. 明确不做

- Pi vendor / sidecar / 新 event 协议
- 可信 SSO 替换 `X-User-Id`
- 文件 TTL/quota 完整生命周期
- browser E2E gate
- tracking、支付、Desktop

## 5. 成功标准（线 A 完成）

1. 删 done 分析后 primary thread 消息仍在，ref 为 deleted。
2. `/coach` 可无分析聊天；可 attach 未删分析。
3. 旧 chat 不丢。
4. 全仓 pytest 绿；frontend build 过。
5. 线 B 仍有独立入口，不假装 runtime 已接管 Pi。
