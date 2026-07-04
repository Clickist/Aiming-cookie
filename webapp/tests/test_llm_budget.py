from __future__ import annotations

import pytest

from webapp.backend import llm_budget, queue


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
