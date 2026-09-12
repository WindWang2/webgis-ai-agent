"""Data Profile V9 —— 增量画像（P2）。

任务书：追加数据只算增量。核心是一个**可合并画像状态**（mergeable
state）：逐字段 Welford 兼容矩（n/sum/sumsq/min/max/类型计数）+ H3 cell
直方图 + 几何族计数。追加批次只扫描新要素，与既有状态合并 ——
不重扫全量。

- **纯函数**：``update_state`` / ``merge_states`` 恒返回新 dict（无就地
  别名，可安全跨请求持有）；
- **JSON 可序列化**：状态可入缓存/DB（画像失效由 ref_lifecycle 钩子 +
  修订绑定共同兜底）；
- **有界**：单批扫描 ≤ ``max_scan``；H3 cell 数有硬帽（超出保留 top 计数
  并打 ``truncated`` 旗标）。
"""
from __future__ import annotations

import copy
import math
from typing import Any, Dict, List

_MAX_CELLS = 512
_MAX_FIELDS = 48
_DEFAULT_RESOLUTION = 7

_STATE_VERSION = 1


def new_state(resolution: int = _DEFAULT_RESOLUTION) -> Dict[str, Any]:
    return {
        "v": _STATE_VERSION,
        "count": 0,
        "resolution": int(resolution),
        "fields": {},
        "geom_types": {},
        "h3": {},
        "h3_truncated": False,
    }


def _field_slot(state: Dict[str, Any], name: str) -> Dict[str, Any]:
    fields = state["fields"]
    if name not in fields and len(fields) < _MAX_FIELDS:
        fields[name] = {
            "n": 0, "nulls": 0, "sum": 0.0, "sumsq": 0.0,
            "min": None, "max": None, "types": {},
        }
    return fields.get(name, {})


def _h3_bucket(features: List[Dict[str, Any]], resolution: int) -> Dict[str, int]:
    from app.services.data_profile.distribution import _representative_point

    try:
        import h3
    except Exception:  # noqa: BLE001 — h3 缺席时增量状态退化为无分布
        return {}
    out: Dict[str, int] = {}
    for f in features:
        geometry = f.get("geometry") if isinstance(f, dict) else None
        pt = _representative_point(geometry) if isinstance(geometry, dict) else None
        if pt is None:
            continue
        try:
            cell = h3.latlng_to_cell(pt[1], pt[0], int(resolution))
        except (ValueError, TypeError):
            continue
        out[cell] = out.get(cell, 0) + 1
    return out


def _cap_cells(cells: Dict[str, int], cap: int = _MAX_CELLS) -> tuple[Dict[str, int], bool]:
    if len(cells) <= cap:
        return cells, False
    kept = dict(sorted(cells.items(), key=lambda kv: -kv[1])[:cap])
    return kept, True


def update_state(
    state: Dict[str, Any],
    features: List[Dict[str, Any]],
    *,
    max_scan: int = 50000,
) -> Dict[str, Any]:
    """增量合并一批要素（只扫本批；返回新状态）。"""
    new = copy.deepcopy(state)
    batch = [f for f in features[:max_scan] if isinstance(f, dict)]
    new["count"] = int(new.get("count") or 0) + len(batch)
    for f in batch:
        geometry = f.get("geometry")
        if isinstance(geometry, dict):
            gtype = str(geometry.get("type") or "other")
        else:
            gtype = "missing"
        new["geom_types"][gtype] = new["geom_types"].get(gtype, 0) + 1

        props = f.get("properties")
        if not isinstance(props, dict):
            continue
        for k, v in list(props.items())[:_MAX_FIELDS]:
            key = str(k)[:96]
            slot = _field_slot(new, key)
            if not slot:
                continue  # 字段数超帽：诚实丢增量（summary 侧标注 truncated）
            slot["n"] += 1
            if v is None or (isinstance(v, str) and v == ""):
                slot["nulls"] += 1
                continue
            tname = type(v).__name__ if not isinstance(v, bool) else "bool"
            if isinstance(v, (int, float)) and not isinstance(v, bool) \
                    and math.isfinite(float(v)):
                fv = float(v)
                slot["sum"] += fv
                slot["sumsq"] += fv * fv
                slot["min"] = fv if slot["min"] is None else min(slot["min"], fv)
                slot["max"] = fv if slot["max"] is None else max(slot["max"], fv)
                tname = "number"
            slot["types"][tname] = slot["types"].get(tname, 0) + 1

    resolution = int(new.get("resolution") or _DEFAULT_RESOLUTION)
    for cell, cnt in _h3_bucket(batch, resolution).items():
        new["h3"][cell] = new["h3"].get(cell, 0) + cnt
    capped, truncated = _cap_cells(new["h3"])
    new["h3"] = capped
    new["h3_truncated"] = bool(new.get("h3_truncated")) or truncated
    return new


