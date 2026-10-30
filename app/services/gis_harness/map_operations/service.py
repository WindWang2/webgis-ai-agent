"""Map Operation 服务 —— preview（零突变）/ commit（CAS 链）/ 回执环（M4/M9）。

执行语义（ADR-0278）：

- **presentation 组单事务**：visible/opacity 效果聚合为既有
  ``apply_gis_mutation_batch``（一次锁/一次 revision+1，user-wins 守卫
  逐 intent 锁内复检）。
- **其余 intent 顺序提交**：paint/symbol/move/viewport/component/remove
  逐效果走 ``apply_gis_mutation``，每步携带链上 revision（前一步的
  mutation_revision）作 CAS 基线 —— 任何一步 superseded 即停链（外部
  写者推进了 revision，剩余效果以 chain_aborted 披露，不盲目重试）。
  **不虚构强原子**：混合 intent 无引擎级原子包，局部成功如实披露
  （effect.state + reason_code），不自动补偿（补偿写会与 user-wins
  竞争；表现层效果彼此独立，回退即再提交一次 mutation，由下一轮
  agent/用户决策）。
- **幂等**：operation_id 操作级键（回执环重放返回存证）；底层
  mutation_id = ``op:<operation_id>:<slot>``（引擎 dedup 兜底）。
- **敏感效果**（remove_layer）agent 工具只产 pending 回执
  （confirmation_required），由 owner 经受控提交路由批准后 origin=user
  落盘（引擎对 user origin 强制 expected_revision —— 路由在批准时现读
  当前 revision）。

回执环：``_map_operation_receipts``（map_state 单键，有界 16，最新在前）
—— 跨刷新/中断后 agent 与前端都能对账「已完成动作不重复提交」。
"""
from __future__ import annotations

import logging
import math
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

from app.services.gis_harness.map_operations.contracts import (
    KIND_MOVE_LAYER,
    KIND_REMOVE_LAYER,
    KIND_SET_COMPONENT,
    KIND_SET_LAYER_OPACITY,
    KIND_SET_LAYER_PAINT,
    KIND_SET_LAYER_SYMBOL,
    KIND_SET_LAYER_VISIBILITY,
    KIND_SET_VIEWPORT,
    MAX_RECEIPT_RING,
    MOVE_POSITIONS,
    RECEIPTS_KEY,
    RC_APPLIED,
    RC_CHAIN_ABORTED,
    RC_CLARIFICATION_NEEDED,
    RC_CONFIRMATION_REQUIRED,
    RC_INTERNAL_ERROR,
    RC_LAYER_NOT_FOUND,
    RC_NOOP,
    RC_SUPERSEDED,
    RC_USER_WINS,
    RC_VALIDATION_REJECTED,
    SCHEMA_VERSION,
    SYMBOL_PROPERTIES,
    MapOperationEffect,
    MapOperationRequest,
    sensitive_request,
)
from app.services.gis_harness.map_operations.resolver import (
    ResolvedTarget,
    layer_layer_ids_for_order,
    resolve_effect_target,
)

logger = logging.getLogger(__name__)

#: symbol 属性 → (bucket, MapLibre 键)。circle-radius/line-width 是 paint；
#: text-size/icon-size 是 layout（MapLibre 规范，读改写必须落对桶）。
_SYMBOL_TARGET_KEYS: Dict[str, Tuple[str, str]] = {
    "point_size": ("paint", "circle-radius"),
    "line_width": ("paint", "line-width"),
    "label_size": ("layout", "text-size"),
    "icon_size": ("layout", "icon-size"),
}

_USER_WINS_MARKERS = ("用户手动设定", "Agent 不覆盖", "Agent 不得移除")

RECEIPT_COMMAND = "map_operation_receipt"


# ── 小工具 ──────────────────────────────────────────────────────────────


def _now_ts() -> float:
    return round(time.time(), 3)


