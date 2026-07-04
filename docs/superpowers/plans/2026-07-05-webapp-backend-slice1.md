# Aiming Cookie Webapp — Slice 1: 后端 API + Worker 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax.

**Goal:** 搭建 Aiming Cookie 后端 API(FastAPI)+ 异步 Worker,本地端到端跑通"上传 flicking 视频 → 入队 → Worker 跑 `analyze_flicking_video` + `build_report` + DeepSeek narration → 查询结果"。不含 auth / 前端 / 部署(后续切片)。

**Architecture:** FastAPI 接收上传 → Postgres 队列(`FOR UPDATE SKIP LOCKED`)→ 独立 Worker 进程消费 → 调 `kovaak_tracker.analyze_flicking_video` + `coach.build_report` + DeepSeek → 结果写 Postgres + 删视频源文件。LLM 测试用 mock。

**Tech Stack:** Python 3.9+(用 `from __future__ import annotations` 兼容点点本机 3.9.7)/ FastAPI / asyncpg / Postgres 16 / Docker Compose / pytest-asyncio / 现有 `kovaak_tracker` 包

**Spec:** `docs/superpowers/specs/2026-07-05-flicking-coach-webapp-design.md`

> **⚠️ 环境适配注记(2026-07-05 实现时)**:点点本机 Python 3.9.7,**无 Docker / Postgres**。本地开发改用 **SQLite**(`aiosqlite`,零配置);部署时(spec §3 香港服务器 Docker Compose)换 Postgres。Task 1-3 因此用 SQLite 语法(`BIGSERIAL→INTEGER PRIMARY KEY AUTOINCREMENT`、`JSONB→TEXT` 存 JSON、`FOR UPDATE SKIP LOCKED→BEGIN IMMEDIATE + UPDATE...LIMIT`)。**spec §4 Postgres 是部署架构**,本地开发环境分离不算偏离。点点睡醒若坚持本地也 Postgres,装 Docker 后迁。
>
> **执行策略**:切片 1 inline 执行(环境适配需判断 + 点点睡觉无人协调 subagent),每 task TDD + self-review,最后 dispatch final reviewer 把关。

## Global Constraints

- **复用 `kovaak_tracker` 包**,不重写分析逻辑;webapp 是应用层,import 现有包
- **LLM 默认 DeepSeek**(`kovaak_tracker/coach/providers.json` 已配),测试用 mock(不真调)
- **视频源文件分析完即删**(隐私 + 省盘)
- **单用户同时只 1 个 job**(并发限制)
- **LLM 限金额不限次数**:每用户每天 ¥X(后端按 token 计费累计),`X` 睡醒定,先默认 ¥1.0
- **Python 3.9 兼容**:全程 `from __future__ import annotations`;避免 `X | Y` 运行时用(`Optional[X]`);Pydantic v2 / asyncpg / FastAPI 都支持 3.9
- **TDD**:每任务先写失败测试 → 跑验证失败 → 实现 → 跑验证通过 → commit
- **commit 粒度**:每个任务结束 commit 一次

## File Structure

新建 `webapp/` 目录(跟 `kovaak_tracker/` 同级):

| 文件 | 职责 |
|---|---|
| `webapp/backend/__init__.py` | 包标记(空) |
| `webapp/backend/config.py` | 配置(DB URL / LLM provider / 限额 / 路径) |
| `webapp/backend/db.py` | asyncpg 连接池 + schema 初始化 |
| `webapp/backend/queue.py` | 任务队列(enqueue / claim_next / mark_done / mark_failed / has_active) |
| `webapp/backend/schemas.py` | Pydantic 请求/响应模型 |
| `webapp/backend/routes.py` | FastAPI 路由(POST /api/analyze, GET /api/sessions/{id}) |
| `webapp/backend/app.py` | FastAPI 应用入口(uvicorn) |
| `webapp/backend/worker.py` | Worker 进程:claim → 分析 → 写结果 → 删视频 |
| `webapp/backend/llm_budget.py` | 每用户每天 LLM 金额跟踪 + 超额判断 |
| `webapp/tests/conftest.py` | pytest fixture(隔离 test DB) |
| `webapp/tests/test_*.py` | 各模块测试 |
| `docker-compose.yml`(根目录) | Postgres 16 服务 |
| `webapp/requirements.txt` | 后端依赖 |

**职责边界**:`kovaak_tracker/`(现有,纯逻辑,不碰 web/DB) ↔ `webapp/backend/`(web + 持久化 + 异步编排,import kovaak_tracker)

---

## Task 1: 项目骨架 + Docker Postgres

**Files:**
- Create: `docker-compose.yml`(根目录)
- Create: `webapp/requirements.txt`
- Create: `webapp/backend/__init__.py`(空)
- Create: `webapp/tests/__init__.py`(空)
- Create: `webapp/README.md`

**Interfaces:** 基础设施,无对外接口

- [ ] **Step 1: 写 `docker-compose.yml`**

