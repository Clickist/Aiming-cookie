# webapp 后端 review（2026-07-08）

**scope**：`webapp/backend/` 全部 + `webapp/tests/` 后端测试
**reviewer**：webapp 后端 reviewer agent
**健康度**：**B+（v1 可上线，但有几条并发/IDOR/budget 一致性问题应在或紧随 v1 上线前处理）**

07-07 五项修复全部落地且方向正确（见末尾§验证），无回归迹象。核心问题集中在三处：**budget 计量一致性**（check 不 record + chat 失败路径漏记账）、**IDOR**（所有 session 读写端点无 ownership 校验，已知 backlog 跟 Clerk）、**TOCTOU 竞争**（单用户单 job + budget pre-check 两处都有 check-then-act 窗口）。

无 Critical（无 SQL 注入路径、API key 全走 env var 不触达客户端、无 RCE/路径穿越——`_USER_ID_RE` + 扩展名白名单已堵住）。

---

## 分级清单

### Critical

无。

最接近的候选是 IDOR + budget drain 组合，但 v1 是单用户 dev 场景 + IDOR 已在 PROGRESS.md 539 行标为 Clerk slice 3 backlog，不构成 release blocker。上线前必须加 ownership 校验（见 H-3）。

### High

#### H-1. budget check_and_record 只读不写 + chat 失败路径漏记 cost

**文件**：`webapp/backend/llm_budget.py:31-38`、`webapp/backend/routes.py:236-244`

**问题**：
1. `check_and_record` 名字暗示"检查并记录"，实际**只读 today_total 做预测，不写任何东西**（docstring 已承认"实际记账由 queue.mark_done 写"）。预检查与实际记账之间有一个 TOCTOU 窗口：并发请求都看到旧 total，全部过预检查。
2. chat 路径在 `reply is None` 时**跳过 `add_llm_cost`**（routes.py:240-244），但 `reply is None` 有三种成因：
   - `backend is None`（LLM 没调）→ 不记账 ✓ 正确
   - `chat_with_coach` 返回 None（agent 跑完 N 轮没出回复，**LLM 实际调过**）→ 不记账 ✗ 漏
   - `chat_with_coach` 抛异常（LLM 调到一半挂，**LLM 实际调过**）→ 不记账 ✗ 漏

   后两种情况下真实 token 已消耗，budget 却没更新。重复触发"agent 空转"路径可让 budget 永远停在预检查水平。

**对比**：worker 路径（worker.py:164-165）在 narration=None 时**仍记 cost**（用 `_estimate_llm_cost_cny("")` 走 min_output_tokens=500 下限），因为 LLM 确实被调过。chat 路径与 worker 路径**行为不一致**。

**影响**：budget 可被绕过（后两种场景重复触发），freemium 计量失准。单用户 dev 下实际风险低，但上 freemium 后是真实的成本黑洞。

**建议**：
- 把"是否调过 LLM"作为记账判据，而非"是否产出 reply"：当 `backend is not None` 时（无论 reply 是否 None），都调 `add_llm_cost`。
- 中期把 `check_and_record` 改名 `check_budget`（消除"已记录"的误导），或让它真的原子记录（SQLite 事务包 check+insert 一条 budget ledger 行）。后者是更彻底的修复，但需加一张 `budget_spends` 表。v1 可只做改名 + chat 记账修正。

---

#### H-2. chat 并发可绕过 budget 预检查（TOCTOU）

**文件**：`webapp/backend/routes.py:193-195`、`webapp/backend/llm_budget.py:31-38`

**问题**：budget 预检查是 read-then-act 模式：
```
total = await _today_total(user_id)   # 读
return (total + cost) <= BUDGET        # 算
# 实际 add_llm_cost 在几十秒后的 reply 后
```

两个并发 chat 请求同时进预检查，都看到同一 total，都过；后续都调 LLM + 都 add_llm_cost。预算可超 `LLM_DAILY_BUDGET_CNY`。

**影响**：chat 耗时 10-30s（LLM 同步调用），TOCTOU 窗口很大。无前端/UI 防并发（用户狂点发送），freemium 下可超额。

**建议**：
- 短期：chat 端点加 per-user 内存锁（`asyncio.Lock` 字典 keyed by user_id），把"预检查 → 调 LLM → 记 cost"串行化。注意 worker 用同 user_id 也在写 cost，但 worker 在另一进程，内存锁管不到——进程间只能靠 DB 事务。
- 中期：`budget_ledger` 表 + `BEGIN IMMEDIATE` 事务原子 check-and-insert（对齐 worker 的 `claim_next` 模式）。

