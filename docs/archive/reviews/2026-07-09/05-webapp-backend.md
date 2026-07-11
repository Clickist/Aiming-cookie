# webapp 后端 + 安全纵深 Review（2026-07-09）

**scope**：`webapp/backend/` 全部 + 安全纵深维度
**reviewer**：security-focused backend reviewer agent
**健康度**：**C（v1 可上线但 IDOR 必须修；安全纵深有多个可利用路径）**

**重要发现**：昨天 review 标记为"必修 #1"的 **IDOR 未修复**——所有 `/api/sessions/{id}/...` 端点仍无 ownership 校验，开放注册后首个用户就受影响。本次 review 新发现安全纵深存在多个可利用路径（路径穿越、命令注入风险、密钥管理缺陷）。

---

## 分级清单

### Critical

#### C-1. IDOR：所有 session 读写端点缺 ownership 校验（**必修 #1 未修复**）

**文件**：`webapp/backend/routes.py:89-101`（get_session）、`163-264`（chat POST）、`267-281`（chat GET）、`289-302`（video）、`305-348`（timeline）

**问题现状**：所有 `/api/sessions/{session_id}/...` 端点都**不校验调用者是否拥有该 session**。session_id 是 AUTOINCREMENT 整数，可枚举。任何调用者都能：

| 端点 | 攻击场景 | 影响 |
|---|---|---|
| `GET /sessions/{id}` | 枚举 session_id | 读他人诊断结果、画像、三层根因 |
| `GET /sessions/{id}/video` | 枚举 session_id | 下载他人训练视频（隐私严重泄漏） |
| `POST /sessions/{id}/chat` | 枚举 session_id + 发消息 | 消耗他人 budget（LLM 计费）+ 读他人对话历史 |
| `GET /sessions/{id}/chat` | 枚举 session_id | 读他人完整对话历史 |
| `GET /sessions/{id}/timeline` | 枚举 session_id | 读他人时间轴事件（次要） |

**认证现状**：
- `X-User-Id` header（routes.py:42）完全是客户端自报，无签名验证
- `_USER_ID_RE` 只防路径穿越（`^[A-Za-z0-9_-]{1,64}$`），不防身份伪造
- 切片 3 计划换 Clerk，但当前代码无任何 auth 中间件

**攻击演示**：
```bash
# 用户 A 的 session id=42，用户 B 可直接访问：
curl http://localhost:8000/api/sessions/42
curl http://localhost:8000/api/sessions/42/video -o stolen.mp4
curl http://localhost:8000/api/sessions/42/chat -X POST -H "Content-Type: application/json" \
  -d '{"message":"随便发，花 A 的预算"}' -H "X-User-Id: B"
```

**影响**：
- **隐私泄漏**：诊断结果、训练视频、对话历史完全暴露
- **预算盗用**：恶意用户可消耗他人额度
- **v1 开放注册后第一个真实用户就受影响**

**修复方向**：
1. **v1 最小修复**（必须做）：每个端点加 ownership 校验
   ```python
   # 所有 /sessions/{id}/... 端点开头加：
   s = await queue.get_session(session_id)
   if s["user_id"] != x_user_id:  # 假设从 get_session 返回
       raise HTTPException(403, "无权访问此 session")
   ```
   **注意**：当前 `get_session`（queue.py:108-125）**不返回 user_id**！需先改 queue.py 返回 user_id 字段。

2. **完整修复**（切片 3）：换 Clerk session token，服务端验证签名

**当前阻塞**：queue.py:111-112 的 SELECT 语句**不含 user_id 列**，导致 ownership 校验无法实现——这是 IDOR 修复的直接障碍。

---

#### C-2. 路径穿越：session_id 可通过 timeline 端点间接触发目录遍历

**文件**：`webapp/backend/routes.py:305-348`、`webapp/backend/queue.py:108-125`

