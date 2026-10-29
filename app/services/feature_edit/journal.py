"""Feature-edit journal —— 纯函数核心（commit 与 undo/redo 共用同一路径）。

持久形态是 map_state ``_feature_edit_journals`` 下的 JSON dict；本模块只做
纯变换，不触碰 session store（I/O 在 executor）。纪律：

- **同路径回放**：apply_entry / invert_entry 同时被 commit（正向）与
  undo/redo（反向）使用，不建第二条执行路径。
- **链完整性**：entry.fingerprint_after 必须等于下一 entry 的
  fingerprint_before；undo 的前提只有一条 —— 当前载荷指纹 == head 的
  fingerprint_after（FIFO 丢弃只影响更早条目的可撤销性，披露 dropped）。
- **copy-on-write**：变换产出新结构，绝不就地改入参（map_state 读者可能
  正在读旧对象）。
"""
from __future__ import annotations

import copy
from typing import Any, Dict, List, Optional, Tuple

from app.services.feature_edit.contracts import (
    ENTRY_KINDS,
    JOURNAL_BYTE_BUDGET,
    MAX_UNDO_HISTORY,
    WFID_PROP,
)

__all__ = [
    "new_journal",
    "append_entry",
    "invert_entry",
    "apply_entry_effects",
    "journal_bytes",
    "head_entry",
    "entry_for_idempotency_key",
    "count_positions",
]

# 几何编辑/undo 保真的顶点规模门（超限 finding geometry_too_complex）。
MAX_GEOMETRY_POSITIONS = 2048


def new_journal() -> Dict[str, Any]:
    return {"entries": [], "head_seq": 0, "dropped": 0}


def journal_bytes(journal: Dict[str, Any]) -> int:
    """有界预算用字节估计（canonical JSON 长度；估算失败按 0 计）。"""
    import json

    try:
        return len(json.dumps(journal, sort_keys=True, separators=(",", ":"), ensure_ascii=False))
    except (TypeError, ValueError):
        return 0


def append_entry(journal: Dict[str, Any], entry: Dict[str, Any]) -> Dict[str, Any]:
    """纯函数 append：返回新 journal（FIFO + 字节预算，丢最旧并计 dropped）。

    kind 必须来自封闭集；seq 由本函数分配（= head_seq + 1），调用方不自带。
    """
    if entry.get("kind") not in ENTRY_KINDS:
        raise ValueError(f"invalid journal entry kind: {entry.get('kind')!r}")
    out: Dict[str, Any] = {
        "entries": list(journal.get("entries") or []),
        "head_seq": int(journal.get("head_seq") or 0),
        "dropped": int(journal.get("dropped") or 0),
    }
    seq = out["head_seq"] + 1
    out["entries"].append({**entry, "seq": seq})
    out["head_seq"] = seq
    # FIFO 条目数 + 字节预算（双闸；丢弃只针对非 head 侧的最旧条目）
    while len(out["entries"]) > MAX_UNDO_HISTORY or (
        len(out["entries"]) > 1 and journal_bytes(out) > JOURNAL_BYTE_BUDGET
    ):
        out["entries"].pop(0)
        out["dropped"] += 1
    return out


