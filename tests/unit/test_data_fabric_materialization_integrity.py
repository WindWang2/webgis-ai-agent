"""Data Fabric materialization integrity tests (P0: truthful refs).

Invariant under test: *a ref exists IFF its payload is retrievable*.
Materialization must NEVER mint a fake ref or report success when the session
store failed (raised or returned the store-unavailability sentinel), and must
NOT persist an audit row for a non-retrievable ref.
"""
from unittest.mock import MagicMock

import pytest

from app.schemas.data_fabric_schema import QueryResult, QuerySpec
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
    item.name = "ds1"
    item.source_id = "src1"
    item.title = "T"
    # data_source 提供真实字符串（V2 物化路径构造 ConnectionProfile 需要可校验值）
    ds = MagicMock()
    ds.id = "src1"
    ds.name = "src1"
    ds.source_type = "generic"
    ds.endpoint_url = "https://example.com/api"
    ds.connection_profile = {"options": {}, "allow_private": False}
    item.data_source = ds
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = item
    return db, item


class _FakeAdapter:
    """V2 单管线 seam：get_adapter 返回的假适配器（query → 固定 QueryResult）。"""

    def __init__(self, result=None, error=None):
        self._result = result if result is not None else _qr()
        self._error = error
        self.profile = MagicMock()
        self.profile.source_type = "generic"

    def query(self, dataset_id, spec):
        if self._error is not None:
            raise self._error
        return self._result


def _patch_pipeline(monkeypatch, adapter=None):
    """把 V2 物化管线固定为假适配器（绕过真实连接构造）。"""
    monkeypatch.setattr(
        DataFabricManager, "get_adapter", staticmethod(lambda profile: adapter or _FakeAdapter())
    )


@pytest.mark.asyncio
async def test_manager_materialize_store_unavailable_writes_no_audit(monkeypatch):
    """Store-unavailable → success=False AND no audit row added/committed."""
    db, _item = _mock_db_with_item()
    _patch_pipeline(monkeypatch)

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
    _patch_pipeline(monkeypatch)

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
    _patch_pipeline(monkeypatch)

    async def fake_store(session_id, data, prefix="data"):
        return "ref:df-real789"

    monkeypatch.setattr(df_manager.session_data_manager, "store", fake_store)

    res = await DataFabricManager.materialize_catalog_item(db=db, session_id="s1", item_id="cat1")

    assert res["success"] is True
    assert res["ref_id"] == "ref:df-real789"
    db.add.assert_called_once()
    db.commit.assert_called_once()


@pytest.mark.asyncio
async def test_materialize_respects_cancel_token_before_store(monkeypatch):
    """A pre-cancelled token must abort BEFORE the remote query/store — no stale
    materialization (Section 17)."""
    from app.services.jobs.cancellation import CancellationToken, OperationCancelled

    db, _item = _mock_db_with_item()
    token = CancellationToken(job_id="job1")
    token.cancel("user requested")

    async def no_store(*a, **k):  # pragma: no cover - must not be reached
        raise AssertionError("store must not run for a cancelled materialize")

    monkeypatch.setattr(df_manager.session_data_manager, "store", no_store)

    with pytest.raises(OperationCancelled):
        await DataFabricManager.materialize_catalog_item(
            db=db, session_id="s1", item_id="cat1", cancel_token=token
        )
    db.add.assert_not_called()
    db.commit.assert_not_called()


# ── #766: adapter fetch failures are never materialized as empty successes ───


class _FailingQueryAdapter:
    """Adapter double: query() returns the in-band error shape real network
    adapters emit on failure (empty features + error marker)."""

    calls = 0

    def query(self, dataset_id, query_spec):
        type(self).calls += 1
        return QueryResult(
            dataset_id=dataset_id,
            features=[],
            total_count=0,
            metadata={"error_type": "SOURCE_BAD_RESPONSE", "error": "HTTP 503"},
        )


@pytest.fixture
def _clean_breaker(monkeypatch):
    """Isolate the per-process breaker registry so failure-counting tests
    don't bleed into each other (#770)."""
    from app.services.data_fabric.circuit_breaker import (
        CircuitBreaker,
        CircuitBreakerRegistry,
        set_breaker_registry,
    )

    reg = CircuitBreakerRegistry(breaker=CircuitBreaker(failure_threshold=5, cool_down=30.0))
    set_breaker_registry(reg)
    yield reg
    set_breaker_registry(CircuitBreakerRegistry())


@pytest.mark.asyncio
async def test_manager_materialize_typed_source_failure_no_ref_no_audit(monkeypatch, _clean_breaker):
    """#766: a typed source failure from the remote query must surface as a
    success=False result — no ref minted, no audit row."""
    from app.services.data_fabric.errors import SourceBadResponseError

    db, _item = _mock_db_with_item()

    # V2 单管线：假适配器抛 typed error
    _patch_pipeline(monkeypatch, adapter=_FakeAdapter(error=SourceBadResponseError("HTTP 503")))

    res = await DataFabricManager.materialize_catalog_item(db=db, session_id="s1", item_id="cat1")

    assert res["success"] is False
    assert res["ref_id"] is None
    assert res["error_type"] == "SOURCE_BAD_RESPONSE"
    assert "503" in res["error"]
    db.add.assert_not_called()
    db.commit.assert_not_called()


def _db_with_item_and_source():
    """Mock DB whose query().first() yields an object usable as BOTH the
    catalog item and its parent data source (manager looks both up)."""
    from unittest.mock import MagicMock

    item = MagicMock()
    item.id = "cat1"
    item.name = "layer1"
    item.source_id = "src1"
    item.title = "T"
    item.data_source = None  # force the fallback DataSourceModel lookup
    item.source_type = "wfs"
    item.name_ds = None
    item.endpoint_url = "https://example.com/wfs"
    item.connection_profile = {"options": {}, "allow_private": False}
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = item
    return db, item


def test_query_catalog_item_raises_typed_on_in_band_error(monkeypatch, _clean_breaker):
    """#766: query_catalog_item converts the adapter's in-band error marker
    into a typed DataFabricError (fetch failed ≠ empty dataset)."""
    from app.services.data_fabric.errors import SourceBadResponseError

    db, _item = _db_with_item_and_source()
    monkeypatch.setattr(
        DataFabricManager, "get_adapter", staticmethod(lambda profile: _FailingQueryAdapter())
    )
    with pytest.raises(SourceBadResponseError):
        DataFabricManager.query_catalog_item(db, "cat1", QuerySpec(limit=5))


def test_query_catalog_item_records_failure_into_breaker(monkeypatch, _clean_breaker):
    """#766+#770: the typed in-band failure must feed the per-source breaker;
    after the threshold the next query fails fast without an adapter call."""
    from app.services.data_fabric.errors import SourceBadResponseError, SourceUnreachableError

    db, _item = _db_with_item_and_source()
    monkeypatch.setattr(
        DataFabricManager, "get_adapter", staticmethod(lambda profile: _FailingQueryAdapter())
    )
    for _ in range(5):
        with pytest.raises(SourceBadResponseError):
            DataFabricManager.query_catalog_item(db, "cat1", QuerySpec(limit=5))

    # Breaker is open: the 6th call fails fast and never reaches the adapter.
    before = _FailingQueryAdapter.calls
    with pytest.raises(SourceUnreachableError) as ei:
        DataFabricManager.query_catalog_item(db, "cat1", QuerySpec(limit=5))
    assert "circuit breaker" in str(ei.value)
    assert _FailingQueryAdapter.calls == before  # no HTTP attempt