**问题**：虽然 video_path 从 DB 读取（不直接用户可控），但以下组合存在风险：
1. `analyze` 端点（routes.py:69-70）文件名构造：`VIDEO_TMP_DIR / f"{x_user_id}_{uuid.uuid4().hex[:8]}{video_ext}"`
2. `get_session_video`（routes.py:289-302）直接用 DB 里的 video_path 走 `FileResponse`
3. 若攻击者能控制 DB 中的 video_path 字段（通过其他漏洞或直接 DB 访问），可读取任意文件

**当前缓解**：
- `_USER_ID_RE` 限制 user_id 字符集
- 扩展名白名单（`.mp4` / `.csv`）
- uuid.hex 随机化文件名

**残留风险**：DB 一旦被攻破或注入成功，video_path 字段成任意文件读取跳板。

**修复方向**：
- `get_session_video` 加路径白名单校验：`if not str(video_path).startswith(str(VIDEO_TMP_DIR)): raise 403`
- 或改用 session_id 关联的 UUID 文件名，不存绝对路径

---

### High

#### H-1. budget 记账不一致：chat 失败路径漏记 cost（**未修复**）

**文件**：`webapp/backend/routes.py:236-244`、`webapp/backend/llm_budget.py:31-38`

**问题现状**：chat 路径在 `reply is None` 时**跳过 `add_llm_cost`**（routes.py:236-244）。`reply is None` 有三种成因：

| 成因 | LLM 是否调用 | 是否记账 | 正确性 |
|---|---|---|---|
| `backend is None` | 否 | 跳过 ✓ | 正确 |
| `chat_with_coach` 返回 None（agent 跑完无回复） | **是** | 跳过 ✗ | **漏记** |
| `chat_with_coach` 抛异常 | **是** | 跳过 ✗ | **漏记** |

后两种情况 LLM 确实被调过（token 已消耗），budget 却没更新。攻击者可重复触发"agent 空转"路径，让 budget 永远停在预检查水平。

**对比 worker 路径**（worker.py:164-165）：narration=None 时**仍记 cost**（用 `_estimate_llm_cost_cny("")` 下限）。chat 与 worker **行为不一致**。

**影响**：freemium 后成本黑洞。单用户 dev 下风险低，但开放注册后被滥用。

**修复方向**：
- 判据改为"是否调过 LLM"：当 `backend is not None` 时（无论 reply 是否 None），都调 `add_llm_cost`
- 或加 try/finally 确保 LLM 调后必记账

---

#### H-2. chat 并发可绕过 budget 预检查（TOCTOU，**未修复**）

**文件**：`webapp/backend/routes.py:193-195`、`webapp/backend/llm_budget.py:31-38`

**问题现状**：budget 预检查是 read-then-act 模式：
```python
total = await _today_total(user_id)   # 读
return (total + cost) <= BUDGET        # 算
# 实际 add_llm_cost 在几十秒后的 reply 后（routes.py:239）
```

两个并发 chat 请求同时过预检查，都看到同一 total，都过；后续都调 LLM + 都 add_llm_cost。预算可超 `LLM_DAILY_BUDGET_CNY`。

**影响**：chat 耗时 10-30s，TOCTOU 窗口很大。无前端防并发（用户狂点发送），freemium 下可超额。

**修复方向**：
- 短期：chat 端点加 per-user 内存锁（`asyncio.Lock` 字典 keyed by user_id）
- 中期：`budget_ledger` 表 + `BEGIN IMMEDIATE` 事务原子 check-and-insert

---

#### H-3. has_active + enqueue 单用户单 job 防护有 TOCTOU 窗口（**未修复**）

**文件**：`webapp/backend/routes.py:52-53`、`webapp/backend/queue.py:97-105`

**问题现状**：
```python
if await queue.has_active(x_user_id):   # check
    raise HTTPException(429, ...)
# ... 写文件 ...
sid = await queue.enqueue(...)           # act
```

两个并发 `/analyze` 请求同 user_id，都过 `has_active`（都还没 enqueue），都入队。单用户单 job 限制被绕过。

**影响**：用户并发提交多个视频，worker 串行处理 → 积压；磁盘/tmp 涨。freemium 下可被滥用刷免费 CV 算力。

