"""非破坏性几何综合引擎（M3）—— 拓扑安全、逐要素可回退、确定性。

职责边界（DECISIONS D5）：
- 只产**派生视图**：输入 feature 列表逐字节不被修改（浅拷贝 + 换几何）；
  派生 FC 无时间戳（确定性），由调用方存为新 ref（D2）。
- 策略词表 = profile.GEOMETRY_STRATEGIES；本引擎执行其中三档几何动作：
  - ``bounded_simplify``：局部等距投影（数据集质心 equirectangular，
    诚实近似并披露）→ shapely ``simplify(preserve_topology=True)`` →
    不变量复核（有效性/类型族/部件数/洞数/面积误差/长度损失）——
    任一违反 ⇒ **该要素回退原几何**并记因，绝不输出坏几何；
  - ``shared_boundary_protect``：坐标频次 ≥2 的共享锚点顶点必须在简化
    结果中存活（锚点不可丢）；不存活 ⇒ 逐次减半容差重试（≤4 次），
    仍失败回退原几何 —— 相邻面共享角点不裂缝的确定性保证；
  - ``safe_no_generalization`` / ``none``：几何零改动（只做要素级取舍）。
- 微环（min_ring_area_m2 显式设置时）可有意丢弃并计数 —— 缺省不丢。

取舍（selection）与几何（strategy）正交：importance kept_values 之外的
要素被抑制（suppress reason 入回执）；mandatory ids 恒存活（user-wins）。

容差单位在引擎边界一次换算（meters_ground 直接；degrees 经
111320·cos(φ) 赤道-纬度换算 —— 与 MVT 通道同族近似；pixels_display
经 profile.display_meters_per_pixel(band_zoom, lat)）。
"""
from __future__ import annotations

import copy
import math
from typing import Any, Dict, FrozenSet, List, Optional, Sequence, Tuple

from pydantic import BaseModel, ConfigDict
import numpy as np
from shapely import transform as shapely_transform
from shapely.geometry import LineString, mapping as _mapping, shape

from app.lib.cartography.multiscale.profile import (
    FeatureSelectionPolicy,
    GeometryGeneralizationPolicy,
    display_meters_per_pixel,
)

#: 局部等距换算常数（米/度）：纬向测地近似 + 赤道经向（cos 修正于原点）。
_M_PER_DEG_LAT = 110_574.0
_M_PER_DEG_LON_EQ = 111_320.0

#: 共享锚点存活重试的最大减半次数（300m→150→75→37.5→18.75 后回退）。
_MAX_ANCHOR_RETRY = 4

#: 长度损失上界（线族不变量；抖动细节抹平但主干不能丢一半以上）。
_MAX_LENGTH_LOSS_RATIO = 0.5

#: 回执 per_feature 明细上限（防病理输入放大；溢出只计数）。
_RECEIPT_DETAIL_LIMIT = 64


class GeneralizationResult(BaseModel):
    """综合产物：派生 FeatureCollection + 全量回执（可序列化、确定性）。"""

    model_config = ConfigDict(extra="allow")

    feature_collection: Dict[str, Any]
    receipt: Dict[str, Any]


def _tolerance_meters(
    policy: GeometryGeneralizationPolicy,
    latitude_hint: float,
    band_zoom: Optional[float],
) -> float:
    """容差 → 地面米（引擎边界唯一换算点）。"""
    if policy.tolerance_units == "meters_ground":
        return max(0.0, float(policy.tolerance))
    if policy.tolerance_units == "degrees":
        return max(0.0, float(policy.tolerance)) * _M_PER_DEG_LON_EQ * math.cos(
            math.radians(latitude_hint))
    if policy.tolerance_units == "pixels_display":
        if band_zoom is None:
            return 0.0
        return max(0.0, float(policy.tolerance)) * display_meters_per_pixel(
            band_zoom, latitude_hint)
    return 0.0


def _all_coords(shapely_geom: Any) -> List[Tuple[float, float]]:
    """几何 → (x, y) 全坐标（含 Polygon 外环/内环、Multi 部件、点）。"""
    out: List[Tuple[float, float]] = []
    g = shapely_geom
    if g is None or g.is_empty:
        return out
    t = g.geom_type
    if t == "Point":
        out.append((g.x, g.y))
    elif t in ("LineString", "LinearRing"):
        out.extend(g.coords)
    elif t == "Polygon":
        out.extend(g.exterior.coords)
        for ring in g.interiors:
            out.extend(ring.coords)
    elif t.startswith("Multi") or t == "GeometryCollection":
        for part in g.geoms:
            out.extend(_all_coords(part))
    return out


