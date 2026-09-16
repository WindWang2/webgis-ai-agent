"""MapSpec mutation Body → 引擎 Intent 的单一映射源（ADR-0201）。

此前 Body→Intent 的 if/elif 链内联在 ``mapspec_mutations`` 路由里；
proposal merge 需要同一映射（proposal 是"待审 mutation 集合"，合并回放
必须与用户直提路径语义完全一致），因此抽为 codec 供两处共用。

校验失败抛 ``ValueError``（消息与原路由逐字一致）——路由层捕获取 400。
"""
from __future__ import annotations

from app.services.mapspec.lifecycle_engine import (
    DuplicateComponentIntent,
    InitProjectIntent,
    MutationIntent,
    PatchComponentIntent,
    PatchLayerPresentationIntent,
    PatchLayerStyleIntent,
    RemoveComponentIntent,
    RemoveLayerIntent,
    RebindComponentIntent,
    ReorderLayersIntent,
    SetLayoutIntent,
    SetTimeIntent,
    SetViewIntent,
    SetWorkbenchStateIntent,
    PatchWorkbenchDeltaIntent,
)
from app.schemas.mapspec_mutation_schema import (
    DuplicateComponentBody,
    InitProjectBody,
    PatchComponentBody,
    PatchLayerPresentationBody,
    PatchLayerStyleBody,
    PatchWorkbenchDeltaBody,
    RemoveComponentBody,
    RemoveLayerBody,
    RebindComponentBody,
    ReorderLayersBody,
    SetLayoutBody,
    SetTimeBody,
    SetViewBody,
    SetWorkbenchStateBody,
)


def body_to_intent(req) -> MutationIntent:
    """14 Body discriminated union → 引擎 MutationIntent（与用户路由同语义）。"""
    if isinstance(req, PatchLayerStyleBody):
        return PatchLayerStyleIntent(
            layer_id=req.layer_id, paint=dict(req.paint),
        )
    if isinstance(req, PatchLayerPresentationBody):
        if req.visible is None and req.opacity is None:
            raise ValueError(
                "patch_layer_presentation requires visible and/or opacity"
            )
        return PatchLayerPresentationIntent(
            layer_id=req.layer_id,
            visible=req.visible,
            opacity=req.opacity,
        )
    if isinstance(req, PatchComponentBody):
        if all(
            f is None
            for f in (req.enabled, req.position, req.placement, req.variant, req.style, req.options)
        ):
            raise ValueError("patch_component requires at least one mutation field")
        return PatchComponentIntent(
            component_id=req.component_id,
            enabled=req.enabled,
            position=req.position,
            placement=req.placement.model_dump(exclude_none=True) if req.placement else None,
            variant=req.variant,
            style=req.style,
            options=req.options,
            upsert=req.upsert,
        )
    if isinstance(req, SetViewBody):
        if (
            req.center is None
            and req.zoom is None
            and req.pitch is None
            and req.bearing is None
        ):
            raise ValueError(
                "set_view requires center, zoom, pitch, and/or bearing"
            )
        return SetViewIntent(
            center=req.center,
            zoom=req.zoom,
            pitch=req.pitch,
            bearing=req.bearing,
        )
    if isinstance(req, RemoveLayerBody):
        return RemoveLayerIntent(layer_id=req.layer_id)
    if isinstance(req, RemoveComponentBody):
        return RemoveComponentIntent(component_id=req.component_id)
    if isinstance(req, DuplicateComponentBody):
        return DuplicateComponentIntent(
            component_id=req.component_id, new_id=req.new_id,
        )
    if isinstance(req, RebindComponentBody):
        bindings: dict[str, str] = {}
        if req.chart_ref:
            bindings["chartRef"] = req.chart_ref
        if req.table_ref:
            bindings["tableRef"] = req.table_ref
        if req.layer_id:
            bindings["layerId"] = req.layer_id
        if not bindings:
            raise ValueError(
                "rebind_component requires chart_ref, table_ref, or layer_id"
            )
        return RebindComponentIntent(
            component_id=req.component_id, bindings=bindings,
        )
    if isinstance(req, ReorderLayersBody):
        return ReorderLayersIntent(layer_ids=req.layer_ids)
    if isinstance(req, SetLayoutBody):
        return SetLayoutIntent(
            legend=req.legend, controls=req.controls, margins=req.margins,
            components=req.components,
        )
    if isinstance(req, SetTimeBody):
        return SetTimeIntent(
            enabled=req.enabled,
            field=req.field,
            type=req.type,
            extent=req.extent,
            current=req.current,
            window=req.window,
            playback=req.playback,
            step=req.step,
            speed=req.speed,
        )
    if isinstance(req, InitProjectBody):
        return InitProjectIntent(view=req.view)
    if isinstance(req, SetWorkbenchStateBody):
        return SetWorkbenchStateIntent(
            doc=req.doc,
            base_workbench_revision=req.base_workbench_revision,
        )
    if isinstance(req, PatchWorkbenchDeltaBody):
        return PatchWorkbenchDeltaIntent(delta=req.delta)
    raise ValueError("unsupported mapspec mutation intent")


def intent_kind(body) -> str:
    """Body 的 intent 词（存证/日志用）。"""
    return str(getattr(body, "intent", type(body).__name__))


def intent_target(body) -> str:
    """Body 的主结构目标（layer/component id；无则空串）。"""
    for attr in ("layer_id", "component_id"):
        value = getattr(body, attr, None)
        if value:
            return str(value)
    return ""


__all__ = ["body_to_intent", "intent_kind", "intent_target"]
