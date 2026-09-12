"""ADR-0153 P1：MapSpec lifecycle pre-commit 质量门禁（集成）。

覆盖：blocking 拒绝（含一键 op 序列 correction_hint）/ warning 放行 +
quality_advisories 落元数据 / P7 profile 契约扩展落 source.profile /
settings 三态（enforce/advisory/off）/ 逃生舱 bypass 留审计事件 /
非破坏（拒绝后 session spec 无残留）。
"""
from __future__ import annotations

import shutil
import uuid

import pytest

from app.core.config import settings
from app.services.mapspec.lifecycle_engine import (
    InitProjectIntent,
    MapSpecLifecycleEngine,
    UpsertLayerIntent,
    UpsertSourceIntent,
)
from app.services.mapspec.store import BASE_STORAGE_DIR
from app.services.session_data import session_data_manager


@pytest.fixture
async def clean_session():
    sid = f"qgate-{uuid.uuid4().hex[:8]}"
    await session_data_manager.clear_session(sid)
    yield sid
    await session_data_manager.clear_session(sid)
    d = BASE_STORAGE_DIR / sid
    if d.exists():
        shutil.rmtree(d, ignore_errors=True)


def _bowtie_fc():
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {"v": 1},
                "geometry": {"type": "Polygon", "coordinates": [[
                    [116.0, 39.0], [118.0, 41.0], [118.0, 39.0], [116.0, 41.0], [116.0, 39.0],
                ]]},
            },
        ],
    }


def _dirty_fc():
    """blocking 数据：null geometry + 自交面。"""
    return {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "properties": {"v": 1}, "geometry": None},
            {
                "type": "Feature",
                "properties": {"v": 2},
                "geometry": {"type": "Polygon", "coordinates": [[
                    [116.0, 39.0], [118.0, 41.0], [118.0, 39.0], [116.0, 41.0], [116.0, 39.0],
                ]]},
            },
        ],
    }


def _clean_fc():
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {"v": i},
                "geometry": {"type": "Point", "coordinates": [116.0 + i * 0.1, 39.0 + i * 0.1]},
            }
            for i in range(3)
        ],
    }


def _layer(layer_id="L1", **extra):
    layer = {"id": layer_id, "source": f"s-{layer_id}", "type": "circle",
             "paint": {"circle-color": "#00f"}}
    layer.update(extra)
    return layer


@pytest.mark.cartography
@pytest.mark.asyncio
async def test_blocking_data_refused_with_repair_plan(clean_session, monkeypatch):
    monkeypatch.setattr(settings, "MAP_QUALITY_GATE_MODE", "enforce")
    engine = MapSpecLifecycleEngine()
    await engine.apply_mutation(clean_session, InitProjectIntent())
    result = await engine.apply_mutation(
        clean_session, UpsertLayerIntent(layer=_layer(), source_data=_dirty_fc())
    )
    assert result.is_error is True
    assert result.error_code == "quality_gate_blocked"
    # 可操作修复建议：op 序列出现在 correction_hint。
    assert "remove_empty" in result.correction_hint
    assert "make_valid" in result.correction_hint
    # 零残留：拒绝后 session 仍无 layers。
    spec = await engine.store.get_mapspec(clean_session)
    assert spec is None or not (spec.get("layers") or [])


@pytest.mark.cartography
@pytest.mark.asyncio
async def test_clean_data_passes_and_writes_profile_extension(clean_session, monkeypatch):
    monkeypatch.setattr(settings, "MAP_QUALITY_GATE_MODE", "enforce")
    engine = MapSpecLifecycleEngine()
    await engine.apply_mutation(clean_session, InitProjectIntent())
    result = await engine.apply_mutation(
        clean_session, UpsertLayerIntent(layer=_layer(), source_data=_clean_fc())
    )
    assert result.is_error is False
    spec = await engine.store.get_mapspec(clean_session)
    source_entry = spec["sources"][spec["layers"][0]["source"]]
    profile = source_entry.get("profile") or {}
    # P7 契约字段（evaluate 在本仓 profile 之上的扩展）。
    for key in ("geometry_mix", "n_valid", "extent", "crs_confidence",
                "outlier_policy", "quality_advisories"):
        assert key in profile, f"profile missing P7 contract key {key}"
    assert profile["geometry_mix"]["dominant"] == "Point"


