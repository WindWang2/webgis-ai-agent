"""面小面积消除/合并引擎（WP2b）—— 目标尺度最小可辨面斑的选择性合并。

定位（与既有引擎的分工）：

- ``generalize.py``：几何简化（顶点增删），不改要素集合；
- ``topology.py``（WP2a）：共享边一致简化，不改要素集合；
- 本模块：**消除/合并**（eliminate）——低于目标尺度最小可辨面积
  （paper_scale.minimum_mappable_area_m2 的产物）的面要素按「共享边界
  最长」确定性并入幸存邻居，几何 = union，成员归属进 manifest。

专题量纲纪律（goal WP2 硬约束）：

- ``sum_fields``（计数/总量字段）：幸存者显示值 = Σ(自身 + 被吸收成员)，
  manifest 记录逐次贡献 —— 合并不伪造统计（分类口径仍 original_source）；
- 未声明 sum_fields 的数值字段：幸存者**保留自身值**（不平均、不臆造
  权重），回执披露发散计数 —— 比例/率在分母未知时拒绝重算，宁可不并；
- ``protected_ids``（user-wins 硬约束）与 ``keep_region``（重点区域 bbox
  内要素）恒不被消除；无邻居的孤立小面**保留**并披露（不静默删除）；
- 输入要素永不被修改（输出为拷贝 + 覆盖属性）。

确定性：候选按 (面积, fid) 升序处理；共享边界强度并列取输入序靠前者；
union 结果确定；同输入恒同输出。
"""
from __future__ import annotations

import copy
import math
from typing import Any, Dict, List, Optional, Sequence

from pydantic import BaseModel, ConfigDict
from shapely.geometry import Polygon, shape
from shapely.geometry.base import BaseGeometry

# 同包单例复用：局部等距投影与 GeoJSON 规范化（单一事实源纪律）。
from app.lib.cartography.multiscale.generalize import (
    _dataset_origin,
    _geojson_geometry,
    _metric_transforms,
    _stable_feature_id,
)

#: 回执 merges 明细上限（溢出只计数）。
_RECEIPT_MERGE_LIMIT = 64


class EliminationResult(BaseModel):
    """消除/合并产物：幸存者 FC + manifest + 回执（可序列化、确定性）。"""

    model_config = ConfigDict(extra="allow")

    feature_collection: Dict[str, Any]
    manifest: Dict[str, Dict[str, Any]]
    receipt: Dict[str, Any]


def _num(v: Any) -> Optional[float]:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    f = float(v)
    return f if math.isfinite(f) else None


def _noop_result(features: Sequence[Dict[str, Any]],
                 min_area_m2: float) -> EliminationResult:
    return EliminationResult(
        feature_collection={"type": "FeatureCollection",
                            "features": [copy.copy(dict(f)) for f in features]},
        manifest={},
        receipt={
            "engine": "polygon_eliminate_v1",
            "min_area_m2": float(min_area_m2),
            "input_count": len(features),
            "kept_count": len(features),
            "eliminated_count": 0,
            "merge_count": 0,
            "isolated_kept": 0,
            "protected_count": 0,
            "display_divergence": 0,
            "stats_match": True,
            "merges": [],
            "disclosures": ([] if features else ["空输入直通"]),
        },
    )


