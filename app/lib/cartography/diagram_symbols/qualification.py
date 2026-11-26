"""图符数据资格层 —— 从要素属性到"可参算值"的有界、可会计抽取。

职责边界（WP1）：**属性语义资格** —— 字段存在性、数值性、缺失处置、
量纲一致性、要素稳定身份。几何锚资格（点/面代表点/线、无效几何、
位移策略）在 WP2 layout；不改变数据事实（只读属性，不写回）。

会计纪律（与 geometry.oracle 呼应）：每一条被 omit 的缺失都进
``missing_by_category`` 计数；block 策略下任何缺失 = typed 拒绝。
绝不静默丢类、绝不把 NaN 当 0。
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from app.lib.cartography.diagram_symbols.contracts import (
    MISSING_POLICIES,
    DiagramSymbolError,
    validate_category_spec,
)

#: 要素身份的回退属性名（GeoJSON "id" 成员缺失时查 properties.id）。
DEFAULT_ID_FIELD = "id"


def qualify_features(
    features: Sequence[Mapping[str, Any]],
    *,
    kind: str,
    categories: Sequence[str],
    fields: Sequence[str],
    missing_policy: str = "omit",
    units: Optional[Mapping[str, str]] = None,
    id_field: str = DEFAULT_ID_FIELD,
) -> Dict[str, Any]:
    """要素集资格化 → 每要素按类别序对齐的参算值 + 全量会计。

    features：GeoJSON Feature 形状（{"id"?, "properties": {...}}）或裸属性
    dict（等价 properties）。输出 ``schema="diagram_symbol_qualification.v1"``：
    - ``values``：与 features 等长；每要素为 [(category, value)]（仅参算项，
      类别序保持；缺失已按 policy 剔除/拒绝）；
    - ``glyph_feature_ids``：稳定要素身份（与 values 一一对应）；
    - ``evidence``：features_total / glyphs / features_empty（全类别缺失、
      无图符） / missing_by_category / zero_total_features。

    抛错语义（typed，全部带可执行 hint）：
    - 字段在整个数据集中完全不存在 → FIELD_NOT_FOUND（选择器写错）；
    - 值非数值（str/bool 等）→ FIELD_NOT_NUMERIC（数据缺陷，不静默转型）；
    - NaN/Inf → NON_FINITE_VALUE；
    - missing_policy="block" 且存在缺失 → MISSING_VALUE_BLOCKED；
    - 声明 units 且类别间不一致 → MIXED_UNITS（组成/条形/分段都是
      "同一总量/同一量纲比较"语义，混量纲不可比）；
    - 要素身份不可得 → MISSING_FEATURE_ID（图符 identity 必须稳定，
      交互命中/CAS/undo 都依赖它）。
    """
    if missing_policy not in MISSING_POLICIES:
        raise DiagramSymbolError(
            "UNKNOWN_MISSING_POLICY", f"未知缺失策略 {missing_policy!r}",
            correction_hint=f"可选：{MISSING_POLICIES}",
        )
    pairs = validate_category_spec(categories, fields, kind=kind)
    if units is not None:
        declared = {f: str(units[f]) for f in fields if units.get(f) is not None}
        distinct = set(declared.values())
        if len(distinct) > 1:
            raise DiagramSymbolError(
                "MIXED_UNITS",
                f"类别字段声明了混合量纲 {sorted(distinct)}：图符的组成/长度/面积"
                "语义要求同一总量下的同量纲比较",
                correction_hint="统一量纲或拆成独立图层",
                details={"units": declared},
            )

    missing_by_category: Dict[str, int] = {cat: 0 for cat, _ in pairs}
    values_out: List[List[Tuple[str, float]]] = []
    ids_out: List[str] = []
    features_empty = 0
    zero_total_features = 0
    field_seen = {f: False for _, f in pairs}
    n_categories = len(pairs)

    for idx, feature in enumerate(features):
        props = feature.get("properties", feature) if isinstance(feature, Mapping) else feature
        if not isinstance(props, Mapping):
            raise DiagramSymbolError(
                "FIELD_NOT_FOUND",
                f"第 {idx} 个要素既不是 Feature 也不是属性 dict（got {type(props).__name__}）",
                correction_hint="传入 GeoJSON Feature 列表或属性 dict 列表",
            )
        fid = feature.get("id") if isinstance(feature, Mapping) and feature.get("id") is not None else None
        if fid is None:
            fid = props.get(id_field)
        if fid is None or isinstance(fid, (dict, list)):
            raise DiagramSymbolError(
                "MISSING_FEATURE_ID",
                f"第 {idx} 个要素缺少稳定身份（无 id 成员且 properties.{id_field} 缺失）",
                correction_hint=f"补 GeoJSON id 或指定 properties 里的 id_field（当前 {id_field!r}）",
            )
        row: List[Tuple[str, float]] = []
        row_missing = 0
        for cat, field in pairs:
            raw = props.get(field)
            if field in props:
                field_seen[field] = True  # 键存在即算"字段在数据集中"（全 null ≠ 选择器写错）
            if raw is None:
                # 缺失（键缺席或 null）—— 政策分流
                if missing_policy == "block":
                    raise DiagramSymbolError(
                        "MISSING_VALUE_BLOCKED",
                        f"要素 {fid!r} 类别 {cat!r}（字段 {field!r}）缺失，missing_policy=block",
                        correction_hint="改 missing_policy=omit（带会计），或补全数据",
                        details={"feature_id": str(fid), "category": cat},
                    )
                missing_by_category[cat] += 1
                row_missing += 1
                continue
            if isinstance(raw, bool) or not isinstance(raw, (int, float)):
                raise DiagramSymbolError(
                    "FIELD_NOT_NUMERIC",
                    f"要素 {fid!r} 类别 {cat!r}（字段 {field!r}）值非数值：{raw!r}",
                    correction_hint="先修数据或经聚合服务转数值，资格层不做静默转型",
                    details={"feature_id": str(fid)},
                )
            v = float(raw)
            if not math.isfinite(v):
                raise DiagramSymbolError(
                    "NON_FINITE_VALUE",
                    f"要素 {fid!r} 类别 {cat!r}（字段 {field!r}）值非有限（NaN/Inf）",
                    correction_hint="缺失请显式用 null（走 missing_policy），不要写 NaN",
                    details={"feature_id": str(fid)},
                )
            row.append((cat, v))
        if row:
            if math.fsum(v for _, v in row) == 0 and kind != "series_bars":
                zero_total_features += 1
            values_out.append(row)
            ids_out.append(str(fid))
            if row_missing == n_categories:
                features_empty += 1
        else:
            features_empty += 1

    absent = [f for f, seen in field_seen.items() if not seen]
    if absent:
        raise DiagramSymbolError(
            "FIELD_NOT_FOUND",
            f"字段在数据集中完全不存在：{absent}",
            correction_hint="检查字段名拼写/来源 schema（可用 webgis_field_contract_inspect 对账）",
            details={"missing_fields": absent},
        )
    if not values_out:
        raise DiagramSymbolError(
            "ZERO_TOTAL",
            "所有要素的全部类别均缺失，没有可参算图符",
            correction_hint="换字段或放宽 missing_policy",
        )
    return {
        "schema": "diagram_symbol_qualification.v1",
        "kind": kind,
        "categories": list(categories),
        "fields": list(fields),
        "unit": (next(iter({str(units[f]) for f in fields if units.get(f) is not None}), None)
                 if units else None),
        "units_declared": bool(units and any(units.get(f) is not None for f in fields)),
        "missing_policy": missing_policy,
        "values": values_out,
        "glyph_feature_ids": ids_out,
        "evidence": {
            "features_total": len(features),
            "glyphs": len(values_out),
            "features_empty": features_empty,
            "zero_total_features": zero_total_features,
            "missing_by_category": missing_by_category,
            "missing_total": sum(missing_by_category.values()),
        },
    }