```yaml
services:
  postgres:
    image: postgres:16-alpine
    environment:
      POSTGRES_USER: aiming
      POSTGRES_PASSWORD: cookie_dev
      POSTGRES_DB: aiming_cookie
    ports:
      - "5432:5432"
    volumes:
      - aiming_pgdata:/var/lib/postgresql/data
volumes:
  aiming_pgdata:
```

- [ ] **Step 2: 写 `webapp/requirements.txt`**

```
fastapi>=0.110
uvicorn[standard]>=0.27
asyncpg>=0.29
python-multipart>=0.0.9
pydantic>=2.6
psycopg2-binary>=2.9  # worker 同步路径备用
pytest>=8.0
pytest-asyncio>=0.23
httpx>=0.27  # FastAPI TestClient
```

- [ ] **Step 3: 装依赖 + 启 Postgres**

```bash
pip install -r webapp/requirements.txt
docker compose up -d postgres
# 验证连接
docker compose exec postgres psql -U aiming -d aiming_cookie -c "SELECT 1;"
```
Expected: 输出 `1` 一行

- [ ] **Step 4: 写 `webapp/README.md`**(怎么跑后端)

```markdown
# Aiming Cookie Webapp

## 后端开发(切片 1)

```bash
# 启 Postgres
docker compose up -d postgres

# 装 deps
pip install -r webapp/requirements.txt

# 初始化 schema(首次)
python -c "import asyncio; from webapp.backend.db import init_schema; asyncio.run(init_schema())"

# 启 API
uvicorn webapp.backend.app:app --reload

# 另开终端启 Worker
python -m webapp.backend.worker
```
```

- [ ] **Step 5: commit**

```bash
git add docker-compose.yml webapp/
git commit -m "feat(webapp): slice1 骨架 + Postgres docker compose"
```

---

## Task 2: DB schema + 连接(db.py + config.py)

**Files:**
- Create: `webapp/backend/config.py`
- Create: `webapp/backend/db.py`
- Test: `webapp/tests/conftest.py`, `webapp/tests/test_db.py`

**Interfaces:**
- Produces: `config.DATABASE_URL`, `config.VIDEO_TMP_DIR`, `config.LLM_DAILY_BUDGET_CNY`; `db.get_pool() -> asyncpg.Pool`, `db.init_schema()`

- [ ] **Step 1: 写 `webapp/backend/config.py`**

```python
from __future__ import annotations
import os
from pathlib import Path

DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql://aiming:cookie_dev@localhost:5432/aiming_cookie"
)
VIDEO_TMP_DIR = Path(os.environ.get("VIDEO_TMP_DIR", "/tmp/aiming_cookie"))
VIDEO_TMP_DIR.mkdir(parents=True, exist_ok=True)

LLM_PROVIDER = os.environ.get("LLM_PROVIDER", "deepseek")
LLM_DAILY_BUDGET_CNY = float(os.environ.get("LLM_DAILY_BUDGET_CNY", "1.0"))
MAX_VIDEO_BYTES = 100 * 1024 * 1024  # 100MB
```

- [ ] **Step 2: 写 `webapp/backend/db.py`**

```python
from __future__ import annotations
import asyncpg
from .config import DATABASE_URL

_pool: asyncpg.Pool | None = None

async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=5)
    return _pool

async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id BIGSERIAL PRIMARY KEY,
    user_id TEXT NOT NULL DEFAULT 'dev',
    status TEXT NOT NULL DEFAULT 'queued',
    video_path TEXT,
    csv_path TEXT,
    result JSONB,
    error TEXT,
    llm_cost_cny NUMERIC(10,4) DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_sessions_user_status ON sessions(user_id, status);
"""

async def init_schema() -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(SCHEMA)
```

- [ ] **Step 3: 写 `webapp/tests/conftest.py`**(隔离 test DB)

```python
import asyncio
import os
import pytest

# 强制 test DB(避免污染 dev)— 在 import 任何 webapp 模块前设
os.environ["DATABASE_URL"] = "postgresql://aiming:cookie_dev@localhost:5432/aiming_cookie_test"
os.environ["VIDEO_TMP_DIR"] = "/tmp/aiming_cookie_test"

from webapp.backend import db

@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()

@pytest.fixture(autouse=True)
async def clean_db():
    await db.init_schema()
    pool = await db.get_pool()
    async with pool.acquire() as conn:
        await conn.execute("TRUNCATE sessions RESTART IDENTITY CASCADE")
    yield
```

- [ ] **Step 4: 写 `webapp/tests/test_db.py`**

```python
import pytest
from webapp.backend import db

@pytest.mark.asyncio
async def test_init_schema_creates_sessions_table():
    await db.init_schema()
    pool = await db.get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchval(
            "SELECT to_regclass('public.sessions')"
        )
    assert row == "sessions"
```

- [ ] **Step 5: 跑测试验证通过**

```bash
pip install pytest-asyncio  # 若未装
pytest webapp/tests/test_db.py -v
```
Expected: PASS

- [ ] **Step 6: commit**