def _dataset_origin(features: Sequence[Dict[str, Any]]) -> Tuple[float, float]:
    """数据集坐标原点（首个有限坐标 —— equirectangular 局部原点，确定性）。"""
    for f in features:
        geom = f.get("geometry") if isinstance(f, dict) else None
        if not geom:
            continue
        try:
            coords = _all_coords(shape(geom))
        except Exception:  # noqa: BLE001 — 坏几何按无坐标处理
            continue
        for x, y in coords:
            if math.isfinite(x) and math.isfinite(y):
                return float(x), float(y)
    return 0.0, 0.0


def _metric_transforms(lon0: float, lat0: float):
    """lon/lat ↔ 局部米（equirectangular @ 原点 —— 近似披露于回执）。

    用 ``shapely.transform`` 的 numpy 数组路径（每几何部件一次数组运算），
    不用 ``shapely.ops.transform`` 的逐坐标 Python 回调（200k 要素时差
    两个数量级 —— M8 性能证据）。
    """
    kx = _M_PER_DEG_LON_EQ * math.cos(math.radians(lat0))

    def _arr_to_m(coords: "np.ndarray") -> "np.ndarray":
        out = np.empty_like(coords)
        out[:, 0] = (coords[:, 0] - lon0) * kx
        out[:, 1] = (coords[:, 1] - lat0) * _M_PER_DEG_LAT
        if out.shape[1] > 2:
            out[:, 2] = coords[:, 2]
        return out

    def _arr_to_ll(coords: "np.ndarray") -> "np.ndarray":
        out = np.empty_like(coords)
        out[:, 0] = coords[:, 0] / kx + lon0
        out[:, 1] = coords[:, 1] / _M_PER_DEG_LAT + lat0
        if out.shape[1] > 2:
            out[:, 2] = coords[:, 2]
        return out

    def to_m(geom: Any) -> Any:
        return shapely_transform(geom, _arr_to_m)

    def to_ll(geom: Any) -> Any:
        return shapely_transform(geom, _arr_to_ll)

    return to_m, to_ll


def _geojson_geometry(geom: Any) -> Dict[str, Any]:
    """shapely 几何 → GeoJSON dict（坐标是 JSON 数组而非 tuple）。

    ``shapely.geometry.mapping`` 产出 tuple 嵌套；GeoJSON 契约是数组。
    内存 ref 不经 JSON 序列化直连消费方（渲染器/聚合），tuple 会让
    逐环 isinstance(list) 检查静默跳过 —— 在生产端一次规范化。
    """
    import json as _json

    return _json.loads(_json.dumps(_mapping(geom)))


def _feature_value(feature: Dict[str, Any], field: Optional[str]) -> Any:
    if not field:
        return None
    props = feature.get("properties")
    return props.get(field) if isinstance(props, dict) else None


def _stable_feature_id(feature: Dict[str, Any], index: int) -> str:
    fid = feature.get("id")
    if isinstance(fid, (str, int)):
        return str(fid)
    return f"__idx{index}"


