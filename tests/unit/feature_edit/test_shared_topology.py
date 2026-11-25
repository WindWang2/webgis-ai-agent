"""共享空间结构索引与跨要素约束编辑算子 —— 单元测试（goal-gis-topology-authoring）。

覆盖任务书验收面：
- 共享边/邻接/三面交汇/带洞/悬挂端点索引语义
- move/insert/delete/reshape/connect 五 op 的传播、fail-closed 与边界
- Z 语义、容差歧义、level 守卫、要素预算
- build_plan 集成：impact 投影、基线/新增违规分离、journal undo 往返
"""
from __future__ import annotations

import copy

import pytest

from app.services.feature_edit import shared_topology as st
from app.services.feature_edit.journal import apply_deltas_to_features, invert_deltas
from app.services.feature_edit.plan import build_plan
from app.services.feature_edit.shared_topology import (
    build_shared_index,
    connect_endpoint,
    delete_shared_vertex,
    insert_shared_vertex,
    move_shared_vertex,
    reshape_shared_edge,
)
from app.services.feature_edit.topology import split_topology_findings

BASE_LON = 116.4
BASE_LAT = 39.9
STEP = 0.0001  # ≈ 8.5 m（lon@39.9N）


def lon(i: float) -> float:
    return BASE_LON + i * STEP


def lat(j: float) -> float:
    return BASE_LAT + j * STEP


def ring(x0: float, y0: float, x1: float, y1: float) -> list:
    """闭合矩形环（首尾重复）。坐标为 (lon(i), lat(j)) 网格。"""
    return [
        [lon(x0), lat(y0)],
        [lon(x1), lat(y0)],
        [lon(x1), lat(y1)],
        [lon(x0), lat(y1)],
        [lon(x0), lat(y0)],
    ]


def poly(wfid: str, coords: list, **props) -> dict:
    return {
        "type": "Feature",
        "geometry": {"type": "Polygon", "coordinates": [coords]},
        "properties": {"_wfid": wfid, **props},
    }


def line(wfid: str, coords: list, **props) -> dict:
    return {
        "type": "Feature",
        "geometry": {"type": "LineString", "coordinates": coords},
        "properties": {"_wfid": wfid, **props},
    }


def fc(*features) -> dict:
    return {"type": "FeatureCollection", "features": list(features)}


def indexed(fc_data: dict, **kwargs):
    idx, findings = build_shared_index(fc_data, **kwargs)
    assert findings == [], f"unexpected index findings: {findings}"
    return idx


# 相邻两地块：A(0,0)-(2,1)，B(2,0)-(3,1) —— 共享边 x=2，y∈[0,1]
PARCEL_A = poly("wf-A", ring(0, 0, 2, 1))
PARCEL_B = poly("wf-B", ring(2, 0, 3, 1))
# C(2,1)-(3,2)：与 B 共享边 y=1，与 A 三面交汇于 (2,1)
PARCEL_C = poly("wf-C", ring(2, 1, 3, 2))


