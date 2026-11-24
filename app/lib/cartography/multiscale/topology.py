"""拓扑一致面综合引擎（WP2a）—— 共享边链级一次简化、两侧一致重建。

与既有 ``generalize.py`` 的分工（不重复）：

- ``generalize.py`` 的 ``shared_boundary_protect`` 只钉**共享顶点**（锚点）；
  锚点之间的共享边界两侧各自独立简化 —— 输出对锚点一致、但锚点间的
  插值边可以分叉 ⇒ 相邻面之间可能产生细缝隙/重叠（slivers）。
- 本引擎把**共享边链**（≥2 环共享的连续折线段）识别出来，每条链只简化
  **一次**，两侧重建时引用同一份简化坐标 ⇒ 逐段字节级一致，不可能缝隙。
- 共享顶点但顶点序列未配齐的真实脏数据不伪装保真：未配对段按独占链
  处理并计数披露（``partial_vertex_match``）；真实缝隙量由 WP4 QA pack
  （qa_pack.py）逐对面审计。

算法（确定性、逐要素可回退）：

1. 全局链注册：所有环（外环+洞，跨要素）的坐标段按 1e-9 量化配对；
   连续共享段的极大连续 run → 规范链（canonical chain，正/反方向归并）；
   非共享 run → 独占链。
2. 链简化：规范链只 simplify 一次（Douglas-Peucker 天然保留端点 = 环上
   拼接点，均为共享节点或环起始点，拼接精确）；独占链逐 run 简化（端点
   同样保留）。简化失败/端点漂移的链保护回原坐标。
3. 环重建：按 run 序拼接（共享链用规范简化坐标正/反拼接）⇒ Polygon →
   有效性/面积误差逐要素复核，任一违反 ⇒ 该要素回退原几何并记因。
4. 回退传播：回退要素参与的链全部保护回原坐标，第二轮重建（共享链两侧
   同用原坐标 = 与回退要素无缝）；仍失败才真正回退。链级状态全或无 ⇒
   任何共享边两侧要么同用简化链、要么同用原链，混合态只可能是
   「该边本来就没配对」（partial_vertex_match，已披露）。

换算纪律：容差恒 meters_ground（纸面契约经 paper_scale.resolve_tolerance_m
或 zoom 带经 generalize._tolerance_meters 换算后传入）；投影复用
generalize 引擎同一局部等距实现（单一投影事实，不建第二套换算）。
"""
from __future__ import annotations

import copy
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from pydantic import BaseModel, ConfigDict
from shapely.geometry import LineString, MultiPolygon, Polygon
from shapely.geometry import shape as _shape

# 同包单例复用：局部等距投影/坐标提取/GeoJSON 规范化与 generalize 引擎
# 是同一实现（不建第二套投影换算 —— 单一事实源纪律）。
from app.lib.cartography.multiscale.generalize import (
    _dataset_origin,
    _geojson_geometry,
    _metric_transforms,
)

#: 坐标量化精度（与 generalize._anchor_map 同值：1e-9）。
_Q = 9

#: 回执 per_feature 明细上限（防病理输入放大；溢出只计数）。
_RECEIPT_DETAIL_LIMIT = 64

#: 重建轮数上限：第 1 轮发现回退 → 链保护 → 第 2 轮恒成功（原坐标重建）。
_MAX_ROUNDS = 2


class TopologyResult(BaseModel):
    """拓扑一致综合产物：派生 FC + 全量回执（可序列化、确定性）。"""

    model_config = ConfigDict(extra="allow")

    feature_collection: Dict[str, Any]
    receipt: Dict[str, Any]


# ── 基础数据结构 ─────────────────────────────────────────────────────────


QCoord = Tuple[float, float]


def _q(pt: Any) -> QCoord:
    return (round(float(pt[0]), _Q), round(float(pt[1]), _Q))


def _seg_key(a: QCoord, b: QCoord) -> Tuple[QCoord, QCoord]:
    """无向段键（端点量化后升序）。"""
    return (a, b) if a <= b else (b, a)


def _dedupe_consecutive(coords: Sequence[Any]) -> List[Any]:
    """连续重复坐标去除（脏数据防御；确定性）。"""
    out: List[Any] = []
    for c in coords:
        if not out or _q(out[-1]) != _q(c):
            out.append(c)
    return out