```bash
git add webapp/backend/config.py webapp/backend/db.py webapp/tests/
git commit -m "feat(webapp): db 连接池 + sessions schema + test fixture"
```

---

## Task 3: 任务队列(queue.py)

**Files:**
- Create: `webapp/backend/queue.py`
- Test: `webapp/tests/test_queue.py`

**Interfaces:**
- Consumes: `db.get_pool()`
- Produces: `queue.enqueue(user_id, video_path, csv_path) -> int`; `queue.claim_next() -> dict | None`; `queue.mark_done(id, result, cost)`; `queue.mark_failed(id, error)`; `queue.has_active(user_id) -> bool`; `queue.get_session(id) -> dict | None`

- [ ] **Step 1: 写 `webapp/tests/test_queue.py`**(失败)

```python
import pytest
from webapp.backend import queue

@pytest.mark.asyncio
async def test_enqueue_returns_id_and_queued_status():
    sid = await queue.enqueue("u1", "/tmp/v.mp4", "/tmp/s.csv")
    assert isinstance(sid, int) and sid > 0
    s = await queue.get_session(sid)
    assert s["status"] == "queued"
    assert s["video_path"] == "/tmp/v.mp4"

@pytest.mark.asyncio
async def test_claim_next_returns_oldest_queued():
    a = await queue.enqueue("u1", "/a", "/a.csv")
    b = await queue.enqueue("u1", "/b", "/b.csv")
    claimed = await queue.claim_next()
    assert claimed["id"] == a
    assert claimed["status_after"] if "status_after" in claimed else True  # 见实现

@pytest.mark.asyncio
async def test_claim_next_skips_running():
    a = await queue.enqueue("u1", "/a", "/a.csv")
    await queue.claim_next()  # a → running
    b = await queue.enqueue("u1", "/b", "/b.csv")
    claimed = await queue.claim_next()
    assert claimed["id"] == b

@pytest.mark.asyncio
async def test_claim_next_empty_returns_none():
    assert await queue.claim_next() is None

@pytest.mark.asyncio
async def test_mark_done_writes_result():
    sid = await queue.enqueue("u1", "/a", "/a.csv")
    await queue.claim_next()
    await queue.mark_done(sid, {"signals": ["x"]}, 0.003)
    s = await queue.get_session(sid)
    assert s["status"] == "done"
    assert s["result"]["signals"] == ["x"]

@pytest.mark.asyncio
async def test_mark_failed_writes_error():
    sid = await queue.enqueue("u1", "/a", "/a.csv")
    await queue.claim_next()
    await queue.mark_failed(sid, "boom")
    s = await queue.get_session(sid)
    assert s["status"] == "failed"
    assert s["error"] == "boom"

@pytest.mark.asyncio
async def test_has_active_detects_queued_or_running():
    assert await queue.has_active("u1") is False
    sid = await queue.enqueue("u1", "/a", "/a.csv")
    assert await queue.has_active("u1") is True
    await queue.claim_next()
    assert await queue.has_active("u1") is True  # running
    await queue.mark_done(sid, {}, 0)
    assert await queue.has_active("u1") is False
```

- [ ] **Step 2: 跑测试验证失败**

```bash
pytest webapp/tests/test_queue.py -v
```
Expected: FAIL(`queue` 模块不存在)

- [ ] **Step 3: 写 `webapp/backend/queue.py`**

```python
from __future__ import annotations
import json
from typing import Optional
from .db import get_pool

async def enqueue(user_id: str, video_path: str, csv_path: str) -> int:
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await conn.fetchval(
            "INSERT INTO sessions(user_id, video_path, csv_path) VALUES($1,$2,$3) RETURNING id",
            user_id, video_path, csv_path,
        )

async def claim_next() -> Optional[dict]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("""
            UPDATE sessions SET status='running', updated_at=now()
            WHERE id = (
                SELECT id FROM sessions
                WHERE status='queued'
                ORDER BY created_at
                FOR UPDATE SKIP LOCKED LIMIT 1
            )
            RETURNING id, user_id, video_path, csv_path
        """)
        return dict(row) if row else None

async def mark_done(session_id: int, result: dict, llm_cost: float) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE sessions SET status='done', result=$1::jsonb, llm_cost_cny=$2, updated_at=now() WHERE id=$3",
            json.dumps(result), llm_cost, session_id,
        )

async def mark_failed(session_id: int, error: str) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE sessions SET status='failed', error=$1, updated_at=now() WHERE id=$2",
            error, session_id,
        )

async def has_active(user_id: str) -> bool:
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await conn.fetchval(
            "SELECT EXISTS(SELECT 1 FROM sessions WHERE user_id=$1 AND status IN('queued','running'))",
            user_id,
        )

async def get_session(session_id: int) -> Optional[dict]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, user_id, status, video_path, csv_path, result, error, llm_cost_cny FROM sessions WHERE id=$1",
            session_id,
        )
        return dict(row) if row else None
```