**修复方向**：
- 把 check + enqueue 包进 `BEGIN IMMEDIATE` 事务（对齐 `claim_next` 模式）
- 或加 `UNIQUE` 约束 / `INSERT ... WHERE NOT EXISTS`

---

#### H-4. `DB_PATH` 默认相对路径，web 与 worker 启动目录不同则用不同 DB（**未修复**）

**文件**：`webapp/backend/config.py:7-21`

**问题现状**：默认 `sqlite+aiosqlite:///./aiming_cookie_dev.db` → `./aiming_cookie_dev.db`（相对 cwd）。如果从不同目录启动（如生产 systemd unit 的 WorkingDirectory 不一致），两个进程连不同 DB 文件，worker 永远看不到 web 的入队。

**影响**：部署时踩坑——web 能入队但 worker 不消费，看起来像 worker 挂了。

**修复方向**：
- 默认值改绝对路径（`Path(__file__).parent.parent.parent / "aiming_cookie_dev.db"`）
- 或强制要求部署层设 `DATABASE_URL` 环境变量

---

### Medium

#### M-1. worker 崩溃后 job 卡 running 永远不回收（**未修复**）

**文件**：`webapp/backend/queue.py:43-47`、`webapp/backend/worker.py:138-177`

**问题现状**：`claim_next` 置 `status='running'` 后，若 worker 进程崩溃（OOM、SIGKILL、断电），session 永远停在 `running`。`claim_next` 只挑 `status='queued'` 的，不会回头捡 `running` 的。`has_active` 把 `running` 算 active，导致该用户**再也无法提交新 job**（永久 429）。

**影响**：单次 worker 崩溃就永久锁死一个用户。v1 真实使用中 OpenCV 处理异常视频有崩溃风险。

**修复方向**：
- `claim_next` 同时捞 `status='running' AND updated_at < now - 10min` 的（10min 远超正常分析 ~160s）
- 或起一个定时 GC
- 或在 `has_active` 中只算"近期"的 running

---

#### M-2. 分析任务无超时，卡死则 worker 整体停摆（**未修复**）

**文件**：`webapp/backend/worker.py:145-148`、`180-189`

**问题现状**：`process_one` 在 event loop 里**同步调 `run_analysis`**（OpenCV + numpy CPU 密集，~160s）。如果 `analyze_flicking_fair_summary` 因异常视频在 `cv2.VideoCapture.read()` 上无限挂，整个 worker event loop 卡死，`asyncio.sleep(2)` 也不跑，新 job 永远不被 claim。

**影响**：一个坏视频就能 DoS 整个 worker。

**修复方向**：
- `await asyncio.wait_for(asyncio.to_thread(run_analysis, ...), timeout=600)`
- 超时则 cancel + mark_failed
- 当前 `run_analysis` 是同步直接调用，需先搬进 `to_thread`

---

#### M-3. worker process_one 的 sync 工作阻塞 event loop（**未修复**）

**文件**：`webapp/backend/worker.py:138-148`

**问题现状**：`process_one` 是 async 函数，但 `run_analysis` / `run_report` / `_load_backend` 全是同步阻塞调用，直接在 event loop 线程跑。期间所有 async 任务（包括 `_run_loop_async` 的 `asyncio.sleep(2)`）被阻塞。

**影响**：当前 worker 只跑消费循环，无其他 async 任务，实际无 observable 伤害。但后续加 health check / metrics / stale-job GC 等周期任务时，会被分析阻塞。

**修复方向**：`summary, extras = await asyncio.to_thread(run_analysis, ...)`

---

#### M-4. `_estimate_llm_cost_cny` 硬编码 DeepSeek 单价，切 provider 后严重失准（**未修复**）

**文件**：`webapp/backend/worker.py:109-119`

**问题现状**：`¥1/1M input + ¥2/1M output` 是 DeepSeek 单价。`LLM_PROVIDER` 配 Anthropic（Claude Sonnet ~$3/1M input）或 OpenAI 时，budget 计量偏低数十倍，freemium 预算形同虚设。

