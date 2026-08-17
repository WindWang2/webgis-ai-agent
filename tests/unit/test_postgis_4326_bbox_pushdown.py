"""Regression test for issue #603 (PostGIS bbox pushdown dead branch).

The old guard ``col_srid not in (-1, 4326)`` excluded the most common case —
EPSG:4326 tables — from bbox pushdown entirely, leaving the inner 4326
envelope branch unreachable dead code: such queries degraded to a full-table
``LIMIT/OFFSET`` scan with no spatial filter, while metadata still claimed
``pushdown_bbox=True`` (it only inspected the request, not reality).

Fix: guard on ``col_srid != -1`` so 4326 tables push down the envelope directly
(no transform needed), and metadata reports whether a spatial predicate was
actually generated.
"""
from app.services.data_fabric.adapters.postgis_adapter import PostGISAdapter


def _adapter_with_srid(srid, executed):
    """PostGISAdapter whose geometry_columns row reports the given SRID.

    Mirrors the fake-connection pattern in tests/test_gis_audit_fixes.py.
    """
    adapter = PostGISAdapter.__new__(PostGISAdapter)

    class _Cur:
        def execute(self, sql, params=()):
            executed.append((sql, params))

        def fetchone(self):
            return ("geom", srid)

        def fetchall(self):
            return []

        @property
        def description(self):
            return []

    class _ConnCtx:
        def __enter__(self):
            class _C:
                def cursor(self):
                    class _CursorCtx:
                        def __enter__(self):
                            return _Cur()

                        def __exit__(self, *a):
                            return None

                    return _CursorCtx()

            return _C()

        def __exit__(self, *a):
            return None

    adapter._connection_context = _ConnCtx
    return adapter


class _Spec:
    def __init__(self, bbox=None, limit=10, offset=0, where=None):
        self.bbox = bbox
        self.limit = limit
        self.offset = offset
        self.where = where


def test_4326_table_bbox_pushdown_uses_envelope_without_transform():
    """#603: the most common case — EPSG:4326 — must get a real spatial predicate."""
    executed: list[tuple[str, tuple]] = []
    adapter = _adapter_with_srid(4326, executed)

    res = adapter.query("public.roads", _Spec(bbox=[116.0, 39.0, 117.0, 40.0]))

    env_sql = [sql for sql, _ in executed if "ST_MakeEnvelope" in sql]
    assert env_sql, "4326 table with bbox must push down ST_MakeEnvelope"
    assert "ST_Transform(ST_MakeEnvelope" not in env_sql[0], (
        "4326 needs NO transform — envelope is already WGS84"
    )
    assert "4326" in env_sql[0], "envelope must be declared in EPSG:4326"
    assert any("ST_Intersects" in sql for sql, _ in executed)
    # Metadata must reflect what actually happened.
    assert res.metadata["pushdown_bbox"] is True


def test_unknown_srid_table_skips_pushdown_and_reports_honestly():
    """#603: unknown SRID cannot push down — report it honestly.

    geometry_columns returning the integer 0 is read-time-normalized to 4326
    (native-coordinate assumption); the unknown sentinel -1 is reached via the
    P2-1 path (truthy "0" → col_srid == 0 → -1). An unknown SRID has no source
    CRS to express an envelope in, so the bbox must NOT be pushed down and the
    metadata must not claim it was.
    """
    executed: list[tuple[str, tuple]] = []
    adapter = _adapter_with_srid("0", executed)

    res = adapter.query("public.roads_unknown", _Spec(bbox=[116.0, 39.0, 117.0, 40.0]))

    assert not [sql for sql, _ in executed if "ST_MakeEnvelope" in sql], (
        "unknown SRID must not emit an envelope predicate"
    )
    # Metadata must NOT claim a pushdown that never happened.
    assert res.metadata["pushdown_bbox"] is False


def test_projected_table_pushdown_still_transforms_envelope():
    """#603: the projected-SRID branch (already covered by test_gis_audit_fixes)
    must keep transforming the envelope into the column SRID."""
    executed: list[tuple[str, tuple]] = []
    adapter = _adapter_with_srid(3857, executed)

    res = adapter.query("public.roads_3857", _Spec(bbox=[116.0, 39.0, 117.0, 40.0]))

    env_sql = [sql for sql, _ in executed if "ST_MakeEnvelope" in sql]
    assert env_sql
    assert "ST_Transform(ST_MakeEnvelope" in env_sql[0], (
        "projected table must transform the 4326 envelope into the column SRID"
    )
    assert res.metadata["pushdown_bbox"] is True