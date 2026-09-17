"""What-If 反事实假设推演分支管理器（agent-09, ADR-0193）单测套件。

覆盖（对应 docs/dev/whatif-branching-spec.md §6 验收矩阵）：
- 分支生命周期：主干 fork 出方案 A/B；任一分支修改/回滚不影响另一分支与
  Baseline 的世界状态（结构性会话命名空间隔离）；
- 多方案差分度量：给定 Baseline 与 Intervention，断言精确 delta
  （几何配对、面积/长度增量、服务覆盖人口增量、代理交通指标）；
- 报告自动生成：结构化对比专报（JSON 矩阵 + markdown），指标严格自洽；
- 处方性建议：确定性核（Pareto/ROI/优先级）+ LLM 降级路径（零静默降级）；
- scenario_mode 协议：schema 1.3 additive 演进 + SetScenarioModeIntent。

数字口径：本文件所有手算期望值基于等距圆柱局部投影（半径 6378137）。
经线方向每度 ≈ R·π/180 米（无 cos 因子）——同经线上的距离/比率断言与
投影细节无关；同纬度线段长度同乘 cos(lat0)，比率断言精确成立。
"""
import json
import math
import uuid

import pytest

from app.services.gis_world_state import build_world_state
from app.services.mapspec.lifecycle_engine import (
    MapSpecLifecycleEngine,
    UpsertLayerIntent,
)
from app.services.mapspec.store import mapspec_store_instance
from app.services.session_data import session_data_manager

from app.services.simulation.whatif import (
    BranchError,
    PrescriptiveAdvisor,
    ScenarioBranchManager,
    build_comparison_report,
    build_diff_overlay,
    compute_metric_deltas,
    diff_layers,
    render_comparison_markdown,
)
from app.services.simulation.whatif.prescriptive_advisor import BranchDiffResult
from app.services.simulation.whatif.spatial_diff_engine import (
    extract_layer_payloads,
    resolve_layer_role,
)

R_EARTH = 6378137.0
M_PER_DEG_LAT = R_EARTH * math.pi / 180.0


# ────────────────────────────── fixtures / helpers ──────────────────────────


def _sid(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _poly_fc(cells: list[tuple[str, float, float, float, dict]]) -> dict:
    """以经纬度半宽构造方形 Polygon FeatureCollection。

    cells: (feature_id, center_lng, center_lat, half_deg, properties)
    """
    features = []
    for fid, lng, lat, half, props in cells:
        features.append(
            {
                "type": "Feature",
                "id": fid,
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [
                        [
                            [lng - half, lat - half],
                            [lng + half, lat - half],
                            [lng + half, lat + half],
                            [lng - half, lat + half],
                            [lng - half, lat - half],
                        ]
                    ],
                },
                "properties": {"id": fid, **props},
            }
        )
    return {"type": "FeatureCollection", "features": features}


def _line_fc(segments: list[tuple[str, float, float, float, dict]]) -> dict:
    """同经线上的竖直线段 FeatureCollection：(id, lng, lat_start, lat_end, props)。"""
    features = []
    for fid, lng, lat0, lat1, props in segments:
        features.append(
            {
                "type": "Feature",
                "id": fid,
                "geometry": {
                    "type": "LineString",
                    "coordinates": [[lng, lat0], [lng, lat1]],
                },
                "properties": {"id": fid, **props},
            }
        )
    return {"type": "FeatureCollection", "features": features}


def _point_fc(points: list[tuple[str, float, float, dict]]) -> dict:
    features = []
    for fid, lng, lat, props in points:
        features.append(
            {
                "type": "Feature",
                "id": fid,
                "geometry": {"type": "Point", "coordinates": [lng, lat]},
                "properties": {"id": fid, **props},
            }
        )
    return {"type": "FeatureCollection", "features": features}


def _fc_geometry(fc: dict) -> dict:
    """把 FeatureCollection 包成 geojson source 的 inlineData。"""
    return fc


async def _seed_baseline_world(parent_sid: str) -> None:
    """主干播种：绿地面 + 道路线 + 设施点 + 人口格网（推演基线现状）。"""
    engine = MapSpecLifecycleEngine()
    layers = [
        (
            "park-main",
            "park-main",
            _poly_fc(
                [
                    ("park-1", 104.0, 30.6, 0.001, {"kind": "park", "category": "绿地"}),
                ]
            ),
        ),
        (
            "road-main",
            "road-main",
            _line_fc(
                [
                    ("road-1", 104.0, 30.5, 30.51, {"kind": "arterial"}),
                ]
            ),
        ),
        (
            "facility-main",
            "facility-main",
            _point_fc(
                [
                    ("clinic-1", 104.0, 30.6, {"kind": "clinic"}),
                ]
            ),
        ),
        (
            "population-main",
            "population-main",
            _poly_fc(
                [
                    ("cell-a", 104.0, 30.604, 0.00025, {"population": 100}),
                    ("cell-b", 104.0, 30.59, 0.00025, {"population": 300}),
                ]
            ),
        ),
    ]
    geom_by_layer = {
        "park-main": "fill",
        "road-main": "line",
        "facility-main": "circle",
        "population-main": "fill",
    }
    for layer_id, source_id, fc in layers:
        res = await engine.apply_mutation(
            parent_sid,
            UpsertLayerIntent(
                layer={
                    "id": layer_id,
                    "type": geom_by_layer[layer_id],
                    "source": source_id,
                },
                source_data={"type": "geojson", "inlineData": fc},
            ),
        )
        assert not res.is_error, res.error_msg


