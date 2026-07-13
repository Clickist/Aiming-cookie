# KovaaK Run / Raw Input Lifecycle — Design Contract

> 状态：active
> 目的：冻结 KovaaK Stats/Performance discovery、Raw Input buffer、Run persistence、trace attach 与恢复语义。
> 上游：[`../../ARCHITECTURE.md`](../../ARCHITECTURE.md)、[`../assessments/2026-07-13-reflek-capability-adoption.md`](../assessments/2026-07-13-reflek-capability-adoption.md)

## 1. 范围

本 spec 只定义：

- Stats / Performance 文件发现和配对；
- Raw Input 采集、缓冲和 snapshot；
- Run/trace 写入、重试和 reconciliation；
- source availability 与 trace quality；
- 本地 API 的稳定引用边界。

不定义：

- input-native 指标算法；
- AnalysisResult schema；
- History 布局；
- Coach 上下文；
- benchmark、云同步或删除 UI。

## 2. 核心实体

```text
KovaaKRun
  id
  owner/local_profile
  source_key
  discovery_state
  stats_source
  performance_source
  trace_artifact?
  pairing
  capture_quality?
  source_availability
  created_at / updated_at
```

### SourceRef

```text
id                      stable local source id
kind                    stats | performance
path                    DB-internal only
basename                user-visible
fingerprint             sha256 + size + mtime_ns for the observed revision
parser_version
availability            available | missing | invalid | unreadable
observed_at
```

### TraceArtifact

```text
id                      stable artifact id
format_version
path                    DB-internal only
window_start_ms
window_end_ms
point_count
dropped_event_count
button_transition_count
clock_source
capture_backend
quality_status          complete | partial | invalid | unavailable
warnings[]
local_only = true
owner_type = kovaak_run
owner_id = run_id
```

## 3. Discovery / ingestion 状态机

```text
discovered
  → stabilizing
  → ready
  → parsing
  → sources_persisted
  → trace_pending
  → complete | partial

任何可重试失败：
  → retry_wait → ready

不可解析/身份冲突：
  → invalid
```

规则：

1. watcher 只有在 callback/ingestion 成功提交后才能标记 source 已处理；
2. callback future 失败必须撤销或不写 emitted/seen；
3. Stats 与 Performance 可以先后到达，但每次补全都必须重新验证 source identity；
4. 只凭 stem 不能把两份已冲突文件静默拼接；冲突进入 `pairing_conflict`；
5. source 文件移动、删除或修改后，Run 保留，但 availability 更新；
6. 启动扫描必须有范围/retention，不无限制导入全部历史。

## 4. Raw Input 生命周期

### 4.1 授权与进程 gate

- 默认关闭；
- 仅 Windows supported；
- 用户授权、capture enabled、KovaaK process present、runtime healthy 是不同状态；
- 不采集键盘、桌面绝对坐标或非 KovaaK 场景的输入；
- process exit 只停止新事件进入，不清除尚未关联数据。

### 4.2 Buffer 保留

- process exit 不得清空 buffer 或覆盖为空 snapshot；
- 当前默认 rolling retention 为 10 分钟；除非后续 retention spec 修改，不自行扩大或缩短；
- process exit 不触发清空；数据保留到对应 Run attach、自然超过 10 分钟，或用户明确执行“清理未关联 buffer”；
- disable 停止新采集，但不会把 process-exit 误当成用户清理；
- 清理未关联 buffer 必须记录清理原因和丢弃点数；
- orphan trace 默认进入 quarantine，不自动删除；恢复或删除必须由后续生命周期操作显式决定。

### 4.3 Capture thread

- `WM_INPUT` thread 只做必要解析和非阻塞 enqueue；
- 不在 capture thread 中同步写完整 rolling snapshot；
- snapshot/serialization/disk I/O 在独立 worker；
- 队列有明确容量；满时记录 drop count；
- snapshot/write/replace 错误进入 runtime status 和 diagnostics；
- 高 polling rate 下不得因全量每秒重写而形成与 buffer 长度线性增长的持续写放大。

### 4.4 时间

- canonical event 保存 Unix epoch milliseconds；
- 同毫秒多事件按 trace record order 保留顺序；
- `Performance.challenge_start_utc` 是 v1 trace pairing 的 required anchor；
- canonical window 为 `[challenge_start_utc, challenge_start_utc + time_limit_ms]`；v1 不添加隐式 guard；
- 缺少有效 Performance anchor 时不得 attach native trace；
- raw epoch → challenge-relative 的转换由 `TimeAlignment v1` 完成；
- wall-clock 回拨、非单调 timestamp、超大 gap 产生 quality warning；
- alignment 必须记录 `clock_source`、`anchor_source`、`offset_ms`、`coverage_ratio` 和 `status`；不能把闭区间截取本身当成精确证明。

