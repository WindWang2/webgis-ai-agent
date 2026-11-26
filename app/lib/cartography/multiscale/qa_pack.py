"""制图综合 QA pack（WP4）—— 派生工件的独立确定性审计。

定位：**独立几何/语义 oracle**，不信任各综合引擎自述回执 —— 直接对
派生 FC 用 shapely/图算法复核。与各引擎的分工：引擎回执记录「过程」，
本 pack 判定「结果可否上版」。全部纯函数、零 IO、确定性。

检查族（词表 = SemanticCheckRecord 语义；供 compile/服务面与
evaluate_cartography_semantics 消费）：

- ``psc.geometry_validity``：shapely is_valid 逐要素（面/线族）；
- ``psc.polygon_seams``：相邻面逐对 intersection ≈ 0 且 union 覆盖差
  ≤ 容差（WP2a 无缝保证的独立复核；顶点未配齐数据在此现形）；
- ``psc.min_display_size``：目标尺度最小可辨面斑/线长达标（消除/简化
  后残留 sub-threshold 要素计数）；
- ``psc.count_monotonic``：派生要素数 ≤ 原始（count 口径的引擎侧
  stats_match = kept+eliminated==input 由各引擎回执承载）；
- ``psc.sum_conservation``：sum_fields Σ(派生可显示值) == Σ(原始)
  （计数/总量专题上图对账）；
- ``psc.network_connectivity``：线工件分量数（navigation 硬约束 fail、
  其余 warning）；
- ``psc.scale_consistency``：契约合法 + 等效 zoom 域内 + Mercator 纬度
  变化披露在场（真实变形不静默）。

状态纪律：无法评估（缺输入/非该族）⇒ ``not_evaluated``；超容差/超预算
⇒ ``fail``；语义发散但可披露解释 ⇒ ``warning``。绝不因缺证据报 pass。
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Sequence, Tuple

from shapely.geometry import shape
from shapely.ops import unary_union

#: 缝隙/重叠容差（米²；绝对值，含浮点噪声）。
DEFAULT_SEAM_TOLERANCE_M2 = 1.0

#: 检查 id 前缀。
_PSC = "psc."

SEMANTIC_CHECK_STATUSES = ("pass", "warning", "fail", "not_evaluated")

CheckRow = Dict[str, Any]


def _fid(feature: Dict[str, Any], index: int) -> str:
    fid = feature.get("id")
    return str(fid) if isinstance(fid, (str, int)) else f"__idx{index}"


def _metric_factors(latitude_hint: float) -> Tuple[float, float]:
    lat = max(-85.0, min(85.0, float(latitude_hint)))
    kx = 111_320.0 * math.cos(math.radians(lat))
    return kx, 110_574.0


# ── 各审计 ────────────────────────────────────────────────────────────────


def audit_geometry_validity(
    features: Sequence[Dict[str, Any]],
) -> Dict[str, Any]:
    """shapely is_valid 逐要素审计（几何族全检；空几何跳过计数）。"""
    invalid: List[str] = []
    checked = 0
    skipped = 0
    for index, feature in enumerate(features):
        geom_dict = feature.get("geometry")
        if not isinstance(geom_dict, dict):
            skipped += 1
            continue
        try:
            geom = shape(geom_dict)
        except Exception:  # noqa: BLE001 — 不可解析 = 无效几何
            invalid.append(_fid(feature, index))
            continue
        if geom.is_empty:
            skipped += 1
            continue
        checked += 1
        if not geom.is_valid:
            invalid.append(_fid(feature, index))
    return {"checked": checked, "skipped": skipped, "invalid_ids": invalid}


def audit_polygon_pairwise_seams(
    features: Sequence[Dict[str, Any]],
    *,
    latitude_hint: float = 0.0,
    overlap_tolerance_m2: float = DEFAULT_SEAM_TOLERANCE_M2,
    gap_tolerance_m2: float = DEFAULT_SEAM_TOLERANCE_M2,
    reference_features: Optional[Sequence[Dict[str, Any]]] = None,
    gap_distance_tolerance_m: float = 5.0,
) -> Dict[str, Any]:
    """面集合逐对缝隙/重叠审计（shapely 独立复核，米口径）。

    - ``max_pairwise_overlap_m2``：任意两要素 intersection 面积最大值；
    - ``coverage_gap_m2``：Σ面积 − union 面积（≥0；只度量多重覆盖）；
    - **邻接缝隙**（评审 R1-P1-4）：Σ−union 数学上检不出「应邻接却分叉
      分离」的细缝 —— 给 ``reference_features``（原始输入）时，凡原输入
      共享 ≥2 量化顶点的邻接对，在派生输出上按 ``distance`` 复核最近距
      离，> ``gap_distance_tolerance_m`` 记 gap 对（米）。邻接对按参考
      索引稀疏复核（非全对 O(n²) 距离）。
    """
    def _collect(seq: Sequence[Dict[str, Any]]):
        out = []
        for feature in seq:
            geom_dict = feature.get("geometry")
            if not isinstance(geom_dict, dict):
                continue
            try:
                geom = shape(geom_dict)
            except Exception:  # noqa: BLE001
                continue
            if geom.geom_type in ("Polygon", "MultiPolygon")                     and not geom.is_empty:
                out.append(geom)
        return out

    geoms = _collect(features)
    if len(geoms) < 2:
        return {"pairs_checked": max(0, len(geoms) - 1),
                "max_pairwise_overlap_m2": 0.0,
                "coverage_gap_m2": 0.0,
                "adjacent_pairs_checked": 0,
                "gap_pairs": [],
                "within_tolerance": True,
                "evaluated": len(geoms) >= 1}
    kx, ky = _metric_factors(latitude_hint)
    deg_to_m2 = kx * ky
    max_overlap = 0.0
    pairs = 0
    for i in range(len(geoms)):
        for j in range(i + 1, len(geoms)):
            try:
                inter = geoms[i].intersection(geoms[j])
            except Exception:  # noqa: BLE001 — 相交失败按未知重叠保守计
                max_overlap = float("inf")
                pairs += 1
                continue
            if not inter.is_empty:
                max_overlap = max(max_overlap, inter.area * deg_to_m2)
            pairs += 1
    try:
        union_area = unary_union(geoms).area
        total = sum(g.area for g in geoms)
        gap = max(0.0, (total - union_area) * deg_to_m2)
    except Exception:  # noqa: BLE001 — union 失败按未知保守
        gap = float("inf")

    # 邻接缝隙复核（评审 R2-N1 修正：按**稳定 fid** 对齐参考/派生 —— 消除
    # 后幸存者 id 不变，计数对齐门槛会让消除路径静默跳过审计）。参考输入
    # 定义邻接对（外环量化顶点共享 ≥2）；两侧均在派生集合中的对做米空间
    # 距离复核（等距换算，消除 kx-only 方向偏差 —— 评审 R2-N4）；被消除
    # 一侧的对自然跳过（几何已并入邻居 —— 邻接关系随消除合法改变），
    # 跳过数如实披露。
    adjacent_pairs_checked = 0
    adjacent_pairs_skipped = 0
    gap_pairs: List[Dict[str, Any]] = []
    ref_geoms = _collect(reference_features) if reference_features else []
    if ref_geoms:
        from app.lib.cartography.multiscale.generalize import (
            _dataset_origin as _orig,
            _metric_transforms as _mt,
        )

        ref_fid_q = []
        for ref_index, feature in enumerate(reference_features or []):
            gd = feature.get("geometry")
            g = None
            if isinstance(gd, dict):
                try:
                    g = shape(gd)
                except Exception:  # noqa: BLE001
                    g = None
            qs: set = set()
            if g is not None and g.geom_type in ("Polygon", "MultiPolygon"):
                exts: List[Any] = []
                if g.geom_type == "Polygon":
                    exts = [g.exterior]
                else:
                    exts = [part.exterior for part in g.geoms]
                for ext in exts:
                    for c in ext.coords:
                        qs.add((round(c[0], 9), round(c[1], 9)))
            ref_fid_q.append((_fid(feature, ref_index), qs))

        derived_by_fid: Dict[str, Any] = {}
        for feature in features:
            gd = feature.get("geometry")
            if not isinstance(gd, dict):
                continue
            try:
                g = shape(gd)
            except Exception:  # noqa: BLE001
                continue
            if g.geom_type in ("Polygon", "MultiPolygon") and not g.is_empty:
                derived_by_fid[_fid(feature, len(derived_by_fid))] = g
        lon0, lat0 = _orig(ref_geoms)
        to_m, _to_ll = _mt(lon0, lat0)
        derived_m: Dict[str, Any] = {}
        for fid_key, g in derived_by_fid.items():
            try:
                derived_m[fid_key] = to_m(g)
            except Exception:  # noqa: BLE001 — 换算失败不入索引（诚实少审）
                continue

        for idx_a, (fid_a, qa) in enumerate(ref_fid_q):
            for fid_b, qb in ref_fid_q[idx_a + 1:]:
                shared = qa & qb
                if len(shared) < 2:
                    continue   # 非邻接对
                ga = derived_m.get(fid_a)
                gb = derived_m.get(fid_b)
                if ga is None or gb is None:
                    # 一侧被消除/聚合：邻接关系随合并合法改变（跳过并披露）
                    adjacent_pairs_skipped += 1
                    continue
                adjacent_pairs_checked += 1
                try:
                    dist_m = float(ga.distance(gb))
                except Exception:  # noqa: BLE001 — 距离失败按未知保守
                    dist_m = float("inf")
                if dist_m > float(gap_distance_tolerance_m):
                    gap_pairs.append({
                        "pair": [fid_a, fid_b],
                        "distance_m": round(dist_m, 3),
                    })
    within = bool(
        max_overlap <= overlap_tolerance_m2 and gap <= gap_tolerance_m2
        and not gap_pairs)
    return {
        "pairs_checked": pairs,
        "max_pairwise_overlap_m2": max_overlap,
        "coverage_gap_m2": gap,
        "adjacent_pairs_checked": adjacent_pairs_checked,
        "adjacent_pairs_skipped": adjacent_pairs_skipped,
        "gap_pairs": gap_pairs[:16],
        "within_tolerance": within,
        "evaluated": True,
    }


def audit_min_display_size(
    features: Sequence[Dict[str, Any]],
    *,
    min_area_m2: Optional[float] = None,
    min_length_m: Optional[float] = None,
    latitude_hint: float = 0.0,
) -> Dict[str, Any]:
    """目标尺度最小可辨尺寸达标审计（面斑面积 / 线长；米口径）。"""
    kx, ky = _metric_factors(latitude_hint)
    deg_to_m2 = kx * ky
    sub_area: List[str] = []
    sub_length: List[str] = []
    area_checked = 0
    length_checked = 0
    for index, feature in enumerate(features):
        geom_dict = feature.get("geometry")
        if not isinstance(geom_dict, dict):
            continue
        try:
            geom = shape(geom_dict)
        except Exception:  # noqa: BLE001
            continue
        if min_area_m2 is not None and geom.geom_type in (
                "Polygon", "MultiPolygon") and not geom.is_empty:
            area_checked += 1
            if geom.area * deg_to_m2 < float(min_area_m2):
                sub_area.append(_fid(feature, index))
        if min_length_m is not None and geom.geom_type in (
                "LineString", "MultiLineString") and not geom.is_empty:
            length_checked += 1
            # 米空间量长（评审 R1-P2-5：度长 × kx 会把南北向线低估
            # cos(lat) 倍 —— 等距换算后量长，与引擎族同口径）
            from shapely.ops import transform as _shp_transform

            def _to_m(xs, ys):
                return (xs * kx, ys * ky)

            try:
                length_m = _shp_transform(_to_m, geom).length
            except Exception:  # noqa: BLE001 — 换算失败退化 kx（诚实近似）
                length_m = geom.length * kx
            if length_m < float(min_length_m):
                sub_length.append(_fid(feature, index))
    return {
        "area_checked": area_checked,
        "length_checked": length_checked,
        "sub_threshold_area_ids": sub_area,
        "sub_threshold_length_ids": sub_length,
    }


def audit_sum_conservation(
    input_features: Sequence[Dict[str, Any]],
    artifact_features: Sequence[Dict[str, Any]],
    *,
    sum_fields: Sequence[str],
) -> Dict[str, Any]:
    """sum_fields Σ(原始) == Σ(派生可显示值) 守恒审计。

    派生可显示值 = 派生 FC properties 中的字段值（消除幸存者/聚合代表
    均承载 Σ 成员值）。缺字段按缺省 0 计数披露（不臆造）。
    """
    if not sum_fields:
        return {"evaluated": False, "reason": "no_sum_fields_declared"}

    def _totals(features: Sequence[Dict[str, Any]]) -> Dict[str, Tuple[float, int]]:
        out: Dict[str, Tuple[float, int]] = {}
        for feature in features:
            props = feature.get("properties") if isinstance(
                feature.get("properties"), dict) else {}
            for field in sum_fields:
                v = props.get(field)
                if isinstance(v, bool) or not isinstance(v, (int, float)) \
                        or not math.isfinite(float(v)):
                    continue
                total, n = out.get(field, (0.0, 0))
                out[field] = (total + float(v), n + 1)
        return out

    src = _totals(input_features)
    art = _totals(artifact_features)
    per_field: Dict[str, Dict[str, Any]] = {}
    conserved = True
    for field in sum_fields:
        s_total, s_n = src.get(field, (0.0, 0))
        a_total, a_n = art.get(field, (0.0, 0))
        ok = abs(a_total - s_total) <= 1e-6 * max(1.0, abs(s_total))
        conserved = conserved and ok
        per_field[field] = {
            "input_total": round(s_total, 9),
            "input_features_with_value": s_n,
            "artifact_total": round(a_total, 9),
            "artifact_features_with_value": a_n,
            "conserved": ok,
        }
    return {"evaluated": True, "conserved": conserved, "fields": per_field}


def audit_network_connectivity(
    features: Sequence[Dict[str, Any]],
    *,
    snap_digits: int = 9,
) -> Dict[str, Any]:
    """线工件连通分量审计（复用 network_select 图语义的独立计数）。"""
    from app.lib.cartography.multiscale.network_select import count_line_components

    components = count_line_components(features, snap_digits=snap_digits)
    return {"components": components, "fully_connected": components <= 1}


def audit_scale_consistency(contract: Any) -> Dict[str, Any]:
    """契约合法 + 等效 zoom 域内 + Mercator 变化披露判定。"""
    from app.lib.cartography.multiscale.paper_scale import (
        equivalent_display_zoom,
        scale_variation_report,
        validate_contract,
    )

    errors = validate_contract(contract)
    zoom = None
    if not errors:
        zoom = equivalent_display_zoom(
            int(contract.target_denominator),
            float(contract.frame.scale_reference_lat),
            dpi=float(contract.page.dpi))
    variation = scale_variation_report(contract)
    return {
        "contract_errors": errors,
        "equivalent_zoom": zoom,
        "zoom_in_domain": bool(zoom is not None and 0.0 <= zoom <= 24.0),
        "variation_disclosure_required": bool(
            variation.get("disclosure_required")),
        "variation_evaluated": bool(variation.get("evaluated")),
    }


# ── 汇总面（compile/服务层消费）─────────────────────────────────────────


def run_paper_scale_qa(
    contract: Any,
    input_features: Sequence[Dict[str, Any]],
    artifact_features: Sequence[Dict[str, Any]],
    *,
    geometry_family: str,
    reference_features: Optional[Sequence[Dict[str, Any]]] = None,
    sum_fields: Sequence[str] = (),
    latitude_hint: float = 0.0,
    min_area_m2: Optional[float] = None,
    min_length_m: Optional[float] = None,
    network_connectivity_hard: bool = False,
    seam_overlap_tolerance_m2: float = DEFAULT_SEAM_TOLERANCE_M2,
    seam_gap_tolerance_m2: float = DEFAULT_SEAM_TOLERANCE_M2,
) -> List[CheckRow]:
    """一次出版综合工件的完整 QA → 检查行列表（确定性）。

    行结构兼容 SemanticCheckRecord / report.add_check evidence：
    ``{"check", "status", "detail"}``；状态词表 = SEMANTIC_CHECK_STATUSES。
    """
    rows: List[CheckRow] = []
    scale = audit_scale_consistency(contract)
    rows.append({
        "check": f"{_PSC}scale_consistency",
        "status": "pass" if (not scale["contract_errors"]
                             and scale["zoom_in_domain"]) else "fail",
        "detail": scale,
    })
    if scale["variation_disclosure_required"]:
        rows.append({
            "check": f"{_PSC}scale_variation_disclosure",
            "status": "warning",
            "detail": {"message": "Web Mercator 纬度跨度大：图内实际比例尺"
                                  "随纬度显著变化（见 detail）",
                       **scale},
        })

    validity = audit_geometry_validity(artifact_features)
    rows.append({
        "check": f"{_PSC}geometry_validity",
        "status": "pass" if not validity["invalid_ids"] else "fail",
        "detail": validity,
    })

    if geometry_family == "polygon":
        seams = audit_polygon_pairwise_seams(
            artifact_features, latitude_hint=latitude_hint,
            overlap_tolerance_m2=seam_overlap_tolerance_m2,
            gap_tolerance_m2=seam_gap_tolerance_m2,
            reference_features=reference_features if reference_features is not None
            else input_features)
        rows.append({
            "check": f"{_PSC}polygon_seams",
            "status": ("pass" if seams.get("within_tolerance")
                       else "fail") if seams.get("evaluated") else "not_evaluated",
            "detail": seams,
        })
    if geometry_family == "line":
        conn = audit_network_connectivity(artifact_features)
        status = "not_evaluated" if conn["components"] == 0 else (
            "pass" if conn["fully_connected"] else (
                "fail" if network_connectivity_hard else "warning"))
        rows.append({
            "check": f"{_PSC}network_connectivity",
            "status": status,
            "detail": {**conn, "hard_constraint": network_connectivity_hard},
        })
    if geometry_family == "point":
        # 点族无几何尺寸语义（聚合代表点尺寸恒 0）；守恒检查在下面统一
        pass

    if min_area_m2 is not None or min_length_m is not None:
        size = audit_min_display_size(
            artifact_features, min_area_m2=min_area_m2,
            min_length_m=min_length_m, latitude_hint=latitude_hint)
        residual = len(size["sub_threshold_area_ids"]) + len(
            size["sub_threshold_length_ids"])
        rows.append({
            "check": f"{_PSC}min_display_size",
            "status": "pass" if residual == 0 else (
                "fail" if residual > max(4, len(artifact_features) // 10)
                else "warning"),
            "detail": size,
        })

    counts_ok = len(input_features) >= len(artifact_features)
    rows.append({
        "check": f"{_PSC}count_monotonic",
        "status": "pass" if counts_ok else "fail",
        "detail": {"input_count": len(input_features),
                   "artifact_count": len(artifact_features)},
    })

    if sum_fields:
        sums = audit_sum_conservation(
            input_features, artifact_features, sum_fields=sum_fields)
        rows.append({
            "check": f"{_PSC}sum_conservation",
            "status": ("pass" if sums.get("conserved") else "fail")
            if sums.get("evaluated") else "not_evaluated",
            "detail": sums,
        })
    return rows


def qa_summary(rows: Sequence[CheckRow]) -> Dict[str, Any]:
    """检查行 → 简要判定面（fail 计数 / 状态机可读）。"""
    counts: Dict[str, int] = {}
    for row in rows:
        counts[row["status"]] = counts.get(row["status"], 0) + 1
    return {
        "total": len(rows),
        "status_counts": counts,
        "blocking": counts.get("fail", 0),
        "checks": [row["check"] for row in rows],
    }


__all__ = [
    "DEFAULT_SEAM_TOLERANCE_M2",
    "SEMANTIC_CHECK_STATUSES",
    "audit_geometry_validity",
    "audit_polygon_pairwise_seams",
    "audit_min_display_size",
    "audit_sum_conservation",
    "audit_network_connectivity",
    "audit_scale_consistency",
    "run_paper_scale_qa",
    "qa_summary",
]