def _layer(layer_id: str, source_id: str, fc: dict, geom_type: str = "fill") -> UpsertLayerIntent:
    return UpsertLayerIntent(
        layer={"id": layer_id, "type": geom_type, "source": source_id},
        source_data={"type": "geojson", "inlineData": fc},
    )


# ────────────────────────── T1–T5: 分支生命周期与隔离 ──────────────────────────


@pytest.mark.asyncio
async def test_t1_fork_two_branches_from_baseline_is_safe():
    parent = _sid("wif-t1")
    await _seed_baseline_world(parent)

    mgr = ScenarioBranchManager()
    base_spec = await mapspec_store_instance.get_mapspec(parent)
    assert base_spec is not None
    parent_rev_before = (await session_data_manager.get_map_state(parent)).get(
        "_cartographic_mutation_revision"
    )

    meta_a = await mgr.create_branch(parent, "A", title="立交桥方案", hypothesis="新建立交桥后早高峰拥堵如何？")
    meta_b = await mgr.create_branch(parent, "B", title="商业中心方案", hypothesis="绿地改商业中心后学区负荷如何？")

    # 两分支 spec 与 baseline 快照一致
    spec_a = await mapspec_store_instance.get_mapspec(meta_a.branch_session_id)
    spec_b = await mapspec_store_instance.get_mapspec(meta_b.branch_session_id)
    assert spec_a == base_spec
    assert spec_b == base_spec
    # 指纹一致且非空
    assert meta_a.baseline_fingerprint == meta_b.baseline_fingerprint
    assert len(meta_a.baseline_fingerprint) == 64
    # fork 不触碰父会话世界状态
    parent_rev_after = (await session_data_manager.get_map_state(parent)).get(
        "_cartographic_mutation_revision"
    )
    assert parent_rev_after == parent_rev_before
    # 注册表 2 条 active，父 spec 未变
    branches = await mgr.list_branches(parent)
    assert [b.branch_id for b in branches] == ["A", "B"]
    assert all(b.status == "active" for b in branches)
    assert await mapspec_store_instance.get_mapspec(parent) == base_spec
    # fork 存证落在分支 map_state
    evidence = (await session_data_manager.get_map_state(meta_a.branch_session_id)).get(
        "_whatif_fork"
    )
    assert evidence["parent_session_id"] == parent
    assert evidence["baseline_fingerprint"] == meta_a.baseline_fingerprint


@pytest.mark.asyncio
async def test_t2_branch_mutation_isolation_between_branches():
    parent = _sid("wif-t2")
    await _seed_baseline_world(parent)
    mgr = ScenarioBranchManager()
    await mgr.create_branch(parent, "A")
    await mgr.create_branch(parent, "B")

    # 方案 A 新增立交（道路层）
    road_fc = _line_fc([("flyover-a", 104.0005, 30.6, 30.61, {"kind": "flyover"})])
    results = await mgr.apply_intervention(
        parent, "A", [_layer("road-proposal-a", "road-proposal-a", road_fc, "line")]
    )
    assert len(results) == 1 and not results[0].is_error

    state_a = await build_world_state(
        (await mgr.get_branch_meta(parent, "A")).branch_session_id
    )
    state_b = await build_world_state(
        (await mgr.get_branch_meta(parent, "B")).branch_session_id
    )
    state_p = await build_world_state(parent)
    ids_a = {lyr["id"] for lyr in state_a["layers"]}
    ids_b = {lyr["id"] for lyr in state_b["layers"]}
    ids_p = {lyr["id"] for lyr in state_p["layers"]}
    assert "road-proposal-a" in ids_a
    assert "road-proposal-a" not in ids_b
    assert "road-proposal-a" not in ids_p
    # A/B 修订计数独立递增（分支从 revision=1 物化起步，各走各的）
    meta_a = await mgr.get_branch_meta(parent, "A")
    meta_b = await mgr.get_branch_meta(parent, "B")
    assert meta_a.last_revision >= 2
    assert meta_b.last_revision == 1


@pytest.mark.asyncio
async def test_t3_branch_rollback_spares_sibling_and_baseline():
    parent = _sid("wif-t3")
    await _seed_baseline_world(parent)
    mgr = ScenarioBranchManager()
    await mgr.create_branch(parent, "A")
    await mgr.create_branch(parent, "B")

    base_spec = await mapspec_store_instance.get_mapspec(parent)
    base_rev = (await session_data_manager.get_map_state(parent)).get(
        "_cartographic_mutation_revision"
    )

    # 干预 1（自动 checkpoint 捕获干预 1 后状态）
    road_fc = _line_fc([("flyover-a", 104.0005, 30.6, 30.61, {"kind": "flyover"})])
    r1 = await mgr.apply_intervention(
        parent, "A", [_layer("road-proposal-a", "road-proposal-a", road_fc, "line")]
    )
    assert not r1[0].is_error
    ckpt_id = r1[0].checkpoint_id
    assert ckpt_id

    # 干预 2
    plaza_fc = _poly_fc([("plaza-a", 104.001, 30.6, 0.0005, {"kind": "plaza"})])
    r2 = await mgr.apply_intervention(
        parent, "A", [_layer("plaza-proposal-a", "plaza-proposal-a", plaza_fc)]
    )
    assert not r2[0].is_error
    spec_a_after_two = await mapspec_store_instance.get_mapspec(
        (await mgr.get_branch_meta(parent, "A")).branch_session_id
    )
    assert {"road-proposal-a", "plaza-proposal-a"} <= {
        lyr["id"] for lyr in spec_a_after_two["layers"]
    }

    # 回滚 A 到干预 1 后状态
    rb = await mgr.rollback_branch(parent, "A", checkpoint_id=ckpt_id)
    assert not rb.is_error, rb.error_msg

    spec_a = await mapspec_store_instance.get_mapspec(
        (await mgr.get_branch_meta(parent, "A")).branch_session_id
    )
    spec_b = await mapspec_store_instance.get_mapspec(
        (await mgr.get_branch_meta(parent, "B")).branch_session_id
    )
    ids_a = {lyr["id"] for lyr in spec_a["layers"]}
    ids_b = {lyr["id"] for lyr in spec_b["layers"]}
    assert "road-proposal-a" in ids_a and "plaza-proposal-a" not in ids_a
    assert "road-proposal-a" not in ids_b and "plaza-proposal-a" not in ids_b
    # 另一分支与 Baseline 的世界状态纹丝不动
    assert spec_b == base_spec
    assert await mapspec_store_instance.get_mapspec(parent) == base_spec
    assert (await session_data_manager.get_map_state(parent)).get(
        "_cartographic_mutation_revision"
    ) == base_rev