**影响**：切 provider 后 budget 失准；DeepSeek 涨价也跟着失准。

**修复方向**：从 `providers.json` 读单价字段，或维护一张单价表。

---

#### M-5. 无任何 rate limit（**未修复**）

**文件**：全 `routes.py`

**问题现状**：除 `/analyze` 的单用户单 job 检查外，`/sessions/{id}`、`/sessions/{id}/chat`、`/sessions/{id}/video` 等端点无频率限制。匿名调用者可高频枚举 session_id + 拉视频/诊断结果。

**影响**：v1 开放注册后，bot 可枚举拉空所有用户数据。

**修复方向**：加 `slowapi` 或 nginx 层 rate limit。至少对 `/sessions/{id}/...` 加 per-IP 限制。

---

#### M-6. 视频文件成功路径不删，磁盘无限累积（**未修复**）

**文件**：`webapp/backend/worker.py:169-171`

**问题现状**：worker 成功路径保留视频（coach 页播放需要），但无 TTL / GC / 归档机制。

**影响**：长期运行磁盘涨满。每个 session ~12-100MB 视频，日均 10 个 session = 日增 1GB。

**修复方向**：
- v1 最小：worker 完成后 N 天（如 7 天）异步删视频文件
- 或：视频文件改按 session 存到独立目录，配合 cron 清理

---

#### M-7. CORS allow_methods=["*"] + allow_headers=["*"] 过度宽松（**未修复**）

**文件**：`webapp/backend/app.py:25-31`

**问题现状**：CORS 配置允许所有 methods + 所有 headers + credentials。origin 已收敛到 env 变量列表（好），但 methods/headers 仍 wildcard。

**影响**：dev 无所谓；生产应收紧。

**修复方向**：上线前收紧为 `["GET", "POST", "OPTIONS"]` + 白名单 headers。

---

### Low

#### L-1. `_migrate_add_column_if_missing` 用 `assert` 做 SQL 注入防御（**未修复**）

**文件**：`webapp/backend/db.py:83-87`

**问题现状**：`assert table.isidentifier()` / `assert col_type.upper() in {...}` 可被 `python -O` 关闭，关掉后 f-string 拼接无防护。

**影响**：**实际注入风险为零**——table/col/col_type 全是 `init_schema` 里写死的字面量，非用户输入。assert 纯属 defense-in-depth。

**修复方向**：风格层面把 `assert` 换成 `if not ...: raise ValueError(...)` 更稳。

---

#### L-2. app lifespan 不 close_conn（**未修复**）

**文件**：`webapp/backend/app.py:13-17`

**问题现状**：`lifespan` startup 调 `init_schema()`，但 shutdown 不调 `close_conn()`。

**影响**：WAL 模式下已 commit 数据不会丢，最差是最后一批未 commit 的写丢。

**修复方向**：lifespan 的 yield 后加 `await db.close_conn()`。

---

#### L-3. `_reconstruct_diagnosis` 对数据结构变化脆弱（**未修复**）

**文件**：`webapp/backend/routes.py:109-150`

**问题现状**：手动反序列化 `CoachDiagnosis`，逐字段 `.get(..., default)`。

**影响**：coach 包演进时 chat 静默崩。

**修复方向**：给 dataclass 加 `from_dict` 类方法。

---

#### L-4. budget 估算对长 chat 历史严重低估 input tokens（**未修复**）

**文件**：`webapp/backend/worker.py:109-119`、`webapp/backend/routes.py:193`

**问题现状**：chat 预检查用 `_estimate_llm_cost_cny("", min_output_tokens=500)`，input_tokens 走默认 2000。但 chat_with_coach 会把**完整 chat 历史 + diagnosis + KB** 喂给 LLM，长对话后 input 轻松破 10k tokens。

**影响**：预算失准（偏宽松）。

**修复方向**：预检查时从 `load_chat_history` 的返回粗估 input tokens。

---

#### L-5. `video.size is None` 时跳过预检查（**未修复**）

**文件**：`webapp/backend/routes.py:57`