---

#### H-3. 所有 session 读写端点缺 ownership 校验（IDOR）

**文件**：`webapp/backend/routes.py:89-101`（get_session）、`163-264`（chat POST）、`267-281`（chat GET）、`289-302`（video）、`305-348`（timeline）

**问题**：所有 `/api/sessions/{session_id}/...` 端点都**不校验调用者是否拥有该 session**。session_id 是 AUTOINCREMENT 整数，可枚举。任何调用者都能：
- 读他人 session 的诊断结果（`GET /sessions/{id}`）
- 读他人视频（`GET /sessions/{id}/video`）
- 读/写他人 chat 历史（`GET/POST /sessions/{id}/chat`）
- 消耗他人 budget（`POST /sessions/{id}/chat` 扣 session.user_id 的额度）

`/analyze` 端点的 `X-User-Id` header（routes.py:42）完全是客户端自报，无签名。

**影响**：
- 隐私泄漏（诊断结果、视频、对话历史）
- budget 盗用（chat 消耗他人额度）
- v1 开放注册后，第一个真实用户就受影响

**已知**：PROGRESS.md:539 标记"IDOR / session ownership（跟 Clerk slice 3）"。但 PRD §9 说 v1 开放注册，且 IDOR 影响的是真实用户隐私，不应等 Clerk。

**建议**：
- v1 最小修复：所有 `/sessions/{id}/...` 端点加 `X-User-Id` header + `s["user_id"] != x_user_id → 403`。当前已有 `_USER_ID_RE` 校验可复用。
- 中期：换 Clerk session token（已计划）。

---

#### H-4. has_active + enqueue 单用户单 job 防护有 TOCTOU 窗口

**文件**：`webapp/backend/routes.py:52-53`、`webapp/backend/queue.py:97-105`

**问题**：
```python
if await queue.has_active(x_user_id):   # check
    raise HTTPException(429, ...)
# ... 写文件 ...
sid = await queue.enqueue(...)           # act
```

两个并发 `/analyze` 请求同 user_id，都过 `has_active`（因为都还没 enqueue），都入队。单用户单 job 限制被绕过。

**影响**：用户并发提交多个视频，worker 串行处理 → 积压；磁盘/tmp 涨。freemium 下可被滥用刷免费 CV 算力。

**建议**：把 check + enqueue 包进一个 `BEGIN IMMEDIATE` 事务（对齐 `claim_next` 模式），或加 `UNIQUE` 约束 / INSERT ... WHERE NOT EXISTS。

---

### Medium

#### M-1. worker 崩溃后 job 卡 running 永远不回收

**文件**：`webapp/backend/queue.py:43-47`（claim_next 置 running）、`webapp/backend/worker.py:138-177`

**问题**：`claim_next` 置 `status='running'` 后，若 worker 进程在 `run_analysis` 中崩溃（OOM、SIGKILL、断电），session 永远停在 `running`。`claim_next` 只挑 `status='queued'` 的，不会回头捡 `running` 的。`has_active` 把 `running` 算 active，导致该用户**再也无法提交新 job**（永久 429）。

**影响**：单次 worker 崩溃就永久锁死一个用户。v1 真实使用中 OpenCV 处理异常视频有崩溃风险。

**建议**：
- 加 stale-job 回收：`claim_next` 同时捞 `status='running' AND updated_at < now - 10min` 的（10min 远超正常分析 ~160s）；或起一个定时 GC。
- 或在 `has_active` 中只算"近期"的 running（旧 running 视为 stale）。

---

#### M-2. 分析任务无超时，卡死则 worker 整体停摆

**文件**：`webapp/backend/worker.py:145-148`、`180-189`

**问题**：`process_one` 在 event loop 里**同步调 `run_analysis`**（OpenCV + numpy CPU 密集，~160s）。如果 `analyze_flicking_fair_summary` 因异常视频（损坏 mp4、坏帧）在 `cv2.VideoCapture.read()` 上无限挂，整个 worker event loop 卡死，`asyncio.sleep(2)` 也不跑，新 job 永远不被 claim。

**影响**：一个坏视频就能 DoS 整个 worker。

**建议**：`await asyncio.wait_for(asyncio.to_thread(run_analysis, ...), timeout=600)` 给硬上限（10min），超时则 cancel + mark_failed。注意当前 `run_analysis` 是同步直接调用，需要先搬进 `to_thread` 才能被 `wait_for` 取消。

