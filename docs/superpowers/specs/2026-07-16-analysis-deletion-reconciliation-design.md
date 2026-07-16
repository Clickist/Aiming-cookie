# Analysis Deletion and Workspace Reconciliation — Design Contract

> 状态：active
> 目的：冻结 terminal Analysis 的 SQLite 删除、Coach 保留、managed workspace 清理与崩溃恢复语义。
> 上游：[`../../PRD.md`](../../PRD.md)、[`../../ARCHITECTURE.md`](../../ARCHITECTURE.md)、[`2026-07-13-kovaak-run-trace-lifecycle-design.md`](2026-07-13-kovaak-run-trace-lifecycle-design.md)、[`2026-07-13-analysis-evidence-coach-context-design.md`](2026-07-13-analysis-evidence-coach-context-design.md)

## 1. 范围

本 spec 只定义：

- `done | failed` Analysis 的删除事务；
- session-bound legacy Coach 消息迁移与 Analysis ref 失效；
- Analysis-owned managed workspace 的幂等清理；
- SQLite commit 与文件系统清理之间的 transient tombstone；
- Desktop 启动时对未完成清理的 reconciliation；
- 删除响应中逻辑删除与文件清理结果的稳定语义。

不定义：

- Run/source 删除、Run tombstone、Run-owned Raw Input trace retention；
- 用户 Stats/Performance/原始 MP4 的删除；
- Raw Input rolling buffer、quarantine 或 orphan trace 策略；
- uploading/importing partial workspace 恢复；
- TTL、quota、批量清理、导出/导入或正式前端删除 UI。

## 2. 稳定不变量

1. 只有 `done | failed` Analysis 可删除；queued/running 等非 terminal 状态拒绝。
2. 删除只影响 Analysis row、legacy session chat row 与该 Analysis 的 managed workspace。
3. Coach 消息、context 和长期关系保留；active Analysis ref 永久转为 `deleted`，wire 投影为 `unavailable`。
4. KovaaK Run、Run-owned Raw Input trace、用户 Stats/Performance 和用户源 MP4 永不级联删除。
5. Phase A commit 前的 SQLite 失败或事务 rollback 时，session、Coach refs/messages 和 workspace 必须保持删除前状态。
6. 一旦 SQLite 删除事务 commit，Analysis 在产品语义上已经删除；workspace cleanup 失败不能复活 Analysis。
7. workspace 不存在视为 cleanup 成功；部分删除和重复 reconciliation 必须收敛。
8. tombstone 不保存绝对路径；workspace 只能由 decimal `analysis_session_id` 通过受管 helper 推导。

## 3. Transient tombstone

使用下列完整 DDL；不创建 state index，因为 reconciliation 处理全部 transient row，当前没有
按 state 分页或长期保留的查询需求：

```sql
CREATE TABLE IF NOT EXISTS analysis_deletion_tombstones (
    analysis_session_id INTEGER PRIMARY KEY CHECK(analysis_session_id > 0),
    owner_id TEXT NOT NULL CHECK(TRIM(owner_id) <> ''),
    cleanup_state TEXT NOT NULL DEFAULT 'pending'
        CHECK(cleanup_state IN ('pending', 'failed')),
    cleanup_attempts INTEGER NOT NULL DEFAULT 0 CHECK(cleanup_attempts >= 0),
    last_error_code TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK(
        (cleanup_state = 'pending' AND cleanup_attempts = 0
            AND last_error_code IS NULL)
        OR
        (cleanup_state = 'failed' AND cleanup_attempts >= 1
            AND last_error_code IS NOT NULL
            AND last_error_code = 'workspace_cleanup_failed')
    )
);
```

约束：

- presence 表示 workspace cleanup 尚未确认完成；
- 只允许 `pending | failed`，不保存 `complete` row；
- cleanup 成功后删除 tombstone，不建立长期删除历史或 retention 默认值；
- `last_error_code` 只允许稳定分类，例如 `workspace_cleanup_failed`，不得保存异常文本或路径；
- table 不对 `sessions` 建 foreign key，因为同一事务会删除 session row。

这不是通用 operation journal。不得在本 Task 扩展为批量删除、审计历史或任意 artifact queue。

## 4. 删除顺序

### Phase A — SQLite logical delete

```text
BEGIN IMMEDIATE
  1. 按 session id 读取 owner/status
  2. 校验 owner 与 terminal status
  3. 将 legacy chat_messages 幂等迁入 owner primary Coach thread
  4. 将 active coach_analysis_refs 标记 deleted
  5. INSERT tombstone(cleanup_state = pending)
  6. DELETE legacy chat_messages
  7. DELETE sessions row
COMMIT
```

任一步失败都 rollback。Phase A 不读取、移动或删除 workspace。

### Phase B — Managed workspace cleanup