def _spec_layers(spec: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not isinstance(spec, dict):
        return []
    return [lyr for lyr in (spec.get("layers") or []) if isinstance(lyr, dict)]


def _find_layer(spec: Optional[Dict[str, Any]], layer_id: str) -> Optional[Dict[str, Any]]:
    for lyr in _spec_layers(spec):
        if str(lyr.get("id") or "") == layer_id:
            return lyr
    return None


def _layer_visible(lyr: Dict[str, Any]) -> bool:
    layout = lyr.get("layout") if isinstance(lyr.get("layout"), dict) else {}
    if layout.get("visibility") == "none":
        return False
    return lyr.get("visible", True) is not False


def _layer_opacity(lyr: Dict[str, Any]) -> Optional[float]:
    paint = lyr.get("paint") if isinstance(lyr.get("paint"), dict) else {}
    val = paint.get("opacity")
    return float(val) if isinstance(val, (int, float)) else None


async def _read_revision(session_id: str) -> int:
    try:
        from app.services.session_data import session_data_manager

        state = await session_data_manager.get_map_state(session_id) or {}
        raw = state.get("_cartographic_mutation_revision", 0)
        return int(raw) if isinstance(raw, int) else 0
    except Exception:  # noqa: BLE001 — 读失败按 0（引擎 CAS 仍权威）
        return 0


async def _load_spec(session_id: str) -> Optional[Dict[str, Any]]:
    try:
        from app.services.mapspec.store import mapspec_store_instance

        return await mapspec_store_instance.get_mapspec(session_id) or None
    except Exception:  # noqa: BLE001 — spec 缺席 = 空地图
        return None


async def _fingerprint(spec: Optional[Dict[str, Any]]) -> str:
    if not spec:
        return ""
    try:
        from app.lib.cartography.quality_loop import cartographic_fingerprint

        return cartographic_fingerprint(spec)
    except Exception:  # noqa: BLE001
        return ""


def _bbox_view(bbox: List[float]) -> Dict[str, Any]:
    """bbox → center/zoom（与 map_view._commit_framed_view 同式）。"""
    west, south, east, north = (float(x) for x in bbox[:4])
    center = [(west + east) / 2.0, (south + north) / 2.0]
    span = max(east - west, north - south, 1e-9)
    zoom = max(1.0, min(18.0, math.log2(360.0 / span)))
    return {"center": center, "zoom": round(zoom, 4)}


def _viewport_after(
    spec: Optional[Dict[str, Any]], effect: MapOperationEffect
) -> Dict[str, Any]:
    view: Dict[str, Any] = {}
    if isinstance(spec, dict) and isinstance(spec.get("view"), dict):
        view = dict(spec["view"])
    if effect.bbox:
        view.update(_bbox_view(effect.bbox))
    if effect.center:
        view["center"] = [float(effect.center[0]), float(effect.center[1])]
    if effect.zoom is not None:
        view["zoom"] = float(effect.zoom)
    if effect.bearing is not None:
        view["bearing"] = float(effect.bearing)
    if effect.pitch is not None:
        view["pitch"] = float(effect.pitch)
    return view


# ── 效果 → intent 编译（含 before/after 投影）───────────────────────────


class _CompiledEffect:
    """解析 + 编译后的效果（服务内部值对象）。"""

    __slots__ = (
        "index", "effect", "resolution", "intent", "before", "after",
        "state", "reason_code", "note",
    )

    def __init__(self, index: int, effect: MapOperationEffect, resolution: ResolvedTarget):
        self.index = index
        self.effect = effect
        self.resolution = resolution
        self.intent: Optional[Any] = None
        self.before: Dict[str, Any] = {}
        self.after: Dict[str, Any] = {}
        self.state = "planned"
        self.reason_code = ""
        self.note = ""


async def _compile_effects(
    session_id: str,
    request: MapOperationRequest,
    spec: Optional[Dict[str, Any]],
) -> Tuple[List[_CompiledEffect], Optional[Dict[str, Any]]]:
    """解析 + 校验全部效果。返回 (compiled, clarification)。

    clarification 非 None 时整笔进入澄清态（不写任何状态）。
    """
    compiled: List[_CompiledEffect] = []
    pending_clarifications: List[Dict[str, Any]] = []

    for idx, effect in enumerate(request.effects):
        resolution = await resolve_effect_target(session_id, effect, spec)
        ce = _CompiledEffect(idx, effect, resolution)
        compiled.append(ce)

        if not resolution.ok:
            ce.state = "skipped"
            ce.reason_code = resolution.reason_code or RC_VALIDATION_REJECTED
            ce.note = resolution.note
            if resolution.reason_code == RC_CLARIFICATION_NEEDED:
                pending_clarifications.append({
                    "effect_index": idx,
                    "kind": effect.kind,
                    "target_kind": effect.target_kind,
                    "note": resolution.note,
                    "candidates": resolution.candidates,
                })
            continue

        try:
            _compile_single(ce, spec)
        except ValueError as exc:  # per-kind 语义校验失败
            ce.state = "skipped"
            ce.reason_code = RC_VALIDATION_REJECTED
            ce.note = str(exc)[:200]

    if pending_clarifications:
        first = pending_clarifications[0]
        question = first.get("note") or "目标不明确"
        candidates = first.get("candidates") or []
        clarification = {
            "question": f"请明确操作目标：{question}",
            "pending_effects": pending_clarifications,
        }
        if candidates:
            clarification["candidates"] = candidates
        return compiled, clarification
    return compiled, None


def _compile_single(ce: _CompiledEffect, spec: Optional[Dict[str, Any]]) -> None:
    """单效果编译为 intent + before/after 投影（纯函数；ValueError=拒）。"""
    from app.contracts.mapspec_intents import PatchLayerPresentationIntent, SetViewIntent
    from app.services.mapspec.intents import (
        PatchComponentIntent,
        PatchLayerStyleIntent,
        ReorderLayersIntent,
        RemoveLayerIntent,
        UpsertLayerIntent,
    )

    effect = ce.effect
    kind = effect.kind

    if kind == KIND_SET_LAYER_VISIBILITY:
        layer_id = ce.resolution.targets[0]
        lyr = _find_layer(spec, layer_id)
        if lyr is None:
            raise ValueError(f"图层 {layer_id!r} 不在 MapSpec")
        if effect.visible is None:
            raise ValueError("set_layer_visibility 需要 visible")
        before = _layer_visible(lyr)
        ce.before = {"visible": before}
        ce.after = {"visible": bool(effect.visible)}
        if before == bool(effect.visible):
            ce.state = "skipped"
            ce.reason_code = RC_NOOP
            ce.note = "目标已是请求状态"
            return
        ce.intent = PatchLayerPresentationIntent(layer_id=layer_id, visible=bool(effect.visible))

    elif kind == KIND_SET_LAYER_OPACITY:
        layer_id = ce.resolution.targets[0]
        lyr = _find_layer(spec, layer_id)
        if lyr is None:
            raise ValueError(f"图层 {layer_id!r} 不在 MapSpec")
        if effect.opacity is None:
            raise ValueError("set_layer_opacity 需要 opacity")
        before = _layer_opacity(lyr)
        ce.before = {"opacity": before}
        ce.after = {"opacity": float(effect.opacity)}
        if before is not None and abs(before - float(effect.opacity)) < 1e-9:
            ce.state = "skipped"
            ce.reason_code = RC_NOOP
            ce.note = "透明度已是请求值"
            return
        ce.intent = PatchLayerPresentationIntent(layer_id=layer_id, opacity=float(effect.opacity))

    elif kind == KIND_SET_LAYER_PAINT:
        layer_id = ce.resolution.targets[0]
        lyr = _find_layer(spec, layer_id)
        if lyr is None:
            raise ValueError(f"图层 {layer_id!r} 不在 MapSpec")
        paint = dict(effect.paint or {})
        if not paint:
            raise ValueError("set_layer_paint 需要 paint")
        cur_paint = lyr.get("paint") if isinstance(lyr.get("paint"), dict) else {}
        ce.before = {k: cur_paint.get(k) for k in paint}
        ce.after = dict(paint)
        if all(
            isinstance(cur_paint.get(k), (int, float))
            and abs(float(cur_paint[k]) - float(v)) < 1e-9
            for k, v in paint.items()
        ):
            ce.state = "skipped"
            ce.reason_code = RC_NOOP
            ce.note = "paint 已是请求值"
            return
        ce.intent = PatchLayerStyleIntent(layer_id=layer_id, paint=dict(paint))

    elif kind == KIND_SET_LAYER_SYMBOL:
        layer_id = ce.resolution.targets[0]
        lyr = _find_layer(spec, layer_id)
        if lyr is None:
            raise ValueError(f"图层 {layer_id!r} 不在 MapSpec")
        prop = effect.symbol_property or ""
        bucket_key = _SYMBOL_TARGET_KEYS.get(prop)
        if bucket_key is None:
            raise ValueError(
                f"symbol_property {prop!r} 不支持；词表 {sorted(SYMBOL_PROPERTIES)}"
            )
        bucket, paint_key = bucket_key
        cur_bucket = lyr.get(bucket) if isinstance(lyr.get(bucket), dict) else {}
        before_val = cur_bucket.get(paint_key)
        if effect.symbol_value is not None:
            after_val = float(effect.symbol_value)
        elif effect.symbol_scale is not None:
            base = float(before_val) if isinstance(before_val, (int, float)) and before_val else None
            if base is None:
                raise ValueError(
                    f"层 {layer_id} 的 {paint_key} 无现值，相对缩放需要 symbol_value 绝对值"
                )
            after_val = round(base * float(effect.symbol_scale), 4)
        else:
            raise ValueError("set_layer_symbol 需要 symbol_value 或 symbol_scale")
        if after_val < 0:
            raise ValueError("symbol 值不能为负")
        ce.before = {paint_key: before_val}
        ce.after = {paint_key: after_val}
        if isinstance(before_val, (int, float)) and abs(float(before_val) - after_val) < 1e-9:
            ce.state = "skipped"
            ce.reason_code = RC_NOOP
            ce.note = "符号尺寸已是请求值"
            return
        new_layer = dict(lyr)
        new_bucket = dict(cur_bucket)
        new_bucket[paint_key] = after_val
        new_layer[bucket] = new_bucket
        # 红线：symbol 读改写只触碰尺寸键，visible/opacity 保持原样
        # （presentation 面必须走守卫路径，不得经 upsert 洗白）。
        ce.intent = UpsertLayerIntent(layer=new_layer)

    elif kind == KIND_MOVE_LAYER:
        layer_id = ce.resolution.targets[0]
        order = layer_layer_ids_for_order(spec)
        if layer_id not in order:
            raise ValueError(f"图层 {layer_id!r} 不在 MapSpec")
        position = effect.position or "top"
        if position not in MOVE_POSITIONS:
            raise ValueError(f"position 词表 {MOVE_POSITIONS}")
        idx = order.index(layer_id)
        if position == "top":
            new_idx = 0
        elif position == "bottom":
            new_idx = len(order) - 1
        elif position == "up":
            new_idx = max(0, idx - 1)
        else:
            new_idx = min(len(order) - 1, idx + 1)
        new_order = list(order)
        new_order.pop(idx)
        new_order.insert(new_idx, layer_id)
        ce.before = {"index": idx, "position": position}
        ce.after = {"index": new_idx}
        if new_order == order:
            ce.state = "skipped"
            ce.reason_code = RC_NOOP
            ce.note = "层序已满足请求位置"
            return
        ce.intent = ReorderLayersIntent(layer_ids=new_order)

    elif kind == KIND_SET_VIEWPORT:
        if not (effect.bbox or effect.center or effect.zoom is not None
                or effect.bearing is not None or effect.pitch is not None):
            raise ValueError("set_viewport 需要 center/zoom/bbox 之一")
        current_view: Dict[str, Any] = {}
        if isinstance(spec, dict) and isinstance(spec.get("view"), dict):
            current_view = dict(spec["view"])
        after_view = _viewport_after(spec, effect)
        ce.before = {
            k: current_view.get(k)
            for k in ("center", "zoom", "bearing", "pitch") if k in after_view
        }
        ce.after = {k: after_view.get(k) for k in ce.before}
        if ce.before == ce.after:
            ce.state = "skipped"
            ce.reason_code = RC_NOOP
            ce.note = "视口已是请求状态"
            return
        kwargs: Dict[str, Any] = {}
        if "center" in ce.after and isinstance(ce.after["center"], list):
            kwargs["center"] = [float(x) for x in ce.after["center"]]
        if ce.after.get("zoom") is not None:
            kwargs["zoom"] = float(ce.after["zoom"])
        if ce.after.get("bearing") is not None:
            kwargs["bearing"] = float(ce.after["bearing"])
        if ce.after.get("pitch") is not None:
            kwargs["pitch"] = float(ce.after["pitch"])
        ce.intent = SetViewIntent(**kwargs)

    elif kind == KIND_SET_COMPONENT:
        comp_id = ce.resolution.targets[0]
        patch = dict(effect.component_patch or {})
        if not patch:
            raise ValueError("set_component 需要 component_patch")
        ce.before = {"patched_keys": sorted(patch)}
        ce.after = {"patched_keys": sorted(patch)}
        ce.intent = PatchComponentIntent(
            component_id=comp_id,
            position=patch.get("position"),
            placement=patch.get("placement"),
            enabled=patch.get("enabled"),
            variant=patch.get("variant"),
        )

    elif kind == KIND_REMOVE_LAYER:
        layer_id = ce.resolution.targets[0]
        if _find_layer(spec, layer_id) is None:
            raise ValueError(f"图层 {layer_id!r} 不在 MapSpec")
        lyr = _find_layer(spec, layer_id)
        ce.before = {"present": True, "visible": _layer_visible(lyr or {})}
        ce.after = {"present": False}
        ce.intent = RemoveLayerIntent(layer_id=layer_id)

    else:  # pragma: no cover — contracts 词表已闭合
        raise ValueError(f"未知效果 {kind}")


# ── 回执环 ──────────────────────────────────────────────────────────────


async def _read_ring(session_id: str) -> List[Dict[str, Any]]:
    try:
        from app.services.session_data import session_data_manager

        state = await session_data_manager.get_map_state(session_id) or {}
        ring = state.get(RECEIPTS_KEY)
        if isinstance(ring, list):
            return [r for r in ring if isinstance(r, dict)]
        return []
    except Exception:  # noqa: BLE001
        return []


async def _write_ring(session_id: str, ring: List[Dict[str, Any]]) -> None:
    try:
        from app.services.session_data import session_data_manager

        await session_data_manager.set_map_state(
            session_id, RECEIPTS_KEY, ring[:MAX_RECEIPT_RING])
    except Exception:  # noqa: BLE001 — 环写失败不阻断回执返回（下次重写）
        logger.warning("[map_operations] receipt ring write failed", exc_info=True)


def _slim_receipt(receipt: Dict[str, Any]) -> Dict[str, Any]:
    """回执的 LLM/前端投影（剥 mapspec 大对象）。"""
    slim = {k: v for k, v in receipt.items() if k not in ("mapspec", "commands")}
    return slim


def _receipt_commands(receipt: Dict[str, Any]) -> List[Dict[str, Any]]:
    commands: List[Dict[str, Any]] = [
        {"command": RECEIPT_COMMAND, "params": {"receipt": _slim_receipt(receipt)}},
    ]
    fly_to = (receipt.get("viewport_command") or {}).get("fly_to")
    if fly_to:
        commands.append({"command": "fly_to", "params": fly_to})
    return commands


def _effect_receipt_view(ce: _CompiledEffect) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "index": ce.index,
        "kind": ce.effect.kind,
        "target_kind": ce.effect.target_kind,
        "targets": ce.resolution.targets,
        "state": ce.state,
        "before": ce.before,
        "after": ce.after,
    }
    if ce.reason_code:
        out["reason_code"] = ce.reason_code
    if ce.note:
        out["note"] = ce.note[:160]
    return out


