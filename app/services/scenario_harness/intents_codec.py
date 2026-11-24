"""候选 delta ↔ MapSpec MutationIntent 编解码（G05 共享契约，主 Agent 冻结）。

- 白名单词表：只有**幂等可重放、目标有界**的意图可进候选 delta
  （样式/组件/布局/图层 upsert/数据源 upsert/时态/推演视图）。
  全局/回滚/自愈类意图（Rollback/UndoAgentCommit/ApplyVisualHealPatch/
  InitProject…）不可序列化为候选 —— 采纳语义不明确，拒绝提案。
- payload 红线：单条 ≤MAX_DELTA_ITEM_BYTES，整笔 ≤MAX_DELTA_TOTAL_BYTES；
  超限整笔拒绝（caller 转结构化错误），绝不静默截断 —— 大数据留在
  会话 refs/数据面，候选只存最小可重放载荷。
- 字段闭包：``intent_field_closure`` 确定性推导 (target_id, field_path)
  列表，供锁定检查与三方合并冲突检测消费（WP1/WP4）。
"""

from __future__ import annotations

import json
from dataclasses import fields as dataclass_fields, is_dataclass
from typing import Any, Dict, List, Tuple

from app.services.scenario_harness.contracts import (
    MAX_DELTA_ITEM_BYTES,
    MAX_DELTA_TOTAL_BYTES,
    DeltaGroup,
    DeltaIntent,
    group_of_kind,
)

#: 可提议意图白名单（kind → (模块内类名, 目标字段, 分组)）。
SUPPORTED_KINDS: Tuple[str, ...] = (
    "UpsertLayerIntent",
    "UpsertSourceIntent",
    "PatchLayerStyleIntent",
    "PatchLayerLabelAuthoringIntent",
    "PatchComponentIntent",
    "RemoveComponentIntent",
    "SetLayoutIntent",
    "SetBasemapIntent",
    "SetTimeIntent",
    "SetTemporalViewIntent",
    "SetScenarioModeIntent",
)

#: 布尔键 → 目标提取（None = 全局意图无 target）。
_TARGET_FIELD = {
    "UpsertLayerIntent": "layer.id",
    "UpsertSourceIntent": "source_id",
    "PatchLayerStyleIntent": "layer_id",
    "PatchLayerLabelAuthoringIntent": "layer_id",
    "PatchComponentIntent": "component_id",
    "RemoveComponentIntent": "component_id",
}


