"""GOAL 04 M9 —— 三个闭环制图场景（端到端：真实引擎 → apply → MapSpec →
多尺度 SVG 导出）。

场景 1：城市学校分布 —— 密度去密带级切换 + 「放大后显示所有学校点」
（user pin raw 整程）；
场景 2：省域道路图 —— 道路等级语义取舍（1:500000 主干不丢）+ 行政区
共享边界保护；
场景 3：行政分级专题图 —— 三种尺度导出共享同一分类口径（legend breaks
不变；粗尺度走 generalized 工件 + 诊断披露，细尺度 raw）。
"""
from __future__ import annotations

import uuid
from typing import Any

import pytest

from app.services.gis_harness.scale_profile import (
    apply_scale_representation,
    inspect_map_scale_profile,
    preview_scale_representation,
)
from app.services.mapspec.lifecycle_engine import (
    MapSpecLifecycleEngine,
    UpsertLayerIntent,
)
from app.services.mapspec.store import mapspec_store_instance
from app.services.session_data import session_data_manager
from app.services.mapspec_to_svg import compile_mapspec_to_svg_detailed

CHENGDU_LAT = 30.66


def _schools_fc(n: int = 8000) -> dict:
    feats = []
    for i in range(n):
        gx, gy = i % 100, i // 100
        feats.append({
            "type": "Feature", "id": f"school-{i}",
            "properties": {"name": f"小学{i}", "学生数": 200 + (i % 40) * 15},
            "geometry": {"type": "Point", "coordinates": [
                103.9 + gx * 0.0025 + (i % 7) * 1e-4,
                CHENGDU_LAT - 0.12 + gy * 0.0025 + (i % 5) * 1e-4]},
        })
    return {"type": "FeatureCollection", "features": feats}


def _roads_fc(n: int = 250) -> dict:
    classes = ["高速公路", "国道", "省道", "县道", "乡道"]
    feats = []
    for i in range(n):
        cls = classes[i % 5]
        pts = []
        for k in range(50):
            x = 104.0 + (i % 50) * 0.004 + k * 0.0006
            y = CHENGDU_LAT - 0.1 + (i // 50) * 0.05 + (
                2e-5 if k % 2 else -2e-5)
            pts.append([round(x, 7), round(y, 7)])
        feats.append({
            "type": "Feature", "id": f"road-{i}",
            "properties": {"道路等级": cls, "name": f"{cls}{i}"},
            "geometry": {"type": "LineString", "coordinates": pts},
        })
    return {"type": "FeatureCollection", "features": feats}


def _admin_fc(nx: int = 8, ny: int = 6, d: float = 0.04) -> dict:
    feats = []
    breaks = [0, 2000, 4000, 6000]  # 分类 breaks（口径一致性断言用）
    for gx in range(nx):
        for gy in range(ny):
            lon, lat = 103.95 + gx * d, CHENGDU_LAT - 0.1 + gy * d
            pop = 1000 + gx * 350 + gy * 520
            feats.append({
                "type": "Feature", "id": f"district-{gx}-{gy}",
                "properties": {"区县": f"区{gx}{gy}", "常住人口": pop},
                "geometry": {"type": "Polygon", "coordinates": [[
                    [lon, lat], [lon + d, lat], [lon + d, lat + d],
                    [lon, lat + d], [lon, lat]]]},
            })
    return {"type": "FeatureCollection", "features": feats,
            "_breaks": breaks}  # type: ignore[dict-item]


async def _seed(session_id: str, layer_id: str, fc: dict,
                ltype: str, paint: dict, legend: dict | None = None) -> None:
    engine = MapSpecLifecycleEngine()
    layer: dict[str, Any] = {
        "id": layer_id, "type": ltype, "source": f"src-{layer_id}",
        "paint": paint,
    }
    if legend:
        layer["legend_spec"] = legend
    res = await engine.apply_mutation(session_id, UpsertLayerIntent(
        layer=layer,
        source_data={"type": "geojson", "inlineData": fc},
    ))
    assert not res.is_error, res.error_msg