def eliminate_small_polygons(
    features: Sequence[Dict[str, Any]],
    *,
    min_area_m2: float,
    latitude_hint: float = 0.0,
    protected_ids: frozenset = frozenset(),
    keep_region: Optional[Sequence[float]] = None,
    sum_fields: Sequence[str] = (),
) -> EliminationResult:
    """面要素列表 → 幸存者 FC + manifest + 回执（确定性）。

    - ``min_area_m2``：米²（paper_scale.minimum_mappable_area_m2 的产物；
      ≤0 = 不消除，全部保留）；
    - ``protected_ids``：恒保留要素 id（user-wins）；
    - ``keep_region``：[minx,miny,maxx,maxy]，完全落入区域内的要素恒保留；
    - ``sum_fields``：计数/总量字段（幸存者显示值 = Σ 成员数值）。
    """
    features = list(features)
    input_count = len(features)
    if min_area_m2 <= 0 or input_count == 0:
        return _noop_result(features, min_area_m2)
    lon0, lat0 = _dataset_origin(features)
    to_m, to_ll = _metric_transforms(lon0, lat0)

    # keep_region（bbox → Polygon；非法区域 = 无保护区域，不猜测）
    region_box: Optional[Polygon] = None
    if isinstance(keep_region, (list, tuple)) and len(keep_region) == 4:
        try:
            x0, y0, x1, y1 = (float(v) for v in keep_region)
            if x0 < x1 and y0 < y1:
                region_box = Polygon(
                    [(x0, y0), (x1, y0), (x1, y1), (x0, y1)])
        except (TypeError, ValueError):
            region_box = None

    # ── 解析（输入序恒定；几何/面积/保护态一次算清；输入不被修改）──
    fids: List[str] = []
    geoms: List[Optional[BaseGeometry]] = []
    areas_m2: List[float] = []
    protected: List[bool] = []
    for index, feature in enumerate(features):
        fid = _stable_feature_id(feature, index)
        fids.append(fid)
        geom: Optional[BaseGeometry] = None
        gd = feature.get("geometry")
        if isinstance(gd, dict):
            try:
                g = shape(gd)
                if g.geom_type in ("Polygon", "MultiPolygon"):
                    geom = g
            except Exception:  # noqa: BLE001 — 坏几何保留不消除
                geom = None
        geoms.append(geom)
        try:
            areas_m2.append(0.0 if geom is None else float(to_m(geom).area))
        except Exception:  # noqa: BLE001 — 投影失败按不可消除处理
            areas_m2.append(0.0)
            geom = None
            geoms[index] = None
        is_protected = (
            fid in protected_ids
            or geoms[index] is None
            or (region_box is not None and bool(geoms[index].within(region_box)))
        )
        protected.append(is_protected)

    alive: List[int] = [i for i in range(input_count) if geoms[i] is not None]
    survivors_overlay_props: Dict[int, Dict[str, Any]] = {}
    eliminated: Dict[str, Dict[str, Any]] = {}
    isolated_kept = 0
    display_divergence = 0
    merges: List[Dict[str, Any]] = []
    merges_truncated = False

    # 候选序：面积升序（并列按 fid），小者先处理（允许链式吸收：小面并入
    # 的幸存者若自身也是候选，后续仍可再并入其邻居）
    candidates = sorted(
        (i for i in alive
         if not protected[i] and areas_m2[i] < float(min_area_m2)),
        key=lambda i: (areas_m2[i], fids[i]),
    )

    def _shared_strength(a: BaseGeometry, b: BaseGeometry) -> float:
        """共享边界强度：邻接线长；面重叠（脏数据）按面积放大恒优先。"""
        try:
            inter = a.intersection(b)
        except Exception:  # noqa: BLE001 — 相交计算失败按无邻居
            return 0.0
        if inter.is_empty:
            return 0.0
        t = inter.geom_type
        if t in ("LineString", "MultiLineString", "LinearRing"):
            return float(inter.length)
        if t.startswith(("Polygon", "MultiPolygon")):
            return float(inter.area) * 1000.0
        return 0.0

    alive_set = set(alive)
    for idx in candidates:
        if geoms[idx] is None or idx not in alive_set:
            continue  # 已被链式吸收
        # 幸存邻居查询：线性扫描**当前幸存者**（行政区等面集合通常数百
        # 量级；逐次动态合并下直接确定性扫描，被吸收者已出 alive_set）
        best: Optional[int] = None
        best_strength = 0.0
        for j in sorted(alive_set):
            if j == idx or geoms[j] is None:
                continue
            strength = _shared_strength(geoms[idx], geoms[j])
            if strength > best_strength:
                best, best_strength = j, strength
            elif strength == best_strength and strength > 0 and best is not None \
                    and j < best:
                best = j
        if best is None or best_strength <= 0:
            isolated_kept += 1
            continue  # 孤立小面：保留并披露（不静默删除）
        # 合并：米空间 union → 投回 lon/lat（失败 = 诚实降级保留两者）
        try:
            merged_m = to_m(geoms[best]).union(to_m(geoms[idx]))
            merged_ll = to_ll(merged_m)
        except Exception:  # noqa: BLE001
            isolated_kept += 1
            continue
        if merged_ll is None or merged_ll.is_empty:
            isolated_kept += 1
            continue
        # sum_fields：幸存者显示值 = Σ(自身 + 被吸收成员数值)。链式吸收时
        # idx 此前吸收他人的增量在其 overlay 中 —— 取「原始 ∪ overlay」的
        # 有效值作为贡献（评审 R1-P1-1：只取原始属性会丢中间增量，
        # 幸存链显示值 ≠ Σ成员）。
        src_overlay = survivors_overlay_props.get(idx, {})
        src_props = features[idx].get("properties") if isinstance(
            features[idx].get("properties"), dict) else {}
        sum_delta: Dict[str, float] = {}
        for field in sum_fields:
            v = _num(src_overlay.get(field))
            if v is None:
                v = _num(src_props.get(field))
            if v is not None:
                sum_delta[field] = sum_delta.get(field, 0.0) + v
        # 非 sum_fields 的数值字段发散计数（幸存者不并值 → 显示发散披露）
        for field, v in src_props.items():
            if field in sum_fields:
                continue
            if _num(v) is not None:
                display_divergence += 1
                break  # 每个被吸收要素计一次
        eliminated[fids[idx]] = {
            "absorbed_into": fids[best],
            "area_m2": round(areas_m2[idx], 3),
            "shared_boundary_strength_deg": round(best_strength, 9),
            "sum_fields_delta": {k: round(v, 9) for k, v in sum_delta.items()},
        }
        if len(merges) < _RECEIPT_MERGE_LIMIT:
            merges.append({"eliminated": fids[idx], "survivor": fids[best],
                           "area_m2": round(areas_m2[idx], 3)})
        else:
            merges_truncated = True
        overlay = survivors_overlay_props.setdefault(best, {})
        for field, delta in sum_delta.items():
            base = _num(overlay.get(field))
            if base is None:
                base = _num((features[best].get("properties") or {}).get(field))
                base = base if base is not None else 0.0
            overlay[field] = base + delta
        # idx 若还持有此前链式吸收的 overlay（理论上全在 sum_delta 中，
        # 防御性转移未知键），一并迁给 best —— 链式语义：增量随几何迁移。
        idx_overlay = survivors_overlay_props.pop(idx, None)
        if idx_overlay:
            for field, v in idx_overlay.items():
                if field in sum_delta:
                    continue
                merged = survivors_overlay_props.setdefault(best, {})
                base = _num(merged.get(field))
                if base is None:
                    base = _num((features[best].get("properties") or {})
                                .get(field))
                    base = base if base is not None else 0.0
                merged[field] = base + (_num(v) or 0.0)
        # 应用合并（幸存者几何替换；被吸收者移出幸存集合）
        geoms[best] = merged_ll
        areas_m2[best] = areas_m2[best] + areas_m2[idx]
        alive_set.discard(idx)

    # 幸存者 FC（保序；几何/属性用覆盖值，输入对象不被修改）
    out_features: List[Dict[str, Any]] = []
    for i in range(input_count):
        if i not in alive_set:
            continue
        feature = copy.copy(features[i])
        if i in survivors_overlay_props:
            feature = dict(feature)
            props = dict(feature.get("properties") or {})
            props.update(survivors_overlay_props[i])
            feature["properties"] = props
        if geoms[i] is not None:
            try:
                out_geom = _geojson_geometry(geoms[i])
            except Exception:  # noqa: BLE001 — 规范化失败保留原几何
                out_geom = features[i].get("geometry")
            feature = dict(feature)
            feature["geometry"] = out_geom
        out_features.append(feature)

    disclosures: List[str] = []
    if eliminated:
        disclosures.append(
            f"{len(eliminated)} 个小面积要素按共享边界最长并入幸存邻居"
            "（display-only；统计/图例口径仍 original_source，成员归属见 manifest）")
        if display_divergence:
            disclosures.append(
                f"{display_divergence} 个被吸收要素携带未声明 sum_fields 的数值"
                "字段 —— 幸存者显示值未并入这些值（不平均、不臆造权重；率/比"
                "在分母未知时拒绝重算）")
    if isolated_kept:
        disclosures.append(
            f"{isolated_kept} 个无共享邻居的小面保留（不静默删除）")

    receipt = {
        "engine": "polygon_eliminate_v1",
        "min_area_m2": float(min_area_m2),
        "input_count": input_count,
        "kept_count": len(out_features),
        "eliminated_count": len(eliminated),
        "merge_count": len(merges),
        "merges_truncated": merges_truncated,
        "isolated_kept": isolated_kept,
        "protected_count": sum(1 for p in protected if p),
        "display_divergence": display_divergence,
        "stats_match": len(out_features) + len(eliminated) == input_count,
        "merges": merges,
        "disclosures": disclosures,
    }
    return EliminationResult(
        feature_collection={"type": "FeatureCollection",
                            "features": out_features},
        manifest=eliminated,
        receipt=receipt,
    )


__all__ = [
    "EliminationResult",
    "eliminate_small_polygons",
]
