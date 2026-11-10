"""独立评审 P1 修复回归（每条对应 REVIEW.md 的一个阻塞发现）。"""
from __future__ import annotations

import pytest
from rasterio.transform import from_origin

from app.schemas.raster_spec import RasterExpressionError
from app.services.raster_authoring import commit_expression, describe_raster, validate_expression
from app.services.raster_authoring.contract import resolve_expression
from app.services.raster_authoring.delivery import persist_snapshot_artifact
from app.services.raster_authoring.palettes import canonical_colormap, colormap_lut
from app.services.raster_authoring.service import (
    _read_mapspec, current_revision, export_snapshot, resolve_source,
)
from app.services.raster_authoring.source import _SOURCE_CACHE
from tests.unit.raster_authoring.helpers import register_raster_ref, write_tif


def _world(owner_root):
    import numpy as np

    rows, cols = np.mgrid[0:32, 0:64]
    data = np.stack([(b + 1) * 100 + rows * 2 + cols for b in range(4)]).astype("float32")
    return write_tif(
        owner_root / "world.tif", bands=4, width=64, height=32, values=data,
        transform=from_origin(-180.0, 90.0, 360.0 / 64, 180.0 / 32),
    )


@pytest.mark.asyncio
async def test_single_mutation_first_mount_creates_versioned_spec_and_source(
    owner_root, session_id, store_root
):
    """P1-3：一次用户提交 = 一笔 CAS/checkpoint/provenance；失败不残留半状态。"""
    ref = await register_raster_ref(session_id, _world(owner_root))
    receipt = await commit_expression(
        session_id, ref, "layer-a",
        {"bands": [4, 3, 2], "stretch": {"min": 0, "max": 500}},
        expected_revision=await current_revision(session_id),
    )
    assert receipt["mutations"] == 1
    doc = await _read_mapspec(session_id)
    assert doc["version"]  # 引擎 auto-init 骨架，不需要额外 InitProjectIntent
    assert doc["sources"][receipt["source_id"]]["type"] == "numeric_raster"
    assert doc["layers"][0]["raster_style"]["bands"] == [4, 3, 2]
    # 第二笔只改样式，仍然只写一笔
    again = await commit_expression(
        session_id, ref, "layer-a",
        {"bands": [1], "stretch": {"min": 0, "max": 500}, "colormap": "viridis"},
        expected_revision=receipt["mapspec_revision"],
    )
    assert again["mutations"] == 1


@pytest.mark.asyncio
async def test_concurrent_commit_rejection_writes_nothing(
    owner_root, session_id, store_root
):
    """CAS 失败必须真的「nothing was written」（单事务后这句为真）。"""
    ref = await register_raster_ref(session_id, _world(owner_root))
    first = await commit_expression(
        session_id, ref, "layer-a", {"bands": [1], "stretch": {"min": 0, "max": 500}},
        expected_revision=await current_revision(session_id),
    )
    before = await _read_mapspec(session_id)
    with pytest.raises(RasterExpressionError) as exc:
        await commit_expression(
            session_id, ref, "layer-a",
            {"bands": [1], "stretch": {"min": 0, "max": 500}, "colormap": "magma"},
            expected_revision=first["mapspec_revision"] + 99,
        )
    assert exc.value.code == "CAS_SUPERSEDED"
    assert await _read_mapspec(session_id) == before


@pytest.mark.asyncio
async def test_auto_stretch_is_approximate_and_pi_ui_identical(
    owner_root, session_id
):
    """P1-1：UI 默认（stretch='auto'）不能把降采样统计写成精确 manual。"""
    ref = await register_raster_ref(session_id, write_tif(owner_root / "four.tif", bands=4))
    source = await resolve_source(session_id, ref)
    for req in (
        {"bands": [4, 3, 2], "stretch": "auto"},
        {"stretch": "auto"},  # Pi/UI 都可用 defaults
    ):
        spec = resolve_expression(req, source)
        assert spec.stretch.source == "bounded_sample"
        assert spec.stretch.approximate is True
        assert spec.stretch.sample_count and spec.stretch.sample_count > 0
        assert spec.stretch.method == "minmax_decimated_2048"


@pytest.mark.asyncio
async def test_colormap_aliases_resolve_to_a_real_lut(owner_root, session_id):
    """P2-1：'grey' 等别名不得通过校验却在瓦片渲染期 502。"""
    assert canonical_colormap("grey") == "gray"
    assert colormap_lut(canonical_colormap("grey") or "", 8).shape == (8, 3)
    ref = await register_raster_ref(session_id, write_tif(owner_root / "one.tif"))
    preview = await validate_expression(
        session_id, ref,
        {"bands": [1], "stretch": {"min": 0, "max": 200}, "colormap": "grey"},
    )
    assert preview["expression"]["colormap"] == "gray"
    assert preview["legend"]["palette_colors"]  # 图例色样与像素 LUT 同源


@pytest.mark.asyncio
async def test_source_resolution_is_cached_and_band_bounded(
    owner_root, session_id, monkeypatch
):
    """P1-5：瓦片风暴不按瓦片重跑全幅统计读；高波段数有界。"""
    import app.services.raster_authoring.source as source_mod

    ref = await register_raster_ref(session_id, write_tif(owner_root / "one.tif"))
    _SOURCE_CACHE.clear()
    calls = {"n": 0}
    original = source_mod._inspect_bounded

    def counting(path):
        calls["n"] += 1
        return original(path)

    monkeypatch.setattr(source_mod, "_inspect_bounded", counting)
    first = await resolve_source(session_id, ref)
    second = await resolve_source(session_id, ref)
    third = await resolve_source(session_id, ref)
    assert calls["n"] == 1, "同一 (session, ref, 文件版本) 只解析一次"
    assert first.data_revision == second.data_revision == third.data_revision
    assert first.bands_truncated is False
    assert source_mod._DESCRIPTOR_MAX_BANDS <= 16
    assert source_mod.MAX_BAND_COUNT_METADATA == 64


@pytest.mark.asyncio
async def test_export_download_is_always_a_signed_capability(
    owner_root, session_id, store_root, tmp_path, monkeypatch
):
    """P1-4：登录用户的 window.open 不带 Bearer，必须签发能力 URL。"""
    import app.services.export_paths as ep

    root = tmp_path / "exports"
    root.mkdir()
    monkeypatch.setattr(ep, "exports_root", lambda: root, raising=False)
    monkeypatch.setattr(ep, "ensure_exports_root", lambda: root, raising=False)

    ref = await register_raster_ref(session_id, _world(owner_root))
    receipt = await commit_expression(
        session_id, ref, "layer-a", {"bands": [1], "stretch": {"min": 0, "max": 500}},
        expected_revision=await current_revision(session_id),
    )
    artifact = await export_snapshot(
        session_id, expected_revision=receipt["mapspec_revision"], fmt="png"
    )
    delivered = await persist_snapshot_artifact(
        session_id, artifact, principal="logged-in-user-42"
    )
    assert "exp=" in delivered["download_url"] and "sig=" in delivered["download_url"]
    assert (root / delivered["filename"]).exists()


@pytest.mark.asyncio
async def test_descriptor_discloses_approximate_and_truncation(owner_root, session_id):
    """P3-1：直方图截断披露基于原始尺寸，不再恒 False。"""
    ref = await register_raster_ref(session_id, write_tif(owner_root / "one.tif"))
    info = await describe_raster(session_id, ref)
    histogram = info["histograms"][0]
    assert histogram["approximate"] is True
    assert histogram["sample_truncation"]["requested"] == 8
    assert histogram["sample_truncation"]["used"] == 8
