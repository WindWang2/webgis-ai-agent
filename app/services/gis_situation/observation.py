"""前端交互观察契约（方向 2 S4，ADR-0180）。

Harness 需要感知的轮间交互以**小型结构化观察**进入后端：viewport /
selection / layer focus / layer visibility / map rendered / gesture /
display mode / pending mutation。

纪律：
- **不允许每个 mousemove 写整包 map state**：payload 有界（≤512 字节
  规范化 JSON），服务端按 (kind, payload) 内容寻重去重；
- **revision-aware**：``client_generation`` 单调 —— 旧代观察拒收
  （``stale_generation``），sequence 严格递增；
- 有界环：``map_state["_situation_interactions"]`` 尾部保留最近
  MAX_INTERACTION_RING 条（编译器只投影尾部 MAX_INTERACTIONS 条）；
- 防抖在客户端（前端只上报已有节流事件），服务端去重是兜底而非替代。
"""
from __future__ import annotations

import hashlib
import json
import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_INTERACTIONS_KEY = "_situation_interactions"

#: 封闭词表：Harness 真正需要感知的交互种类（任务书 S4）。
KIND_VIEWPORT = "viewport_change"
KIND_SELECTION = "selection_change"
KIND_FOCUS = "layer_focus"
KIND_VISIBILITY = "layer_visibility"
KIND_RENDERED = "map_rendered"
KIND_GESTURE = "user_gesture"
KIND_DISPLAY_MODE = "display_mode"
KIND_PENDING = "pending_mutation"
ALLOWED_KINDS = frozenset({
    KIND_VIEWPORT, KIND_SELECTION, KIND_FOCUS, KIND_VISIBILITY,
    KIND_RENDERED, KIND_GESTURE, KIND_DISPLAY_MODE, KIND_PENDING,
})

MAX_INTERACTION_RING = 32
MAX_PAYLOAD_BYTES = 512
_MAX_KIND_CHARS = 32
_MAX_STR_VALUE_CHARS = 128
_MAX_NESTED_KEYS = 8

# 每类交互允许的 payload 键（规范化白名单：未知键丢弃 —— 与 #811 WS
# 快照白名单同纪律，客户端可写面不得携带任意结构）。
_PAYLOAD_SCHEMAS: Dict[str, frozenset] = {
    KIND_VIEWPORT: frozenset({"center", "zoom", "bearing", "pitch", "bounds"}),
    KIND_SELECTION: frozenset({"layer_id", "feature_id", "properties"}),
    KIND_FOCUS: frozenset({"layer_id"}),
    KIND_VISIBILITY: frozenset({"layer_id", "visible"}),
    KIND_RENDERED: frozenset({"mapspec_fingerprint", "client_generation", "map_idle"}),
    KIND_GESTURE: frozenset({"kind"}),
    KIND_DISPLAY_MODE: frozenset({"is_3d"}),
    KIND_PENDING: frozenset({"tool", "status"}),
}


class InteractionAck:
    """摄入裁决结果（可序列化，供 route 响应）。"""

    __slots__ = ("accepted", "reason", "sequence")

    def __init__(self, accepted: bool, reason: str, sequence: int) -> None:
        self.accepted = accepted
        self.reason = reason
        self.sequence = sequence

    def to_dict(self) -> Dict[str, Any]:
        return {
            "accepted": self.accepted,
            "reason": self.reason,
            "sequence": self.sequence,
        }


def _clip_str(value: Any) -> Any:
    if isinstance(value, str) and len(value) > _MAX_STR_VALUE_CHARS:
        return value[:_MAX_STR_VALUE_CHARS]
    return value


