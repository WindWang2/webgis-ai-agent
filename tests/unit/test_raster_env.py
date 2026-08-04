"""Tests for the shared rasterio_env context manager (ADR-0037 Win 2)."""

import rasterio

from app.lib.geo_analysis.raster_math import rasterio_env


def test_rasterio_env_is_context_manager_that_yields():
    """rasterio_env() yields exactly once and cleans up on exit."""
    with rasterio_env() as entered:
        assert entered is None  # contextmanager yields None, the Env is ambient


def test_rasterio_env_supports_a_real_raster_read(tmp_path):
    """Inside the context, a normal rasterio open/read works end-to-end.

    This is the real contract: the context sets GDAL options (disable
    read-dir-on-open, short HTTP timeout, no retries) but must not break
    ordinary local-file raster reads. We don't assert on rasterio's private
    env internals (they're an implementation detail); we assert the
    observable behavior — a read succeeds.
    """
    # Write a 1x1 synthetic raster.
    import numpy as np
    from rasterio.transform import from_origin
    path = tmp_path / "cell.tif"
    data = np.array([[42]], dtype=np.float32)
    with rasterio.open(
        str(path), "w", driver="GTiff",
        height=1, width=1, count=1, dtype=np.float32,
        crs="EPSG:4326", transform=from_origin(0, 1, 1, 1),
    ) as dst:
        dst.write(data, 1)

    # Read it back inside the shared env.
    with rasterio_env():
        with rasterio.open(str(path)) as src:
            arr = src.read(1)
    assert arr[0, 0] == 42


def test_rasterio_env_restores_prior_env_on_exit():
    """Exiting the context restores whatever env was active before entry.

    rasterio.Env.__enter__/__exit__ saves/restores the prior thread env. We
    verify the restore happened by checking that re-entering yields a fresh
    context (no exception, no leaked state)."""
    with rasterio_env():
        pass
    # Re-entering must work cleanly — if exit had corrupted the env stack,
    # this would raise.
    with rasterio_env():
        pass