@pytest.mark.cartography
@pytest.mark.asyncio
async def test_advisory_mode_downgrades_block_to_passthrough(clean_session, monkeypatch):
    monkeypatch.setattr(settings, "MAP_QUALITY_GATE_MODE", "advisory")
    engine = MapSpecLifecycleEngine()
    await engine.apply_mutation(clean_session, InitProjectIntent())
    result = await engine.apply_mutation(
        clean_session, UpsertLayerIntent(layer=_layer(), source_data=_dirty_fc())
    )
    assert result.is_error is False  # advisory 模式放行
    spec = await engine.store.get_mapspec(clean_session)
    layer = spec["layers"][0]
    # 放行但降级事实必须写进 layer metadata。
    advisories = layer.get("quality_advisories") or []
    assert any(a.get("code") == "QUALITY_GATE_BLOCK_DOWNGRADED" for a in advisories)


@pytest.mark.cartography
@pytest.mark.asyncio
async def test_off_mode_disables_gate_entirely(clean_session, monkeypatch):
    monkeypatch.setattr(settings, "MAP_QUALITY_GATE_MODE", "off")
    engine = MapSpecLifecycleEngine()
    await engine.apply_mutation(clean_session, InitProjectIntent())
    result = await engine.apply_mutation(
        clean_session, UpsertLayerIntent(layer=_layer(), source_data=_dirty_fc())
    )
    assert result.is_error is False
    spec = await engine.store.get_mapspec(clean_session)
    layer = spec["layers"][0]
    assert "quality_advisories" not in layer  # off = 完全无门禁痕迹（回滚面）


@pytest.mark.cartography
@pytest.mark.asyncio
async def test_bypass_escape_hatch_records_audit_event(clean_session, monkeypatch):
    monkeypatch.setattr(settings, "MAP_QUALITY_GATE_MODE", "enforce")
    engine = MapSpecLifecycleEngine()
    await engine.apply_mutation(clean_session, InitProjectIntent())
    result = await engine.apply_mutation(
        clean_session,
        UpsertLayerIntent(layer=_layer(quality_gate_bypass=True), source_data=_dirty_fc()),
    )
    assert result.is_error is False  # bypass 逃生舱放行
    spec = await engine.store.get_mapspec(clean_session)
    layer = spec["layers"][0]
    advisories = layer.get("quality_advisories") or []
    assert any(a.get("code") == "QUALITY_GATE_BLOCK_DOWNGRADED" for a in advisories)
    assert layer.get("quality_gate_bypass") is True  # 留痕在持久 spec


@pytest.mark.cartography
@pytest.mark.asyncio
async def test_upsert_source_intent_gate(clean_session, monkeypatch):
    monkeypatch.setattr(settings, "MAP_QUALITY_GATE_MODE", "enforce")
    engine = MapSpecLifecycleEngine()
    await engine.apply_mutation(clean_session, InitProjectIntent())
    result = await engine.apply_mutation(
        clean_session,
        UpsertSourceIntent(source_id="s-dirty", source={"type": "geojson", "inlineData": _dirty_fc()}),
    )
    assert result.is_error is True
    assert result.error_code == "quality_gate_blocked"

    result_ok = await engine.apply_mutation(
        clean_session,
        UpsertSourceIntent(source_id="s-clean", source={"type": "geojson", "inlineData": _clean_fc()}),
    )
    assert result_ok.is_error is False
    spec = await engine.store.get_mapspec(clean_session)
    clean_entry = spec["sources"]["s-clean"]
    # 放行路径：P7 profile 契约扩展必须落 source.profile；干净数据无 gate 拦截痕迹。
    profile = clean_entry.get("profile") or {}
    assert "crs_confidence" in profile
    assert "quality_advisories" in profile


@pytest.mark.cartography
@pytest.mark.asyncio
async def test_warning_data_passes_with_advisories(clean_session, monkeypatch):
    monkeypatch.setattr(settings, "MAP_QUALITY_GATE_MODE", "enforce")
    # warning 级：Null Island 点（warning，非 blocking）→ 放行 + advisory。
    warn_fc = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "properties": {},
             "geometry": {"type": "Point", "coordinates": [0.0, 0.0]}},
        ],
    }
    engine = MapSpecLifecycleEngine()
    await engine.apply_mutation(clean_session, InitProjectIntent())
    result = await engine.apply_mutation(
        clean_session, UpsertLayerIntent(layer=_layer(), source_data=warn_fc)
    )
    assert result.is_error is False
    spec = await engine.store.get_mapspec(clean_session)
    layer = spec["layers"][0]
    codes = {a.get("code") for a in (layer.get("quality_advisories") or [])}
    assert "NULL_ISLAND" in codes
