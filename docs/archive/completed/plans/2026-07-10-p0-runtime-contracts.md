# P0 Runtime Contracts — 可执行施工计划

> 状态：已完成（2026-07-11 集成验收通过并归档）。Task 1–5 不得重复执行。  
> 后续变更：本计划最初冻结的 `max_attempts=1` / 不做 recovery 已被后续批准并完成的 `2026-07-10-p0-worker-recovery.md` 正式替代；当前运行基线以该后续计划为准。  
> 受众：Composer 2.5 Fast 或同等级执行模型  
> 上游事实源：`docs/PRD.md`、`docs/ARCHITECTURE.md`、`docs/ROADMAP.md`  
> 执行规则：**一次只执行一个 Task；完成后停止并提交证据，不得自动进入下一 Task。**

## 1. Goal

在不重构 Domain Core、不拆 `sessions`/`jobs` 表、不实现 History 或完整 worker recovery 的前提下，为当前 Web Runtime 建立第一版可持久化合同：

1. `AnalysisResult v1`：版本化、严格 JSON、可读取 legacy result；
2. `Error v1`：稳定 machine code 与用户安全文案；
3. `JobState v1 foundation`：补齐 attempts/worker/timestamp 字段和最小状态语义；
4. `ArtifactManifest v1`：结果中不暴露机器绝对路径；
5. Python / DB / API / TypeScript / consumer 对同一合同达成一致。

本计划完成后，后续 History、retry/recovery 和 Desktop sidecar 可以依赖明确合同，而不是继续依赖偶然形成的 `dict` 形状。

## 2. Why now

当前实际链路是：

```text
CoachReport dataclass
→ dataclasses.asdict()
→ worker 注入 timeline
→ queue.mark_done(json.dumps)
→ sessions.result TEXT
→ queue.get_session(json.loads)
→ FastAPI Optional[dict]
→ TypeScript 手写 CoachReport 镜像
```

已核验的风险：

- `result` 没有 schema/analysis version；
- Python `json.dumps` 默认允许 `NaN`/`Infinity`；
- DB、API 和 TypeScript 的 error 仍是任意字符串；
- `running` 没有 attempt、worker identity 或 timestamps；
- 前端和 Coach API 直接读取旧的 `result.diagnosis` / `result.figures`；
- 文件路径存在 DB/runtime 内部，结果合同没有 artifact manifest；
- legacy rows 没有明确读取策略。

## 3. Preconditions

执行任一 Task 前必须：

1. 阅读 `AGENTS.md`、`CLAUDE.md`、`docs/PRD.md`、`docs/ARCHITECTURE.md`、`docs/ROADMAP.md` 和本文件；
2. 运行 `git status --short`，记录用户原有改动；
3. 只执行点点或架构负责人明确指定的单个 Task；
4. 不修改 `output/`、设计稿、理论文档或本 Task 未列入 Allowed files 的文件；
5. 不安装依赖，不提交，不推送，不启动下一 Task。

### 3.1 Fast 模型开工前回显

执行模型在改文件前，必须先在回复中逐字列出以下信息，然后才可开始该 Task：

```text
Task: <精确编号和标题>
Allowed files: <逐项列出>
Tests first: <逐项列出精确测试名>
Frozen decisions: no architecture/schema/migration/default decisions may be made
Stop rule: any mismatch or scope expansion stops execution
```

若用户指令没有明确指定 Task 编号，或回显内容无法从本文唯一确定，必须停止；不得从 Roadmap 自行选择“下一个任务”。

## 4. Verified current state

| Area | Current source | Current fact |
|---|---|---|
| Domain report | `kovaak_tracker/coach/diagnosis.py::CoachReport` | `diagnosis`、`figures`、`narration`、`notes`，无版本 envelope |
| Report routing | `kovaak_tracker/coach/report.py::build_report` | tracking 可显式/启发式识别；Web 主线未显式传 `summary_type` |
| Worker | `webapp/backend/worker.py::process_one` | 主线是 flicking；完成时写旧 report dict 并注入 timeline |
| Persistence | `webapp/backend/queue.py` | `result`/`error` 用 TEXT；普通 `json.dumps`；legacy string error |
| DB | `webapp/backend/db.py::SCHEMA` | `sessions` 同时承担 session/job；无 lease/attempt/worker/timestamps |
| API | `webapp/backend/schemas.py::SessionStatus` | `result: Optional[dict]`，`error: Optional[str]` |
| Backend consumers | `webapp/backend/routes.py` | chat/timeline 直接读取旧 report shape |
| Frontend contract | `webapp/frontend/lib/types.ts` | 手写 `CoachReport`；`SessionStatus.result` 直接指向它 |
| Frontend consumers | report/coach/processing routes | 直接读取旧 result 和 string error |
| Artifact access | `webapp/backend/routes.py::get_session_video` | 仍从 DB 内部 `video_path` 读取；本计划不改变该接口 |