def _status_of(states: List[str], reason_codes: List[str], superseded: bool,
               clarification: bool, pending_confirm: bool) -> str:
    if clarification:
        return RC_CLARIFICATION_NEEDED
    if pending_confirm:
        return RC_CONFIRMATION_REQUIRED
    if superseded and "applied" not in states:
        return RC_SUPERSEDED
    if "failed" in states:
        return "partial"
    if "applied" in states:
        return "committed"
    # 无 applied：全部 skipped —— 区分「全是 noop」与「存在被拒项」
    if reason_codes and all(rc == RC_NOOP for rc in reason_codes):
        return "noop"
    return "rejected"


def _user_wins(result: Any) -> bool:
    msg = str(getattr(result, "error_msg", "") or "")
    if any(marker in msg for marker in _USER_WINS_MARKERS):
        return True
    locked = list(getattr(result, "locked_layer_ids", None) or [])
    return bool(locked)


# ── 公共 API ────────────────────────────────────────────────────────────


async def preview_map_operation(
    session_id: str, request: MapOperationRequest
) -> Dict[str, Any]:
    """零突变 dry-run：逐效果 before/after + 适用性 + 风险/确认要求。"""
    spec = await _load_spec(session_id)
    compiled, clarification = await _compile_effects(session_id, request, spec)
    base_revision = await _read_revision(session_id)
    fingerprint = await _fingerprint(spec)

    effects_view = [_effect_receipt_view(ce) for ce in compiled]
    return {
        "schema_version": SCHEMA_VERSION,
        "operation_id": request.operation_id,
        "mode": "preview",
        "status": "preview",
        "session_id": session_id,
        "base_revision": base_revision,
        "base_fingerprint": fingerprint,
        "effects": effects_view,
        "layer_count": len(_spec_layers(spec)),
        "untouched_layer_count": max(
            0, len(_spec_layers(spec)) - len({t for ce in compiled for t in ce.resolution.targets}),
        ),
        "sensitive": sensitive_request(request.effects),
        "requires_confirmation": sensitive_request(request.effects),
        **({"clarification": clarification} if clarification else {}),
        "created_at": _now_ts(),
    }


