"""ADR-0153 P1：MapSpec lifecycle pre-commit 质量门禁（集成）。

覆盖：blocking 拒绝（含一键 op 序列 correction_hint）/ warning 放行 +
quality_advisories 落元数据 / P7 profile 契约扩展落 source.profile /
settings 三态（enforce/advisory/off）/ 逃生舱 bypass 留审计事件 /
非破坏（拒绝后 session spec 无残留）。
"""
from __future__ import annotations

import shutil
import time
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


# ── #1262 review 回归：超帽绝不跑全量审计 / 未知模式绝不静默关闸 ────────────
def _fc_with_n_points(n: int) -> dict:
    return {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "properties": {"v": i},
             "geometry": {"type": "Point",
                          "coordinates": [116.0 + i * 0.01, 39.0 + i * 0.01]}}
            for i in range(n)
        ],
    }


@pytest.mark.cartography
def test_over_budget_returns_advisory_without_full_audit(monkeypatch):
    """超帽分支必须立即返回 advisory —— 绝不逐要素审计/剖析全量载荷。

    回归：修复前该分支设置 QUALITY_AUDIT_SKIPPED_OVER_BUDGET 后仍对全量
    payload 跑 audit_dataset + profile_numeric_fields —— 100k inline FC 在
    Backend CI 挂 ~14 分钟被 runner 杀掉（无 pytest summary）。这里用
    max_features=5 + 6 个小要素证明预算墙真的挡住了重活（永不本地造 100k）。
    """
    import app.services.spatial_quality_gate as gate_mod
    from app.services.spatial_quality_gate import evaluate_quality_gate
    from app.services.spatial_quality_service import SpatialQualityEngine

    max_features = 5
    data = _fc_with_n_points(max_features + 1)

    audit_calls: list = []
    real_audit = SpatialQualityEngine.audit_dataset

    def spy_audit(*args, **kwargs):
        audit_calls.append(args)
        return real_audit(*args, **kwargs)

    monkeypatch.setattr(SpatialQualityEngine, "audit_dataset", spy_audit)

    def forbidden_profiler(*args, **kwargs):
        raise AssertionError("profile_numeric_fields must not run on the over-budget path")

    monkeypatch.setattr(gate_mod, "profile_numeric_fields", forbidden_profiler)

    t0 = time.perf_counter()
    verdict = evaluate_quality_gate(data, max_features=max_features)
    elapsed = time.perf_counter() - t0

    # 1) advisory 在：verdict / 顶层 advisories / P7 契约三处一致。
    assert verdict["verdict"] == "warn"          # 未审计不得谎称 pass
    assert verdict["audit_truncated"] is True
    assert verdict["feature_count"] == max_features + 1
    codes = [a.get("code") for a in verdict["advisories"]]
    assert codes == ["QUALITY_AUDIT_SKIPPED_OVER_BUDGET"]
    assert verdict["profile_extension"]["quality_advisories"] == verdict["advisories"]
    # P7 契约六键仍在（下游 hook / 02/03 线零改动消费），CRS 如实标注未评估。
    for key in ("geometry_mix", "n_valid", "extent", "crs_confidence",
                "outlier_policy", "quality_advisories"):
        assert key in verdict["profile_extension"], f"missing P7 key {key}"
    assert verdict["profile_extension"]["crs_confidence"]["method"] == "skipped_over_budget"
    assert verdict["repair_plan"] is None
    # 2) 重审计没跑（哪怕对 ≤max_features 的采样也不跑 —— 早退语义）。
    assert audit_calls == []
    # 3) 及时返回（修复前这里会进入分钟级逐要素审计）。
    assert elapsed < 10.0


@pytest.mark.cartography
@pytest.mark.asyncio
async def test_over_budget_upsert_source_passes_with_advisory_no_audit(clean_session, monkeypatch):
    """hook 端到端：超帽载荷放行 + advisory 落 source 元数据，全量审计零调用。"""
    from app.services.spatial_quality_service import SpatialQualityEngine

    monkeypatch.setattr(settings, "MAP_QUALITY_GATE_MODE", "enforce")
    monkeypatch.setattr(settings, "MAP_QUALITY_GATE_MAX_FEATURES", 5)

    audit_calls: list = []
    real_audit = SpatialQualityEngine.audit_dataset

    def spy_audit(*args, **kwargs):
        audit_calls.append(args)
        return real_audit(*args, **kwargs)

    monkeypatch.setattr(SpatialQualityEngine, "audit_dataset", spy_audit)

    engine = MapSpecLifecycleEngine()
    await engine.apply_mutation(clean_session, InitProjectIntent())
    result = await engine.apply_mutation(
        clean_session,
        UpsertSourceIntent(source_id="s-big",
                           source={"type": "geojson", "inlineData": _fc_with_n_points(6)}),
    )
    assert result.is_error is False  # 超帽 = advisory 放行，不是 blocking
    spec = await engine.store.get_mapspec(clean_session)
    entry = spec["sources"]["s-big"]
    codes = [a.get("code") for a in (entry.get("quality_advisories") or [])]
    assert "QUALITY_AUDIT_SKIPPED_OVER_BUDGET" in codes
    profile = entry.get("profile") or {}
    assert profile.get("crs_confidence", {}).get("method") == "skipped_over_budget"
    assert audit_calls == []  # CI DoS 回归面：重审计一次都不跑


@pytest.mark.cartography
@pytest.mark.asyncio
async def test_invalid_gate_mode_falls_back_to_enforce_not_off(clean_session, monkeypatch):
    """拼写错误的模式绝不静默关闸（fail-closed）：按 enforce 兜底 + 留审计事件。"""
    import app.services.spatial_quality_gate as gate_mod

    monkeypatch.setattr(settings, "MAP_QUALITY_GATE_MODE", "enfroce")  # typo

    events: list = []
    real_record = gate_mod.record_gate_event

    def spy_record(event, **kwargs):
        events.append(event)
        return real_record(event, **kwargs)

    monkeypatch.setattr(gate_mod, "record_gate_event", spy_record)

    engine = MapSpecLifecycleEngine()
    await engine.apply_mutation(clean_session, InitProjectIntent())
    result = await engine.apply_mutation(
        clean_session, UpsertLayerIntent(layer=_layer(), source_data=_dirty_fc())
    )
    # typo ≠ off —— blocking 数据仍被拦（修复前这里静默放行）。
    assert result.is_error is True
    assert result.error_code == "quality_gate_blocked"
    assert "invalid_mode_fallback_enforce" in events
