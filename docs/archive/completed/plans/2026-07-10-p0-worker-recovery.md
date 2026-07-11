# P0 Worker Recovery — 可执行施工计划

> 状态：已完成（架构负责人批准并验收，点点大方向：分析别永远转圈）  
> 上游：`docs/ARCHITECTURE.md` §4.2 / §7，`docs/ROADMAP.md` P0  
> 产品锁：自用内部预览；失败/中断可读；用户可重试；视频默认保留由用户删  
> 执行规则：一次一个 Task；可并行仅当 Allowed files 不重叠

## 1. Goal

消灭永久 `running`，并在文件仍在时允许失败任务显式重试：

1. claim 时写入 lease + heartbeat；
2. 分析期间周期性 heartbeat 续租；
3. worker 循环回收过期 lease 的 stale job（可再试则 requeue，否则 failed）；
4. 新 job 默认 `max_attempts=3`；
5. `POST /api/sessions/{id}/retry`：failed + 输入文件仍在 → 重新 queued；
6. 分析失败**不再**自动删视频/CSV（与「用户自己删」一致，并支持重试）。

## 2. Frozen decisions

| 项 | 值 |
|---|---|
| `LEASE_TTL_SECONDS` | `300`（CV 可 ~160s，留余量） |
| `HEARTBEAT_INTERVAL_SECONDS` | `30` |
| `DEFAULT_MAX_ATTEMPTS` | `3` |
| SQLite `PRAGMA user_version` | 保持 `1`（列已在 contracts 计划加入，不升版本） |
| stale + attempts 未耗尽 | `running → queued`，清 `worker_id` / lease / heartbeat，**不**减 attempts |
| stale + attempts 已耗尽 | `running → failed`，Error v1 `code=stale_lease_exhausted`，`retryable=true` |
| 分析异常 | 立即 `failed`（本计划不做异常自动 requeue） |
| 失败删文件 | **停止删除**（成功路径本就不删） |
| 用户 retry | 仅 `failed`；要求 video/csv 路径非空且文件存在；`attempts=0`、清 error/finished/worker/lease，`status=queued` |
| 显式 retry 次数 | 重置 attempts，不另计费；自用足够 |
| lease 时间存储 | SQLite UTC `YYYY-MM-DD HH:MM:SS`（与 CURRENT_TIMESTAMP 一致） |

## 3. Out of scope

- History 列表/删除 UI
- supervisor / health 端点（可后置）
- 真正的 job 取消 token
- Desktop / tracking
- 自动清理 TTL

## 4. Tasks

### Task 1 — queue lease / heartbeat / recover + max_attempts

**Allowed files**

- `webapp/backend/config.py`
- `webapp/backend/queue.py`
- `webapp/tests/test_queue.py`

**Tests first（精确名）**

```text
test_claim_next_sets_lease_and_heartbeat
test_heartbeat_extends_lease_for_owner_worker
test_heartbeat_ignores_wrong_worker_or_non_running
test_recover_stale_requeues_when_attempts_remain
test_recover_stale_fails_when_attempts_exhausted
test_enqueue_default_max_attempts_is_three
test_requeue_failed_session_for_retry
```

**API**

```python
# config
LEASE_TTL_SECONDS = 300
HEARTBEAT_INTERVAL_SECONDS = 30
DEFAULT_MAX_ATTEMPTS = 3

# queue
async def claim_next(worker_id: str) -> Optional[dict]:  # 写 lease_expires_at, heartbeat_at
async def heartbeat(session_id: int, worker_id: str) -> bool
async def recover_stale_jobs(now: str | None = None) -> dict  # {requeued: int, failed: int}
async def requeue_for_retry(session_id: int) -> dict  # raises/returns structured; only failed+files ok
```

`recover_stale_jobs` 条件：`status='running' AND lease_expires_at IS NOT NULL AND lease_expires_at < now`  
legacy `running` 且 `lease_expires_at IS NULL`：用 `updated_at` 或 `started_at` 早于 now-LEASE_TTL 视为 stale（兼容 contracts 上线后未写 lease 的 row）。

### Task 2 — worker heartbeat loop + recover + no delete on fail

**Allowed files**

- `webapp/backend/worker.py`
- `webapp/tests/test_worker.py`

**Tests**

```text
test_process_one_runs_recover_before_claim
test_process_one_heartbeats_during_analysis
test_process_one_failure_keeps_input_files
test_run_loop_recovers_stale_when_idle
```

实现要点：

- `process_one` 开头 `await recover_stale_jobs()`
- `run_analysis`（及如需 `run_report`）经 `asyncio.to_thread`，并行 heartbeat task
- 失败路径：`mark_failed` 后**不**调用 `_delete_video_safely`
- idle sleep 前也可 `recover_stale_jobs`（双保险）

### Task 3 — API retry + 前端失败态可重试

**Allowed files**

- `webapp/backend/routes.py`
- `webapp/backend/schemas.py`（若需 RetryResponse）
- `webapp/tests/test_routes.py`
- `webapp/frontend/app/sessions/[id]/page.tsx`
- `webapp/frontend/lib/api.ts`

**Tests / 验证**

```text
test_retry_failed_session_requeues
test_retry_rejects_done_or_running
test_retry_rejects_missing_files
```

- `POST /api/sessions/{id}/retry` + owner 校验
- 前端 failed 卡：展示 `error.message`，按钮「重新分析」调 retry 后回到 processing 轮询
- `npm run build` + `pytest webapp/tests -q`

## 5. Verification (full)

```bash
.venv/bin/python -m pytest webapp/tests -q
.venv/bin/python -m pytest tests -q
cd webapp/frontend && npm run build
```

## 6. Stop conditions

- 需要改 Domain Core / 拆表 / 升 user_version
- heartbeat 无法在不阻塞 event loop 下实现
- 与用户未提交改动冲突
