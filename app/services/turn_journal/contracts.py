"""Turn journal contracts：账本行契约 + 载荷纪律（H04 / ADR-0216）。

``TurnEventRecord`` 是 envelope 事件行（``harness_kernel.models.PlanDecision``）
的 durable 投影形态：字段同名同义（kind/host/turn_id/note/detail/seq/
event_id/causal_id），另加 run_id / step_id / payload_ref / mutation_revision
等因果与凭证引用。**envelope 仍是 live 权威**——本记录只进账本，不参与
kernel 决策。

载荷纪律：``detail`` 入库前经 ``sanitize_detail`` 收敛到 ≤2KB canonical
JSON；超限截断并替换为 ``{"truncated": true, "keys": [...]}`` 摘要 +
``journal_detail_truncated`` 计数。大内容（GeoJSON/截图）永不入账——
调用方需要留痕时传 ``payload_ref``（ref + descriptor），journal 只存 ref。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Optional

from app.lib.runtime.clock import epoch_to_utc, utc_now

#: detail 入库上限（canonical JSON 字节数；与 spatial_events 载荷纪律同量级）。
MAX_DETAIL_BYTES = 2048
#: 单字符串字段入库上限（note 已在 envelope 侧截到 300）。
_MAX_STR = 300


@dataclass(frozen=True)
class TurnEventRecord:
    """一条 turn 事件的事实投影（append-only；幂等键 ``event_id``）。"""

    session_id: str
    kind: str
    turn_id: str = ""
    run_id: str = ""
    step_id: str = ""
    host: str = "unknown"
    seq: int = 0
    event_id: str = ""
    causal_id: str = ""
    note: str = ""
    detail: Dict[str, Any] = field(default_factory=dict)
    payload_ref: str = ""
    mutation_revision: Optional[int] = None
    #: 进程内 aware UTC；DB 边界由 ledger 转 naive-UTC（``to_db_utc``）。
    occurred_at: datetime = field(default_factory=utc_now)

    @staticmethod
    def from_epoch(
        session_id: str,
        kind: str,
        *,
        at_epoch: float,
        **kwargs: Any,
    ) -> "TurnEventRecord":
        """envelope 事件行（epoch float 时间戳）→ 账本记录。"""
        return TurnEventRecord(
            session_id=session_id, kind=kind,
            occurred_at=epoch_to_utc(at_epoch), **kwargs,
        )

    def to_row_kwargs(self) -> Dict[str, Any]:
        """→ ``TurnEventRow`` 构造参数（detail 已 sanitize；时间已截断）。"""
        from app.lib.runtime.clock import to_db_utc

        return {
            "event_id": self.event_id[:160],
            "session_id": self.session_id[:64],
            "turn_id": self.turn_id[:80],
            "run_id": self.run_id[:64],
            "step_id": self.step_id[:80],
            "kind": self.kind[:64],
            "host": self.host[:32],
            "seq": int(self.seq),
            "causal_id": self.causal_id[:160],
            "note": self.note[:300],
            "detail": sanitize_detail(self.detail),
            "payload_ref": self.payload_ref[:160] or None,
            "mutation_revision": self.mutation_revision,
            "occurred_at": to_db_utc(self.occurred_at),
            "status": "recorded",
        }


def _truncate_value(value: Any, budget: int) -> Any:
    """把单个值收敛进 budget 字节预算；超限返回 None（调用方剔除）。"""
    try:
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    except (TypeError, ValueError):
        return None
    if len(encoded.encode("utf-8")) <= budget:
        return value
    if isinstance(value, str):
        return value[: max(0, budget // 8)]
    if isinstance(value, dict):
        kept: Dict[str, Any] = {}
        used = 0
        for key in sorted(value.keys()):
            item = json.dumps(
                {key: value[key]}, ensure_ascii=False, default=str)
            cost = len(item.encode("utf-8"))
            if used + cost > budget:
                break
            kept[key] = value[key]
            used += cost
        return kept
    return None


def sanitize_detail(detail: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """载荷纪律执行点：detail ≤2KB canonical JSON，超限确定性截断。

    截断是**确定性**的（键排序贪心保留），同一条事件重复 sanitize 得到
    同一结果——重复 append 判重不受影响。截断发生时以
    ``{"truncated": true, "keys": [...]}`` 摘要替代原值，并保留未超限的
    小字段，方便诊断定位哪个键膨胀。
    """
    src = detail or {}
    try:
        size = len(json.dumps(
            src, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8"))
    except (TypeError, ValueError):
        return {"truncated": True, "keys": [], "reason": "unserializable"}
    if size <= MAX_DETAIL_BYTES:
        return dict(src)
    trimmed: Dict[str, Any] = {}
    for key, value in sorted(src.items()):
        small = _truncate_value(value, MAX_DETAIL_BYTES // 4)
        if small is not None:
            trimmed[key] = small
    trimmed["truncated"] = True
    trimmed["keys"] = sorted(str(k)[:64] for k in src.keys())[:32]
    return trimmed