```text
remove {DATA_ROOT}/sessions/{analysis_session_id}
  success or already absent:
    DELETE tombstone
  OSError / partial removal:
    UPDATE tombstone
      cleanup_state = failed
      cleanup_attempts += 1
      last_error_code = workspace_cleanup_failed
```

Phase B 只能在 Phase A commit 后运行，不持有 Phase A 的 SQLite write transaction。

## 5. 崩溃与失败矩阵

| 崩溃/失败位置 | SQLite | workspace | 收敛方式 |
|---|---|---|---|
| Phase A commit 前 | rollback，Analysis/Coach 保持原状 | 未触碰 | 用户可重试删除 |
| Phase A commit 后、cleanup 前 | Analysis 已删除，tombstone pending | 保留 | startup reconciliation |
| cleanup 部分失败 | Analysis 已删除，tombstone failed | 可能残缺 | 幂等重试 rmtree |
| cleanup 成功后、删 tombstone 前 | Analysis 已删除，tombstone pending/failed | 已不存在 | reconciliation 将 absent 视为成功并删 tombstone |
| 更新 failed 状态失败 | 原 pending tombstone 仍在 | 保留/残缺 | 下一次 reconciliation 重试 |
| HTTP response 前崩溃 | 以上持久状态为准 | 以上持久状态为准 | 不让 response 决定事实 |

不得通过“先删 workspace、SQLite 失败再声称未删除”处理任何失败。

## 6. Reconciliation

- Desktop runtime 在对 shell 发出 `ready` 前处理全部 pending/failed tombstone；
- 每个 tombstone 独立处理，一个 Windows locked-file failure 不阻止其他记录收敛；
- cleanup failure 不阻止应用启动，但 tombstone 必须保留并返回 aggregate failed count；下次
  startup 或显式 reconciliation 再重试，本 spec 不引入常驻 scheduler；
- 日志只记录稳定 Analysis ref/count/error code，不记录 workspace path 或底层异常文本；
- reconciliation 不扫描没有 tombstone 的未知目录，不推导 uploading/importing orphan policy；
- repeated delete/reconciliation 不得重复迁移 Coach 消息或把 deleted ref 恢复 active。

## 7. API 语义

`DELETE /api/sessions/{id}` 保持现有 response envelope：

```text
deleted = true
id
files_removed[]
cleanup_failed[]
```

- Phase A 未 commit：返回稳定错误，`deleted` 不得为 true；
- Phase A commit 且 cleanup 成功：`deleted=true`、`files_removed=[workspace]`（若原目录存在）、`cleanup_failed=[]`；
- Phase A commit 且 cleanup 失败：仍为 `deleted=true`、`cleanup_failed=[workspace]`；Analysis 不再可读，tombstone 保留到下次 startup 或显式 reconciliation；
- wrong owner 与 non-terminal 行为保持现有 forbidden/active 语义；
- response、日志和 tombstone 均不得返回绝对路径。

本 spec 不承诺删除完成后的长期 idempotency replay；completed tombstone 不保留。

## 8. Migration

- SQLite schema 从 v12 升至 v13；
- v13 DDL 不加入事务前执行的 canonical `SCHEMA` script；fresh install 与 v12→v13 upgrade
  都必须在 `init_schema()` 的外层 migration transaction 内调用同一个 v13 helper；
- migration helper 必须尊重 caller transaction，rollback 后不得残留 table；
- 完整 v12→v13 `init_schema()` 在 helper 创建 table 后、user_version commit 前失败时，
  `user_version` 仍为 12 且 table 不存在；不得只测 helper rollback 而漏过外层顺序；
- newer `user_version` 继续 fail closed，不做降级；
- 现有 terminal sessions 不自动创建 tombstone，只有用户删除请求产生。

## 9. 验收条件

- Phase A 任一注入失败均保持 session、workspace、Coach message/ref 不变；
- Phase A commit 后即使进程退出，tombstone 可在重启时完成清理；
- Windows locked-file/partial rmtree 后记录 failed，解锁后 reconciliation 收敛；
- workspace already absent 时删除和 reconciliation 均成功；
- legacy Coach message exactly-once，Analysis ref exactly-once 转为 deleted；
- Run、Run-owned trace 和用户 source fixture 在删除前后字节不变；
- startup reconciliation 在 Desktop `ready` 前执行；
- API、日志与 tombstone 无绝对路径或底层异常泄露。

## 10. Stop rules

立即停止并由 Sol/点点裁决，如果实现需要：

- 删除或修改用户 source、Run metadata、Run-owned trace 或 Raw Input buffer；
- 定义 TTL、quota、未知 orphan workspace 自动删除或 tombstone retention；
- 改变 PRD 的 terminal-only 删除或 Coach message retention；
- 接收来自 DB/API/frontend 的任意 workspace path；
- 让 frontend 决定恢复顺序；
- 无法用 Windows locked file、SQLite rollback 与 restart fixture 验证收敛。
