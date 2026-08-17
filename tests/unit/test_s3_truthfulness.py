"""Regression test for issue #604 (S3 seam silently serving synthetic fixtures).

The S3 adapter fell back to SYNTHETIC_S3_FIXTURES (Beijing/county demo data)
whenever a configured real endpoint failed or returned no geospatial objects,
and describe()/preview() unconditionally used fixture size_bytes/bbox — so the
catalog registered fabricated metadata and callers saw "synced" demo data with
no synthetic label. This mirrors #430's truthfulness fix for the other adapters.

Contract after the fix: fixtures are served ONLY in explicit no-endpoint demo
mode (labeled ``source="synthetic-demo"``); with a real endpoint configured,
failure is honest — empty list / typed-error stub, never fixtures.
"""
from app.schemas.data_fabric_schema import ConnectionProfile, QuerySpec
from app.services.data_fabric.adapters.s3_storage_seam import (
    S3StorageSeam,
    SYNTHETIC_S3_FIXTURES,
)


def _adapter(endpoint):
    profile = ConnectionProfile(
        provider_type="s3",
        source_type="s3",
        endpoint=endpoint,
        credentials={"access_key": "k", "secret_key": "s"},
        options={"bucket": "geo-bucket", "region": "us-east-1"},
    )
    return S3StorageSeam(profile)


class _RaisingClient:
    """boto3 client whose calls fail like a bad credential / unreachable endpoint."""

    def list_objects_v2(self, **kwargs):
        raise ConnectionError("InvalidAccessKeyId: credential/signature error")

    def head_object(self, **kwargs):
        raise ConnectionError("InvalidAccessKeyId: credential/signature error")


class _EmptyBucketClient:
    def list_objects_v2(self, **kwargs):
        return {"Contents": []}


class _NoGeoClient:
    def list_objects_v2(self, **kwargs):
        return {"Contents": [{"Key": "README.txt", "Size": 10}]}


class _HeadClient:
    def head_object(self, **kwargs):
        return {"ContentLength": 1234, "ContentType": "application/octet-stream"}


def test_list_datasets_boto3_failure_returns_empty_not_fixtures():
    """#604 AC: real endpoint + credential error -> sync gets no fixture entries."""
    adapter = _adapter("http://minio.example.com:9000")
    adapter._s3_client = lambda: _RaisingClient()

    datasets = adapter.list_datasets()
    assert datasets == []
    fixture_ids = {item["s3_uri"] for item in SYNTHETIC_S3_FIXTURES.values()}
    assert not fixture_ids.intersection(d.get("id") for d in datasets)


def test_list_datasets_empty_bucket_returns_empty():
    adapter = _adapter("http://minio.example.com:9000")
    adapter._s3_client = lambda: _EmptyBucketClient()
    assert adapter.list_datasets() == []


def test_list_datasets_no_geospatial_objects_returns_empty():
    adapter = _adapter("http://minio.example.com:9000")
    adapter._s3_client = lambda: _NoGeoClient()
    assert adapter.list_datasets() == []


def test_list_datasets_real_objects_returned_without_fixture_label():
    class _GeoClient:
        def list_objects_v2(self, **kwargs):
            return {"Contents": [{"Key": "raster/ortho.tif", "Size": 5000}]}

    adapter = _adapter("http://minio.example.com:9000")
    adapter._s3_client = lambda: _GeoClient()
    datasets = adapter.list_datasets()
    assert [d["id"] for d in datasets] == ["s3://geo-bucket/raster/ortho.tif"]
    assert all(d.get("source") != "synthetic-demo" for d in datasets)


def test_describe_real_endpoint_failure_is_honest_stub():
    """#604: describe() must not return the Beijing fixture bbox/size on failure."""
    adapter = _adapter("http://minio.example.com:9000")
    adapter._s3_client = lambda: _RaisingClient()

    desc = adapter.describe("s3://geo-bucket/vectors/x.fgb")
    assert desc.metadata.get("error"), "typed error must be present"
    assert desc.metadata.get("error_type") == "ConnectionError"
    assert desc.feature_count is None  # no fabricated count
    assert "size_bytes" not in desc.metadata  # no fabricated size
    beijing_bboxes = [f["bbox"] for f in SYNTHETIC_S3_FIXTURES.values()]
    assert desc.bbox not in beijing_bboxes, "must not leak fixture bbox"


def test_describe_real_endpoint_success_uses_head_object():
    adapter = _adapter("http://minio.example.com:9000")
    adapter._s3_client = lambda: _HeadClient()

    desc = adapter.describe("s3://geo-bucket/vectors/x.fgb")
    assert desc.metadata["size_bytes"] == 1234
    beijing_bboxes = [f["bbox"] for f in SYNTHETIC_S3_FIXTURES.values()]
    assert desc.bbox not in beijing_bboxes, "no fabricated fixture bbox"


def test_preview_real_endpoint_failure_has_no_fabricated_lines():
    adapter = _adapter("http://minio.example.com:9000")
    adapter._s3_client = lambda: _RaisingClient()

    pv = adapter.preview("s3://geo-bucket/vectors/x.fgb", limit=5)
    assert pv["features"] == []
    assert pv.get("error"), "preview of a failed remote describe must surface the error"


def test_query_real_endpoint_failure_surfaces_error_and_remote_label():
    """#604: label must be 'remote' (truthful) with the error surfaced, not
    synthetic fixtures masquerading as remote bytes."""
    adapter = _adapter("http://minio.example.com:9000")
    adapter._s3_client = lambda: _RaisingClient()

    res = adapter.query("s3://geo-bucket/vectors/x.fgb", QuerySpec(limit=1))
    assert res.features == []
    assert res.metadata["source"] == "remote"
    assert res.metadata.get("error_type") == "ConnectionError"
    assert res.metadata.get("success") is False


def test_demo_mode_still_serves_fixtures_but_labels_everything_synthetic():
    """No-endpoint demo mode keeps working — every surface carries the label."""
    adapter = _adapter("s3://geo-data-bucket/remote_sensing/sentinel2_beijing.parquet")

    datasets = adapter.list_datasets()
    assert len(datasets) == 2
    assert all(d.get("source") == "synthetic-demo" for d in datasets)

    desc = adapter.describe("s3://geo-data-bucket/vectors/county_boundaries.fgb")
    assert desc.metadata.get("source") == "synthetic-demo"

    pv = adapter.preview("s3://geo-data-bucket/vectors/county_boundaries.fgb", limit=2)
    assert pv["properties"]["source"] == "synthetic-demo"

    res = adapter.query("s3://geo-data-bucket/vectors/county_boundaries.fgb", QuerySpec(limit=1))
    assert res.metadata["source"] == "synthetic-demo"