def _simplify_one(
    geom_ll: Any,
    tol_m: float,
    to_m,
    to_ll,
    *,
    policy: GeometryGeneralizationPolicy,
    geom_m_pre: Optional[Any] = None,
) -> Tuple[Any, List[str], Dict[str, Any]]:
    """单几何有界简化 → (新 lon/lat 几何, 理由码, 观测)。

    ``geom_m_pre``：调用方已完成的米空间投影（多带复用同一解析/投影，
    性能路径）；缺席时自投影。不变量复核失败 → 返回 (原几何, 回退理由,
    观测)。空/None 几何原样。
    """
    observations: Dict[str, Any] = {
        "area_error": 0.0, "length_loss": 0.0, "rings_dropped": 0,
    }
    if geom_ll is None or geom_ll.is_empty or tol_m <= 0:
        return geom_ll, [], observations
    if geom_m_pre is not None:
        geom_m = geom_m_pre
    else:
        try:
            geom_m = to_m(geom_ll)
        except Exception:  # noqa: BLE001 — 投影失败（病态坐标）→ 原样
            return geom_ll, ["project_failed"], observations

    is_poly = geom_m.geom_type in ("Polygon", "MultiPolygon")
    is_line = geom_m.geom_type in ("LineString", "MultiLineString")

    # 微环显式丢弃（仅 policy 声明阈值时；计量在米空间）
    if is_poly and policy.min_ring_area_m2 is not None:
        geom_m, dropped = _drop_small_rings(geom_m, float(policy.min_ring_area_m2))
        observations["rings_dropped"] = dropped

    # 线亚像素快路径（DP 等价）：容差 ≥ 线全长时，任一中间顶点到端点弦
    # 的偏离 ≤ 全长/2 < 容差 —— DP 必然只留端点；直接构造端点线省去 GEOS
    # 逐点处理（200k 世界带的主流形态，M8 性能证据）。锚点守卫路径不走
    # 此捷径（锚点存活需真实简化复核）。
    if (
        geom_m_pre is not None
        and geom_m.geom_type == "LineString"
        and tol_m >= geom_m.length
        and len(geom_m.coords) > 2
    ):
        cs = list(geom_m.coords)
        fast = LineString([cs[0], cs[-1]])
        observations["length_loss"] = 0.0
        return to_ll(fast), ["simplified"], observations

    simplified = geom_m.simplify(tol_m, preserve_topology=True)
    if simplified.is_empty:
        return geom_ll, ["simplify_emptied"], observations

    # 不变量 1：类型族保持（Polygon↔MultiPolygon 同族；族翻转 = 拓扑变化）
    fam_in = _family(geom_m.geom_type)
    fam_out = _family(simplified.geom_type)
    if fam_in != fam_out:
        return geom_ll, ["family_changed"], observations
    # 不变量 2：有效性
    if not simplified.is_valid:
        return geom_ll, ["invalid_result"], observations
    # 不变量 3：部件数保持
    n_parts_in = _parts(geom_m)
    n_parts_out = _parts(simplified)
    if n_parts_in != n_parts_out:
        return geom_ll, ["parts_lost"], observations
    # 不变量 4：洞数保持（min_ring 丢弃已在计数中显式披露）
    if is_poly:
        holes_in = _holes(geom_m)
        holes_out = _holes(simplified)
        if holes_in - observations["rings_dropped"] != holes_out:
            return geom_ll, ["holes_lost"], observations
        area_in = geom_m.area
        if area_in > 0:
            ratio = abs(simplified.area - area_in) / area_in
            observations["area_error"] = ratio
            if ratio > policy.max_area_error_ratio:
                return geom_ll, ["area_error_exceeded"], observations
    # 不变量 5：线长损失界
    if is_line and geom_m.length > 0:
        loss = (geom_m.length - simplified.length) / geom_m.length
        observations["length_loss"] = loss
        if loss > _MAX_LENGTH_LOSS_RATIO:
            return geom_ll, ["length_loss_exceeded"], observations

    return to_ll(simplified), ["simplified"], observations


def _family(geom_type: str) -> str:
    if geom_type in ("Polygon", "MultiPolygon"):
        return "polygon"
    if geom_type in ("LineString", "MultiLineString", "LinearRing"):
        return "line"
    if geom_type in ("Point", "MultiPoint"):
        return "point"
    return geom_type


def _parts(geom: Any) -> int:
    return len(geom.geoms) if hasattr(geom, "geoms") else 1


def _holes(geom: Any) -> int:
    if geom.geom_type == "Polygon":
        return len(geom.interiors)
    if geom.geom_type == "MultiPolygon":
        return sum(len(p.interiors) for p in geom.geoms)
    return 0


def _drop_small_rings(geom_m: Any, min_area_m2: float) -> Tuple[Any, int]:
    """米空间 Polygon/MultiPolygon → 丢弃面积 < 阈值的洞/部件（计数）。"""
    if geom_m.geom_type == "Polygon":
        keep_ext = geom_m.exterior
        keep_int = [r for r in geom_m.interiors
                    if _ring_area_m2(r) >= min_area_m2]
        dropped = len(geom_m.interiors) - len(keep_int)
        from shapely.geometry import Polygon as _P
        return _P(keep_ext, keep_int), dropped
    if geom_m.geom_type == "MultiPolygon":
        parts_out: List[Any] = []
        dropped = 0
        for p in geom_m.geoms:
            keep_int = [r for r in p.interiors
                        if _ring_area_m2(r) >= min_area_m2]
            dropped += len(p.interiors) - len(keep_int)
            from shapely.geometry import Polygon as _P
            parts_out.append(_P(p.exterior, keep_int))
        from shapely.geometry import MultiPolygon as _MP
        return _MP(parts_out), dropped
    return geom_m, 0


