"""Legacy fixed-CNY budget helper; active selected-provider flows do not call it."""
from __future__ import annotations

import datetime

from .db import get_conn
from .config import LLM_DAILY_BUDGET_CNY


async def _today_total(user_id: str) -> float:
    """该用户今日已 done sessions 的 llm_cost_cny 累计。

    按 updated_at 而非 created_at 过滤:chat 路径的 cost 经 queue.add_llm_cost
    累加到已 done 的 session,会刷新 updated_at 但不动 created_at。若按
    created_at 过滤,跨日对话(session 昨天创建)的 chat cost 累加会被忽略
    → _today_total 返回 0 → 预算绕过。updated_at 在每次 mark_done /
    add_llm_cost 时刷新,反映"今日的 cost 写入"。
    updated_at 是 SQLite CURRENT_TIMESTAMP(UTC),today 也用 UTC 匹配
    (避免本地 UTC+8 跨日期漏算)。
    """
    conn = await get_conn()
    today = datetime.datetime.now(datetime.timezone.utc).date().isoformat()
    cur = await conn.execute(
        "SELECT COALESCE(SUM(llm_cost_cny), 0) FROM sessions "
        "WHERE user_id=? AND status='done' AND date(updated_at)=?",
        (user_id, today),
    )
    row = await cur.fetchone()
    return float(row[0] or 0)


async def check_and_record(user_id: str, cost: float) -> bool:
    """检查若计入此 cost 是否超额。

    返回 True=允许。实际记账由 queue.mark_done 写 llm_cost_cny
    (此函数只读 today done total 做预测)。
    """
    total = await _today_total(user_id)
    return (total + cost) <= LLM_DAILY_BUDGET_CNY