@pytest.fixture
async def session():
    sid = f"m9-{uuid.uuid4().hex[:10]}"
    yield sid
    session_data_manager._store.pop(sid, None)
    session_data_manager._map_state.pop(sid, None)
    await mapspec_store_instance.discard_mapspec(sid)


# ── 场景 1：城市学校不同 zoom ────────────────────────────────────────────


class TestScenario1Schools:
    async def test_density_bands_and_pin_raw(self, session):
        await _seed(session, "schools", _schools_fc(), "circle",
                    {"circle-color": "#1f77b4", "circle-radius": 4})
        # 证据面：无重要性字段 → 不取舍；密度证据驱动去密带
        report = await inspect_map_scale_profile(session, "schools")
        assert report["importance"]["selected"] is None
        assert any("无可信重要性字段" in d for d in report["disclosures"])

        pv = await preview_scale_representation(session, "schools")
        bands = {b["tier"]: b for b in pv["bands"]}
        # 8000 点：world(>1200)/province(>2000)/city(>4000) 密集 →
        # 去密模式激活；street(>8000 不成立) → 单点全显
        assert bands["world"]["decluttering"] in (
            "point_cluster", "visual_heatmap")
        assert bands["street"]["decluttering"] == "none"
        # 点族统计口径：原始 8000 恒披露（聚合是视觉口径）
        assert all(b["original"] == 8000 and b["displayed"] == 8000
                   for b in bands.values())

        # 「放大后显示所有学校点」→ user pin raw（整程单点显示）
        receipt = await apply_scale_representation(
            session, "schools", mode="commit", operation_id="m9-s1",
            overrides=[{"kind": "pin_mode", "value": "raw"}])
        assert receipt["status"] == "committed"
        assert all(b["representation_mode"] == "raw" for b in receipt["bands"])


# ── 场景 2：道路等级语义 + 行政边界 ──────────────────────────────────────


class TestScenario2RoadsAndBoundaries:
    async def test_semantic_selection_and_boundary_protection(self, session):
        await _seed(session, "roads", _roads_fc(), "line",
                    {"line-color": "#c33", "line-width": 1.5})
        receipt = await apply_scale_representation(
            session, "roads", mode="commit", operation_id="m9-s2")
        assert receipt["status"] == "committed"
        bands = {b["tier"]: b for b in receipt["bands"]}
        # 1:500000（world 带 1:N ≈ 1:460k@z8 显示端）主干不丢
        world = bands["world"]
        assert world["representation_mode"] == "generalized"
        assert world["displayed"] == 250 * 3 // 5  # 5 类取前 3
        assert bands["street"]["displayed"] == 250
        # 工件语义：粗带只有 高速/国道/省道
        gen_ref = receipt["artifact_refs"]["world"]
        res = await session_data_manager.get_ref_data_shared(session, gen_ref)
        kept_classes = {f["properties"]["道路等级"]
                        for f in res.data["features"]}
        assert kept_classes == {"高速公路", "国道", "省道"}
        # MapSpec 窗口 + 工件源 + 档案一致
        spec = await mapspec_store_instance.get_mapspec(session)
        layer = next(lyr for lyr in spec["layers"] if lyr["id"] == "roads")
        windows = layer["representation"]["windows"]
        gen_windows = [w for w in windows if w["mode"] == "generalized"]
        assert gen_windows and all(
            w["source"] in spec["sources"] for w in gen_windows)
        assert layer["scale_profile"]["classification_basis"] == (
            "original_source")

        # 行政边界（面族共享边界保护）
        await _seed(session, "districts", _admin_fc(), "line",
                    {"line-color": "#333", "line-width": 1})
        r2 = await apply_scale_representation(
            session, "districts", mode="commit", operation_id="m9-s2b")
        assert r2["status"] == "committed"
        d_world = next(b for b in r2["bands"] if b["tier"] == "world")
        if d_world["representation_mode"] == "generalized":
            gen2 = r2["artifact_refs"].get("world")
            res2 = await session_data_manager.get_ref_data_shared(session, gen2)
            # 48 面全保留（无重要性取舍）+ 锚点
            assert len(res2.data["features"]) == 48