如执行时任一事实已变化，**停止并报告差异**，不得自行改写本计划来适配。

## 5. Architecture decisions — 已裁决，不得重新选择

### 5.1 Domain Core 保持不变

本计划不得修改：

- `CoachReport` / `CoachDiagnosis` dataclass；
- `build_report()` 的指标、诊断、处方或 narration 算法；
- flicking/tracking 理论和产品开放范围。

版本化发生在 **Local Analysis Runtime 边界**，不是 Domain Core 内部。

### 5.2 新合同模块位置

新增：

```text
webapp/backend/contracts.py
webapp/frontend/lib/contracts.ts
```

- Python 模块负责 model、normalization、legacy adapter、序列化；
- TypeScript 模块只负责 wire type 对应和 envelope → 现有 `CoachReport` view model 的确定性转换；
- 不引入 schema generator 或新依赖；生成式工具链后置。

### 5.3 API 永远返回 v1 envelope

`GET /api/sessions/{id}` 的 `result`：

- 新 row：返回原生 `AnalysisResult v1`；
- legacy row：读取时包装为 `AnalysisResult v1`，不回写 DB；
- 不允许 API 有时返回 legacy、有时返回 v1；
- 前端不保留双 shape 分支。

### 5.4 DB 兼容策略

- `sessions.result TEXT` 继续保存 JSON，不新增 `result_json`；
- 新写入必须是 `AnalysisResult v1`；
- legacy result 只做 read-time adapter，不做批量回写；
- `sessions.error TEXT` 继续复用：新失败保存 Error v1 JSON，旧的非 JSON string 由 read-time adapter 包装；**不得新增 `error_json`**；
- SQLite schema 使用 `PRAGMA user_version` 管理；本计划建立版本 `1`，不引入外部 migration framework；
- 本计划不拆 `jobs` 表。

### 5.5 版本常量

必须使用以下精确值：

```text
analysis result schema_version = "analysis_result.v1"
analysis_version = "flicking_fair_summary.v1"
summary_type = "flicking"
legacy analysis_version = "legacy_unversioned"
artifact manifest schema_version = "artifact_manifest.v1"
error schema_version = "error.v1"
SQLite PRAGMA user_version = 1
```

这些字符串分别标识不同合同，不得统一改成 `"1.0"`，也不得改成数字、日期、camelCase 或其他枚举。JobState foundation 是 SQLite state-machine contract，由 `PRAGMA user_version` 管理；本计划**不新增** JobState JSON envelope 或 `job_state_schema_version` API 字段。

### 5.6 时间格式

Wire contract 中所有非空时间：

```text
YYYY-MM-DDTHH:MM:SSZ
```

- 统一 UTC；
- SQLite `CURRENT_TIMESTAMP` 的 `YYYY-MM-DD HH:MM:SS` 按 UTC 转为上述格式；
- 新生成时间使用 UTC，秒精度即可；
- legacy 缺失时间使用 `null`，不得伪造当前时间。

## 6. Exact contracts

### 6.1 AnalysisResult v1

所有 key 使用 snake_case。完整 wire shape：

```json
{
  "schema_version": "analysis_result.v1",
  "analysis_version": "flicking_fair_summary.v1",
  "summary_type": "flicking",
  "created_at": "2026-07-10T12:00:00Z",
  "completed_at": "2026-07-10T12:01:00Z",
  "input": {
    "cm_per_360": 34.5,
    "fov": 103.0
  },
  "deterministic": {
    "diagnosis": {},
    "figures": {},
    "timeline": []
  },
  "narration": {
    "status": "not_requested",
    "text": null,
    "provider": null,
    "model": null,
    "usage": null
  },
  "artifact_manifest": {
    "schema_version": "artifact_manifest.v1",
    "inputs": [],
    "outputs": []
  },
  "notes": [],
  "normalization_issues": []
}
```

字段规则：

