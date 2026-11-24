"""点要素可追溯聚合（WP2d）—— 目标尺度格网聚合 + 成员/计数守恒。

定位（与既有面的分工）：

- ``profile.py``/``plan.py`` 的点族去密只声明**渲染模式**（point_cluster /
  visual_heatmap，无工件、无成员归属）；
- 本模块产出**真实聚合工件**：目标尺度格网（cell_size_m 经纸面契约换算）
  内的点确定性地并为一个代表要素 —— 成员 id 全量保留、计数守恒、
  sum_fields 求和守恒（计数/总量专题上图仍可与原始数据对账）。

语义纪律：

- **display-only**：聚合代表要素只用于显示；统计/图例口径恒
  original_source（回执强制披露）；分析通道继续读原始 ref；
- **可追溯**：manifest 记录每格 member_ids（全量，受回执明细帽约束时
  溢出计数）与 sum 贡献 —— 任何聚合点都可回查成员；
- 单点格不聚合（原样直通，诚实统计）；``protected_ids``（user-wins）
  恒直通（即使与它点同格）；
- 确定性：格锚定 (0,0)（floor(lon/cell_lon), floor(lat/cell_lat)，局部
  等距换算与引擎族同口径并披露），与数据集无关 → 子集/乱序同格。
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Sequence, Tuple

from pydantic import BaseModel, ConfigDict

#: 米/度换算（与 generalize 引擎同族常数）。
_M_PER_DEG_LAT = 110_574.0
_M_PER_DEG_LON_EQ = 111_320.0

#: 单格成员 id 明细帽（防病理密度放大；溢出计数披露）。
_MEMBER_ID_LIMIT = 256

#: 回执 groups 明细帽。
_RECEIPT_GROUP_LIMIT = 64


class PointAggregateResult(BaseModel):
    """点聚合产物：聚合 FC + manifest + 回执（可序列化、确定性）。"""

    model_config = ConfigDict(extra="allow")

    feature_collection: Dict[str, Any]
    manifest: Dict[str, Dict[str, Any]]
    receipt: Dict[str, Any]


def _num(v: Any) -> Optional[float]:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    f = float(v)
    return f if math.isfinite(f) else None


def _stable_feature_id(feature: Dict[str, Any], index: int) -> str:
    fid = feature.get("id")
    if isinstance(fid, (str, int)):
        return str(fid)
    return f"__idx{index}"


def _point_coord(feature: Dict[str, Any]) -> Optional[Tuple[float, float]]:
    geom = feature.get("geometry")
    if not isinstance(geom, dict) or geom.get("type") != "Point":
        return None
    coords = geom.get("coordinates")
    if not (isinstance(coords, (list, tuple)) and len(coords) >= 2):
        return None
    try:
        x, y = float(coords[0]), float(coords[1])
    except (TypeError, ValueError):
        return None
    if not (math.isfinite(x) and math.isfinite(y)):
        return None
    return (x, y)


def aggregate_points(
    features: Sequence[Dict[str, Any]],
    *,
    cell_size_m: float,
    latitude_hint: float = 0.0,
    sum_fields: Sequence[str] = (),
    protected_ids: frozenset = frozenset(),
    keep_singleton: bool = True,
) -> PointAggregateResult:
    """点要素列表 → 格网聚合 FC + manifest + 回执（确定性）。

    - ``cell_size_m``：格网地面尺寸（米；服务层经纸面契约换算，缺省建议
      数个纸面 mm 的地面覆盖）；≤0 = 不聚合全直通；
    - ``sum_fields``：计数/总量字段（代表要素值 = Σ 成员，manifest 记账）；
    - ``protected_ids``：恒直通（不参与聚合）；
    - ``keep_singleton``：单点格保持原要素（False 时单点格也出代表要素
      —— 仅用于显示一致性实验，缺省诚实直通）。
    """
    features = list(features)
    input_count = len(features)
    if cell_size_m <= 0 or input_count == 0:
        return PointAggregateResult(
            feature_collection={"type": "FeatureCollection",
                                "features": [dict(f) for f in features]},
            manifest={},
            receipt={"engine": "point_aggregate_v1",
                     "cell_size_m": float(cell_size_m),
                     "input_count": input_count, "aggregated_count": 0,
                     "representative_count": 0, "passthrough_count": input_count,
                     "groups": [], "stats_match": True,
                     "disclosures": [] if input_count else ["空输入直通"]},
        )

    # 格尺寸（度；锚定 (0,0) 的 floor 格 —— 与数据集无关、子集稳定）
    lat = max(-85.0, min(85.0, float(latitude_hint)))
    kx = _M_PER_DEG_LON_EQ * math.cos(math.radians(lat))
    cell_lon_deg = float(cell_size_m) / max(kx, 1e-9)
    cell_lat_deg = float(cell_size_m) / _M_PER_DEG_LAT

    def _cell_of(lon: float, lat_y: float) -> Tuple[int, int]:
        return (math.floor(lon / cell_lon_deg),
                math.floor(lat_y / cell_lat_deg))

    # ── 分格（输入序无关：格 id → 成员序按输入序，代表属性确定）──
    cells: Dict[Tuple[int, int], List[int]] = {}
    passthrough: List[int] = []
    parse_failures = 0
    for index, feature in enumerate(features):
        fid = _stable_feature_id(feature, index)
        coord = _point_coord(feature)
        if coord is None:
            passthrough.append(index)
            parse_failures += 1
            continue
        if fid in protected_ids:
            passthrough.append(index)
            continue
        cells.setdefault(_cell_of(*coord), []).append(index)

    out_features: List[Dict[str, Any]] = []
    manifest: Dict[str, Dict[str, Any]] = {}
    groups_rows: List[Dict[str, Any]] = []
    aggregated_consumed = 0
    representative_count = 0

    def _passthrough_feature(index: int) -> Dict[str, Any]:
        return dict(features[index])

    # 输出顺序 = 原输入顺序投影：直通者原样；聚合格的代表要素落在格内
    # 首个成员的原位置（稳定、可对齐）。
    first_index_of_cell: Dict[int, Tuple[int, int]] = {}
    index_to_cell: Dict[int, Tuple[int, int]] = {}
    for cell_key, members in cells.items():
        if len(members) == 1 and keep_singleton:
            continue  # 单点格直通
        for m in members:
            index_to_cell[m] = cell_key
        first_index_of_cell[members[0]] = cell_key

    emitted_cells: set = set()
    for index in range(input_count):
        if index in passthrough or index not in index_to_cell:
            out_features.append(_passthrough_feature(index))
            continue
        cell_key = index_to_cell[index]
        if cell_key in emitted_cells:
            continue  # 代表要素已在首成员位输出
        emitted_cells.add(cell_key)
        members = cells[cell_key]
        # 代表几何 = 成员质心（米空间均值 → 投回度；确定性）
        lon_sum = sum(_point_coord(features[m])[0] for m in members)
        lat_sum = sum(_point_coord(features[m])[1] for m in members)
        cx, cy = lon_sum / len(members), lat_sum / len(members)
        member_ids = [_stable_feature_id(features[m], m) for m in members]
        props: Dict[str, Any] = {
            "agg_member_count": len(members),
            "agg_cell": f"{cell_key[0]}:{cell_key[1]}",
            "display_only": True,
        }
        ids_field = member_ids[:_MEMBER_ID_LIMIT]
        if len(member_ids) > _MEMBER_ID_LIMIT:
            props["agg_member_ids_overflow"] = len(member_ids) - _MEMBER_ID_LIMIT
        props["agg_member_ids"] = ids_field
        sum_delta: Dict[str, float] = {}
        for field in sum_fields:
            total = 0.0
            missing = 0
            for m in members:
                p = features[m].get("properties") if isinstance(
                    features[m].get("properties"), dict) else {}
                v = _num(p.get(field))
                if v is None:
                    missing += 1
                else:
                    total += v
            props[field] = total
            if missing:
                props[f"agg_missing_{field}"] = missing
            sum_delta[field] = round(total, 9)
        rep = {
            "type": "Feature",
            "id": f"agg:{props['agg_cell']}",
            "geometry": {"type": "Point", "coordinates": [cx, cy]},
            "properties": props,
        }
        out_features.append(rep)
        representative_count += 1
        aggregated_consumed += len(members)
        manifest[props["agg_cell"]] = {
            "member_ids": member_ids,
            "member_count": len(members),
            "centroid": [round(cx, 9), round(cy, 9)],
            "sum_fields": sum_delta,
        }
        if len(groups_rows) < _RECEIPT_GROUP_LIMIT:
            groups_rows.append({
                "cell": props["agg_cell"],
                "member_count": len(members),
                "centroid": [round(cx, 9), round(cy, 9)],
            })

    disclosures = [
        "点聚合为 display-only 显示工件：统计/图例口径恒 original_source，"
        "成员归属见 manifest（分析通道继续读原始 ref）",
    ]
    if keep_singleton:
        disclosures.append("单点格不聚合（原样直通，诚实统计）")
    receipt = {
        "engine": "point_aggregate_v1",
        "cell_size_m": float(cell_size_m),
        "cell_lon_deg": round(cell_lon_deg, 12),
        "cell_lat_deg": round(cell_lat_deg, 12),
        "grid_anchor": "lonlat_0_floor",
        "latitude_hint": lat,
        "input_count": input_count,
        "representative_count": representative_count,
        "aggregated_count": aggregated_consumed,
        "passthrough_count": len(out_features) - representative_count,
        "protected_count": sum(
            1 for i, f in enumerate(features)
            if _stable_feature_id(f, i) in protected_ids
            and _point_coord(f) is not None),
        "parse_failures": parse_failures,
        "max_members_per_cell": max(
            (len(v) for v in cells.values()), default=0),
        "stats_match": aggregated_consumed + (
            len(out_features) - representative_count) == input_count,
        "groups": groups_rows,
        "groups_truncated": len(cells) > _RECEIPT_GROUP_LIMIT,
        "disclosures": disclosures,
    }
    return PointAggregateResult(
        feature_collection={"type": "FeatureCollection",
                            "features": out_features},
        manifest=manifest,
        receipt=receipt,
    )


__all__ = [
    "PointAggregateResult",
    "aggregate_points",
]
