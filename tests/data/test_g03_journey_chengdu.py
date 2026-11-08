"""G03 M6 —— 零模拟业务旅程：本地输入 → 资格 → 真实算法 → 图层 ref。

旅程一（正向）：成都 5 区小学点位 + 各区人口 → IngestPipeline 语义身份
→ preflight（任务范围=成都）→ **真实 spatial_aggregate 算法**（每万人
小学数率值图）→ 结果图层 ref。所有中间断言以生成的确定性真值为准。

旅程二（阻断）：同一数据，任务范围切到北京 → preflight SCOPE_MISMATCH
→ 不得进入算法（GD-08「未具备时明确阻断」）。
"""
from __future__ import annotations

import pytest

from app.services.data_ingest.pipeline import (
    get_ingest_pipeline,
    reset_ingest_pipeline,
)
from app.services.dataset_semantics import store as sem_store_mod
from app.services.dataset_semantics.store import DatasetSemanticStore

#: 生成真值：每区小学数（点在区内网格上，归属无歧义）与人口（万）。
DISTRICTS = [
    # (区名, x0, y0, 学校数, 人口万)
    ("锦江区", 104.10, 30.64, 12, 71.0),
    ("青羊区", 104.02, 30.66, 8, 83.0),
    ("金牛区", 104.04, 30.70, 10, 108.0),
    ("武侯区", 104.04, 30.60, 14, 122.0),
    ("成华区", 104.12, 30.68, 9, 94.0),
]
CELL = 0.004  # 区内点间距（远小于区格 0.06，归属确定）
CHENGDU_BBOX = [103.95, 30.52, 104.25, 30.78]
#: 合法 GeoJSON named-CRS 成员（文件自带声明 = 正当 CRS 证据；工具面
#: spatial_semantics 据此放行度量敏感操作，非虚构）。
_CRS_MEMBER = {"type": "name", "properties": {"name": "EPSG:4326"}}