| Field | Required | Rule |
|---|---:|---|
| `schema_version` | yes | 仅接受 `"analysis_result.v1"` |
| `analysis_version` | yes | 新结果固定 `"flicking_fair_summary.v1"`；legacy 固定 `"legacy_unversioned"` |
| `summary_type` | yes | 本 Web 主线固定 `"flicking"` |
| `created_at` | yes | `string | null`；新结果必须非空，legacy 可为 null |
| `completed_at` | yes | `string | null`；done 新结果必须非空，legacy 可为 null |
| `input.cm_per_360` | yes | `number | null` |
| `input.fov` | yes | `number | null` |
| `deterministic.diagnosis` | yes | 现有 `CoachDiagnosis` 序列化 dict |
| `deterministic.figures` | yes | 现有 Plotly `to_dict()` 结果 |
| `deterministic.timeline` | yes | 现有 timeline event array |
| `narration.status` | yes | `available | unavailable | not_requested` |
| `narration.text` | yes | `string | null`；仅 `available` 时必须为非空 string |
| provider/model/usage | yes | 当前没有可信 metadata，必须写 `null`；不得猜测或重构 provider 层 |
| `artifact_manifest` | yes | 精确结构见 §6.4，不允许退化为裸数组 |
| `notes` | yes | `string[]`，来自现有 `CoachReport.notes` |
| `normalization_issues` | yes | 非有限数被替换时记录；无问题为空数组 |

### 6.2 Strict JSON normalization

递归处理 dict/list/tuple 和标量：

- 正常有限 `int` / `float` 保留；
- `NaN` → `null`；
- `+Infinity` → `null`；
- `-Infinity` → `null`；
- 每次替换追加一个 issue：

```json
{
  "path": "$.deterministic.diagnosis.summary.sparc.med",
  "code": "non_finite_number",
  "original": "nan"
}
```

`original` 仅允许：`"nan" | "+infinity" | "-infinity"`。

最终持久化必须调用：

```python
json.dumps(value, ensure_ascii=False, allow_nan=False)
```

如果 normalization 后仍不可序列化，抛出明确异常，不能回退为 `str(value)`。

### 6.3 Narration metadata v1

```json
{
  "status": "not_requested",
  "text": null,
  "provider": null,
  "model": null,
  "usage": null
}
```

状态判定必须由 worker 显式传入，不能由 adapter 猜测：

- `available`：backend 非空且最终 narration 是非空 string；
- `unavailable`：产品路径希望生成 narration，但 backend 加载失败、provider 失败或最终未产出文本；
- `not_requested`：预算检查明确跳过 LLM，或 legacy result 无法证明曾请求 LLM；
- `available` 时 `text` 必须非空；其他状态 `text` 必须为 null；
- provider/model/usage 在本计划中固定为 null；LLM 成本仍由现有 `llm_cost_cny` 字段承担，不重写 usage 采集。

### 6.4 ArtifactManifest v1

完整 manifest：

```json
{
  "schema_version": "artifact_manifest.v1",
  "inputs": [
    {
      "id": "input-video",
      "kind": "input_video",
      "media_type": "video/mp4",
      "size_bytes": 123456,
      "checksum_sha256": null,
      "status": "available",
      "created_at": "2026-07-10T12:00:00Z"
    }
  ],
  "outputs": []
}
```

Entry 精确规则：

- `id`: input 仅允许 `"input-video"` 或 `"input-stats-csv"`；
- `kind`: input 仅允许 `"input_video"` 或 `"input_stats_csv"`；
- `media_type`: video 用 `"video/mp4"`，CSV 用 `"text/csv"`；
- `size_bytes`: 文件存在且可读时 `os.path.getsize`，否则 `null`；
- `checksum_sha256`: 本计划固定 `null`，不得为此读取整段大视频；
- `status`: `available | missing | deleted`；本计划新建时只会写 `available` 或 `missing`，不得猜测 retention policy；
- `created_at`: session 创建时间，允许 null 的规则与 AnalysisResult `created_at` 相同；
- **禁止出现** `path`、`video_path`、`csv_path` 或任何绝对路径字段。

构建规则：

- source path 非空时必须创建对应 input entry；文件存在且可读为 `available`，否则为 `missing`；
- source path 为空时不创建该 entry；
- 当前 timeline/figures 是 AnalysisResult 内嵌数据，不是独立可寻址文件，因此 `outputs` 固定为空数组；
- `/video` 端点继续使用 DB 内部 `video_path`，不改为按 manifest 查找。

### 6.5 Error v1

```json
{
  "schema_version": "error.v1",
  "category": "internal_unknown",
  "code": "analysis_failed",
  "message": "分析失败，请重试；若持续失败请联系维护者。",
  "retryable": false,
  "trace_id": "uuid-string",
  "details": null
}
```

规则：

- category enum：`input_validation | local_cv_runtime | llm_provider | network_cloud | storage_disk | internal_unknown`；
- 本计划仅改 worker 顶层未知异常：`category="internal_unknown"`、`code="analysis_failed"`、`retryable=false`；
- 新 Error v1 的 `trace_id` 使用 UUID4 string；worker log 必须包含同一 trace id 和完整 exception/traceback；
- `details` 当前固定 `null`；
- exception string 和 traceback 不进入 API message/details；
- 新失败严格 JSON 序列化后写入现有 `sessions.error TEXT`；不得新增平行 error 列；
- legacy `sessions.error` 非 JSON string 读取时包装为：
  - schema_version `error.v1`
  - category `internal_unknown`
  - code `legacy_error`
  - message 固定为 `"分析失败，请重试；若持续失败请联系维护者。"`，不得把旧 exception string 暴露给客户端
  - retryable `false`
  - trace_id `null`
  - details `null`