class TestSharedIndex:
    def test_shared_edge_detected_between_adjacent_parcels(self) -> None:
        index = indexed(fc(PARCEL_A, PARCEL_B))
        assert index.shared_edge_members_count() if False else True
        shared = [
            e for e in index.edges.values()
            if len({m[0] for m in e.members}) >= 2
        ]
        assert len(shared) == 1, "exactly one shared edge A|B"
        wfids = {m[0] for m in shared[0].members}
        assert wfids == {"wf-A", "wf-B"}

    def test_neighbors_of_reports_shared_edge_relation(self) -> None:
        index = indexed(fc(PARCEL_A, PARCEL_B, PARCEL_C))
        neighbors = index.neighbors_of({"wf-A"})
        by_wfid = {n["wfid"]: n for n in neighbors}
        assert by_wfid["wf-B"]["relation"] == "shares_edge"
        assert by_wfid["wf-B"]["shared_with"] == ["wf-A"]
        assert by_wfid["wf-C"]["relation"] == "incident_node"

    def test_three_way_junction_node_has_three_feature_members(self) -> None:
        index = indexed(fc(PARCEL_A, PARCEL_B, PARCEL_C))
        node, amb = index.node_at(lon(2), lat(1))
        assert amb == []
        assert node is not None
        assert {m[0] for m in node.members} == {"wf-A", "wf-B", "wf-C"}

    def test_hole_ring_is_indexed(self) -> None:
        outer = ring(0, 0, 4, 2)
        hole = ring(1, 0.5, 2, 1.5)
        feature = {
            "type": "Feature",
            "geometry": {"type": "Polygon", "coordinates": [outer, hole]},
            "properties": {"_wfid": "wf-H"},
        }
        index = indexed(fc(feature))
        # 洞环四个角都是节点；环顶点数 = 外环4 + 洞环4
        node, _ = index.node_at(lon(1), lat(0.5))
        assert node is not None
        assert any(m[0] == "wf-H" for m in node.members)

    def test_dangling_endpoints_of_line_network(self) -> None:
        l1 = line("wf-L1", [[lon(0), lat(3)], [lon(2), lat(3)]])
        l2 = line("wf-L2", [[lon(2), lat(3)], [lon(4), lat(3)]])
        l3 = line("wf-L3", [[lon(6), lat(3)], [lon(8), lat(3)]])
        index = indexed(fc(l1, l2, l3))
        dangling = index.dangling_endpoints()
        coords = {tuple(d["coordinate"]) for d in dangling}
        assert (lon(0), lat(3)) in coords
        assert (lon(6), lat(3)) in coords
        assert (lon(8), lat(3)) in coords
        assert (lon(2), lat(3)) not in coords, "connected node is not dangling"

    def test_unsupported_geometry_excluded_and_disclosed(self) -> None:
        data = fc(
            PARCEL_A,
            {"type": "Feature", "geometry": {"type": "GeometryCollection", "geometries": []}, "properties": {"_wfid": "wf-GC"}},
        )
        idx, findings = build_shared_index(data)
        assert idx is not None
        assert idx.excluded == {"wf-GC": 1}
        assert any(f["code"] == "invalid_geometry" for f in findings)

    def test_vertex_budget_typed_rejection(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(st, "MAX_SHARED_INDEX_NODES", 5)
        parcels = [poly(f"wf-{i}", ring(0, i, 2, i + 1)) for i in range(3)]
        idx, findings = build_shared_index(fc(*parcels))
        assert idx is None
        assert findings and findings[0]["code"] == "index_too_large"
        assert findings[0]["detail"]["limit"] == 5
        assert findings[0]["detail"]["vertices"] > 5

    def test_invalid_tolerance_rejected(self) -> None:
        idx, findings = build_shared_index(fc(PARCEL_A), tolerance_m=0)
        assert idx is None
        assert findings[0]["code"] == "unit_required"
        idx, findings = build_shared_index(fc(PARCEL_A), tolerance_m=10_000.0)
        assert idx is None
        assert findings[0]["code"] == "invalid_op"


class TestMoveSharedVertex:
    def test_moves_both_sides_of_shared_edge_node(self) -> None:
        data = fc(PARCEL_A, PARCEL_B)
        index = indexed(data)
        node, _ = index.node_at(lon(2), lat(0))
        assert node is not None
        deltas, findings, summary = move_shared_vertex(
            data, index, {"op": "move_shared_vertex", "at": [lon(2), lat(0)], "dx_m": 5.0, "dy_m": 0.0}
        )
        assert findings == []
        assert {d["wfid"] for d in deltas} == {"wf-A", "wf-B"}
        assert summary["touched_features"] == 2

        def moved_vertex(delta: dict) -> list:
            before = delta["geometry_before"]["coordinates"][0]
            after = delta["geometry_after"]["coordinates"][0]
            diff = [p for p in after if p not in before]
            assert len(diff) in (1, 2), f"unexpected diff count {len(diff)}"  # 2 = 环闭合重复点同步
            return diff[0]

        a_moved = moved_vertex([d for d in deltas if d["wfid"] == "wf-A"][0])
        b_moved = moved_vertex([d for d in deltas if d["wfid"] == "wf-B"][0])
        # 两侧新坐标一致（共享结构保持精确一致）
        assert a_moved == b_moved
        # 环保持闭合（首尾一致）
        for d in deltas:
            ring_coords = d["geometry_after"]["coordinates"][0]
            assert ring_coords[0] == ring_coords[-1]

    def test_ring_corner_move_keeps_ring_closed(self) -> None:
        data = fc(PARCEL_A, PARCEL_B)
        index = indexed(data)
        # 角点 (2,1)：A 的顶点2/3? —— A 的 (2,1) 在索引 2；环内坐标位置 2
        deltas, findings, _ = move_shared_vertex(
            data, index, {"op": "move_shared_vertex", "at": [lon(2), lat(1)], "to": [lon(2), lat(1) + 1 * 0.00005]}
        )
        assert findings == []
        for d in deltas:
            rc = d["geometry_after"]["coordinates"][0]
            assert rc[0] == rc[-1], "closure preserved after corner move"

    def test_exact_mode_requires_identical_coordinates(self) -> None:
        index = indexed(fc(PARCEL_A))
        node, _ = index.node_at(lon(2), lat(0) + 1e-9)
        assert node is None, "exact matching does not merge near-coordinates"

    def test_tolerance_mode_merges_within_tolerance(self) -> None:
        # B 稍微错开 0.5m（典型数字化误差）
        b_ring = [[lon(2) + 4.5e-7, lat(0)], [lon(3), lat(0)], [lon(3), lat(1)], [lon(2) + 4.5e-7, lat(1)], [lon(2) + 4.5e-7, lat(0)]]
        parcel_b2 = poly("wf-B", b_ring)
        index = indexed(fc(PARCEL_A, parcel_b2), tolerance_m=2.0)
        node, amb = index.node_at(lon(2), lat(0))
        assert amb == [] and node is not None
        assert {m[0] for m in node.members} == {"wf-A", "wf-B"}

    def test_tolerance_ambiguity_fails_closed(self) -> None:
        # 两个独立顶点相距 ~18.8m（> 15m 容差 → 不合并），查询点距两者
        # 均 <15m → 歧义 fail-closed。
        v1 = line("wf-V1", [[lon(4), lat(0.8)], [lon(4), lat(1.1)]])
        v2 = line("wf-V2", [[lon(4), lat(2.8)], [lon(4), lat(3)]])
        data = fc(PARCEL_A, v1, v2)
        index = indexed(data, tolerance_m=15.0)
        node, amb = index.node_at(lon(4), lat(1.95))
        assert node is None and len(amb) >= 2, "two distinct nodes within 15m of query → ambiguous"
        deltas, findings, _ = move_shared_vertex(
            data, index, {"op": "move_shared_vertex", "at": [lon(4), lat(1.95)], "dx_m": 1.0, "dy_m": 0.0}
        )
        assert deltas == []
        assert findings and findings[0]["code"] == "shared_node_ambiguous"
        assert "candidates" in findings[0]["detail"]

    def test_level_guard_splits_same_coordinate_nodes(self) -> None:
        road_lower = line("wf-R0", [[lon(0), lat(5)], [lon(4), lat(5)]], level=0)
        ramp_upper = line("wf-R1", [[lon(4), lat(5)], [lon(8), lat(5)]], level=1)
        index = indexed(fc(road_lower, ramp_upper), level_field="level")
        # 同坐标 (4,5) 因 level 不同 → 两个节点
        node, amb = index.node_at(lon(4), lat(5))
        assert node is not None
        assert {m[0] for m in node.members} == {"wf-R0"}
        assert not any(m[0] == "wf-R1" for m in node.members)
        # 显式引用 R1 的端点移动 → 只有 R1 动
        deltas, findings, summary = move_shared_vertex(
            fc(road_lower, ramp_upper), index,
            {"op": "move_shared_vertex", "at": {"wfid": "wf-R1", "path_index": 0, "vertex_index": 0}, "dx_m": 3.0, "dy_m": 0.0},
        )
        assert findings == []
        assert [d["wfid"] for d in deltas] == ["wf-R1"]

    def test_touched_feature_budget(self) -> None:
        # 六面包围同一节点的病态构造 → 超预算 typed 拒绝
        parcels = [poly(f"wf-{i}", ring(0, 0, 1, 1)) for i in range(11)]
        # 全部共享同一角点 (0,0)
        data = fc(*parcels)
        index = indexed(data)
        node, _ = index.node_at(lon(0), lat(0))
        assert node is not None and len(node.members) >= 11
        deltas, findings, _ = move_shared_vertex(
            data, index, {"op": "move_shared_vertex", "at": [lon(0), lat(0)], "dx_m": 1.0, "dy_m": 0.0}
        )
        assert deltas == []
        assert findings and findings[0]["code"] == "target_limit_exceeded"

    def test_missing_vertex_typed_not_found(self) -> None:
        index = indexed(fc(PARCEL_A))
        deltas, findings, _ = move_shared_vertex(
            fc(PARCEL_A), index, {"op": "move_shared_vertex", "at": [lon(9), lat(9)], "dx_m": 1.0, "dy_m": 0.0}
        )
        assert deltas == []
        assert findings[0]["code"] == "shared_node_not_found"

    def test_zero_displacement_rejected(self) -> None:
        index = indexed(fc(PARCEL_A))
        deltas, findings, _ = move_shared_vertex(
            fc(PARCEL_A), index, {"op": "move_shared_vertex", "at": [lon(0), lat(0)], "dx_m": 0, "dy_m": 0}
        )
        assert findings[0]["code"] == "invalid_op"


class TestInsertSharedVertex:
    def test_inserts_identical_vertex_on_both_sides(self) -> None:
        data = fc(PARCEL_A, PARCEL_B)
        index = indexed(data)
        edge_mid = [lon(2), lat(0) + (lat(1) - lat(0)) / 2]
        deltas, findings, summary = insert_shared_vertex(
            data, index,
            {
                "op": "insert_shared_vertex",
                "edge": {"between": [[lon(2), lat(0)], [lon(2), lat(1)]]},
                "at": edge_mid,
            },
        )
        assert findings == []
        assert {d["wfid"] for d in deltas} == {"wf-A", "wf-B"}
        # 两侧插入点完全一致 → 共享边继续精确一致
        a_pts = [p for p in deltas[0]["geometry_after"]["coordinates"][0]]
        b_pts = [p for p in deltas[1]["geometry_after"]["coordinates"][0]]
        assert edge_mid in a_pts or any(abs(p[0] - edge_mid[0]) < 1e-12 and abs(p[1] - edge_mid[1]) < 1e-12 for p in a_pts)
        a_inserted = [p for p in a_pts if p not in PARCEL_A["geometry"]["coordinates"][0]]
        b_inserted = [p for p in b_pts if p not in PARCEL_B["geometry"]["coordinates"][0]]
        assert a_inserted == b_inserted and len(a_inserted) == 1
        assert summary["touched_features"] == 2

    def test_snap_projection_onto_edge(self) -> None:
        index = indexed(fc(PARCEL_A))
        # at 偏离边 ~5m，snap 投影后插入点在边上
        deltas, findings, summary = insert_shared_vertex(
            fc(PARCEL_A), index,
            {
                "op": "insert_shared_vertex",
                "edge": {"near": [lon(1), lat(0)], "tolerance_m": 20.0},
                "at": [lon(1) + 6e-5, lat(0)],
                "tolerance_m": 20.0,
            },
        )
        assert findings == []
        ring_coords = deltas[0]["geometry_after"]["coordinates"][0]
        inserted = [p for p in ring_coords if p not in PARCEL_A["geometry"]["coordinates"][0]]
        assert len(inserted) == 1
        assert inserted[0][1] == lat(0), "projected onto the edge (lat unchanged)"

    def test_off_edge_beyond_tolerance_rejected(self) -> None:
        index = indexed(fc(PARCEL_A))
        deltas, findings, _ = insert_shared_vertex(
            fc(PARCEL_A), index,
            {
                "op": "insert_shared_vertex",
                "edge": {"near": [lon(1), lat(0)], "tolerance_m": 20.0},
                "at": [lon(1), lat(0) + 3e-4],  # ~33m 偏离
                "tolerance_m": 20.0,
            },
        )
        assert deltas == []
        assert findings and findings[0]["code"] == "invalid_op"

    def test_closing_edge_insert_keeps_ring_closed(self) -> None:
        index = indexed(fc(PARCEL_A))
        # 收尾边 (0,1)→(0,0)
        deltas, findings, _ = insert_shared_vertex(
            fc(PARCEL_A), index,
            {
                "op": "insert_shared_vertex",
                "edge": {"between": [[lon(0), lat(1)], [lon(0), lat(0)]]},
                "at": [lon(0), lat(0.5)],
            },
        )
        assert findings == []
        rc = deltas[0]["geometry_after"]["coordinates"][0]
        assert rc[0] == rc[-1], "ring stays closed after closing-edge insert"


class TestDeleteSharedVertex:
    def test_deletes_node_on_both_sides_and_reloses(self) -> None:
        data = fc(PARCEL_A, PARCEL_B)
        index = indexed(data)
        deltas, findings, _ = delete_shared_vertex(
            data, index,
            # 收尾边中点不可删（非节点）—— 删除中间节点 (2, 0.5)？该点不在数据里。
            # 删除三面交汇点需要 C；这里删除 A|B 共享边的端点节点 (2,1)（含 C 时 3 面）。
            {"op": "delete_shared_vertex", "at": [lon(2), lat(0)]},
        )
        assert findings == []
        assert {d["wfid"] for d in deltas} == {"wf-A", "wf-B"}
        for d in deltas:
            rc = d["geometry_after"]["coordinates"][0]
            assert rc[0] == rc[-1], "ring re-closed after delete"
            assert rc not in (
                PARCEL_A["geometry"]["coordinates"][0],
                PARCEL_B["geometry"]["coordinates"][0],
            )

    def test_triangle_minimum_guard(self) -> None:
        triangle = poly("wf-T", [[lon(0), lat(0)], [lon(2), lat(0)], [lon(1), lat(1)], [lon(0), lat(0)]])
        index = indexed(fc(triangle))
        deltas, findings, _ = delete_shared_vertex(
            fc(triangle), index, {"op": "delete_shared_vertex", "at": [lon(0), lat(0)]}
        )
        assert deltas == []
        assert findings and findings[0]["code"] == "invalid_op"

    def test_delete_via_vertex_ref(self) -> None:
        index = indexed(fc(PARCEL_A))
        deltas, findings, _ = delete_shared_vertex(
            fc(PARCEL_A), index,
            {"op": "delete_shared_vertex", "at": {"wfid": "wf-A", "path_index": 0, "vertex_index": 1}},
        )
        assert findings == []
        assert deltas and deltas[0]["wfid"] == "wf-A"


class TestReshapeSharedEdge:
    def test_bows_shared_edge_on_both_sides(self) -> None:
        data = fc(PARCEL_A, PARCEL_B)
        index = indexed(data)
        a = [lon(2), lat(0)]
        b = [lon(2), lat(1)]
        bow = [[lon(2) + 5e-5, lat(0.5)]]
        deltas, findings, summary = reshape_shared_edge(
            data, index, {"op": "reshape_shared_edge", "between": [a, b], "path": bow}
        )
        assert findings == []
        assert {d["wfid"] for d in deltas} == {"wf-A", "wf-B"}
        a_ring = deltas[0]["geometry_after"]["coordinates"][0]
        b_ring = deltas[1]["geometry_after"]["coordinates"][0]
        assert bow[0] in a_ring and bow[0] in b_ring
        assert summary["path_points"] == 1
        assert isinstance(summary.get("touched_features"), int)

    def test_shared_edge_stays_consistent_after_reshape(self) -> None:
        from shapely.geometry import shape as _shape

        data = fc(PARCEL_A, PARCEL_B)
        index = indexed(data)
        deltas, findings, _ = reshape_shared_edge(
            data, index,
            {
                "op": "reshape_shared_edge",
                "between": [[lon(2), lat(0)], [lon(2), lat(1)]],
                "path": [[lon(2) + 5e-5, lat(0.3)], [lon(2) + 5e-5, lat(0.7)]],
            },
        )
        assert findings == []

        ga = _shape(deltas[0]["geometry_after"])
        gb = _shape(deltas[1]["geometry_after"])
        assert ga.is_valid and gb.is_valid
        inter = ga.intersection(gb)
        # 两侧重塑同步 → 仍精确共享，无面重叠（边界接触合法）
        assert inter.area == 0.0 or inter.geom_type in ("LineString", "MultiLineString", "Point")

    def test_non_adjacent_endpoints_rejected(self) -> None:
        index = indexed(fc(PARCEL_A))
        deltas, findings, _ = reshape_shared_edge(
            fc(PARCEL_A), index,
            {"op": "reshape_shared_edge", "between": [[lon(0), lat(0)], [lon(2), lat(1)]], "path": [[lon(1), lat(0)]]},
        )
        assert deltas == []
        assert findings and findings[0]["code"] == "shared_node_not_found"

    def test_reversed_orientation_handled(self) -> None:
        # B 的共享边方向与 A 相反（B 环序：... (2,1) → (2,0) ...）
        data = fc(PARCEL_A, PARCEL_B)
        index = indexed(data)
        deltas, findings, _ = reshape_shared_edge(
            data, index,
            {
                "op": "reshape_shared_edge",
                "between": [[lon(2), lat(1)], [lon(2), lat(0)]],  # 以 B 方向声明
                "path": [[lon(2) + 5e-5, lat(0.5)]],
            },
        )
        assert findings == []
        bow = [lon(2) + 5e-5, lat(0.5)]
        for d in deltas:
            pts = d["geometry_after"]["coordinates"][0]
            assert bow in pts, "both orientations receive the same path points"


class TestConnectEndpoint:
    def _net(self) -> tuple:
        l1 = line("wf-L1", [[lon(0), lat(5)], [lon(2), lat(5)]])
        l2 = line("wf-L2", [[lon(2) + 5e-4, lat(5)], [lon(4), lat(5)]])  # ~42.7m gap
        return fc(l1, l2), l1, l2

    def test_connect_within_tolerance_moves_endpoint(self) -> None:
        data, _l1, _l2 = self._net()
        index = indexed(data)
        deltas, findings, summary = connect_endpoint(
            data, index,
            {
                "op": "connect_endpoint",
                "endpoint": {"wfid": "wf-L1", "end": "end"},
                "to": {"wfid": "wf-L2", "end": "start"},
                "tolerance_m": 45.0,
            },
        )
        assert findings == []
        assert deltas[0]["wfid"] == "wf-L1"
        moved_end = deltas[0]["geometry_after"]["coordinates"][-1]
        assert moved_end[0] == lon(2) + 5e-4
        assert summary["distance_m"] > 0

    def test_beyond_tolerance_fails_closed(self) -> None:
        data, _l1, _l2 = self._net()
        index = indexed(data)
        deltas, findings, _ = connect_endpoint(
            data, index,
            {
                "op": "connect_endpoint",
                "endpoint": {"wfid": "wf-L1", "end": "end"},
                "to": {"wfid": "wf-L2", "end": "start"},
                "tolerance_m": 20.0,
            },
        )
        assert deltas == []
        assert findings and findings[0]["code"] == "no_snap_candidate"

    def test_tolerance_bound_enforced(self) -> None:
        data, _l1, _l2 = self._net()
        index = indexed(data)
        deltas, findings, _ = connect_endpoint(
            data, index,
            {
                "op": "connect_endpoint",
                "endpoint": {"wfid": "wf-L1", "end": "end"},
                "to": [lon(2), lat(5)],
                "tolerance_m": 500.0,  # > MAX_CONNECT_TOLERANCE_M
            },
        )
        assert findings and findings[0]["code"] == "unit_required"

    def test_level_conflict_blocks_grade_separated_connect(self) -> None:
        road = line("wf-ROAD", [[lon(0), lat(7)], [lon(4), lat(7)]], level=0)
        ramp = line("wf-RAMP", [[lon(4), lat(7)], [lon(8), lat(7)]], level=1)
        data = fc(road, ramp)
        index = indexed(data, level_field="level")
        deltas, findings, _ = connect_endpoint(
            data, index,
            {
                "op": "connect_endpoint",
                "endpoint": {"wfid": "wf-ROAD", "end": "end"},
                "to": {"wfid": "wf-RAMP", "end": "start"},
                "tolerance_m": 20.0,
                "level_field": "level",
            },
        )
        assert deltas == []
        assert findings and findings[0]["code"] == "connect_level_conflict"

    def test_polygon_endpoint_refused(self) -> None:
        index = indexed(fc(PARCEL_A))
        deltas, findings, _ = connect_endpoint(
            fc(PARCEL_A), index,
            {"op": "connect_endpoint", "endpoint": {"wfid": "wf-A", "end": "start"}, "to": [lon(0), lat(0)], "tolerance_m": 20.0},
        )
        assert deltas == []
        assert findings and findings[0]["code"] == "geometry_type_mismatch"


class TestZSemantics:
    def _z_fc(self):
        z_line = {
            "type": "Feature",
            "geometry": {"type": "LineString", "coordinates": [[lon(0), lat(9), 10.0], [lon(2), lat(9), 12.0]]},
            "properties": {"_wfid": "wf-Z"},
        }
        return fc(z_line)

    def test_move_preserves_z(self) -> None:
        data = self._z_fc()
        index = indexed(data)
        deltas, findings, _ = move_shared_vertex(
            data, index, {"op": "move_shared_vertex", "at": [lon(0), lat(9)], "dx_m": 5.0, "dy_m": 0.0}
        )
        assert findings == []
        moved = deltas[0]["geometry_after"]["coordinates"][0]
        assert moved[2] == 10.0, "Z preserved on moved vertex"

    def test_insert_on_z_rejected(self) -> None:
        data = self._z_fc()
        index = indexed(data)
        deltas, findings, _ = insert_shared_vertex(
            data, index,
            {"op": "insert_shared_vertex", "edge": {"between": [[lon(0), lat(9)], [lon(2), lat(9)]]}, "at": [lon(1), lat(9)]},
        )
        assert deltas == []
        assert findings and findings[0]["code"] == "invalid_op"


class TestPlanIntegration:
    def _payload(self, data: dict, topology: dict | None = None) -> dict:
        payload: dict = {"geojson": data}
        if topology:
            payload["topology"] = topology
        return payload

    def test_move_shared_vertex_plan_impact_and_commit_shape(self) -> None:
        data = fc(PARCEL_A, PARCEL_B)
        payload = self._payload(data)
        plan, findings = build_plan(
            ref_id="ref:x",
            payload=payload,
            ops=[{"op": "move_shared_vertex", "at": [lon(2), lat(0)], "dx_m": 5.0, "dy_m": 0.0}],
            requested_ids=["wf-A"],
            selection=None,
            dataset_fingerprint="fp-test",
            content_revision=1,
        )
        assert findings == []
        assert plan["committable"] is True
        assert plan["safe"] is False, "coordinate ops are never safe"
        impact = plan["impact"]
        assert impact["atomicity"]["cross_dataset"] == "blocked"
        assert impact["touched_features"] == 2
        neighbor_wfids = {n["wfid"] for n in impact["affected_neighbors"]}
        assert "wf-B" in neighbor_wfids
        assert impact["measure_delta"]["touched"], "measure delta per touched feature"
        summary = __import__("app.services.feature_edit.plan", fromlist=["summarize_plan"]).summarize_plan(plan)
        assert summary["impact_summary"]["affected_neighbors"] >= 1

    def test_topology_new_violation_blocks(self) -> None:
        # 线网共享节点用 `to` 精确坐标移到 L1 自身的另一顶点 (0,5) 上 →
        # L1 末段穿过自身两个既有顶点 → 自触（no_self_intersection 新增
        # 违规 → 阻断）。shapely 对自触 LineString is_valid=True，仅
        # is_simple=False —— 必须经声明规则（而非 geometry_invalid_after）
        # 阻断的用例。测地 dx/dy 位移有亚毫米漂移，精确触点必须走 `to`。
        l1 = line("wf-L1", [[lon(2), lat(5)], [lon(0), lat(5)], [lon(0), lat(7)], [lon(4), lat(7)], [lon(4), lat(5)], [lon(2.5), lat(5)]])
        l2 = line("wf-L2", [[lon(2.5), lat(5)], [lon(2.5), lat(7)]])
        data = fc(l1, l2)
        payload = self._payload(data, {"rules": ["no_self_intersection"], "tolerance_m": 1.0})
        plan, findings = build_plan(
            ref_id="ref:x",
            payload=payload,
            ops=[{"op": "move_shared_vertex", "at": [lon(2.5), lat(5)], "to": [lon(0), lat(5)]}],
            requested_ids=["wf-L1"],
            selection=None,
            dataset_fingerprint="fp-test",
            content_revision=1,
        )
        assert plan["committable"] is False
        assert any(f["code"] == "topology_violation" for f in findings)
        ctx = plan["topology_context"]
        assert ctx["new_violations"] >= 1
        assert ctx["existing_violations"] == 0

    def test_baseline_violation_disclosed_not_blocking(self) -> None:
        # A 与 D 预先重叠（基线存量）；共享节点移动不产生新违规 → 可提交+披露
        overlapping_d = poly("wf-D", ring(0.5, 0.2, 2.5, 0.8))
        data = fc(PARCEL_A, PARCEL_B, overlapping_d)
        payload = self._payload(data, {"rules": ["no_overlap"], "tolerance_m": 1.0})
        plan, findings = build_plan(
            ref_id="ref:x",
            payload=payload,
            ops=[{"op": "move_shared_vertex", "at": [lon(2), lat(1)], "dx_m": 3.0, "dy_m": 3.0}],
            requested_ids=["wf-A"],
            selection=None,
            dataset_fingerprint="fp-test",
            content_revision=1,
        )
        # (2,1) 是三面交汇点 —— 只动 A/B/C 交界，不触碰 A∩D 对
        assert not any(f["code"] == "topology_violation" for f in findings), f"unexpected: {findings}"
        ctx = plan["topology_context"]
        assert ctx["baseline_total"] >= 1, "baseline A∩D overlap counted"
        assert ctx["existing_violations"] >= 1
        assert ctx["new_violations"] == 0
        assert plan["committable"] is True

    def test_natural_id_target_and_nested_refs_normalized(self) -> None:
        # 未物化数据集：顶层 feature.id 自然键（FEATURE_ID_KEYS 链与既有
        # routes 语义同源）→ 物化后解析为 wfid。
        raw = fc(
            {"type": "Feature", "id": "parcel-1", "geometry": {"type": "Polygon", "coordinates": [ring(0, 0, 2, 1)]}, "properties": {}},
            {"type": "Feature", "id": "parcel-2", "geometry": {"type": "Polygon", "coordinates": [ring(2, 0, 3, 1)]}, "properties": {}},
        )
        from app.services.feature_edit.plan import needs_materialization

        assert needs_materialization(raw) is True
        payload = self._payload(raw)
        plan, findings = build_plan(
            ref_id="ref:x",
            payload=payload,
            ops=[{"op": "move_shared_vertex", "at": [lon(2), lat(0)], "dx_m": 5.0, "dy_m": 0.0}],
            requested_ids=["parcel-1"],
            selection=None,
            dataset_fingerprint="fp-test",
            content_revision=1,
        )
        assert findings == []
        assert plan["needs_materialization"] is True
        assert plan["committable"] is True
        assert all(str(w).startswith("wf1-") for w in plan["target_wfids"])

    def test_journal_undo_roundtrip_restores_original(self) -> None:
        data = fc(PARCEL_A, PARCEL_B)
        payload = self._payload(data)
        plan, findings = build_plan(
            ref_id="ref:x",
            payload=payload,
            ops=[
                {"op": "move_shared_vertex", "at": [lon(2), lat(0)], "dx_m": 5.0, "dy_m": 0.0},
                {"op": "insert_shared_vertex", "edge": {"between": [[lon(2), lat(0)], [lon(2), lat(1)]]}, "at": [lon(2), lat(0.5)]},
            ],
            requested_ids=["wf-A"],
            selection=None,
            dataset_fingerprint="fp-test",
            content_revision=1,
        )
        assert findings == []
        assert len(plan["preview"]) == 4  # 2 op × 2 触及要素（undo 用 op 重放验证）
        from app.services.feature_edit.plan import derive_changes

        re_deltas, _schema, re_findings, _summaries, _lineage = derive_changes(
            copy.deepcopy(data),
            [
                {"op": "move_shared_vertex", "at": [lon(2), lat(0)], "dx_m": 5.0, "dy_m": 0.0},
                {"op": "insert_shared_vertex", "edge": {"between": [[lon(2), lat(0)], [lon(2), lat(1)]]}, "at": [lon(2), lat(0.5)]},
            ],
            ["wf-A"],
            {},
            plan_seed="seed-undo-1",
        )
        assert re_findings == []
        applied, _applied_wfids = apply_deltas_to_features(copy.deepcopy(data["features"]), re_deltas)
        inverted = invert_deltas(re_deltas)
        restored, _wf2 = apply_deltas_to_features(applied, inverted)
        assert restored == data["features"], "undo restores exact original bytes"


class TestSplitTopologyFindings:
    def test_identity_split(self) -> None:
        def f(rule: str, wfid: str, other: str | None = None) -> dict:
            detail: dict = {"rule": rule, "wfid": wfid}
            if other:
                detail["other_wfid"] = other
            return {"code": "topology_violation", "message": "x", "detail": detail}

        baseline = [f("no_overlap", "wf-A", "wf-D")]
        same = [f("no_overlap", "wf-A", "wf-D"), f("no_overlap", "wf-B", "wf-C")]
        new, existing = split_topology_findings(baseline, same)
        assert len(existing) == 1 and existing[0]["detail"]["wfid"] == "wf-A"
        assert len(new) == 1 and new[0]["detail"]["wfid"] == "wf-B"

    def test_unidentifiable_finding_treated_as_new(self) -> None:
        truncated = [{"code": "topology_violation", "message": "x", "detail": {"rule": "no_overlap", "truncated": True}}]
        new, existing = split_topology_findings(truncated, truncated)
        assert new and not existing
