"""Harness V5 Progressive DatasetProfile 契约（ADR-0118 D4）。

验收锚点（Epic V5）：
- cheap → deep 渐进加深：显式入口、cheap provenance 保留、deep 失败
  回退 cheap（不丢浅事实）；
- 修订绑定失效：content_revision 变化 → 旧缓存不命中；
- 经度约定/AM 语义事实：deep 有真实证据、shallow 诚实 absent；
- profile 证据进 planner（resolver 词表 additive：longitudeConvention /
  crossesAntimeridian 仅在场发射）。
"""
from __future__ import annotations

import pytest

from app.services.data_profile.profiler import DatasetProfiler


def _fc(n=12, lon_rng=(116.0, 117.0), lat_rng=(39.0, 40.0)):
    feats = []
    for i in range(n):
        lon = lon_rng[0] + (lon_rng[1] - lon_rng[0]) * i / max(1, n - 1)
        lat = lat_rng[0] + (lat_rng[1] - lat_rng[0]) * i / max(1, n - 1)
        feats.append({
            "type": "Feature",
            "properties": {"value": float(i)},
            "geometry": {"type": "Point", "coordinates": [lon, lat]},
        })
    return {"type": "FeatureCollection", "features": feats}


@pytest.fixture()
async def session_with_ref():
    from app.services.session_data import session_data_manager

    sid = "v5-profile-session"
    await session_data_manager.clear_session(sid)
    ref = await session_data_manager.store(sid, _fc(20))
    assert ref, "session store fixture 失败"
    yield sid, ref
    await session_data_manager.clear_session(sid)


async def test_shallow_then_deepen_progressive(session_with_ref):
    """cheap → deepen：diagnostics 标注 progressive_deepened，deep 事实更富。"""
    sid, ref = session_with_ref
    profiler = DatasetProfiler()

    cheap = await profiler.profile_session_ref(sid, ref, deep=False)
    assert cheap is not None
    assert cheap.profile_quality.value == "partial"

    deep = await profiler.deepen_profile(sid, ref)
    assert deep is not None
    assert "progressive_deepened" in deep.diagnostics
    assert deep.profile_quality.value in ("complete", "sampled")
    assert deep.vector is not None and deep.vector.scanned_rows > 0


async def test_deepen_falls_back_to_cheap_on_failure(session_with_ref, monkeypatch):
    """deep 失败 → 回退既有 cheap 剖析（浅事实不丢）。"""
    sid, ref = session_with_ref
    profiler = DatasetProfiler()
    cheap = await profiler.profile_session_ref(sid, ref, deep=False)
    assert cheap is not None

    async def _boom(*_a, **_k):
        return None

    monkeypatch.setattr(
        DatasetProfiler, "profile_session_ref",
        staticmethod(_boom))
    got = await profiler.deepen_profile(sid, ref)
    assert got is not None
    assert got.profile_quality.value == "partial"


async def test_profile_longitude_facts(session_with_ref):
    """deep 剖析携带真实经度证据；shallow 为 absent（不虚构）。"""
    sid, ref = session_with_ref
    profiler = DatasetProfiler()
    cheap = await profiler.profile_session_ref(sid, ref, deep=False)
    assert cheap.longitude_facts is None or \
        cheap.longitude_facts.get("evidence") == "absent"

    deep = await profiler.deepen_profile(sid, ref)
    lf = deep.longitude_facts
    assert lf is not None
    assert lf["evidence"] in ("bbox", "geometry", "bbox+geometry")
    assert lf["convention"] in ("pm180", "e360", "ambiguous")
    assert isinstance(lf["crosses_antimeridian"], bool)


async def test_revision_rebind_invalidates_cache(session_with_ref):
    """content_revision 变化 → 旧缓存键失效（重新剖析）。"""
    from app.services.session_data import session_data_manager

    sid, ref = session_with_ref
    profiler = DatasetProfiler()
    p1 = await profiler.profile_session_ref(sid, ref, deep=False)
    base_rev = p1.source_revision  # 初始 revision 由 store 侧决定（≥0）

    # 覆写同一 ref（content_revision +1 —— ref 不可变，覆写走 overwrite 通道）
    ok = await session_data_manager.overwrite(
        sid, ref, _fc(30, lon_rng=(10.0, 11.0)))
    assert ok is True
    desc = await session_data_manager.get_ref_descriptor(sid, ref)
    rev = int(desc.get("content_revision") or 0)
    p2 = await profiler.profile_session_ref(sid, ref, deep=False)
    assert p2.source_revision == rev, "修订绑定失效失败（旧缓存被复用）"
    assert rev > base_rev, "overwrite 未推进 content_revision"


def test_resolver_profile_emits_longitude_keys_only_with_evidence():
    """resolver 词表 additive：经度键仅真实证据在场时发射。"""
    from app.lib.gis.dataset_profile import DatasetProfile

    absent = DatasetProfile(source="spatial_profile")
    rp = absent.to_resolver_profile()
    assert "longitudeConvention" not in rp
    assert "crossesAntimeridian" not in rp

    present = DatasetProfile(
        source="spatial_profile",
        longitude_facts={"convention": "e360",
                         "crosses_antimeridian": True,
                         "normalization_required": True,
                         "evidence": "bbox"},
    )
    rp2 = present.to_resolver_profile()
    assert rp2["longitudeConvention"] == "e360"
    assert rp2["crossesAntimeridian"] is True


def test_from_profile_v3_passes_longitude_facts():
    from app.lib.data.profile import DatasetProfileV3
    from app.lib.gis.dataset_profile import DatasetProfile

    v3 = DatasetProfileV3(
        target_ref="ref:x",
        longitude_facts={"convention": "pm180",
                         "crosses_antimeridian": False,
                         "evidence": "bbox"},
    )
    dp = DatasetProfile.from_profile_v3(v3)
    assert dp.longitude_facts == v3.longitude_facts
    rp = dp.to_resolver_profile()
    assert rp["longitudeConvention"] == "pm180"
    assert rp["crossesAntimeridian"] is False


def test_planner_evidence_consumes_longitude_facts():
    """planner fact bundle 透传经度证据（2c 规则）。"""
    from app.services.gis_harness.planner import _interpolation_fact_signals

    out = _interpolation_fact_signals({
        "featureCount": 50, "numericFields": ["v"],
        "longitudeConvention": "e360", "crossesAntimeridian": True,
    })
    ev = out.get("evidence") or {}
    assert ev.get("longitudeConvention") == "e360"
    assert ev.get("crossesAntimeridian") is True
    assert ev.get("longitudeNormalizationRequired") is True

    out2 = _interpolation_fact_signals({"featureCount": 50,
                                        "numericFields": ["v"]})
    ev2 = out2.get("evidence") or {}
    assert "longitudeConvention" not in ev2
