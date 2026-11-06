"""Independent behavioral regressions for final review runtime findings."""
import asyncio
from types import SimpleNamespace
import threading

import pytest


@pytest.mark.parametrize('org_value', [0, -1])
def test_mission_unlimited_org_policy_keeps_server_ceiling_and_requested_zero(org_value):
    from app.services.mission_runtime.quota_policy import MISSION_CEILINGS, mission_quota
    db = SimpleNamespace(get=lambda *args: SimpleNamespace(
        max_storage_bytes=org_value, max_concurrent_tasks=org_value, rate_limit_per_min=10000))
    limits = mission_quota(db, '1', {})
    assert limits['bytes'] == MISSION_CEILINGS['bytes']
    assert limits['storage_bytes'] == MISSION_CEILINGS['storage_bytes']
    assert limits['concurrent_tasks'] == MISSION_CEILINGS['concurrent_tasks']
    requested = mission_quota(db, '1', {'bytes': 0, 'storage_bytes': 0, 'concurrent_tasks': 0})
    assert requested['bytes'] == requested['storage_bytes'] == requested['concurrent_tasks'] == 0


def test_mission_positive_org_policy_only_tightens_finite_server_limits():
    from app.services.mission_runtime.quota_policy import mission_quota
    db = SimpleNamespace(get=lambda *args: SimpleNamespace(
        max_storage_bytes=123, max_concurrent_tasks=2, rate_limit_per_min=100))
    assert mission_quota(db, '1', {})['bytes'] == 123
    assert mission_quota(db, '1', {})['concurrent_tasks'] == 2
    assert mission_quota(db, '1', {'concurrent_tasks': 1})['concurrent_tasks'] == 1


@pytest.mark.asyncio
async def test_rag_probe_waits_for_metadata_in_worker_thread(monkeypatch):
    from app.api.routes.config import _check_rag_store
    from app.services import rag_service
    main_thread = threading.get_ident()
    entered, release = threading.Event(), threading.Event()
    seen = []
    def load():
        seen.append(threading.get_ident())
        entered.set()
        assert release.wait(2), 'reader lock probe did not release'
        return {'chunks': [{}, {}]}
    monkeypatch.setattr(rag_service._default_store, 'load_metadata', load)
    task = asyncio.create_task(_check_rag_store())
    try:
        assert await asyncio.to_thread(entered.wait, 1)
        assert not task.done()
        assert seen and seen[0] != main_thread
    finally:
        release.set()
    assert '2' in await task
