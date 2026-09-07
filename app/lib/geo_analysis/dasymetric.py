"""分区密度重分配（dasymetric reallocation）—— dasymetric_map 的计算层。

语义（Wright 1936 经典分区密度图的现代化面插值变体）：

- 输入 = 源统计面（行政区等，携带**总量语义**的数值字段）+ 控制要素面
  （土地利用/居住区等，可携带权重字段）；
- 每个源面被控制面切割成若干相交碎片（fragment），碎片权重 =
  控制密度 d_j = w_j / A_j × 碎片面积（权重缺失/全零时退化为**纯面积
  权重**的等面积插值，退化逐源计数披露 —— 从不静默）；
- 源值按碎片权重占比分配（总量守恒：Σ 碎片值 = 源值，浮点舍入前精确）；
- 输出 = 碎片面要素集（polygon_feature_set）：几何是 source∩control，
  属性携带 source_id / ancillary_id / 分配值 / 密度 / 面积 / 方法标签。

红线：
- **总量（extensive）语义**：value_field 必须是可加总量（人口/户数/建筑
  面积）。比率/密度（intensive）不可重分配 —— 需先乘以分母换算成总量；
- 负值钳制到 0（负总量无密度语义），钳制计数披露；
- 无控制覆盖的源面保留整面 + 全值（method=no_ancillary_coverage），
  绝不静默丢面/丢值；
- 有界：源/控制要素数上限硬拒绝（先拒绝不 OOM）；
- 确定性：源按输入序、碎片按控制索引序固定累加顺序，无 RNG，
  同输入逐位一致。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

import geopandas as gpd

from app.lib.geo_processor.core import GeoAnalysisResult, to_utm_gdf
from app.lib.geo_analysis.evidence import build_quality_evidence

logger = logging.getLogger(__name__)

#: 有界硬上限（§11 先拒绝不 OOM）：面×面相交经 sindex 剪枝后近似线性，
#: 但输入面数本身仍需封顶。
DASYMETRIC_MAX_SOURCE_FEATURES = 20_000
DASYMETRIC_MAX_ANCILLARY_FEATURES = 50_000

#: 碎片面积下界（m²）：退化碎片（缝/点接触）丢弃，不计入权重
_MIN_FRAGMENT_AREA_M2 = 1e-6

#: 输出数值统一舍入位（与 od_flow_edges 同约定）
_COORD_DECIMALS = 6

_METHOD_ANCILLARY = "ancillary_weighted"
_METHOD_AREA = "area_proportional"
_METHOD_UNCOVERED = "no_ancillary_coverage"
_METHOD_NO_VALUE = "no_value"


def _fc_to_gdf(geojson: Any) -> Tuple[Optional[gpd.GeoDataFrame], str]:
    """GeoJSON（dict/str）→ UTM GeoDataFrame。失败返回 (None, "")。"""
    res = to_utm_gdf(geojson)
    if res is None or res[0] is None:
        return None, ""
    gdf, crs = res
    if gdf.empty:
        return None, str(crs)
    return gdf, str(crs)


def _clean_geometry_layer(gdf: gpd.GeoDataFrame) -> Tuple[gpd.GeoDataFrame, int]:
    """丢弃空/无效几何（计数披露；确定性保序）。"""
    mask = gdf.geometry.notna() & ~gdf.geometry.is_empty
    dropped = int((~mask).sum())
    return gdf[mask].copy(), dropped


def _parse_weights(
    ancillary: gpd.GeoDataFrame, weight_field: str
) -> Tuple[Optional[List[float]], Dict[str, int]]:
    """控制面权重解析：NaN→0（计数）、负值钳 0（计数）。

    返回 (weights 或 None, 披露计数)。weight_field 为空或字段缺失时
    返回 None —— 调用方退化为纯面积权重（area-proportional）。
    """
    stats = {"nan_weights": 0, "clamped_weights": 0}
    if not weight_field or weight_field not in ancillary.columns:
        return None, stats
    weights: List[float] = []
    raw = ancillary[weight_field].tolist()
    for v in raw:
        try:
            w = float(v)
        except (TypeError, ValueError):
            w = 0.0
            stats["nan_weights"] += 1
            weights.append(w)
            continue
        if w != w:  # NaN
            w = 0.0
            stats["nan_weights"] += 1
        elif w < 0.0:
            w = 0.0
            stats["clamped_weights"] += 1
        weights.append(w)
    return weights, stats


def dasymetric_reallocation(
    source_geojson: dict | str,
    ancillary_geojson: dict | str,
    value_field: str,
    weight_field: str = "",
    output_crs: str | None = "EPSG:4326",
) -> GeoAnalysisResult:
    """分区密度重分配：源面统计量按控制要素面权重比例切分到碎片。

    Args:
        source_geojson: 源统计面 FeatureCollection（携带 value_field 总量）。
        ancillary_geojson: 控制要素面 FeatureCollection（可选 weight_field）。
        value_field: 源面数值字段名（**总量语义**；缺失字段结构化拒绝）。
        weight_field: 控制面权重字段名（空 = 纯面积权重插值）。
            语义红线（Review R1 GIS F7）：权重必须是控制面内的**总量语义**
            （居住人口/建筑面积等计数，Eicher–Brewer 平均加权）；相对/适宜性
            权重会引入控制面尺寸偏差，需先换算成计数再传入。
        output_crs: 输出 CRS（默认 EPSG:4326；None = 保留工作 UTM 帧）。

    Returns:
        GeoAnalysisResult，data = 碎片面 FeatureCollection（含 metadata 披露）。
    """
    try:
        if not value_field:
            return GeoAnalysisResult(
                False, None, "value_field is required",
                error_type="ValueError",
                correction_hint="显式指定源统计面的总量字段名（如 population）",
            )

        src, src_crs = _fc_to_gdf(source_geojson)
        if src is None:
            return GeoAnalysisResult(
                False, None, "Invalid source GeoJSON (empty or unparseable)",
                error_type="ValueError",
                correction_hint="源统计面需要非空 Polygon/MultiPolygon FeatureCollection",
            )
        anc, anc_crs = _fc_to_gdf(ancillary_geojson)
        if anc is None:
            return GeoAnalysisResult(
                False, None, "Invalid ancillary GeoJSON (empty or unparseable)",
                error_type="ValueError",
                correction_hint="控制要素面需要非空 Polygon/MultiPolygon FeatureCollection",
            )
        # 统一到源的工作帧（米制）再算面积/相交
        if anc_crs != src_crs:
            anc = anc.to_crs(src_crs)

        if len(src) > DASYMETRIC_MAX_SOURCE_FEATURES:
            return GeoAnalysisResult(
                False, None,
                f"source features {len(src)} exceed cap "
                f"{DASYMETRIC_MAX_SOURCE_FEATURES}",
                error_type="ValueError",
            )
        if len(anc) > DASYMETRIC_MAX_ANCILLARY_FEATURES:
            return GeoAnalysisResult(
                False, None,
                f"ancillary features {len(anc)} exceed cap "
                f"{DASYMETRIC_MAX_ANCILLARY_FEATURES}",
                error_type="ValueError",
            )
        if value_field not in src.columns:
            return GeoAnalysisResult(
                False, None,
                f"source layer is missing value field '{value_field}'",
                error_type="ValueError",
                correction_hint=f"源面可用的数值字段: "
                                f"{[c for c in src.columns if c != 'geometry'][:12]}",
            )

        src, src_dropped = _clean_geometry_layer(src)
        anc, anc_dropped = _clean_geometry_layer(anc)
        if src.empty:
            return GeoAnalysisResult(
                False, None, "no valid source polygons after geometry cleaning",
                error_type="ValueError",
            )
        if anc.empty:
            return GeoAnalysisResult(
                False, None, "no valid ancillary polygons after geometry cleaning",
                error_type="ValueError",
                correction_hint="控制要素面覆盖为空 —— dasymetric 需要控制层"
                                "（如居住区/土地利用）；无控制层请直接用 choropleth",
            )

        weights, wstats = _parse_weights(anc, weight_field)
        weight_applied = weights is not None
        anc_areas = anc.geometry.area.to_numpy()  # m²（工作 UTM 帧）
        anc_geoms = anc.geometry.to_numpy()
        anc_idx = anc.index.to_numpy()
        sindex = anc.sindex

        fragments: List[Dict[str, Any]] = []
        # ── 逐法披露计数（诚实降级，从不静默）───────────────────────────
        n_clamped_values = 0
        n_area_fallback = 0      # 权重全零但有覆盖 → 面积比例
        n_uncovered = 0          # 无控制覆盖 → 整面保值
        n_no_value = 0           # 值缺失/非数值 → 整面保空值
        n_degenerate_dropped = 0  # Review R1（GIS F9）：退化碎片丢弃计数

        for src_idx, src_row in src.iterrows():
            geom = src_row.geometry
            # 源值解析：缺失/非数值不编造 0 —— 整面保留空值并计数
            raw_value = src_row.get(value_field)
            try:
                value = float(raw_value)
                if value != value:  # NaN
                    raise ValueError
            except (TypeError, ValueError):
                fragments.append(_source_only_fragment(
                    src_idx, geom, value_field, None, _METHOD_NO_VALUE,
                    source_id=_source_id(src_idx, src_row)))
                n_no_value += 1
                continue
            if value < 0.0:
                value = 0.0
                n_clamped_values += 1

            # sindex 候选（返回升序数组 → 确定性累加序）
            cand = sorted(int(i) for i in
                          sindex.query(geom, predicate="intersects"))
            pieces: List[Tuple[float, float, Any, int]] = []  # (weight, area, geom, anc_pos)
            for pos_i, i in enumerate(cand):
                inter = geom.intersection(anc_geoms[i])
                if inter.is_empty:
                    continue
                a = float(inter.area)
                if a <= _MIN_FRAGMENT_AREA_M2:
                    # Review R1（GIS F9）：退化碎片（缝/点接触）丢弃也要计数
                    # ——「诚实降级，从不静默」对本模块自己的丢弃同样适用。
                    n_degenerate_dropped += 1
                if weight_applied:
                    zone_area = float(anc_areas[i])
                    if zone_area <= 0.0:
                        w = 0.0
                    else:
                        # 控制密度 d_j = w_j / A_j × 碎片面积
                        w = (weights[i] / zone_area) * a
                else:
                    w = a
                pieces.append((w, a, inter, i))

            if not pieces:
                fragments.append(_source_only_fragment(
                    src_idx, geom, value_field, value, _METHOD_UNCOVERED,
                    source_id=_source_id(src_idx, src_row)))
                n_uncovered += 1
                continue

            total_w = 0.0
            for w, _a, _g, _i in pieces:
                total_w += w
            method = _METHOD_ANCILLARY if weight_applied else _METHOD_AREA
            if total_w <= 0.0:
                # 权重全零（覆盖存在）→ 纯面积权重（总量仍守恒）
                method = _METHOD_AREA
                pieces = [(a, a, g, i) for (w, a, g, i) in pieces]
                total_w = 0.0
                for a, _a2, _g, _i in pieces:
                    total_w += a
                n_area_fallback += 1

            src_id = _source_id(src_idx, src_row)
            for w, a, inter, i in pieces:
                share = (w / total_w) if total_w > 0.0 else 0.0
                fragments.append({
                    "type": "Feature",
                    "id": f"{src_id}:{anc_idx[i]}",
                    "geometry": inter.__geo_interface__,
                    "properties": {
                        "source_id": src_id,
                        "ancillary_id": str(anc_idx[i]),
                        value_field: round(value * share, _COORD_DECIMALS),
                        "value_share": round(share, _COORD_DECIMALS),
                        "density": round(
                            (value * share) / a if a > 0 else 0.0,
                            _COORD_DECIMALS),
                        "area_m2": round(a, 3),
                        "method": method,
                    },
                })

        out_crs = str(src_crs)
        fc: Dict[str, Any] = {
            "type": "FeatureCollection",
            "features": fragments,
            "metadata": {
                "dasymetric": True,
                "value_field": value_field,
                "weight_field": weight_field or "",
                "weight_applied": weight_applied,
                "method": (_METHOD_ANCILLARY if weight_applied
                           else _METHOD_AREA),
                # Review R1（GIS F8）：有效方法 —— 全部源都退化成面积比例时
                # 顶层 method 不再虚标 ancillary_weighted（碎片级标签恒真实）。
                "effective_method": (
                    _METHOD_AREA if (weight_applied and n_area_fallback > 0
                                     and n_area_fallback >= int(len(src)))
                    else (_METHOD_ANCILLARY if weight_applied else _METHOD_AREA)
                ),
                "sources": int(len(src)),
                "ancillary_zones": int(len(anc)),
                "fragments": len(fragments),
                "clamped_negative_values": n_clamped_values,
                "area_proportional_fallback": n_area_fallback,
                "uncovered_sources": n_uncovered,
                "no_value_sources": n_no_value,
                "dropped_degenerate_fragments": n_degenerate_dropped,
                "nan_weights": int(wstats.get("nan_weights", 0)),
                "clamped_weights": int(wstats.get("clamped_weights", 0)),
                "dropped_invalid_source": src_dropped,
                "dropped_invalid_ancillary": anc_dropped,
                # 总量守恒：碎片值之和 = 源值之和（舍入前精确）。
                # Review R1（GIS F10）：负值钳制改变输入总量 —— 此时对
                # 「输入总量」而言不再守恒，如实降级为 False。
                "mass_conserving": n_clamped_values == 0,
                "note": "碎片=source∩control；value_field 必须是总量语义"
                        "（可加），比率字段不可重分配",
            },
        }

        if output_crs and out_crs != str(output_crs):
            gdf_out = gpd.GeoDataFrame.from_features(
                fragments, crs=out_crs).to_crs(output_crs)
            fc = {
                "type": "FeatureCollection",
                "features": [
                    {**f, "geometry": g.__geo_interface__}
                    for f, g in zip(fragments, gdf_out.geometry)
                ],
                "metadata": fc["metadata"],
            }

        return GeoAnalysisResult(
            success=True,
            data=fc,
            summary=(
                f"Dasymetric reallocation: {len(src)} sources × "
                f"{len(anc)} ancillary zones → {len(fragments)} fragments "
                f"({'ancillary-weighted' if weight_applied else 'area-proportional'})."
            ),
            evidence=build_quality_evidence(
                input_count=int(len(src)),
                output_count=len(fragments),
                working_crs=str(src_crs),
                dropped_invalid=src_dropped + anc_dropped,
                empty_count=n_uncovered,
                extra={
                    "method": fc["metadata"]["method"],
                    "clamped_negative_values": n_clamped_values,
                    "area_proportional_fallback": n_area_fallback,
                    "uncovered_sources": n_uncovered,
                },
            ),
        )
    except Exception as e:  # noqa: BLE001 — 有界披露失败（不静默崩溃）
        logger.error("[dasymetric] reallocation failed: %s", e, exc_info=True)
        return GeoAnalysisResult(False, None, f"Dasymetric reallocation failed: {e}")


def _source_id(src_idx: Any, src_row: Any) -> str:
    """源面稳定 id：显式 id/name 属性优先，退回输入行索引。"""
    for cand in ("id", "name", "code"):
        v = src_row.get(cand)
        if v is not None and str(v) != "":
            return str(v)
    return str(src_idx)


def _source_only_fragment(
    src_idx: Any,
    src_geom: Any,
    value_field: str,
    value: Optional[float],
    method: str,
    source_id: str = "",
) -> Dict[str, Any]:
    """无控制覆盖/无值源面的整面碎片（保形保值，诚实标签）。"""
    area = float(src_geom.area)
    return {
        "type": "Feature",
        "id": f"{source_id or src_idx}",
        "geometry": src_geom.__geo_interface__,
        "properties": {
            "source_id": source_id or str(src_idx),
            "ancillary_id": "",
            value_field: value,
            "value_share": None,
            "density": None,
            "area_m2": round(area, 3),
            "method": method,
        },
    }