@pytest.mark.asyncio
async def test_t4_registry_discipline_rejects_bad_forks():
    parent = _sid("wif-t4")
    await _seed_baseline_world(parent)
    mgr = ScenarioBranchManager()

    # 非法 branch_id（路径字符 / 超长）
    with pytest.raises(BranchError) as exc:
        await mgr.create_branch(parent, "../evil")
    assert exc.value.code == "branch_id_invalid"
    with pytest.raises(BranchError):
        await mgr.create_branch(parent, "x" * 64)

    # 重复 fork
    await mgr.create_branch(parent, "A")
    with pytest.raises(BranchError) as exc:
        await mgr.create_branch(parent, "A")
    assert exc.value.code == "branch_exists"

    # 活跃分支上限（MAX_ACTIVE_BRANCHES；当前活跃 1 个 → 限额压到 1 触发）
    from app.services.simulation.whatif import branch_manager as bm

    original_limit = bm.MAX_ACTIVE_BRANCHES
    bm.MAX_ACTIVE_BRANCHES = 1
    try:
        with pytest.raises(BranchError) as exc:
            await mgr.create_branch(parent, "C")
        assert exc.value.code == "branch_limit"
    finally:
        bm.MAX_ACTIVE_BRANCHES = original_limit

    # 父无 spec → baseline_missing
    empty_parent = _sid("wif-t4-empty")
    with pytest.raises(BranchError) as exc:
        await mgr.create_branch(empty_parent, "A")
    assert exc.value.code == "baseline_missing"
    # 所有失败路径不得遗留注册表垃圾（除合法 A 外无多余条目）
    branches = await mgr.list_branches(parent)
    assert [b.branch_id for b in branches] == ["A"]


@pytest.mark.asyncio
async def test_t5_delete_branch_releases_state_and_is_idempotent():
    parent = _sid("wif-t5")
    await _seed_baseline_world(parent)
    mgr = ScenarioBranchManager()
    meta = await mgr.create_branch(parent, "A")
    branch_sid = meta.branch_session_id

    from app.services.mapspec.store import BASE_STORAGE_DIR

    branch_dir = BASE_STORAGE_DIR / branch_sid
    assert branch_dir.exists()

    assert await mgr.delete_branch(parent, "A") is True
    # 内存/Redis map_state 已清空
    assert await session_data_manager.get_map_state(branch_sid) == {}
    # 磁盘目录已回收
    assert not branch_dir.exists()
    # 注册表已移除
    assert await mgr.list_branches(parent) == []
    # 幂等：再删返回 False 且不抛
    assert await mgr.delete_branch(parent, "A") is False


# ─────────────────────── T6–T8: 空间差分与指标度量 ───────────────────────


def test_t6_geometry_diff_pairing_and_deltas():
    """几何配对 + 面积/长度增量精确断言（同纬度比率不受投影影响）。"""
    base_fc = _poly_fc(
        [
            ("f1", 104.0, 30.6, 0.001, {}),
            ("f2", 104.01, 30.6, 0.001, {}),
            ("f4", 104.02, 30.6, 0.001, {}),
        ]
    )
    branch_fc = _poly_fc(
        [
            ("f1", 104.0, 30.6005, 0.001, {}),  # modified（同 id 几何变）
            ("f2", 104.01, 30.6, 0.001, {}),  # unchanged
            ("f3", 104.03, 30.6, 0.002, {}),  # added（边长 2×）
        ]
    )
    diffs = diff_layers({"zones": base_fc}, {"zones": branch_fc})
    d = diffs["zones"]
    assert [f["properties"]["id"] for f in d.added_features] == ["f3"]
    assert [f["properties"]["id"] for f in d.removed_features] == ["f4"]
    assert [f["after"]["properties"]["id"] for f in d.modified_features] == ["f1"]
    assert d.unchanged_count == 1

    # added 面积 = (2×半宽比)² × 基线方格面积 → 用 f2 面积作参照推手算值。
    # 同纬度方格面积 ∝ (全边长)²；f3 半宽 0.002、参照半宽 0.001 → 4.0×参照。
    ref_area = d.unchanged_area_m2  # unchanged f2 的面积（引擎同法计算）
    assert ref_area > 0
    assert d.added_area_m2 == pytest.approx(4.0 * ref_area, rel=1e-9)
    assert d.removed_area_m2 == pytest.approx(ref_area, rel=1e-9)

    # 绝对量级 sanity：0.001° 半宽方格 @ lat 30.6 → x 边 2·half·M·cos(lat0)，
    # y 边 2·half·M（等距圆柱仅 x 向乘 cos 因子）。
    cos306 = math.cos(math.radians(30.6))
    expected = (2 * 0.001 * M_PER_DEG_LAT * cos306) * (2 * 0.001 * M_PER_DEG_LAT)
    assert ref_area == pytest.approx(expected, rel=0.01)

    # 线段长度增量：同经线段长 = Δlat · M_PER_DEG_LAT（无 cos 因子，精确手算）
    base_roads = _line_fc([("r1", 104.0, 30.5, 30.51, {})])
    branch_roads = _line_fc(
        [
            ("r1", 104.0, 30.5, 30.51, {}),
            ("r2", 104.0, 30.52, 30.522, {}),
        ]
    )
    rdiff = diff_layers({"roads": base_roads}, {"roads": branch_roads})["roads"]
    assert len(rdiff.added_features) == 1
    assert rdiff.added_length_m == pytest.approx(0.002 * M_PER_DEG_LAT, rel=1e-9)


