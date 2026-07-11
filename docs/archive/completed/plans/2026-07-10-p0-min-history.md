# P0 Min History — 可执行施工计划

> 状态：已完成（Task 1–2 由 subagent 施工；主代理 review + 集成验证通过）  
> 上游：`docs/PRD.md`、`docs/ARCHITECTURE.md`、`docs/ROADMAP.md`  
> 执行规则：一次一个 Task；主代理 review；禁止顺手做趋势/export/auth
>
> **2026-07-11 裁决**：本计划交付即为内部预览 P0 的最小 History 范围；趋势/对比后移 P1。常驻 Coach 数据归属迁移已提升为预览 P0，但必须走 Pi assessment/Spike 后的新 implementation plan，本文件不得继续修改 chat/schema。
>
> ## 0. 2026-07-10 PRD 覆盖说明（保留历史事实，不可继续照此施工）
>
> Task 1–2 的完成记录仍描述当时的最小 History 交付；它**不是**新 PRD 下的终局删除或 Coach 合同。新 PRD 已覆盖以下旧决策：
>
> | 原 plan 中的历史决策 | 当前有效合同 | 后续处理 |
> |---|---|---|
> | 删除 session 时 `DELETE chat_messages WHERE session_id` | 删除分析不得级联删除 Coach 对话/长期记忆 | **内部预览 P0**：由 Pi adoption assessment/Spike 后的替代 Coach migration plan 消除 session-bound chat 依赖；旧 persistent plan 已冻结。 |
> | queued/running 也可删除 | queued/running 不可删除；仅 done/failed 可删 | 当前实现与新增回归测试已按新 PRD 收紧；本计划第 2 节仅保留历史记录。 |
> | session 是 chat 的父级 | Coach 关系可引用 0～N 次分析，分析不是会话父级 | 由 Pi adoption assessment 后的替代 Coach migration plan 重做；不得执行旧 Task。 |
>
> 因此不得从本文件的旧「Frozen decisions」或 Task 1 实现要点复制删除/chat 逻辑。新的实施事实源是 PRD、持久 Coach 产品迁移边界，以及 Pi assessment 后尚待编写的替代 implementation plan。

## 1. Goal（产品）

> 本节保留最小 History 的交付目标，但删除语义以 2026-07-11 裁决为准；原实现中的 chat 级联删除不是当前有效合同。

用户能：

1. 看到自己的分析列表（含状态：done / failed / running / queued）；
2. 点开 done 项进入已有 report 页；
3. 仅删除 done/failed analysis 及其输入/artifacts；Coach 消息和长期档案保留，相关 analysis reference 变为已删除/不可用。

**已有、不重做**：`done` 已写入 SQLite `sessions`；`GET /sessions/{id}` + report/coach 页已存在。当前 chat 仍为 session-bound，需由预览 P0 的替代 Coach migration plan 修复；不得在本计划继续施工。

**本刀不做**：趋势对比、export/import、自动清理 TTL、登录、视觉大改、JSONL 双写。

## 2. Frozen decisions

| 项 | 值 |
|---|---|
| Canonical store | 现有 SQLite `sessions`（不新建 history 表） |
| 列表范围 | 当前 `X-User-Id` 下全部 session，**新→旧** `created_at DESC, id DESC` |
| 列表字段 | `id, status, created_at, finished_at, attempts, max_attempts, llm_cost_cny` + 可选摘要 `summary_label` |
| `summary_label` | done 时从 result 取 `deterministic.diagnosis.profile.label`（若有），否则 null；列表 API **不**返回完整 result |
| 删除语义 | 硬删 session 行；`DELETE chat_messages WHERE session_id`；best-effort 删 `video_path`/`csv_path` 文件；文件已不存在不算失败 |
| 删除权限 | 仅 owner；跨用户 403；不存在 404 |
| 不可删 | 无 — queued/running/failed/done 均可删（自用；删 running 即放弃任务） |
| 打开报告 | 前端链到既有 `/sessions/{id}/report`（done）；failed → `/sessions/{id}` 处理页；running/queued → `/sessions/{id}` |
| 趋势 | **不做** |
| user_version | 保持 1，无 migration |
| 视觉 | 复用 `globals.css` token；极简列表，不新开设计体系 |

## 3. API

```text
GET  /api/sessions
     → { "sessions": [ SessionListItem, ... ] }

DELETE /api/sessions/{id}
     → { "deleted": true, "id": <int>, "files_removed": ["video"|"csv", ...] }
```

`SessionListItem`:

```json
{
  "id": 1,
  "status": "done",
  "created_at": "2026-07-10T12:00:00Z",
  "finished_at": "2026-07-10T12:03:00Z",
  "attempts": 1,
  "max_attempts": 3,
  "llm_cost_cny": 0.0,
  "summary_label": "减速不足型" 
}
```

时间 wire 格式与现有 SessionStatus 一致（`…Z`）。

## 4. Tasks

### Task 1 — Backend list + delete

**Allowed files**

- `webapp/backend/queue.py` 或 `webapp/backend/db.py`（二选一放 list/delete 数据访问；推荐 `queue.py` 与 session 同模块）
- `webapp/backend/routes.py`
- `webapp/backend/schemas.py`
- `webapp/tests/test_queue.py` 和/或 `webapp/tests/test_routes.py`（允许新建 `webapp/tests/test_history.py`）

**Tests first（精确名）**

```text
test_list_sessions_empty
test_list_sessions_newest_first_owner_only
test_list_sessions_omits_full_result
test_list_sessions_summary_label_from_profile
test_delete_session_removes_row_chat_and_files
test_delete_session_404
test_delete_session_forbidden_other_user
test_delete_running_session_allowed
```

**实现要点**

- `list_sessions(user_id) -> list[dict]`
- `delete_session(session_id, user_id) -> dict`（校验 owner）
- 删文件用 `os.remove` best-effort（与现有 regenerable temp 策略一致）；记录 `files_removed`
- chat 先删再删 session（FK）
- 列表 SQL 不 `SELECT result` 全文更佳；若需 label，可 `SELECT result` 但响应不回传 result 字段

### Task 2 — Frontend History page + API client

**Allowed files**

- `webapp/frontend/lib/api.ts`
- `webapp/frontend/lib/types.ts`
- `webapp/frontend/app/history/page.tsx`（新建）
- `webapp/frontend/app/page.tsx`（仅加「历史」入口链接，最小改动）
- `webapp/frontend/app/sessions/[id]/page.tsx`（可选：失败/完成区链到历史，最小）
- `webapp/frontend/app/sessions/[id]/report/page.tsx`（可选：返回历史链接）

**要求**

- `/history` 列表：状态、时间、summary_label、点进 report/processing、删除按钮（确认可用 `window.confirm`）
- 删除后刷新列表
- 空状态：「还没有分析记录」+ 链到上传 `/`
- 使用 design tokens；不装新依赖；`npm run build` 通过

### Task 3 — Integration gate

**Allowed**

- 仅修本 plan 引入的遗漏（经主代理批准）

**验证**

```bash
.venv/bin/python -m pytest webapp/tests -q
.venv/bin/python -m pytest tests -q
cd webapp/frontend && npm run build
```

## 5. Stop conditions

- 需要新建并行 history 表或改 Domain Core
- 需要做趋势/export
- 需要升 `user_version` 或破坏性 migration
- 测试要求改 Allowed 外文件且无法在范围内解决

## 6. Completion

每 Task 按 §17 风格报告：changed files、验证、偏差、git status 相关行。主代理 review 后才下一 Task。