- 若 `sessions.error` 可解析为 object 且带未知 `schema_version`，必须明确拒绝；普通非 JSON string 才走 legacy adapter；
- HTTP validation/404/409/429 error 不纳入本计划，不要统一 FastAPI 全局 error shape。

### 6.6 JobState v1 foundation

状态枚举仍为：

```text
queued | running | done | failed
```

`sessions` 新增：

```text
attempts INTEGER NOT NULL DEFAULT 0
max_attempts INTEGER NOT NULL DEFAULT 1
worker_id TEXT
lease_expires_at TEXT
heartbeat_at TEXT
started_at TEXT
finished_at TEXT
```

精确语义：

- enqueue：`attempts=0`、`max_attempts=1`；默认 `1` 用于保持当前“没有自动 retry”的真实行为，不得预填尚未实现的 3 次重试；
- claim：只选择 `status='queued' AND attempts < max_attempts` 的最旧 row；同一原子事务中 `queued → running`，`attempts = attempts + 1`，写 `worker_id`，首次写 `started_at=CURRENT_TIMESTAMP`；
- mark done/failed：写 `finished_at=CURRENT_TIMESTAMP`；
- 本计划中 `lease_expires_at` 和 `heartbeat_at` 保持 null；
- 不允许在本计划实现 stale recovery、heartbeat loop、自动 retry 或显式 retry endpoint；
- worker id 使用进程内常量 `f"{socket.gethostname()}:{os.getpid()}"`；
- legacy row migration defaults：attempts `0`、max_attempts `1`，其他新列 null；不得猜测历史 attempt/timestamps；
- migration 必须通过 `PRAGMA user_version` 从 `0 → 1`：在一个事务中补齐 `cm_per_360`、`fov` 和本节新增列，全部成功后才执行 `PRAGMA user_version = 1` 并 commit；版本高于 `1` 时安全失败，不得静默降级。

API `SessionStatus` 保持顶层 `status`，并新增且始终返回以下字段；不要自行改成嵌套 JobState envelope：

```text
created_at: string
attempts: int
max_attempts: int
worker_id: string | null
started_at: string | null
finished_at: string | null
```

`lease_expires_at` / `heartbeat_at` 暂不暴露给前端，留给 worker recovery plan。

## 7. Compatibility matrix

| Stored row | DB read | API result/error | DB rewrite |
|---|---|---|---|
| v1 result + Error v1 JSON in `sessions.error` | validate named versions | v1 | no |
| legacy report dict | wrap为 AnalysisResult v1 | v1 | no |
| unknown `schema_version` | raise `UnsupportedContractVersion` | request fails clearly; log version | no |
| legacy string error | wrap为 Error v1 | Error v1 | no |
| null result/error | 保持 null | null | no |
| malformed JSON text | 保持现有安全降级为 null，并记录 warning log | null | no |

Legacy report shape：

```json
{
  "diagnosis": {},
  "figures": {},
  "narration": null,
  "notes": [],
  "timeline": []
}
```

Legacy adapter：

- diagnosis/figures/timeline 进入 `deterministic`；
- narration 非空 string 时进入 `narration.text` 且 status=`available`；否则 text=null、status=`not_requested`，不得猜测历史 provider 失败；
- notes 保留；
- `artifact_manifest` 使用 `artifact_manifest.v1` + 空 inputs/outputs（legacy 不从绝对路径反推长期合同）；
- input 参数从 session row 的 `cm_per_360` / `fov` 读取；
- created/completed 从 row 的 `created_at` / `updated_at` 转换，缺失则 null；
- `schema_version="analysis_result.v1"`；
- `analysis_version="legacy_unversioned"`；
- `summary_type="flicking"`。

## 8. Consumer adapter — 不允许每个页面自行理解 envelope

### Python

`webapp/backend/contracts.py` 提供单一 helper：

```python
analysis_result_to_coach_report(result_v1) -> dict
```

返回现有内部 shape：

```json
{
  "diagnosis": {},
  "figures": {},
  "narration": null,
  "notes": [],
  "timeline": []
}
```

`routes.py` 的 chat/timeline 先调用该 helper，再沿用现有内部逻辑。

### TypeScript

`webapp/frontend/lib/contracts.ts` 提供：

```ts
analysisResultToCoachReport(result: AnalysisResultV1): CoachReport
```

