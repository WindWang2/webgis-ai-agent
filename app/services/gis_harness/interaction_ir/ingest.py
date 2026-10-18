"""Interaction IR V1 摄入状态机（Goal 02 / ADR-0224 D4）。

核心纪律：**幂等/排序/去重/淘汰的全部判定是纯函数**
:func:`apply_event`（``LedgerState`` in-place fold）；生产接线
（:func:`ingest_interaction_batch`）与回放（replay.py / corpus）走同一条
代码路径 —— 回放确定性由构造保证，而非靠测试巧合。

分类词表（verdict × reason）：

- ``accepted``           —— 依序接受（client_seq == watermark+1）；
- ``accepted_with_gap``  —— 接受但 client_seq > watermark+1（披露 gap_from，
  丢失事件不假装见过；ADR-0224 G late/gap 语义）；
- ``duplicate``          —— event_id 已在环内（批内重复 / 短窗重发兜底；
  主去重是 per-instance 水印）；
- ``stale_seq``          —— client_seq ≤ 该实例水印（ack 丢失后的重试、
  多标签页各自独立实例互不干扰）；
- ``rejected``           —— actor 伪造 / kind 词表外 / payload 白名单外 /
  单条记录超预算。

mutation 链接（causality bridge，ADR-0224 E）：携带 ``client_mutation_id``
的事件在摄入时对 ``map_state["_mutation_dedup"]``（引擎 ``c:<id>`` 幂等
索引）做**只读交叉校验**：

- 命中 → 记录 ``mutation_verified=true`` + 权威 ``verified_revision``；
- 未命中（索引界 64 淘汰 / mutation 尚未落 / 被 superseded 不进索引）→
  ``mutation_verified=false``，客户端申报的结局保留但投影面按
  "claimed_unverified" 诚实呈现 —— **mutation 被拒绝绝不投影为成功**。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from app.services.gis_harness.interaction_ir.contracts import (
    MAX_LEDGER_BYTES,
    MAX_LEDGER_EVENTS,
    MAX_TRACKED_CLIENTS,
    SCHEMA_VERSION,
    LedgerState,
    canonical_event_blob,
    interaction_event_fingerprint,
    normalize_payload,
    spec_for,
)

logger = logging.getLogger(__name__)

LEDGER_KEY = "_interaction_ir_ledger"
CLIENTS_KEY = "_interaction_ir_clients"
META_KEY = "_interaction_ir_meta"
MUTATION_DEDUP_KEY = "_mutation_dedup"

VERDICT_ACCEPTED = "accepted"
VERDICT_ACCEPTED_WITH_GAP = "accepted_with_gap"
VERDICT_DUPLICATE = "duplicate"
VERDICT_STALE_SEQ = "stale_seq"
VERDICT_REJECTED = "rejected"

#: 单条 ledger 记录的序列化字节上限（规范化 payload ≤512B + 信封字段）。
_MAX_RECORD_BYTES = 1536


class EventVerdict:
    __slots__ = ("event_id", "client_seq", "verdict", "reason", "server_seq", "gap_from")

    def __init__(
        self,
        event_id: str,
        client_seq: int,
        verdict: str,
        reason: Optional[str] = None,
        server_seq: Optional[int] = None,
        gap_from: Optional[int] = None,
    ) -> None:
        self.event_id = event_id
        self.client_seq = client_seq
        self.verdict = verdict
        self.reason = reason
        self.server_seq = server_seq
        self.gap_from = gap_from

    def to_ack(self) -> Dict[str, Any]:
        ack: Dict[str, Any] = {
            "event_id": self.event_id,
            "client_seq": self.client_seq,
            "verdict": self.verdict,
        }
        if self.reason is not None:
            ack["reason"] = self.reason
        if self.server_seq is not None:
            ack["server_seq"] = self.server_seq
        if self.gap_from is not None:
            ack["gap_from"] = self.gap_from
        return ack


def _dedup_entry(mutation_dedup: Any, client_mutation_id: Optional[str]) -> Optional[Dict[str, Any]]:
    """引擎幂等索引只读查询（``c:<client_mutation_id>`` → 存证 revision）。"""
    if not client_mutation_id or not isinstance(mutation_dedup, dict):
        return None
    entry = mutation_dedup.get(f"c:{client_mutation_id}")
    return entry if isinstance(entry, dict) else None


def apply_event(
    state: LedgerState,
    raw: Dict[str, Any],
    *,
    mutation_dedup: Any = None,
) -> EventVerdict:
    """对 state 折叠一条原始事件（in-place），返回摄入裁决。

    纯确定性：不读时钟、不读随机源、不做 I/O。输入 ``raw`` 已通过
    ``InteractionEventV1`` 边界校验的路由会带全量字段；corpus 回放允许
    最小字段集（kind/event_id/client_* 必需）。
    """
    event_id = str(raw.get("event_id") or "")[:64]
    client_seq = _as_int(raw.get("client_seq")) or 0
    instance = str(raw.get("client_instance_id") or "")[:64]

    spec = spec_for(str(raw.get("kind") or ""))
    if spec is None:
        return EventVerdict(event_id, client_seq, VERDICT_REJECTED, "unknown_kind")
    # 客户端端点只放行 user（防伪造 agent/system 事件）。
    if str(raw.get("actor") or "user") != "user":
        return EventVerdict(event_id, client_seq, VERDICT_REJECTED, "actor_not_user")

    payload = normalize_payload(spec.kind, raw.get("payload"))
    if payload is None:
        return EventVerdict(event_id, client_seq, VERDICT_REJECTED, "invalid_payload")

    client_state = state.clients.get(instance)
    watermark = int(client_state["acked_seq"]) if client_state else 0

    if client_seq <= watermark:
        # 水印幂等：ack 丢失后的整批重试在此收敛（不重复入账）。
        return EventVerdict(event_id, client_seq, VERDICT_STALE_SEQ, "client_seq_already_acked")

    # 批内/短窗 event_id 兜底查重（主去重是上面的水印）。
    if any(rec.get("event_id") == event_id for rec in state.events):
        _bump_client(state, instance, client_seq, watermark)
        return EventVerdict(event_id, client_seq, VERDICT_DUPLICATE, "duplicate_event_id")

    # Review P2-1：gap 只对**已知实例**披露（watermark>0）。新实例（新 tab/
    # 复制 tab/刷新）的首个 seq 无从要求从 1 起 —— 披露 gap_from=1 是假信号。
    gap_from = watermark + 1 if watermark > 0 and client_seq > watermark + 1 else None
    state.server_seq += 1
    record: Dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "server_seq": state.server_seq,
        "event_id": event_id,
        "kind": spec.kind,
        "actor": "user",
        "client_instance_id": instance,
        "client_seq": client_seq,
        "payload": payload,
        "fingerprint": interaction_event_fingerprint(
            spec.kind, "user", instance, client_seq, payload,
            client_mutation_id=raw.get("client_mutation_id"),
        ),
    }
    for optional_key in (
        "causation_id", "correlation_id", "turn_id", "client_mutation_id",
        "mutation_outcome", "mapspec_fingerprint", "created_at",
    ):
        value = raw.get(optional_key)
        if value is not None:
            record[optional_key] = str(value)[:128]
    for int_key in (
        "expected_revision", "mutation_revision", "viewport_seq",
        "render_observation_seq",
    ):
        value = _as_int(raw.get(int_key))
        if value is not None:
            record[int_key] = value

    # causality bridge：对引擎幂等索引做只读交叉校验（不改 mutation 真相）。
    if record.get("client_mutation_id"):
        dedup_hit = _dedup_entry(mutation_dedup, record["client_mutation_id"])
        if dedup_hit is not None:
            record["mutation_verified"] = True
            verified_rev = _as_int(dedup_hit.get("revision"))
            if verified_rev is not None:
                record["verified_revision"] = verified_rev
        else:
            record["mutation_verified"] = False

    _bump_client(state, instance, client_seq, watermark)
    _append_bounded(state, record)

    if gap_from is not None:
        return EventVerdict(
            event_id, client_seq, VERDICT_ACCEPTED_WITH_GAP,
            server_seq=state.server_seq, gap_from=gap_from,
        )
    return EventVerdict(event_id, client_seq, VERDICT_ACCEPTED, server_seq=state.server_seq)


def _bump_client(
    state: LedgerState, instance: str, client_seq: int, watermark: int,
) -> None:
    if not instance:
        return
    entry = state.clients.get(instance)
    if entry is None:
        if len(state.clients) >= MAX_TRACKED_CLIENTS:
            # FIFO：淘汰最久未见实例（水印随之丢失 —— 新一代实例 id 重开，
            # 语义等价于新标签页）。
            oldest = min(state.clients, key=lambda k: state.clients[k].get("last_seen", 0))
            del state.clients[oldest]
        entry = {"acked_seq": 0, "last_seen": 0}
        state.clients[instance] = entry
    entry["acked_seq"] = max(int(entry.get("acked_seq", 0)), client_seq, watermark)
    entry["last_seen"] = state.server_seq + 1


def _append_bounded(state: LedgerState, record: Dict[str, Any]) -> None:
    record_bytes = len(canonical_event_blob(record).encode("utf-8"))
    if record_bytes > _MAX_RECORD_BYTES:
        # 规范化后仍超单条预算（理论上不可达 —— payload ≤512B）：诚实丢弃。
        state.dropped += 1
        return
    state.events.append(record)
    state.total_bytes += record_bytes
    evicted = 0
    while (
        len(state.events) > MAX_LEDGER_EVENTS
        or (state.total_bytes > MAX_LEDGER_BYTES and len(state.events) > 1)
    ):
        evicted += 1
        dropped_record = state.events.pop(0)
        state.total_bytes -= len(canonical_event_blob(dropped_record).encode("utf-8"))
    if evicted:
        state.dropped += evicted


def _as_int(value: Any) -> Optional[int]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return int(value)


def summarize_verdicts(verdicts: List[EventVerdict]) -> Dict[str, int]:
    accepted = duplicates = stale = rejected = 0
    for v in verdicts:
        if v.verdict in (VERDICT_ACCEPTED, VERDICT_ACCEPTED_WITH_GAP):
            accepted += 1
        elif v.verdict == VERDICT_DUPLICATE:
            duplicates += 1
        elif v.verdict == VERDICT_STALE_SEQ:
            stale += 1
        else:
            rejected += 1
    return {
        "accepted": accepted,
        "duplicates": duplicates,
        "stale": stale,
        "rejected": rejected,
    }


# ── 生产接线（store I/O 壳）──────────────────────────────────────────────


class InteractionLedgerReadError(RuntimeError):
    """ledger 读失败（存储故障，区别于缺键）。

    Review P2-4：读失败若折叠为空态，恢复序列（读失败→写成功）会把
    既有 ledger/水印/server_seq 整体覆写清零。fail-closed 抛出 → 路由
    503 背压，客户端幂等重试（不写共享状态）。
    """


async def load_ledger_state(store: Any, session_id: str) -> LedgerState:
    """从 map_state 定向读三个键，还原状态机（缺键 = 空态；读失败 = 抛出）。"""
    get_field = getattr(store, "get_state_field", None)
    if not callable(get_field):
        return LedgerState()

    async def _field(key: str) -> Any:
        # 缺键（None）与读失败（异常）必须区分：前者是合法空态，后者
        # fail-closed —— 空态覆写会清掉共享账面（P2-4）。
        return await get_field(session_id, key)

    try:
        events = await _field(LEDGER_KEY)
        clients = await _field(CLIENTS_KEY)
        meta = await _field(META_KEY)
    except Exception as exc:  # noqa: BLE001 — 统一转读失败信号
        logger.warning("[interaction_ir] state field read failed: %s", exc)
        raise InteractionLedgerReadError(str(exc)) from exc
    state = LedgerState(
        events=list(events) if isinstance(events, list) else [],
        clients=dict(clients) if isinstance(clients, dict) else {},
        server_seq=_as_int((meta or {}).get("server_seq")) if isinstance(meta, dict) else 0,
        dropped=_as_int((meta or {}).get("dropped")) if isinstance(meta, dict) else 0,
    )
    state.recalculate_bytes()
    # server_seq 与环尾不一致（手工清过环）时取 max，保证单调不减。
    tail = _as_int(state.events[-1].get("server_seq")) if state.events else 0
    state.server_seq = max(state.server_seq, tail or 0)
    return state


async def persist_ledger_state(store: Any, session_id: str, state: LedgerState) -> bool:
    """三键写回（调用方持 session 锁）。任一失败 → False（调用方按降级披露）。"""
    try:
        ok_ledger = await store.set_map_state(session_id, LEDGER_KEY, state.events)
        ok_clients = await store.set_map_state(session_id, CLIENTS_KEY, state.clients)
        ok_meta = await store.set_map_state(
            session_id, META_KEY,
            {"server_seq": state.server_seq, "dropped": state.dropped},
        )
        return not any(result is False for result in (ok_ledger, ok_clients, ok_meta))
    except Exception:  # noqa: BLE001 — 增值观察面降级
        logger.warning("[interaction_ir] state persist failed", exc_info=True)
        return False


async def load_mutation_dedup(store: Any, session_id: str) -> Any:
    """只读引擎幂等索引（causality bridge 校验源；失败 → None = 全部 unverified）。"""
    get_field = getattr(store, "get_state_field", None)
    if not callable(get_field):
        return None
    try:
        return await get_field(session_id, MUTATION_DEDUP_KEY)
    except Exception:  # noqa: BLE001 — 交叉校验是增值面
        logger.warning("[interaction_ir] mutation dedup read failed")
        return None


async def ingest_interaction_batch(
    store: Any,
    session_id: str,
    events: List[Any],
) -> Dict[str, Any]:
    """路由面入口：读态 → fold → 写回 → ack 信封。

    幂等语义由水印/查重保证：任何失败重放本批都收敛到同一账面。返回
    ``InteractionIngestResponse`` 兼容 dict。
    """
    state = await load_ledger_state(store, session_id)
    dedup = await load_mutation_dedup(store, session_id)
    verdicts = [
        apply_event(state, _raw_of(event), mutation_dedup=dedup)
        for event in events
    ]
    persisted = await persist_ledger_state(store, session_id, state)
    summary = summarize_verdicts(verdicts)
    response: Dict[str, Any] = {
        **summary,
        "acks": [v.to_ack() for v in verdicts],
    }
    if persisted:
        # Review P2-3：水印只在对账成功落库时携带 —— 降级时水印来自内存态，
        # 客户端据 2xx 弃队会丢事件；降级响应不带水印 + persist_degraded
        # 让客户端按暂态重试（幂等收敛）。
        response["watermarks"] = {
            instance: int(entry.get("acked_seq", 0))
            for instance, entry in state.clients.items()
        }
    if state.dropped:
        response["dropped"] = state.dropped
    if not persisted:
        # 诚实披露：分类已给，但持久化降级 —— 客户端对 accepted 重试是安全的
        # （重放将命中水印/查重收敛为 stale/duplicate，不双计）。
        response["persist_degraded"] = True
    return response


def _raw_of(event: Any) -> Dict[str, Any]:
    """InteractionEventV1（或 dict）→ 原始 dict（fold 统一入口）。"""
    if isinstance(event, dict):
        return event
    return event.model_dump(exclude_none=True)
