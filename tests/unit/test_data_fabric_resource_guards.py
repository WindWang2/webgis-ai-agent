"""Resource-guard tests: oversized results raise ResultTooLargeError before
materialization (no OOM)."""
import pytest

from app.services.data_fabric.errors import ResultTooLargeError
from app.services.data_fabric import limits


def _feat(i):
    return {"type": "Feature", "geometry": {"type": "Point", "coordinates": [i, i]}, "properties": {"id": i}}


def test_features_over_count_limit_raises():
    feats = [_feat(i) for i in range(10)]
    with pytest.raises(ResultTooLargeError) as ei:
        limits.enforce_result_bounds(feats, max_feat=5, max_bytes=10 * 1024 * 1024)
    assert ei.value.details["feature_count"] == 10
    assert "hint" in ei.value.details


def test_features_over_byte_limit_raises():
    # few features but each huge → byte guard trips
    big = [{"type": "Feature", "properties": {"blob": "x" * 100_000}, "geometry": None}]
    with pytest.raises(ResultTooLargeError):
        limits.enforce_result_bounds(big, max_feat=100, max_bytes=1024)


def test_within_bounds_passes():
    feats = [_feat(i) for i in range(5)]
    # should not raise
    limits.enforce_result_bounds(feats, max_feat=100, max_bytes=10 * 1024 * 1024)


def test_empty_passes():
    limits.enforce_result_bounds([], max_feat=1, max_bytes=1)


def test_page_bound_raises_beyond_limit():
    limits.enforce_page_bound(0)  # ok
    with pytest.raises(ResultTooLargeError):
        # monkeypatch the page floor up so this is deterministic regardless of config
        limits.enforce_result_bounds([], page_count=10_000)


def test_config_floor_clamps_to_nonzero(monkeypatch):
    """An operator setting 0 cannot disable protection."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "DATA_FABRIC_MAX_FEATURES", 0, raising=False)
    assert limits.max_features() >= limits._MIN_MAX_FEATURES
    monkeypatch.setattr(settings, "DATA_FABRIC_MAX_RESPONSE_BYTES", 0, raising=False)
    assert limits.max_response_bytes() >= limits._MIN_MAX_RESPONSE_BYTES


async def test_materialize_service_rejects_oversized(monkeypatch):
    """The materialization choke point enforces the guard before store."""
    from app.schemas.data_fabric_schema import QueryResult
    from app.services.data_fabric import materialization_service as mat_svc

    # Force a tiny limit so we don't have to build 50k features.
    monkeypatch.setattr(limits, "max_features", lambda: 3)
    monkeypatch.setattr(limits, "max_response_bytes", lambda: 10 * 1024 * 1024)

    async def no_store(*a, **k):  # pragma: no cover - must not be reached
        raise AssertionError("store must not be called for an oversized result")

    monkeypatch.setattr(mat_svc.session_data_manager, "store", no_store)

    qr = QueryResult(dataset_id="ds", features=[_feat(i) for i in range(50)], total_count=50)
    with pytest.raises(ResultTooLargeError):
        await mat_svc.materialization_service.materialize("ds", qr, session_id="s")
