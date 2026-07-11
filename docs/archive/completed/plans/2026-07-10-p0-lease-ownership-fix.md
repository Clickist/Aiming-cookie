# P0 Fix — mark_done/mark_failed lease ownership

> 来源：Codex review（点点转发）  
> 状态：已完成（subagent 实现 + 主代理补测试调用 + 全量验收）  
> 范围：仅修复「失去 lease 的旧 worker 覆盖新 worker 结果」

## 问题

`claim_next` / `heartbeat` 校验 `worker_id`，但 `mark_done` / `mark_failed` 只按 `session_id` 写终态 → stale worker 可覆盖 reclaimed job。

## Frozen

- `mark_done(session_id, result, llm_cost, *, worker_id: str) -> bool`
- `mark_failed(session_id, error, *, worker_id: str) -> bool`
- UPDATE 条件：`id=? AND status='running' AND worker_id=?`
- 0 行更新 → 返回 `False`，**不抛**；worker 记 warning 并退出（已失去 lease）
- 不引入 claim generation 列（本刀用 worker_id 足够；generation 后置）
- 本刀**不**改删除 running 语义、auth、流式上传

## Task 1 only

**Allowed files**

- `webapp/backend/queue.py`
- `webapp/backend/worker.py`
- `webapp/tests/test_queue.py`
- `webapp/tests/test_worker.py`

**Tests first**

```text
test_mark_done_requires_running_owner_worker
test_mark_failed_requires_running_owner_worker
test_stale_worker_cannot_overwrite_after_reclaim
```

场景 `test_stale_worker_cannot_overwrite_after_reclaim`：

1. worker A claim → running  
2. 强制 lease 过期 + recover → requeued（attempts 仍 < max）  
3. worker B claim → running，mark_done with B 结果  
4. worker A mark_done with A 结果 → False，DB 仍是 B 的 result  

**Worker**：所有 `mark_done`/`mark_failed` 传 `WORKER_ID`；`False` 时 log.warning，不再当成功。

## Verify

```bash
.venv/bin/python -m pytest webapp/tests/test_queue.py webapp/tests/test_worker.py -q
.venv/bin/python -m pytest webapp/tests -q
```

## Stop

- 需要新 DB 列 / migration  
- 需要改 delete/history/auth  