---

#### M-3. `_estimate_llm_cost_cny` 硬编码 DeepSeek 单价，切 provider 后严重失准

**文件**：`webapp/backend/worker.py:109-119`

**问题**：`¥1/1M input + ¥2/1M output` 是 DeepSeek 单价。`LLM_PROVIDER` 配 Anthropic（Claude Sonnet ~$3/1M input）或 OpenAI 时，budget 计量偏低数十倍，freemium 预算形同虚设。

**影响**：切 provider 后 budget 失准；DeepSeek 涨价也跟着失准。

**建议**：从 `providers.json` 读单价字段（每 provider 加 `price_input_cny_per_mtOk`、`price_output_cny_per_mtok`），或维护一张单价表。v1 只用 DeepSeek 可先加 TODO + 硬 alert（切 provider 时人工更新）。

---

#### M-4. `DB_PATH` 默认相对路径，web 与 worker 启动目录不同则用不同 DB

**文件**：`webapp/backend/config.py:7-21`

**问题**：默认 `sqlite+aiosqlite:///./aiming_cookie_dev.db` → `./aiming_cookie_dev.db`（相对 cwd）。README 示例从仓库根启 uvicorn + worker，但如果从不同目录启动（如生产 systemd unit 的 WorkingDirectory 不一致），两个进程连不同 DB 文件，worker 永远看不到 web 的入队。

**影响**：部署时踩坑——web 能入队但 worker 不消费，看起来像 worker 挂了。

**建议**：默认值改绝对路径（如 `Path(__file__).parent.parent.parent / "aiming_cookie_dev.db"`），或强制要求部署层设 `DATABASE_URL` 环境变量（README 显式标注）。

---

#### M-5. worker process_one 的 sync 工作阻塞 event loop

**文件**：`webapp/backend/worker.py:138-148`

**问题**：`process_one` 是 async 函数，但 `run_analysis` / `run_report` / `_load_backend` 全是同步阻塞调用，直接在 event loop 线程跑。期间所有 async 任务（包括 `_run_loop_async` 的 `asyncio.sleep(2)`）被阻塞。

**影响**：当前 worker 只跑消费循环，无其他 async 任务，实际无 observable 伤害。但：
1. 后续加 health check / metrics / stale-job GC 等周期任务时，会被分析阻塞。
2. `claim_next` 的 DB 操作和 `mark_done` 也被串行化（本就串行，无并发收益可失）。
3. chat 路径的 `asyncio.to_thread`（07-07 修复）说明团队已意识到 LLM 同步调用该进线程池——worker 的 CV 同步调用同理。

**建议**：`summary, extras = await asyncio.to_thread(run_analysis, ...)`，与 chat 路径模式一致。这样也为 M-2 的 `wait_for` 超时铺路。

---

#### M-6. 无任何 rate limit

**文件**：全 `routes.py`

**问题**：除 `/analyze` 的单用户单 job 检查外，`/sessions/{id}`、`/sessions/{id}/chat`、`/sessions/{id}/video` 等端点无频率限制。匿名调用者可高频枚举 session_id + 拉视频/诊断结果。

**影响**：v1 开放注册后，bot 可枚举拉空所有用户数据。

**建议**：加 `slowapi` 或 nginx 层 rate limit。至少对 `/sessions/{id}/...` 加 per-IP 限制。

---

#### M-7. 视频文件成功路径不删，磁盘无限累积

**文件**：`webapp/backend/worker.py:169-171`

**问题**：worker 成功路径保留视频（coach 页播放需要），但无 TTL / GC / 归档机制。注释已承认"归档/清理策略由部署层另行处理（磁盘累积风险，点点 TODO）"。

**影响**：长期运行磁盘涨满。每个 session ~12-100MB 视频，日均 10 个 session = 日增 1GB。

**建议**：
- v1 最小：worker 完成后 N 天（如 7 天）异步删视频文件（保留 session 行）。
- 或：视频文件改按 session 存到独立目录，配合 cron 清理。
- coach 页对已删视频的 404 已处理（routes.py:301）。

---

### Low

#### L-1. `_migrate_add_column_if_missing` 用 `assert` 做 SQL 注入防御

**文件**：`webapp/backend/db.py:83-87`

**问题**：`assert table.isidentifier()` / `assert col_type.upper() in {...}` 可被 `python -O` 关闭，关掉后 f-string 拼接无防护。

