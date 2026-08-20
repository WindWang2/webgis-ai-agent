"""Live-session raster rewrite (#696) — pure unit tests, no browser.

Verifies the compile-caller-side rewrite ``ref:raster/<id>`` → ``__ORIGIN__/raster/<id>.png``
and the PNG copy into ``dist/raster/`` that lets the headless static server fetch.

All tests are pure (filesystem + dict manipulation), suitable for ``-m "not heavy"``.
Heavy browser validation of the rewritten URL is covered by the live
``validate_runtime`` path but not executed here (two parallel agents share Redis).
"""
from __future__ import annotations

from pathlib import Path


def _sample_mapspec_with_raster() -> dict:
    return {
        "version": "1.0",
        "sources": {
            "ndvi": {
                "type": "raster",
                "imageRef": "ref:raster/ndvi_src",
                "bounds": [10, 10, 20, 20],
            },
            "geo": {
                "type": "geojson",
                "inlineData": {"type": "FeatureCollection", "features": []},
            },
        },
        "layers": [
            {"id": "raster-lyr", "source": "ndvi", "type": "raster"},
            {"id": "geo-lyr", "source": "geo", "type": "fill"},
        ],
    }


# ── pure rewrite mapping ──────────────────────────────────────────────

def test_rewrite_mapspec_raster_refs_basic():
    from app.services.runtime_asset_assembly import rewrite_mapspec_raster_refs

    mapspec = _sample_mapspec_with_raster()
    new_spec, count = rewrite_mapspec_raster_refs(mapspec)
    assert count == 1
    assert new_spec["sources"]["ndvi"]["imageRef"] == "__ORIGIN__/raster/ndvi_src.png"
    # geojson untouched
    assert new_spec["sources"]["geo"] == mapspec["sources"]["geo"]
    # input not mutated
    assert mapspec["sources"]["ndvi"]["imageRef"] == "ref:raster/ndvi_src"


def test_rewrite_leaves_non_raster_untouched():
    from app.services.runtime_asset_assembly import rewrite_mapspec_raster_refs

    mapspec = {
        "version": "1.0",
        "sources": {
            "a": {"type": "geojson", "inlineData": {"type": "FeatureCollection", "features": []}},
        },
        "layers": [],
    }
    new_spec, count = rewrite_mapspec_raster_refs(mapspec)
    assert count == 0
    assert new_spec is mapspec  # no copy when unchanged


def test_rewrite_already_origin_untouched():
    from app.services.runtime_asset_assembly import rewrite_mapspec_raster_refs

    mapspec = {
        "version": "1.0",
        "sources": {
            "r": {"type": "raster", "imageRef": "__ORIGIN__/raster/already.png", "bounds": [0, 0, 1, 1]},
        },
        "layers": [],
    }
    _, count = rewrite_mapspec_raster_refs(mapspec)
    assert count == 0


def test_rewrite_rejects_traversal_id():
    from app.services.runtime_asset_assembly import rewrite_mapspec_raster_refs

    mapspec = {
        "version": "1.0",
        "sources": {
            "r": {"type": "raster", "imageRef": "ref:raster/../evil", "bounds": [0, 0, 1, 1]},
        },
        "layers": [],
    }
    _, count = rewrite_mapspec_raster_refs(mapspec)
    assert count == 0


def test_raster_ids_from_mapspec():
    from app.services.runtime_asset_assembly import raster_ids_from_mapspec

    mapspec = _sample_mapspec_with_raster()
    ids = raster_ids_from_mapspec(mapspec)
    assert ids == ["ndvi_src"]
    # rewritten form also recognised
    rewritten, _ = __import__("app.services.runtime_asset_assembly", fromlist=["rewrite_mapspec_raster_refs"]).rewrite_mapspec_raster_refs(mapspec)
    ids2 = raster_ids_from_mapspec(rewritten)
    assert ids2 == ["ndvi_src"]


# ── PNG copy into dist/raster/ ────────────────────────────────────────

def test_copy_session_raster_assets_copies_png(tmp_path: Path):
    from app.services.runtime_asset_assembly import copy_session_raster_assets

    session_dir = tmp_path / "session"
    raster_dir = session_dir / "raster"
    raster_dir.mkdir(parents=True)
    # Minimal valid PNG (1x1)
    png_bytes = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    (raster_dir / "ndvi_src.png").write_bytes(png_bytes)
    # Add unrelated file that should not be copied when mapspec is provided
    (raster_dir / "other.png").write_bytes(png_bytes)

    mapspec = _sample_mapspec_with_raster()
    dist = tmp_path / "dist"
    dist.mkdir()

    copied = copy_session_raster_assets(session_dir, dist, mapspec)
    assert copied == 1
    assert (dist / "raster" / "ndvi_src.png").exists()
    assert (dist / "raster" / "ndvi_src.png").read_bytes() == png_bytes
    assert not (dist / "raster" / "other.png").exists()