async def commit_map_operation(
    session_id: str,
    request: MapOperationRequest,
    *,
    origin: str = "agent",
    user_ack: bool = False,
) -> Dict[str, Any]:
    """提交操作（CAS 链）。返回 receipt（同时持久化进回执环）。"""
    from app.services.gis_world_state import (
        apply_gis_mutation,
        apply_gis_mutation_batch,
    )

    # 1) 幂等重放：同 operation_id 已有回执 → 原样返回（标记 replayed）。
    ring = await _read_ring(session_id)
    for existing in ring:
        if existing.get("operation_id") == request.operation_id:
            replay = dict(existing)
            replay["replayed"] = True
            return replay

    spec = await _load_spec(session_id)
    compiled, clarification = await _compile_effects(session_id, request, spec)
    base_revision = await _read_revision(session_id)

    receipt: Dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "operation_id": request.operation_id,
        "mode": "commit",
        "session_id": session_id,
        "actor": request.actor,
        "origin": origin,
        "base_revision": base_revision,
        "created_at": _now_ts(),
        "provenance": {
            "turn_id": request.turn_id,
            "note": (request.note or "")[:160] or None,
            "user_ack": bool(user_ack),
        },
    }

    # 2) 澄清态：不写任何状态。
    if clarification:
        receipt.update({
            "status": RC_CLARIFICATION_NEEDED,
            "clarification": clarification,
            "effects": [_effect_receipt_view(ce) for ce in compiled],
        })
        receipt["message"] = "操作目标不明确，未修改地图；请向用户澄清后重试。"
        await _persist_receipt(session_id, receipt)
        return receipt

    # 3) 敏感效果 + agent 未获 owner 批准 → pending（confirmation_required）。
    if sensitive_request(request.effects) and not user_ack and origin == "agent":
        receipt.update({
            "status": RC_CONFIRMATION_REQUIRED,
            "effects": [_effect_receipt_view(ce) for ce in compiled],
            "awaiting": "owner_commit",
            # 批准路由据此重建 intent（有界：effects ≤8 × 小载荷）
            "pending_effects_request": {
                "effects": [e.model_dump() for e in request.effects],
                "expected_revision": base_revision,
                "note": (request.note or "")[:160] or None,
            },
            "commit_url": (
                f"/api/v1/chat/sessions/{session_id}/map-operations/"
                f"{request.operation_id}/commit"
            ),
        })
        receipt["message"] = (
            "操作含不可逆效果（移除图层），需用户在预览卡上确认后提交；未做任何修改。"
        )
        await _persist_receipt(session_id, receipt)
        return receipt

    # 4) 执行：presentation 组单事务，其余顺序 CAS 链。
    chain_rev: Optional[int] = request.expected_revision
    superseded = False

    presentation_ces = [
        ce for ce in compiled
        if ce.intent is not None and ce.effect.kind in (KIND_SET_LAYER_VISIBILITY, KIND_SET_LAYER_OPACITY)
    ]
    if presentation_ces:
        intents = [ce.intent for ce in presentation_ces]
        try:
            batch = await apply_gis_mutation_batch(
                session_id,
                intents,
                origin=origin,  # type: ignore[arg-type]
                actor=request.actor,
                expected_revision=chain_rev,
                mutation_id=f"op:{request.operation_id}:pres",
                turn_id=request.turn_id,
                reason="map_operation",
            )
            chain_rev = int(batch.mutation_revision or 0) or chain_rev
            outcome_by_layer = {o.layer_id: o for o in batch.outcomes}
            for ce in presentation_ces:
                outcome = outcome_by_layer.get(ce.resolution.targets[0])
                if batch.superseded:
                    ce.state, ce.reason_code = "planned", RC_SUPERSEDED
                elif outcome is None:
                    ce.state, ce.reason_code = "applied", RC_APPLIED
                elif outcome.status == "applied":
                    ce.state, ce.reason_code = "applied", RC_APPLIED
                elif outcome.status == "refused":
                    ce.state, ce.reason_code = "skipped", RC_USER_WINS
                    ce.note = (outcome.error_msg or "")[:160]
                else:  # not_found
                    ce.state, ce.reason_code = "skipped", RC_LAYER_NOT_FOUND
            if batch.superseded:
                superseded = True
        except Exception as exc:  # noqa: BLE001 — batch 层异常如实入回执
            for ce in presentation_ces:
                ce.state, ce.reason_code = "failed", RC_INTERNAL_ERROR
                ce.note = str(exc)[:160]

    if not superseded:
        for ce in compiled:
            if ce.intent is None or ce.state in ("applied", "skipped"):
                continue
            try:
                result = await apply_gis_mutation(
                    session_id,
                    ce.intent,
                    origin=origin,  # type: ignore[arg-type]
                    actor=request.actor,
                    expected_revision=chain_rev,
                    mutation_id=f"op:{request.operation_id}:e{ce.index}",
                    turn_id=request.turn_id,
                    reason="map_operation",
                )
            except Exception as exc:  # noqa: BLE001 — 引擎异常如实入回执
                ce.state, ce.reason_code = "failed", RC_INTERNAL_ERROR
                ce.note = str(exc)[:160]
                continue
            if result.superseded:
                ce.state, ce.reason_code = "planned", RC_SUPERSEDED
                ce.note = (
                    f"revision 已被推进到 {result.mutation_revision}；"
                    "本效果及后续未尝试（请基于新状态重发）。"
                )
                superseded = True
                break
            chain_rev = int(result.mutation_revision or 0) or chain_rev
            if result.is_error:
                if _user_wins(result):
                    ce.state, ce.reason_code = "skipped", RC_USER_WINS
                else:
                    ce.state, ce.reason_code = "failed", RC_VALIDATION_REJECTED
                ce.note = (result.error_msg or "")[:160]
                continue
            if result.duplicate:
                ce.state, ce.reason_code = "applied", RC_APPLIED
                ce.note = "幂等重放（mutation_id 已存证）"
                continue
            ce.state, ce.reason_code = "applied", RC_APPLIED

    # superseded 后剩余 planned 效果标记 chain_aborted
    if superseded:
        for ce in compiled:
            if ce.state == "planned" and not ce.reason_code:
                ce.state, ce.reason_code = "planned", RC_CHAIN_ABORTED

    states = [ce.state for ce in compiled]
    reason_codes = [ce.reason_code for ce in compiled if ce.state == "skipped"]
    before_layer_count = len(_spec_layers(spec))
    applied_targets = {
        t for ce in compiled if ce.state == "applied" for t in ce.resolution.targets
    }
    final_spec = await _load_spec(session_id)
    final_revision = await _read_revision(session_id)
    final_fp = await _fingerprint(final_spec)

    receipt.update({
        "status": _status_of(
            states, reason_codes, superseded,
            clarification=False, pending_confirm=False,
        ),
        "final_revision": final_revision,
        "final_fingerprint": final_fp,
        "effects": [_effect_receipt_view(ce) for ce in compiled],
        "applied_count": states.count("applied"),
        "skipped_count": states.count("skipped"),
        "failed_count": states.count("failed"),
        "untouched_layer_count": max(0, before_layer_count - len(applied_targets)),
    })
    if final_spec is not None:
        receipt["mapspec"] = final_spec
        receipt["mapspec_fingerprint"] = final_fp
        receipt["mutation_revision"] = final_revision

    # 视口效果已应用 → fly_to 命令（相机仲裁在前端 user-wins）
    for ce in compiled:
        if ce.effect.kind == KIND_SET_VIEWPORT and ce.state == "applied":
            view = (final_spec or {}).get("view") if isinstance(final_spec, dict) else None
            if isinstance(view, dict) and view.get("center"):
                fly_params: Dict[str, Any] = {"center": view.get("center")}
                if view.get("zoom") is not None:
                    fly_params["zoom"] = view.get("zoom")
                if view.get("bearing") is not None:
                    fly_params["bearing"] = view.get("bearing")
                if view.get("pitch") is not None:
                    fly_params["pitch"] = view.get("pitch")
                receipt["viewport_command"] = {"fly_to": fly_params}
            break

    receipt["message"] = _receipt_message(receipt)
    receipt["commands"] = _receipt_commands(receipt)
    await _persist_receipt(session_id, receipt)
    return receipt


