"""Data Fabric materialization integrity tests (P0: truthful refs).

Invariant under test: *a ref exists IFF its payload is retrievable*.
Materialization must NEVER mint a fake ref or report success when the session
store failed (raised or returned the store-unavailability sentinel), and must
NOT persist an audit row for a non-retrievable ref.
"""
from unittest.mock import MagicMock

import pytest

from app.schemas.data_fabric_schema import QueryResult
from app.services.data_fabric import manager as df_manager
from app.services.data_fabric import materialization_service as mat_svc
from app.services.data_fabric.manager import DataFabricManager
from app.services.session_data_protocol import UNAVAILABLE_REF_PREFIX


def _qr(features=None) -> QueryResult:
    return QueryResult(
        dataset_id="ds1",
        features=features
        or [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [0.0, 0.0]},
                "properties": {"id": 1},
            }
        ],
        total_count=1,
    )


# ── Service layer ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_materialize_success_ref_is_retrievable(monkeypatch):
    """Case A: store success → success=True AND ref points at stored payload."""
    stored = {}

    async def fake_store(session_id, data, prefix="data"):
        ref = f"ref:{prefix}-real123"
        stored[ref] = data
        return ref

    async def fake_set_alias(session_id, ref_id, alias):
        return None

    monkeypatch.setattr(mat_svc.session_data_manager, "store", fake_store)
    monkeypatch.setattr(mat_svc.session_data_manager, "set_alias", fake_set_alias)

    res = await mat_svc.materialization_service.materialize("ds1", _qr(), session_id="s1")

    assert res["success"] is True
    assert res["status"] == "success"
    assert res["ref_id"] == "ref:data-fabric-real123"
    # invariant: the ref we returned resolves to a stored payload
    assert res["ref_id"] in stored


@pytest.mark.asyncio
async def test_materialize_store_exception_returns_no_fake_ref(monkeypatch):
    """Case B: store raises → success=False, ref_id=None, typed error."""

    async def boom(session_id, data, prefix="data"):
        raise RuntimeError("redis down")

    monkeypatch.setattr(mat_svc.session_data_manager, "store", boom)

    res = await mat_svc.materialization_service.materialize("ds1", _qr(), session_id="s1")

    assert res["success"] is False
    assert res["status"] == "failed"
    assert res["ref_id"] is None
    assert res["error_type"] == "MATERIALIZATION_FAILED"
    assert "redis down" in res["error"]


@pytest.mark.asyncio
async def test_materialize_store_unavailable_sentinel_returns_no_fake_ref(monkeypatch):
    """Case B': store returns the redis-unavailable sentinel → still a failure."""

    async def fake_store(session_id, data, prefix="data"):
        return f"{UNAVAILABLE_REF_PREFIX}abc"

    monkeypatch.setattr(mat_svc.session_data_manager, "store", fake_store)

    res = await mat_svc.materialization_service.materialize("ds1", _qr(), session_id="s1")

    assert res["success"] is False
    assert res["ref_id"] is None
    assert res["error_type"] == "MATERIALIZATION_FAILED"


@pytest.mark.asyncio
async def test_materialize_set_alias_failure_keeps_valid_ref(monkeypatch):
    """set_alias is best-effort: its failure must NOT invalidate a real ref."""

    async def fake_store(session_id, data, prefix="data"):
        return "ref:data-fabric-real789"

    async def alias_boom(session_id, ref_id, alias):
        raise RuntimeError("alias write failed")

    monkeypatch.setattr(mat_svc.session_data_manager, "store", fake_store)
    monkeypatch.setattr(mat_svc.session_data_manager, "set_alias", alias_boom)

    res = await mat_svc.materialization_service.materialize("ds1", _qr(), session_id="s1")

    assert res["success"] is True
    assert res["ref_id"] == "ref:data-fabric-real789"


# ── Manager layer (REST materialize path) ────────────────────────────────────


def _mock_db_with_item():
    item = MagicMock()
    item.id = "cat1"
    item.source_id = "src1"
    item.title = "T"
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = item
    return db, item


@pytest.mark.asyncio
async def test_manager_materialize_store_unavailable_writes_no_audit(monkeypatch):
    """Store-unavailable → success=False AND no audit row added/committed."""
    db, _item = _mock_db_with_item()
    monkeypatch.setattr(
        DataFabricManager,
        "query_catalog_item",
        classmethod(lambda cls, d, i, s: _qr()),
    )

    async def fake_store(session_id, data, prefix="data"):
        return f"{UNAVAILABLE_REF_PREFIX}xyz"

    monkeypatch.setattr(df_manager.session_data_manager, "store", fake_store)

    res = await DataFabricManager.materialize_catalog_item(db=db, session_id="s1", item_id="cat1")

    assert res["success"] is False
    assert res["ref_id"] is None
    assert res["error_type"] == "MATERIALIZATION_FAILED"
    db.add.assert_not_called()
    db.commit.assert_not_called()


@pytest.mark.asyncio
async def test_manager_materialize_audit_commit_failure_rolls_back(monkeypatch):
    """Audit commit failure after a successful store → success=False + rollback."""
    db, _item = _mock_db_with_item()
    db.commit.side_effect = RuntimeError("db gone")
    monkeypatch.setattr(
        DataFabricManager,
        "query_catalog_item",
        classmethod(lambda cls, d, i, s: _qr()),
    )

    async def fake_store(session_id, data, prefix="data"):
        return "ref:df-real456"

    monkeypatch.setattr(df_manager.session_data_manager, "store", fake_store)

    res = await DataFabricManager.materialize_catalog_item(db=db, session_id="s1", item_id="cat1")

    assert res["success"] is False
    assert res["ref_id"] is None
    assert res["error_type"] == "MATERIALIZATION_FAILED"
    db.rollback.assert_called_once()


@pytest.mark.asyncio
async def test_manager_materialize_success_commits_audit(monkeypatch):
    """Happy path → success=True, real ref, audit committed once."""
    db, _item = _mock_db_with_item()
    monkeypatch.setattr(
        DataFabricManager,
        "query_catalog_item",
        classmethod(lambda cls, d, i, s: _qr()),
    )

    async def fake_store(session_id, data, prefix="data"):
        return "ref:df-real789"

    monkeypatch.setattr(df_manager.session_data_manager, "store", fake_store)

    res = await DataFabricManager.materialize_catalog_item(db=db, session_id="s1", item_id="cat1")

    assert res["success"] is True
    assert res["ref_id"] == "ref:df-real789"
    db.add.assert_called_once()
    db.commit.assert_called_once()