def _ring_area_m2(ring: Any) -> float:
    from shapely.geometry import Polygon as _P
    return _P(ring).area


def _anchor_map(features: Sequence[Dict[str, Any]]) -> Dict[Tuple[float, float], int]:
    """坐标（1e-9 量化）→ 出现要素数；频次 ≥2 = 共享锚点。"""
    freq: Dict[Tuple[float, float], int] = {}
    for f in features:
        geom = f.get("geometry") if isinstance(f, dict) else None
        if not geom:
            continue
        try:
            coords = _all_coords(shape(geom))
        except Exception:  # noqa: BLE001
            continue
        seen: FrozenSet[Tuple[float, float]] = frozenset(
            (round(x, 9), round(y, 9)) for x, y in coords
        )
        for c in seen:
            freq[c] = freq.get(c, 0) + 1
    return freq


def generalize_features(
    features: Sequence[Dict[str, Any]],
    *,
    geometry_policy: GeometryGeneralizationPolicy,
    selection_policy: FeatureSelectionPolicy,
    latitude_hint: float = 0.0,
    band_zoom: Optional[float] = None,
    mandatory_ids: FrozenSet[str] = frozenset(),
) -> GeneralizationResult:
    """要素列表 ×（几何策略 × 取舍策略）→ 派生 FC + 回执（确定性）。"""
    features = list(features)
    input_count = len(features)
    tol_m = _tolerance_meters(geometry_policy, latitude_hint, band_zoom)
    lon0, lat0 = _dataset_origin(features)
    to_m, to_ll = _metric_transforms(lon0, lat0)

    # 共享锚点（仅 shared_boundary_protect 需要）
    anchors: Dict[Tuple[float, float], int] = {}
    anchors_total = 0
    if geometry_policy.strategy == "shared_boundary_protect":
        anchors = {c: n for c, n in _anchor_map(features).items() if n >= 2}
        anchors_total = len(anchors)

    reason_counts: Dict[str, int] = {}
    details: List[Dict[str, Any]] = []
    kept_features: List[Dict[str, Any]] = []
    suppressed = 0
    suppressed_by_importance = 0
    mandatory_saved = 0
    geometry_simplified = 0
    geometry_fallback = 0
    rings_dropped = 0
    max_area_error = 0.0
    max_length_loss = 0.0
    anchors_retained = 0
    selection_active = bool(
        selection_policy.kept_values and selection_policy.importance_field)
    kx = _M_PER_DEG_LON_EQ * math.cos(math.radians(lat0))

    def _bump(code: str) -> None:
        reason_counts[code] = reason_counts.get(code, 0) + 1

    for index, feature in enumerate(features):
        fid = _stable_feature_id(feature, index)
        outcomes: List[str] = []

        # ── 要素取舍（user-wins：mandatory 恒存活）──
        if selection_active:
            value = _feature_value(feature, selection_policy.importance_field)
            if value is None:
                outcomes.append("kept_unclassified")
            elif value in selection_policy.kept_values:
                outcomes.append("kept_by_importance")
            elif fid in mandatory_ids:
                outcomes.append("kept_mandatory")
                mandatory_saved += 1
            else:
                suppressed += 1
                suppressed_by_importance += 1
                outcomes.append("suppressed_by_importance")
                _bump("suppressed_by_importance")
                if len(details) < _RECEIPT_DETAIL_LIMIT:
                    details.append({"id": fid, "outcome": "suppressed",
                                    "reasons": list(outcomes)})
                continue
        else:
            outcomes.append("kept_no_selection")

        # ── 几何综合（浅拷贝保输入不被修改）──
        out_feature = copy.copy(feature)
        geom_dict = feature.get("geometry")
        new_geom_dict = geom_dict
        if (
            geom_dict is not None
            and geometry_policy.strategy in (
                "bounded_simplify", "shared_boundary_protect")
            and tol_m > 0
        ):
            try:
                geom_ll = shape(geom_dict)
            except Exception:  # noqa: BLE001 — 不可解析几何原样保留
                geom_ll = None
                outcomes.append("geometry_unparseable")
            if geom_ll is not None:
                result_ll, codes, obs = _apply_with_anchor_guard(
                    geom_ll, tol_m, to_m, to_ll, anchors, kx,
                    lat0, lon0, policy=geometry_policy)
                outcomes.extend(codes)
                rings_dropped += obs.get("rings_dropped", 0)
                max_area_error = max(max_area_error, obs.get("area_error", 0.0))
                max_length_loss = max(max_length_loss,
                                      obs.get("length_loss", 0.0))
                if "simplified" in codes:
                    geometry_simplified += 1
                    new_geom_dict = _geojson_geometry(result_ll)
                elif any(c.endswith(("_failed", "_emptied", "_changed",
                                     "_result", "_lost", "_exceeded"))
                         for c in codes):
                    geometry_fallback += 1
        if new_geom_dict is not geom_dict:
            out_feature = dict(out_feature)
            out_feature["geometry"] = new_geom_dict
        kept_features.append(out_feature)
        for c in outcomes:
            _bump(c)
        if len(details) < _RECEIPT_DETAIL_LIMIT:
            details.append({"id": fid, "outcome": "kept", "reasons": outcomes})

    # 锚点存活核验（shared_boundary_protect 的硬不变量）：逐要素守卫已
    # 保证锚点存活或整要素回退 —— 此处为集合级复核（回执披露；不一致
    # 时 strategy 守卫已把受影响要素回退原始几何，receipt.anchors_retained
    # < anchors_total 即消费方的红旗信号）。
    if geometry_policy.strategy == "shared_boundary_protect" and anchors:
        anchors_retained = _count_retained_anchors(kept_features, anchors)
    receipt = {
        "strategy": geometry_policy.strategy,
        "tolerance_meters": tol_m,
        "tolerance_units_declared": geometry_policy.tolerance_units,
        "projection": "equirectangular_local_approx",
        "input_count": input_count,
        "kept_count": len(kept_features),
        "suppressed_count": suppressed,
        "suppressed_by_importance": suppressed_by_importance,
        "mandatory_saved": mandatory_saved,
        "geometry_simplified": geometry_simplified,
        "geometry_fallback": geometry_fallback,
        "rings_dropped": rings_dropped,
        "max_area_error_ratio_observed": round(max_area_error, 9),
        "max_length_loss_ratio_observed": round(max_length_loss, 9),
        "anchors_total": anchors_total,
        "anchors_retained": anchors_retained,
        "stats_match": (len(kept_features) + suppressed) == input_count,
        "reason_counts": dict(sorted(reason_counts.items())),
        "per_feature": details,
    }
    return GeneralizationResult(
        feature_collection={"type": "FeatureCollection",
                            "features": kept_features},
        receipt=receipt,
    )