- diagnosis/figures 来自 `deterministic`；
- narration 来自 `narration.text`；
- notes 来自顶层；
- 页面不得复制这段 mapping。

## 9. Out of scope — 禁止顺手实现

- 完整 lease / heartbeat / stale recovery / retry；
- `jobs` 与 `sessions` 分表；
- History 列表、详情、趋势、删除；
- JSONL → SQLite 全量导入；
- `sessions/<id>/` workspace 文件迁移；
- retention 默认天数、orphan scan、quota、低磁盘保护；
- auth、JWT、SSO、VPN、可信代理；
- Desktop shell/sidecar、cloud sync；
- provider/model/usage 真实采集；
- schema-generated TypeScript 工具链；
- Domain Core 指标、diagnosis、profile、处方重构；
- tracking 产品开放；
- `/video` 的 manifest 化；
- HTTP 全局错误协议；
- 新第三方依赖；
- UI 视觉重设计。

## 10. Task 1 — Python contract module + contract tests

### Goal

先建立纯合同和 adapter，不接 worker/DB/API。

### Allowed files

- Create: `webapp/backend/contracts.py`
- Create: `webapp/tests/test_contracts.py`

### Tests first

必须先创建以下精确测试名并确认至少一个因实现缺失而失败：

```text
test_build_analysis_result_v1_exact_shape
test_non_finite_numbers_become_null_with_issue_paths
test_dump_contract_json_rejects_remaining_non_json_values
test_legacy_report_is_wrapped_without_db_rewrite
test_unknown_schema_version_is_rejected
test_analysis_result_to_coach_report_restores_internal_shape
test_build_artifact_manifest_does_not_expose_paths
test_build_artifact_manifest_marks_missing_input
test_narration_status_must_match_text
test_legacy_error_is_wrapped_as_safe_error_v1
```

### Required public API

`contracts.py` 必须导出以下常量和 public API；不得用一个含糊的 `SCHEMA_VERSION` 代替多个具名版本：

```python
ANALYSIS_RESULT_SCHEMA_VERSION = "analysis_result.v1"
ANALYSIS_VERSION = "flicking_fair_summary.v1"
LEGACY_ANALYSIS_VERSION = "legacy_unversioned"
SUMMARY_TYPE = "flicking"
ARTIFACT_MANIFEST_SCHEMA_VERSION = "artifact_manifest.v1"
ERROR_SCHEMA_VERSION = "error.v1"

class UnsupportedContractVersion(ValueError): ...

def normalize_json_value(value, *, path: str = "$") -> tuple[object, list[dict]]: ...

def build_artifact_manifest_v1(
    *, video_path: str | None, csv_path: str | None, created_at: str | None,
) -> dict: ...

def build_analysis_result_v1(
    *, report: dict, timeline: list[dict], narration_status: str,
    cm_per_360: float | None, fov: float | None,
    artifact_manifest: dict, created_at: str, completed_at: str,
) -> dict: ...

def coerce_analysis_result_v1(
    stored_result: dict | None, *, cm_per_360: float | None = None,
    fov: float | None = None, created_at: str | None = None,
    updated_at: str | None = None,
) -> dict | None: ...

def dump_contract_json(value: object) -> str: ...
def analysis_result_to_coach_report(result_v1: dict) -> dict: ...

def build_error_v1(
    *, category: str, code: str, message: str, retryable: bool,
    trace_id: str | None, details: object | None = None,
) -> dict: ...

def coerce_error_v1(stored_error: str | dict | None) -> dict | None: ...
```

允许增加仅供模块内部使用的 private helper；public 参数和返回语义不得自行改变。Python 版本若不支持上述 annotation syntax，停止并报告，不得悄悄改合同。

### Verification

```bash
.venv/bin/python -m pytest webapp/tests/test_contracts.py -q
```

### Acceptance checklist

- [ ] exact shape 与 §6 完全一致；
- [ ] NaN/Infinity 全部转 null 且记录精确 path；
- [ ] `allow_nan=False`；
- [ ] legacy adapter 不写 DB；
- [ ] unknown version 明确拒绝；
- [ ] artifact 无 path；
- [ ] 仅改 Allowed files。

### Stop conditions

- 项目 Pydantic/stdlib 能力不足且需要新增依赖；
- 发现当前 stored result 不是 §7 的 legacy shape；
- 无法在不修改 Domain Core 的情况下实现；
- 测试要求与当前 Python 版本冲突。

发生任一情况：停止并报告，不扩大文件范围。

## 11. Task 2 — Worker 写入 AnalysisResult v1

### Depends on

Task 1 已通过并经 review 批准。

### Allowed files

- Modify: `webapp/backend/worker.py`
- Modify: `webapp/backend/queue.py`
- Modify: `webapp/tests/test_worker.py`
- Modify: `webapp/tests/test_queue.py`

