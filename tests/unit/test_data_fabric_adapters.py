"""
Unit Tests for All 10 Data Fabric Adapters Interface Contracts
(PostGIS, OGC API Features, WFS, WMS/WMTS, ArcGIS REST, STAC, GeoParquet, FlatGeobuf, PMTiles, S3)
"""
from app.schemas.data_fabric_schema import ConnectionProfile
from app.services.data_fabric.adapters import (
    PostGISAdapter,
    OGCApiFeaturesAdapter,
    WFSAdapter,
    WMSWMTSAdapter,
    ArcGISRESTAdapter,
    STACAdapter,
    GeoParquetAdapter,
    FlatGeobufAdapter,
    PMTilesAdapter,
    S3ObjectStorageSeam,
)


def test_postgis_adapter_interface():
    profile = ConnectionProfile(
        source_type="postgis",
        endpoint_url="postgresql://user:pass@localhost:5432/gisdb",
        name="test_postgis",
    )
    adapter = PostGISAdapter(profile)
    assert isinstance(adapter.capabilities(), list)
    desc = adapter.describe("public.cities")
    assert desc.id == "public.cities"


def test_ogc_api_adapter_interface():
    profile = ConnectionProfile(
        source_type="ogc_api",
        endpoint_url="https://demo.ldproxy.net/dusseldorf",
        name="test_ogc",
    )
    adapter = OGCApiFeaturesAdapter(profile)
    assert isinstance(adapter.capabilities(), list)


def test_wfs_adapter_interface():
    profile = ConnectionProfile(
        source_type="wfs",
        endpoint_url="https://demo.mapserver.org/cgi-bin/wfs",
        name="test_wfs",
    )
    adapter = WFSAdapter(profile)
    assert isinstance(adapter.capabilities(), list)


def test_wms_wmts_adapter_interface():
    profile = ConnectionProfile(
        source_type="wms",
        endpoint_url="https://demo.mapserver.org/cgi-bin/wms",
        name="test_wms",
    )
    adapter = WMSWMTSAdapter(profile)
    assert isinstance(adapter.capabilities(), list)


def test_arcgis_adapter_interface():
    profile = ConnectionProfile(
        source_type="arcgis",
        endpoint_url="https://services.arcgis.com/World_Cities/FeatureServer/0",
        name="test_arcgis",
    )
    adapter = ArcGISRESTAdapter(profile)
    assert isinstance(adapter.capabilities(), list)


def test_stac_adapter_interface():
    profile = ConnectionProfile(
        source_type="stac",
        endpoint_url="https://earth-search.aws.element84.com/v1",
        name="test_stac",
    )
    adapter = STACAdapter(profile)
    assert isinstance(adapter.capabilities(), list)


def test_geoparquet_adapter_interface():
    profile = ConnectionProfile(
        source_type="geoparquet",
        endpoint_url="s3://mybucket/data.parquet",
        name="test_parquet",
    )
    adapter = GeoParquetAdapter(profile)
    assert adapter.probe() is True
    desc = adapter.describe("data.parquet")
    assert desc.geometry_type == "MultiPolygon"


def test_flatgeobuf_adapter_interface():
    profile = ConnectionProfile(
        source_type="flatgeobuf",
        endpoint_url="https://flatgeobuf.org/test.fgb",
        name="test_fgb",
    )
    adapter = FlatGeobufAdapter(profile)
    assert adapter.probe() is True


def test_pmtiles_adapter_interface():
    profile = ConnectionProfile(
        source_type="pmtiles",
        endpoint_url="https://pmtiles.io/test.pmtiles",
        name="test_pmtiles",
    )
    adapter = PMTilesAdapter(profile)
    assert adapter.probe() is True
    desc = adapter.describe("pmtiles_layer")
    assert desc.feature_type == "tile"


