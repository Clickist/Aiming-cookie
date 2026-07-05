from __future__ import annotations

import json
from typing import Optional

from .db import get_conn


async def enqueue(
    user_id: str, video_path: str, csv_path: str,
    cm_per_360: float | None = None, fov: float | None = None,
) -> int:
    conn = await get_conn()
    cur = await conn.execute(
        "INSERT INTO sessions(user_id, video_path, csv_path, cm_per_360, fov) "
        "VALUES(?, ?, ?, ?, ?) RETURNING id",
        (user_id, video_path, csv_path, cm_per_360, fov),
    )
    row = await cur.fetchone()
    await conn.commit()
    return row["id"]


async def claim_next() -> Optional[dict]:
    """Atomically claim the next queued job.

    SQLite 无 FOR UPDATE SKIP LOCKED,用 BEGIN IMMEDIATE(写锁)序列化。
    单 worker 场景下安全;多 worker 由 IMMEDIATE 锁序列化(并发等锁)。
    部署换 Postgres 时改回 `FOR UPDATE SKIP LOCKED`(见 spec 部署架构)。
    """
    conn = await get_conn()
    await conn.execute("BEGIN IMMEDIATE")
    try:
        cur = await conn.execute(
            "SELECT id FROM sessions WHERE status='queued' "
            "ORDER BY created_at LIMIT 1"
        )
        row = await cur.fetchone()
        if row is None:
            await conn.execute("COMMIT")
            return None
        sid = row["id"]
        await conn.execute(
            "UPDATE sessions SET status='running', updated_at=CURRENT_TIMESTAMP "
            "WHERE id=?",
            (sid,),
        )
        cur = await conn.execute(
            "SELECT id, user_id, video_path, csv_path, cm_per_360, fov "
            "FROM sessions WHERE id=?",
            (sid,),
        )
        claimed = await cur.fetchone()
        await conn.execute("COMMIT")
        return dict(claimed) if claimed else None
    except Exception:
        await conn.execute("ROLLBACK")
        raise


async def mark_done(session_id: int, result: dict, llm_cost: float) -> None:
    conn = await get_conn()
    await conn.execute(
        "UPDATE sessions SET status='done', result=?, llm_cost_cny=?, "
        "updated_at=CURRENT_TIMESTAMP WHERE id=?",
        (json.dumps(result), llm_cost, session_id),
    )
    await conn.commit()


async def mark_failed(session_id: int, error: str) -> None:
    conn = await get_conn()
    await conn.execute(
        "UPDATE sessions SET status='failed', error=?, "
        "updated_at=CURRENT_TIMESTAMP WHERE id=?",
        (error, session_id),
    )
    await conn.commit()


async def add_llm_cost(session_id: int, delta: float) -> None:
    """累加 LLM cost 到已 done 的 session(用于 chat 等非 worker 路径记账)。

    worker 路径用 mark_done 一次性设 cost;chat 在 session 已 done 后追加,
    用 UPDATE 累加,这样下次 llm_budget.check_and_record 反映真实累计
    (避免反复调 chat 绕过日预算限制)。
    """
    conn = await get_conn()
    await conn.execute(
        "UPDATE sessions SET llm_cost_cny = COALESCE(llm_cost_cny, 0) + ?, "
        "updated_at=CURRENT_TIMESTAMP WHERE id=?",
        (delta, session_id),
    )
    await conn.commit()


async def has_active(user_id: str) -> bool:
    conn = await get_conn()
    cur = await conn.execute(
        "SELECT EXISTS(SELECT 1 FROM sessions "
        "WHERE user_id=? AND status IN ('queued', 'running'))",
        (user_id,),
    )
    row = await cur.fetchone()
    return bool(row[0])


async def get_session(session_id: int) -> Optional[dict]:
    conn = await get_conn()
    cur = await conn.execute(
        "SELECT id, user_id, status, video_path, csv_path, result, error, "
        "llm_cost_cny FROM sessions WHERE id=?",
        (session_id,),
    )
    row = await cur.fetchone()
    if row is None:
        return None
    d = dict(row)
    # result 是 JSON 字符串,解析回 dict(便于 routes 序列化)
    if d.get("result"):
        try:
            d["result"] = json.loads(d["result"])
        except (json.JSONDecodeError, TypeError):
            pass
    return d