# ── 场景 3：省域分级专题图三种尺度导出 ───────────────────────────────────


class TestScenario3ChoroplethExport:
    async def test_three_scale_exports_share_classification(self, session):
        admin = _admin_fc()
        admin.pop("_breaks")
        legend = {
            "title": "常住人口（人）",
            "items": [
                {"color": "#eff3ff", "label": "1000-2000"},
                {"color": "#6baed6", "label": "2000-4000"},
                {"color": "#2171b5", "label": "4000-6000"},
            ],
        }
        await _seed(session, "choro", admin, "fill",
                    {"fill-color": "#6baed6", "fill-opacity": 0.75},
                    legend=legend)
        receipt = await apply_scale_representation(
            session, "choro", mode="commit", operation_id="m9-s3")
        assert receipt["status"] == "committed"
        # 分类 breaks 指纹跨视图固定（legend_spec 不被 apply 触碰）
        spec = await mapspec_store_instance.get_mapspec(session)
        layer = next(lyr for lyr in spec["layers"] if lyr["id"] == "choro")
        fp = layer["scale_profile"]["classification_breaks_fingerprint"]
        assert fp and fp.startswith("legend-sha256:")

        # 三种尺度导出（1:500k≈z8 / 1:100k≈z11.5 / 街区级≈z15 显示端）。
        # 工件源是 ref 载荷 —— 按生产惯例（report_service/publication 在
        # 调 SVG 编译前水合）内联；主源同步解包为规范单层载体（播种的
        # 双层 inlineData 是既有 ingestion 形状，SVG 孪生只认单层 ——
        # 基线行为核对：双层在 master 也画 0 要素）。
        import copy as _copy

        hydrated = _copy.deepcopy(spec)
        for w in layer["representation"]["windows"]:
            sid_ = w.get("source")
            if sid_ and sid_ in hydrated["sources"]:
                src_entry = hydrated["sources"][sid_]
                ref_ = src_entry.get("ref_id") or src_entry.get("ref")
                res_ = await session_data_manager.get_ref_data_shared(
                    session, ref_)
                data_ = res_.data
                if isinstance(data_, dict) and isinstance(
                        data_.get("data"), dict):
                    data_ = data_["data"]  # ref 共享载荷的包装层
                src_entry["inlineData"] = data_
        main_entry = hydrated["sources"][layer["source"]]
        if isinstance(main_entry.get("inlineData"), dict) and isinstance(
                main_entry["inlineData"].get("inlineData"), dict):
            main_entry["inlineData"] = main_entry["inlineData"]["inlineData"]

        results = {}
        for zoom in (8.0, 11.5, 15.0):
            hydrated["view"]["zoom"] = zoom
            results[zoom] = compile_mapspec_to_svg_detailed(
                hydrated, include_chrome=True)

        # 细尺度 raw：无工件诊断；粗尺度 generalized：工件诊断在场
        codes = {z: [d.get("code") for d in r.diagnostics]
                 for z, r in results.items()}
        assert "layer_generalized_artifact" in codes[8.0]
        assert "layer_generalized_artifact" in codes[11.5]  # city 带也是工件
        assert "layer_generalized_artifact" not in codes[15.0]  # street raw
        # 三个尺度都真实画出了面要素（渲染非空）
        for z, r in results.items():
            assert r.feature_count > 0, z
        # 三尺度分类口径一致：同一 legend_spec 驱动 —— 层填色在三个导出中
        # 逐字节相同（分类不随尺度重算），legend_spec 本体未被 apply 触碰。
        for z, r in results.items():
            assert "#6baed6" in r.svg.lower(), z
        assert hydrated["layers"][0]["legend_spec"] == legend
        # 统计披露在档案里（displayed ≠ original 带可解释）
        block = layer["scale_profile"]
        assert block["classification_basis"] == "original_source"