def test_s3_storage_seam_interface():
    profile = ConnectionProfile(
        source_type="s3",
        endpoint_url="s3://spatial-bucket/layer.geojson",
        name="test_s3",
    )
    adapter = S3ObjectStorageSeam(profile)
    assert adapter.probe() is True
    health = adapter.health()
    assert health.reachable is True


def test_postgis_release_closes_conn_when_putconn_fails(caplog):
    """DATA-04: if pool.putconn() raises, the connection must be explicitly
    closed (so the pool slot is reclaimed) and the failure logged at WARNING,
    not silently swallowed.

    Previously _release_connection did `except Exception: pass`, leaving the
    connection neither returned to the pool nor deterministically closed, which
    drifts the pool's bookkeeping toward maxconn exhaustion.
    """
    from app.services.data_fabric.adapters import postgis_adapter as pga_mod

    class _FakeConn:
        def __init__(self):
            self.closed = False
            self.close_calls = 0

        def close(self):
            self.close_calls += 1
            self.closed = True

    class _FakePool:
        def __init__(self, conn):
            self._conn = conn
            self.putconn_calls = 0

        def getconn(self):
            return self._conn

        def putconn(self, conn):
            self.putconn_calls += 1
            raise RuntimeError("pool putconn simulated failure")

    fake_conn = _FakeConn()
    fake_pool = _FakePool(fake_conn)

    profile = ConnectionProfile(
        source_type="postgis",
        endpoint_url="postgresql://user:pass@localhost:5432/gisdb",
        name="test_postgis_pool",
    )
    adapter = PostGISAdapter(profile)

    # Force the adapter onto our fake pool for its (host,port,db,user) key.
    pool_key = f"{adapter.username}@{adapter.host}:{adapter.port or 5432}/{adapter.database}"
    pga_mod._POSTGIS_POOLS[pool_key] = fake_pool
    try:
        acquired = adapter._get_connection()
        assert acquired is fake_conn
        assert getattr(acquired, "_is_pooled", False) is True  # set by _get_connection

        import logging
        with caplog.at_level(logging.WARNING, logger=pga_mod.logger.name):
            adapter._release_connection(acquired)

        # putconn was attempted and raised; the conn must be explicitly closed.
        assert fake_pool.putconn_calls == 1
        assert fake_conn.close_calls == 1, "conn.close() must reclaim the slot on putconn failure"
        # The failure must be observable (not a silent pass).
        assert any(
            "putconn" in rec.message.lower() and rec.levelno == logging.WARNING
            for rec in caplog.records
        ), "putconn failure must be logged at WARNING"
    finally:
        pga_mod._POSTGIS_POOLS.pop(pool_key, None)


def test_postgis_release_puts_back_on_success():
    """DATA-04 (behavior-preservation): on the happy path the pooled connection
    is returned via putconn and NOT closed."""
    from app.services.data_fabric.adapters import postgis_adapter as pga_mod

    class _FakeConn:
        def __init__(self):
            self.close_calls = 0

        def close(self):
            self.close_calls += 1

    class _FakePool:
        def __init__(self, conn):
            self._conn = conn
            self.putconn_calls = 0

        def getconn(self):
            return self._conn

        def putconn(self, conn):
            self.putconn_calls += 1

    fake_conn = _FakeConn()
    fake_pool = _FakePool(fake_conn)

    profile = ConnectionProfile(
        source_type="postgis",
        endpoint_url="postgresql://user:pass@localhost:5432/gisdb",
        name="test_postgis_pool_ok",
    )
    adapter = PostGISAdapter(profile)
    pool_key = f"{adapter.username}@{adapter.host}:{adapter.port or 5432}/{adapter.database}"
    pga_mod._POSTGIS_POOLS[pool_key] = fake_pool
    try:
        acquired = adapter._get_connection()
        adapter._release_connection(acquired)
        assert fake_pool.putconn_calls == 1
        assert fake_conn.close_calls == 0, "happy-path pooled conn must not be closed"
    finally:
        pga_mod._POSTGIS_POOLS.pop(pool_key, None)