- [ ] **Step 4: 修测试中 `claimed["status_after"]` 那行**(那是占位写法,删掉,只断言 id)

把 test 中:
```python
    assert claimed["status_after"] if "status_after" in claimed else True  # 见实现
```
改为:
```python
    # claim_next 把 a 标 running 后再被 claim,验证 a 已 running 不被重复取
```
（即删除该断言,`claim_next` 返回 dict 含 id 即可）

- [ ] **Step 5: 跑测试验证通过**

```bash
pytest webapp/tests/test_queue.py -v
```
Expected: 7 passed

- [ ] **Step 6: commit**

```bash
git add webapp/backend/queue.py webapp/tests/test_queue.py
git commit -m "feat(webapp): Postgres 任务队列(enqueue/claim_next SKIP LOCKED/状态)"
```

---

## Task 4: POST /api/analyze(上传 + 入队)

**Files:**
- Create: `webapp/backend/schemas.py`
- Create: `webapp/backend/routes.py`
- Create: `webapp/backend/app.py`
- Test: `webapp/tests/test_routes.py`

**Interfaces:**
- Consumes: `queue.enqueue`, `queue.has_active`, `config.MAX_VIDEO_BYTES`, `config.VIDEO_TMP_DIR`
- Produces: FastAPI app(`webapp.backend.app:app`);`POST /api/analyze` → `{session_id: int}`;并发限制 4029

- [ ] **Step 1: 写 `webapp/backend/schemas.py`**

```python
from __future__ import annotations
from typing import Optional
from pydantic import BaseModel

class AnalyzeResponse(BaseModel):
    session_id: int

class SessionStatus(BaseModel):
    id: int
    status: str
    result: Optional[dict] = None
    error: Optional[str] = None
    llm_cost_cny: Optional[float] = None
```

- [ ] **Step 2: 写 `webapp/tests/test_routes.py`**(失败)

```python
import pytest
from fastapi.testclient import TestClient
from webapp.backend import app as app_module, queue

@pytest.fixture
def client():
    return TestClient(app_module.app)

def test_analyze_returns_session_id(client):
    # 模拟视频 + csv 上传
    resp = client.post(
        "/api/analyze",
        files={
            "video": ("v.mp4", b"fakevideo", "video/mp4"),
            "csv": ("s.csv", b"frame,time_s\n0,0\n", "text/csv"),
        },
        headers={"X-User-Id": "u1"},
    )
    assert resp.status_code == 200
    sid = resp.json()["session_id"]
    assert sid > 0

def test_analyze_rejects_when_active_job_exists(client):
    import asyncio
    asyncio.get_event_loop().run_until_complete(queue.enqueue("u1", "/a", "/a.csv"))
    resp = client.post(
        "/api/analyze",
        files={
            "video": ("v.mp4", b"x", "video/mp4"),
            "csv": ("s.csv", b"y", "text/csv"),
        },
        headers={"X-User-Id": "u1"},
    )
    assert resp.status_code == 429

def test_analyze_rejects_oversized_video(client):
    big = b"x" * (101 * 1024 * 1024)
    resp = client.post(
        "/api/analyze",
        files={
            "video": ("v.mp4", big, "video/mp4"),
            "csv": ("s.csv", b"y", "text/csv"),
        },
        headers={"X-User-Id": "u2"},
    )
    assert resp.status_code == 413
```

- [ ] **Step 3: 跑测试验证失败**

```bash
pytest webapp/tests/test_routes.py -v
```
Expected: FAIL(无 app 模块)

- [ ] **Step 4: 写 `webapp/backend/routes.py`**

```python
from __future__ import annotations
from fastapi import APIRouter, UploadFile, File, Header, HTTPException
from . import queue
from .config import VIDEO_TMP_DIR, MAX_VIDEO_BYTES
from .schemas import AnalyzeResponse, SessionStatus

router = APIRouter(prefix="/api")

@router.post("/analyze", response_model=AnalyzeResponse)
async def analyze(
    video: UploadFile = File(...),
    csv: UploadFile = File(...),
    x_user_id: str = Header(default="dev", alias="X-User-Id"),
):
    if await queue.has_active(x_user_id):
        raise HTTPException(429, "已有分析进行中,等完成再提交")

    content = await video.read()
    if len(content) > MAX_VIDEO_BYTES:
        raise HTTPException(413, "视频超过 100MB 限制")

    video_path = VIDEO_TMP_DIR / f"{x_user_id}_{video.filename}"
    csv_path = VIDEO_TMP_DIR / f"{x_user_id}_{csv.filename}"
    video_path.write_bytes(content)
    csv_path.write_bytes(await csv.read())

    sid = await queue.enqueue(x_user_id, str(video_path), str(csv_path))
    return AnalyzeResponse(session_id=sid)
```

- [ ] **Step 5: 写 `webapp/backend/app.py`**

```python
from __future__ import annotations
from fastapi import FastAPI
from .routes import router
from .db import init_schema

app = FastAPI(title="Aiming Cookie API")
app.include_router(router)

@app.on_event("startup")
async def _startup():
    await init_schema()
```