def test_copy_session_raster_assets_missing_dir_returns_zero(tmp_path: Path):
    from app.services.runtime_asset_assembly import copy_session_raster_assets

    session_dir = tmp_path / "nope"
    dist = tmp_path / "dist2"
    dist.mkdir()
    assert copy_session_raster_assets(session_dir, dist, _sample_mapspec_with_raster()) == 0


def test_copy_session_raster_assets_missing_png_skipped(tmp_path: Path):
    from app.services.runtime_asset_assembly import copy_session_raster_assets

    session_dir = tmp_path / "sess2"
    (session_dir / "raster").mkdir(parents=True)
    # No PNG written
    dist = tmp_path / "dist3"
    dist.mkdir()
    assert copy_session_raster_assets(session_dir, dist, _sample_mapspec_with_raster()) == 0
    assert not (dist / "raster").exists()


# ── end-to-end: rewrite + copy + compiled style contains __ORIGIN__ ────

def test_e2e_rewrite_and_copy_produces_origin_url_and_dist_file(tmp_path: Path):
    """Simulate the validate_runtime pre-compile + copy steps without a browser.

    - session raster dir contains PNG
    - mapspec rewritten → style would contain __ORIGIN__/raster/...
    - dist/raster/*.png is present so static server would serve 200 (not 404)
    """
    from app.services.runtime_asset_assembly import copy_session_raster_assets, rewrite_mapspec_raster_refs

    # Simulate session dir
    session_dir = tmp_path / ".webgis-agent" / "test-sess"
    raster_dir = session_dir / "raster"
    raster_dir.mkdir(parents=True)
    png_bytes = b"\x89PNG\r\n\x1a\nfake-png-bytes"
    (raster_dir / "ndvi_src.png").write_bytes(png_bytes)

    mapspec = _sample_mapspec_with_raster()
    rewritten, count = rewrite_mapspec_raster_refs(mapspec)
    assert count == 1
    # Compile step would emit style with rewritten URL; verify the rewritten mapspec carries it
    assert rewritten["sources"]["ndvi"]["imageRef"] == "__ORIGIN__/raster/ndvi_src.png"

    # Simulate dist after compile
    dist = tmp_path / "compiled"
    dist.mkdir()
    # Copy assets like validate_runtime does after compile
    copied = copy_session_raster_assets(session_dir, dist, rewritten)
    assert copied == 1
    assert (dist / "raster" / "ndvi_src.png").read_bytes() == png_bytes

    # Verify the URL that would be in style.json is fetchable relative to dist
    # (static server serves dist/; __ORIGIN__/raster/<id>.png → dist/raster/<id>.png)
    # Simulate what html-template does: replace __ORIGIN__ with origin, then fetch /raster/...
    style_url = rewritten["sources"]["ndvi"]["imageRef"].replace("__ORIGIN__", "")
    # should be /raster/ndvi_src.png which resolves under dist
    assert style_url == "/raster/ndvi_src.png"
    assert (dist / style_url.lstrip("/")).exists()


def test_e2e_mixed_geojson_and_raster_only_raster_rewritten(tmp_path: Path):
    from app.services.runtime_asset_assembly import rewrite_mapspec_raster_refs

    mapspec = {
        "version": "1.0",
        "sources": {
            "r1": {"type": "raster", "imageRef": "ref:raster/a1", "bounds": [0, 0, 1, 1]},
            "r2": {"type": "raster", "imageRef": "ref:raster/b2", "bounds": [0, 0, 1, 1]},
            "g": {"type": "geojson", "inlineData": {"type": "FeatureCollection", "features": []}},
        },
        "layers": [
            {"id": "l1", "source": "r1", "type": "raster"},
            {"id": "l2", "source": "r2", "type": "raster"},
        ],
    }
    new_spec, count = rewrite_mapspec_raster_refs(mapspec)
    assert count == 2
    assert new_spec["sources"]["r1"]["imageRef"] == "__ORIGIN__/raster/a1.png"
    assert new_spec["sources"]["r2"]["imageRef"] == "__ORIGIN__/raster/b2.png"
