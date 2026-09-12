"""Data Quality V9 —— 内置规则判定函数（纯函数面，P1）。

纪律：

- **纯函数**：输入 = ``RuleEvalContext``（features / raster_stats / params），
  输出 = ``RuleOutcome``；不碰 DB、不碰 session、不做 IO；
- **有界**：每条规则尊重 ``ctx.max_scan``（features 扫描上限）；拓扑邻接
  O(n²) 有 ``max_polygons`` 硬帽，超帽诚实 ``skipped``（绝不无界循环）；
- **诚实缺省**：参数不足以判定（如未配 time_field、无波段统计）→
  ``skipped`` 并给 reason，绝不虚构 pass；
- **几何判定**优先用 shapely（仓库硬依赖），缺席/失败退回纯 Python
  环检测（退化/未闭合/零面积）；拓扑邻接在 shapely 缺席时诚实跳过；
- **修复建议**只引用 REMEDIATION_OPS 词表（rules.ALLOWED_FIX_OPS 校验）。
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from app.services.data_quality.rules import RULE_TYPES

# 规则单趟扫描的几何/顶点帽（防御畸形输入，非业务阈值）。
_MAX_RING_VERTS = 512
_MAX_PROP_STR = 512
_ISO_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}([T ]\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:?\d{2})?)?$"
)


# ── 形状 ─────────────────────────────────────────────────────────────


@dataclass
class RuleOutcome:
    """单规则判定结果（与 QualityRuleResult 行字段同构）。"""

    status: str = "pass"            # pass / warn / fail / skipped / error
    message: str = ""
    affected_count: int = 0
    metric: Dict[str, Any] = field(default_factory=dict)
    autofixable: bool = False
    fix_operations: Tuple[str, ...] = ()

    def bounded_metric(self, cap: int = 24) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        for k, v in list(self.metric.items())[:cap]:
            key = str(k)[:64]
            if isinstance(v, str):
                out[key] = v[:200]
            elif isinstance(v, (int, float, bool)) or v is None:
                out[key] = v
            elif isinstance(v, (list, tuple)):
                out[key] = [str(x)[:64] for x in v[:8]]
            elif isinstance(v, dict):
                out[key] = {str(k2)[:64]: (v2 if isinstance(v2, (int, float, bool)) else str(v2)[:64])
                            for k2, v2 in list(v.items())[:8]}
            else:
                out[key] = str(v)[:64]
        return out


@dataclass
class RuleEvalContext:
    """规则求值上下文（engine 装配；规则函数只读）。"""

    kind: str = "vector"                     # vector / raster / table
    features: List[Dict[str, Any]] = field(default_factory=list)
    raster_stats: Dict[str, Any] = field(default_factory=dict)
    crs: str = ""
    params: Dict[str, Any] = field(default_factory=dict)
    max_scan: int = 20000


def _num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _finite(v: Any) -> bool:
    return _num(v) and math.isfinite(float(v))


# ── 几何工具（纯 Python；shapely 仅拓扑邻接使用） ────────────────────


def _iter_positions(geometry: Dict[str, Any]) -> List[Tuple[float, float]]:
    """GeoJSON geometry → 有限坐标对列表（非有限值以 None 占位保序）。"""
    out: List[Tuple[float, float]] = []

    def walk(node: Any) -> None:
        if not isinstance(node, (list, tuple)):
            return
        if node and _num(node[0]) and len(node) >= 2 and _num(node[1]):
            x, y = node[0], node[1]
            out.append((float(x) if _finite(x) else math.nan,
                        float(y) if _finite(y) else math.nan))
            return
        for child in node:
            walk(child)

    if isinstance(geometry, dict):
        walk(geometry.get("coordinates"))
    return out


def _iter_rings(geometry: Dict[str, Any]) -> List[List[Tuple[float, float]]]:
    """Polygon/MultiPolygon → 外/内环列表（其余类型空）。"""
    rings: List[List[Tuple[float, float]]] = []
    gtype = str(geometry.get("type") or "")

    def ring_of(coords: Any) -> None:
        if isinstance(coords, list) and coords:
            head = coords[0]
            if isinstance(head, list) and head and _num(head[0]) and len(head) >= 2 and _num(head[1]):
                rings.append([(float(c[0]), float(c[1])) for c in coords
                              if isinstance(c, list) and len(c) >= 2
                              and _num(c[0]) and _num(c[1])][: (_MAX_RING_VERTS + 1)])
                return
            for sub in coords:
                ring_of(sub)

    if gtype == "Polygon":
        ring_of(geometry.get("coordinates"))
    elif gtype == "MultiPolygon":
        for poly in geometry.get("coordinates") or []:
            ring_of(poly)
    return rings


def _ring_is_closed(ring: List[Tuple[float, float]]) -> bool:
    return len(ring) >= 4 and ring[0] == ring[-1]


def _ring_distinct_points(ring: List[Tuple[float, float]]) -> int:
    return len(set(ring))


def _ring_area(ring: List[Tuple[float, float]]) -> float:
    """鞋带公式绝对面积（未闭合环自动视为闭合到首点）。"""
    pts = ring[:-1] if (_ring_is_closed(ring)) else ring
    n = len(pts)
    if n < 3:
        return 0.0
    s = 0.0
    for i in range(n):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % n]
        s += x1 * y2 - x2 * y1
    return abs(s) / 2.0


def _ring_self_intersects(ring: List[Tuple[float, float]]) -> Optional[bool]:
    """简单环自交检测（相邻边共享端点不算；顶点超帽 → None 诚实放弃）。"""
    pts = ring[:-1] if _ring_is_closed(ring) else ring
    n = len(pts)
    if n > _MAX_RING_VERTS:
        return None

    def seg_intersect(p1, p2, p3, p4) -> bool:
        def orient(a, b, c) -> float:
            return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
        d1 = orient(p3, p4, p1)
        d2 = orient(p3, p4, p2)
        d3 = orient(p1, p2, p3)
        d4 = orient(p1, p2, p4)
        if ((d1 > 0) != (d2 > 0)) and ((d3 > 0) != (d4 > 0)):
            return True
        return False

    for i in range(n):
        a1, a2 = pts[i], pts[(i + 1) % n]
        for j in range(i + 1, n):
            if j == i or (j + 1) % n == i or (i + 1) % n == j:
                continue  # 相邻边共享端点
            b1, b2 = pts[j], pts[(j + 1) % n]
            if seg_intersect(a1, a2, b1, b2):
                return True
    return False


def _geom_family(gtype: str) -> str:
    if "Point" in gtype:
        return "point"
    if "Line" in gtype:
        return "line"
    if "Polygon" in gtype:
        return "polygon"
    return "other"


# ── 逐规则实现 ────────────────────────────────────────────────────────
# 约定：函数名 = ``rule_<type>``；注册表在文件尾 RULE_FUNCTIONS。


def rule_null_rate(ctx: RuleEvalContext) -> RuleOutcome:
    null_counts: Dict[str, int] = {}
    total = 0
    for f in ctx.features[: ctx.max_scan]:
        total += 1
        props = f.get("properties") if isinstance(f, dict) else None
        if not isinstance(props, dict):
            continue
        for k, v in props.items():
            if v is None or (isinstance(v, str) and v == ""):
                null_counts[k] = null_counts.get(k, 0) + 1
    if total == 0:
        return RuleOutcome(status="skipped", message="no_features")
    fail_at = float(ctx.params.get("fail_null_rate", 0.8))
    warn_at = float(ctx.params.get("max_null_rate", 0.5))
    rates = {k: c / total for k, c in null_counts.items()}
    worst_field, worst_rate = max(
        rates.items(), key=lambda kv: kv[1], default=("", 0.0)
    )
    worst = float(worst_rate)
    metric = {
        "scanned": total,
        "worst_field": worst_field,
        "worst_null_rate": round(worst, 4),
        "fields_over_warn": sorted(k for k, r in rates.items() if r > warn_at)[:8],
    }
    if worst > fail_at:
        return RuleOutcome("fail", f"字段 '{worst_field}' 空值率 {worst:.2%} > {fail_at:.0%}",
                           null_counts.get(worst_field, 0), metric, True, ("filter_null",))
    if worst > warn_at:
        return RuleOutcome("warn", f"字段 '{worst_field}' 空值率 {worst:.2%} > {warn_at:.0%}",
                           null_counts.get(worst_field, 0), metric, True, ("filter_null",))
    return RuleOutcome("pass", "null rates within thresholds", 0, metric)


def rule_crs_validity(ctx: RuleEvalContext) -> RuleOutcome:
    crs = str(ctx.crs or "").strip()
    unknown_markers = ("EPSG:0", "unknown", "none", "+proj=?", "0")
    missing = not crs
    unknown = (not missing) and any(m in crs.upper() for m in map(str.upper, unknown_markers))
    if missing or unknown:
        suggested = str(ctx.params.get("target_crs") or "EPSG:4326")
        metric = {"crs": crs[:64] or "(missing)", "suggested_crs": suggested}
        return RuleOutcome(
            "fail" if missing else "warn",
            "CRS 缺失" if missing else f"CRS 不可识别: {crs[:48]}",
            1, metric, True, ("reproject",),
        )
    metric = {"crs": crs[:64] or "(missing)"}
    return RuleOutcome("pass", "crs present", 0, metric)


def rule_geometry_validity(ctx: RuleEvalContext) -> RuleOutcome:
    total = 0
    invalid = 0
    degenerate = 0
    unclosed = 0
    self_intersected = 0
    rings_checked = 0
    rings_skipped = 0
    for f in ctx.features[: ctx.max_scan]:
        geometry = f.get("geometry") if isinstance(f, dict) else None
        total += 1
        if not isinstance(geometry, dict) or geometry.get("coordinates") in (None, []):
            invalid += 1
            degenerate += 1
            continue
        gtype = str(geometry.get("type") or "")
        if gtype not in ("Polygon", "MultiPolygon"):
            if gtype in ("Point", "MultiPoint", "LineString", "MultiLineString"):
                positions = _iter_positions(geometry)
                if not positions or all(
                    math.isnan(x) or math.isnan(y) for x, y in positions
                ):
                    invalid += 1
                continue
            invalid += 1
            continue
        for ring in _iter_rings(geometry):
            rings_checked += 1
            if len(ring) < 4 or not _ring_is_closed(ring):
                unclosed += 1
                invalid += 1
                continue
            # 自交先判（蝶形环的鞋带面积恰为 0，先查面积会把自交误归退化）
            si = _ring_self_intersects(ring)
            if si is None:
                rings_skipped += 1
                continue
            if si:
                self_intersected += 1
                invalid += 1
                continue
            if _ring_distinct_points(ring) < 3 or _ring_area(ring) == 0.0:
                degenerate += 1
                invalid += 1
    if total == 0:
        return RuleOutcome(status="skipped", message="no_features")
    ratio = invalid / total
    max_ratio = float(ctx.params.get("max_invalid_ratio", 0.05))
    metric = {
        "total": total,
        "invalid": invalid,
        "invalid_ratio": round(ratio, 4),
        "degenerate": degenerate,
        "unclosed": unclosed,
        "self_intersected": self_intersected,
        "rings_checked": rings_checked,
        "rings_skipped_over_cap": rings_skipped,
    }
    outcome = RuleOutcome(metric=metric, autofixable=True, fix_operations=("repair_geometry",))
    if ratio > max_ratio:
        outcome.status = "fail"
        outcome.message = f"无效几何比例 {ratio:.2%} > {max_ratio:.0%}（{invalid}/{total}）"
        outcome.affected_count = invalid
    elif invalid:
        outcome.status = "warn"
        outcome.message = f"发现 {invalid} 个无效/退化几何"
        outcome.affected_count = invalid
    else:
        outcome.message = "geometries valid"
    return outcome


def rule_envelope_sanity(ctx: RuleEvalContext) -> RuleOutcome:
    inverted = 0
    out_of_range = 0
    non_finite = 0
    scanned = 0
    for f in ctx.features[: ctx.max_scan]:
        geometry = f.get("geometry") if isinstance(f, dict) else None
        if not isinstance(geometry, dict):
            continue
        positions = _iter_positions(geometry)
        if not positions:
            continue
        scanned += 1
        # 显式声明的 bbox 反转（min > max）是独立的范围异常信号
        bbox = f.get("bbox") if isinstance(f, dict) else None
        if isinstance(bbox, (list, tuple)) and len(bbox) >= 4:
            if _finite(bbox[0]) and _finite(bbox[2]) and (bbox[2] < bbox[0]):
                inverted += 1
                continue
            if _finite(bbox[1]) and _finite(bbox[3]) and (bbox[3] < bbox[1]):
                inverted += 1
                continue
        xs = [p[0] for p in positions]
        ys = [p[1] for p in positions]
        if any(math.isnan(x) or math.isnan(y) for x, y in positions):
            non_finite += 1
            continue
        crs_u = str(ctx.crs or "").upper()
        geographic = (not crs_u) or "4326" in crs_u or "CRS84" in crs_u
        if geographic:
            lon_lo, lon_hi = (float(v) for v in ctx.params.get("lon_range", (-180.5, 180.5)))
            lat_lo, lat_hi = (float(v) for v in ctx.params.get("lat_range", (-90.5, 90.5)))
            if max(xs) > lon_hi or min(xs) < lon_lo or max(ys) > lat_hi or min(ys) < lat_lo:
                out_of_range += 1
    if scanned == 0:
        return RuleOutcome(status="skipped", message="no_features")
    metric = {"scanned": scanned, "inverted_bbox": inverted,
              "out_of_range": out_of_range, "non_finite": non_finite}
    affected = inverted + out_of_range + non_finite
    if affected:
        return RuleOutcome("warn", f"空间范围异常 {affected} 个要素（越界/反转/非有限坐标）",
                           affected, metric)
    return RuleOutcome("pass", "envelopes sane", 0, metric)


def rule_attribute_domain(ctx: RuleEvalContext) -> RuleOutcome:
    fields = ctx.params.get("fields") or {}
    if not isinstance(fields, dict) or not fields:
        return RuleOutcome(status="skipped", message="no_fields_configured")
    violations: Dict[str, int] = {}
    samples: Dict[str, List[str]] = {}
    for name, spec in list(fields.items())[:16]:
        if not isinstance(spec, dict):
            continue
        lo = spec.get("min")
        hi = spec.get("max")
        enum = spec.get("enum")
        for f in ctx.features[: ctx.max_scan]:
            props = f.get("properties") if isinstance(f, dict) else None
            if not isinstance(props, dict) or name not in props:
                continue
            v = props[name]
            bad = False
            if _num(v):
                if lo is not None and float(v) < float(lo):
                    bad = True
                if hi is not None and float(v) > float(hi):
                    bad = True
            elif enum is not None and isinstance(enum, (list, tuple)):
                bad = v not in enum
            elif not _num(v) and (lo is not None or hi is not None):
                bad = True
            if bad:
                violations[name] = violations.get(name, 0) + 1
                if len(samples.get(name, [])) < 3:
                    samples.setdefault(name, []).append(str(v)[:64])
    if violations:
        worst = max(violations.items(), key=lambda kv: kv[1])
        return RuleOutcome(
            "warn", f"值域越界 {sum(violations.values())} 处（最重字段 '{worst[0]}' {worst[1]}）",
            sum(violations.values()),
            {"violations": violations, "samples": samples},
        )
    return RuleOutcome("pass", "attribute domains satisfied", 0,
                       {"checked_fields": len(fields)})


def rule_primary_key_uniqueness(ctx: RuleEvalContext) -> RuleOutcome:
    field_name = str(ctx.params.get("field") or "id")
    seen: Dict[str, int] = {}
    missing = 0
    scanned = 0
    for f in ctx.features[: ctx.max_scan]:
        props = f.get("properties") if isinstance(f, dict) else None
        if not isinstance(props, dict):
            continue
        scanned += 1
        v = props.get(field_name)
        if v is None:
            missing += 1
            continue
        key = str(v)[:128]
        seen[key] = seen.get(key, 0) + 1
    if scanned == 0:
        return RuleOutcome(status="skipped", message="no_features")
    dups = {k: c for k, c in seen.items() if c > 1}
    dup_rows = sum(c for c in dups.values())
    metric = {"field": field_name, "duplicate_keys": len(dups),
              "duplicate_rows": dup_rows, "missing": missing,
              "sample_keys": sorted(dups)[:8]}
    if dups:
        return RuleOutcome("fail", f"主键 '{field_name}' 重复 {len(dups)} 组（{dup_rows} 行）",
                           dup_rows, metric)
    return RuleOutcome("pass", "primary key unique", 0, metric)


def rule_fk_referential(ctx: RuleEvalContext) -> RuleOutcome:
    field_name = str(ctx.params.get("field") or "")
    valid = ctx.params.get("valid_values")
    if not field_name or not isinstance(valid, (list, tuple)) or not valid:
        return RuleOutcome(status="skipped", message="field/valid_values not configured")
    valid_set = {str(v)[:128] for v in list(valid)[:10000]}
    orphans: List[str] = []
    orphan_rows = 0
    scanned = 0
    for f in ctx.features[: ctx.max_scan]:
        props = f.get("properties") if isinstance(f, dict) else None
        if not isinstance(props, dict):
            continue
        scanned += 1
        v = props.get(field_name)
        if v is None:
            continue
        if str(v)[:128] not in valid_set:
            orphan_rows += 1
            if len(orphans) < 8:
                orphans.append(str(v)[:64])
    if scanned == 0:
        return RuleOutcome(status="skipped", message="no_features")
    metric = {"field": field_name, "orphan_rows": orphan_rows,
              "valid_values": len(valid_set), "sample_orphans": orphans}
    if orphan_rows:
        return RuleOutcome("warn", f"字段 '{field_name}' 存在 {orphan_rows} 个悬空引用",
                           orphan_rows, metric)
    return RuleOutcome("pass", "references intact", 0, metric)


def _feature_dup_key(feature: Dict[str, Any], precision: int) -> str:
    geometry = feature.get("geometry") or {}
    positions = _iter_positions(geometry if isinstance(geometry, dict) else {})
    geo_key = ";".join(
        f"{round(x, precision) if _finite(x) else 'nan'},{round(y, precision) if _finite(y) else 'nan'}"
        for x, y in positions[:256]
    )
    props = feature.get("properties")
    if isinstance(props, dict):
        prop_key = ";".join(
            f"{str(k)[:48]}={str(v)[:64]}" for k, v in sorted(props.items())[:32]
        )
    else:
        prop_key = "-"
    gtype = str(geometry.get("type") or "")[:24]
    return f"{gtype}|{geo_key}|{prop_key}"


def rule_duplicate_features(ctx: RuleEvalContext) -> RuleOutcome:
    precision = int(ctx.params.get("coordinate_precision", 6))
    seen: Dict[str, int] = {}
    scanned = 0
    for f in ctx.features[: ctx.max_scan]:
        if not isinstance(f, dict):
            continue
        scanned += 1
        key = _feature_dup_key(f, precision)
        seen[key] = seen.get(key, 0) + 1
    if scanned == 0:
        return RuleOutcome(status="skipped", message="no_features")
    dup_groups = {k: c for k, c in seen.items() if c > 1}
    dup_rows = sum(c - 1 for c in dup_groups.values())
    metric = {"scanned": scanned, "duplicate_groups": len(dup_groups),
              "duplicate_rows": dup_rows,
              "sample_keys": [k[:64] for k in sorted(dup_groups)[:8]]}
    if dup_rows:
        return RuleOutcome("warn", f"重复要素 {dup_rows} 行（{len(dup_groups)} 组）",
                           dup_rows, metric, True, ("repair_geometry",))
    return RuleOutcome("pass", "no duplicates", 0, metric)


def _observed_type(v: Any) -> str:
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "bool"
    if _num(v):
        return "number"
    if isinstance(v, str):
        return "string"
    if isinstance(v, (list, tuple)):
        return "array"
    if isinstance(v, dict):
        return "object"
    return "unknown"


_COMPATIBLE = {
    ("number", "number"): True,
    ("string", "string"): True,
    ("bool", "bool"): True,
}


def rule_field_type_drift(ctx: RuleEvalContext) -> RuleOutcome:
    expected = ctx.params.get("expected_types") or {}
    if not isinstance(expected, dict):
        expected = {}
    observed: Dict[str, Dict[str, int]] = {}
    for f in ctx.features[: ctx.max_scan]:
        props = f.get("properties") if isinstance(f, dict) else None
        if not isinstance(props, dict):
            continue
        for k, v in props.items():
            slot = observed.setdefault(str(k)[:96], {})
            t = _observed_type(v)
            if t != "null":
                slot[t] = slot.get(t, 0) + 1
    drifted: Dict[str, str] = {}
    for name, types in observed.items():
        non_null = {t: c for t, c in types.items()}
        if name in expected:
            exp = str(expected[name])
            compatible = [t for t in non_null
                          if _COMPATIBLE.get((exp, t)) or exp == t
                          or (exp == "number" and t == "number")]
            if non_null and not compatible:
                drifted[name] = f"expected={exp},got={','.join(sorted(non_null))}"
        elif len(non_null) > 1:
            drifted[name] = "mixed:" + ",".join(sorted(non_null))
    metric = {"checked_fields": len(observed), "drifted_fields": len(drifted),
              "detail": dict(list(drifted.items())[:8])}
    if drifted:
        return RuleOutcome("warn", f"字段类型漂移 {len(drifted)} 个字段",
                           len(drifted), metric)
    return RuleOutcome("pass", "field types stable", 0, metric)


def rule_temporal_gaps(ctx: RuleEvalContext) -> RuleOutcome:
    field_name = str(ctx.params.get("time_field") or "")
    if not field_name:
        return RuleOutcome(status="skipped", message="time_field not configured")
    stamps: List[datetime] = []
    unparsed = 0
    for f in ctx.features[: ctx.max_scan]:
        props = f.get("properties") if isinstance(f, dict) else None
        raw = props.get(field_name) if isinstance(props, dict) else None
        if raw is None:
            continue
        text = str(raw).strip()
        if not _ISO_RE.match(text):
            unparsed += 1
            continue
        try:
            stamps.append(datetime.fromisoformat(text.replace("Z", "+00:00")))
        except ValueError:
            unparsed += 1
    if len(stamps) < 3:
        return RuleOutcome(status="skipped",
                           message=f"insufficient timestamps ({len(stamps)})")
    stamps.sort()
    diffs = [(stamps[i + 1] - stamps[i]).total_seconds()
             for i in range(len(stamps) - 1)]
    positive = sorted(d for d in diffs if d > 0)
    if not positive:
        return RuleOutcome(status="skipped", message="no positive intervals")
    median = positive[len(positive) // 2]
    gap_factor = float(ctx.params.get("gap_factor", 4.0))
    max_gap_s = ctx.params.get("max_gap_s")
    gaps = 0
    for d in diffs:
        over_median = gap_factor > 0 and d > median * gap_factor
        over_abs = isinstance(max_gap_s, (int, float)) and d > float(max_gap_s)
        if over_median or over_abs:
            gaps += 1
    metric = {"time_field": field_name, "points": len(stamps),
              "median_interval_s": median, "gaps": gaps,
              "max_gap_s": max(diffs), "unparsed": unparsed}
    if gaps:
        return RuleOutcome("warn", f"时间序列存在 {gaps} 处断裂（> {gap_factor}× 中位间隔）",
                           gaps, metric)
    return RuleOutcome("pass", "temporal continuity ok", 0, metric)


_MOJIBAKE_MARKERS = (
    "Ã", "â€", "æ", "å", "ç\x81", "ï¿½", "\ufffd", "å", "ç", "é\U0001d400",
)


def _looks_mojibake(text: str) -> bool:
    if not text:
        return False
    if "\ufffd" in text:
        return True
    hits = sum(1 for m in _MOJIBAKE_MARKERS if m in text)
    if hits == 0:
        return False
    # 双重编码证据：latin-1 → utf-8 往返能解出更长/有效文本
    try:
        roundtrip = text.encode("latin-1").decode("utf-8")
        return len(roundtrip) >= 1 and roundtrip != text
    except (UnicodeEncodeError, UnicodeDecodeError):
        # 有标记但不可往返：仅当标记密度高才判乱码（避免北欧字母误报）
        return hits >= 2 and sum(text.count(m) for m in _MOJIBAKE_MARKERS) >= max(2, len(text) // 8)


def rule_attribute_encoding(ctx: RuleEvalContext) -> RuleOutcome:
    total_strings = 0
    suspicious = 0
    samples: List[str] = []
    for f in ctx.features[: ctx.max_scan]:
        props = f.get("properties") if isinstance(f, dict) else None
        if not isinstance(props, dict):
            continue
        for v in props.values():
            if not isinstance(v, str) or not v:
                continue
            total_strings += 1
            if _looks_mojibake(v[:_MAX_PROP_STR]):
                suspicious += 1
                if len(samples) < 8:
                    samples.append(v[:64])
    if total_strings == 0:
        return RuleOutcome(status="skipped", message="no_string_properties")
    ratio = suspicious / total_strings
    max_ratio = float(ctx.params.get("max_mojibake_ratio", 0.02))
    metric = {"string_values": total_strings, "suspicious": suspicious,
              "mojibake_ratio": round(ratio, 4), "samples": samples}
    if ratio > max_ratio:
        return RuleOutcome("fail" if ratio > max_ratio * 5 else "warn",
                           f"疑似乱码比例 {ratio:.2%} > {max_ratio:.0%}",
                           suspicious, metric, True, ("normalize",))
    return RuleOutcome("pass", "encoding ok", 0, metric)


def rule_topology_adjacency(ctx: RuleEvalContext) -> RuleOutcome:
    max_polygons = int(ctx.params.get("max_polygons", 200))
    polygons: List[Tuple[int, Any]] = []
    idx = -1
    for f in ctx.features[: ctx.max_scan]:
        idx += 1
        geometry = f.get("geometry") if isinstance(f, dict) else None
        if not isinstance(geometry, dict) or geometry.get("type") != "Polygon":
            continue
        polygons.append((idx, geometry))
        if len(polygons) > max_polygons:
            return RuleOutcome(status="skipped",
                               message=f"bounded_skip: polygons > {max_polygons}")
    if len(polygons) < 2:
        return RuleOutcome(status="skipped", message="fewer than 2 polygons")
    try:
        from shapely.geometry import shape as shapely_shape
    except Exception:  # noqa: BLE001 — 诚实降级（shapely 是硬依赖，理论不可达）
        return RuleOutcome(status="skipped", message="shapely_unavailable")
    max_ratio = float(ctx.params.get("max_overlap_ratio", 0.01))
    geoms = []
    for _, g in polygons:
        try:
            geoms.append(shapely_shape(g))
        except Exception:  # noqa: BLE001 — 非法几何跳过（geometry_validity 负责）
            geoms.append(None)
    overlapping_pairs = 0
    samples: List[str] = []
    for i in range(len(geoms)):
        gi = geoms[i]
        if gi is None or gi.is_empty:
            continue
        for j in range(i + 1, len(geoms)):
            gj = geoms[j]
            if gj is None or gj.is_empty:
                continue
            try:
                inter = gi.intersection(gj)
            except Exception:  # noqa: BLE001 — GEOHP 错误按无重叠处理
                continue
            if inter.is_empty:
                continue
            area_i = float(gi.area) or 1e-12
            area_j = float(gj.area) or 1e-12
            ratio = float(inter.area) / min(area_i, area_j)
            if ratio > max_ratio:
                overlapping_pairs += 1
                if len(samples) < 8:
                    samples.append(f"feature#{polygons[i][0]}×feature#{polygons[j][0]}:{ratio:.3f}")
    metric = {"polygons": len(polygons), "overlapping_pairs": overlapping_pairs,
              "max_overlap_ratio": max_ratio, "samples": samples}
    if overlapping_pairs:
        return RuleOutcome("warn", f"相邻面重叠 {overlapping_pairs} 对（> {max_ratio:.1%}）",
                           overlapping_pairs, metric)
    return RuleOutcome("pass", "adjacency ok", 0, metric)


def rule_mixed_geometry_types(ctx: RuleEvalContext) -> RuleOutcome:
    families: Dict[str, int] = {}
    for f in ctx.features[: ctx.max_scan]:
        geometry = f.get("geometry") if isinstance(f, dict) else None
        if not isinstance(geometry, dict):
            continue
        fam = _geom_family(str(geometry.get("type") or ""))
        families[fam] = families.get(fam, 0) + 1
    present = sorted(k for k in families if k != "other" and families[k])
    metric = {"families": families}
    if len(present) > 1:
        return RuleOutcome("warn", f"几何族混杂: {','.join(present)}",
                           sum(families[k] for k in present if k != present[0]),
                           metric)
    return RuleOutcome("pass", "single geometry family", 0, metric)


# ── 栅格规则（raster_stats 形状：profiler.RasterProfileData 的 dict 投影） ──


def _bands_of(ctx: RuleEvalContext) -> List[Dict[str, Any]]:
    bands = ctx.raster_stats.get("band_stats") or ctx.raster_stats.get("bands")
    if isinstance(bands, list):
        return [b for b in bands if isinstance(b, dict)]
    return []


def rule_nodata_ratio(ctx: RuleEvalContext) -> RuleOutcome:
    top = ctx.raster_stats.get("nodata_ratio")
    bands = _bands_of(ctx)
    ratios: List[float] = []
    if _num(top):
        ratios.append(1.0 - float(top) if 0 <= float(top) <= 1 else float(top))
    for b in bands:
        vpr = b.get("valid_pixel_ratio")
        if _num(vpr):
            ratios.append(1.0 - float(vpr))
    if not ratios:
        return RuleOutcome(status="skipped", message="no nodata evidence")
    worst = max(ratios)
    max_ratio = float(ctx.params.get("max_nodata_ratio", 0.6))
    metric = {"worst_nodata_ratio": round(worst, 4), "bands_evaluated": len(ratios)}
    if worst > max_ratio:
        return RuleOutcome("warn", f"nodata 比例 {worst:.2%} > {max_ratio:.0%}",
                           1, metric, True, ("filter_nodata",))
    return RuleOutcome("pass", "nodata ratio ok", 0, metric)


def rule_resolution_drift(ctx: RuleEvalContext) -> RuleOutcome:
    rx = ctx.raster_stats.get("resolution_x")
    ry = ctx.raster_stats.get("resolution_y")
    if not _num(rx) or not _num(ry) or float(rx) <= 0 or float(ry) <= 0:
        return RuleOutcome(status="skipped", message="resolution unknown")
    rx_f, ry_f = float(rx), float(ry)
    anisotropy = max(rx_f, ry_f) / min(rx_f, ry_f)
    max_aniso = float(ctx.params.get("max_anisotropy", 1.05))
    expected = ctx.params.get("expected_resolution")
    drift_note = ""
    expected_violation = False
    if _num(expected) and float(expected) > 0:
        ratio = min(rx_f, ry_f) / float(expected)
        if ratio > 1.5 or ratio < 1 / 1.5:
            expected_violation = True
            drift_note = f" vs expected {float(expected):g}"
    metric = {"resolution_x": rx_f, "resolution_y": ry_f,
              "anisotropy": round(anisotropy, 4)}
    if anisotropy > max_aniso or expected_violation:
        reason = f"分辨率各向异性 {anisotropy:.3f} > {max_aniso:g}" if anisotropy > max_aniso \
            else f"分辨率漂移{drift_note}"
        return RuleOutcome("warn", reason, 1, metric)
    return RuleOutcome("pass", "resolution consistent", 0, metric)


def rule_raster_stats_outlier(ctx: RuleEvalContext) -> RuleOutcome:
    bands = _bands_of(ctx)
    means = [b.get("mean") for b in bands if _num(b.get("mean"))]
    if len(means) < 3:
        return RuleOutcome(status="skipped",
                           message=f"need >=3 band means, got {len(means)}")
    values = sorted(float(m) for m in means)
    median = values[len(values) // 2]
    # MAD 稳健 z 分数（band 数少时 mean/std 会被离群 band 自己拉偏）
    abs_dev = sorted(abs(v - median) for v in values)
    mad = abs_dev[len(abs_dev) // 2] or 1e-12
    z_threshold = float(ctx.params.get("z_threshold", 3.0))
    outliers: List[str] = []
    for i, b in enumerate(bands):
        m = b.get("mean")
        if not _num(m):
            continue
        robust_z = 0.6745 * (float(m) - median) / mad
        if abs(robust_z) > z_threshold:
            outliers.append(f"band{b.get('band', i + 1)}:z={robust_z:.2f}")
    metric = {"bands": len(means), "median_mean": median,
              "z_threshold": z_threshold, "outliers": outliers[:8]}
    if outliers:
        return RuleOutcome("warn", f"{len(outliers)} 个波段统计离群（robust z > {z_threshold:g}）",
                           len(outliers), metric)
    return RuleOutcome("pass", "band stats consistent", 0, metric)


#: 规则类型 → 判定函数（封闭注册表；rules.RULE_TYPES 新增必须同步此处）。
RULE_FUNCTIONS = {
    "null_rate": rule_null_rate,
    "crs_validity": rule_crs_validity,
    "geometry_validity": rule_geometry_validity,
    "envelope_sanity": rule_envelope_sanity,
    "attribute_domain": rule_attribute_domain,
    "primary_key_uniqueness": rule_primary_key_uniqueness,
    "fk_referential": rule_fk_referential,
    "duplicate_features": rule_duplicate_features,
    "field_type_drift": rule_field_type_drift,
    "temporal_gaps": rule_temporal_gaps,
    "attribute_encoding": rule_attribute_encoding,
    "topology_adjacency": rule_topology_adjacency,
    "mixed_geometry_types": rule_mixed_geometry_types,
    "nodata_ratio": rule_nodata_ratio,
    "resolution_drift": rule_resolution_drift,
    "raster_stats_outlier": rule_raster_stats_outlier,
}

#: 各规则类型的输入需求（engine 用来做 kind 适用性过滤）。
RULE_INPUTS: Dict[str, Tuple[str, ...]] = {
    rtype: ("features",) if rtype not in ("nodata_ratio", "resolution_drift",
                                          "raster_stats_outlier")
    else ("raster_stats",)
    for rtype in RULE_TYPES
}
# crs_validity 只依赖 ctx.crs（vector/raster 都适用）
RULE_INPUTS["crs_validity"] = ("crs",)

__all__ = [
    "RuleOutcome",
    "RuleEvalContext",
    "RULE_FUNCTIONS",
    "RULE_INPUTS",
]