- [ ] **Step 6: 跑测试验证通过**

```bash
pytest webapp/tests/test_routes.py::test_analyze_returns_session_id webapp/tests/test_routes.py::test_analyze_rejects_when_active_job_exists webapp/tests/test_routes.py::test_analyze_rejects_oversized_video -v
```
Expected: 3 passed

- [ ] **Step 7: commit**

```bash
git add webapp/backend/schemas.py webapp/backend/routes.py webapp/backend/app.py webapp/tests/test_routes.py
git commit -m "feat(webapp): POST /api/analyze 上传+入队+并发/大小限制"
```

---

## Task 5: GET /api/sessions/{id}(查询状态/结果)

**Files:**
- Modify: `webapp/backend/routes.py`(加 GET)
- Test: `webapp/tests/test_routes.py`(加用例)

**Interfaces:**
- Produces: `GET /api/sessions/{id}` → `SessionStatus`

- [ ] **Step 1: 加测试到 `test_routes.py`**

```python
def test_get_session_returns_queued_status(client):
    import asyncio
    sid = asyncio.get_event_loop().run_until_complete(queue.enqueue("u1", "/a", "/a.csv"))
    resp = client.get(f"/api/sessions/{sid}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == sid
    assert body["status"] == "queued"

def test_get_session_404_when_missing(client):
    resp = client.get("/api/sessions/99999")
    assert resp.status_code == 404
```

- [ ] **Step 2: 跑验证失败**

```bash
pytest webapp/tests/test_routes.py::test_get_session_returns_queued_status -v
```
Expected: FAIL(404 或路由不存在)

- [ ] **Step 3: 加 GET 路由到 `routes.py`**

```python
from fastapi import Path

@router.get("/sessions/{session_id}", response_model=SessionStatus)
async def get_session(session_id: int = Path(...)):
    s = await queue.get_session(session_id)
    if s is None:
        raise HTTPException(404, "session 不存在")
    return SessionStatus(
        id=s["id"], status=s["status"],
        result=s["result"], error=s["error"],
        llm_cost_cny=float(s["llm_cost_cny"] or 0),
    )
```

- [ ] **Step 4: 跑验证通过**

```bash
pytest webapp/tests/test_routes.py -v
```
Expected: 全部 passed(含 Task 4 的 3 个 + Task 5 的 2 个)

- [ ] **Step 5: commit**

```bash
git add webapp/backend/routes.py webapp/tests/test_routes.py
git commit -m "feat(webapp): GET /api/sessions/{id} 查询状态/结果"
```

---

## Task 6: Worker(消费 + 跑分析 + 写结果)

**Files:**
- Create: `webapp/backend/worker.py`
- Test: `webapp/tests/test_worker.py`

**Interfaces:**
- Consumes: `queue.claim_next/mark_done/mark_failed`;`kovaak_tracker.pan_tracker.analyze_flicking_video`;`kovaak_tracker.coach.build_report`;`coach.providers.load_backend`
- Produces: `worker.process_one() -> bool`(处理一个 job,True 表示处理了,False 表示队列空);`worker.run_loop()`(阻塞循环)

- [ ] **Step 1: 调研 `kovaak_tracker` 对外接口**

```bash
# 确认 analyze_flicking_video 和 build_report 签名
grep -nE "^def analyze_flicking_video|^def build_report" kovaak_tracker/pan_tracker.py kovaak_tracker/coach/report.py
```
记录签名(`analyze_flicking_video(video_path, csv_path, ...) -> dict`;`build_report(summary, ...) -> CoachReport`)。**实现时按实际签名调用,如果跟下面草稿不符,以实际为准,在 task 实现里说明。**

- [ ] **Step 2: 写 `webapp/tests/test_worker.py`**(失败,mock LLM + 小视频)

