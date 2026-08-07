"""Unit tests for content-addressed artifact cache (app/lib/artifact_cache.py)."""
import os
import tempfile
import pytest

from app.lib.artifact_cache import (
    make_artifact_key,
    get_artifact,
    publish_artifact,
    clear_artifact_cache,
    _source_identity,
)


@pytest.fixture(autouse=True)
def clean_cache():
    clear_artifact_cache()
    yield
    clear_artifact_cache()


def test_source_identity():
    with tempfile.NamedTemporaryFile(delete=False) as f:
        f.write(b"test data 123")
        fname = f.name

    try:
        ident1 = _source_identity(fname)
        assert fname in ident1
        assert "13" in ident1  # size 13 bytes
    finally:
        os.unlink(fname)


def test_make_artifact_key_deterministic():
    key1 = make_artifact_key("file.tif", "resample", {"res": 10.0})
    key2 = make_artifact_key("file.tif", "resample", {"res": 10.0})
    key3 = make_artifact_key("file.tif", "resample", {"res": 20.0})

    assert key1 == key2
    assert key1 != key3
    assert len(key1) == 16


def test_publish_and_get_artifact():
    with tempfile.NamedTemporaryFile(suffix=".tif", delete=False) as src_f:
        src_f.write(b"SOURCE_RASTER_DATA")
        src_path = src_f.name

    key = make_artifact_key(src_path, "test_op", {"param": 1})
    assert get_artifact(key) is None

    def _compute():
        with tempfile.NamedTemporaryFile(suffix=".tif", delete=False) as out_f:
            out_f.write(b"PROCESSED_RASTER_OUTPUT")
            return out_f.name

    published_path = publish_artifact(key, src_path, _compute)
    assert os.path.exists(published_path)
    assert published_path != src_path

    # Second get should hit cache
    hit_path = get_artifact(key)
    assert hit_path == published_path

    # Cleanup temp source file
    os.unlink(src_path)