### Tests first

新增/更新测试，精确覆盖：

```text
test_process_one_happy_path_writes_analysis_result_v1
test_process_one_over_budget_keeps_v1_narration_null
test_process_one_normalizes_non_finite_values_before_persisting
test_mark_done_uses_strict_json_serialization
```

### Implementation steps

1. `run_report()` 仍返回旧内部 CoachReport dict，不修改 Domain Core；
2. `process_one()` 构建 timeline 后，调用 Task 1 helper 创建 v1 envelope；
3. `summary_type` 明确固定 `flicking`，不得依赖 `build_report` heuristic；
4. created_at/input/path 从 claimed session row 取；completed_at 在完成时生成；
5. worker 按 §6.3 显式计算 narration status：预算跳过=`not_requested`，backend 加载/生成失败=`unavailable`，有非空文本=`available`；
6. artifact manifest 按 §6.4 构建；
7. `queue.mark_done()` 用 `dump_contract_json()` 或等价 strict path；
8. 不改变预算行为、LLM 降级行为或成功后文件保留行为。

### Verification

```bash
.venv/bin/python -m pytest webapp/tests/test_contracts.py webapp/tests/test_worker.py webapp/tests/test_queue.py -q
```

### Acceptance checklist

- [ ] 新写入 result 永远是 v1；
- [ ] narration status 与预算/backend/text 路径一致，provider/model/usage 未猜测；
- [ ] timeline 位于 deterministic 下；
- [ ] DB JSON 无 NaN/Infinity token；
- [ ] 两个 input artifacts 不含 path；
- [ ] 仅改 Allowed files。

### Stop conditions

- 需要修改 `kovaak_tracker/`；
- 需要改变报告字段或分析算法；
- 需要为 artifacts 读取完整视频做 checksum；
- 需要实现文件移动/删除策略。

## 12. Task 3 — DB JobState foundation + Error v1

### Depends on

Task 1、Task 2 已通过并经 review 批准。不得与 Task 2 并行修改 `queue.py` / `worker.py`。

### Allowed files

- Modify: `webapp/backend/db.py`
- Modify: `webapp/backend/queue.py`
- Modify: `webapp/backend/worker.py`
- Modify: `webapp/tests/test_db.py`
- Modify: `webapp/tests/test_queue.py`
- Modify: `webapp/tests/test_worker.py`

### Tests first

```text
test_init_schema_migrates_v0_to_v1_transactionally
test_init_schema_rejects_newer_user_version
test_enqueue_initializes_attempt_defaults
test_claim_next_increments_attempt_and_records_worker
test_claim_next_skips_exhausted_job
test_mark_done_records_finished_at
test_mark_failed_writes_error_v1_without_exception_details
test_get_session_wraps_legacy_string_error
```

### Implementation steps

1. 在 fresh schema 中加入 §6.6 列；
2. 把现有 additive helper 收进明确的 `0 → 1` migration：同一事务补齐 `cm_per_360`、`fov` 和 §6.6 列，全部成功后才设置 `PRAGMA user_version = 1`；不得引入外部 framework；
3. `PRAGMA user_version > 1` 时抛出明确错误；重复 `init_schema()` 不再重复迁移；
4. `claim_next(worker_id)` 在现有 `BEGIN IMMEDIATE` 事务内更新 attempt/worker/start，并跳过 exhausted row；
5. worker 定义单个进程级 `WORKER_ID` 并传入；
6. worker 顶层 exception 生成 Error v1；log 使用同一 trace id 记录完整异常；
7. `mark_failed()` 用 strict JSON 写现有 `sessions.error`；不得新增 `error_json`；
8. `get_session()` 对 Error v1 JSON / legacy string 做 read-time coerce；
9. 不实现 lease、heartbeat 或 retry。

### Verification

```bash
.venv/bin/python -m pytest webapp/tests/test_db.py webapp/tests/test_queue.py webapp/tests/test_worker.py -q
```

### Acceptance checklist

- [ ] migration `0 → 1` 事务化、可重复 init，且拒绝未来版本；
- [ ] claim 保持 oldest queued + atomic；
- [ ] attempt 仅 claim 时递增；
- [ ] worker id 格式固定；
- [ ] API 安全文案不含原 exception；
- [ ] legacy error 可读；
- [ ] lease/heartbeat 仍为空；
- [ ] 仅改 Allowed files。

### Stop conditions

- 现有 DB 中出现本计划未覆盖的同名列/不同语义；
- SQLite migration 需要破坏性重建表；
- 原子 claim 无法在现有事务内保持；
- 测试必须实现 retry/recovery 才能通过。

## 13. Task 4 — API、backend consumers 与 legacy read path

### Depends on