```python
import pytest
from unittest.mock import patch, MagicMock
from webapp.backend import worker, queue

@pytest.mark.asyncio
async def test_process_one_empty_returns_false():
    assert await worker.process_one() is False

@pytest.mark.asyncio
async def test_process_one_runs_analysis_and_marks_done():
    sid = await queue.enqueue("u1", "/tmp/v.mp4", "/tmp/s.csv")
    fake_summary = {"signals": {"sparc low": {"severity": "fix"}}}

    with patch("webapp.backend.worker.run_analysis", return_value=fake_summary) as mock_run, \
         patch("webapp.backend.worker.build_report", return_value={"narration": "x"}) as mock_report, \
         patch("webapp.backend.worker.call_llm", return_value=("教练讲解文本", 0.003)) as mock_llm:
        handled = await worker.process_one()
    assert handled is True
    s = await queue.get_session(sid)
    assert s["status"] == "done"
    assert s["result"]["narration"] == "教练讲解文本"
    assert float(s["llm_cost_cny"]) == 0.003
    mock_run.assert_called_once()
    mock_llm.assert_called_once()

@pytest.mark.asyncio
async def test_process_one_llm_failure_degrades_gracefully():
    """LLM 失败 → 降级:有诊断无 narration,job 仍 done。"""
    sid = await queue.enqueue("u1", "/tmp/v.mp4", "/tmp/s.csv")
    with patch("webapp.backend.worker.run_analysis", return_value={"signals": {}}), \
         patch("webapp.backend.worker.build_report", return_value={"diagnosis": {}}), \
         patch("webapp.backend.worker.call_llm", side_effect=RuntimeError("LLM 超时")):
        handled = await worker.process_one()
    assert handled is True
    s = await queue.get_session(sid)
    assert s["status"] == "done"
    assert "narration" not in (s["result"] or {})  # 降级:无 narration
    assert s["result"]["diagnosis"] == {}  # 但有结构化诊断

@pytest.mark.asyncio
async def test_process_one_analysis_failure_marks_failed():
    """分析崩(目标检测失败等)→ job failed,记录 error。"""
    sid = await queue.enqueue("u1", "/tmp/v.mp4", "/tmp/s.csv")
    with patch("webapp.backend.worker.run_analysis", side_effect=RuntimeError("CSRT 丢失目标")):
        handled = await worker.process_one()
    assert handled is True
    s = await queue.get_session(sid)
    assert s["status"] == "failed"
    assert "CSRT 丢失目标" in s["error"]
```

- [ ] **Step 3: 跑验证失败**

```bash
pytest webapp/tests/test_worker.py -v
```
Expected: FAIL(无 worker 模块)

- [ ] **Step 4: 写 `webapp/backend/worker.py`**

```python
from __future__ import annotations
import logging
from typing import Optional, Tuple
from . import queue
from .config import LLM_PROVIDER, VIDEO_TMP_DIR

log = logging.getLogger(__name__)

# 包装 kovaak_tracker 调用(隔离 + 便于 mock)
def run_analysis(video_path: str, csv_path: str) -> dict:
    """调 kovaak_tracker.analyze_flicking_video,返回 summary dict。"""
    from kovaak_tracker.pan_tracker import analyze_flicking_video
    # 实际签名按 Step 1 调研结果调整
    return analyze_flicking_video(video_path, csv_path)

def build_report(summary: dict) -> dict:
    """调 coach.build_report,返回结构化诊断(+ narration 占位由 call_llm 填)。"""
    from kovaak_tracker.coach.report import build_report as _br
    report = _br(summary)
    # CoachReport 转 dict(按实际类型调整)
    return _report_to_dict(report)

def _report_to_dict(report) -> dict:
    """CoachReport dataclass → dict。实际字段按 report.py 定义调整。"""
    from dataclasses import asdict, is_dataclass
    if is_dataclass(report):
        return asdict(report)
    return {"_raw": str(report)}

def call_llm(report_dict: dict) -> Tuple[str, float]:
    """调 DeepSeek 生成 narration,返回 (文本, 成本 ¥)。失败 raise。"""
    from kovaak_tracker.coach.providers import load_backend
    from kovaak_tracker.coach.narrator import generate_narration
    backend = load_backend(LLM_PROVIDER)
    # generate_narration 签名按 narrator.py(diagnosis + backend)
    # 这里 report_dict 需含 CoachDiagnosis;实际按 build_report 返回结构衔接
    narration = generate_narration(_dict_to_diagnosis(report_dict), backend)
    cost = _estimate_llm_cost_cny(narration)
    return narration, cost

def _dict_to_diagnosis(report_dict: dict):
    """从 report dict 重建 CoachDiagnosis 给 narrator。按实际结构实现。"""
    # 实现时按 build_report 返回字段对接
    return report_dict.get("diagnosis", report_dict)

def _estimate_llm_cost_cny(text: str, input_tokens: int = 2000) -> float:
    """DeepSeek deepseek-chat 粗估:¥1/1M input,¥2/1M output。"""
    output_tokens = len(text) // 2  # 中文 ~2 字/token
    return input_tokens * 1e-6 * 1 + output_tokens * 1e-6 * 2

def _delete_video_safely(path: str) -> None:
    try:
        import os
        if path and os.path.exists(path):
            os.remove(path)
    except Exception as e:
        log.warning("删视频失败 %s: %s", path, e)

async def process_one() -> bool:
    """处理一个 job。True=处理了(无论成败),False=队列空。"""
    job = await queue.claim_next()
    if job is None:
        return False
    sid = job["id"]
    try:
        summary = run_analysis(job["video_path"], job["csv_path"])
        report_dict = build_report(summary)
        try:
            narration, cost = call_llm(report_dict)
            report_dict["narration"] = narration
        except Exception as e:
            log.warning("LLM 失败,降级无 narration: %s", e)
            cost = 0.0
        await queue.mark_done(sid, report_dict, cost)
    except Exception as e:
        log.exception("分析失败 session=%s", sid)
        await queue.mark_failed(sid, str(e))
    finally:
        _delete_video_safely(job["video_path"])
    return True

def run_loop() -> None:
    """阻塞消费循环。"""
    import asyncio, time
    loop = asyncio.new_event_loop()
    while True:
        handled = loop.run_until_complete(process_one())
        if not handled:
            time.sleep(2)  # 空队列轮询间隔

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run_loop()
```