def test_t7_service_coverage_population_delta():
    """覆盖差分：同经线布设，距离 = Δlat·M_PER_DEG_LAT，手算精确。"""
    # 设施 clinic-1 在 (104.0, 30.60)，服务半径 800m
    baseline_fac = _point_fc([("clinic-1", 104.0, 30.60, {"kind": "clinic"})])
    branch_fac = _point_fc(
        [
            ("clinic-1", 104.0, 30.60, {"kind": "clinic"}),
            ("clinic-2", 104.0, 30.585, {"kind": "clinic"}),  # 新增设施
        ]
    )
    # 人口格网：cell-a 中心距 clinic-1 = 0.004°·M ≈ 444.8m（基线已覆盖）
    #            cell-b 中心距 clinic-1 = 0.010°·M ≈ 1111.9m（基线未覆盖）
    #            cell-b 距 clinic-2   = 0.005°·M ≈  556.0m（分支覆盖）
    population = _poly_fc(
        [
            ("cell-a", 104.0, 30.604, 0.00025, {"population": 100}),
            ("cell-b", 104.0, 30.59, 0.00025, {"population": 300}),
        ]
    )
    base_payloads = {
        "facility-main": baseline_fac,
        "population-main": population,
    }
    branch_payloads = {
        "facility-main": branch_fac,
        "population-main": population,
    }
    coverage = diff_layers(
        base_payloads, branch_payloads, service_radius_m=800.0
    ).get("_coverage")
    assert coverage is not None
    assert coverage["gained_area_m2"] > 0
    assert coverage["lost_area_m2"] == pytest.approx(0.0, abs=1e-6)

    deltas = compute_metric_deltas(
        base_payloads, branch_payloads, service_radius_m=800.0
    )
    by_key = {d.metric_key: d for d in deltas}
    cov = by_key["service_coverage_population"]
    # 覆盖人口：基线 100 → 分支 400（cell-a + cell-b），增量 +300 人
    assert cov.baseline == pytest.approx(100.0)
    assert cov.simulated == pytest.approx(400.0)
    assert cov.delta_abs == pytest.approx(300.0)
    assert cov.delta_pct == pytest.approx(300.0)
    # 设施计数 +1
    fac = by_key["facility_count"]
    assert fac.baseline == pytest.approx(1.0)
    assert fac.simulated == pytest.approx(2.0)
    assert fac.delta_pct == pytest.approx(100.0)


def test_t8_metric_deltas_precise_and_gis03_missing_baseline():
    """指标差分：精确 delta_pct + 缺输入指标全 None（GIS-03 纪律）。"""
    base_green = _poly_fc([("park-1", 104.0, 30.6, 0.001, {"kind": "park"})])
    branch_green = _poly_fc(
        [
            ("park-1", 104.0, 30.6, 0.001, {"kind": "park"}),
            ("park-2", 104.02, 30.6, 0.0015, {"kind": "park"}),  # 半宽 1.5× → 面积 2.25×
        ]
    )
    base_roads = _line_fc([("r1", 104.0, 30.5, 30.51, {})])
    branch_roads = _line_fc(
        [
            ("r1", 104.0, 30.5, 30.51, {}),
            ("r2", 104.0, 30.52, 30.522, {}),  # +0.002° / 基线 0.010° = +20%
        ]
    )
    base_payloads = {"park-main": base_green, "road-main": base_roads}
    branch_payloads = {"park-main": branch_green, "road-main": branch_roads}

    deltas = compute_metric_deltas(base_payloads, branch_payloads)
    by_key = {d.metric_key: d for d in deltas}
    green = by_key["green_area_m2"]
    # 基线面积 1×，分支 1 + 2.25 = 3.25× → +225%
    assert green.delta_pct == pytest.approx(225.0, rel=1e-9)
    assert green.simulated == pytest.approx(3.25 * green.baseline, rel=1e-9)
    # 绝对量级 sanity：x 边乘 cos、y 边不乘（等距圆柱口径）
    cos306 = math.cos(math.radians(30.6))
    assert green.baseline == pytest.approx(
        (2 * 0.001 * M_PER_DEG_LAT * cos306) * (2 * 0.001 * M_PER_DEG_LAT), rel=0.01
    )

    cap = by_key["road_capacity_index"]
    assert cap.delta_pct == pytest.approx(20.0, rel=1e-9)
    assert cap.metric_name  # 中文名称披露
    length = by_key["road_length_m"]
    assert length.delta_pct == pytest.approx(20.0, rel=1e-9)

    # GIS-03：缺人口图层 → 覆盖指标全 None + gap note
    deltas_no_pop = compute_metric_deltas(base_payloads, branch_payloads)
    by_key2 = {d.metric_key: d for d in deltas_no_pop}
    cov = by_key2["service_coverage_population"]
    assert cov.missing_baseline is True
    assert cov.baseline is None and cov.simulated is None
    assert cov.delta_abs is None and cov.delta_pct is None
    assert cov.evidence_gap_note
    # 缺设施层同理
    deltas_only_pop = compute_metric_deltas(
        {"population-main": _poly_fc([("c", 104.0, 30.6, 0.001, {"population": 50})])},
        {"population-main": _poly_fc([("c", 104.0, 30.6, 0.001, {"population": 80})])},
    )
    cov2 = {d.metric_key: d for d in deltas_only_pop}["service_coverage_population"]
    assert cov2.missing_baseline is True and cov2.evidence_gap_note


