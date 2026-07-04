from __future__ import annotations

import datetime

from .db import get_conn
from .config import LLM_DAILY_BUDGET_CNY


async def _today_total(user_id: str) -> float:
    """该用户今日已 done sessions 的 llm_cost_cny 累计。

    created_at 是 SQLite CURRENT_TIMESTAMP(UTC),today 也用 UTC 匹配
    (避免本地 UTC+8 跨日期漏算)。
    """
    conn = await get_conn()
    today = datetime.datetime.now(datetime.timezone.utc).date().isoformat()
    cur = await conn.execute(
        "SELECT COALESCE(SUM(llm_cost_cny), 0) FROM sessions "
        "WHERE user_id=? AND status='done' AND date(created_at)=?",
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