def head_entry(journal: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    entries = journal.get("entries") or []
    return entries[-1] if entries else None


def entry_for_idempotency_key(
    journal: Dict[str, Any], idempotency_key: str
) -> Optional[Dict[str, Any]]:
    """幂等命中查找（最新优先）。"""
    for entry in reversed(journal.get("entries") or []):
        if entry.get("idempotency_key") == idempotency_key:
            return entry
    return None


# ─── 要素级变换 ────────────────────────────────────────────────────────────

def _iter_positions(geometry: Any) -> int:
    """几何坐标位置数（Point/LineString/Polygon 及 Multi*，遍历计数）。"""
    count = 0

    def walk(node: Any) -> None:
        nonlocal count
        if not isinstance(node, (list, tuple)):
            return
        if len(node) >= 2 and isinstance(node[0], (int, float)) and isinstance(node[1], (int, float)):
            count += 1
            return
        for child in node:
            walk(child)

    if isinstance(geometry, dict):
        walk(geometry.get("coordinates"))
    return count


def count_positions(geometry: Any) -> int:
    return _iter_positions(geometry)


def apply_deltas_to_features(
    features: List[Dict[str, Any]], deltas: List[Dict[str, Any]]
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """把 per-feature deltas 应用到 features（按 WFID_PROP 寻址）。

    返回 (new_features, applied_wfids)。未命中的 wfid 跳过（调用方在
    preview 阶段已保证存在；commit 阶段的偏差由指纹 CAS 兜底拒绝）。
    copy-on-write：只有被命中的要素被复制。
    """
    by_wfid = {
        f.get("properties", {}).get(WFID_PROP): (i, f)
        for i, f in enumerate(features)
        if isinstance(f, dict)
    }
    out = list(features)
    applied: List[str] = []
    for delta in deltas:
        wfid = delta.get("wfid")
        hit = by_wfid.get(wfid)
        if hit is None:
            continue
        i, _feature = hit
        # 同一要素可命中多个 delta（一个 op 一条：先字段后几何）——基于
        # out[i] 的**当前态**累积，绝不由原始要素重新拷贝（否则后一个
        # delta 静默抹掉前一个的效果）。
        new_feature = copy.deepcopy(out[i])
        props = new_feature.get("properties")
        if not isinstance(props, dict):
            props = {}
            new_feature["properties"] = props
        fields = delta.get("fields")
        if isinstance(fields, dict):
            for name, change in fields.items():
                if not isinstance(change, dict):
                    continue
                if "after" in change and change["after"] is not None:
                    props[name] = change["after"]
                else:
                    props.pop(name, None)
        if delta.get("geometry_after") is not None:
            new_feature["geometry"] = copy.deepcopy(delta["geometry_after"])
        out[i] = new_feature
        applied.append(wfid)
    return out, applied


def apply_schema_ops_to_features(
    features: List[Dict[str, Any]], schema_ops: List[Dict[str, Any]]
) -> Tuple[List[Dict[str, Any]], bool]:
    """数据集级 schema 变换（当前词表只有 rename_field 的 from→to）。

    返回 (new_features, changed)。inverse 由 invert_schema_ops 提供，
    undo 走同一路径。
    """
    changed = False
    out = features
    for op in schema_ops or []:
        if op.get("op") != "rename_field":
            continue
        src, dst = op.get("from"), op.get("to")
        if not isinstance(src, str) or not isinstance(dst, str) or src == dst:
            continue
        new_out: List[Dict[str, Any]] = []
        for feature in out:
            props = feature.get("properties") if isinstance(feature, dict) else None
            if isinstance(props, dict) and src in props:
                nf = copy.deepcopy(feature)
                nf["properties"] = {dst: props[src], **{k: v for k, v in props.items() if k != src}}
                new_out.append(nf)
                changed = True
            else:
                new_out.append(feature)
        out = new_out
    return out, changed


def invert_schema_ops(schema_ops: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """schema_ops 的逆（rename 反向；顺序反转）。"""
    out: List[Dict[str, Any]] = []
    for op in reversed(schema_ops or []):
        if op.get("op") == "rename_field":
            out.append({"op": "rename_field", "from": op.get("to"), "to": op.get("from")})
    return out


def invert_deltas(deltas: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """per-feature deltas 的逆：before/after 互换。"""
    out: List[Dict[str, Any]] = []
    for delta in deltas or []:
        inv: Dict[str, Any] = {"wfid": delta.get("wfid")}
        fields = delta.get("fields")
        if isinstance(fields, dict):
            inv["fields"] = {
                name: {"before": change.get("after"), "after": change.get("before")}
                for name, change in fields.items()
                if isinstance(change, dict)
            }
        if delta.get("geometry_before") is not None or delta.get("geometry_after") is not None:
            inv["geometry_before"] = delta.get("geometry_after")
            inv["geometry_after"] = delta.get("geometry_before")
        out.append(inv)
    return out


def invert_entry(entry: Dict[str, Any]) -> Dict[str, Any]:
    """entry 的逆（undo/redo 复用 apply 语义，不建第二路径）。

    - deltas：before/after 互换；
    - schema_ops：invert_schema_ops；
    - 指纹/revision 字段由 executor 重算（这里不臆造）。
    """
    return {
        "kind": "undo" if entry.get("kind") == "apply" else "redo",
        "undo_of_seq": entry.get("seq"),
        "idempotency_key": None,  # executor 分配
        "actor": None,            # executor 填（undo 的 actor 是发起者，非原提交者）
        "origin": None,
        "ref_id": entry.get("ref_id"),
        "deltas": invert_deltas(entry.get("deltas") or []),
        "schema_ops": invert_schema_ops(entry.get("schema_ops") or []),
        "op_summary": {
            "summary": f"inverse of seq={entry.get('seq')}",
            "ops": (entry.get("op_summary") or {}).get("ops") or [],
        },
        "fingerprint_before": entry.get("fingerprint_after"),
        "fingerprint_after": entry.get("fingerprint_before"),
        "content_revision_before": entry.get("content_revision_after"),
        "content_revision_after": entry.get("content_revision_before"),
        "created_at": None,
    }


def apply_entry_effects(
    fc: Dict[str, Any], entry: Dict[str, Any]
) -> Tuple[Dict[str, Any], List[str]]:
    """把 entry 的全部效果应用到 FeatureCollection（正向语义）。

    返回 (new_fc, applied_wfids)。信封（外层 dict 的其他键）保留。
    """
    features = fc.get("features") or []
    features, applied = apply_deltas_to_features(features, entry.get("deltas") or [])
    features, _ = apply_schema_ops_to_features(features, entry.get("schema_ops") or [])
    new_fc = {**fc, "features": features}
    return new_fc, applied
