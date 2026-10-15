"""UI MapSpec mutation → Requirement 语义投影（ADR-0219 C4）。

**只信服务端已提交 receipt**：挂点在 ``gis_world_state/mutation`` 门面的
post-commit 通知平面（provenance/collab 事件同段），输入是引擎事务的
成功回执（MapSpecResult）——前端 optimistic state 永不进入本模块。

门控（全部满足才投影）：
1. ``origin == "user"``（服务端权威归因；agent/system/repair 一律不投影）；
2. taxonomy 判定 requirement-significant 或 undo_rebaseline；
3. requirement 文档存在且未超替（不复活旧文档、不为此建文档）。

写面：``requirement_ir.service.apply_user_patches``（actor=user；
op_id 从 mutation_id + 语义路径确定性派生 → duplicate mutation 幂等；
hidden/locked 写**绝对值集**（从提交后权威 spec 派生）→ 并发自愈）。

undo/redo（RollbackIntent / RestoreStyleIntent，user origin）：按提交后
spec **重定基**——hidden = 用户拥有且 expected_visible=False 的层族；
locked = workbench 锁集。撤销不留幽灵锁。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from app.services.gis_harness.representation_binding.conflicts import resolve_conflict
from app.services.gis_harness.representation_binding.flags import (
    requirement_projection_enabled,
)
from app.services.gis_harness.representation_binding.taxonomy import (
    classify_mutation,
)
from app.services.gis_harness.requirement_ir import service as requirement_service
from app.services.session_data import session_data_manager

logger = logging.getLogger(__name__)

_FAMILY_SEPS = ("-", "__")
_MAX_PROJECTED_IDS = 32


def _turn_of(turn_id: Optional[str]) -> int:
    """envelope.turn_id（str）→ IR turn（int；无数字信息时 0，诚实降级）。"""
    if not turn_id:
        return 0
    digits = "".join(ch for ch in turn_id if ch.isdigit())
    try:
        return int(digits) if digits else 0
    except ValueError:
        return 0


def _layer_base_id(layer_id: str) -> str:
    for sep in _FAMILY_SEPS:
        pos = layer_id.find(sep)
        if pos > 0:
            return layer_id[:pos]
    return layer_id


def _user_hidden_ids(mapspec: Optional[Dict[str, Any]]) -> List[str]:
    """提交后权威 spec 的用户隐藏集（presentation_owner=user 且
    expected_visible=False 的层族基 id；确定性、去重、有界）。"""
    if not isinstance(mapspec, dict):
        return []
    hidden = set()
    for layer in mapspec.get("layers") or []:
        if not isinstance(layer, dict):
            continue
        intent = layer.get("cartographic_intent")
        if not isinstance(intent, dict):
            continue
        if intent.get("presentation_owner") != "user":
            continue
        if intent.get("expected_visible") is False:
            layer_id = layer.get("id")
            if isinstance(layer_id, str) and layer_id:
                hidden.add(_layer_base_id(layer_id))
    return sorted(hidden)[:_MAX_PROJECTED_IDS]


def _spec_layer_exists(mapspec: Optional[Dict[str, Any]], layer_id: str) -> bool:
    if not isinstance(mapspec, dict):
        return False
    for layer in mapspec.get("layers") or []:
        if isinstance(layer, dict) and layer.get("id") == layer_id:
            return True
    return False


def _component_type_of(component_id: str) -> Optional[str]:
    """组件实例 id → IR 组件面名（只认 IR ``components.*`` 白名单面；
    id 与类型同名或族基命中时直配，无法确定 → None（不投影，绝不猜）。"""
    known = {"legend", "title", "scale_bar", "north_arrow", "labels", "attribution"}
    if component_id in known:
        return component_id
    base = _layer_base_id(component_id)
    if base in known and base != component_id:
        return base
    return None


def build_projection_patches(
    intent: Any,
    *,
    origin: str,
    actor: Optional[str],
    producer_class: Optional[str],
    mutation_id: str,
    turn: int,
    result_mapspec: Optional[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """一笔已提交 user mutation → TurnPatch 载荷列表（纯函数）。

    返回 ``[]`` 表示不投影（taxonomy 门控拒绝）；调用方把每项转
    ``TurnPatch``（actor=user）。reason 携带 mutation_id（稳定 receipt，
    不靠自由文本归因）。
    """
    kind = type(intent).__name__
    visible = getattr(intent, "visible", None)
    taxonomy = classify_mutation(
        kind, origin, actor=actor, producer_class=producer_class,
        visible_only_opacity=(kind == "PatchLayerPresentationIntent"
                              and visible is None),
    )
    if not taxonomy.projects_to_requirement:
        return []

    reason = f"mapspec mutation {mutation_id} ({taxonomy.dimension})"
    patches: List[Dict[str, Any]] = []

    # ── undo 重定基：hidden/locked 按提交后权威 spec 重算（绝对值集） ──
    if taxonomy.dimension == "undo_rebaseline":
        from app.contracts.workbench_locks import locked_layer_ids_of

        patches.append({
            "op": "set", "path": "representation.hidden_layer_ids",
            "value": _user_hidden_ids(result_mapspec),
            "reason": f"undo rebaseline: {reason}",
            "op_id": f"rbp:{mutation_id}:rebase:hidden",
            "turn": turn,
        })
        patches.append({
            "op": "set", "path": "representation.locked_layer_ids",
            "value": sorted(set(locked_layer_ids_of(result_mapspec)))[:_MAX_PROJECTED_IDS],
            "reason": f"undo rebaseline: {reason}",
            "op_id": f"rbp:{mutation_id}:rebase:locked",
            "turn": turn,
        })
        return patches

    if kind == "PatchLayerPresentationIntent":
        layer_id = str(getattr(intent, "layer_id", "") or "")
        if not layer_id:
            return []
        # 绝对值集语义：以提交后 spec 的用户隐藏集为准（并发/族成员一并收敛）。
        patches.append({
            "op": "set", "path": "representation.hidden_layer_ids",
            "value": _user_hidden_ids(result_mapspec),
            "reason": reason,
            "op_id": f"rbp:{mutation_id}:hidden",
            "turn": turn,
        })
        return patches

    if kind in ("SetWorkbenchStateIntent", "PatchWorkbenchDeltaIntent"):
        from app.contracts.workbench_locks import locked_layer_ids_of

        patches.append({
            "op": "set", "path": "representation.locked_layer_ids",
            "value": sorted(set(locked_layer_ids_of(result_mapspec)))[:_MAX_PROJECTED_IDS],
            "reason": reason,
            "op_id": f"rbp:{mutation_id}:locked",
            "turn": turn,
        })
        return patches

    if kind == "RemoveLayerIntent":
        # 删除后 spec 不含被删层——从 post-commit spec 派生的绝对值集天然
        # 清掉 ghost 成员，同时保留存活家族成员的用户语义（review P1#2：
        # 旧实现对照删除后 spec 判 membership，双向失效）。
        from app.contracts.workbench_locks import locked_layer_ids_of

        patches.append({
            "op": "set", "path": "representation.hidden_layer_ids",
            "value": _user_hidden_ids(result_mapspec),
            "reason": f"layer removed: {reason}",
            "op_id": f"rbp:{mutation_id}:hidden",
            "turn": turn,
        })
        patches.append({
            "op": "set", "path": "representation.locked_layer_ids",
            "value": sorted(set(locked_layer_ids_of(result_mapspec)))[:_MAX_PROJECTED_IDS],
            "reason": f"layer removed: {reason}",
            "op_id": f"rbp:{mutation_id}:locked",
            "turn": turn,
        })
        return patches

    if kind == "PatchComponentIntent":
        component_id = str(getattr(intent, "component_id", "") or "")
        enabled = getattr(intent, "enabled", None)
        component_type = getattr(intent, "component_type", None) or _component_type_of(component_id)
        if enabled is None or component_type is None:
            return []
        patches.append({
            "op": "set", "path": f"components.{component_type}",
            "value": bool(enabled),
            "reason": reason,
            "op_id": f"rbp:{mutation_id}:component:{component_type}",
            "turn": turn,
        })
        return patches

    if kind == "RemoveComponentIntent":
        component_id = str(getattr(intent, "component_id", "") or "")
        component_type = _component_type_of(component_id)
        if component_type is None:
            return []
        patches.append({
            "op": "set", "path": f"components.{component_type}", "value": False,
            "reason": f"component removed: {reason}",
            "op_id": f"rbp:{mutation_id}:component:{component_type}",
            "turn": turn,
        })
        return patches

    # 其余 requirement-significant 面（style/layout/duplicate/rebind）：
    # MapSpec 是这些面的权威（requirement 不镜像逐层 paint/布局坐标），
    # 有意不投影（ADR-0219 已知边界）——skip reason 见 projection_skip_reason。
    return patches


def projection_skip_reason(
    intent: Any,
    *,
    origin: str,
    actor: Optional[str],
    producer_class: Optional[str],
) -> Optional[str]:
    """不投影的专属 reason code（可观测面；None = 应投影或已投影）。"""
    kind = type(intent).__name__
    visible = getattr(intent, "visible", None)
    taxonomy = classify_mutation(
        kind, origin, actor=actor, producer_class=producer_class,
        visible_only_opacity=(kind == "PatchLayerPresentationIntent"
                              and visible is None),
    )
    if not taxonomy.projects_to_requirement:
        if taxonomy.taxonomy == "system_repair":
            return "projection_system_repair"
        if taxonomy.reason_code == "taxonomy_unknown_kind":
            return "projection_unknown_kind"
        if taxonomy.reason_code == "taxonomy_opacity_only":
            return "projection_ephemeral"
        return "projection_ephemeral"
    # requirement-significant 但 requirement 不镜像的面（MapSpec 权威）
    if taxonomy.dimension in ("layer_style", "title_content"):
        return "projection_not_mirrored"
    return None





async def project_user_mutation(
    session_id: str,
    intent: Any,
    *,
    origin: str,
    actor: Optional[str],
    producer_class: Optional[str],
    mutation_id: str,
    turn_id: Optional[str],
    result_mapspec: Optional[Dict[str, Any]],
    store: Optional[Any] = None,
) -> Dict[str, Any]:
    """一次投影（内部使用；外部入口见 ``project_user_mutation_safe``）。

    Args:
        store: SessionStore map_state 面（缺省生产单例 session_data_manager；
            测试可注入内存实现——与 requirement service 同 DI 纪律）。

    Returns:
        {"projected": int, "errors": [reason codes], "skipped": code|None}
    """
    session_store = store if store is not None else session_data_manager
    # 纯门控前置（无 IO——非投影面不读文档；review P2#8 IO 削减）。
    turn = _turn_of(turn_id)
    patch_payloads = build_projection_patches(
        intent, origin=origin, actor=actor, producer_class=producer_class,
        mutation_id=mutation_id, turn=turn, result_mapspec=result_mapspec,
    )
    if not patch_payloads:
        reason = projection_skip_reason(
            intent, origin=origin, actor=actor, producer_class=producer_class)
        return {"projected": 0, "errors": [], "skipped": reason or "projection_ephemeral"}

    state = await requirement_service.load_state(session_store, session_id)
    if state is None:
        resolution = resolve_conflict("projection_document_absent")
        return {"projected": 0, "errors": [], "skipped": resolution.code}
    from app.services.gis_harness.requirement_ir.contracts import RequirementDocument

    try:
        doc = RequirementDocument.model_validate(state.document)
    except Exception:  # noqa: BLE001 — schema skew 视同缺席
        return {"projected": 0, "errors": [], "skipped": "projection_document_absent"}
    if doc.lifecycle == "superseded":
        return {"projected": 0, "errors": [], "skipped": "projection_document_superseded"}

    patches = [
        requirement_service.TurnPatch(
            op=p["op"], path=p["path"], value=p["value"],
            reason=p["reason"], turn=p["turn"], actor="user", op_id=p["op_id"],
        )
        for p in patch_payloads
    ]
    outcome = await requirement_service.apply_user_patches(
        session_store, session_id, patches)
    errors = list(outcome.get("errors") or [])
    return {
        "projected": int(outcome.get("applied") or 0),
        "errors": errors,
        "skipped": "projection_patch_refused" if errors else None,
    }


async def project_user_mutation_safe(
    session_id: str,
    intent: Any,
    result: Any,
    *,
    origin: str,
    actor: Optional[str] = None,
    producer_class: Optional[str] = None,
    mutation_id: Optional[str] = None,
    turn_id: Optional[str] = None,
) -> None:
    """门面 post-commit 挂点入口（fail-open；绝不影响 mutation 主路径）。

    Args:
        result: 引擎事务成功回执（MapSpecResult / MapSpecBatchResult）——
            投影只信其 ``mapspec``（提交后权威 spec）与 revision。
    """
    if not requirement_projection_enabled():
        return
    try:
        if origin != "user":
            return
        mid = str(mutation_id or getattr(result, "mutation_id", "") or "")
        if not mid:
            return
        # duplicate 幂等重放**不跳过**投影（review P2#6）：首次提交的投影
        # 若因 fail-open 故障丢失，同 id 重试在此补偿——op_id 幂等使重复
        # 投影天然安全（已投影 → dedup 跳过；未投影 → 补上）。
        outcome = await project_user_mutation(
            session_id, intent,
            origin=origin, actor=actor, producer_class=producer_class,
            mutation_id=mid, turn_id=turn_id,
            result_mapspec=getattr(result, "mapspec", None),
        )
        if outcome.get("skipped") or outcome.get("errors"):
            logger.debug(
                "[representation_binding] projection outcome: %s", outcome)
    except Exception as exc:  # noqa: BLE001 — 通知平面纪律
        logger.debug("[representation_binding] projection skipped: %s", exc)


async def project_user_batch_safe(
    session_id: str,
    intents: List[Any],
    result: Any,
    *,
    origin: str,
    actor: Optional[str] = None,
    producer_class: Optional[str] = None,
    mutation_id: Optional[str] = None,
    turn_id: Optional[str] = None,
) -> None:
    """batch 提交的投影（applied 项聚合为一次 load→apply→save；纪律同单笔）。

    绝对值集语义使批内顺序无关——首条 patch 已携带收敛后的完整集合，
    聚合写入既省 IO（review P2#8）也避免逐 intent 的 N 次全量保存。
    """
    if not requirement_projection_enabled():
        return
    try:
        if origin != "user":
            return
        outcomes = getattr(result, "outcomes", None) or []
        applied_ids = {
            o.layer_id for o in outcomes if getattr(o, "status", "") == "applied"
        }
        mid = str(mutation_id or getattr(result, "mutation_id", "") or "")
        if not mid:
            return
        turn = _turn_of(turn_id)
        result_mapspec = getattr(result, "mapspec", None)
        aggregate: List[Dict[str, Any]] = []
        for intent in intents:
            if getattr(intent, "layer_id", None) not in applied_ids:
                continue
            aggregate.extend(build_projection_patches(
                intent, origin=origin, actor=actor,
                producer_class=producer_class, mutation_id=mid,
                turn=turn, result_mapspec=result_mapspec))
        if not aggregate:
            return
        # 同 op_id 语义聚合：取首条（绝对值集已收敛全部批效果）。
        dedup: Dict[str, Dict[str, Any]] = {}
        for payload in aggregate:
            dedup.setdefault(payload["op_id"], payload)
        state = await requirement_service.load_state(
            session_data_manager, session_id)
        if state is None:
            return
        from app.services.gis_harness.requirement_ir.contracts import RequirementDocument

        try:
            doc = RequirementDocument.model_validate(state.document)
        except Exception:  # noqa: BLE001
            return
        if doc.lifecycle == "superseded":
            return
        patches = [
            requirement_service.TurnPatch(
                op=p["op"], path=p["path"], value=p["value"],
                reason=p["reason"], turn=p["turn"], actor="user",
                op_id=p["op_id"])
            for p in dedup.values()
        ]
        outcome = await requirement_service.apply_user_patches(
            session_data_manager, session_id, patches)
        if outcome.get("errors"):
            logger.debug(
                "[representation_binding] batch projection errors: %s",
                outcome["errors"])
    except Exception as exc:  # noqa: BLE001 — 通知平面纪律
        logger.debug("[representation_binding] batch projection skipped: %s", exc)
