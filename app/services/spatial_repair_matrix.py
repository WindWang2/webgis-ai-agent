"""Repair Matrix — 19 诊断码 × 11 规范 op 的实测矩阵（V11 W3.4，ADR-0163）。

任务书 W3.4：「CANONICAL_OP_ORDER 11 个 op 与 19 个诊断码的映射做实测
矩阵（19×11），补齐未覆盖格或明示不可修」。

设计：

- **轴**：行 = ``_CODE_TO_OP`` 的 19 个诊断码（同一来源，不另立词表）；
  列 = ``CANONICAL_OP_ORDER`` 的 11 个规范 op；
- **实测**：每格对该诊断码的合成夹具**真实执行单 op**
  （``SpatialRepairPipeline.repair_dataset_detailed``，op 级证据
  ``features_affected``），矩阵不是纸面映射而是行为记录；
- **结论枚举**（每格必有结论，验收「补齐未覆盖格或明示不可修」）：
  - ``mapped_effective``：映射在册且实测影响了要素（修得了）；
  - ``mapped_no_effect``：映射在册但该夹具上无效果 —— 如实标记
    （调参/夹具语义的复核线索，不是隐藏失败）；
  - ``unmapped_no_effect``：不在映射内且无效果 —— **明示不可修**（正确）；
  - ``unmapped_side_effect``：不在映射内却影响了要素 —— 交叉效应
    （如 remove_empty 顺带清掉极端坐标夹具的空要素），供映射表复核；
- 确定性：夹具合成无随机；同输入恒同矩阵。golden JSON 冻结（回归 = 矩阵
  不回退：effective 集不得缩小）。
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from app.services.spatial_repair_pipeline import (
    CANONICAL_OP_ORDER,
    SpatialRepairPipeline,
    _CODE_TO_OP,
)


# ── 合成夹具（每码一份最小 GeoJSON；确定性）──────────────────────────────

def _fc(features: List[Dict[str, Any]], *, source_crs: str = "EPSG:4326") -> Dict[str, Any]:
    fc: Dict[str, Any] = {"type": "FeatureCollection", "features": features}
    if source_crs != "EPSG:4326":
        fc["crs"] = {"type": "name", "properties": {"name": source_crs}}
    return fc


def _pt(x: float, y: float, props: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    return {"type": "Feature", "properties": props or {}, "geometry": {"type": "Point", "coordinates": [x, y]}}


def _poly(ring: List[List[float]], props: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    return {"type": "Feature", "properties": props or {}, "geometry": {"type": "Polygon", "coordinates": [ring]}}


#: 每码一份夹具 + 可选的 source_crs（crs_transform 类码需要非 4326 源）。
REPAIR_FIXTURES: Dict[str, Dict[str, Any]] = {
    "EMPTY_GEOMETRY": {"fc": _fc([
        _pt(116.0, 39.0),
        {"type": "Feature", "properties": {}, "geometry": {"type": "Point", "coordinates": []}},
    ])},
    "NULL_ISLAND": {"fc": _fc([
        _pt(116.0, 39.0),
        _pt(0.0, 0.0),
    ])},
    "INVALID_GEOMETRY": {"fc": _fc([
        _poly([[0, 0], [2, 2], [2, 0], [0, 2], [0, 0]]),  # 自交环（非法）
    ])},
    "SELF_INTERSECTION": {"fc": _fc([
        _poly([[0, 0], [2, 2], [2, 0], [0, 2], [0, 0]]),  # 蝴蝶结
    ])},
    "RING_CHECK_FAILED": {"fc": _fc([{
        "type": "Feature", "properties": {},
        "geometry": {"type": "Polygon", "coordinates": [
            [[0, 0], [4, 0], [4, 4], [0, 4], [0, 0]],
            [[5, 5], [6, 5], [6, 6], [5, 6], [5, 5]],  # 内环在外环外
        ]},
    }])},
    "DUPLICATE_GEOMETRY": {"fc": _fc([
        _pt(116.0, 39.0, {"n": "a"}),
        _pt(116.0, 39.0, {"n": "b"}),
    ])},
    "DUPLICATE_FEATURE": {"fc": _fc([
        _pt(116.0, 39.0, {"n": "a"}),
        _pt(116.0, 39.0, {"n": "a"}),
    ])},
    "DUPLICATE_PRIMARY_KEY": {"fc": _fc([
        _pt(116.0, 39.0, {"pk": "k1", "v": 1}),
        _pt(116.1, 39.1, {"pk": "k1", "v": 1}),  # 同键同属性
    ])},
    "MISSING_CRS": {"fc": _fc([
        _pt(13000000.0, 4000000.0),
        _pt(13100000.0, 4010000.0),
    ], source_crs="EPSG:3857")},
    "SUSPICIOUS_CRS": {"fc": _fc([
        _pt(12000000.0, 3000000.0),
        _pt(12100000.0, 3010000.0),
    ], source_crs="EPSG:3857")},
    "IMPOSSIBLE_LAT_LON": {"fc": _fc([
        _pt(110.0, 250.0),  # 纬度 > 90
    ])},
    "EXTREME_COORDINATES": {"fc": _fc([
        _pt(1e9, 1e9),
        _pt(116.0, 39.0),
    ])},
    "TOPOLOGY_GAP": {"fc": _fc([
        _poly([[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]),
        _poly([[1.000001, 0], [2, 0], [2, 1], [1.000001, 1], [1.000001, 0]]),  # 1e-6 缝（容差内可 snap）
    ])},
    "NEAR_DUPLICATE_VERTICES": {"fc": _fc([
        _poly([[0, 0], [1, 0], [1.0000001, 0.0000001], [1, 1], [0, 1], [0, 0]]),
    ])},
    "TOPOLOGY_OVERLAP": {"fc": _fc([
        _poly([[0, 0], [1.2, 0], [1.2, 1], [0, 1], [0, 0]]),
        _poly([[1, 0], [2, 0], [2, 1], [1, 1], [1, 0]]),  # 0.2 重叠
    ])},
    "TYPE_INCONSISTENCY": {"fc": _fc([
        _pt(116.0, 39.0, {"v": "1"}),
        _pt(116.1, 39.1, {"v": 2}),
    ])},
    "HIGH_NULL_RATIO": {"fc": _fc([
        _pt(116.0, 39.0, {"a": None, "b": None, "c": 1}),
        _pt(116.1, 39.1, {"a": None, "b": None, "c": 2}),
    ])},
    "NUMERIC_OUTLIER": {"fc": _fc([
        _pt(116.0, 39.0, {"v": 1}),
        _pt(116.1, 39.1, {"v": 2}),
        _pt(116.2, 39.2, {"v": 3}),
        _pt(116.3, 39.3, {"v": 100000}),
    ])},
    "INCONSISTENT_SCHEMA": {"fc": _fc([
        _pt(116.0, 39.0, {"a": 1, "b": 2}),
        _pt(116.1, 39.1, {"a": 1, "c": 3}),  # 缺 b 多 c
    ])},
}

#: 单 op 执行的统一参数（crs 类码在夹具上声明 source_crs）。
_TOLERANCE = 1e-5


def _feature_signature(feature: Dict[str, Any]) -> str:
    """要素签名的规范化 JSON（sort_keys；tuple/list 归一 —— pipeline 重建
    输出时坐标容器类型可能从 list 变 tuple，打印相同而 == 不等）。"""
    return json.dumps(feature, sort_keys=True, ensure_ascii=False, default=list)


def _changed_count(before: Dict[str, Any], after: Dict[str, Any]) -> int:
    """前后要素深比较的**实际变更数**（op 级 evidence 的 affected 语义
    因 op 而异 —— 有的是触碰数；矩阵以真实变更为准）。"""
    bf = before.get("features") or []
    af = after.get("features") or []
    bsig = [_feature_signature(f) for f in bf]
    asig = [_feature_signature(f) for f in af]
    # 数量变化（删/并）或逐位签名不同都算变更
    changed = abs(len(bsig) - len(asig))
    for b, a in zip(bsig, asig):
        if b != a:
            changed += 1
    return changed


def _run_single_op(fixture: Dict[str, Any], op: str) -> Dict[str, Any]:
    """夹具上执行单 op：返回实际变更数 + op 级证据触碰数（异常 fail-closed）。"""
    fc = fixture["fc"]
    source_crs = fixture.get("source_crs", "EPSG:4326")
    try:
        repaired, _logs, evidence = SpatialRepairPipeline.repair_dataset_detailed(
            fc, ops=[op], tolerance=_TOLERANCE, source_crs=source_crs,
        )
        evidence_affected = 0
        for entry in evidence:
            if entry.get("op") == op:
                evidence_affected = int(entry.get("features_affected") or 0)
        return {
            "changed": _changed_count(fc, repaired),
            "evidence_affected": evidence_affected,
        }
    except Exception:  # noqa: BLE001 —— 实测失败也是结论（fail-closed 标记）
        return {"changed": -1, "evidence_affected": -1}


def build_repair_matrix() -> Dict[str, Any]:
    """19×11 全格实测（确定性；同输入恒同矩阵）。"""
    cells: List[Dict[str, Any]] = []
    summary = {"mapped_effective": 0, "mapped_no_effect": 0,
               "unmapped_no_effect": 0, "unmapped_side_effect": 0,
               "error": 0}
    for code, fixture in REPAIR_FIXTURES.items():
        mapped_ops = _CODE_TO_OP.get(code, ())
        for op in CANONICAL_OP_ORDER:
            result = _run_single_op(fixture, op)
            changed = result["changed"]
            is_mapped = op in mapped_ops
            if changed < 0:
                verdict = "error"
            elif is_mapped and changed > 0:
                verdict = "mapped_effective"
            elif is_mapped:
                verdict = "mapped_no_effect"
            elif changed > 0:
                verdict = "unmapped_side_effect"
            else:
                verdict = "unmapped_no_effect"
            summary[verdict] += 1
            cells.append({
                "code": code, "op": op, "mapped": is_mapped,
                "changed_features": changed,
                "evidence_affected": result["evidence_affected"],
                "verdict": verdict,
            })
    return {
        "version": 1,
        "codes": list(REPAIR_FIXTURES.keys()),
        "ops": list(CANONICAL_OP_ORDER),
        "cells": cells,
        "summary": summary,
        "note": ("每格 = 该诊断码合成夹具上单 op 实测的 features_affected；"
                 "mapped_no_effect/unmapped_side_effect 是映射表的复核线索，"
                 "unmapped_no_effect 即「明示不可修」"),
    }


__all__ = ["REPAIR_FIXTURES", "build_repair_matrix"]