def _school_points() -> dict:
    features = []
    for d_idx, (name, x0, y0, n, _) in enumerate(DISTRICTS):
        for i in range(n):
            features.append({
                "type": "Feature",
                "geometry": {"type": "Point",
                             "coordinates": [x0 + (i % 4) * CELL,
                                             y0 + (i // 4) * CELL]},
                "properties": {"name": f"{name}小学{i}", "district": name,
                               "ordinal": d_idx * 100 + i},
            })
    return {"type": "FeatureCollection", "crs": _CRS_MEMBER,
            "features": features}


def _district_polygons() -> dict:
    """每区一个确定性方格面（0.06°），互不相交、覆盖全部本区点位。"""
    features = []
    for d_idx, (name, x0, y0, _, pop) in enumerate(DISTRICTS):
        half = 0.03
        features.append({
            "type": "Feature",
            "geometry": {"type": "Polygon", "coordinates": [[
                [x0 - half, y0 - half], [x0 + half, y0 - half],
                [x0 + half, y0 + half], [x0 - half, y0 + half],
                [x0 - half, y0 - half],
            ]]},
            "properties": {"district": name, "population_wan": pop,
                           "ordinal": d_idx},
        })
    return {"type": "FeatureCollection", "crs": _CRS_MEMBER,
            "features": features}


@pytest.fixture(autouse=True)
def _reset():
    reset_ingest_pipeline()
    yield
    reset_ingest_pipeline()


@pytest.fixture()
def sem_store(tmp_path, monkeypatch):
    store = DatasetSemanticStore(base_dir=tmp_path)
    monkeypatch.setattr(sem_store_mod, "_store", store)
    yield store
    from app.services.dataset_semantics import reset_dataset_semantic_store

    reset_dataset_semantic_store()


class TestChengduSchoolRateJourney:
    async def test_full_journey_points_to_rate_layer(self, sem_store):
        sid = "g03-journey"
        # ── 1. 本地输入 → 语义身份（真实 ingest 管线）─────────────────
        schools = await get_ingest_pipeline().ingest(
            sid, _school_points(), name="成都小学点位", crs="EPSG:4326",
            source_type="upload", license="CC-BY-4.0",
            attribution="本地生成 fixture（确定性）")
        assert schools.ok, schools.error
        districts = await get_ingest_pipeline().ingest(
            sid, _district_polygons(), name="成都各区边界与人口",
            crs="EPSG:4326", source_type="upload")
        assert districts.ok, districts.error
        assert schools.ref_id != districts.ref_id

        s_rec = await sem_store.get(sid, schools.ref_id)
        d_rec = await sem_store.get(sid, districts.ref_id)
        assert s_rec.ok and d_rec.ok
        assert s_rec.descriptor.feature_count == sum(d[3] for d in DISTRICTS)
        assert s_rec.descriptor.license == "CC-BY-4.0"
        assert "Polygon" in d_rec.descriptor.geometry_types

        # ── 2. preflight（任务范围=成都）→ 可联───────────────────────
        from app.services.dataset_semantics.preflight import (
            preflight_multi_dataset,
        )

        report = preflight_multi_dataset(
            {"subject": s_rec.descriptor, "boundary": d_rec.descriptor},
            task_scope={"place": "成都", "bbox": CHENGDU_BBOX},
        )
        assert report.verdict != "unknown"
        scope_mismatch = any(
            g.code == "PREFLIGHT_SCOPE_MISMATCH" for g in report.gaps)
        assert not scope_mismatch, "成都数据在成都任务范围内不得误报范围缺口"

        # ── 3. 真实算法：spatial_aggregate（分母=人口 → 每万人率值）───
        from app.tools.registry import ToolRegistry
        from app.tools.advanced_spatial import register_advanced_spatial_tools

        registry = ToolRegistry()
        register_advanced_spatial_tools(registry)
        result = await registry.dispatch(
            "spatial_aggregate",
            {"points": schools.ref_id, "polygons": districts.ref_id,
             "denominator": "population_wan", "denominator_kind": "field"},
            session_id=sid,
        )
        assert result.get("success") is True, result
        data = result.get("data") or {}
        features = data.get("features") or []
        assert len(features) == len(DISTRICTS), "五个区都要有聚合结果"
        # 科学语义披露在场（GD-08：readiness/科学证据随结果可观测）
        assert result.get("spatial_semantics"), "空间语义证据必须随结果披露"

        by_district = {
            f["properties"]["district"]: f["properties"] for f in features}
        # 真值对账：每区分子（点计数）与率值 = 分子/分母，零虚构
        for name, _, _, n, pop in DISTRICTS:
            props = by_district[name]
            assert props["numerator"] == n, (
                f"{name} 点计数必须等于生成真值 {n}，实际 {props['numerator']}")
            assert abs(props["rate"] - n / pop) < 1e-9, (
                f"{name} 每万人率值 = 计数/人口，实际 {props['rate']}")
            assert props["has_support"] is True

    async def test_scope_switch_blocks_reuse(self, sem_store):
        """切城市：同一数据在北京任务范围 → preflight SCOPE_MISMATCH。"""
        sid = "g03-journey-block"
        schools = await get_ingest_pipeline().ingest(
            sid, _school_points(), name="成都小学", crs="EPSG:4326")
        assert schools.ok
        s_rec = await sem_store.get(sid, schools.ref_id)
        assert s_rec.ok

        from app.services.dataset_semantics.preflight import (
            CODE_SCOPE_MISMATCH,
            preflight_multi_dataset,
        )

        report = preflight_multi_dataset(
            {"subject": s_rec.descriptor},
            task_scope={"place": "北京", "bbox": [116.2, 39.8, 116.6, 40.1]},
        )
        assert CODE_SCOPE_MISMATCH in [g.code for g in report.gaps], (
            "成都数据不得凭名字复用到北京任务（明确阻断，不自动降格）"
        )

    async def test_scope_change_recomputes_mapspec_binding(self, sem_store):
        """城市切换后重绑定同数据 → 复用裁决 RECOMPUTE/TASK_SCOPE_CHANGED。"""
        from app.services.dataset_semantics.reuse import (
            evaluate_scoped_reuse,
        )
        from app.services.dataset_semantics.task_scope import (
            scope_fingerprint,
        )

        sid = "g03-journey-scope"
        schools = await get_ingest_pipeline().ingest(
            sid, _school_points(), name="成都小学", crs="EPSG:4326")
        assert schools.ok
        rec = await sem_store.get(sid, schools.ref_id)
        assert rec.ok

        chengdu = scope_fingerprint(place="成都", bbox=CHENGDU_BBOX)
        beijing = scope_fingerprint(place="北京", bbox=[116.2, 39.8, 116.6, 40.1])
        # 同数据、指纹一致，但范围变了 → recompute
        decision = evaluate_scoped_reuse(
            rec.descriptor.descriptor_fingerprint, rec,
            recorded_scope_fingerprint=chengdu,
            current_scope_fingerprint=beijing)
        assert decision.verdict == "recompute"
        assert "TASK_SCOPE_CHANGED" in decision.reason_codes
        # 范围未变 → 指纹对账照旧（valid）
        same = evaluate_scoped_reuse(
            rec.descriptor.descriptor_fingerprint, rec,
            recorded_scope_fingerprint=chengdu,
            current_scope_fingerprint=chengdu)
        assert same.verdict == "valid"