def test_t8b_layer_role_resolution():
    assert resolve_layer_role("park-main", {}) == "green"
    assert resolve_layer_role("road-main", {}) == "road"
    assert resolve_layer_role("facility-main", {}) == "facility"
    assert resolve_layer_role("population-main", {}) == "population"
    assert resolve_layer_role("noise-layer", {}) == "other"
    # 显式 whatif_role 优先于关键词启发
    assert resolve_layer_role("noise-layer", {"whatif_role": "road"}) == "road"


def test_t8c_diff_overlay_renders_impact_signs():
    branch = {
        "park-main": _poly_fc(
            [
                ("p1", 104.0, 30.6, 0.001, {"kind": "park"}),
                ("p2", 104.02, 30.6, 0.001, {"kind": "park"}),  # 新增绿地 → positive
            ]
        ),
        "road-main": _line_fc(
            [
                ("r1", 104.0, 30.5, 30.51, {}),
                ("r2", 104.0, 30.52, 30.522, {}),
            ]
        ),
    }
    branch_base = {
        "park-main": _poly_fc([("p1", 104.0, 30.6, 0.001, {"kind": "park"})]),
        "road-main": _line_fc([("r1", 104.0, 30.5, 30.51, {})]),
    }
    diffs = diff_layers(branch_base, branch)
    overlay = build_diff_overlay(diffs)
    assert overlay["type"] == "FeatureCollection"
    feats = overlay["features"]
    signs = {f["properties"]["id"]: f["properties"]["impact_sign"] for f in feats}
    hints = {f["properties"]["id"]: f["properties"]["render_hint"] for f in feats}
    kinds = {f["properties"]["id"]: f["properties"]["diff_kind"] for f in feats}
    assert signs["p2"] == "positive"
    assert hints["p2"] == "#22c55e"  # 绿 = 正面改善
    assert signs["r2"] == "positive"  # 新增道路供给 = 正面
    assert kinds["p2"] == "added"
    # 负面样例：移除设施
    diffs_removed = diff_layers(
        {
            "facility-main": _point_fc(
                [("c1", 104.0, 30.6, {}), ("c2", 104.01, 30.6, {})]
            )
        },
        {"facility-main": _point_fc([("c1", 104.0, 30.6, {})])},
    )
    overlay_removed = build_diff_overlay(diffs_removed)
    assert overlay_removed["features"][0]["properties"]["impact_sign"] == "negative"
    assert overlay_removed["features"][0]["properties"]["render_hint"] == "#ef4444"


def test_extract_layer_payloads_from_mapspec():
    """从 MapSpec 抽取 layer→FeatureCollection（inlineData 通道）。"""
    mapspec = {
        "version": "1.0",
        "sources": {
            "src-park": {
                "type": "geojson",
                "inlineData": _poly_fc([("p1", 104.0, 30.6, 0.001, {"kind": "park"})]),
            },
            "src-ref": {"type": "geojson", "ref": "ref:abc"},
        },
        "layers": [
            {"id": "park-main", "type": "fill", "source": "src-park"},
            {"id": "ref-layer", "type": "fill", "source": "src-ref"},
        ],
    }
    payloads = extract_layer_payloads(mapspec)
    assert set(payloads) == {"park-main"}  # ref 载荷不在 inlineData → 跳过
    assert payloads["park-main"]["features"][0]["properties"]["id"] == "p1"


# ─────────────────────── T9–T12: 对比矩阵、专报与处方建议 ───────────────────────


def _mk_branch_diff(
    branch_id: str,
    metric_deltas: list,
    cost_proxy: float,
    hypothesis: str = "",
    geometry_summary: dict | None = None,
) -> BranchDiffResult:
    return BranchDiffResult(
        branch_id=branch_id,
        title=f"方案 {branch_id}",
        hypothesis=hypothesis,
        metric_deltas=metric_deltas,
        geometry_summary=geometry_summary or {"layers": {}, "_coverage": None},
        overlay_features=[],
        cost_proxy=cost_proxy,
    )


def _metric(key: str, base: float | None, sim: float | None, name: str | None = None):
    from app.services.spatial_decision.models import MetricDeltaV2

    delta_abs = None if (base is None or sim is None) else sim - base
    delta_pct = None if (not base or sim is None) else (sim - base) / base * 100.0
    return MetricDeltaV2(
        metric_key=key,
        metric_name=name or key,
        baseline=base,
        simulated=sim,
        delta_abs=delta_abs,
        delta_pct=delta_pct,
        missing_baseline=base is None,
    )


