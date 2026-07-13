# Coach 结构加固 — Review P0 重构施工图

> **状态：active。点点 2026-07-12 授权（code-review Request Changes 后续）。**  
> **目标**：行为尽量不变；消灭 routes 里的引擎分叉与假 diagnosis；删除原子化；迁移幂等硬化。  
> **不做**：Pi daemon、vendor 裁剪、前端大改、可信身份、文件 TTL。

## 0. Executor 口令

每次只做一个 Task。回显 Task / Allowed / Tests first / Stop。不 commit/push。不扩大到 frontend IA。

---

## 1. Goal

```text
routes.py          → 只 parse HTTP + 调 service + 组 response
coach_service.py   → 一轮对话编排（预算、落库、引擎、notes）
coach_engine.py    → Protocol + PiEngine + PythonEngine + fallback
coach_store.py     → 持久化；migrate 单一入口 + legacy id 幂等
queue.delete_session → 单事务：lock → migrate → mark refs → delete
```

成功：全仓 pytest 绿；`COACH_RUNTIME=pi|python` 与 fallback 行为与现测一致；删除仍保留 coach 消息。

---

## 2. Frozen decisions

1. **不改** 对外 API 路径与主要 JSON 字段名（`/api/coach/primary*`、session chat）。
2. `CoachTurn` 输入：`messages: list[{role,content}]`、`analysis_summary: str|None`、`user_message: str`。Python 引擎内部可从 summary/空构造 diagnosis，**routes 不再构造 `_empty_coach_diagnosis`**。
3. Fallback：`COACH_RUNTIME=pi` 且 Pi 失败且 `COACH_RUNTIME_FALLBACK_PYTHON` 真 → PythonEngine。
4. `delete_session`：一个 `BEGIN IMMEDIATE` 内完成校验+migrate+mark+delete rows；文件删除仍在 commit 后。
5. Schema：`coach_messages.legacy_chat_message_id INTEGER` 可空；`UNIQUE(legacy_chat_message_id)` 或 `UNIQUE` 部分索引 where not null；`user_version` **2 → 3**。
6. 合并 migrate：只保留 `migrate_session_legacy_messages(conn|None, session_id)`；全量迁移循环调它。

---

## 3. Tasks

# Task 1 — Schema v3 + migrate 单一入口 + legacy id

## Allowed

- `webapp/backend/db.py`
- `webapp/backend/coach_store.py`
- `webapp/tests/test_db.py`

## Tests first

1. init 到 user_version=3，有 `legacy_chat_message_id` 列。
2. 迁移同一 chat_messages 行两次不复制（靠 legacy id）。
3. 旧 v2 DB 升级到 v3 幂等。

## Implementation

- TARGET_USER_VERSION=3；ALTER 加列 + 唯一约束（SQLite：unique index on legacy_chat_message_id WHERE NOT NULL）。
- 重写 migrate 使用 legacy id；删除重复的 ensure/migrate 分叉逻辑（ensure 变 thin wrapper）。

## Verify

```bash
python -m pytest webapp/tests/test_db.py -q
```

---

# Task 2 — delete_session 单事务

## Depends on

Task 1。

## Allowed

- `webapp/backend/queue.py`
- `webapp/backend/coach_store.py`（仅：migrate/mark 支持传入 conn、事务内不各自 commit）
- `webapp/tests/test_queue.py`
- `webapp/tests/test_history.py`（若删测失败）

## Tests first

保持/加强：删 done 保留 coach 消息；legacy 先迁；queued 拒绝；跨 user 拒绝。

## Implementation

- `coach_store` 函数支持 `conn` 参数；在外部事务中 `commit=False`。
- `delete_session` 单事务路径。

## Verify

```bash
python -m pytest webapp/tests/test_queue.py webapp/tests/test_history.py webapp/tests/test_db.py -q
```

---

# Task 3 — CoachEngine + coach_service；routes 瘦身

## Depends on

Task 2。

## Allowed

- `webapp/backend/coach_engine.py`（新建）
- `webapp/backend/coach_service.py`（新建）
- `webapp/backend/routes.py`（只保留 HTTP 适配）
- `webapp/backend/coach_runtime.py`（仅当 engine 需要小改签名）
- `webapp/tests/test_routes_coach.py`
- `webapp/tests/test_routes_chat.py`
- `webapp/tests/test_coach_runtime.py`（若需）

## Tests first

现有 runtime 分支测必须仍绿：

- python 路径
- pi 路径 mock
- pi 失败 fallback
- primary + session chat 写入 coach_messages

## Implementation

```python
# coach_engine.py
class CoachEngine(Protocol):
    def complete(self, turn: CoachTurn) -> str: ...  # or async

def get_engine() -> CoachEngine:  # reads config, wraps fallback
```

```python
# coach_service.py
async def run_chat_turn(...) -> CoachChatResult:
    # budget, append user, engine.complete, append assistant, cost
```

- routes：`post_coach_primary_message` / session `chat` 只组 turn 调 service。
- **删除** routes 内 `_execute_coach_chat_turn` 大段 if pi/python；删除对外暴露的空 diagnosis 构造（可迁到 PythonEngine 私有）。

## Verify

```bash
python -m pytest webapp/tests/test_routes_coach.py webapp/tests/test_routes_chat.py webapp/tests/test_coach_runtime.py webapp/tests -q
```

## Stop

routes.py 若仍 >600 行且 coach 逻辑未搬出 → 未完成。目标：coach 编排不在 routes。

---

# Task 4 — 回归 + 文档

## Allowed

- `docs/PROGRESS.md`
- `docs/superpowers/plans/README.md`
- 失败则最小修测

## Verify

```bash
python -m pytest -q
cd webapp/frontend && npx tsc --noEmit
```

---

## 4. 明确不做

- 不删 third_party coding-agent（另 plan）
- 不改 Coach UI
- 不引入 daemon