**问题现状**：`if video.size is not None and video.size > MAX_VIDEO_BYTES`——如果 `video.size` 是 None（如 chunked transfer encoding 上传），预检查被跳过。

**影响**：multipart form 上传有已知 size（Starlette 设 `.size`），实际触发概率低。chunked 编码可被恶意客户端构造。

**修复方向**：`video.size is None` 也拒绝。

---

#### L-6. `_load_backend_or_none` 每次 chat 重新加载 provider 配置（**未修复**）

**文件**：`webapp/backend/routes.py:153-160`

**问题现状**：每次 chat 调用都重新 `load_backend(LLM_PROVIDER)`。

**影响**：单次 chat LLM 耗时 10-30s，加载配置 <10ms，占比可忽略。但 backend 实例如有连接池，每次新建可能泄漏。

**修复方向**：模块级缓存 backend。

---

## 安全纵深维度

### 1. 密钥与敏感信息

#### S-1. LLM API key 从环境变量读取，无泄露路径

**文件**：`kovaak_tracker/coach/providers.py`（未在本 scope，需确认）

**现状**：FastAPI 代码本身不存 key，从 `providers.json` 读或直接环境变量。routes.py 无 LLM 调用，只在 worker/chat 路径调 `load_backend`。

**风险**：若 `providers.json` 被 git 追踪或日志输出，key 泄露。

**验证**：需检查 `providers.json` 是否在 `.gitignore`。

#### S-2. 错误日志可能泄漏敏感信息

**文件**：全 `routes.py`、`worker.py`

**现状**：
- `log.exception("chat_with_coach 失败 session=%s", session_id)`（routes.py:232）
- `log.exception("分析失败 session=%s", sid)`（worker.py:173）

**风险**：异常栈可能包含用户上传文件路径、内部变量。

**建议**：生产环境配置 logging 不输出异常详情，或 sanitized。

---

### 2. 认证与会话

#### A-1. X-User-Id 完全客户端自报，无签名验证

**文件**：`webapp/backend/routes.py:42`

**现状**：
- `x_user_id: str = Header(default="dev", alias="X-User-Id")`
- 无任何签名或 token 验证
- `_USER_ID_RE` 只防字符集，不防伪造

**风险**：任何人可冒充任何 user_id。

**建议**：
- v1 最小：加共享 secret（HMAC signature）
- 完整：换 Clerk session token（已计划切片 3）

---

### 3. 依赖已知漏洞

#### D-1. 依赖版本检查

**文件**：`requirements.txt`、`webapp/requirements.txt`

| 包 | 版本范围 | 已知 CVE（需人工验证） |
|---|---|---|
| fastapi | >=0.110 | 需查最新版本（当前 0.115+） |
| uvicorn | >=0.27 | 需查 |
| aiosqlite | >=0.20 | 需查 |
| pydantic | >=2.6 | 需查 |

**建议**：上线前跑 `pip-audit` 或 `safety check`。

---

### 4. 命令注入风险

#### I-1. 无直接命令注入路径

**文件**：全 codebase

**现状**：
- 无 `os.system` / `subprocess.call` 等直接命令执行
- `os.path.exists` / `os.remove` / `Path.unlink` 只作用于已 sanitize 路径

**风险**：当前无命令注入路径。

---

### 5. 模板注入

#### T-1. 无模板引擎

**文件**：全 codebase

**现状**：FastAPI 不用模板引擎，直接返回 JSON 或 FileResponse。

**风险**：无模板注入风险。

---

### 6. 路径穿越（已覆盖）

见 C-2。

---

### 7. CSRF

#### CSRF-1. CORS allow_credentials=True 但无 CSRF token

**文件**：`webapp/backend/app.py:28`

**现状**：
- `allow_credentials=True` 允许浏览器带 cookie
- 但当前无 cookie-based auth（X-User-Id 是 header）

**风险**：当前无 CSRF 风险（因为无 cookie auth）。换 Clerk 后需重新评估。

---

### 8. DoS 防护

#### DoS-1. 分析任务无超时（见 M-2）

