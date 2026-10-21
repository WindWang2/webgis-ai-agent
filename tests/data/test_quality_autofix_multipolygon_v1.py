"""Data Quality autofix —— MultiPolygon 修复不得摊平部件/孔洞（#1705 回归）。

覆盖：
- ``repair_geometry`` 对「带孔洞 + 多部件」的 MultiPolygon 只做原地闭合：
  part 数、每 part 环数、孔洞坐标（除闭合点外）原样保留；
- 同样保护带孔洞的 Polygon；
- dry-run 只计数、零改动（new-ref 语义的硬前提）；
- 规则链（geometry_validity → repair_geometry）走真实 apply 路径后结构不变。
"""
from __future__ import annotations

import copy

from app.services.data_quality.autofix import (
    AutofixStep,
    apply_autofix,
    dry_run_autofix,
    plan_autofix,
)
from app.services.data_quality.engine import evaluate_payload

_REPAIR = AutofixStep(operation="repair_geometry")


def _fc(geometry: dict) -> dict:
    return {"geojson": {"type": "FeatureCollection",
                        "features": [{"type": "Feature", "geometry": geometry,
                                      "properties": {"id": "g"}}]}}


# part0 = 外环 + 孔洞环（均未闭合）；part1 = 单环（未闭合）
_UNCLOSED_MULTIPOLYGON = {
    "type": "MultiPolygon",
    "coordinates": [
        [
            [[0, 0], [0, 10], [10, 10], [10, 0]],
            [[2, 2], [2, 4], [4, 4], [4, 2]],
        ],
        [
            [[20, 20], [20, 30], [30, 30]],
        ],
    ],
}


def test_apply_repair_geometry_keeps_multipolygon_parts_and_holes():
    payload = _fc(copy.deepcopy(_UNCLOSED_MULTIPOLYGON))
    new_payload, evidence = apply_autofix(payload, [_REPAIR])

    assert evidence["changed"]
    assert evidence["operations"][0]["closed_rings"] == 3
    geom = new_payload["geojson"]["features"][0]["geometry"]
    assert geom["type"] == "MultiPolygon"
    # 部件分组保持：仍是 2 个 part，每 part 的环数不变（孔洞没被摊成实心面）
    assert len(geom["coordinates"]) == 2
    assert [len(part) for part in geom["coordinates"]] == [2, 1]
    # 孔洞环原样保留（仅追加闭合点），且仍嵌套在 part0 内
    hole = geom["coordinates"][0][1]
    assert hole[:-1] == [[2.0, 2.0], [2.0, 4.0], [4.0, 4.0], [4.0, 2.0]]
    assert hole[0] == hole[-1], "孔洞环也必须被闭合"
    # 外环闭合，part1 环闭合
    outer = geom["coordinates"][0][0]
    assert outer[0] == outer[-1]
    assert geom["coordinates"][1][0][0] == geom["coordinates"][1][0][-1]
    # new-ref 语义：输入载荷不被覆写
    assert payload["geojson"]["features"][0]["geometry"]["coordinates"] == \
        _UNCLOSED_MULTIPOLYGON["coordinates"]


def test_apply_repair_geometry_keeps_polygon_hole():
    polygon = {"type": "Polygon", "coordinates": [
        [[0, 0], [0, 5], [5, 5], [5, 0]],
        [[1, 1], [1, 2], [2, 2], [2, 1]],
    ]}
    new_payload, _ = apply_autofix(_fc(copy.deepcopy(polygon)), [_REPAIR])
    coords = new_payload["geojson"]["features"][0]["geometry"]["coordinates"]
    assert len(coords) == 2, "Polygon 孔洞不得丢失"
    assert coords[1][:-1] == [[1.0, 1.0], [1.0, 2.0], [2.0, 2.0], [2.0, 1.0]]
    assert all(ring[0] == ring[-1] for ring in coords)


def test_dry_run_counts_rings_without_mutating():
    snapshot = copy.deepcopy(_UNCLOSED_MULTIPOLYGON)
    payload = _fc(_UNCLOSED_MULTIPOLYGON)
    preview = dry_run_autofix(payload, [_REPAIR])
    assert preview["applicable"]
    assert preview["would_change"]["repair_geometry"]["close_rings"] == 3
    assert payload["geojson"]["features"][0]["geometry"]["coordinates"] == \
        snapshot["coordinates"], "dry-run 绝不改动输入"


def test_rule_chain_autofix_preserves_multipolygon_structure():
    """rules.py 的 geometry_validity → repair_geometry 全链路径同样保结构。"""
    payload = _fc(copy.deepcopy(_UNCLOSED_MULTIPOLYGON))
    steps = plan_autofix(evaluate_payload(payload))
    assert any(s.operation == "repair_geometry" for s in steps)

    new_payload, evidence = apply_autofix(payload, steps)
    assert evidence["changed"]
    geom = new_payload["geojson"]["features"][0]["geometry"]
    assert len(geom["coordinates"]) == 2
    assert [len(part) for part in geom["coordinates"]] == [2, 1]
    assert geom["coordinates"][0][1][:-1] == \
        [[2.0, 2.0], [2.0, 4.0], [4.0, 4.0], [4.0, 2.0]]
