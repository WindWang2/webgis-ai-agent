"""Remote raster reads must never fall back to native, unpinned DNS."""
import io

import numpy as np
import pytest
import rasterio
from rasterio.io import MemoryFile
from rasterio.transform import from_origin

from app.lib.geo_raster.env import open_raster
from app.lib.geo_raster.reader import RasterReader


def _tiff():
    with MemoryFile() as memory:
        with memory.open(driver="GTiff", width=32, height=32, count=1,
                         dtype="uint8", crs="EPSG:4326", tiled=True,
                         blockxsize=16, blockysize=16,
                         transform=from_origin(10, 20, 1, 1)) as writer:
            writer.write(np.full((1, 32, 32), 7, dtype="uint8"))
        return memory.read()


@pytest.mark.parametrize("prefix", ["", "/vsicurl/", "/vsicurl_streaming/"])
def test_http_reader_uses_python_opener_and_closes_it(monkeypatch, prefix):
    import fsspec.implementations.http as http
    import app.lib.geo_raster.env as env

    payload = _tiff()
    calls = []
    handles = []

    class FakeFilesystem:
        _session = None

        def __init__(self, **options):
            calls.append(options)

        def open(self, url, mode="rb"):
            assert url.startswith("https://example.com/")
            assert mode in ("r", "rb")
            handle = io.BytesIO(payload)
            handles.append(handle)
            return handle

    monkeypatch.setattr(http, "HTTPFileSystem", FakeFilesystem)
    monkeypatch.setattr(env, "validate_remote_href", lambda uri: uri)
    monkeypatch.setattr("app.lib.geo_raster.reader.validate_remote_href", lambda uri: uri)
    with RasterReader.open(prefix + "https://example.com/map.tif") as reader:
        assert reader.dataset.read(1)[0, 0] == 7
        assert not reader.closed
    assert reader.closed
    assert calls[0]["allow_redirects"] is False
    assert calls[0]["client_kwargs"]["allow_private"] is False
    assert calls[0]["client_kwargs"]["timeout"].total == 5
    assert all(handle.closed for handle in handles)


def test_remote_private_target_is_blocked_before_rasterio_open(monkeypatch):
    def unexpected(*args, **kwargs):
        raise AssertionError("native GDAL must not open a rejected target")

    monkeypatch.setattr(rasterio, "open", unexpected)
    with pytest.raises(ValueError, match="SSRF|private|blocked"):
        with open_raster("http://127.0.0.1/private.tif"):
            pytest.fail("private endpoint was accepted")


@pytest.mark.parametrize("uri", [
    "/vsizip//vsicurl/https://example.com/data.zip/map.tif",
    "zip+https://example.com/data.zip!map.tif",
])
def test_nested_remote_archive_never_falls_back_to_native_dns(monkeypatch, uri):
    monkeypatch.setattr("app.lib.geo_raster.env.validate_remote_href", lambda uri: uri)
    with pytest.raises(ValueError, match="compound VSI"):
        with open_raster(uri):
            pytest.fail("native compound path was accepted")


def test_local_reads_and_cog_checks_keep_dataset_open(tmp_path):
    path = tmp_path / "map.tif"
    path.write_bytes(_tiff())
    with rasterio.open(path, "r+") as dataset:
        dataset.build_overviews([2], rasterio.enums.Resampling.nearest)
    with open_raster(path) as dataset:
        assert dataset.read(1).shape == (32, 32)
    assert dataset.closed
    assert RasterReader.cog_structure_ok(str(path))