Task 1–3 已通过并经 review 批准。

### Allowed files

- Modify: `webapp/backend/schemas.py`
- Modify: `webapp/backend/routes.py`
- Modify: `webapp/backend/queue.py`
- Modify: `webapp/tests/test_routes.py`
- Modify: `webapp/tests/test_routes_chat.py`
- Modify: `webapp/tests/test_routes_coach.py`
- Modify: `webapp/tests/test_e2e.py`

### Tests first

```text
test_get_session_returns_v1_result_for_new_row
test_get_session_wraps_legacy_result_as_v1
test_get_session_returns_error_v1
test_chat_accepts_v1_result_via_contract_adapter
test_timeline_accepts_v1_result_via_contract_adapter
test_session_status_exposes_job_state_foundation_fields
```

### Implementation steps

1. Pydantic schema 明确描述 AnalysisResult/Error，不再用裸 `dict`/`str`；
2. `queue.get_session()` 将 parsed row 交给 contract coerce；
3. `/sessions/{id}` 始终返回 v1 result/error；
4. chat 和 timeline 调用唯一 Python adapter 获取旧内部 view；
5. 不修改 chat agent、timeline event schema 或 video endpoint；
6. legacy row 测试必须通过直接插入 legacy JSON 验证，不允许只 mock adapter。

### Verification

```bash
.venv/bin/python -m pytest webapp/tests -q
```

### Acceptance checklist

- [ ] API 不返回双 shape；
- [ ] legacy DB row 无需回写即可用；
- [ ] chat/timeline 不直接解析 v1 字段；
- [ ] unknown schema version 清晰失败并被 log；
- [ ] `created_at`、attempt/worker/start/finish 字段始终存在且时间已转为 wire UTC 格式；
- [ ] video endpoint 行为不变；
- [ ] 仅改 Allowed files。

### Stop conditions

- API consumer 还存在未列出的 result 直读；
- 需要修改 chat/domain 算法；
- 需要批量迁移用户数据；
- Pydantic response validation 与 exact contract 冲突且无法在 Allowed files 内解决。

## 14. Task 5 — TypeScript contract + frontend consumers

### Depends on

Task 4 API shape 已固定并通过测试。

### Allowed files

- Modify: `webapp/frontend/lib/types.ts`
- Modify: `webapp/frontend/lib/api.ts`
- Create: `webapp/frontend/lib/contracts.ts`
- Modify: `webapp/frontend/app/sessions/[id]/report/page.tsx`
- Modify: `webapp/frontend/app/sessions/[id]/coach/page.tsx`
- Modify: `webapp/frontend/app/sessions/[id]/page.tsx`

如果仓库在执行前已经具备前端测试基线，可在获得架构负责人批准后新增对应 `lib/contracts.test.ts`；没有批准不得安装依赖或修改 `package.json`。

### Exact TypeScript types

必须新增：

```ts
export type AnalysisSchemaVersion = "analysis_result.v1";
export type AnalysisVersion = "flicking_fair_summary.v1" | "legacy_unversioned";
export type AnalysisSummaryType = "flicking";
export type ArtifactManifestSchemaVersion = "artifact_manifest.v1";
export type ErrorSchemaVersion = "error.v1";
export type ErrorCategory =
  | "input_validation"
  | "local_cv_runtime"
  | "llm_provider"
  | "network_cloud"
  | "storage_disk"
  | "internal_unknown";
export type NarrationStatus = "available" | "unavailable" | "not_requested";
export type ArtifactStatus = "available" | "missing" | "deleted";
```

并为 §6 的全部对象定义以下具名 interface：`NarrationMetadataV1`、`ArtifactEntryV1`、`ArtifactManifestV1`、`NormalizationIssueV1`、`AnalysisResultV1`、`ErrorV1`。不得把 `artifact_manifest` 改回 array。

`SessionStatus` 保持现有 `id/status/llm_cost_cny`，并精确改为：

```ts
result: AnalysisResultV1 | null;
error: ErrorV1 | null;
created_at: string;
attempts: number;
max_attempts: number;
worker_id: string | null;
started_at: string | null;
finished_at: string | null;
```

### Implementation steps

1. 保留现有 `CoachReport` 作为 UI view model，不伪装成 wire model；
2. 在唯一 adapter 中把 AnalysisResult v1 转成 CoachReport；
3. report/coach 页面只调用 adapter；
4. processing/report error UI 显示 `status.error?.message`；
5. 更新 `lib/api.ts` 的旧 CoachReport 注释，使其明确 `result` 是 AnalysisResult v1；不要改 FastAPI 非 2xx 的 `apiError()` 行为；
6. 不修改视觉层级、文案之外的交互或导航；
7. 不为 legacy shape 添加 TypeScript union；legacy 已由 API 统一。