def test_t9_comparison_matrix_and_none_propagation():
    """矩阵与分支 MetricDelta 一致；None 传播；确定性。"""
    advisor = PrescriptiveAdvisor()
    branches = [
        _mk_branch_diff(
            "A",
            [
                _metric("service_coverage_population", 100.0, 400.0),
                _metric("road_capacity_index", 1000.0, 1200.0),
                _metric("green_area_m2", None, None),
            ],
            cost_proxy=120.0,
        ),
        _mk_branch_diff(
            "B",
            [
                _metric("service_coverage_population", 100.0, 250.0),
                _metric("road_capacity_index", 1000.0, 1000.0),
                _metric("green_area_m2", 400.0, 1300.0),
            ],
            cost_proxy=150.0,
        ),
    ]
    baseline_metrics = {
        "service_coverage_population": 100.0,
        "road_capacity_index": 1000.0,
        "green_area_m2": 400.0,
    }
    comparison = advisor.build_comparison(
        baseline_metrics=baseline_metrics,
        branches=branches,
    )
    matrix = comparison.metric_matrix
    assert matrix["service_coverage_population"] == {
        "baseline": 100.0, "A": 400.0, "B": 250.0,
    }
    # 分支 A 的 green 无基线证据 → 全 None（不传播 baseline 值伪造）
    assert matrix["green_area_m2"]["A"] is None
    assert matrix["green_area_m2"]["B"] == 1300.0
    # 两次构建同一输出（确定性）
    comparison2 = advisor.build_comparison(
        baseline_metrics=baseline_metrics, branches=branches
    )
    assert comparison2.metric_matrix == matrix
    assert comparison2.advice.recommended_branch_id == (
        comparison.advice.recommended_branch_id
    )


def test_t10_report_generation_structured_and_self_consistent():
    advisor = PrescriptiveAdvisor()
    branches = [
        _mk_branch_diff(
            "A",
            [
                _metric("service_coverage_population", 100.0, 400.0, "服务覆盖人口"),
                _metric("road_capacity_index", 1000.0, 1200.0, "道路通行能力指数"),
                _metric("green_area_m2", None, None, "绿地面积"),
            ],
            cost_proxy=120.0,
            hypothesis="新建立交桥后早高峰拥堵如何？",
            geometry_summary={
                "layers": {
                    "road-proposal-a": {
                        "added": 1, "removed": 0, "modified": 0,
                        "added_length_m": 222.39,
                    }
                },
                "_coverage": {"gained_area_m2": 250000.0, "lost_area_m2": 0.0},
            },
        ),
        _mk_branch_diff(
            "B",
            [
                _metric("service_coverage_population", 100.0, 250.0, "服务覆盖人口"),
                _metric("road_capacity_index", 1000.0, 1000.0, "道路通行能力指数"),
                _metric("green_area_m2", 400.0, 1300.0, "绿地面积"),
            ],
            cost_proxy=150.0,
            hypothesis="绿地改商业中心后学区负荷如何？",
            geometry_summary={"layers": {}, "_coverage": None},
        ),
    ]
    baseline_metrics = {
        "service_coverage_population": 100.0,
        "road_capacity_index": 1000.0,
        "green_area_m2": 400.0,
    }
    comparison = advisor.build_comparison(
        baseline_metrics=baseline_metrics, branches=branches
    )
    report = build_comparison_report(comparison)
    md = render_comparison_markdown(report)

    # 结构化 JSON 面
    assert report["report_id"]
    assert report["baseline_fingerprint"] or report["baseline_revision"] is not None
    assert set(report["metric_matrix"]["service_coverage_population"].keys()) == {
        "baseline", "A", "B",
    }
    assert report["advice"]["recommended_branch_id"] in {"A", "B"}
    # 缺口披露：A 的 green_area_m2 无基线
    gaps = " ".join(report["gaps"])
    assert "green_area_m2" in gaps and "A" in gaps

    # markdown 面：三段齐备
    assert "多方案对比专报" in md
    assert "| 指标 | Baseline | 方案 A | 方案 B |" in md
    assert "处方性" in md
    assert "缺口" in md
    assert "新建立交桥" in md  # 假设问句入报
    # 自洽：矩阵数值以 2 位小数出现在对应行；None 渲染 "—"
    row_cov = next(line for line in md.splitlines() if "服务覆盖人口" in line)
    assert "400.00" in row_cov and "250.00" in row_cov and "100.00" in row_cov
    row_green = next(line for line in md.splitlines() if "绿地面积" in line)
    assert "—" in row_green  # A 无基线 → —
    # Δ% 列：A 覆盖 +300%
    row_cov_delta = row_cov
    assert "300.00" in row_cov_delta or "+300.00" in row_cov_delta