**影响**：**实际注入风险为零**——table/col/col_type 全是 `init_schema` 里写死的字面量（`"sessions"` / `"cm_per_360"` / `"REAL"`），非用户输入。assert 纯属 defense-in-depth。

**建议**：风格层面把 `assert` 换成 `if not ...: raise ValueError(...)` 更稳。优先级极低——无真实注入路径。

---

#### L-2. app lifespan 不 close_conn

**文件**：`webapp/backend/app.py:13-17`

**问题**：`lifespan` startup 调 `init_schema()`，但 shutdown 不调 `close_conn()`。进程退出靠 GC 关连接。

**影响**：WAL 模式下已 commit 数据不会丢，最差是最后一批未 commit 的写丢。可忽略。

**建议**：lifespan 的 yield 后加 `await db.close_conn()`。一行改动。

---

#### L-3. `_reconstruct_diagnosis` 对数据结构变化脆弱

**文件**：`webapp/backend/routes.py:109-150`

**问题**：手动反序列化 `CoachDiagnosis`，逐字段 `.get(..., default)`。如果 `CoachDiagnosis` / `DiagnosisIssue` / `ProfileMatch` / `Prescription` 加新 required 字段，反序列化会 `TypeError`（无 default 的位置参数）。当前测试覆盖的 case 过，但新字段加完不会自动报错。

**影响**：coach 包演进时 chat 静默崩。

**建议**：低优先。可考虑给 dataclass 加 `from_dict` 类方法（需动 kovaak_tracker/coach）。当前手动反序列化 + 测试覆盖可接受。

---

#### L-4. budget 估算对长 chat 历史严重低估 input tokens

**文件**：`webapp/backend/worker.py:109-119`、`webapp/backend/routes.py:193`

**问题**：chat 预检查用 `_estimate_llm_cost_cny("", min_output_tokens=500)`，input_tokens 走默认 2000。但 chat_with_coach 会把**完整 chat 历史 + diagnosis + KB** 喂给 LLM，长对话后 input 轻松破 10k tokens。预算估算偏低 5-10×。

**影响**：budget 失准（偏宽松）。与 M-3 叠加。

**建议**：预检查时从 `load_chat_history` 的返回粗估 input tokens（总字符数 // 2），或接 DeepSeek usage 字段后用真实值回填。v1 先加 TODO。

---

#### L-5. CORS allow_methods=["*"] + allow_credentials=True

**文件**：`webapp/backend/app.py:25-31`

**问题**：CORS 配置允许所有 methods + 所有 headers + credentials。origin 已收敛到 env 变量列表（好），但 methods/headers 仍 wildcard。

**影响**：dev 无所谓；生产应收敛为 `["GET", "POST", "OPTIONS"]` + 白名单 headers。

**建议**：上线前收紧。低优先。

---

#### L-6. `video.size is None` 时跳过预检查

**文件**：`webapp/backend/routes.py:57`

**问题**：`if video.size is not None and video.size > MAX_VIDEO_BYTES`——如果 `video.size` 是 None（如 chunked transfer encoding 上传），预检查被跳过，直接进 `await video.read()`，回到 OOM 风险。

**影响**：multipart form 上传有已知 size（Starlette 设 `.size`），实际触发概率低。chunked 编码可被恶意客户端构造。

**建议**：`video.size is None` 也拒绝（或要求客户端声明 Content-Length）。低优先。

---

#### L-7. `_load_backend_or_none` 每次 chat 重新加载 provider 配置

**文件**：`webapp/backend/routes.py:153-160`

**问题**：每次 chat 调用都重新 `load_backend(LLM_PROVIDER)`，重读 providers.json + 构造 backend 实例。

**影响**：单次 chat LLM 耗时 10-30s，加载配置 <10ms，占比可忽略。但 backend 实例如有连接池，每次新建可能泄漏。

**建议**：模块级缓存 backend（首次加载后复用）。低优先。

---

## Top 3（上线前应处理）

1. **H-3 IDOR**：所有 `/sessions/{id}/...` 加 ownership 校验（`X-User-Id` 对比 `session.user_id`）。v1 开放注册后第一个真实用户就受影响，不该等 Clerk。最小改动是在每个端点开头加一行 `if x_user_id != s["user_id"]: raise 403`。

2. **H-1 budget 记账一致性**：chat 路径 `reply is None` 但 LLM 实际调过时不记 cost。改为"backend 非 None 即记 cost"（对齐 worker 路径）。同时把 `check_and_record` 改名为 `check_budget`（消除"已记录"的误导）。