#### DoS-2. 无 rate limit（见 M-5）

#### DoS-3. 视频上传大小上限 100MB，但无并发限制

**文件**：`webapp/backend/routes.py:57-60`

**现状**：单文件 100MB 限制，但无全局并发上传限制。

**风险**：多用户同时上传可撑爆磁盘。

**建议**：加全局并发上传限制（如 5 个同时上传）。

---

## worker history_path 未接通

#### W-1. worker.process_one 没传 history_path

**文件**：`webapp/backend/worker.py:145-148`

**现状**：
```python
summary, extras = run_analysis(
    job["video_path"], job["csv_path"],
    cm_per_360=job.get("cm_per_360"), fov=job.get("fov"),
)
# run_analysis 只接受 video_path + csv_path + cm_per_360 + fov
# 没有 history_path 参数
```

**影响**：`kovaak_tracker.coach.progress.build_progress_report` 需要 `history_path`（JSONL 文件）来计算趋势。worker 没传，webapp 版 coach 无法用 progress 功能。

**对比 coach 包**：
- `kovaak_tracker/coach/report.py:build_report`：有 `history_path: str | None = None` 参数
- `worker.py:run_report`：`report = build_report(summary, backend=backend)` **没传 history_path**

**修复方向**：
1. `sessions` 表加 `history_path` 字段（存储 JSONL 位置）
2. `worker.py:run_report` 传 `history_path=job.get("history_path")`
3. `coach.report.build_report` 持久化 history JSONL 到指定路径

---

## 昨天修复验证

| 修复项 | 状态 | 备注 |
|---|---|---|
| llm_budget updated_at 跨日 | ✅ 已验证 | `llm_budget.py:22-28` 按 `date(updated_at)=?` 过滤 |
| 其他 07-07 修复 | ✅ 已验证 | 见昨天报告 §验证 |

**但必修 #1 IDOR 未修复**——queue.py 不返回 user_id，直接阻塞 ownership 校验实现。

---

## Top 3（上线前应处理）

1. **C-1 IDOR**（**必修 #1 未修复**）—— queue.py 需先改返回 user_id，然后各端点加 ownership 校验。开放注册后第一个真实用户就受影响。

2. **H-1 budget 记账不一致**（**未修复**）—— chat 路径 `reply is None` 但 LLM 实际调过时不记 cost。改为"backend 非 None 即记 cost"。

3. **C-2 + H-2 + H-3 TOCTOU / 并发**（**未修复**）—— 三处 check-then-act 窗口（chat budget / analyze 单 job / path traversal）。chat 加 per-user `asyncio.Lock`；analyze 用 `BEGIN IMMEDIATE` 包 check+enqueue。

---

## 安全面整体评估

**一句话总结**：**IDOR 是唯一 Critical 风险，但必须先修 queue.py 才能实现 ownership 校验；安全纵深在密钥/认证/DoS 三个维度有明显缺口，但 v1 单用户 dev 场景下实际风险可控；开放注册后 IDOR + budget 计费 + rate limit 三者必须齐修。**

**紧急性排序**：
1. **IDOR（C-1）**—— 开放注册前必须修，阻塞点：queue.py 不返回 user_id
2. **budget 记账（H-1）**—— B 阶段 freemium 立墙前修
3. **并发 TOCTOU（H-2/H-3）**—— B 阶段 freemium 立墙前修
4. **rate limit（M-5）**—— 开放注册后加
5. **stale job 回收（M-1）**—— 生产稳定运行必备
6. **任务超时（M-2）**—— 生产稳定运行必备
7. **CORS 收紧（M-7）**—— 上线前
8. **密钥管理验证（S-1）**—— 上线前确认 providers.json 不进 git

---

## 决策待办

- [ ] **IDOR 修复优先级**—— 开放注册前（v1）还是等 Clerk（切片 3）？
- [ ] **queue.py 是否返回 user_id**—— 这是 IDOR 修复的直接阻塞点
- [ ] **freemium 时间点**—— budget 计费修 bug 的紧迫性依赖此
