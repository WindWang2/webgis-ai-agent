"""G21 OD flow product builder (pure domain core).

No SessionStore, no MapSpec, no I/O: ``build_flow_product`` takes the OD
payload (+ optional node payload), a :class:`FlowProductConfig`, and source
scope/revision identity, and returns a :class:`FlowProductResult` value
object (product dict + edge FeatureCollection + node FeatureCollection).
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from . import geometry as geo
from .contracts import (
    COST_LIKE_WEIGHT_FIELDS,
    MAX_INPUT_ROWS,
    MAX_OUTPUT_EDGES,
    MAX_OUTPUT_NODES,
    SCHEMA_VERSION,
    SUPPORTED_CRS,
    SUPPORTED_MODES,
    FlowProductConfig,
    FlowProductError,
)

DEFAULT_PALETTE = ["#0d0887", "#7e03a8", "#cc4778", "#f89540", "#f0f921"]

NodeKey = Tuple[str, str]  # ("id", business_id) | ("coord", "lng,lat")


@dataclass
class FlowProductResult:
    """Value object returned by :func:`build_flow_product`."""

    product: Dict[str, Any]
    edge_fc: Dict[str, Any]
    node_fc: Dict[str, Any]


# ── canonical helpers ────────────────────────────────────────────────────


def _canon(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, default=str)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _fp(obj: Any) -> str:
    return _sha(_canon(obj))


def _round6(v: float) -> float:
    return round(float(v), 6)


def _coord_key(lng: float, lat: float) -> str:
    lng_r, lat_r = _round6(lng) + 0.0, _round6(lat) + 0.0  # normalize -0.0
    return f"{lng_r:.6f},{lat_r:.6f}"


def _haversine_km(lng1: float, lat1: float, lng2: float, lat2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = p2 - p1
    dlmb = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(a)))


# ── input extraction ─────────────────────────────────────────────────────


def _extract_rows(payload: Any) -> List[Dict[str, Any]]:
    """Accept od_table docs, plain row lists, or FCs with OD properties."""
    if isinstance(payload, list):
        return [r for r in payload if isinstance(r, dict)]
    if not isinstance(payload, dict):
        return []
    rows = payload.get("rows")
    if isinstance(rows, list):
        return [r for r in rows if isinstance(r, dict)]
    features = payload.get("features")
    if isinstance(features, list) and payload.get("type") == "FeatureCollection":
        return [
            f["properties"]
            for f in features
            if isinstance(f, dict) and isinstance(f.get("properties"), dict)
        ]
    return []


def _check_crs(payload: Any, config: FlowProductConfig) -> None:
    candidates = [("config.source_crs", config.source_crs)]
    if isinstance(payload, dict) and payload.get("crs"):
        candidates.append(("payload.crs", payload["crs"]))
    for label, crs in candidates:
        if str(crs).strip().lower() not in SUPPORTED_CRS:
            raise FlowProductError(
                f"不支持的 CRS '{crs}'（{label}）：OD 流产品仅接受 EPSG:4326/WGS84；"
                "投影坐标（如 EPSG:3857 米）不能当作经纬度，请先投影转换"
            )


def _check_semantics(config: FlowProductConfig) -> None:
    if config.value_semantics == "cost":
        raise FlowProductError(
            "value_semantics=cost：成本/时间/距离语义不能当流量渲染；"
            "请改用流量字段或明确 value_semantics=flow"
        )
    if config.value_semantics == "auto":
        wf = config.weight_field.strip().lower()
        if wf in COST_LIKE_WEIGHT_FIELDS:
            raise FlowProductError(
                f"weight_field '{config.weight_field}' 呈成本/时间/距离语义"
                f"（od_matrix 类成本不能当流量）；请改用流量字段或显式声明 value_semantics=flow"
            )


# ── node table + node registry ───────────────────────────────────────────


def _node_conflict(id_value: str, prev: Tuple[float, float],
                   new: Tuple[float, float]) -> FlowProductError:
    return FlowProductError(
        f"节点 '{id_value}' 坐标冲突：{prev} vs {new}；"
        "同一显式业务 ID 不允许对应不同坐标（非 first-wins），请修正数据或拆分产品"
    )


def _parse_node_table(
    node_payload: Any, config: FlowProductConfig
) -> Dict[str, Dict[str, Any]]:
    """Explicit node table: rows or Point FeatureCollection, one-to-one."""
    if node_payload is None:
        return {}
    table: Dict[str, Dict[str, Any]] = {}

    def put(nid: Any, lng: Any, lat: Any, name: Any = None) -> None:
        if nid is None or nid == "":
            return
        try:
            flng, flat = float(lng), float(lat)
        except (TypeError, ValueError):
            return
        if not (math.isfinite(flng) and math.isfinite(flat)):
            return
        if not (-180 <= flng <= 180 and -90 <= flat <= 90):
            return
        key = str(nid)
        prev = table.get(key)
        if prev is not None:
            if (prev["lng_r"], prev["lat_r"]) != (_round6(flng), _round6(flat)):
                raise _node_conflict(key, (prev["lng_r"], prev["lat_r"]),
                                     (_round6(flng), _round6(flat)))
            return
        table[key] = {"lng": flng, "lat": flat, "lng_r": _round6(flng),
                      "lat_r": _round6(flat), "name": name}

    if isinstance(node_payload, dict) and isinstance(node_payload.get("rows"), list):
        for r in node_payload["rows"]:
            if isinstance(r, dict):
                put(r.get(config.node_id_field), r.get(config.node_lng_field),
                    r.get(config.node_lat_field), r.get("name"))
        return table
    if isinstance(node_payload, list):
        for r in node_payload:
            if isinstance(r, dict):
                put(r.get(config.node_id_field), r.get(config.node_lng_field),
                    r.get(config.node_lat_field), r.get("name"))
        return table
    if isinstance(node_payload, dict) and node_payload.get("type") == "FeatureCollection":
        for f in node_payload.get("features", []):
            if not isinstance(f, dict):
                continue
            geom = f.get("geometry") or {}
            if geom.get("type") != "Point":
                continue
            coords = geom.get("coordinates") or []
            if len(coords) < 2:
                continue
            props = f.get("properties") or {}
            nid = props.get(config.node_id_field)
            if nid is None:
                nid = f.get("id")
            put(nid, coords[0], coords[1], props.get("name"))
        return table
    raise FlowProductError("节点表结构不识别（需要 rows 列表或 Point FeatureCollection）")


class _NodeRegistry:
    """Stable node identity: render IDs hash source scope + typed business
    identity (+ coordinates for derived identity) — never array indices."""

    def __init__(self, scope: str) -> None:
        self.scope = scope
        self.nodes: Dict[NodeKey, Dict[str, Any]] = {}

    @staticmethod
    def sort_key(key: NodeKey) -> Tuple[str, str]:
        return (key[0], key[1])

    def resolve(self, id_value: Any, lng: Any, lat: Any,
                table: Dict[str, Dict[str, Any]]) -> Tuple[Optional[NodeKey], Optional[str]]:
        """Returns (node_key, exclusion_reason). Exactly one is None."""
        inline: Optional[Tuple[float, float]] = None
        try:
            flng, flat = float(lng), float(lat)
            if math.isfinite(flng) and math.isfinite(flat) and (
                -180 <= flng <= 180 and -90 <= flat <= 90
            ):
                inline = (flng, flat)
        except (TypeError, ValueError):
            inline = None

        has_id = id_value is not None and id_value != ""
        if has_id:
            key: NodeKey = ("id", str(id_value))
            existing = self.nodes.get(key)
            coords = inline
            tentry = table.get(str(id_value))
            if tentry is not None:
                if inline is not None and (
                    tentry["lng_r"], tentry["lat_r"]
                ) != (_round6(inline[0]), _round6(inline[1])):
                    raise _node_conflict(str(id_value),
                                         (tentry["lng_r"], tentry["lat_r"]),
                                         (_round6(inline[0]), _round6(inline[1])))
                coords = (tentry["lng"], tentry["lat"])
            if coords is None:
                if existing is not None:
                    return key, None
                return None, "unmapped_node"
            lng_r, lat_r = _round6(coords[0]), _round6(coords[1])
            if existing is not None:
                if (existing["lng_r"], existing["lat_r"]) != (lng_r, lat_r):
                    raise _node_conflict(str(id_value),
                                         (existing["lng_r"], existing["lat_r"]),
                                         (lng_r, lat_r))
                return key, None
            name = (tentry or {}).get("name") or str(id_value)
            self.nodes[key] = {
                "key": key, "render_id": self._render_id(key),
                "legacy_id": str(id_value), "lng": coords[0], "lat": coords[1],
                "lng_r": lng_r, "lat_r": lat_r, "name": name, "derived": False,
            }
            return key, None

        if inline is None:
            return None, "invalid_coordinate"
        key = ("coord", _coord_key(inline[0], inline[1]))
        if key not in self.nodes:
            self.nodes[key] = {
                "key": key, "render_id": self._render_id(key),
                "legacy_id": key[1], "lng": inline[0], "lat": inline[1],
                "lng_r": _round6(inline[0]), "lat_r": _round6(inline[1]),
                "name": key[1], "derived": True,
            }
        return key, None

    def _render_id(self, key: NodeKey) -> str:
        payload = {"scope": self.scope, "kind": "node",
                   "id_type": key[0], "id_value": key[1]}
        return "n_" + _sha(_canon(payload))[:16]

    def require(self, key: NodeKey) -> Dict[str, Any]:
        return self.nodes[key]


# ── time helpers ─────────────────────────────────────────────────────────


def _parse_time(value: Any, tz: ZoneInfo) -> Optional[datetime]:
    try:
        if isinstance(value, bool):
            return None
        if isinstance(value, (int, float)):
            if not math.isfinite(float(value)):
                return None
            return datetime.fromtimestamp(float(value), tz)
        if isinstance(value, str):
            s = value.strip()
            if not s:
                return None
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=tz)
            return dt
    except (ValueError, OverflowError, OSError):
        return None
    return None


# ── weight encoding ──────────────────────────────────────────────────────


def _encode(weight: float, transform: str, d0: float, d1: float) -> float:
    if d1 <= d0:
        return 0.0
    if transform == "sqrt":
        lo, hi = math.sqrt(max(0.0, d0)), math.sqrt(max(0.0, d1))
        if hi <= lo:
            return 0.0
        v = (math.sqrt(max(0.0, weight)) - lo) / (hi - lo)
    elif transform == "log1p":
        lo, hi = math.log1p(max(0.0, d0)), math.log1p(max(0.0, d1))
        if hi <= lo:
            return 0.0
        v = (math.log1p(max(0.0, weight)) - lo) / (hi - lo)
    else:
        v = (weight - d0) / (d1 - d0)
    return round(min(1.0, max(0.0, v)), 6)


# ── main build ───────────────────────────────────────────────────────────


def build_flow_product(
    payload: Any,
    config: FlowProductConfig,
    *,
    source_scope: str,
    source_revision: Any,
    node_payload: Any = None,
) -> FlowProductResult:
    rows = _extract_rows(payload)
    if not rows:
        raise FlowProductError(
            "OD 输入为空或结构不识别（需要 rows 列表、od_table 或带 OD 属性的 FeatureCollection）"
        )
    if len(rows) > MAX_INPUT_ROWS:
        raise FlowProductError(f"OD 行数 {len(rows)} 超过处理上限 {MAX_INPUT_ROWS}")
    _check_crs(payload, config)
    _check_semantics(config)
    try:
        tz = ZoneInfo(config.timezone)
    except Exception as e:
        raise FlowProductError(f"无效 IANA timezone '{config.timezone}': {e}") from e

    table = _parse_node_table(node_payload, config)
    registry = _NodeRegistry(source_scope)

    warnings: List[Dict[str, Any]] = []
    degradations: List[Dict[str, Any]] = []
    excluded: Dict[str, int] = {}

    def exclude(reason: str) -> None:
        excluded[reason] = excluded.get(reason, 0) + 1

    # Time window [start, end)
    win_start = _parse_time(config.time_start, tz) if config.time_start else None
    win_end = _parse_time(config.time_end, tz) if config.time_end else None
    if config.time_field:
        for label, raw, parsed in (("time_start", config.time_start, win_start),
                                   ("time_end", config.time_end, win_end)):
            if raw and parsed is None:
                raise FlowProductError(
                    f"无法解析 {label} '{raw}'：需要 ISO-8601 时间（如 2024-01-01T00:00:00Z）"
                )
    time_active = bool(config.time_field and (win_start or win_end))

    cat_field = config.category_field
    cat_values = set(config.category_values) if config.category_values else None

    # Pass 1 — field validity + filters + directed aggregation by dimensions.
    agg: Dict[Tuple[NodeKey, NodeKey, Tuple[str, ...]], Dict[str, Any]] = {}
    n_raw = len(rows)
    n_valid = 0
    n_filtered = 0
    w_raw = 0.0
    w_valid = 0.0
    w_filtered = 0.0
    any_coords = bool(table)

    for r in rows:
        # weight
        wv = r.get(config.weight_field)
        if wv is None or (isinstance(wv, str) and not wv.strip()):
            exclude("missing_weight")
            continue
        try:
            w = float(wv)
        except (TypeError, ValueError):
            exclude("invalid_weight")
            continue
        if not math.isfinite(w):
            exclude("invalid_weight")
            continue
        w_raw += w
        if w < 0:
            exclude("negative_weight")
            continue
        # coordinates / identity
        o_key, o_reason = registry.resolve(
            r.get(config.origin_id_field),
            r.get(config.origin_lng_field), r.get(config.origin_lat_field), table)
        d_key, d_reason = registry.resolve(
            r.get(config.destination_id_field),
            r.get(config.destination_lng_field), r.get(config.destination_lat_field),
            table)
        if o_key is None or d_key is None:
            exclude(o_reason or d_reason or "invalid_coordinate")
            continue
        any_coords = True
        n_valid += 1
        w_valid += w
        # time window [start, end)
        if time_active:
            t = _parse_time(r.get(config.time_field), tz)
            if t is None:
                exclude("invalid_time")
                continue
            if win_start and t < win_start:
                exclude("time_out_of_window")
                continue
            if win_end and t >= win_end:
                exclude("time_out_of_window")
                continue
        # category
        if cat_field and cat_values is not None:
            if str(r.get(cat_field, "")) not in cat_values:
                exclude("category_excluded")
                continue
        n_filtered += 1
        w_filtered += w
        dims = tuple(str(r.get(f, "")) for f in config.dimension_fields)
        key3 = (o_key, d_key, dims)
        bucket = agg.get(key3)
        if bucket is None:
            bucket = {"weight": 0.0, "members": {}}
            agg[key3] = bucket
        bucket["weight"] += w
        fp = _fp(r)
        bucket["members"][fp] = bucket["members"].get(fp, 0) + 1

    if not any_coords:
        raise FlowProductError(
            "OD 行既无有效坐标也无节点表可解析；拒绝地理编码猜测——"
            "请提供起讫经纬度字段或显式节点表"
        )

    # Pass 2 — unordered links (canonical node-ascending orientation).
    links: Dict[Tuple[NodeKey, NodeKey, Tuple[str, ...]], Dict[str, Any]] = {}
    self_loops: List[Tuple[NodeKey, Tuple[str, ...], Dict[str, Any]]] = []
    for (o_key, d_key, dims), bucket in agg.items():
        if o_key == d_key:
            self_loops.append((o_key, dims, bucket))
            continue
        lo, hi = (o_key, d_key)
        if registry.sort_key(hi) < registry.sort_key(lo):
            lo, hi = hi, lo
        lk = (lo, hi, dims)
        link = links.get(lk)
        if link is None:
            link = {
                "fwd": None, "bwd": None, "members": {},
                "forward_members": {}, "backward_members": {},
            }
            links[lk] = link
        side = "fwd" if (o_key, d_key) == (lo, hi) else "bwd"
        link[side] = (link[side] or 0.0) + bucket["weight"]
        member_key = "forward_members" if side == "fwd" else "backward_members"
        for fp, cnt in bucket["members"].items():
            link["members"][fp] = link["members"].get(fp, 0) + cnt
            link[member_key][fp] = link[member_key].get(fp, 0) + cnt

    # Pass 3 — mode projection.
    mode = config.direction_mode
    unknown_net_pairs = 0
    projected: List[Dict[str, Any]] = []

    def link_stats(lo: NodeKey, hi: NodeKey, link: Dict[str, Any]) -> Dict[str, Any]:
        fwd, bwd = link["fwd"], link["bwd"]
        f_obs, b_obs = fwd is not None, bwd is not None
        if f_obs and b_obs:
            net = fwd - bwd
            status = "observed" if net != 0 else "zero"
        elif f_obs or b_obs:
            if config.missing_reverse == "zero":
                net = fwd if f_obs else -bwd  # type: ignore[operator]
                status = "assumed_zero"
            else:
                net = None
                status = "unknown"
        else:  # pragma: no cover — links always have a side
            net, status = None, "unknown"
        gross = (fwd or 0.0) + (bwd or 0.0)
        return {
            "forward": fwd, "backward": bwd,
            "forward_observed": f_obs, "backward_observed": b_obs,
            "forward_unknown": (not f_obs) and config.missing_reverse == "unknown",
            "backward_unknown": (not b_obs) and config.missing_reverse == "unknown",
            "net": net, "net_status": status, "gross": gross,
        }

    # link_stats 每链接只算一次：unknown 计数、可用性判定与主循环共用，
    # 避免重复遍历（review P2：原实现对同一 link 统计三遍）。
    # 自环计入可用性：自环净流恒零 → 不产边但节点仍在（既有契约，
    # 由 test_self_loop_gross_once_nodes_in_out_once 锁定）。
    net_unknown_total = 0
    net_available = bool(self_loops)
    for (lo, hi, _dims), link in links.items():
        st = link_stats(lo, hi, link)
        if st["net"] is None:
            net_unknown_total += 1
        else:
            net_available = True
    if mode == "net" and not net_available:
        raise FlowProductError(
            "净流无法计算：所有反向关系都未观测；missing_reverse=unknown 时"
            "不能把未观测当作真实零。请补全双向数据或显式声明 missing_reverse=zero"
        )

    for (lo, hi, dims), link in links.items():
        st = link_stats(lo, hi, link)
        if mode == "net":
            if st["net_status"] == "unknown":
                unknown_net_pairs += 1
                continue
            if st["net_status"] == "zero":
                continue
            net = st["net"]
            assert net is not None
            # Orient geometry along the net flow; identity stays canonical.
            flow_lo, flow_hi = (lo, hi) if net > 0 else (hi, lo)
            projected.append({
                "lo": lo, "hi": hi, "dims": dims, "members": link["members"],
                "o": flow_lo, "d": flow_hi, "weight": abs(net),
                "directed": True, **st,
            })
        elif mode == "undirected-total":
            projected.append({
                "lo": lo, "hi": hi, "dims": dims, "members": link["members"],
                "o": lo, "d": hi, "weight": st["gross"],
                "directed": False, **st,
                "net": None, "net_status": None,
            })
        else:  # directed — one display edge per observed direction
            for o_key, d_key, side in ((lo, hi, "forward"), (hi, lo, "backward")):
                w = st[side]
                if w is None:
                    continue
                projected.append({
                    "lo": lo, "hi": hi, "dims": dims,
                    "members": link["forward_members" if side == "forward" else "backward_members"],
                    "o": o_key, "d": d_key, "weight": w,
                    "directed": True, **st,
                })

    # Self-loops: gross once; net 0 → never a net edge.
    for o_key, dims, bucket in self_loops:
        st = {
            "forward": bucket["weight"], "backward": None,
            "forward_observed": True, "backward_observed": False,
            "forward_unknown": False, "backward_unknown": False,
            "net": 0.0, "net_status": "zero", "gross": bucket["weight"],
        }
        if mode == "directed":
            projected.append({
                "lo": o_key, "hi": o_key, "dims": dims,
                "members": bucket["members"],
                "o": o_key, "d": o_key, "weight": bucket["weight"],
                "directed": True, **st,
            })
        elif mode == "undirected-total":
            projected.append({
                "lo": o_key, "hi": o_key, "dims": dims,
                "members": bucket["members"],
                "o": o_key, "d": o_key, "weight": bucket["weight"],
                "directed": False, **st,
                "net": None, "net_status": None,
            })
        # net mode: zero → no render edge.

    if unknown_net_pairs:
        degradations.append({
            "code": "net_unknown_missing_reverse",
            "message": f"{unknown_net_pairs} 个单向 pair 的净流不可计算"
                       "（missing_reverse=unknown，绝不当作零）",
            "pairs": unknown_net_pairs,
        })

    # Pass 4 — threshold + deterministic top-N (magnitude desc, stable ID).
    min_w = config.min_weight
    thresholded = [p for p in projected if p["weight"] >= min_w]

    def edge_render_id(p: Dict[str, Any]) -> str:
        # Directed identity includes the direction; net/undirected identity is
        # the canonical (node-ascending) link, stable under reverse swaps.
        o_key, d_key = (p["o"], p["d"]) if mode == "directed" else (p["lo"], p["hi"])
        payload = {
            "scope": source_scope, "kind": "edge",
            "origin": {"type": o_key[0], "value": o_key[1]},
            "destination": {"type": d_key[0], "value": d_key[1]},
            "dimensions": dict(zip(config.dimension_fields, p["dims"])),
        }
        return "e_" + _sha(_canon(payload))[:16]

    for p in projected:
        p["render_id"] = edge_render_id(p)

    top_n = min(config.top_n, MAX_OUTPUT_EDGES)
    if config.top_n > MAX_OUTPUT_EDGES:
        warnings.append({
            "code": "top_n_clamped",
            "message": f"top_n={config.top_n} 超过输出上限，已截断为 {MAX_OUTPUT_EDGES}",
        })
    selected = sorted(thresholded, key=lambda p: (-p["weight"], p["render_id"]))[:top_n]

    # Weight domain: fixed config domain wins; top-N never changes it.
    if config.weight_domain is not None:
        d0, d1 = config.weight_domain
    else:
        mags = [p["weight"] for p in thresholded]
        d0, d1 = (min(mags), max(mags)) if mags else (0.0, 1.0)
    degenerate = d1 <= d0

    # Pass 5 — node totals over the declared scope population (raw directed
    # vectors, never pseudo origin/destination totals, never the top-N slice).
    scope_pop = agg
    if config.node_totals_scope == "thresholded":
        scope_pop = {
            k: v for k, v in agg.items()
            if v["weight"] >= min_w
        }
    totals: Dict[NodeKey, Dict[str, float]] = {}
    for (o_key, d_key, _dims), bucket in scope_pop.items():
        t = totals.setdefault(o_key, {"inbound": 0.0, "outbound": 0.0})
        t["outbound"] += bucket["weight"]
        t = totals.setdefault(d_key, {"inbound": 0.0, "outbound": 0.0})
        t["inbound"] += bucket["weight"]

    # Pass 6 — geometry + features (display set only).
    pair_both = {}
    for p in selected:
        if p["lo"] != p["hi"] and mode == "directed":
            pair_both[(p["lo"], p["hi"])] = pair_both.get((p["lo"], p["hi"]), 0) + 1

    features: List[Dict[str, Any]] = []
    touched: Dict[NodeKey, None] = {}
    members_out: Dict[str, Any] = {}
    zero_len = 0
    for p in selected:
        o_node = registry.require(p["o"])
        d_node = registry.require(p["d"])
        if p["lo"] != p["hi"] and (o_node["lng_r"], o_node["lat_r"]) == (
            d_node["lng_r"], d_node["lat_r"]
        ):
            zero_len += 1
        separate = mode == "directed" and pair_both.get((p["lo"], p["hi"]), 0) > 1
        forward = (p["o"], p["d"]) == (p["lo"], p["hi"])
        geom, gmeta = geo.edge_geometry(
            (o_node["lng"], o_node["lat"]), (d_node["lng"], d_node["lat"]),
            kind=config.geometry, curvature=config.curvature,
            separate=separate, forward=forward,
        )
        touched[p["lo"]] = None
        touched[p["hi"]] = None
        encoded = _encode(p["weight"], config.weight_transform, d0, d1)
        props: Dict[str, Any] = {
            "id": p["render_id"],
            "origin_id": o_node["legacy_id"],
            "destination_id": d_node["legacy_id"],
            "origin_render_id": o_node["render_id"],
            "destination_render_id": d_node["render_id"],
            "origin_node_id": o_node["render_id"],
            "destination_node_id": d_node["render_id"],
            "origin_lng": o_node["lng_r"], "origin_lat": o_node["lat_r"],
            "destination_lng": d_node["lng_r"], "destination_lat": d_node["lat_r"],
            "weight": _round6(p["weight"]),
            "directed": p["directed"],
            "gross": _round6(p["gross"]),
            "forward": _round6(p["forward"]) if p["forward"] is not None else None,
            "backward": _round6(p["backward"]) if p["backward"] is not None else None,
            "forward_observed": p["forward_observed"],
            "backward_observed": p["backward_observed"],
            "forward_unknown": p["forward_unknown"],
            "backward_unknown": p["backward_unknown"],
            "net": _round6(p["net"]) if p["net"] is not None else None,
            "net_magnitude": _round6(abs(p["net"])) if p["net"] is not None else None,
            "net_status": p["net_status"] if mode == "net" else None,
            "weight_encoded": encoded,
            "distance_km": round(_haversine_km(
                o_node["lng"], o_node["lat"], d_node["lng"], d_node["lat"]), 3),
            "geometry_kind": gmeta["geometry_kind"],
            "curve_version": gmeta["curve_version"],
            "vertices": gmeta["vertices"],
            "dimensions": dict(zip(config.dimension_fields, p["dims"])),
        }
        if config.normalize:
            props["weight_norm"] = _encode(p["weight"], "linear", d0, d1)
        features.append({
            "type": "Feature", "id": p["render_id"],
            "geometry": geom, "properties": props,
        })
        members_out[p["render_id"]] = [
            {"row_fingerprint": fp, "count": cnt}
            for fp, cnt in sorted(p["members"].items())
        ]

    if zero_len:
        warnings.append({
            "code": "zero_length_edge",
            "message": f"{zero_len} 条边两端节点坐标相同（不同节点同坐标），"
                       "已用小型环披露其退化几何",
            "edges": zero_len,
        })
    derived_nodes = sum(1 for n in registry.nodes.values() if n["derived"])
    if derived_nodes:
        warnings.append({
            "code": "derived_node_identity",
            "message": f"{derived_nodes} 个节点无显式业务 ID，"
                       "使用 6 位小数 lng+lat 派生身份（重排序稳定；"
                       "同坐标不同业务实体无法区分）",
            "nodes": derived_nodes,
        })

    # Node output: only nodes touched by displayed edges, deterministic
    # truncation with disclosure.
    node_recs = []
    for key in touched:
        t = totals.get(key, {"inbound": 0.0, "outbound": 0.0})
        total = t["inbound"] + t["outbound"]
        node = registry.require(key)
        node_recs.append({
            "key": key, "render_id": node["render_id"],
            "legacy_id": node["legacy_id"], "name": node["name"],
            "lng": node["lng_r"], "lat": node["lat_r"],
            "inbound": _round6(t["inbound"]), "outbound": _round6(t["outbound"]),
            "net": _round6(t["outbound"] - t["inbound"]),
            "total": _round6(total),
            "radius_value": round(math.sqrt(total), 6),
        })
    node_recs.sort(key=lambda n: (-n["total"], n["render_id"]))
    truncated = len(node_recs) > MAX_OUTPUT_NODES
    if truncated:
        node_recs = node_recs[:MAX_OUTPUT_NODES]
        degradations.append({
            "code": "node_output_truncated",
            "message": f"显示节点超过 {MAX_OUTPUT_NODES}，已按 total 降序确定性截断",
        })
    node_features = []
    for n in node_recs:
        node_features.append({
            "type": "Feature", "id": n["render_id"],
            "geometry": {"type": "Point", "coordinates": [n["lng"], n["lat"]]},
            "properties": {
                "node_id": n["render_id"], "legacy_id": n["legacy_id"],
                "name": n["name"], "lng": n["lng"], "lat": n["lat"],
                "inbound": n["inbound"], "outbound": n["outbound"],
                "net": n["net"], "total": n["total"],
                "radius_value": n["radius_value"],
                "total_scope": config.node_totals_scope,
            },
        })

    # Stats — conservation across the declared populations. Directed display
    # selects individual vectors; the other modes select a whole canonical link.
    if mode == "net":
        # Unknown-net links remain in the gross denominator: their original
        # vectors are known even when their net direction/magnitude is not.
        pop_gross = sum((lk["fwd"] or 0.0) + (lk["bwd"] or 0.0) for lk in links.values())
        pop_gross += sum(b["weight"] for _o, _d, b in self_loops)
    else:
        pop_gross = sum(
            p["weight"] if mode == "directed" else p["gross"]
            for p in thresholded
        )
    disp_gross = sum(
        p["weight"] if mode == "directed" else p["gross"]
        for p in selected
    )

    def net_magnitude(population: List[Dict[str, Any]]) -> float:
        seen = set()
        total = 0.0
        for p in population:
            lk_key = (p["lo"], p["hi"], p["dims"])
            if lk_key in seen or p["lo"] == p["hi"]:
                continue
            seen.add(lk_key)
            if p["net"] is not None:
                total += abs(p["net"])
        return total

    pop_net_mag = net_magnitude(thresholded)
    disp_net_mag = net_magnitude(selected)

    stats = {
        "rows": {"raw": n_raw, "valid": n_valid, "filtered": n_filtered,
                 "excluded": excluded},
        "weights": {"raw": _round6(w_raw), "valid": _round6(w_valid),
                    "filtered": _round6(w_filtered)},
        "edges": {"aggregated": len(projected), "thresholded": len(thresholded),
                  "displayed": len(features)},
        "gross": {"population": _round6(pop_gross),
                  "displayed": _round6(disp_gross),
                  "retained": (round(disp_gross / pop_gross, 6)
                               if pop_gross > 0 else None)},
        "net": {"population_magnitude": _round6(pop_net_mag),
                "displayed_magnitude": _round6(disp_net_mag),
                "magnitude_coverage": (round(disp_net_mag / pop_net_mag, 6)
                                       if pop_net_mag > 0 else None)},
        "nodes": {"total": len(registry.nodes), "displayed": len(node_recs)},
    }

    # Fingerprints — order-invariant, index-free.
    row_fps = sorted(_fp(r) for r in rows)
    node_fps = sorted(
        _canon({"id": n["legacy_id"], "lng": n["lng_r"], "lat": n["lat_r"]})
        for n in registry.nodes.values()
    )
    source_fingerprint = _sha(_canon({"rows": row_fps, "nodes": node_fps}))
    analysis_payload = {
        "mode": mode,
        "missing_reverse": config.missing_reverse,
        "links": sorted(
            (_canon({"lo": lk_key[0], "hi": lk_key[1], "dims": list(lk_key[2]),
                     "fwd": lk["fwd"], "bwd": lk["bwd"]})
             for lk_key, lk in links.items())
        ),
        "self_loops": sorted(
            _canon({"node": o_key, "dims": list(dims), "weight": b["weight"]})
            for o_key, dims, b in self_loops
        ),
        "nodes": sorted(
            [list(n["key"]), n["lng_r"], n["lat_r"]]
            for n in registry.nodes.values()
        ),
    }
    analysis_fingerprint = _sha(_canon(analysis_payload))

    id_mapping = {
        "render_to_legacy": {n["render_id"]: n["legacy_id"]
                             for n in registry.nodes.values()},
        "legacy_to_render": {n["legacy_id"]: n["render_id"]
                             for n in registry.nodes.values()},
    }

    family_id = config.product_id or "odf_" + _sha(_canon({
        "scope": source_scope,
        "origin_id_field": config.origin_id_field,
        "destination_id_field": config.destination_id_field,
        "weight_field": config.weight_field,
        "node_table_ref": config.node_table_ref,
    }))[:16]
    supported_modes = [m for m in SUPPORTED_MODES if m != "net"]
    if net_available:
        supported_modes.append("net")

    product: Dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "family_id": family_id,
        "product_id": config.product_id or f"od-flow-{analysis_fingerprint[:12]}",
        "source_ref": config.od_table_ref,
        "source_revision": source_revision,
        "source_fingerprint": source_fingerprint,
        "analysis_fingerprint": analysis_fingerprint,
        "fingerprint": analysis_fingerprint,
        "config": config.model_dump(),
        "direction": {
            "mode": mode,
            "missing_reverse": config.missing_reverse,
            "net_reference": config.net_reference,
        },
        "supported_modes": supported_modes,
        "mode_availability": {
            "directed": {"available": True, "reason": "raw_vectors_retained"},
            "undirected-total": {"available": True, "reason": "raw_vectors_retained"},
            "net": {
                "available": net_available,
                "reason": "computable_pairs" if net_available else "missing_reverse_observations",
                "unknown_pairs": net_unknown_total,
            },
        },
        "stats": stats,
        "warnings": warnings,
        "degradations": degradations,
        "nodes": {
            "total": len(registry.nodes), "displayed": len(node_recs),
            "cap": MAX_OUTPUT_NODES, "truncated": truncated,
            "totals_scope": config.node_totals_scope,
        },
        "edges": {
            "aggregated": len(projected), "thresholded": len(thresholded),
            "displayed": len(features), "cap": MAX_OUTPUT_EDGES,
            "returned": len(features), "offset": 0,
        },
        "aggregation_members": members_out,
        "id_mapping": id_mapping,
        "legend": {
            "field": "weight",
            "source_field": config.weight_field,
            "unit": config.weight_unit,
            "transform": config.weight_transform,
            "domain": [_round6(d0), _round6(d1)],
            "fixed_domain": config.weight_domain is not None,
            "degenerate": degenerate,
            "palette": config.palette or DEFAULT_PALETTE,
        },
        "weight_transform": config.weight_transform,
        "weight_domain": [_round6(d0), _round6(d1)],
        "weight_unit": config.weight_unit,
        "geometry": {
            "kind": config.geometry,
            "curve_version": geo.CURVE_VERSION,
            "max_segments_per_edge": geo.MAX_EDGE_SEGMENTS,
            "arrows": {
                "show": config.show_arrows,
                "placement": "line",
                "spacing_px": config.arrow_spacing_px,
                "size_px": config.arrow_size_px,
            },
        },
    }

    edge_fc = {"type": "FeatureCollection", "features": features}
    node_fc = {"type": "FeatureCollection", "features": node_features}
    return FlowProductResult(product=product, edge_fc=edge_fc, node_fc=node_fc)