def _canonical_coords(coords_m: List[Tuple[float, float]]) -> Tuple[
        Tuple[Tuple[float, float], ...], bool]:
    """链坐标 → (规范键, run 是否与规范同向)。

    规范键 = 量化坐标的正/反序列字典序小者 —— 两侧遍历方向不同、或坐标
    有 ≤1e-9 抖动也归并为同一链；几何基准取首见浮点坐标（注册处写入
    ``canonical[key]``），两侧拼接同一份浮点坐标 ⇒ 输出逐字节一致。
    """
    fwdq = tuple(_q(c) for c in coords_m)
    revq = tuple(reversed(fwdq))
    if fwdq <= revq:
        return fwdq, True
    return revq, False


class _RingRef:
    """环引用：所属要素 + 米空间闭合坐标（首==尾）。"""

    __slots__ = ("feature_idx", "coords")

    def __init__(self, feature_idx: int, coords: List[Tuple[float, float]]):
        self.feature_idx = feature_idx
        self.coords = coords


class _Run:
    """环的一个极大连续 run：共享链实例或独占段。"""

    __slots__ = ("is_shared", "canon_key", "forward", "coords", "simplified")

    def __init__(self, is_shared: bool, canon_key: Optional[Tuple],
                 forward: bool, coords: List[Tuple[float, float]]):
        self.is_shared = is_shared
        self.canon_key = canon_key
        self.forward = forward
        self.coords = coords          # 本 run 原坐标（米空间）
        self.simplified: Optional[List[Tuple[float, float]]] = None


def _polygon_parts(geom: Any) -> List[Polygon]:
    if isinstance(geom, Polygon):
        return [geom]
    if isinstance(geom, MultiPolygon):
        return list(geom.geoms)
    return []


def _rings_of(part: Polygon) -> List[Tuple[bool, List[Tuple[float, float]]]]:
    """部件 → [(is_exterior, 闭合坐标列)]（外环恒首位）。"""
    out: List[Tuple[bool, List[Tuple[float, float]]]] = [
        (True, list(part.exterior.coords))]
    for ring in part.interiors:
        out.append((False, list(ring.coords)))
    return out


def _ring_signed_area(coords_m: Sequence[Tuple[float, float]]) -> float:
    """鞋带公式（米空间）。"""
    area = 0.0
    n = len(coords_m)
    for i in range(n - 1):
        x0, y0 = coords_m[i]
        x1, y1 = coords_m[i + 1]
        area += x0 * y1 - x1 * y0
    return area / 2.0


def _stable_feature_id(feature: Dict[str, Any], index: int) -> str:
    fid = feature.get("id")
    if isinstance(fid, (str, int)):
        return str(fid)
    return f"__idx{index}"


# ── 主入口 ────────────────────────────────────────────────────────────────