def merge_states(a: Dict[str, Any], b: Dict[str, Any]) -> Dict[str, Any]:
    """两个状态合成（n/sum/sumsq 可加；min/max 取极值；直方图相加）。"""
    if not a or not isinstance(a, dict):
        return copy.deepcopy(b)
    if not b or not isinstance(b, dict):
        return copy.deepcopy(a)
    out = copy.deepcopy(a)
    out["count"] = int(a.get("count") or 0) + int(b.get("count") or 0)
    if int(a.get("resolution") or 0) != int(b.get("resolution") or 0):
        # 分辨率不一致时以小（粗）为准 —— 粗粒度 cell 可加，细不可拆。
        out["resolution"] = min(int(a.get("resolution") or _DEFAULT_RESOLUTION),
                                int(b.get("resolution") or _DEFAULT_RESOLUTION))
    for name, slot_b in (b.get("fields") or {}).items():
        slot_a = out["fields"].get(name)
        if slot_a is None:
            if len(out["fields"]) < _MAX_FIELDS:
                out["fields"][name] = copy.deepcopy(slot_b)
            continue
        slot_a["n"] += slot_b.get("n", 0)
        slot_a["nulls"] += slot_b.get("nulls", 0)
        slot_a["sum"] += slot_b.get("sum", 0.0)
        slot_a["sumsq"] += slot_b.get("sumsq", 0.0)
        if slot_b.get("min") is not None:
            slot_a["min"] = slot_b["min"] if slot_a["min"] is None \
                else min(slot_a["min"], slot_b["min"])
        if slot_b.get("max") is not None:
            slot_a["max"] = slot_b["max"] if slot_a["max"] is None \
                else max(slot_a["max"], slot_b["max"])
        for t, c in (slot_b.get("types") or {}).items():
            slot_a["types"][t] = slot_a["types"].get(t, 0) + c
    for t, c in (b.get("geom_types") or {}).items():
        out["geom_types"][t] = out["geom_types"].get(t, 0) + c
    for cell, cnt in (b.get("h3") or {}).items():
        out["h3"][cell] = out["h3"].get(cell, 0) + cnt
    capped, truncated = _cap_cells(out["h3"])
    out["h3"] = capped
    out["h3_truncated"] = bool(a.get("h3_truncated")) or bool(b.get("h3_truncated")) or truncated
    return out


def state_summary(state: Dict[str, Any]) -> Dict[str, Any]:
    """状态 → 有界画像摘要（均值/标准差/极值/空值率/热点）。"""
    count = int(state.get("count") or 0)
    fields: Dict[str, Any] = {}
    for name, slot in list((state.get("fields") or {}).items())[:_MAX_FIELDS]:
        n = int(slot.get("n") or 0)
        entry: Dict[str, Any] = {
            "null_rate": round(slot.get("nulls", 0) / n, 4) if n else None,
            "types": dict(list((slot.get("types") or {}).items())[:4]),
        }
        if n and slot.get("sumsq"):
            mean = slot["sum"] / n
            var = max(0.0, slot["sumsq"] / n - mean * mean)
            entry["mean"] = round(mean, 6)
            entry["std"] = round(math.sqrt(var), 6)
        if slot.get("min") is not None:
            entry["min"] = slot["min"]
            entry["max"] = slot["max"]
        fields[name] = entry
    h3 = state.get("h3") or {}
    h3_total = sum(h3.values())
    top = sorted(h3.items(), key=lambda kv: -kv[1])[:8]
    return {
        "v": state.get("v"),
        "count": count,
        "resolution": state.get("resolution"),
        "geom_types": dict(list((state.get("geom_types") or {}).items())[:8]),
        "fields": fields,
        "fields_truncated": len(state.get("fields") or {}) >= _MAX_FIELDS,
        "h3_top": [{"cell": c, "count": n,
                    "share": round(n / h3_total, 4)} for c, n in top] if h3_total else [],
        "h3_cell_count": len(h3),
        "h3_truncated": bool(state.get("h3_truncated")),
    }


__all__ = ["new_state", "update_state", "merge_states", "state_summary"]
