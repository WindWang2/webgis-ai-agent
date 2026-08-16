"""Regression test for SEC-10 (deep-audit round 3):

get_or_create_conversation's SELECT→INSERT retry only caught errors whose
message contained "locked" — a concurrent double-submit produced a PRIMARY
KEY IntegrityError (no "locked" in the message) that escaped as an HTTP 500.
The retry now also catches IntegrityError and re-SELECTs the winner's row.
"""
import pytest
from sqlalchemy.exc import IntegrityError


async def _noop(*a, **k):
    return None


def _fake_db(execute, flush=None, commit=None, rollback=None):
    return type("FakeDb", (), {
        "execute": execute,
        "add": lambda *a, **k: None,
        "flush": flush or _noop,
        "commit": commit or _noop,
        "rollback": rollback or _noop,
    })()


@pytest.mark.asyncio
async def test_get_or_create_recovers_from_integrity_conflict(monkeypatch):
    from app.services.history_service_async import AsyncHistoryService

    svc = AsyncHistoryService(db=None)

    # Mock the DB: first attempt INSERT conflicts (concurrent winner exists),
    # second attempt finds the row.
    class _FakeResult:
        def __init__(self, rows):
            self._rows = rows

        def scalar_one_or_none(self):
            return self._rows[0] if self._rows else None

        def scalar_one(self):
            return self._rows[0]

    winner = object()  # the row the concurrent request created

    state = {"attempts": 0}

    async def _fake_execute(stmt, *a, **k):
        state["attempts"] += 1
        # SELECTs return the winner once it exists; the INSERT raises.
        if state["attempts"] == 1:
            return _FakeResult([])  # SELECT: absent
        if state["attempts"] == 2:
            raise IntegrityError("stmt", {}, Exception("UNIQUE constraint failed"))
        return _FakeResult([winner])  # SELECT after conflict: found

    async def _fake_enforce_cap(user_id=None, owner_token=None):
        return []

    svc.db = _fake_db(_fake_execute)
    monkeypatch.setattr(svc, "_enforce_cap", _fake_enforce_cap)

    # SEC-10: must return the winner instead of raising IntegrityError.
    conv = await svc.get_or_create_conversation("sess_double_submit")
    assert conv is winner
    assert state["attempts"] >= 3, "must have retried after the conflict"


@pytest.mark.asyncio
async def test_get_or_create_raises_after_exhausting_retries(monkeypatch):
    """If the conflict persists (or a real error occurs), the exception must
    surface after the retry budget — not be swallowed."""
    from app.services.history_service_async import AsyncHistoryService

    svc = AsyncHistoryService.__new__(AsyncHistoryService)

    class _FakeResult:
        def scalar_one_or_none(self):
            return None

    async def _fake_execute(stmt, *a, **k):
        raise IntegrityError("stmt", {}, Exception("UNIQUE constraint failed"))

    async def _fake_enforce_cap(user_id=None, owner_token=None):
        return []

    svc.db = _fake_db(_fake_execute)
    monkeypatch.setattr(svc, "_enforce_cap", _fake_enforce_cap)

    with pytest.raises(IntegrityError):
        await svc.get_or_create_conversation("sess_persistent_conflict")