def test_t11_prescriptive_advisor_deterministic_core():
    advisor = PrescriptiveAdvisor()
    branches = [
        _mk_branch_diff(
            "A",
            [
                _metric("service_coverage_population", 100.0, 120.0),  # +20%
                _metric("road_capacity_index", 1000.0, 1050.0),  # +5%
            ],
            cost_proxy=100.0,
        ),
        _mk_branch_diff(
            "B",
            [
                _metric("service_coverage_population", 100.0, 120.0),  # +20%
                _metric("road_capacity_index", 1000.0, 1050.0),  # +5%
                _metric("green_area_m2", 400.0, 440.0),  # +10%
            ],
            cost_proxy=150.0,
        ),
        _mk_branch_diff(
            "C",
            [
                _metric("service_coverage_population", 100.0, 90.0),  # −10%
                _metric("road_capacity_index", 1000.0, 980.0),  # −2%
            ],
            cost_proxy=50.0,
        ),
    ]
    advice = advisor.advise(
        baseline_metrics={"service_coverage_population": 100.0,
                          "road_capacity_index": 1000.0,
                          "green_area_m2": 400.0},
        branches=branches,
    )
    # B 支配 A（覆盖/通行力持平、绿地更优）→ 推荐只能是 B；C 全劣不入围
    assert advice.mode == "deterministic" or advice.recommended_branch_id is not None
    assert advice.recommended_branch_id == "B"
    assert "C" not in advice.pareto_optimal
    assert set(advice.pareto_optimal) == {"A", "B"}
    # ROI 敏感度（按 方案×指标 展开；排序口径 = 该方案最优指标 ROI）：
    # A 同等效益更低成本 → 最优 ROI 高于 B；C 全负 → 为负
    roi: dict = {}
    for r in advice.roi_sensitivity:
        roi[r["branch_id"]] = max(roi.get(r["branch_id"], float("-inf")), r["roi_index"])
    assert roi["A"] > roi["B"]
    assert roi["C"] < 0
    priority_ids = [p["branch_id"] for p in advice.implementation_priority]
    assert priority_ids.index("A") < priority_ids.index("B")
    assert priority_ids[-1] == "C"
    # 因果链非空且为字符串步骤
    assert advice.rationale_causal_chain and all(
        isinstance(s, str) and s for s in advice.rationale_causal_chain
    )
    # 确定性：同输入两次调用全等
    advice2 = advisor.advise(
        baseline_metrics={"service_coverage_population": 100.0,
                          "road_capacity_index": 1000.0,
                          "green_area_m2": 400.0},
        branches=branches,
    )
    assert advice2.model_dump() == advice.model_dump()
    # 缺基线指标被剔除（missing_baseline → None → 不参与支配/评分）
    branches_gap = [
        _mk_branch_diff(
            "D",
            [
                _metric("service_coverage_population", None, None),
                _metric("road_capacity_index", 1000.0, 1100.0),
            ],
            cost_proxy=80.0,
        ),
    ]
    advice_gap = advisor.advise(
        baseline_metrics={"road_capacity_index": 1000.0}, branches=branches_gap
    )
    assert advice_gap.recommended_branch_id == "D"
    assert all(
        r["metric_key"] != "service_coverage_population" for r in advice_gap.roi_sensitivity
    )


@pytest.mark.asyncio
async def test_t12_llm_degradation_paths_never_raise(monkeypatch):
    # CI 注入 LLM_API_KEY=test-key-not-real（非占位符集合）——显式置空，
    # 保证"默认环境无 LLM key"的语义不受运行环境影响。
    from app.core.config import settings as app_settings

    monkeypatch.setattr(app_settings, "LLM_API_KEY", "")
    advisor = PrescriptiveAdvisor()
    branches = [
        _mk_branch_diff(
            "A",
            [_metric("road_capacity_index", 1000.0, 1200.0)],
            cost_proxy=100.0,
        ),
    ]
    # 默认环境无 LLM key → 显式降级，不抛
    advice = await advisor.advise_with_narrative(
        baseline_metrics={"road_capacity_index": 1000.0}, branches=branches
    )
    assert advice.mode == "deterministic"
    assert advice.degraded_reason == "llm_unavailable"
    assert advice.narrative  # 模板叙述兜底

    # LLM 可用但输出非法 JSON → llm_output_invalid
    import app.services.simulation.whatif.prescriptive_advisor as pa

    try:
        async def _fake_ok(cfg, messages, tools=None):
            return {
                "choices": [{"message": {"content": "这不是 JSON"}}],
                "usage": {},
            }

        pa._llm_available = lambda: True  # type: ignore[assignment]
        pa._call_llm = _fake_ok  # type: ignore[assignment]
        advice_bad = await advisor.advise_with_narrative(
            baseline_metrics={"road_capacity_index": 1000.0}, branches=branches
        )
        assert advice_bad.mode == "deterministic"
        assert advice_bad.degraded_reason == "llm_output_invalid"

        # LLM 调用抛错 → llm_error
        async def _boom(cfg, messages, tools=None):
            raise RuntimeError("network down")

        pa._call_llm = _boom  # type: ignore[assignment]
        advice_err = await advisor.advise_with_narrative(
            baseline_metrics={"road_capacity_index": 1000.0}, branches=branches
        )
        assert advice_err.mode == "deterministic"
        assert advice_err.degraded_reason == "llm_error"

        # LLM 合法输出 → mode=llm，叙述采用 LLM 文本
        valid_payload = {
            "recommended_branch_id": "A",
            "confidence": 0.8,
            "causal_chain": ["新增道路 → 通行能力提升 → 拥堵缓解"],
            "narrative": "建议采纳方案 A。",
        }

        async def _fake_valid(cfg, messages, tools=None):
            return {
                "choices": [
                    {"message": {"content": "```json\n" + json.dumps(valid_payload, ensure_ascii=False) + "\n```"}}
                ],
                "usage": {},
            }

        pa._call_llm = _fake_valid  # type: ignore[assignment]
        advice_llm = await advisor.advise_with_narrative(
            baseline_metrics={"road_capacity_index": 1000.0}, branches=branches
        )
        assert advice_llm.mode == "llm"
        assert advice_llm.degraded_reason is None
        assert advice_llm.narrative == "建议采纳方案 A。"
        assert advice_llm.rationale_causal_chain == valid_payload["causal_chain"]
    finally:
        pa._llm_available = None  # type: ignore[assignment]
        pa._call_llm = None  # type: ignore[assignment]


# ─────────────────────── T13–T14: scenario_mode 协议 ───────────────────────


