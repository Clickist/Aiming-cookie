"""僵尸条目兜底：pending + waiting_for_sources 超龄 run 终态标记为 source_unavailable。

20260914 远程诊断包实证（新用户首场 10 条 pending 永久等待）：waiting_for_sources
等的 KovaaK 侧文件在采集会话结束后不可能再出现，历史页却永远显示采集等待。
"""

from datetime import datetime, timedelta, timezone

import pytest

from webapp.backend import kovaak_run_store

_USER = "desktop-local"


async def _make_pending_run(source_key: str) -> dict:
    run = await kovaak_run_store.upsert_kovaak_run(
        user_id=_USER,
        source_key=source_key,
        scenario="zombie scenario",
    )
    run = await kovaak_run_store.set_run_finalization_state(
        run["id"], _USER, "pending", "waiting_for_sources",
    ) or run
    return run


def _backdate_created_at(run_id: int, *, hours_ago: float) -> None:
    stored = kovaak_run_store._load_run(run_id)
    assert stored is not None
    created = datetime.now(timezone.utc) - timedelta(hours=hours_ago)
    stored["created_at"] = created.strftime("%Y-%m-%dT%H:%M:%SZ")
    kovaak_run_store._save_run(stored)


@pytest.mark.asyncio
async def test_stale_pending_run_expires_to_source_unavailable():
    run = await _make_pending_run("stale-run")
    _backdate_created_at(run["id"], hours_ago=25)

    expired = await kovaak_run_store.expire_stale_pending_runs(_USER)

    assert expired == [run["id"]]
    stored = await kovaak_run_store.get_kovaak_run(run["id"], _USER)
    assert stored is not None
    assert stored["finalization_state"] == "source_unavailable"
    # 错误码原样保留供排查。
    assert stored["finalization_error"] == "waiting_for_sources"


@pytest.mark.asyncio
async def test_fresh_pending_run_stays_pending():
    run = await _make_pending_run("fresh-run")

    expired = await kovaak_run_store.expire_stale_pending_runs(_USER)

    assert expired == []
    stored = await kovaak_run_store.get_kovaak_run(run["id"], _USER)
    assert stored is not None
    assert stored["finalization_state"] == "pending"


@pytest.mark.asyncio
async def test_other_states_and_errors_untouched():
    stale_age = 48
    zombie = await _make_pending_run("zombie-run")
    _backdate_created_at(zombie["id"], hours_ago=stale_age)

    retryable = await _make_pending_run("retryable-run")
    retryable = await kovaak_run_store.set_run_finalization_state(
        retryable["id"], _USER, "retryable", "trace_pending",
    ) or retryable
    _backdate_created_at(retryable["id"], hours_ago=stale_age)

    finalized = await _make_pending_run("finalized-run")
    finalized = await kovaak_run_store.set_run_finalization_state(
        finalized["id"], _USER, "finalized", None,
    ) or finalized
    _backdate_created_at(finalized["id"], hours_ago=stale_age)

    expired = await kovaak_run_store.expire_stale_pending_runs(_USER)

    assert expired == [zombie["id"]]
    stored_retryable = await kovaak_run_store.get_kovaak_run(retryable["id"], _USER)
    assert stored_retryable is not None
    assert stored_retryable["finalization_state"] == "retryable"
    stored_finalized = await kovaak_run_store.get_kovaak_run(finalized["id"], _USER)
    assert stored_finalized is not None
    assert stored_finalized["finalization_state"] == "finalized"


@pytest.mark.asyncio
async def test_expiry_is_idempotent():
    run = await _make_pending_run("idempotent-run")
    _backdate_created_at(run["id"], hours_ago=30)

    first = await kovaak_run_store.expire_stale_pending_runs(_USER)
    second = await kovaak_run_store.expire_stale_pending_runs(_USER)

    assert first == [run["id"]]
    assert second == []


@pytest.mark.asyncio
async def test_expiry_scoped_to_user():
    run = await _make_pending_run("other-user-run")
    _backdate_created_at(run["id"], hours_ago=30)

    expired = await kovaak_run_store.expire_stale_pending_runs("someone-else")

    assert expired == []
    stored = await kovaak_run_store.get_kovaak_run(run["id"], _USER)
    assert stored is not None
    assert stored["finalization_state"] == "pending"