def normalize_payload(kind: str, payload: Any) -> Optional[Dict[str, Any]]:
    """白名单键规范化 + 有界化（键数、值长、总字节三重封顶）。

    非法（非 dict / 全未知键 / 超总字节预算）→ None（拒收）。
    """
    if not isinstance(payload, dict):
        return None
    allowed = _PAYLOAD_SCHEMAS.get(kind)
    if allowed is None:
        return None
    out: Dict[str, Any] = {}
    for key in sorted(payload):
        if key not in allowed:
            continue
        value = payload[key]
        if isinstance(value, dict):
            # 嵌套 dict（如 feature properties）：键数封顶，逐值有界。
            out[key] = {
                str(k)[:32]: _clip_str(v)
                for k, v in sorted(value.items())[:_MAX_NESTED_KEYS]
            }
        elif isinstance(value, list):
            out[key] = [_clip_str(v) for v in value[:8]]
        else:
            out[key] = _clip_str(value)
    # 键过多（全未知）→ 空载荷，按拒收处理。
    if not out:
        return None
    blob = json.dumps(out, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"))
    if len(blob.encode("utf-8")) > MAX_PAYLOAD_BYTES:
        return None
    return out


def _canonical_hash(kind: str, payload: Dict[str, Any]) -> str:
    blob = json.dumps(
        {"kind": kind, "payload": payload},
        ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()[:12]


def _as_int(value: Any) -> Optional[int]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return int(value)


async def record_interaction(
    session_id: str,
    kind: str,
    payload: Any,
    *,
    client_generation: Optional[int] = None,
    observed_at: str = "",
    store: Any = None,
) -> InteractionAck:
    """摄入一条交互观察（去重 + 单调 + 有界环）。绝不抛出给调用方。

    环是共享 Redis 状态（读-改-写 sequence）—— 与 pre-turn 观察写入同
    纪律，全程持 session 锁（situation review P1-4）；增值感知面用默认
    降级容忍（不 fail-closed 503）。
    """
    if store is None:
        from app.services.session_data import session_data_manager as store

    kind = str(kind or "")[:_MAX_KIND_CHARS]
    if kind not in ALLOWED_KINDS:
        return InteractionAck(False, "unknown_kind", 0)
    normalized = normalize_payload(kind, payload)
    if normalized is None:
        return InteractionAck(False, "empty_or_disallowed_payload", 0)
    gen = _as_int(client_generation)

    from app.services.distributed_lock import session_lock_registry

    try:
        async with session_lock_registry.lock(session_id) as _lock:
            return await _record_interaction_locked(
                session_id, kind, normalized, gen, observed_at, store,
            )
    except Exception as e:  # noqa: BLE001 — 摄入是增值感知面，绝不 500 主链路
        logger.warning("[gis_situation] interaction ingest failed: %s", e)
        return InteractionAck(False, "ingest_error", 0)


async def _record_interaction_locked(
    session_id: str,
    kind: str,
    normalized: Dict[str, Any],
    gen: Optional[int],
    observed_at: str,
    store: Any,
) -> InteractionAck:
    try:
        get_field = getattr(store, "get_state_field", None)
        ring = await get_field(session_id, _INTERACTIONS_KEY) if callable(
            get_field) else None
        ring = list(ring) if isinstance(ring, list) else []
        last: Dict[str, Any] = ring[-1] if ring and isinstance(ring[-1], dict) else {}

        # 内容寻重：同 kind 的**最近一条**载荷完全一致 → 重复帧丢弃
        # （不同 kind 交替不算重复）。
        if last.get("kind") == kind and last.get("payload_hash") == _canonical_hash(kind, normalized):
            return InteractionAck(False, "duplicate", _as_int(last.get("sequence")) or 0)
        # client_generation 单调（双方都在场才比较 —— 旧客户端无代数放行）。
        last_gen = _as_int(last.get("client_generation"))
        if gen is not None and last_gen is not None and gen <= last_gen:
            return InteractionAck(False, "stale_generation", _as_int(last.get("sequence")) or 0)

        sequence = (_as_int(last.get("sequence")) or 0) + 1
        entry: Dict[str, Any] = {
            "sequence": sequence,
            "kind": kind,
            "payload": normalized,
            "payload_hash": _canonical_hash(kind, normalized),
        }
        if gen is not None:
            entry["client_generation"] = gen
        if observed_at:
            entry["observed_at"] = str(observed_at)[:32]
        ring.append(entry)
        if len(ring) > MAX_INTERACTION_RING:
            ring = ring[-MAX_INTERACTION_RING:]
        persisted = await store.set_map_state(session_id, _INTERACTIONS_KEY, ring)
        if persisted is False:
            return InteractionAck(False, "persist_failed", sequence)
        return InteractionAck(True, "accepted", sequence)
    except Exception as e:  # noqa: BLE001 — 摄入是增值感知面，绝不 500 主链路
        logger.warning("[gis_situation] interaction ingest failed: %s", e)
        return InteractionAck(False, "ingest_error", 0)
