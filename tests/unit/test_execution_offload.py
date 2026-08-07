"""Regression tests: async tools must not block the event loop.

The registry offloads sync tools via asyncio.to_thread, but a few async tools
called blocking work directly (a 90s subprocess, pure-Python feature loops,
CPU-bound band math). Each test fakes the slow work with a sync time.sleep
and asserts the loop stays responsive *while the tool runs* — with the work
on the loop, the fake's sleep would block everything and the final
``assert not task.done()`` fails deterministically (the tool finishes before
the test's own timers resume).

Run cost: ~1s per test (0.8s fake sleep), no network, no heavy deps.
"""
import asyncio
import time

import numpy as np
import pytest

from app.services.mapspec_store import mapspec_store
from app.services.rs.spectral_engine import SpectralRasterEngine
from app.services.runtime_validator import RuntimeValidator


def _slow_fake(delay: float, value):
    """Sync stand-in for blocking work (subprocess.run / feature loop / numpy)."""
    def _fake(*args, **kwargs):
        time.sleep(delay)
        return value
    return _fake


def _async_fake(value):
    async def _fake(*args, **kwargs):
        return value
    return _fake


async def _assert_loop_responsive_while(awaitable_factory, delay: float = 0.8):
    """Run awaitable_factory() and assert a 0.05s timer fires mid-flight.

    Deterministic: with the work offloaded the task is still running when the
    timer completes; with the work on the loop the task finishes before the
    test's own sleep resumes, so ``assert not task.done()`` fails.
    """
    task = asyncio.create_task(awaitable_factory())
    await asyncio.sleep(0.15)          # let it enter the slow work
    assert not task.done(), "work finished before the test could observe it"

    ticks = []
    async def _tick():
        await asyncio.sleep(0.05)
        ticks.append(True)

    tick = asyncio.create_task(_tick())
    await asyncio.sleep(0.15)
    assert tick.done() and ticks, "event loop was blocked during tool execution"
    assert not task.done(), "event loop was blocked during tool execution"
    return await task


# ─── RuntimeValidator (90s Chromium subprocess) ──────────────────────────────


def _minimal_validator_report() -> dict:
    return {
        "mapLoaded": True, "mapIdle": True, "pageErrors": [], "consoleErrors": [],
        "failedRequests": [], "fatalError": None,
        "canvas": {"luminanceStdDev": 20.0, "dominantRatio": 0.5,
                   "transparentRatio": 0.1, "blank": False},
        "controls": {"overflow": [], "collisions": []},
    }


@pytest.mark.asyncio
async def test_validate_runtime_offloads_slow_subprocess(monkeypatch, tmp_path):
    """validate_runtime's subprocess.run must run in a thread, not on the loop."""
    validator = RuntimeValidator()
    monkeypatch.setattr(
        validator, "_run_headless_validator",
        _slow_fake(0.8, _minimal_validator_report()),
    )
    monkeypatch.setattr(mapspec_store, "get_mapspec",
                        _async_fake({"sources": {"s1": {}}, "layers": [{"id": "l1"}]}))
    monkeypatch.setattr(mapspec_store, "compile_mapspec_cli",
                        _async_fake({"success": True, "out_dir": str(tmp_path / "out")}))

    res = await _assert_loop_responsive_while(
        lambda: validator.validate_runtime("offload-test-session")
    )
    assert res["valid"] is True
    assert res["score"] > 0


# ─── MapSpecStore.source_profile (per-feature Python profiling) ──────────────


@pytest.mark.asyncio
async def test_source_profile_offloads_geojson_profiling(monkeypatch):
    """profile_geojson_source loops every feature — must run in a thread."""
    monkeypatch.setattr(mapspec_store, "get_mapspec", _async_fake({"sources": {}}))
    saved = []
    async def _fake_save(session_id, mapspec):
        saved.append(mapspec)
        return mapspec
    monkeypatch.setattr(mapspec_store, "save_mapspec", _fake_save)
    monkeypatch.setattr(
        "app.services.spatial_meta_profiler.profile_geojson_source",
        _slow_fake(0.8, {"feature_count": 1}),
    )

    profile = await _assert_loop_responsive_while(
        lambda: mapspec_store.source_profile("offload-test-session", "s1", {"type": "FeatureCollection", "features": []})
    )
    assert profile["feature_count"] == 1
    assert saved[0]["sources"]["s1"]["profile"]["feature_count"] == 1


# ─── SpectralRasterEngine (band algebra / terrain derivatives) ───────────────


@pytest.mark.asyncio
async def test_compute_index_offloads_band_math(monkeypatch):
    """compute_index_array runs numpy over full band arrays — must be threaded."""
    import sys
    spectral_engine_mod = sys.modules["app.services.rs.spectral_engine"]

    engine = SpectralRasterEngine()
    monkeypatch.setattr(
        engine.stac, "fetch_stac_items_and_bands",
        _async_fake({"bands": {"red": np.zeros((4, 4)), "nir": np.ones((4, 4))}}),
    )
    monkeypatch.setattr(spectral_engine_mod, "compute_index_array", _slow_fake(0.8, np.ones((4, 4))))

    res = await _assert_loop_responsive_while(
        lambda: engine.compute_index([0, 0, 1, 1], "2024-01-01", "2024-02-01", "ndvi")
    )
    assert res.is_error is False


@pytest.mark.asyncio
async def test_compute_terrain_offloads_derivatives(monkeypatch):
    """Horn-window slope/aspect/hillshade passes must run in a thread."""
    import sys
    spectral_engine_mod = sys.modules["app.services.rs.spectral_engine"]

    engine = SpectralRasterEngine()
    monkeypatch.setattr(
        engine.stac, "fetch_stac_items_and_bands",
        _async_fake({"bands": {"dem": np.zeros((4, 4))}, "cell_size_m": 30.0}),
    )
    monkeypatch.setattr(spectral_engine_mod, "compute_slope", _slow_fake(0.8, np.ones((4, 4))))

    res = await _assert_loop_responsive_while(
        lambda: engine.compute_terrain([0, 0, 1, 1], products=["slope"])
    )
    assert res.is_error is False