def _apply_with_anchor_guard(
    geom_ll: Any,
    tol_m: float,
    to_m,
    to_ll,
    anchors: Dict[Tuple[float, float], int],
    kx: float,
    lat0: float,
    lon0: float,
    *,
    policy: GeometryGeneralizationPolicy,
    geom_m_pre: Optional[Any] = None,
) -> Tuple[Any, List[str], Dict[str, Any]]:
    """有界简化 + 共享锚点存活守卫（策略为 protect 时逐次减半容差重试）。

    ``geom_m_pre``：复用的米空间投影（多带路径）；重试减半容差时投影
    不变（仿射换算线性 —— 复用安全）。
    """
    my_anchors: List[Tuple[float, float]] = []
    if policy.strategy == "shared_boundary_protect" and anchors:
        my_anchors = [c for c in _all_coords(geom_ll) if (
            round(c[0], 9), round(c[1], 9)) in anchors]

    tol = tol_m
    codes: List[str] = []
    obs: Dict[str, Any] = {}
    for attempt in range(_MAX_ANCHOR_RETRY + 1):
        candidate, codes, obs = _simplify_one(
            geom_ll, tol, to_m, to_ll, policy=policy, geom_m_pre=geom_m_pre)
        if "simplified" not in codes:
            return candidate, codes, obs
        if not my_anchors:
            return candidate, codes, obs
        if _anchors_present(candidate, my_anchors, tol):
            return candidate, codes, obs
        tol = tol / 2.0
        codes = [f"anchor_retry_{attempt}"]
    return geom_ll, ["anchor_lost_fallback"], obs