## 5. Trace codec

- Rust/Python 使用同一版本化格式；
- 必须有双方读取同一个 golden fixture 的测试；
- header 至少包含 magic、version、record count 和必要 metadata reference；
- reader 有最大文件、最大点数、最大时间跨度限制；
- 验证 timestamp 顺序、按钮 bitmask、record length；
- canonical point 为 `timestamp_ms / dx / dy / buttons`；
- cumulative x/y 不写回 canonical raw trace，属于 derived artifact。

## 6. Run + trace 持久化

文件与 SQLite 无法形成单一事务，因此使用显式状态与 reconciliation：

```text
1. Run/source transaction 写 trace_state = pending
2. trace 写临时文件并完成完整性检查
3. 原子 rename 到 managed artifact path
4. DB transaction attach artifact id + metadata，state = attached
5. startup reconciliation 扫描：
   - pending DB + file exists → validate and attach
   - pending DB + no file → retry/unavailable
   - orphan file + no DB ref → quarantine/recover/delete by explicit policy
```

禁止：

- 先把 Run 标 complete，再 best-effort 写 trace；
- trace 写失败后保留陈旧 `mouse_trace_path`；
- 以绝对路径作为 API business identifier。

## 7. Ownership / recovery / deletion boundary

### Artifact ownership

- Raw Input trace 是 `KovaaKRun` owned managed artifact；
- Analysis 只能通过 `external_inputs[]` 引用它；
- 删除 terminal Analysis 不得删除 Run-owned trace；
- Run/source 删除或 trace 损坏后，历史 Analysis 保留，但 evidence 状态变为 `unavailable`；
- 用户源 Stats/Performance 文件永远不由 Aiming Cookie 删除。

### Default recovery policy

| 状态/操作 | Run | trace | Analysis reference | 默认行为 |
|---|---|---|---|---|
| terminal Analysis 删除 | 保留 | 保留 | 删除该 Analysis | 不级联 |
| source moved/deleted | 保留 | 保留 | evidence `source_unavailable` | 可重新发现 |
| trace lost/corrupt | 保留 | unavailable | native evidence unavailable | 可重试/降级 |
| orphan trace | 不变 | quarantine | 无引用 | 不自动删除 |
| process exit | 不变 | 保留至 attach/retention | 不变 | 不能清空 |
| disable capture | 不变 | 未关联数据保留至 retention | 不变 | 显式清理才丢弃 |

Run/trace 的用户删除 UI、tombstone 和长期 retention 变更不在本 Task 实现；若未来修改默认策略，必须新增/更新 lifecycle spec。

### TimeAlignment v1

```text
timebase_version = time_alignment.v1
raw_clock_source = system_wall_clock_epoch_ms
anchor_source = performance.challenge_start_utc
offset_ms = raw_event_ts - anchor_ts
guard_before_ms = 0
guard_after_ms = 0
ordered_sequence_key = record_order
coverage_ratio = covered_duration_ms / expected_duration_ms
status = aligned | partial | failed | unavailable
warnings[]
```

所有 Analysis/Coach evidence range 使用 challenge-relative milliseconds；raw epoch 仅保留在本地 trace artifact。

## 8. API 边界

UI 可见：

- `run_id`；
- basename / scenario / time；
- source availability；
- trace availability/quality；
- stable artifact id；
- pairing/alignment status；
- warnings。

UI 不可见：

- 任意绝对 source path；
- raw snapshot path；
- Data root；
- 可被前端回传用于任意文件读取的 path token。

## 9. 最小错误分类

- `source_unstable`
- `source_unreadable`
- `source_parse_failed`
- `pairing_conflict`
- `trace_capture_unavailable`
- `trace_snapshot_failed`
- `trace_quality_insufficient`
- `trace_attach_failed`
- `trace_orphan_recovered`
- `alignment_partial`
- `alignment_failed`

## 10. 验收条件

- KovaaK 退出后 delayed Performance 仍可获得 trace；
- transient ingestion failure 自动重试；
- source identity 冲突不会静默合并；
- Run/trace 崩溃点均能由 reconciliation 收敛；
- high-rate capture path 不执行同步整 buffer 磁盘写；
- snapshot failures 可观察；
- Rust/Python codec golden tests 通过；
- API 不返回绝对路径。