def test_t13_scenario_mode_schema_contract():
    from app.lib.cartography.mapspec_schema import (
        KNOWN_VERSIONS,
        LATEST_VERSION,
        parse_mapspec,
    )

    assert "1.3" in KNOWN_VERSIONS
    # v1.4（ADR-0201 场景协议）后 LATEST 前移；1.3 语义不变（additive）。
    assert LATEST_VERSION == "1.4"

    doc = {
        "version": "1.2",
        "scenario_mode": "split_view",
        "view": {"center": [104.0, 30.6], "zoom": 12},
        "sources": {},
        "layers": [],
        "layout": {},
    }
    result = parse_mapspec(doc)
    assert result.migrated is True
    # 迁移目标随 LATEST 前移（1.4 后 = "1.4"）；1.2 语义 additive 不变。
    assert result.effective_version == LATEST_VERSION
    assert result.valid is True
    # 已知字段不再产生 unknown 披露
    assert all(d.path != "scenario_mode" for d in result.disclosures)
    assert result.document["scenario_mode"] == "split_view"

    # 非法值 → invalid 披露，valid=False（绝不静默 coerce）
    bad = dict(doc)
    bad["scenario_mode"] = "fancy_3d"
    bad_result = parse_mapspec(bad)
    assert bad_result.valid is False
    assert any(
        d.path == "scenario_mode" and d.kind == "invalid"
        for d in bad_result.invalid_fields
    )

    # 缺省 None = 非推演视图；旧 1.0 文档语义不变
    plain = {
        "version": "1.0",
        "view": {"center": [104.0, 30.6], "zoom": 12},
        "sources": {},
        "layers": [],
        "layout": {},
    }
    plain_result = parse_mapspec(plain)
    assert plain_result.valid is True
    assert "scenario_mode" not in plain_result.document


@pytest.mark.asyncio
async def test_t14_set_scenario_mode_intent_transactional():
    from app.lib.cartography.mapspec_schema import parse_mapspec
    from app.services.gis_world_state import apply_gis_mutation
    from app.services.mapspec.lifecycle_engine import SetScenarioModeIntent

    parent = _sid("wif-t14")
    await _seed_baseline_world(parent)

    # 合法写入 → 顶层字段落地
    res = await apply_gis_mutation(
        parent, SetScenarioModeIntent(scenario_mode="swipe_compare"),
        origin="agent", actor="whatif",
    )
    assert not res.is_error, res.error_msg
    spec = await mapspec_store_instance.get_mapspec(parent)
    assert spec["scenario_mode"] == "swipe_compare"
    # 冷路径解析合法
    parsed = parse_mapspec(spec)
    assert parsed.valid is True

    # 非法值 → is_error，last-known-good 不变
    bad = await apply_gis_mutation(
        parent, SetScenarioModeIntent(scenario_mode="hologram"),
        origin="agent", actor="whatif",
    )
    assert bad.is_error
    spec2 = await mapspec_store_instance.get_mapspec(parent)
    assert spec2["scenario_mode"] == "swipe_compare"

    # None = 退出推演模式（键被清除）
    off = await apply_gis_mutation(
        parent, SetScenarioModeIntent(scenario_mode=None),
        origin="agent", actor="whatif",
    )
    assert not off.is_error
    spec3 = await mapspec_store_instance.get_mapspec(parent)
    assert "scenario_mode" not in spec3


@pytest.mark.asyncio
async def test_t15_compare_branches_end_to_end_wires_matrix_and_report():
    """管理面端到端：fork → 干预 → compare_branches → 专报。"""
    parent = _sid("wif-t15")
    await _seed_baseline_world(parent)
    mgr = ScenarioBranchManager()
    await mgr.create_branch(parent, "A", hypothesis="新增诊所覆盖如何变化？")
    await mgr.create_branch(parent, "B", hypothesis="新增公园覆盖如何变化？")

    # A：新增一个设施（cell-b 被纳入 800m 覆盖）
    res_a = await mgr.apply_intervention(
        parent,
        "A",
        [
            _layer(
                "facility-proposal-a",
                "facility-proposal-a",
                _point_fc([("clinic-2", 104.0, 30.585, {"kind": "clinic"})]),
            )
        ],
    )
    assert not res_a[0].is_error
    # B：新增一块绿地
    res_b = await mgr.apply_intervention(
        parent,
        "B",
        [
            _layer(
                "park-proposal-b",
                "park-proposal-b",
                _poly_fc([("park-2", 104.02, 30.6, 0.001, {"kind": "park"})]),
            )
        ],
    )
    assert not res_b[0].is_error

    comparison = await mgr.compare_branches(parent, ["A", "B"])
    # 基线锚点
    assert comparison.baseline_revision >= 4  # 播种 4 层
    assert comparison.baseline_fingerprint
    # 矩阵含基线列与两分支列
    cov_row = comparison.metric_matrix["service_coverage_population"]
    assert cov_row["baseline"] == pytest.approx(100.0)
    assert cov_row["A"] == pytest.approx(400.0)  # clinic-2 拉入 cell-b（300 人）
    green_row = comparison.metric_matrix["green_area_m2"]
    assert green_row["B"] > green_row["baseline"]
    # 处方建议已生成（确定性核）
    assert comparison.advice.recommended_branch_id in {"A", "B"}
    # 专报可渲染且含两方案假设
    report = build_comparison_report(comparison)
    md = render_comparison_markdown(report)
    assert "新增诊所" in md and "新增公园" in md
    # 对比图层就绪（红绿渲染契约）
    overlay = build_diff_overlay(comparison.branches)
    assert overlay["type"] == "FeatureCollection"
    assert overlay["features"], "至少包含分支新增要素"
    sign_values = {f["properties"].get("impact_sign") for f in overlay["features"]}
    assert sign_values <= {"positive", "negative", "neutral"}