def _anchors_present(geom: Any, anchors: Sequence[Tuple[float, float]],
                     tol_m: float) -> bool:
    """所有锚点是否在几何上存活（距几何 ≤ tol/2 的米空间折算）。"""
    try:
        pts = _all_coords(geom)
    except Exception:  # noqa: BLE001
        return False
    if not pts:
        return False
    for ax, ay in anchors:
        if not any(
            (px - ax) ** 2 + (py - ay) ** 2 < (tol_m / 2.0 / 111320.0) ** 2
            for px, py in pts
        ):
            return False
    return True


def _count_retained_anchors(
    features: Sequence[Dict[str, Any]], anchors: Dict[Tuple[float, float], int]
) -> int:
    retained = 0
    for c in anchors:
        found = False
        for f in features:
            geom = f.get("geometry")
            if not geom:
                continue
            try:
                coords = _all_coords(shape(geom))
            except Exception:  # noqa: BLE001
                continue
            if any(abs(px - c[0]) < 1e-9 and abs(py - c[1]) < 1e-9
                   for px, py in coords):
                found = True
                break
        if found:
            retained += 1
    return retained


__all__ = [
    "GeneralizationResult",
    "generalize_features",
]


# ── 多带单遍综合（M8 性能路径：一次解析、多带复用）───────────────────────


class BandSpec(Tuple):  # type: ignore[misc]
    """单带规格（tier 键 + 几何/取舍策略 + 带端 zoom）。"""

    __slots__ = ()

    def __new__(cls, tier, geometry_policy, selection_policy, band_zoom=None):
        return super().__new__(cls, (tier, geometry_policy, selection_policy,
                                     band_zoom))

    @property
    def tier(self):
        return self[0]

    @property
    def geometry_policy(self):
        return self[1]

    @property
    def selection_policy(self):
        return self[2]

    @property
    def band_zoom(self):
        return self[3]