def simplify_polygon_topology(
    features: Sequence[Dict[str, Any]],
    *,
    tolerance_m: float,
    latitude_hint: float = 0.0,
    max_area_error_ratio: float = 0.02,
    mandatory_ids: frozenset = frozenset(),
) -> TopologyResult:
    """面要素列表 → 拓扑一致派生 FC + 回执（确定性）。

    - 容差恒 meters_ground（调用方完成一切单位换算）；
    - 非 Polygon/MultiPolygon 要素原样透传（本引擎不碰点/线）；
    - ``mandatory_ids``：语义保留面（user-wins）——几何综合一视同仁，
      回退时回执披露 mandatory_saved。
    """
    features = list(features)
    input_count = len(features)
    lon0, lat0 = _dataset_origin(features)
    to_m, to_ll = _metric_transforms(lon0, lat0)
    tol = max(0.0, float(tolerance_m))

    # ── 1. 解析：要素 → 部件 → 环（米空间闭合坐标；坏要素原样记账）──
    parsed: List[Tuple[Dict[str, Any], str, List[List[_RingRef]], List[str]]] = []
    ring_refs: List[_RingRef] = []
    for index, feature in enumerate(features):
        fid = _stable_feature_id(feature, index)
        geom_dict = feature.get("geometry")
        geom_ll = None
        if isinstance(geom_dict, dict):
            try:
                geom_ll = _shape(geom_dict)
            except Exception:  # noqa: BLE001 — 不可解析几何原样保留
                geom_ll = None
        parts: List[List[_RingRef]] = []
        parse_notes: List[str] = []
        if geom_ll is None:
            parse_notes.append("geometry_unparseable")
        elif geom_ll.geom_type not in ("Polygon", "MultiPolygon"):
            parse_notes.append("non_polygon_passthrough")
        else:
            for part in _polygon_parts(geom_ll):
                try:
                    part_m = to_m(part)
                except Exception:  # noqa: BLE001 — 病态坐标部件
                    parse_notes.append("part_project_failed")
                    parts.append([])
                    continue
                rings: List[_RingRef] = []
                for _ridx, (is_ext, coords) in enumerate(_rings_of(part_m)):
                    coords_m = [(float(x), float(y)) for x, y in coords]
                    ref = _RingRef(index, coords_m)
                    rings.append(ref)
                    ring_refs.append(ref)
                parts.append(rings)
        parsed.append((feature, fid, parts, parse_notes))

    # ── 2. 全局段索引（量化；去连续重复后建键）──
    ring_coords: List[List[Tuple[float, float]]] = []
    for ring in ring_refs:
        ring_coords.append([(float(x), float(y))
                            for x, y in _dedupe_consecutive(ring.coords)])
    seg_index: Dict[Tuple[QCoord, QCoord], int] = {}
    for coords in ring_coords:
        for pos in range(len(coords) - 1):
            key = _seg_key(_q(coords[pos]), _q(coords[pos + 1]))
            seg_index[key] = seg_index.get(key, 0) + 1

    def _is_shared_seg(a: QCoord, b: QCoord) -> bool:
        return seg_index.get(_seg_key(a, b), 0) >= 2

    # ── 3. 逐环切 run + 规范链注册 ──
    ring_plans: List[List[_Run]] = [[] for _ in ring_refs]
    ring_id_of: Dict[int, int] = {id(r): i for i, r in enumerate(ring_refs)}
    canonical: Dict[Tuple, List[Tuple[float, float]]] = {}
    chain_member_count: Dict[Tuple, int] = {}
    for r_id, coords in enumerate(ring_coords):
        runs: List[_Run] = []
        n = len(coords)
        pos = 0
        while pos < n - 1:
            shared = _is_shared_seg(_q(coords[pos]), _q(coords[pos + 1]))
            start = pos
            while pos < n - 1 and _is_shared_seg(
                    _q(coords[pos]), _q(coords[pos + 1])) == shared:
                pos += 1
            run_coords = [(float(x), float(y)) for x, y in coords[start:pos + 1]]
            if shared:
                canon, forward = _canonical_coords(run_coords)
                if canon not in canonical:
                    # 几何基准 = 首见浮点坐标（按规范方向）；两侧拼接同一
                    # 份浮点坐标 ⇒ 输出逐字节一致，无缝隙。
                    canonical[canon] = list(run_coords) if forward else list(
                        reversed(run_coords))
                chain_member_count[canon] = chain_member_count.get(canon, 0) + 1
                runs.append(_Run(True, canon, forward, run_coords))
            else:
                runs.append(_Run(False, None, True, run_coords))
        ring_plans[r_id] = runs

    # 顶点对齐诚实统计：有共享顶点但零多成员共享链 = 顶点未配齐数据
    # （闭合环的收尾点与首点同坐标，去尾避免环内重复计数）
    vertex_freq: Dict[QCoord, int] = {}
    for coords in ring_coords:
        seq = coords[:-1] if (
            len(coords) > 1 and _q(coords[0]) == _q(coords[-1])) else coords
        for c in seq:
            qc = _q(c)
            vertex_freq[qc] = vertex_freq.get(qc, 0) + 1
    anchors_shared = sum(1 for v in vertex_freq.values() if v >= 2)
    chains_shared = sum(1 for v in chain_member_count.values() if v >= 2)

    # ── 4. 简化（共享链一次；独占 run 逐个；失败保护原坐标）──
    simplified_chain: Dict[Tuple, Optional[List[Tuple[float, float]]]] = {}

    def _simplify_polyline(
        coords_m: List[Tuple[float, float]],
    ) -> Optional[List[Tuple[float, float]]]:
        if len(coords_m) < 3:
            return list(coords_m)
        try:
            ls = LineString(coords_m).simplify(tol, preserve_topology=True)
        except Exception:  # noqa: BLE001 — 病理坐标 → 保护原坐标
            return None
        out = [(float(x), float(y)) for x, y in ls.coords]
        if len(out) < 2:
            return None
        # DP 保端点；防御复核 —— 端点漂移 = 拼接断裂 → 保护
        if _q(out[0]) != _q(coords_m[0]) or _q(out[-1]) != _q(coords_m[-1]):
            return None
        return out

    for key, coords_m in canonical.items():
        if chain_member_count.get(key, 0) < 2 or len(coords_m) < 3:
            simplified_chain[key] = list(coords_m)
            continue
        simplified_chain[key] = _simplify_polyline(coords_m)

    protected_chains: Set[Tuple] = set()

    def _chain_coords(key: Tuple) -> Optional[List[Tuple[float, float]]]:
        if key in protected_chains:
            return list(canonical[key])
        return simplified_chain.get(key) or list(canonical[key])

    def _run_coords(run: _Run) -> Optional[List[Tuple[float, float]]]:
        """run → 拼接坐标（独占 run 首次简化后缓存于对象；确定性）。"""
        if run.is_shared:
            return _chain_coords(run.canon_key)
        if run.simplified is None:
            run.simplified = _simplify_polyline(run.coords)
        return run.simplified

    # ── 5. 两轮重建（回退 → 链保护 → 重 build）──
    rebuild_failures: List[int] = []

    def _rebuild_ring(plan: List[_Run]) -> Optional[List[Tuple[float, float]]]:
        out: List[Tuple[float, float]] = []
        for run in plan:
            seq = _run_coords(run)
            if seq is None:
                return None
            if not out:
                out.extend(seq)
            else:
                use = seq if (run.is_shared and run.forward) or (
                    not run.is_shared) else list(reversed(seq))
                out.extend(use[1:])
        if len(out) < 4:
            return None
        if _q(out[0]) != _q(out[-1]):
            return None  # 闭合破坏
        if abs(_ring_signed_area(out)) == 0.0:
            return None  # 退化环
        return out

    def _rebuild_feature(parts: List[List[_RingRef]]) -> Tuple[
            Optional[Any], List[str]]:
        outcomes: List[str] = []
        new_parts: List[Polygon] = []
        area_in_total = 0.0
        area_out_total = 0.0
        for rings in parts:
            if not rings:
                return None, (outcomes + ["part_project_failed"])
            ext_plan = ring_plans[ring_id_of[id(rings[0])]]
            ext_coords = _rebuild_ring(ext_plan)
            if ext_coords is None:
                return None, (outcomes + ["ring_rebuild_failed"])
            interiors: List[List[Tuple[float, float]]] = []
            for hole_ref in rings[1:]:
                hole_plan = ring_plans[ring_id_of[id(hole_ref)]]
                hole_coords = _rebuild_ring(hole_plan)
                if hole_coords is None:
                    return None, (outcomes + ["hole_rebuild_failed"])
                interiors.append(hole_coords)
            try:
                poly = Polygon(ext_coords, interiors)
            except Exception:  # noqa: BLE001
                return None, (outcomes + ["polygon_construct_failed"])
            # 面积口径：多边形面积 = 外环 − 洞（与 shapely poly.area 同口径，
            # 洞面积不得当加法 —— 否则含洞要素恒超差）
            area_in = abs(_ring_signed_area(rings[0].coords)) - sum(
                abs(_ring_signed_area(h.coords)) for h in rings[1:])
            area_out = abs(poly.area)
            area_in_total += area_in
            area_out_total += area_out
            if area_in > 0:
                ratio = abs(area_out - area_in) / area_in
                if ratio > max_area_error_ratio:
                    return None, (outcomes + ["area_error_exceeded"])
            if poly.is_empty or not poly.is_valid:
                return None, (outcomes + ["invalid_result"])
            new_parts.append(poly)
        if not new_parts:
            return None, (outcomes + ["ring_rebuild_failed"])
        try:
            geom_out: Any = new_parts[0] if len(new_parts) == 1 else MultiPolygon(
                new_parts)
        except Exception:  # noqa: BLE001
            return None, (outcomes + ["polygon_construct_failed"])
        if area_in_total > 0:
            ratio = abs(area_out_total - area_in_total) / area_in_total
            if ratio > max_area_error_ratio:
                return None, (outcomes + ["area_error_exceeded"])
        return geom_out, (outcomes + ["topology_simplified"])

    final_geom: Dict[int, Optional[Any]] = {}
    final_outcomes: Dict[int, List[str]] = {}
    for index, (_f, _fid, parts, notes) in enumerate(parsed):
        if notes:
            final_geom[index] = None
            final_outcomes[index] = list(notes)
            continue
        geom_out, outcomes = _rebuild_feature(parts)
        if geom_out is None:
            rebuild_failures.append(index)
            final_geom[index] = None
            final_outcomes[index] = outcomes
        else:
            final_geom[index] = geom_out
            final_outcomes[index] = outcomes

    if rebuild_failures:
        # 回退传播：失败要素参与的全部链保护回原坐标；**所有**引用被保护
        # 链的要素（含成功邻居）都必须重 build —— 否则邻居持简化链、回退
        # 要素持原链，同一条共享边两侧不一致（重建用原链 = 原环，恒成功）。
        for r_id, ring in enumerate(ring_refs):
            if ring.feature_idx not in rebuild_failures:
                continue
            for run in ring_plans[r_id]:
                if run.is_shared and run.canon_key is not None:
                    protected_chains.add(run.canon_key)
        touched: set = set(rebuild_failures)
        for r_id, plan in enumerate(ring_plans):
            if any(run.is_shared and run.canon_key in protected_chains
                   for run in plan):
                touched.add(ring_refs[r_id].feature_idx)
        for index, (_f, _fid, parts, notes) in enumerate(parsed):
            if notes or index not in touched:
                continue
            geom_out, outcomes = _rebuild_feature(parts)
            if geom_out is None:
                final_geom[index] = None
                final_outcomes[index] = outcomes + ["topology_fallback"]
            else:
                final_geom[index] = geom_out
                final_outcomes[index] = outcomes

    # ── 6. 组装 + 回执 ──
    kept_features: List[Dict[str, Any]] = []
    reason_counts: Dict[str, int] = {}
    details: List[Dict[str, Any]] = []
    simplified_count = fallback_count = mandatory_saved = 0
    for index, (feature, fid, _parts, _notes) in enumerate(parsed):
        geom_ll_out = final_geom[index]
        outcomes = final_outcomes[index]
        out_feature = copy.copy(feature)
        if geom_ll_out is not None:
            simplified_count += 1
            out_feature = dict(out_feature)
            try:
                out_feature["geometry"] = _geojson_geometry(to_ll(geom_ll_out))
            except Exception:  # noqa: BLE001 — 投回失败按回退诚实记账
                out_feature = copy.copy(feature)
                outcomes = outcomes + ["project_back_failed"]
                fallback_count += 1
                simplified_count -= 1
        else:
            fallback_count += 1
            if fid in mandatory_ids:
                mandatory_saved += 1
        kept_features.append(out_feature)
        for c in outcomes:
            reason_counts[c] = reason_counts.get(c, 0) + 1
        if len(details) < _RECEIPT_DETAIL_LIMIT:
            details.append({"id": fid, "outcome": (
                "simplified" if "topology_simplified" in outcomes
                else "fallback"), "reasons": outcomes})

    disclosure: List[str] = []
    if anchors_shared > 0 and chains_shared == 0:
        disclosure.append(
            "边界共享顶点存在但无逐段配对的共享边链（顶点未配齐数据）——"
            "本引擎不做跨要素一致化保证，相邻面缝隙需 QA 审计（诚实披露）")
    if fallback_count:
        disclosure.append(
            f"{fallback_count} 个要素回退原几何（链保护传播后仍违反不变量）"
            "——其共享链两侧同用原坐标，无新增缝隙")

    receipt = {
        "engine": "topology_chain_v1",
        "tolerance_m": tol,
        "projection": "equirectangular_local_approx",
        "input_count": input_count,
        "kept_count": len(kept_features),
        "geometry_simplified": simplified_count,
        "geometry_fallback": fallback_count,
        "mandatory_saved": mandatory_saved,
        "anchors_shared_vertices": anchors_shared,
        "chains_registered": len(chain_member_count),
        "chains_shared_multi": chains_shared,
        "chains_protected_original": len(protected_chains),
        "partial_vertex_match": bool(
            anchors_shared > 0 and chains_shared == 0),
        "stats_match": len(kept_features) == input_count,
        "reason_counts": dict(sorted(reason_counts.items())),
        "per_feature": details,
        "disclosures": disclosure,
    }
    return TopologyResult(
        feature_collection={"type": "FeatureCollection",
                            "features": kept_features},
        receipt=receipt,
    )


__all__ = [
    "TopologyResult",
    "simplify_polygon_topology",
]