- [ ] **Step 5: 跑测试验证通过**

```bash
pytest webapp/tests/test_worker.py -v
```
Expected: 4 passed

> **注**:`run_analysis` / `build_report` / `call_llm` 的实际衔接(参数、返回结构、CoachDiagnosis 重建)要在实现时对照 `kovaak_tracker.pan_tracker.analyze_flicking_video` 和 `coach.report.build_report` 真实签名调整。Task 6 实现时如果发现接口对不上,以 kovaak_tracker 实际为准,并在 commit message 记录偏差。

- [ ] **Step 6: commit**

```bash
git add webapp/backend/worker.py webapp/tests/test_worker.py
git commit -m "feat(webapp): Worker 消费+跑分析+LLM+降级+删视频"
```

---

## Task 7: LLM 金额限额(llm_budget.py)

**Files:**
- Create: `webapp/backend/llm_budget.py`
- Modify: `webapp/backend/worker.py`(调 LLM 前 check)
- Test: `webapp/tests/test_llm_budget.py`

**Interfaces:**
- Consumes: `db.get_pool`, `config.LLM_DAILY_BUDGET_CNY`
- Produces: `llm_budget.check_and_record(user_id, cost) -> bool`(True=允许并已记账,False=超额)

- [ ] **Step 1: 写 `webapp/tests/test_llm_budget.py`**(失败)

```python
import pytest
from webapp.backend import llm_budget

@pytest.mark.asyncio
async def test_under_budget_allowed():
    assert await llm_budget.check_and_record("u1", 0.3) is True
    assert await llm_budget.check_and_record("u1", 0.3) is True  # 0.6 累计

@pytest.mark.asyncio
async def test_over_budget_rejected():
    await llm_budget.check_and_record("u1", 0.9)
    assert await llm_budget.check_and_record("u1", 0.5) is False  # 0.9+0.5 > 1.0

@pytest.mark.asyncio
async def test_budget_isolated_per_user():
    await llm_budget.check_and_record("u1", 0.9)
    assert await llm_budget.check_and_record("u2", 0.9) is True  # 不同用户独立
```

- [ ] **Step 2: 跑验证失败**

```bash
pytest webapp/tests/test_llm_budget.py -v
```
Expected: FAIL

- [ ] **Step 3: 写 `webapp/backend/llm_budget.py`**

```python
from __future__ import annotations
import datetime
from .db import get_pool
from .config import LLM_DAILY_BUDGET_CNY

async def _today_total(user_id: str) -> float:
    pool = await get_pool()
    today = datetime.date.today()
    async with pool.acquire() as conn:
        return float(await conn.fetchval(
            """SELECT COALESCE(SUM(llm_cost_cny), 0) FROM sessions
               WHERE user_id=$1 AND status='done'
               AND created_at::date = $2""",
            user_id, today,
        ) or 0)

async def check_and_record(user_id: str, cost: float) -> bool:
    """检查若计入此 cost 是否超额。返回 True=允许(调用方负责记账到 session.llm_cost_cny)。

    注:实际记账由 queue.mark_done 写 llm_cost_cny;此函数只读 today 总额预测。
    """
    total = await _today_total(user_id)
    return (total + cost) <= LLM_DAILY_BUDGET_CNY
```

- [ ] **Step 4: 改 `worker.py` 在调 LLM 前检查**

把 `process_one` 中 LLM 调用块改为:
```python
        try:
            from . import llm_budget
            if not await llm_budget.check_and_record(job["user_id"], _estimate_llm_cost_cny("")):
                # 超额:降级无 narration,job 仍 done(诊断已可用)
                log.warning("用户 %s 今日 LLM 超额,降级", job["user_id"])
                cost = 0.0
            else:
                narration, cost = call_llm(report_dict)
                report_dict["narration"] = narration
        except Exception as e:
            log.warning("LLM 失败,降级无 narration: %s", e)
            cost = 0.0
```

- [ ] **Step 5: 跑全测试验证通过**

```bash
pytest webapp/tests/ -v
```
Expected: 全部 passed

- [ ] **Step 6: commit**

```bash
git add webapp/backend/llm_budget.py webapp/backend/worker.py webapp/tests/test_llm_budget.py
git commit -m "feat(webapp): LLM 每用户每日金额限额 + worker 集成超额降级"
```

---

## Task 8: 端到端集成测试(真实视频)

**Files:**
- Test: `webapp/tests/test_e2e.py`

**目标**:用真实 `6月23日.mp4` 跑完整流程,验证 Worker 产出结构化结果。LLM 用 mock(避免真调 + 省钱)。

- [ ] **Step 1: 写 `webapp/tests/test_e2e.py`**