class DeltaCodecError(Exception):
    """机器可读编码错误（提案面结构化拒绝）。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _payload_bytes(payload: Dict[str, Any]) -> int:
    try:
        return len(
            json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            ).encode("utf-8")
        )
    except (TypeError, ValueError):
        raise DeltaCodecError("payload_not_serializable", "干预载荷不可 JSON 序列化")


def _extract_target(kind: str, payload: Dict[str, Any]) -> str:
    spec = _TARGET_FIELD.get(kind)
    if not spec:
        return ""
    if spec == "layer.id":
        layer = payload.get("layer")
        if isinstance(layer, dict):
            return str(layer.get("id") or "")
        return ""
    return str(payload.get(spec) or "")


def _field_paths_of(kind: str, payload: Dict[str, Any]) -> List[str]:
    """确定性字段闭包（top-level 键粒度；样式 patch 到 paint 键）。"""
    if kind == "PatchLayerStyleIntent":
        paint = payload.get("paint")
        if isinstance(paint, dict) and paint:
            return sorted(f"paint.{k}" for k in paint)
        return ["paint"]
    if kind == "UpsertLayerIntent":
        layer = payload.get("layer")
        if isinstance(layer, dict):
            return sorted(f"layer.{k}" for k in layer.keys() if k != "id") or ["layer"]
        return ["layer"]
    if kind == "UpsertSourceIntent":
        return ["source"]
    if kind in ("PatchLayerLabelAuthoringIntent",):
        keys = [k for k in ("ops", "labels_config") if payload.get(k)]
        return keys or ["labels"]
    if kind in ("PatchComponentIntent", "RemoveComponentIntent"):
        return ["component"]
    if kind == "SetLayoutIntent":
        keys = [
            k
            for k in (
                "legend",
                "controls",
                "margins",
                "components",
                "component_links",
                "composition",
            )
            if payload.get(k) is not None
        ]
        return keys or ["layout"]
    if kind == "SetBasemapIntent":
        return ["basemap"]
    if kind in ("SetTimeIntent", "SetTemporalViewIntent"):
        return ["temporal"]
    if kind == "SetScenarioModeIntent":
        return ["scenario_mode"]
    return [""]


def serialize_intent(intent: Any, *, seq: int, summary: str = "") -> DeltaIntent:
    """MutationIntent 实例 → DeltaIntent（白名单校验 + 容量红线）。"""
    kind = type(intent).__name__
    if kind not in SUPPORTED_KINDS:
        raise DeltaCodecError(
            "intent_kind_unsupported",
            f"意图 {kind} 不可作为候选干预（白名单: {', '.join(SUPPORTED_KINDS)}）",
        )
    if not is_dataclass(intent):
        raise DeltaCodecError("intent_not_dataclass", f"意图 {kind} 不是 dataclass")
    payload: Dict[str, Any] = {}
    for f in dataclass_fields(intent):  # type: ignore[arg-type]
        value = getattr(intent, f.name)
        if value is None:
            continue
        payload[f.name] = value
    if not payload:
        raise DeltaCodecError("intent_empty", f"意图 {kind} 无任何字段（空干预）")
    item_bytes = _payload_bytes(payload)
    if item_bytes > MAX_DELTA_ITEM_BYTES:
        raise DeltaCodecError(
            "delta_item_too_large",
            f"干预载荷 {item_bytes}B 超过单条上限 {MAX_DELTA_ITEM_BYTES}B"
            "（大数据应留在会话 refs/数据面，候选只存最小可重放载荷）",
        )
    group: DeltaGroup = group_of_kind(kind)
    return DeltaIntent(
        seq=seq,
        kind=kind,
        group=group,
        target_id=_extract_target(kind, payload),
        field_paths=_field_paths_of(kind, payload),
        summary=(summary or _default_summary(kind, payload))[:160],
        payload=json.loads(json.dumps(payload, ensure_ascii=False, default=str)),
    )


def serialize_intents(intents: List[Any]) -> List[DeltaIntent]:
    """整笔序列化（seq 1 起单调；总量红线）。"""
    out: List[DeltaIntent] = []
    total = 0
    for i, intent in enumerate(intents or [], start=1):
        delta = serialize_intent(intent, seq=i)
        total += _payload_bytes(delta.payload)
        if total > MAX_DELTA_TOTAL_BYTES:
            raise DeltaCodecError(
                "delta_total_too_large",
                f"干预总载荷超过 {MAX_DELTA_TOTAL_BYTES}B 上限（拆分提案或精简载荷）",
            )
        out.append(delta)
    if not out:
        raise DeltaCodecError("delta_empty", "候选至少需要一条干预")
    return out


def deserialize_intent(delta: DeltaIntent) -> Any:
    """DeltaIntent → MutationIntent 实例（白名单外拒绝）。"""
    kind = delta.kind
    if kind not in SUPPORTED_KINDS:
        raise DeltaCodecError(
            "intent_kind_unsupported",
            f"delta.kind={kind!r} 不在可重放白名单内",
        )
    from app.services.mapspec import intents as _intents

    cls = getattr(_intents, kind, None)
    if cls is None or not is_dataclass(cls):
        raise DeltaCodecError(
            "intent_class_missing", f"意图类 {kind} 缺失或非 dataclass"
        )
    valid_names = {f.name for f in dataclass_fields(cls)}
    unknown = set(delta.payload) - valid_names
    if unknown:
        raise DeltaCodecError(
            "payload_fields_unknown",
            f"delta {delta.seq} 载荷字段 {sorted(unknown)} 不属于 {kind}",
        )
    try:
        return cls(**delta.payload)
    except TypeError as exc:
        raise DeltaCodecError(
            "payload_fields_invalid", f"delta {delta.seq} 载荷与 {kind} 签名不符: {exc}"
        )


def deserialize_intents(deltas: List[DeltaIntent]) -> List[Any]:
    return [deserialize_intent(d) for d in deltas or []]


def intent_field_closure(delta: DeltaIntent) -> List[Tuple[str, str, str]]:
    """→ [(kind, target_id, field_path)]（锁定检查/合并冲突检测消费）。"""
    return [
        (delta.kind, delta.target_id, fp)
        for fp in _field_paths_of(delta.kind, delta.payload)
    ]


def _default_summary(kind: str, payload: Dict[str, Any]) -> str:
    target = _extract_target(kind, payload)
    fps = _field_paths_of(kind, payload)
    return f"{kind}({target or 'global'}): {','.join(fps[:4])}"


__all__ = [
    "SUPPORTED_KINDS",
    "DeltaCodecError",
    "deserialize_intent",
    "deserialize_intents",
    "intent_field_closure",
    "serialize_intent",
    "serialize_intents",
]
