"""Independently testable webgis_map_design_select handler and explicit registration."""
from __future__ import annotations
from app.services.session_data import session_data_manager
from app.tools.registry import tool

from app.services.gis_harness.tools import (
    ToolRegistry,
    Optional,
)


async def webgis_map_design_select(
    query: str,
    session_id: Optional[str] = None,
    map_model_id: str = "",
    reason: str = "",
) -> dict:
    """候选选择 → user-wins 表达 pin（幂等；typed 拒绝不静默改道）。"""
    from app.lib.cartography.model_library import get_map_model_registry
    from app.services.gis_harness.requirement_ir.normalize import (
        normalize_geometry_kind,
    )
    from app.services.gis_harness.requirement_ir.service import (
        TurnPatch,
        apply_user_patches,
    )
    from app.services.gis_harness.representation_binding.flags import (
        requirement_binding_enabled,
    )
    from app.services.gis_harness.representation_binding.requirement_to_mapspec import (
        _load_document,
        grammar_face_pins,
    )

    if not session_id:
        # RAW/编排面（同 apply_scene_representation 惯例）：sessionId 由
        # dispatch 层经 RuntimeContext 提供。
        try:
            from app.lib.runtime.context import current_runtime_context
            _rt = current_runtime_context()
            session_id = str(getattr(_rt, "session_id", "") or "") if _rt else ""
        except Exception:  # noqa: BLE001
            session_id = ""
    if not session_id or not map_model_id:
        return {"success": False, "message": "Missing session_id/map_model_id"}

    model = get_map_model_registry().resolve(str(map_model_id))
    if model is None:
        return {
            "success": False,
            "error_code": "unknown_model",
            "message": f"model_library 未注册: {map_model_id[:48]}",
        }
    # 归一化还原校验：pin 词表是 requirement IR 的 geometry_kind（经
    # normalize + bridge/alias 解析）。还原不到同一模型（canonical id
    # 比对，别名经 registry.resolve 归一）→ typed 拒绝，绝不静默 pin
    # 到另一个模型。
    normalized = normalize_geometry_kind(model.id)
    # 往返校验必须走与写入/读回**同一 seam**：requirement IR 归一化 →
    # grammar_face_pins 的 _resolve_representation_model（IR 桥表）→
    # registry canonical（别名归一）。此前只做 registry.resolve 会把
    # visual_heatmap（norm→heatmap）等合法模型误拒。
    from app.services.gis_harness.representation_binding.requirement_to_mapspec import (
        _resolve_representation_model,
    )
    _raw_back = _resolve_representation_model(normalized) or ""
    _canon_back = get_map_model_registry().resolve(_raw_back) \
        if _raw_back else None
    resolved_back = _canon_back.id if _canon_back is not None else ""
    if resolved_back != model.id:
        return {
            "success": False,
            "error_code": "pin_target_mismatch",
            "message": (
                f"'{model.id}' 经 IR 归一化为 '{normalized}'，读写 seam "
                f"重解析到 '{resolved_back or '（无）'}' —— 词表改道，拒绝"
                " pin（可往返的模型 id 如：visual_heatmap/point_cluster/"
                "aggregate_grid/isoline_contour/administrative_choropleth/"
                "simple_point_map/proportional_symbol；改道语义请改 pin "
                "重解析到的目标模型）"),
            "requested_model": model.id,
            "normalized_kind": normalized,
        }
    if not requirement_binding_enabled():
        return {
            "success": False,
            "error_code": "binding_disabled",
            "message": "GIS_REQUIREMENT_BINDING=0 —— pin 写入面关闭（kill-switch）",
        }
    doc = await _load_document(session_id)
    if doc is None:
        return {
            "success": False,
            "error_code": "requirement_document_absent",
            "message": "会话无 RequirementDocument —— 先经 webgis_map_intent 建档",
        }
    _prev_raw = str(
        grammar_face_pins(doc).get("pinned_representation") or "")
    _prev_model = get_map_model_registry().resolve(_prev_raw) \
        if _prev_raw else None
    previous = _prev_model.id if _prev_model is not None else _prev_raw
    patch_reason = f"design-select: {model.id}" + (
        f"（{reason[:120]}）" if reason else "")
    result = await apply_user_patches(session_data_manager, session_id, [
        TurnPatch(op="set", path="representation.geometry_kind",
                  value=normalized, actor="user",
                  reason=patch_reason),
    ])
    errors = list(result.get("errors") or [])
    if errors:
        return {"success": False, "error_code": "patch_rejected",
                "errors": [str(e)[:80] for e in errors[:4]]}
    # 读回校验：pin 必须真实落档（fail-closed，不信任写入返回值）。
    # grammar_face_pins 存归一化词（别名可解析）——canonical 比对。
    doc2 = await _load_document(session_id)
    pinned_raw = str(
        grammar_face_pins(doc2).get("pinned_representation") or "") \
        if doc2 is not None else ""
    pinned_model = get_map_model_registry().resolve(pinned_raw) \
        if pinned_raw else None
    pinned = pinned_model.id if pinned_model is not None else ""
    ok = pinned == model.id
    return {
        "success": ok,
        "pinned_model": pinned,
        "previous_pin": previous,
        "normalized_kind": normalized,
        "user_wins": True,
        "message": (
            f"已固化表达方案 {model.id}（user-wins；下一次成图尊重该 pin）"
            if ok else "pin 读回不一致（罕见——检查并发写）"),
        "alternatives_note": (
            "落选候选可由同会话事实确定性重放（design_candidates 纯函数）"),
    }



def register_map_design_select(registry: ToolRegistry) -> None:
    tool(
        registry,
        tier=2, domains=["statistics", "report"], name="webgis_map_design_select",
        capabilities=['thematic_cartography'],
        contract_version=1,
        description=(
            "设计方案选择器：把用户/Agent 从 webgis_map_product 结果的"
            " design_candidates 中选定的表达模型固化为 user-wins 表达 pin。"
            "经 RequirementDocument 显式 patch（actor=user）写入，下一次成图"
            "（create_thematic_map 语法求解与 design_candidates 对齐面）尊重"
            "该 pin；自动调整/quality_loop 修复不得覆盖（既有 user-wins 守卫）。"
            "\n何时用：用户看到候选对比后说『就用区级统计图』『换成格网聚合』。"
            "\n注意：只接受可经 requirement IR 归一化还原的模型 id；无法还原的"
            "（如 dot_density_map 会被归一化改道）返回 typed 拒绝，绝不静默"
            "换目标。落选理由可由同一会话事实确定性重放（design_candidates "
            "纯函数），不依赖快照。"
        ),
        args_model=None,
        side_effect="state_mutation",
        deterministic=True,
        latency_class="fast",
        memory_class="light",
        scale_class="small",
        tags=("设计方案", "候选", "user-wins", "表达 pin", "design candidate"),
        output_semantic_type="design_selection_receipt",
        result_size_policy="inline_small",
        required_context=("map_state",),
        data_mutations=("session_state",),
        failure_modes=("invalid_args", "missing_data"),
        summary=(
            "把选定的制图表达模型固化为 user-wins pin（RequirementDocument "
            "显式 patch，actor=user）。下一次成图与自动调整均尊重该选择。"
        ),
        examples=(
            "就用区级分级统计图这个方案",
            "用户选了格网聚合表达，固化下来",
        ),
        anti_examples=(
            "直接改图层颜色——那是 presentation mutation，不是表达方案选择",
        ),
    )(webgis_map_design_select)
