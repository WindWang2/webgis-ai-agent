"""Interaction IR V1 有界投影（Goal 02 / ADR-0224 F）。

把 ledger 环投影为 agent 可消费的**有界上下文快照**：

- 禁止把原始高频事件全塞 prompt —— 只投影语义状态（当前视口/选择/
  AOI/用户覆盖）+ 最近有意义交互（合并同 kind 连发）；
- 一切由序号驱动（server_seq = 服务端规范接收序）：**不读时钟**，投影
  是 ledger 列表的纯函数 → 回放确定性由构造保证（ADR-0224 D5）；
- 诚实性：mutation 被拒/superseded 的用户尝试进 ``attempted_not_applied``
  而不是 ``user_overrides``；verified 与 claimed_unverified 分级披露；
- 有界性：recent 窗 ≤ :data:`MAX_RECENT_INTERACTIONS` 条、overrides
  ≤ :data:`MAX_OVERRIDES`、attempted ≤ 8；溢出/淘汰走 ``omitted`` 披露。
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

from app.services.gis_harness.interaction_ir.contracts import (
    MUTATION_LINKED_KINDS,
    SCHEMA_VERSION,
)

MAX_RECENT_INTERACTIONS = 12
MAX_OVERRIDES = 16
MAX_ATTEMPTED = 8

_RECENT_COALESCED_KINDS = frozenset({"viewport_settled"})
_OVERRIDE_FIELDS = {
    "layer_visibility_changed": ("visible",),
    "layer_opacity_changed": ("opacity",),
    "layer_style_changed": ("attribute_names",),
    "layer_order_changed": ("from_index", "to_index"),
}


def _payload_view(record: Dict[str, Any]) -> Dict[str, Any]:
    payload = record.get("payload")
    return payload if isinstance(payload, dict) else {}


def _mutation_link(record: Dict[str, Any]) -> Dict[str, Any]:
    """causality 链视图（Agent 问"为什么保留这个图层"的证据入口）。"""
    link: Dict[str, Any] = {}
    if record.get("client_mutation_id"):
        link["client_mutation_id"] = record["client_mutation_id"]
        link["mutation_verified"] = bool(record.get("mutation_verified"))
        if record.get("verified_revision") is not None:
            link["verified_revision"] = record["verified_revision"]
        elif record.get("mutation_revision") is not None:
            link["claimed_revision"] = record["mutation_revision"]
        if record.get("mutation_outcome"):
            link["outcome"] = record["mutation_outcome"]
    for key in ("causation_id", "correlation_id", "turn_id"):
        if record.get(key):
            link[key] = record[key]
    return link


def project_interaction_snapshot(ledger_events: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    """ledger 记录 → 有界语义快照（纯函数，server_seq 顺序输入）。"""
    events = [rec for rec in ledger_events if isinstance(rec, dict)]

    viewport: Optional[Dict[str, Any]] = None
    selection: Optional[Dict[str, Any]] = None
    active_aoi: Optional[Dict[str, Any]] = None
    overrides: Dict[str, Dict[str, Any]] = {}
    override_order: List[str] = []
    attempted: List[Dict[str, Any]] = []

    for record in events:
        kind = str(record.get("kind") or "")
        payload = _payload_view(record)
        if kind == "viewport_settled":
            viewport = _viewport_view(record, payload)
        elif kind == "feature_selected":
            selection = _selection_view(record, payload)
        elif kind == "feature_deselected":
            selection = None
        elif kind == "aoi_drawn" or kind == "aoi_edited":
            active_aoi = _aoi_view(record, payload)
        elif kind == "aoi_deleted":
            active_aoi = None
        elif kind in _OVERRIDE_FIELDS:
            layer_id = str(payload.get("layer_id") or "")[:200]
            outcome = record.get("mutation_outcome")
            if layer_id and outcome == "committed":
                entry = overrides.get(layer_id, {"layer_id": layer_id})
                for field_name in _OVERRIDE_FIELDS[kind]:
                    if field_name in payload:
                        entry[field_name] = payload[field_name]
                entry["last_kind"] = kind
                entry["link"] = _mutation_link(record)
                if layer_id in overrides:
                    override_order.remove(layer_id)
                override_order.append(layer_id)
                overrides[layer_id] = entry
            elif layer_id and outcome in ("superseded", "failed"):
                attempted.append({
                    "kind": kind,
                    "layer_id": layer_id,
                    "outcome": outcome,
                    "link": _mutation_link(record),
                })
        elif kind == "layer_removed":
            layer_id = str(payload.get("layer_id") or "")[:200]
            if record.get("mutation_outcome") == "committed":
                overrides.pop(layer_id, None)
                if layer_id in override_order:
                    override_order.remove(layer_id)
            elif layer_id and record.get("mutation_outcome") in ("superseded", "failed"):
                attempted.append({
                    "kind": kind,
                    "layer_id": layer_id,
                    "outcome": str(record.get("mutation_outcome")),
                    "link": _mutation_link(record),
                })

    # 有界：overrides 超界按最旧丢弃（新近决策优先）。
    omitted_override = 0
    if len(override_order) > MAX_OVERRIDES:
        for stale_layer in override_order[:-MAX_OVERRIDES]:
            overrides.pop(stale_layer, None)
            omitted_override += 1
        override_order = override_order[-MAX_OVERRIDES:]

    recent = _recent_interactions(events)
    snapshot: Dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "viewport": viewport,
        "selection": selection,
        "active_aoi": active_aoi,
        "user_overrides": [overrides[layer] for layer in reversed(override_order)],
        "attempted_not_applied": attempted[-MAX_ATTEMPTED:],
        "recent_interactions": recent,
        "event_count": len(events),
    }
    omitted: List[Dict[str, Any]] = []
    if omitted_override:
        omitted.append({"reason": "override_cap", "count": omitted_override})
    if len(attempted) > MAX_ATTEMPTED:
        omitted.append({"reason": "attempted_cap", "count": len(attempted) - MAX_ATTEMPTED})
    if omitted:
        snapshot["omitted"] = omitted
    snapshot["token_estimate"] = _token_estimate(snapshot)
    return snapshot


def _viewport_view(record: Dict[str, Any], payload: Dict[str, Any]) -> Dict[str, Any]:
    view = {key: payload[key] for key in ("center", "zoom", "bearing", "pitch", "bounds")
            if key in payload}
    view["source"] = {
        "client_instance_id": record.get("client_instance_id"),
        "client_seq": record.get("client_seq"),
        "server_seq": record.get("server_seq"),
    }
    if record.get("viewport_seq") is not None:
        view["viewport_seq"] = record["viewport_seq"]
    return view


def _selection_view(record: Dict[str, Any], payload: Dict[str, Any]) -> Dict[str, Any]:
    view = {key: payload[key] for key in ("layer_id", "feature_id", "field_names", "geometry_digest", "dataset_ref")
            if key in payload}
    view["source"] = {
        "client_instance_id": record.get("client_instance_id"),
        "client_seq": record.get("client_seq"),
        "server_seq": record.get("server_seq"),
    }
    return view


def _aoi_view(record: Dict[str, Any], payload: Dict[str, Any]) -> Dict[str, Any]:
    view = {key: payload[key] for key in ("geometry_type", "vertex_count", "geometry_digest")
            if key in payload}
    view["source"] = {
        "client_instance_id": record.get("client_instance_id"),
        "client_seq": record.get("client_seq"),
        "server_seq": record.get("server_seq"),
    }
    return view


def _recent_interactions(events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """最近有意义交互（新 → 旧输出）。

    合并语义：窗口内同 kind 的 ``replace_pending`` 类（viewport/temporal）
    只保留最新一条 —— 500 次 pan/zoom 风暴投影为 1 条 settled 事实
    （ADR-0224 I 场景）。
    """
    recent: List[Dict[str, Any]] = []
    seen_coalesced: set = set()
    omitted_recent = 0
    for record in reversed(events):
        kind = str(record.get("kind") or "")
        if kind in _RECENT_COALESCED_KINDS:
            if kind in seen_coalesced:
                omitted_recent += 1
                continue
            seen_coalesced.add(kind)
        entry: Dict[str, Any] = {"kind": kind}
        payload = _payload_view(record)
        if payload.get("layer_id") is not None:
            entry["layer_id"] = payload["layer_id"]
        if record.get("client_instance_id"):
            entry["client_instance_id"] = record["client_instance_id"]
        if record.get("client_seq") is not None:
            entry["client_seq"] = record["client_seq"]
        if record.get("server_seq") is not None:
            entry["server_seq"] = record["server_seq"]
        if kind in MUTATION_LINKED_KINDS:
            link = _mutation_link(record)
            if link:
                entry["link"] = link
        recent.append(entry)
        if len(recent) >= MAX_RECENT_INTERACTIONS:
            break
    if omitted_recent and len(recent) >= MAX_RECENT_INTERACTIONS:
        # 只有因截断隐藏了合并项时才披露（避免常规合并虚报 omission）。
        recent[-1].setdefault("_omitted_coalesced", omitted_recent)
    return recent


def _token_estimate(snapshot: Dict[str, Any]) -> int:
    import json

    return max(1, len(json.dumps(snapshot, ensure_ascii=False, sort_keys=True)) // 4)


def interaction_snapshot_from_map_state(map_state: Dict[str, Any]) -> Dict[str, Any]:
    """map_state → 投影（compile_integration / GET 投影端点共用读面）。"""
    ledger = map_state.get("_interaction_ir_ledger")
    events = list(ledger) if isinstance(ledger, list) else []
    snapshot = project_interaction_snapshot(events)
    meta = map_state.get("_interaction_ir_meta")
    if isinstance(meta, dict) and meta.get("dropped"):
        snapshot["dropped_events"] = int(meta.get("dropped") or 0)
    return snapshot


async def interaction_snapshot_via_store(store: Any, session_id: str) -> Dict[str, Any]:
    """定向读（#1064 纪律）：只为投影读 ledger/meta 两键，不物化全量 map_state
    （重会话 mapspec 可达 MiB 级）。读失败 → 空投影（只读面 fail-open）。"""
    get_field = getattr(store, "get_state_field", None)
    if not callable(get_field):
        return project_interaction_snapshot([])
    try:
        ledger = await get_field(session_id, "_interaction_ir_ledger")
        meta = await get_field(session_id, "_interaction_ir_meta")
    except Exception:  # noqa: BLE001 — 只读诊断面
        return project_interaction_snapshot([])
    return interaction_snapshot_from_map_state({
        "_interaction_ir_ledger": ledger,
        "_interaction_ir_meta": meta if isinstance(meta, dict) else None,
    })
