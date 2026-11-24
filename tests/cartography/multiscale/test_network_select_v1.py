"""network_select 引擎测试 —— 断网修复 oracle / 硬约束 / 诚实披露。

场景 oracle（手工构造路网）：
- 主干 A—B 与 B—C 保留、联络 B—C' 被抑制 → 桥接重加恢复连通；
- max_bridges 预算尽 → 诚实披露断连（不假装连通）；
- mandatory user-wins；孤立要素不影响连通性语义。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.lib.cartography.multiscale.network_select import (
    select_lines_with_connectivity,
)


def _line(fid: str, coords: List[List[float]], road_class: Optional[str],
          ) -> Dict[str, Any]:
    props: Dict[str, Any] = {}
    if road_class is not None:
        props["道路等级"] = road_class
    return {
        "type": "Feature", "id": fid,
        "geometry": {"type": "LineString", "coordinates": coords},
        "properties": props,
    }


def _corridor_network() -> List[Dict[str, Any]]:
    """A—(高速)—B—(县道联络)—C—(高速)—D 主走廊 + 断网制造：联络段降级。

    节点：A(0,0) B(1,0) C(2,0) D(3,0)；B—C 之间的直达联络线为乡道。
    kept = {高速公路}：保留 A—B、C—D；乡道 B—C 被抑制 → 断网 → 必须桥接。
    """
    return [
        _line("exp1", [[0, 0], [1, 0]], "高速公路"),
        _line("link", [[1, 0], [2, 0]], "乡道"),
        _line("exp2", [[2, 0], [3, 0]], "高速公路"),
    ]


class TestConnectivityRepair:
    def test_bridge_restores_connectivity(self):
        out = select_lines_with_connectivity(
            _corridor_network(), importance_field="道路等级",
            kept_values=["高速公路"])
        ids = [f["id"] for f in out.feature_collection["features"]]
        assert set(ids) == {"exp1", "exp2", "link"}, "联络段必须被桥接重加"
        assert out.receipt["fully_connected"] is True
        assert out.receipt["components_before"] == 2
        assert out.receipt["components_final"] == 1
        assert out.receipt["bridges_added"] == 1
        assert out.receipt["bridges"][0]["feature_id"] == "link"
        assert out.receipt["bridges"][0]["reason"] == "connectivity_bridge"
        assert any("连通性桥接" in d for d in out.receipt["disclosures"])

    def test_budget_exhausted_discloses_disconnection(self):
        out = select_lines_with_connectivity(
            _corridor_network(), importance_field="道路等级",
            kept_values=["高速公路"], max_bridges=0)
        ids = [f["id"] for f in out.feature_collection["features"]]
        assert set(ids) == {"exp1", "exp2"}
        assert out.receipt["fully_connected"] is False
        assert out.receipt["components_final"] == 2
        assert any("互不连通分量" in d for d in out.receipt["disclosures"])

    def test_highway_plus_link_no_bridge_needed(self):
        # 等级足够时全部保留：无需桥接、恒连通
        out = select_lines_with_connectivity(
            _corridor_network(), importance_field="道路等级",
            kept_values=["高速公路", "乡道"])
        assert out.receipt["bridges_added"] == 0
        assert out.receipt["fully_connected"] is True
        assert out.receipt["suppressed_count"] == 0

    def test_detour_bridge_via_intermediate_suppressed_segment(self):
        # 直达联络被抑制，但经由两段被抑制次干道可绕行连通 → 桥接 2 段
        feats = [
            _line("h1", [[0, 0], [1, 0]], "高速公路"),
            _line("h2", [[3, 0], [4, 0]], "高速公路"),
            _line("s1", [[1, 0], [2, 0]], "县道"),
            _line("s2", [[2, 0], [3, 0]], "县道"),
        ]
        out = select_lines_with_connectivity(
            feats, importance_field="道路等级", kept_values=["高速公路"])
        ids = set(f["id"] for f in out.feature_collection["features"])
        assert ids == {"h1", "h2", "s1", "s2"}
        assert out.receipt["bridges_added"] == 2
        assert out.receipt["fully_connected"] is True


class TestSelectionSemantics:
    def test_mandatory_user_wins(self):
        feats = [
            _line("h1", [[0, 0], [1, 0]], "高速公路"),
            _line("t1", [[1, 0], [1, 1]], "村道"),
        ]
        out = select_lines_with_connectivity(
            feats, importance_field="道路等级", kept_values=["高速公路"],
            mandatory_ids=frozenset({"t1"}))
        ids = [f["id"] for f in out.feature_collection["features"]]
        assert set(ids) == {"h1", "t1"}
        assert out.receipt["mandatory_saved"] == 1
        # t1 端点 (1,0) 与 h1 端点共享 → h1+t1 一个分量
        assert out.receipt["components_final"] == 1

    def test_unclassified_kept(self):
        feats = [
            _line("h1", [[0, 0], [1, 0]], "高速公路"),
            _line("unk", [[1, 0], [2, 0]], None),
        ]
        out = select_lines_with_connectivity(
            feats, importance_field="道路等级", kept_values=["高速公路"])
        ids = [f["id"] for f in out.feature_collection["features"]]
        assert set(ids) == {"h1", "unk"}
        assert out.receipt["unclassified_kept"] == 1

    def test_numeric_level_normalization(self):
        # 数值等级与字符串词表同义（generalize 同语义）：a2（等级 4）被
        # 抑制 → a1/a3 分裂为两个分量 → 连通性桥接按最短路径重加 a2
        feats = [
            _line("a1", [[0, 0], [1, 0]], 2),
            _line("a2", [[1, 0], [2, 0]], 4),
            _line("a3", [[2, 0], [3, 0]], 2),
        ]
        out = select_lines_with_connectivity(
            feats, importance_field="admin_level", kept_values=["2"])
        ids = set(f["id"] for f in out.feature_collection["features"])
        assert out.receipt["bridges_added"] == 1
        assert out.receipt["bridges"][0]["feature_id"] == "a2"
        assert out.receipt["bridges"][0]["reason"] == "connectivity_bridge"
        assert "a2" in ids and "a1" in ids and "a3" in ids
        assert out.receipt["fully_connected"] is True

    def test_non_line_passthrough(self):
        pt = {"type": "Feature", "id": "p1",
              "geometry": {"type": "Point", "coordinates": [1.0, 1.0]},
              "properties": {"道路等级": "乡道"}}
        out = select_lines_with_connectivity(
            [pt, _line("h1", [[0, 0], [1, 0]], "高速公路")],
            importance_field="道路等级", kept_values=["高速公路"])
        assert out.feature_collection["features"][0]["id"] == "p1"
        assert out.receipt["parse_failures"] == 1

    def test_junction_at_intermediate_vertex(self):
        # T 型交会于中间顶点（非端点）：干线的中间点也是共享节点
        feats = [
            _line("main", [[0, 0], [1, 0], [2, 0]], "高速公路"),
            _line("spur", [[1, 0], [1, 1]], "乡道"),
        ]
        out = select_lines_with_connectivity(
            feats, importance_field="道路等级", kept_values=["高速公路"])
        # spur 被抑制；main 单连通 → 无桥接必要（spur 不在保留子网上）
        ids = [f["id"] for f in out.feature_collection["features"]]
        assert ids == ["main"]
        assert out.receipt["junction_nodes"] >= 1
        assert out.receipt["fully_connected"] is True

    def test_determinism(self):
        o1 = select_lines_with_connectivity(
            _corridor_network(), importance_field="道路等级",
            kept_values=["高速公路"])
        o2 = select_lines_with_connectivity(
            _corridor_network(), importance_field="道路等级",
            kept_values=["高速公路"])
        assert o1.receipt == o2.receipt
        assert o1.feature_collection == o2.feature_collection

    def test_empty_input(self):
        out = select_lines_with_connectivity(
            [], importance_field="道路等级", kept_values=["高速公路"])
        assert out.feature_collection["features"] == []
        assert out.receipt["stats_match"] is True
