"""Test: update_plan_status must persist changes to all backends."""
import pytest
import json
from unittest.mock import AsyncMock, MagicMock, patch


class FakeRedisBackend:
    """Simulates Redis: get() returns a fresh deserialized copy each time."""
    def __init__(self):
        self._data = {}

    async def store(self, session_id, payload, prefix=""):
        key = f"{session_id}:{prefix}"
        self._data[key] = json.dumps(payload)
        return f"ref:{key}"

    async def get(self, session_id, ref):
        # ref = "ref:session_id:prefix"
        key = ref.removeprefix("ref:")
        raw = self._data.get(key)
        if raw is None:
            return None
        return json.loads(raw)  # fresh copy each call — Redis behavior


class TestPlanStatusPersistence:
    @pytest.mark.asyncio
    async def test_update_plan_status_persists(self):
        """After update_plan_status, a subsequent load_plan must see the new status."""
        from app.services.plan_mode import update_plan_status, load_plan

        fake = FakeRedisBackend()

        # Store initial plan
        plan_id = await fake.store("sess-1", {
            "__kind__": "plan_proposal",
            "__status__": "pending",
            "steps": [],
        }, prefix="plan")

        with patch("app.services.plan_mode.session_data_manager", fake):
            # Update status to running
            await update_plan_status("sess-1", plan_id, __status__="running")

            # Load again — must see "running"
            updated = await load_plan("sess-1", plan_id)

        assert updated is not None, "Plan should exist after update"
        assert updated["__status__"] == "running", (
            f"Expected 'running' but got '{updated.get('__status__')}' — mutation was lost"
        )
