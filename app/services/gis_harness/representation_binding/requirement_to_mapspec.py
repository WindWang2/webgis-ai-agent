"""Requirement → MapSpec 绑定应用（ADR-0219 C3）。

只做两件事，别无旁枝：

1. ``build_binding_proposals``（纯函数）：从 RequirementDocument 的
   **显式用户选择**（字段级 origin=user，ADR-0215 ownership）派生
   RepresentationBinding 提案——目标只允许现有 registry intent kind
   或既有 grammar face，绝不新造命令词表；
2. ``apply_binding`` / ``apply_pending_bindings_safe``：构造现有
   MutationIntent，经 ``apply_gis_mutation`` 正式事务落地（CAS /
   mutation_id 幂等 / user-wins 守卫全复用），回执写回 binding 终态。

fail-closed（权威面）：mutation 被拒 → binding 绝不假称 applied；
表达不兼容 → grammar/model eligibility 披露回退（ineligible），不直接强写。
fail-open（接线面）：``apply_pending_bindings_safe`` 是通知面纪律的
旁路——任何故障绝不阻断 intent 主链路。

style-only 保证：全部构造目标都是 presentation 类 intent（显隐/锁/组件），
``no_reanalysis=True`` 由构造面成立——不触发数据获取与科学计算。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from app.services.gis_harness.representation_binding.conflicts import (
    resolve_conflict,
    resolve_from_result,
)
from app.services.gis_harness.representation_binding.contracts import (
    BINDING_TARGET_GRAMMAR_FACE,
    BindingStatus,
    RepresentationBinding,
    binding_fingerprint,
)
from app.services.gis_harness.representation_binding.flags import (
    requirement_binding_enabled,
)
from app.services.gis_harness.requirement_ir import service as requirement_service
from app.services.session_data import session_data_manager

logger = logging.getLogger(__name__)

#: 家族前缀（与 workbench_locks/_should_remove_layer 的层族语义一致）。
_FAMILY_SEPS = ("-", "__")

_MAX_BINDINGS_PER_PASS = 16
_MAX_RETRIES = 1

# ── 进程内 pin 快照（sync 工具消费面） ─────────────────────────────────
# ``create_thematic_map`` 等同步工具运行在 worker 线程（无事件循环），
# 不能 await 文档加载。此快照在**同进程的异步接线点**（turn 开始的
# apply_pending_bindings / C4 投影）发布，sync 工具纯 dict 读。快照是
# best-effort 派生缓存而非第二真相源：缺席 → 工具回退既有参数路径
# （行为与接线前一致）；FIFO ≤64 有界。
_PIN_SNAPSHOT: "Dict[str, Dict[str, Any]]" = {}
_PIN_SNAPSHOT_MAX = 64


def publish_pin_snapshot(session_id: str, doc: Any) -> None:
    """发布当前文档的 grammar pin 快照（异步接线点调用；纯内存）。"""
    if not session_id:
        return
    try:
        _PIN_SNAPSHOT[session_id] = grammar_face_pins(doc)
        while len(_PIN_SNAPSHOT) > _PIN_SNAPSHOT_MAX:
            _PIN_SNAPSHOT.pop(next(iter(_PIN_SNAPSHOT)))
    except Exception:  # noqa: BLE001 — 快照绝不影响主链路
        logger.debug("[representation_binding] pin snapshot skipped", exc_info=True)


def pin_snapshot(session_id: str) -> Dict[str, Any]:
    """读取 pin 快照（sync 工具消费面；缺席 = 空，回退既有行为）。"""
    if not session_id:
        return {}
    return dict(_PIN_SNAPSHOT.get(session_id) or {})


#: IR geometry_kind 词表 ↔ model_library 注册 id 的确定性桥（两个既有
#: 词表的对齐面，不是新词表；歧义项（如 flow 有多个模型）不桥——
#: 未命中即 ineligible 披露，绝不猜）。
_IR_GEOMETRY_MODEL_BRIDGE: Dict[str, str] = {
    "heatmap": "visual_heatmap",
    "isoline": "isoline_contour",
    "point": "simple_point_map",
}


def _resolve_representation_model(kind: str) -> Optional[str]:
    """IR 表达名 → model_library 注册 id（registry alias 优先，桥表补充）。"""
    if not kind:
        return None
    from app.lib.cartography.model_library import get_map_model

    bridged = _IR_GEOMETRY_MODEL_BRIDGE.get(kind)
    if bridged is not None and get_map_model(bridged) is not None:
        return bridged
    if get_map_model(kind) is not None:
        return kind
    return None


def _layer_family_ids(mapspec: Optional[Dict[str, Any]]) -> Dict[str, List[str]]:
    """权威 spec 的层族索引：逻辑 id（去物理后缀）→ 当前物理 id 列表。"""
    families: Dict[str, List[str]] = {}
    if not isinstance(mapspec, dict):
        return families
    for layer in mapspec.get("layers") or []:
        if not isinstance(layer, dict):
            continue
        layer_id = layer.get("id")
        if not isinstance(layer_id, str) or not layer_id:
            continue
        base = layer_id
        for sep in _FAMILY_SEPS:
            pos = layer_id.find(sep)
            if pos > 0:
                base = layer_id[:pos]
                break
        families.setdefault(base, []).append(layer_id)
    return families


def _user_owns(doc: Any, path: str) -> bool:
    """字段级 ownership：override → section provenance（与 patch._current_owner 同序）。"""
    spec = doc.intent
    override = spec.field_provenance.get(path)
    if override is not None:
        return override.is_user()
    section_attr = path.split(".", 1)[0]
    section = getattr(spec, section_attr, None)
    if section is not None:
        prov = getattr(section, "provenance", None)
        if prov is not None:
            return prov.is_user()
    return False


def _current_mapspec_revision(mapspec_state: Dict[str, Any]) -> int:
    try:
        return int(mapspec_state.get("_cartographic_mutation_revision") or 0)
    except (TypeError, ValueError):
        return 0


def build_binding_proposals(
    doc: Any,
    mapspec: Optional[Dict[str, Any]],
    *,
    expected_revision: Optional[int] = None,
) -> List[RepresentationBinding]:
    """从需求文档派生绑定提案（纯函数、确定性、有界 ≤16）。

    只读**用户显式**表达面（字段级 origin=user）且文档未超替——
    "已接受"以字段级 ownership 判定（ADR-0215：user 即最高优先，
    lifecycle=accepted 是整档状态而非表达面门槛）。
    """
    if doc.lifecycle == "superseded":
        return []
    spec = doc.intent
    rep = spec.representation
    comps = spec.components
    families = _layer_family_ids(mapspec)
    proposals: List[RepresentationBinding] = []
    revision = doc.revision

    def _binding(
        path: str, target_kind: str, payload: Dict[str, Any], *,
        reason: str, lock_semantics: str = "respect_existing",
        disclosures: Optional[List[str]] = None,
    ) -> RepresentationBinding:
        fingerprint = binding_fingerprint({
            "doc": doc.document_id, "path": path, "target": target_kind,
            "payload": payload,
        })
        return RepresentationBinding(
            binding_id=f"rb-{fingerprint}",
            source_requirement_path=path,
            target_kind=target_kind,  # type: ignore[arg-type]
            actor="agent",
            payload=payload,
            provenance=spec.field_provenance.get(path)
            or rep.provenance.model_copy(),
            lock_semantics=lock_semantics,  # type: ignore[arg-type]
            expected_mapspec_revision=expected_revision,
            requirement_revision=revision,
            reason=reason[:200],
            reversible=True,
            no_reanalysis=True,
            binding_fingerprint=fingerprint,
            disclosures=disclosures or [],
        )

    # ── hidden_layer_ids → PatchLayerPresentationIntent（族扩展） ──────
    if rep.hidden_layer_ids and _user_owns(doc, "representation.hidden_layer_ids"):
        for layer_id in list(dict.fromkeys(rep.hidden_layer_ids))[:_MAX_BINDINGS_PER_PASS]:
            targets = sorted(families.get(layer_id, []))
            if layer_id in families:
                targets = sorted(set(targets) | {layer_id})
            if not targets:
                proposals.append(_binding(
                    "representation.hidden_layer_ids",
                    "PatchLayerPresentationIntent",
                    {"layer_id": layer_id, "visible": False},
                    reason=f"requirement: 用户隐藏 {layer_id}",
                ).with_status(
                    BindingStatus.SKIPPED, error_code="binding_target_absent",
                    disclosures=["目标层不存在于权威 spec；不视为错误"],
                ))
                continue
            for target_id in targets[:_MAX_BINDINGS_PER_PASS]:
                proposals.append(_binding(
                    "representation.hidden_layer_ids",
                    "PatchLayerPresentationIntent",
                    {"layer_id": target_id, "visible": False},
                    reason=f"requirement: 用户隐藏 {layer_id}（族成员 {target_id}）",
                ))

    # ── locked_layer_ids → PatchWorkbenchDeltaIntent（绝对值语义幂等） ──
    if rep.locked_layer_ids and _user_owns(doc, "representation.locked_layer_ids"):
        locks = sorted(set(rep.locked_layer_ids))[:_MAX_BINDINGS_PER_PASS]
        proposals.append(_binding(
            "representation.locked_layer_ids",
            "PatchWorkbenchDeltaIntent",
            {"locks_add": locks},
            reason=f"requirement: 用户锁定图层 {len(locks)} 个",
            lock_semantics="project_lock",
        ))

    # ── components（显式禁用 → PatchComponentIntent） ─────────────────
    component_types = {
        "legend": comps.legend, "title": comps.title,
        "scale_bar": comps.scale_bar, "north_arrow": comps.north_arrow,
        "labels": comps.labels, "attribution": comps.attribution,
    }
    for comp_type, required in component_types.items():
        # 仅显式 False（禁用）构造突变；True（要求配备）由 completeness
        # 权威承接（required_components_for），不重复施工。
        if required is not False:
            continue
        if not _user_owns(doc, f"components.{comp_type}"):
            continue
        target_id = _find_component_instance(mapspec, comp_type)
        binding = _binding(
            f"components.{comp_type}",
            "PatchComponentIntent",
            {"component_id": target_id, "component_type": comp_type, "enabled": False},
            reason=f"requirement: 用户禁用组件 {comp_type}",
        )
        if target_id is None:
            binding = binding.with_status(
                BindingStatus.SKIPPED, error_code="binding_target_absent",
                disclosures=["组件实例不存在；后续 agent 重建时由下一轮绑定再禁用"],
            )
        proposals.append(binding)

    # ── palette / geometry_kind → grammar face（不强写 paint） ─────────
    if rep.palette and _user_owns(doc, "representation.palette"):
        from app.services.gis_harness.requirement_ir.normalize import normalize_palette

        normalized = normalize_palette(rep.palette)
        binding = _binding(
            "representation.palette", BINDING_TARGET_GRAMMAR_FACE,
            {"pinned_palette": normalized},
            reason=f"requirement: 用户 pin 色带 {normalized}",
            lock_semantics="none",
            disclosures=["pin 经 grammar_request_face 进入 solve_grammar（palette 唯一裁决）"],
        )
        if not normalized:
            binding = binding.with_status(
                BindingStatus.INELIGIBLE, error_code="binding_ineligible_representation",
                disclosures=["palette 规范化为空；交由用户改选"],
            )
        proposals.append(binding)
    if rep.geometry_kind and _user_owns(doc, "representation.geometry_kind"):
        kind = rep.geometry_kind
        resolved = _resolve_representation_model(kind)
        binding = _binding(
            "representation.geometry_kind", BINDING_TARGET_GRAMMAR_FACE,
            {"pinned_representation": resolved or kind},
            reason=f"requirement: 用户 pin 表达 {kind}",
            lock_semantics="none",
            disclosures=["pin 经 grammar_request_face 进入 solve_grammar；数据适配在求解期披露"],
        )
        if resolved is None:
            binding = binding.with_status(
                BindingStatus.INELIGIBLE, error_code="binding_ineligible_representation",
                disclosures=[f"表达 {kind} 不在 model_library 注册词表（含 IR 桥表）；交由用户改选"],
            )
        proposals.append(binding)
    if rep.pinned_channels and _user_owns(doc, "representation.pinned_channels"):
        proposals.append(_binding(
            "representation.pinned_channels", BINDING_TARGET_GRAMMAR_FACE,
            {"pinned_channels": dict(rep.pinned_channels)},
            reason="requirement: 用户 pin 视觉通道",
            lock_semantics="none",
            disclosures=["pin 经 grammar_request_face 进入 solve_grammar"],
        ))

    return proposals[:_MAX_BINDINGS_PER_PASS]


def _find_component_instance(
    mapspec: Optional[Dict[str, Any]], component_type: str
) -> Optional[str]:
    """按类型找组件实例 id（layout.components 的 id/type 面；无则 None）。"""
    if not isinstance(mapspec, dict):
        return None
    layout = mapspec.get("layout")
    if not isinstance(layout, dict):
        return None
    for component in layout.get("components") or []:
        if not isinstance(component, dict):
            continue
        if component.get("type") == component_type and isinstance(component.get("id"), str):
            return component["id"]
    return None


def _intent_of(binding: RepresentationBinding) -> Optional[Any]:
    """binding → 现有 MutationIntent（target 只允许注册 kind；构造失败 → None）。"""
    if binding.target_kind == "PatchLayerPresentationIntent":
        from app.services.mapspec.intents import PatchLayerPresentationIntent

        return PatchLayerPresentationIntent(
            layer_id=str(binding.payload.get("layer_id", "")),
            visible=binding.payload.get("visible"),
        )
    if binding.target_kind == "PatchWorkbenchDeltaIntent":
        from app.services.mapspec.intents import PatchWorkbenchDeltaIntent

        return PatchWorkbenchDeltaIntent(delta={
            "locksAdd": list(binding.payload.get("locks_add") or []),
        })
    if binding.target_kind == "PatchComponentIntent":
        from app.services.mapspec.intents import PatchComponentIntent

        component_id = binding.payload.get("component_id")
        if not isinstance(component_id, str) or not component_id:
            return None
        return PatchComponentIntent(
            component_id=component_id,
            component_type=binding.payload.get("component_type"),
            enabled=binding.payload.get("enabled"),
        )
    return None  # grammar face：不构造 mutation


async def apply_binding(
    session_id: str,
    binding: RepresentationBinding,
    *,
    turn_id: Optional[str] = None,
) -> RepresentationBinding:
    """单条绑定经正式事务落地（回执 → 终态；纯转发，无旁路写）。

    幂等：``mutation_id = rb:<fingerprint>`` —— 同语义重放命中引擎
    dedup，返回存证 revision 不二次落盘（status=applied + duplicate）。
    """
    if binding.status != BindingStatus.PROPOSED:
        return binding
    if binding.target_kind == BINDING_TARGET_GRAMMAR_FACE:
        # grammar face 不是 mutation： ineligible 已在构造期判定，
        # 合法 pin 的"落地"发生在 cartography 求解期（见 grammar_face_pins）。
        return binding

    intent = _intent_of(binding)
    if intent is None:
        return binding.with_status(
            BindingStatus.SKIPPED, error_code="binding_target_absent",
            disclosures=["intent 构造失败（目标缺席）"])

    from app.services.gis_world_state.mutation import apply_gis_mutation

    result = await apply_gis_mutation(
        session_id, intent,
        origin="agent", actor="requirement_binding",
        expected_revision=binding.expected_mapspec_revision,
        mutation_id=binding.engine_mutation_id(),
        turn_id=turn_id,
        reason=binding.reason or binding.source_requirement_path,
    )
    code = resolve_from_result(
        is_error=result.is_error, superseded=result.superseded,
        duplicate=result.duplicate, error_code=result.error_code)
    if result.duplicate:
        return binding.with_status(
            BindingStatus.APPLIED,
            mutation_id=result.mutation_id,
            mutation_revision=result.mutation_revision,
            disclosures=[resolve_conflict(code or "").disclosure],
        )
    if result.superseded:
        return binding.with_status(
            BindingStatus.SUPERSEDED, mutation_revision=result.mutation_revision,
            error_code=code, disclosures=[resolve_conflict(code or "").disclosure])
    if result.is_error:
        # user-wins 守卫拒绝（error_code 空，门面以 error_msg 表达）也归入
        # 拒绝面：显隐反转拒绝 → binding_user_wins_presentation。
        final_code = code
        if final_code in (None, "", "binding_refused") and "Agent 不覆盖用户显式操作" in (result.error_msg or ""):
            final_code = "binding_user_wins_presentation"
        return binding.with_status(
            BindingStatus.REFUSED,
            mutation_id=result.mutation_id,
            error_code=final_code or "binding_refused",
            disclosures=[(result.error_msg or "")[:180]],
        )
    return binding.with_status(
        BindingStatus.APPLIED,
        mutation_id=result.mutation_id,
        mutation_revision=result.mutation_revision,
    )


def grammar_face_pins(doc: Any) -> Dict[str, Any]:
    """grammar face 的 pin 输入（cartography 求解期合并；纯读）。

    只透传用户显式 pin（与 ``projection.grammar_request_face`` 的
    user-wins 语义同源——rule 推断值绝不冒充用户 pin）。
    """
    if doc.lifecycle == "superseded":
        return {}
    spec = doc.intent
    rep = spec.representation
    pins: Dict[str, Any] = {}
    if rep.palette and _user_owns(doc, "representation.palette"):
        from app.services.gis_harness.requirement_ir.normalize import normalize_palette

        normalized = normalize_palette(rep.palette)
        if normalized:
            pins["pinned_palette"] = normalized
    if rep.geometry_kind and _user_owns(doc, "representation.geometry_kind"):
        resolved = _resolve_representation_model(rep.geometry_kind)
        if resolved:
            pins["pinned_representation"] = resolved
    if rep.pinned_channels and _user_owns(doc, "representation.pinned_channels"):
        pins["pinned_channels"] = dict(rep.pinned_channels)
    return pins


async def _load_document(session_id: str):
    state = await requirement_service.load_state(session_data_manager, session_id)
    if state is None:
        return None
    from app.services.gis_harness.requirement_ir.contracts import RequirementDocument

    try:
        return RequirementDocument.model_validate(state.document)
    except Exception:  # noqa: BLE001 — schema skew 视同缺席
        logger.debug("[representation_binding] document parse skipped", exc_info=True)
        return None


async def _mapspect_state(session_id: str) -> Dict[str, Any]:
    try:
        state = await session_data_manager.get_map_state(session_id)
        return state if isinstance(state, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


async def apply_pending_bindings(
    session_id: str,
    *,
    turn_id: Optional[str] = None,
) -> Dict[str, Any]:
    """一轮绑定应用（有界报告；grammar-face pin 单列不参与 mutation）。

    Returns:
        {"applied": [...], "refused": [...], "superseded": [...],
         "skipped": [...], "ineligible": [...], "grammar_pins": {...},
         "document": "absent|superseded|ok"}
    """
    doc = await _load_document(session_id)
    if doc is None:
        return {"document": "absent", "applied": [], "refused": [], "superseded": [],
                "skipped": [{"code": "projection_document_absent"}],
                "ineligible": [], "grammar_pins": {}}
    if doc.lifecycle == "superseded":
        return {"document": "superseded", "applied": [], "refused": [], "superseded": [],
                "skipped": [{"code": "binding_document_superseded"}],
                "ineligible": [], "grammar_pins": {}}

    state = await _mapspect_state(session_id)
    mapspec = state.get("mapspec") if isinstance(state.get("mapspec"), dict) else None
    revision = _current_mapspec_revision(state)
    # 发布 pin 快照（同进程后续 sync 工具的 grammar face 消费面）。
    publish_pin_snapshot(session_id, doc)

    async def _one_pass(expected_revision: Optional[int]) -> List[RepresentationBinding]:
        proposals = build_binding_proposals(doc, mapspec, expected_revision=expected_revision)
        outcomes: List[RepresentationBinding] = []
        for proposal in proposals:
            if proposal.target_kind == BINDING_TARGET_GRAMMAR_FACE:
                outcomes.append(proposal)   # pin 单列，不构造 mutation
                continue
            outcomes.append(await apply_binding(session_id, proposal, turn_id=turn_id))
        return outcomes

    outcomes = await _one_pass(revision or None)
    superseded = [b for b in outcomes if b.status == BindingStatus.SUPERSEDED]
    if superseded and _MAX_RETRIES >= 1:
        # CAS 过期（并发 mutation）：以新基线重放一次（有界重试）。
        fresh = await _mapspect_state(session_id)
        outcomes = await _one_pass(_current_mapspec_revision(fresh) or None)

    report: Dict[str, Any] = {
        "document": "ok",
        "applied": [b.to_dict() for b in outcomes if b.status == BindingStatus.APPLIED],
        "refused": [b.to_dict() for b in outcomes if b.status == BindingStatus.REFUSED],
        "superseded": [b.to_dict() for b in outcomes if b.status == BindingStatus.SUPERSEDED],
        "skipped": [b.to_dict() for b in outcomes if b.status == BindingStatus.SKIPPED],
        "ineligible": [b.to_dict() for b in outcomes if b.status == BindingStatus.INELIGIBLE],
        "grammar_pins": grammar_face_pins(doc),
    }
    return report


async def apply_pending_bindings_safe(
    session_id: str,
    *,
    turn_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """通知面纪律的旁路入口（fail-open；kill-switch 可整体关闭）。"""
    if not requirement_binding_enabled():
        return None
    try:
        return await apply_pending_bindings(session_id, turn_id=turn_id)
    except Exception as exc:  # noqa: BLE001 — 绝不阻断 intent 主链路
        logger.debug("[representation_binding] apply skipped: %s", exc)
        return None