def generalize_features_multiband(
    features: Sequence[Dict[str, Any]],
    bands: Sequence[BandSpec],
    *,
    latitude_hint: float = 0.0,
    mandatory_ids: FrozenSet[str] = frozenset(),
) -> Dict[str, GeneralizationResult]:
    """单遍多带综合（与逐带 ``generalize_features`` 输出等价，见等价测试）。

    性能语义：要素解析（shape）/米空间投影/锚点频次各只算一次，B 个带
    复用 —— O(N·(parse+project) + B·N·simplify)，替代 B 独立调用的
    O(B·N·(parse+project))。回执逐带与单带调用同形。
    """
    features = list(features)
    input_count = len(features)
    if not bands:
        return {}
    lon0, lat0 = _dataset_origin(features)
    to_m, to_ll = _metric_transforms(lon0, lat0)
    kx = _M_PER_DEG_LON_EQ * math.cos(math.radians(lat0))

    any_protect = any(
        b.geometry_policy.strategy == "shared_boundary_protect" for b in bands)
    anchors: Dict[Tuple[float, float], int] = {}
    anchors_total = 0
    if any_protect:
        anchors = {c: n for c, n in _anchor_map(features).items() if n >= 2}
        anchors_total = len(anchors)

    # 预解析 + 预投影（一次）
    parsed: List[Tuple[Dict[str, Any], str, Any, Any]] = []  # (f, fid, ll, m)
    for index, feature in enumerate(features):
        fid = _stable_feature_id(feature, index)
        geom_dict = feature.get("geometry")
        geom_ll: Any = None
        if geom_dict is not None:
            try:
                geom_ll = shape(geom_dict)
            except Exception:  # noqa: BLE001 — 不可解析几何原样保留
                geom_ll = None
        geom_m: Any = None
        if geom_ll is not None and not geom_ll.is_empty:
            try:
                geom_m = to_m(geom_ll)
            except Exception:  # noqa: BLE001
                geom_m = None
        parsed.append((feature, fid, geom_ll, geom_m))

    results: Dict[str, GeneralizationResult] = {}
    for spec in bands:
        policy = spec.geometry_policy
        sel = spec.selection_policy
        tol_m = _tolerance_meters(policy, latitude_hint, spec.band_zoom)
        selection_active = bool(sel.kept_values and sel.importance_field)

        reason_counts: Dict[str, int] = {}
        details: List[Dict[str, Any]] = []
        kept_features: List[Dict[str, Any]] = []
        suppressed = suppressed_by_importance = 0
        mandatory_saved = geometry_simplified = geometry_fallback = 0
        rings_dropped = 0
        max_area_error = max_length_loss = 0.0

        def _bump(code: str) -> None:
            reason_counts[code] = reason_counts.get(code, 0) + 1

        for feature, fid, geom_ll, geom_m in parsed:
            outcomes: List[str] = []
            if selection_active:
                value = _feature_value(feature, sel.importance_field)
                if value is None:
                    outcomes.append("kept_unclassified")
                elif value in sel.kept_values:
                    outcomes.append("kept_by_importance")
                elif fid in mandatory_ids:
                    outcomes.append("kept_mandatory")
                    mandatory_saved += 1
                else:
                    suppressed += 1
                    suppressed_by_importance += 1
                    outcomes.append("suppressed_by_importance")
                    _bump("suppressed_by_importance")
                    if len(details) < _RECEIPT_DETAIL_LIMIT:
                        details.append({"id": fid, "outcome": "suppressed",
                                        "reasons": list(outcomes)})
                    continue
            else:
                outcomes.append("kept_no_selection")

            out_feature = copy.copy(feature)
            geom_dict = feature.get("geometry")
            new_geom_dict = geom_dict
            if (
                geom_ll is not None
                and geom_m is not None
                and policy.strategy in (
                    "bounded_simplify", "shared_boundary_protect")
                and tol_m > 0
            ):
                result_ll, codes, obs = _apply_with_anchor_guard(
                    geom_ll, tol_m, to_m, to_ll, anchors, kx, lat0, lon0,
                    policy=policy, geom_m_pre=geom_m)
                outcomes.extend(codes)
                rings_dropped += obs.get("rings_dropped", 0)
                max_area_error = max(max_area_error,
                                     obs.get("area_error", 0.0))
                max_length_loss = max(max_length_loss,
                                      obs.get("length_loss", 0.0))
                if "simplified" in codes:
                    geometry_simplified += 1
                    new_geom_dict = _geojson_geometry(result_ll)
                elif any(c.endswith(("_failed", "_emptied", "_changed",
                                     "_result", "_lost", "_exceeded"))
                         for c in codes):
                    geometry_fallback += 1
            elif geom_ll is None and geom_dict is not None:
                outcomes.append("geometry_unparseable")
            if new_geom_dict is not geom_dict:
                out_feature = dict(out_feature)
                out_feature["geometry"] = new_geom_dict
            kept_features.append(out_feature)
            for c in outcomes:
                _bump(c)
            if len(details) < _RECEIPT_DETAIL_LIMIT:
                details.append({"id": fid, "outcome": "kept",
                                "reasons": outcomes})

        anchors_retained = 0
        if policy.strategy == "shared_boundary_protect" and anchors:
            anchors_retained = _count_retained_anchors(kept_features, anchors)
        receipt = {
            "strategy": policy.strategy,
            "tolerance_meters": tol_m,
            "tolerance_units_declared": policy.tolerance_units,
            "projection": "equirectangular_local_approx",
            "input_count": input_count,
            "kept_count": len(kept_features),
            "suppressed_count": suppressed,
            "suppressed_by_importance": suppressed_by_importance,
            "mandatory_saved": mandatory_saved,
            "geometry_simplified": geometry_simplified,
            "geometry_fallback": geometry_fallback,
            "rings_dropped": rings_dropped,
            "max_area_error_ratio_observed": round(max_area_error, 9),
            "max_length_loss_ratio_observed": round(max_length_loss, 9),
            "anchors_total": anchors_total,
            "anchors_retained": anchors_retained,
            "stats_match": (len(kept_features) + suppressed) == input_count,
            "reason_counts": dict(sorted(reason_counts.items())),
            "per_feature": details,
        }
        results[spec.tier] = GeneralizationResult(
            feature_collection={"type": "FeatureCollection",
                                "features": kept_features},
            receipt=receipt,
        )
    return results

