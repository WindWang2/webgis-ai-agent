"""Server-owned atomic reservation ledger for automatic visual repair attempts."""
from __future__ import annotations

import uuid
from typing import Any

from app.services.session_state_atomic import atomic_update_state


class BudgetExhausted(RuntimeError):
    pass


async def reserve(store: Any, session_id: str, key: str, revision: int, *, per_revision: int, per_session: int) -> tuple[str, str]:
    token = uuid.uuid4().hex
    def update(snapshot: Any) -> dict:
        raw = snapshot.get(key)
        budget = dict(raw) if isinstance(raw, dict) else {}
        revisions = dict(budget.get("revisions") or {})
        total = int(budget.get("session_total") or 0)
        if int(revisions.get(str(revision)) or 0) >= per_revision:
            raise BudgetExhausted("revision_budget_exhausted")
        if total >= per_session:
            raise BudgetExhausted("session_budget_exhausted")
        revisions[str(revision)] = int(revisions.get(str(revision)) or 0) + 1
        budget.update(revisions=dict(list(revisions.items())[-8:]), session_total=total + 1)
        budget["reservations"] = {**(budget.get("reservations") or {}), token: revision}
        return {key: budget}
    try:
        result = await atomic_update_state(store, session_id, [key], update)
        return (token, "") if result is not None else ("", "budget_unavailable")
    except BudgetExhausted as exc:
        return "", str(exc)
    except Exception:
        return "", "budget_unavailable"


async def finish(store: Any, session_id: str, key: str, token: str, *, retain_charge: bool) -> bool:
    def update(snapshot: Any) -> dict:
        raw = snapshot.get(key)
        budget = dict(raw) if isinstance(raw, dict) else {}
        reservations = dict(budget.get("reservations") or {})
        revision = reservations.pop(token, None)
        if revision is not None and not retain_charge:
            revisions = dict(budget.get("revisions") or {})
            revisions[str(revision)] = max(0, int(revisions.get(str(revision)) or 0) - 1)
            budget["revisions"] = revisions
            budget["session_total"] = max(0, int(budget.get("session_total") or 0) - 1)
        budget["reservations"] = reservations
        return {key: budget}
    try:
        return await atomic_update_state(store, session_id, [key], update) is not None
    except Exception:
        return False