3. **H-2 + H-4 TOCTOU**：chat budget 预检查 + analyze 单 job 检查两处都有 check-then-act 窗口。短期加 per-user `asyncio.Lock` 串行化（chat 路径）+ `BEGIN IMMEDIATE` 事务包 check+enqueue（analyze 路径，复用 `claim_next` 模式）。

---

## 07-07 修复验证

| # | 修复项 | 验证结果 |
|---|---|---|
| 1 | 视频 `.read()` OOM → `video.size` 预检 | **PASS**。`routes.py:57-60` 先查 `video.size > MAX_VIDEO_BYTES` / `csv.size > MAX_CSV_BYTES`，拒绝后才 `await video.read()`。回归测试 `test_analyze_rejects_oversized_video` / `test_analyze_rejects_oversized_csv` 覆盖 413。残留：`video.size is None`（chunked 编码）跳过预检查（见 L-6），实际风险低。 |
| 2 | chat `asyncio.to_thread` 解 event loop 阻塞 | **PASS**。`routes.py:226-228` 用 `await asyncio.to_thread(chat_with_coach, ...)` 把同步 LLM 调用丢线程池。方向正确，其他请求/worker 并发不被 hold。 |
| 3 | budget 跨日 → 查 `updated_at` 而非 `created_at` | **PASS**。`llm_budget.py:22-28` 按 `date(updated_at)=?` 过滤。回归测试 `test_budget_counts_cross_day_session_by_updated_at`（test_llm_budget.py:45-69）覆盖跨日场景。docstring 清晰解释了为什么不能用 created_at（chat 路径的 `add_llm_cost` 刷 updated_at 但不动 created_at）。 |
| 4 | Windows tmp 目录 | **PASS**。`config.py:24-26` 用 `tempfile.gettempdir()` 替代硬编码 `/tmp`，跨平台兼容。`VIDEO_TMP_DIR` 支持 env 覆盖。 |
| 5 | db SQL assert 防御 | **PASS（附注）**。`db.py:83-87` 三道 assert：`table.isidentifier()` / `col.isidentifier()` / `col_type.upper() in {REAL,INTEGER,TEXT,BLOB,NUMERIC}`。方向正确。**附注**：`assert` 可被 `python -O` 关闭（见 L-1），但实际注入风险为零（值全为 caller 写死字面量），可接受。 |

**结论**：五项修复全部落地，方向正确，有回归测试覆盖。无引入新问题。L-1 和 L-6 是修复的边缘残留，优先级低。

---

## 风格/观察（不计入分级）

- `db.py` 用模块级 `_conn` 全局单例。web 进程与 worker 进程各持一份（进程隔离），同一进程内所有请求共享。aiosqlite 内部用线程串行化，正确性 OK，高并发下是瓶颈——v1 单用户 dev 无所谓。
- `queue.py` 的 `claim_next` 用 `BEGIN IMMEDIATE` 序列化，单 worker 安全；多 worker 会串行等锁（性能差但正确）。部署换 Postgres 时改 `FOR UPDATE SKIP LOCKED` 的注释清晰。
- `worker.py` 的 `_estimate_llm_cost_cny` 被 `routes.py` 反向 import（`from .worker import _estimate_llm_cost_cny`），函数级 import 避免循环依赖。合理但可考虑挪到独立 `costing.py` 让依赖方向更干净。
- `schemas.py` 用 Pydantic BaseModel 做响应模型，`SessionStatus.result: Optional[dict]` 无 schema 约束（裸 dict）——前端 TypeScript 类型靠 `frontend/lib/types.ts` 手写同步，有 drift 风险。v1 可接受。
- 测试覆盖：47 passed 1 skipped。覆盖了 happy path + 主要错误分支（404/409/400/413/429）+ 跨日 budget + 降级路径。缺 IDOR 测试（无测试验证"用户 A 不能读用户 B 的 session"）和并发竞争测试。

---

## 测试建议（补强）

1. **IDOR 测试**：`POST /api/sessions/{sid}/chat` with `X-User-Id: other_user` → 应 403（当前会 200，暴露 bug）。
2. **并发 chat 竞争**：两个协程同时 POST chat，验证 budget 不会被双花（当前会）。
3. **stuck job 回收**：手工置一个 session 为 `running` + `updated_at` 为 1 小时前，调 `claim_next`，验证是否被回收（当前不会）。
4. **chat 失败记账**：mock `chat_with_coach` 返回 None，验证 `add_llm_cost` 是否被调用（当前不调，暴露 H-1）。
