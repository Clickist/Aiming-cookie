from __future__ import annotations

import datetime

import pytest

from webapp.backend import db, llm_budget, queue


@pytest.mark.asyncio
async def test_under_budget_allowed_when_no_done():
    """无 done session 时,today total=0,允许。"""
    assert await llm_budget.check_and_record("u1", 0.3) is True


@pytest.mark.asyncio
async def test_over_budget_rejected_after_done_accumulates():
    """done 累计超额 → 拒绝。"""
    sid = await queue.enqueue("u1", "/a", "/a.csv")
    await queue.claim_next()
    await queue.mark_done(sid, {}, 0.9)  # 今日已花 0.9
    assert await llm_budget.check_and_record("u1", 0.5) is False  # 0.9 + 0.5 > 1.0


@pytest.mark.asyncio
async def test_budget_isolated_per_user():
    """不同用户额度独立。"""
    sid = await queue.enqueue("u1", "/a", "/a.csv")
    await queue.claim_next()
    await queue.mark_done(sid, {}, 0.9)
    # u2 无 done,total=0,允许
    assert await llm_budget.check_and_record("u2", 0.9) is True


@pytest.mark.asyncio
async def test_under_boundary_allowed():
    """刚好等于上限 → 允许(<=)。"""
    sid = await queue.enqueue("u1", "/a", "/a.csv")
    await queue.claim_next()
    await queue.mark_done(sid, {}, 0.5)
    assert await llm_budget.check_and_record("u1", 0.5) is True  # 0.5 + 0.5 = 1.0


@pytest.mark.asyncio
async def test_budget_counts_cross_day_session_by_updated_at():
    """回归:跨日 session(Day1 created + done,Day2 仍在 chat)的累计 cost
    应计入今日预算。

    旧版按 created_at 过滤:session 昨天 created → date(created_at)≠today
    → _today_total 返回 0 → 预算绕过(chat 永远通过)。
    修复后按 updated_at 过滤:add_llm_cost 刷新 updated_at 到今日 → 计入。
    """
    today = datetime.datetime.now(datetime.timezone.utc)
    yesterday = today - datetime.timedelta(days=1)
    sid = await queue.enqueue("u1", "/v", "/c")
    conn = await db.get_conn()
    # 模拟:session 昨天创建并 done,但今天又 chat(add_llm_cost 把 updated_at
    # 刷到今天,llm_cost_cny 累加到 0.9)。
    await conn.execute(
        "UPDATE sessions SET status='done', llm_cost_cny=0.9, "
        "created_at=?, updated_at=? WHERE id=?",
        (yesterday.strftime("%Y-%m-%d %H:%M:%S"),
         today.strftime("%Y-%m-%d %H:%M:%S"), sid),
    )
    await conn.commit()
    # created_at=昨天,updated_at=今天,cost=0.9
    # 修复:查 updated_at → 0.9 + 0.5 > 1.0 → False(拦截)
    # 旧 bug:查 created_at → 0 + 0.5 ≤ 1.0 → True(绕过)
    assert await llm_budget.check_and_record("u1", 0.5) is False