async def _persist_receipt(session_id: str, receipt: Dict[str, Any]) -> None:
    ring = await _read_ring(session_id)
    ring = [r for r in ring if r.get("operation_id") != receipt.get("operation_id")]
    stored = _slim_receipt(receipt)
    if receipt.get("status") == RC_CONFIRMATION_REQUIRED:
        # pending 确认的操作保留 effects 编译结果，批准路由据此重建 intent
        stored["pending_effects_request"] = receipt.get("pending_effects_request")
    ring.insert(0, stored)
    await _write_ring(session_id, ring)


def _receipt_message(receipt: Dict[str, Any]) -> str:
    status = receipt.get("status")
    applied = receipt.get("applied_count", 0)
    skipped = receipt.get("skipped_count", 0)
    failed = receipt.get("failed_count", 0)
    if status == "committed":
        return f"地图操作已提交：{applied} 项生效" + (
            f"，{skipped} 项保留用户决策" if skipped else ""
        )
    if status == "partial":
        return (
            f"地图操作部分完成：{applied} 项生效、{failed} 项失败、"
            f"{skipped} 项跳过；失败项见 effects[].reason_code。"
        )
    if status == RC_SUPERSEDED:
        return "操作被并发修改取代（revision 已前进），未完成的效果未尝试；请基于当前地图重发。"
    if status == "rejected":
        return "操作未执行：全部效果被拒绝（目标缺失/校验失败/用户决策保留），详见 effects[].reason_code。"
    if status == "noop":
        return "地图已处于请求状态，未产生修改。"
    return str(status)