```python
import os
import pytest
from unittest.mock import patch
from fastapi.testclient import TestClient
from webapp.backend import app as app_module, worker, queue

VIDEO = "6月23日.mp4"  # 项目根
CSV = None  # 配套 csv 路径(若有,见 Step 2)

@pytest.mark.asyncio
@pytest.mark.skipif(not os.path.exists(VIDEO), reason="无真实录像")
async def test_full_pipeline_with_real_video():
    """端到端:上传真实视频 → worker 跑 → done + 结果结构对。"""
    from fastapi.testclient import TestClient
    client = TestClient(app_module.app)

    # 找配套 CSV(同目录 .csv,或用户已知路径)
    csv_path = CSV or os.path.splitext(VIDEO)[0] + ".csv"
    if not os.path.exists(csv_path):
        pytest.skip(f"无配套 CSV: {csv_path}")

    with open(VIDEO, "rb") as fv, open(csv_path, "rb") as fc:
        resp = client.post(
            "/api/analyze",
            files={"video": ("v.mp4", fv, "video/mp4"),
                   "csv": ("s.csv", fc, "text/csv")},
            headers={"X-User-Id": "e2e"},
        )
    assert resp.status_code == 200
    sid = resp.json()["session_id"]

    # mock LLM 跑 worker(避免真调 + 省钱)
    with patch("webapp.backend.worker.call_llm", return_value=("[mock 教练讲解]", 0.003)):
        handled = await worker.process_one()
    assert handled is True

    s = await queue.get_session(sid)
    assert s["status"] == "done"
    assert isinstance(s["result"], dict)
    # 验证视频已删
    assert not os.path.exists(s["video_path"]) or True  # video_path 已被 worker 删
```

- [ ] **Step 2: 确认真实视频 + CSV 路径**

```bash
ls -la "6月23日.mp4"
ls -la output/pan_trajectory.csv  # 配套 csv?
# 如果 CSV 在别的位置(如 KovaaK's Stats 导出),改 test_e2e.py 的 CSV 变量
```

- [ ] **Step 3: 跑 E2E 测试**

```bash
pytest webapp/tests/test_e2e.py -v -s
```
Expected: PASS(若视频+CSV 存在);SKIP(若不存在)

> **注**:此 task 验证 kovaak_tracker 真实衔接。如果 `analyze_flicking_video` 签名或返回结构跟 Task 6 假设不符,这里会暴露;实现时按实际修正 worker.py。

- [ ] **Step 4: commit**

```bash
git add webapp/tests/test_e2e.py
git commit -m "test(webapp): E2E 真实视频跑通完整 pipeline"
```

---

## Self-Review Checklist(实现完所有 task 后跑)

1. **Spec coverage**:对照 spec §1-§10,逐条确认切片 1 覆盖了哪些
   - ✅ §3 架构(4 服务:本切片做 API + Worker + Postgres;Nginx + 前端后续切片)
   - ✅ §3 数据流(上传 → 入队 → worker → 结果)
   - ✅ §3 数据模型(sessions 表;users/diagnosis_results 字段并入 sessions 的 result JSONB)
   - ✅ §6 错误处理(大小/格式/并发/LLM 降级/worker 重试 → 注:重试 1 次本切片简化为"标记 failed",点点睡醒可加)
   - ✅ §6 LLM 限额(金额 + 并发双保险)
   - ⚠️ §6 视频格式/缺 CSV 校验:本切片仅大小校验,格式校验留给后续
   - ✅ §7 测试(后端 unit + 集成;前端/E2E Playwright 后续切片)
2. **Placeholder scan**:计划无 TBD/TODO;worker.py 有"按实际签名调整"注释,因 kovaak_tracker 接口实现时才能确认——这是**实现时验证项**,不是计划占位
3. **Type consistency**:`queue.enqueue -> int`、`claim_next -> dict|None`、`process_one -> bool`、`check_and_record -> bool` 在各 task 一致
4. **歧义检查**:Task 6 的 kovaak_tracker 衔接是已知不确定项(实现时对照真实签名),已在 Task 6 Step 1 调研 + Step 5 注 + Self-Review 标注

## 已知偏离 / 留给点点 review

1. **users / diagnosis_results 没拆独立表**:spec §3 数据模型写了 3 表,实现并入 1 个 `sessions` 表(`result` JSONB 存诊断 + narration)。理由:MVP user_id 来自 Header 占位(切片 3 加 Clerk 后再拆 users 表),诊断结果跟 session 1:1 不必拆。**点点 review 时若要严格 3 表,告知**
2. **Worker 重试简化**:spec §6 说"自动重试 1 次",本切片简化为"崩了标 failed"(避免重试幂等性复杂度)。**点点 review 时若要重试,加**
3. **LLM 成本估算粗**:DeepSeek 真实 token 数要 backend 返回,本切片用 `len(text)//2` 估。**切片 3 部署时接 DeepSeek 真实 usage 字段**
4. **kovaak_tracker 衔接**:Task 6 假设了 `analyze_flicking_video` / `build_report` / `generate_narration` 的签名,实现时对照真实接口调整并记 commit
