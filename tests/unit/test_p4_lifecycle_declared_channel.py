"""Data Lifecycle — 显式声明通道：角色/持久层赋值（P4 补强 D4 服务面）。"""
from __future__ import annotations

import pytest

from app.services.data_lifecycle import service as LS


class _FakeRegistry:
    def __init__(self):
        self.writes: list[dict] = []

    async def update_record_metadata(self, session_id, artifact_id, *, metadata):  # noqa: ANN001, ANN003
        self.writes.append({"session_id": session_id, "artifact_id": artifact_id,
                            "metadata": metadata})
        return True


@pytest.fixture()
def fake_registry(monkeypatch) -> _FakeRegistry:
    reg = _FakeRegistry()
    monkeypatch.setattr("app.services.artifact_registry.update_record_metadata",
                        reg.update_record_metadata)
    return reg


def _svc() -> LS.ArtifactLifecycleService:
    return LS.ArtifactLifecycleService()


@pytest.mark.asyncio
async def test_assign_role_writes_metadata(fake_registry) -> None:
    result = await _svc().assign_role("s1", "art-1", "boundary")
    assert result.ok is True
    assert fake_registry.writes[0]["metadata"]["logical_role"] == "boundary"


@pytest.mark.asyncio
async def test_assign_unknown_role_is_typed_rejection(fake_registry) -> None:
    result = await _svc().assign_role("s1", "art-1", "not-a-role")
    assert result.ok is False
    assert "unknown logical role" in result.reason
    assert fake_registry.writes == []


@pytest.mark.asyncio
async def test_set_persistence_maps_policy_to_tier(fake_registry) -> None:
    result = await _svc().set_persistence("s1", "art-2", "workspace_persistent")
    assert result.ok is True
    meta = fake_registry.writes[0]["metadata"]
    assert meta["materialization_policy"] == "workspace_persistent"
    assert meta["persistence_tier"]  # tier 由 policy 词表派生


@pytest.mark.asyncio
async def test_set_unknown_policy_is_typed_rejection(fake_registry) -> None:
    result = await _svc().set_persistence("s1", "art-2", "floppy-disk")
    assert result.ok is False
    assert "unknown materialization policy" in result.reason


@pytest.mark.asyncio
async def test_store_write_failure_surfaces_reason(fake_registry, monkeypatch) -> None:
    async def _deny(session_id, artifact_id, *, metadata):  # noqa: ANN001, ANN003
        return False

    monkeypatch.setattr("app.services.artifact_registry.update_record_metadata", _deny)
    result = await _svc().assign_role("s1", "ghost", "boundary")
    assert result.ok is False
    assert "store write failed" in result.reason