async def get_operation_receipt(
    session_id: str, operation_id: str
) -> Optional[Dict[str, Any]]:
    ring = await _read_ring(session_id)
    for receipt in ring:
        if receipt.get("operation_id") == operation_id:
            return receipt
    return None


async def list_operation_receipts(session_id: str, limit: int = 8) -> List[Dict[str, Any]]:
    ring = await _read_ring(session_id)
    return ring[: max(1, min(limit, MAX_RECEIPT_RING))]


async def drop_receipt(session_id: str, operation_id: str) -> bool:
    """从回执环移除一条记录（owner 批准 pending 操作前的预处理）。

    幂等重放按 operation_id 命中环内既有回执 —— pending(confirmation_
    required) 的批准路径必须先移除旧条目，否则重提交会被误判为重放。
    """
    ring = await _read_ring(session_id)
    remaining = [r for r in ring if r.get("operation_id") != operation_id]
    if len(remaining) == len(ring):
        return False
    await _write_ring(session_id, remaining)
    return True


def mint_operation_id() -> str:
    return f"mop-{uuid.uuid4().hex[:20]}"


__all__ = [
    "preview_map_operation",
    "commit_map_operation",
    "get_operation_receipt",
    "list_operation_receipts",
    "mint_operation_id",
    "RECEIPT_COMMAND",
]