### Verification

```bash
cd webapp/frontend && npm run build
```

若仓库已有且不需安装依赖的 test script，再运行对应 test；否则报告“未运行前端单测：当前 Task 禁止新增测试依赖”。

### Acceptance checklist

- [ ] wire model 与 UI model 明确分离；
- [ ] 页面不复制 mapping；
- [ ] error 读取 `.message`；
- [ ] 无 legacy union/any fallback；
- [ ] build 通过；
- [ ] 仅改 Allowed files。

### Stop conditions

- 当前 API 实际返回与 Task 4 不一致；
- 需要修改 `package.json` 或安装依赖；
- 发现更多前端 result/error consumer；
- 需要视觉重构才能适配。

## 15. Task 6 — Contract integration gate

### Depends on

Task 1–5 全部完成并分别 review。

### Allowed files

默认不修改业务代码。只允许修复由本计划合同迁移直接造成、且经架构负责人明确批准的遗漏。

### Required checks

```bash
.venv/bin/python -m pytest webapp/tests -q
.venv/bin/python -m pytest tests -q
cd webapp/frontend && npm run build
```

然后运行静态搜索：

```bash
rg -n 'result\.diagnosis|result\.figures|result\.narration|result\.timeline' webapp/backend webapp/frontend
rg -n 'error: Optional\[str\]|error: string \| null' webapp/backend webapp/frontend
rg -n 'json\.dumps\(' webapp/backend/queue.py webapp/backend/contracts.py
rg -n 'error_json|schema_version = "1.0"|max_attempts.*DEFAULT 3' webapp docs/superpowers/plans/2026-07-10-p0-runtime-contracts.md
```

预期：

- 第一条不得发现绕过 adapter 的旧 wire-shape 读取；
- 第二条不得发现旧 SessionStatus string error；
- 第三条所有 result/error contract 持久化路径可证明 `allow_nan=False`；
- 第四条不得发现本计划已禁止的平行 error 列、通用版本字符串或虚假 3 次 retry 默认值。

### Acceptance checklist

- [x] backend tests 全绿：102 passed, 1 skipped；
- [x] core tests 全绿：116 passed；
- [x] frontend build 全绿；
- [x] legacy result/error integration tests 存在并通过；
- [x] 本次验收未修改 `output/`；工作区已有 tracked output 改动被保留；
- [x] `git diff --check -- CLAUDE.md docs` 通过；全工作区检查仅命中验收前已存在的 `webapp/tests/test_queue.py:377` 末尾空行，本次未越界修改。

> 验收说明：旧 wire-shape 读取只命中唯一前端 adapter；strict JSON 写入使用 `allow_nan=False`。静态搜索命中的 `max_attempts DEFAULT 3` 来自之后已批准并完成的 worker recovery 计划，不是本合同迁移遗漏。

## 16. Rollback

本计划不做批量 DB 回写，因此 rollback 原则：

- 代码回退后，旧代码不能理解 v1 result；所以任何部署 rollback 前必须保留支持 v1 的 read adapter，或明确丢弃仅开发环境新 row；
- 不得通过手工 SQL 把 v1 envelope 拍平成 legacy report；
- 新增列可留在 SQLite 中，旧代码会忽略；不做 DROP COLUMN；
- 若 Task 尚未合并，只回退该 Task 自己的改动，不覆盖用户已有修改。

## 17. Required completion report for every Task

执行模型完成单个 Task 后必须停止，并按以下格式返回：

```markdown
## Task completed
- Task: <number + name>
- Changed files: <exact list>
- Contract decisions made: none / <must be none unless plan explicitly allowed>

## Verification
- `<command>`: PASS/FAIL
- `<command not run>`: NOT RUN — <reason>

## Acceptance checklist
- [x] ...
- [ ] ... — <why>

## Deviations / risks
- none / <exact mismatch>

## Workspace
- Pre-existing changes preserved: yes/no
- `git status --short`: <paste relevant output>
```

禁止以“顺便完成了下一 Task”“做了一些改进”“基本可用”作为完成报告。

## 18. Global stop conditions

出现以下任一情况必须停止并请求强模型裁决：

- 本文件与当前代码事实不一致；
- 需要修改 Allowed files 之外的文件；
- 需要选择新的 schema 字段、枚举、默认值或 migration 语义；
- 需要兼容第三种 stored result/error shape；
- 需要删除、重命名或批量迁移现有数据；
- 测试通过需要扩大产品范围或改变 Domain Core；
- 发现安全、隐私或数据丢失风险；
- 用户工作区改动与 Task 发生冲突；
- 指定验证命令不可运行。

停止时只报告：事实差异、受影响 Task、两种以内可选方案及代价。不得自行选择。
