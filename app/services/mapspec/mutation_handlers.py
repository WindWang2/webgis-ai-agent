"""Per-intent mutation handlers（H02 解巨石 — 每个意图一种语义）。

每个 handler 是 apply_mutation 原 isinstance 分支体的**逐字搬移**（仅
四处机械适配：分支体缩进、``self.store`` → ``ctx.store``、snapshot 赋值
上收 orchestration、workbench CAS 回执的 ``prior_mutation_revision`` 改读
``ctx.prior_revision``），终局拒绝直接返回 MapSpecResult（原 return 语句
原样保留，orchestration 按类型分流）——行为零漂移由 parity corpus
（tests/cartography/test_mutation_parity_corpus.py，golden 取自重构前
基线）逐 case 断言。

新增 intent：在本文件加一个 handler + mutation_registry.py 登记一行
descriptor（lock targets / effect 分类 / codec / 事件投影全部随
descriptor 声明），不再触碰 lifecycle_engine 与任何平行 switch。
"""

import asyncio
import copy
import logging
from typing import Any, Dict, List, Optional, Union

from app.lib.cartography.data_tiers import (
    TIER_EXPORT_FEATURES as MAPSPEC_MAX_FEATURES,
)
from app.services.mapspec.mutation_contracts import (
    MapSpecResult,
    MutationContext,
    MutationPlan,
)
from app.services.mapspec.mutation_primitives import (
    _MAX_COMPONENT_BYTES,
    _estimate_component_bytes,
    _patch_layer_presentation,
    _preserve_durable_presentation,
    _project_cartographic_intent,
    _workbench_doc_error,
)
from app.services.mapspec.quality_gate_hook import _run_quality_gate_hook
from app.services.mapspec.store import _should_remove_layer
from app.services.mapspec.pipeline import process_layer_ingestion
from app.services.session_data import session_data_manager
from app.services.mapspec.checkpoint import (
    rollback as rollback_checkpoint,
    snapshot as create_checkpoint,
)
from app.services.mapspec.visual_healer import (
    VisualHealStrategyPlanner,
    apply_heal_plan,
)

logger = logging.getLogger(__name__)

#: handler 正常产出 candidate 计划；终局拒绝直接返回 MapSpecResult。
HandlerOut = Union[MutationPlan, MapSpecResult]


async def _handle_initproject(ctx: MutationContext) -> HandlerOut:
    """InitProjectIntent 分支体（逐字迁移，见模块 docstring 的适配清单）。"""
    intent = ctx.intent

    # Full spec build, no snapshot needed (nothing to roll back)
    mapspec = {
        "version": "1.0",
        "view": ({**intent.view, "framed": True} if intent.view else {}),
        "sources": {},
        "layers": [],
        "layout": {
            "legend": {"visible": True, "position": "top-right"},
            "controls": [{"type": "navigation", "position": "top-right"}],
        },
        "thresholds": intent.thresholds
        or {"maxFeatures": MAPSPEC_MAX_FEATURES, "timeoutMs": 30000},
    }
    return MutationPlan(mapspec=mapspec)


async def _handle_setview(ctx: MutationContext) -> HandlerOut:
    """SetViewIntent 分支体（逐字迁移，见模块 docstring 的适配清单）。"""
    loaded = ctx.loaded
    intent = ctx.intent

    # V3 COW: view-only mutation, shallow copy + copy touched branch
    mapspec = {**loaded} if loaded else {}
    view = dict(mapspec.get("view", {}))  # copy view branch
    if intent.center is not None:
        view["center"] = intent.center
    if intent.zoom is not None:
        view["zoom"] = intent.zoom
    if intent.pitch is not None:
        view["pitch"] = intent.pitch
    if intent.bearing is not None:
        view["bearing"] = intent.bearing
    view["framed"] = True
    mapspec["view"] = view
    return MutationPlan(mapspec=mapspec)


async def _handle_upsertlayer(ctx: MutationContext) -> HandlerOut:
    """UpsertLayerIntent 分支体（逐字迁移，见模块 docstring 的适配清单）。"""
    session_id = ctx.session_id
    origin = ctx.origin
    loaded = ctx.loaded
    intent = ctx.intent

    # V3 COW: shallow copy + per-branch copy for sources and layers.
    # process_layer_ingestion never mutates mapspec in-place (it copies
    # existing_entry at pipeline.py:56 before any write). The view update
    # suggested_view is an in-place write on mapspec["view"], so we copy
    # that branch too. Source entry objects in sources are shared but
    # not mutated (only replaced by key). This avoids full deepcopy for
    # the rollback snapshot (which just needs the prior reference).
    mapspec = {**loaded} if loaded else {}
    mapspec["sources"] = dict(loaded.get("sources", {})) if loaded else {}
    mapspec["layers"] = list(loaded.get("layers", [])) if loaded else []
    mapspec["view"] = dict(loaded.get("view", {})) if loaded else {}

    session_dir = ctx.store.get_session_dir(session_id)
    # P3-1: pre-fetch content revisions (V5-E) for any ref the
    # ingestion may stamp onto the source entry — the sync
    # ingestion runs in a worker thread and cannot await.
    _ref_revs: dict = {}
    try:
        for _v in (intent.source_data, intent.layer.get("provenance")):
            _r = (
                _v.get("ref_id") or _v.get("result_ref")
                if isinstance(_v, dict)
                else None
            )
            if isinstance(_r, str) and _r.startswith("ref:"):
                _d = await session_data_manager.get_ref_descriptor(session_id, _r)
                if isinstance(_d, dict) and isinstance(_d.get("content_revision"), int):
                    _ref_revs[_r] = _d["content_revision"]
    except Exception as e:  # noqa: BLE001 — stamping is advisory
        logger.debug("content_revision prefetch skipped: %s", e)
    # 卸载重计算（GeoJSON profiling / raster PNG 渲染）到线程，
    # 释放 event loop 给其它 session 的 I/O（REL-07）。
    processed_layer, source_entry, suggested_view = await asyncio.to_thread(
        process_layer_ingestion,
        mapspec,
        intent.layer,
        intent.source_data,
        session_dir,
        ref_content_revisions=_ref_revs,
    )
    # ADR-0153 pre-commit 质量门禁（P1）：blocking → 拒绝本次
    # mutation（候选未提交，直接返回）；warning → advisory 落
    # 元数据放行。只加钩子，不改上方既有摄取逻辑。
    gate_refusal = await _run_quality_gate_hook(
        source_entry,
        processed_layer,
        origin=origin,
    )
    if gate_refusal is not None:
        return gate_refusal
    source_id = processed_layer.get("source", "default_source")
    mapspec["sources"][source_id] = source_entry

    if suggested_view and not mapspec.get("view", {}).get("framed"):
        mapspec["view"]["center"] = suggested_view["center"]
        mapspec["view"]["zoom"] = suggested_view["zoom"]

    layers = mapspec["layers"]
    updated = False
    for i, layer in enumerate(layers):
        if layer.get("id") == processed_layer.get("id"):
            # ST-P2-2：重跑同 id upsert 整层替换时保留既有
            # durable presentation（用户显隐/透明度决策）。
            _preserve_durable_presentation(layer, processed_layer)
            layers[i] = processed_layer
            updated = True
            break
    if not updated:
        layers.append(processed_layer)
    # CA-P1-1：authoring 决策投影为 cartographic_intent
    # （QA RESULT_VISIBILITY 的意图证据——此前只读不写，恒
    # not_evaluated）。
    _project_cartographic_intent(processed_layer)

    pending_layer_op = (
        "upsert",
        processed_layer.get("id", "layer"),
        processed_layer,
    )
    return MutationPlan(
        mapspec=mapspec, auto_checkpoint=True, pending_layer_op=pending_layer_op
    )


async def _handle_patchlayerpresentation(ctx: MutationContext) -> HandlerOut:
    """PatchLayerPresentationIntent 分支体（逐字迁移，见模块 docstring 的适配清单）。"""
    origin = ctx.origin
    loaded = ctx.loaded
    intent = ctx.intent

    mapspec = {**loaded} if loaded else {}
    mapspec["layers"] = list(loaded.get("layers", []) if loaded else [])
    matched = False
    patched_layers: List[Any] = []
    for layer in mapspec["layers"]:
        if not isinstance(layer, dict):
            patched_layers.append(layer)
            continue
        if _should_remove_layer(layer, intent.layer_id):
            matched = True
            patched_layers.append(
                _patch_layer_presentation(
                    layer,
                    intent.visible,
                    intent.opacity,
                    origin=str(origin),
                )
            )
        else:
            patched_layers.append(layer)
    if not matched:
        return MapSpecResult(
            is_error=True,
            origin=origin,
            error_msg=f"Layer {intent.layer_id} not found.",
            correction_hint=("Re-read MapSpec and patch an existing layer id."),
        )
    mapspec["layers"] = patched_layers
    return MutationPlan(mapspec=mapspec, auto_checkpoint=True)


async def _handle_patchlayerstyle(ctx: MutationContext) -> HandlerOut:
    """PatchLayerStyleIntent 分支体（逐字迁移，见模块 docstring 的适配清单）。"""
    origin = ctx.origin
    loaded = ctx.loaded
    intent = ctx.intent

    # #1077：持久样式突变 —— paint 顶层键合并进 spec 层族
    # （与 presentation patch 同族谓词；不触碰
    # cartographic_intent —— 样式不是 presentation 决策）。
    mapspec = {**loaded} if loaded else {}
    mapspec["layers"] = list(loaded.get("layers", []) if loaded else [])
    styled_layers: List[Any] = []
    style_matched = False
    for layer in mapspec["layers"]:
        if not isinstance(layer, dict):
            styled_layers.append(layer)
            continue
        if _should_remove_layer(layer, intent.layer_id):
            style_matched = True
            merged_paint = dict(layer.get("paint") or {})
            merged_paint.update(intent.paint or {})
            patched_style = dict(layer)
            patched_style["paint"] = merged_paint
            styled_layers.append(patched_style)
        else:
            styled_layers.append(layer)
    if not style_matched:
        return MapSpecResult(
            is_error=True,
            origin=origin,
            error_msg=f"Layer {intent.layer_id} not found.",
            correction_hint=("Re-read MapSpec and patch an existing layer id."),
        )
    mapspec["layers"] = styled_layers
    return MutationPlan(mapspec=mapspec, auto_checkpoint=True)


async def _handle_upsertsource(ctx: MutationContext) -> HandlerOut:
    """UpsertSourceIntent 分支体（逐字迁移，见模块 docstring 的适配清单）。"""
    origin = ctx.origin
    loaded = ctx.loaded
    intent = ctx.intent

    mapspec = {**loaded} if loaded else {}
    mapspec["sources"] = dict(loaded.get("sources", {})) if loaded else {}
    # 669: immutable hand-off contract — intent.source is treated
    # as immutable after dispatch. Top-level and `profile` are
    # shallow-copied (O(1) isolation from caller post-mutation);
    # nested payload (`inlineData`, typically a large
    # FeatureCollection) is intentionally shared by reference
    # (CoW parity with SetView/UpsertLayer) — callers must not
    # mutate it after dispatch, and the engine never mutates it.
    _src = dict(intent.source)
    if isinstance(_src.get("profile"), dict):
        _src["profile"] = dict(_src["profile"])
    # ADR-0153 pre-commit 质量门禁（P1，UpsertSource 路径）：
    # 同 UpsertLayer —— blocking 拒绝、warning 落 advisory。
    if isinstance(_src.get("inlineData"), dict):
        _gate_layer_probe: Dict[str, Any] = {
            "id": intent.source_id,
            "crs": _src.get("crs"),
        }
        _gate_probe_entry = dict(_src)
        # 钩子只读 inlineData / type / profile —— 不改 _src 结构，
        # advisory 与 profile 扩展写回 _src（下面若放行则提交）。
        _gate_refusal = await _run_quality_gate_hook(
            _gate_probe_entry,
            _gate_layer_probe,
            origin=origin,
        )
        if _gate_refusal is not None:
            return _gate_refusal
        if isinstance(_gate_probe_entry.get("profile"), dict):
            _src["profile"] = _gate_probe_entry["profile"]
        if _gate_probe_entry.get("quality_advisories"):
            _src["quality_advisories"] = _gate_probe_entry["quality_advisories"]
    mapspec["sources"][intent.source_id] = _src
    return MutationPlan(mapspec=mapspec, auto_checkpoint=True)


async def _handle_patchcomponent(ctx: MutationContext) -> HandlerOut:
    """PatchComponentIntent 分支体（逐字迁移，见模块 docstring 的适配清单）。"""
    origin = ctx.origin
    loaded = ctx.loaded
    intent = ctx.intent

    # 组件局部突变（UI 拖拽收尾 / Agent 组件编辑）——与
    # SetLayoutIntent 的整表替换不同，只动命中的单个组件。
    # 突变/校验复用 gis_harness.components.mutate_component，
    # 不出现第二套组件突变实现。
    from app.services.gis_harness.components import (
        CartographyComponent,
        mutate_component,
    )

    mapspec = {**loaded} if loaded else {}
    layout = dict(mapspec.get("layout", {}))
    raw_components = layout.get("components") or []
    components = [
        CartographyComponent.model_validate(dict(c))
        for c in raw_components
        if isinstance(c, dict)
    ]
    if not components and not intent.upsert:
        return MapSpecResult(
            is_error=True,
            origin=origin,
            error_msg="MapSpec has no layout.components to patch.",
            correction_hint=(
                "Initialize components via webgis_map_product or "
                "webgis_layout_set first, or patch with upsert."
            ),
        )
    mutated, change = mutate_component(
        components,
        component_id=intent.component_id,
        component_type=intent.component_type,
        enabled=intent.enabled,
        position=intent.position,
        placement=intent.placement,
        variant=intent.variant,
        style=intent.style,
        options=intent.options,
        upsert=intent.upsert,
    )
    if change is None:
        return MapSpecResult(
            is_error=True,
            origin=origin,
            error_msg=(f"Component {intent.component_id} not found."),
            correction_hint=(
                "Current components: "
                + ", ".join(f"{c.id}({c.type})" for c in components)
            ),
        )
    # QA 加固对齐 SetLayoutIntent：patch 路径（component_update /
    # 用户路由）同样不允许组件条目携带大数据（96KB）。
    oversized = [
        c.id
        for c in mutated
        if _estimate_component_bytes(c.to_mapspec()) > _MAX_COMPONENT_BYTES
    ]
    if oversized:
        return MapSpecResult(
            is_error=True,
            origin=origin,
            error_msg=(
                "patched layout.components entry exceeds "
                f"{_MAX_COMPONENT_BYTES // 1024}KB: " + ", ".join(oversized[:5])
            ),
            correction_hint=(
                "组件 options 不携带大数据——图表用 chart=ChartData"
                "（大载荷自动转 ref:chart-* artifact），统计用 "
                "stats.items 摘要（≤24 条）。"
            ),
        )
    layout["components"] = sorted(
        [c.to_mapspec() for c in mutated],
        key=lambda c: (c.get("priority", 0), c.get("id", "")),
    )
    mapspec["layout"] = layout
    return MutationPlan(mapspec=mapspec)


async def _handle_removecomponent(ctx: MutationContext) -> HandlerOut:
    """RemoveComponentIntent 分支体（逐字迁移，见模块 docstring 的适配清单）。"""
    origin = ctx.origin
    loaded = ctx.loaded
    intent = ctx.intent

    # Component Lifecycle V3（Runtime V4 §18）：真删除 ——
    # mutate_component 的 enabled=False 是隐藏；这里是布局
    # 条目移除。纯函数 remove_component 与 patch 同源。
    from app.services.gis_harness.components import (
        CartographyComponent,
        remove_component,
    )

    mapspec = {**loaded} if loaded else {}
    layout = dict(mapspec.get("layout", {}))
    raw_components = layout.get("components") or []
    components = [
        CartographyComponent.model_validate(dict(c))
        for c in raw_components
        if isinstance(c, dict)
    ]
    remaining, change = remove_component(
        components,
        component_id=intent.component_id,
    )
    if change is None:
        return MapSpecResult(
            is_error=True,
            origin=origin,
            error_msg=(f"Component {intent.component_id} not found."),
            correction_hint=(
                "Current components: "
                + ", ".join(f"{c.id}({c.type})" for c in components)
            ),
        )
    layout["components"] = sorted(
        [c.to_mapspec() for c in remaining],
        key=lambda c: (c.get("priority", 0), c.get("id", "")),
    )
    mapspec["layout"] = layout
    return MutationPlan(mapspec=mapspec)


async def _handle_duplicatecomponent(ctx: MutationContext) -> HandlerOut:
    """DuplicateComponentIntent 分支体（逐字迁移，见模块 docstring 的适配清单）。"""
    origin = ctx.origin
    loaded = ctx.loaded
    intent = ctx.intent

    from app.services.gis_harness.components import (
        CartographyComponent,
        duplicate_component,
    )

    mapspec = {**loaded} if loaded else {}
    layout = dict(mapspec.get("layout", {}))
    raw_components = layout.get("components") or []
    components = [
        CartographyComponent.model_validate(dict(c))
        for c in raw_components
        if isinstance(c, dict)
    ]
    # 注意不能用 ``copy`` 作局部名 —— 本函数上游用 stdlib
    # copy.deepcopy（同名局部会把它遮蔽成 UnboundLocal）。
    with_copy, copy_component, dup_error = duplicate_component(
        components,
        component_id=intent.component_id,
        new_id=intent.new_id or "",
    )
    if dup_error or copy_component is None:
        return MapSpecResult(
            is_error=True,
            origin=origin,
            error_msg=dup_error or "duplicate failed",
        )
    # 与 patch 分支同纪律：组件条目尺寸有界（96KB）。
    oversized_dup = [
        c.id
        for c in with_copy
        if _estimate_component_bytes(c.to_mapspec()) > _MAX_COMPONENT_BYTES
    ]
    if oversized_dup:
        return MapSpecResult(
            is_error=True,
            origin=origin,
            error_msg=(
                "duplicated layout.components entry exceeds "
                f"{_MAX_COMPONENT_BYTES // 1024}KB: " + ", ".join(oversized_dup[:5])
            ),
        )
    layout["components"] = sorted(
        [c.to_mapspec() for c in with_copy],
        key=lambda c: (c.get("priority", 0), c.get("id", "")),
    )
    mapspec["layout"] = layout
    return MutationPlan(mapspec=mapspec)


async def _handle_rebindcomponent(ctx: MutationContext) -> HandlerOut:
    """RebindComponentIntent 分支体（逐字迁移，见模块 docstring 的适配清单）。"""
    origin = ctx.origin
    loaded = ctx.loaded
    intent = ctx.intent

    from app.services.gis_harness.components import (
        CartographyComponent,
        rebind_component,
    )

    mapspec = {**loaded} if loaded else {}
    layout = dict(mapspec.get("layout", {}))
    raw_components = layout.get("components") or []
    components = [
        CartographyComponent.model_validate(dict(c))
        for c in raw_components
        if isinstance(c, dict)
    ]
    # review M：layerId 绑定目标在**锁内**对权威 spec 复核
    # （纯函数零 IO）；ref 活性探测留在调用方 best-effort
    # （探测是健康证据不是注册真相 —— 文档如实）。
    if "layerId" in intent.bindings:
        wanted = str(intent.bindings["layerId"])
        layer_present = any(
            isinstance(layer, dict)
            and (
                str(layer.get("id") or "") == wanted
                or str(layer.get("id") or "").startswith(f"{wanted}__")
                or str(layer.get("id") or "").startswith(f"{wanted}-")
            )
            for layer in (loaded or {}).get("layers", [])
        )
        if not layer_present:
            return MapSpecResult(
                is_error=True,
                origin=origin,
                error_msg=(f"重绑定图层 {wanted} 不在当前 MapSpec"),
                correction_hint=("先读当前 MapSpec 确认图层族 id 再重绑定。"),
            )
    rebound, change, rebind_error = rebind_component(
        components,
        component_id=intent.component_id,
        bindings=dict(intent.bindings),
    )
    if rebind_error or change is None:
        return MapSpecResult(
            is_error=True,
            origin=origin,
            error_msg=rebind_error or "rebind failed",
        )
    layout["components"] = sorted(
        [c.to_mapspec() for c in rebound],
        key=lambda c: (c.get("priority", 0), c.get("id", "")),
    )
    mapspec["layout"] = layout
    return MutationPlan(mapspec=mapspec)


async def _handle_removelayer(ctx: MutationContext) -> HandlerOut:
    """RemoveLayerIntent 分支体（逐字迁移，见模块 docstring 的适配清单）。"""
    loaded = ctx.loaded
    intent = ctx.intent

    # V3 COW: layers mutation, shallow copy + new filtered list
    mapspec = {**loaded} if loaded else {}
    layers = mapspec.get("layers", [])
    mapspec["layers"] = [
        lay for lay in layers if not _should_remove_layer(lay, intent.layer_id)
    ]
    # F-11（audit5 #1074）复核后维持既有契约：sources 是数据
    # 登记项，remove_layer 只做图层清扫、不做 source GC（#1014
    # TE-P1-1，scenario_8 测试锁定 —— ref 生命周期另有治理；
    # inlineData 死重是已知的权衡而非缺陷）。
    pending_layer_op = ("remove", intent.layer_id, None)
    return MutationPlan(
        mapspec=mapspec, auto_checkpoint=True, pending_layer_op=pending_layer_op
    )


async def _handle_reorderlayers(ctx: MutationContext) -> HandlerOut:
    """ReorderLayersIntent 分支体（逐字迁移，见模块 docstring 的适配清单）。"""
    origin = ctx.origin
    loaded = ctx.loaded
    intent = ctx.intent

    mapspec = {**loaded} if loaded else {}
    current = list(mapspec.get("layers", []) if loaded else [])
    # Support both exact ID and prefix matching for sublayers (__fill, __outline, etc.)
    matched_ordered: List[Dict[str, Any]] = []
    matched_ids: set = set()
    for lid in intent.layer_ids:
        for layer in current:
            if not isinstance(layer, dict):
                continue
            layer_id = str(layer.get("id") or "")
            if layer_id in matched_ids:
                continue
            if (
                layer_id == lid
                or layer_id.startswith(f"{lid}__")
                or layer_id.startswith(f"{lid}-")
            ):
                matched_ordered.append(layer)
                matched_ids.add(layer_id)
    leftover = [
        layer
        for layer in current
        if isinstance(layer, dict) and str(layer.get("id") or "") not in matched_ids
    ]
    if not matched_ordered:
        return MapSpecResult(
            is_error=True,
            origin=origin,
            error_msg="Reorder referenced no existing layers.",
            correction_hint="Re-read MapSpec and reorder current layer ids.",
        )
    mapspec["layers"] = matched_ordered + leftover
    return MutationPlan(mapspec=mapspec, auto_checkpoint=True)


async def _handle_setlayout(ctx: MutationContext) -> HandlerOut:
    """SetLayoutIntent 分支体（逐字迁移，见模块 docstring 的适配清单）。"""
    origin = ctx.origin
    loaded = ctx.loaded
    intent = ctx.intent

    # V3 COW: layout-only mutation, shallow copy + copy touched branch
    mapspec = {**loaded} if loaded else {}
    layout = dict(mapspec.get("layout", {}))  # copy layout branch
    # V5（ADR-0118 D2）：legend/margins 是 dict 形态的局部意图，
    # 做字段级 merge —— 整值替换会静默丢弃既有 position 等键。
    if intent.legend is not None:
        prev_legend = layout.get("legend")
        layout["legend"] = (
            {**prev_legend, **intent.legend}
            if isinstance(prev_legend, dict)
            else dict(intent.legend)
        )
    if intent.controls is not None:
        layout["controls"] = intent.controls
    if intent.margins is not None:
        prev_margins = layout.get("margins")
        layout["margins"] = (
            {**prev_margins, **intent.margins}
            if isinstance(prev_margins, dict)
            else dict(intent.margins)
        )
    if intent.components is not None:
        # 组件整体替换（webgis_component_update 先读后写实现
        # 局部突变）；条目要求唯一 string id + string type，
        # 非法/重复输入确定性拒绝，不留半更新状态。
        # QA-2026-08-26 加固：LLM 曾把整份 FeatureCollection 塞进
        # statistics_panel.options（layout_set 绕过组件 payload
        # 校验）——组件条目尺寸有界（96KB），大数据走
        # ref:chart-* artifact / 图层 ref，不进 layout.components。
        valid = all(
            isinstance(c, dict)
            and isinstance(c.get("id"), str)
            and isinstance(c.get("type"), str)
            for c in intent.components
        )
        oversized = [
            str(c.get("id"))
            for c in intent.components
            if isinstance(c, dict)
            and _estimate_component_bytes(c) > _MAX_COMPONENT_BYTES
        ]
        if oversized:
            return MapSpecResult(
                is_error=True,
                origin=origin,
                error_msg=(
                    "layout.components entries exceed "
                    f"{_MAX_COMPONENT_BYTES // 1024}KB: " + ", ".join(oversized[:5])
                ),
                correction_hint=(
                    "组件 options 不携带大数据（FeatureCollection/"
                    "全量记录）——图表经 generate_chart(attach_to_map)"
                    "或 component_update(chart=…) 走 ref:chart-* "
                    "artifact；统计数据用 stats.items 摘要（≤24 条）。"
                ),
            )
        ids = [c.get("id") for c in intent.components if isinstance(c, dict)]
        if not valid or len(ids) != len(set(ids)):
            return MapSpecResult(
                is_error=True,
                origin=origin,
                error_msg=(
                    "layout.components entries require unique "
                    "string id and string type."
                ),
                correction_hint=(
                    "Each component must be "
                    "{'id': str, 'type': str, ...} with unique "
                    "ids — see CartographyComponent "
                    "(gis_harness.components)."
                ),
            )
        layout["components"] = sorted(
            intent.components,
            key=lambda c: (c.get("priority", 0), c.get("id", "")),
        )
    if intent.component_links is not None:
        # ADR-0214 D2：实例边整表写入（确定性拒绝非法/超限，
        # 与 components 同门 —— 不留半更新状态）。type 词表
        # 与 schema Literal 同表（review P2-6：lax 校验会让
        # 非法 type 入库后打破 canonical parse）。
        from app.lib.cartography.mapspec_schema import (
            COMPONENT_LINK_TYPES,
        )

        links = intent.component_links
        _LINK_TARGET_KINDS = (None, "component", "layer", "source")
        links_valid = all(
            isinstance(lk, dict)
            and isinstance(lk.get("src"), str)
            and isinstance(lk.get("dst"), str)
            and lk.get("type") in COMPONENT_LINK_TYPES
            and lk.get("dst_kind") in _LINK_TARGET_KINDS
            for lk in links
        )
        if not links_valid or len(links) > 32:
            return MapSpecResult(
                is_error=True,
                origin=origin,
                error_msg=(
                    "layout.component_links entries require "
                    "{src: str, dst: str, type: str} and "
                    "total ≤32."
                ),
                correction_hint=(
                    "组件图显式边由组合契约 apply 生成；手工声明保持稀少。"
                ),
            )
        layout["component_links"] = links
    if intent.composition is not None:
        # ADR-0214 D3：组合身份块整值写入（键契约单一事实 =
        # composition_contract.CompositionIdentity；有界 4KB）。
        comp_block = intent.composition
        if (
            not isinstance(comp_block, dict)
            or _estimate_component_bytes(comp_block) > 4096
        ):
            return MapSpecResult(
                is_error=True,
                origin=origin,
                error_msg=(
                    "layout.composition must be a bounded "
                    "object (≤4KB) — see CompositionIdentity."
                ),
                correction_hint=(
                    "身份块由 webgis_apply_composition 写入，不手工构造。"
                ),
            )
        layout["composition"] = comp_block
    mapspec["layout"] = layout
    return MutationPlan(mapspec=mapspec)


async def _handle_setworkbenchstate(ctx: MutationContext) -> HandlerOut:
    """SetWorkbenchStateIntent 分支体（逐字迁移，见模块 docstring 的适配清单）。"""
    prior_mutation_revision = ctx.prior_revision
    origin = ctx.origin
    loaded = ctx.loaded
    intent = ctx.intent

    # V5：组织态整体替换（COW 顶层分支 copy，与 SetLayout 同款）。
    # 校验先行 —— 非法 doc 4xx，不进入 commit/checkpoint。
    doc_error = _workbench_doc_error(intent.doc)
    if doc_error is not None:
        return MapSpecResult(
            is_error=True,
            origin=origin,
            error_msg=doc_error,
            correction_hint=(
                "Re-read the workbench document (mapspec."
                "workbench), apply the mutation locally, and "
                "retry with the full doc."
            ),
        )
    # V6（R1-C2）：workbench 级 CAS。提供 base_workbench_revision
    # 且与存储 `_rev` 不一致 → superseded（回灌当前 doc）。堵住
    # 「游标 revision 被无关 mutation 推进后陈旧全量 doc 静默
    # 覆盖他人组织态」的窗口。缺省 = V5 语义（旧客户端兼容）。
    if intent.base_workbench_revision is not None:
        stored_rev = (
            loaded.get("workbench", {}).get("_rev", 0)
            if isinstance(loaded, dict) and isinstance(loaded.get("workbench"), dict)
            else 0
        )
        try:
            stored_rev_int = int(stored_rev)
        except (TypeError, ValueError):
            stored_rev_int = 0
        if intent.base_workbench_revision != stored_rev_int:
            return MapSpecResult(
                superseded=True,
                is_error=False,
                origin=origin,
                mapspec=loaded,
                mutation_revision=prior_mutation_revision,
                error_msg="Workbench document has changed.",
                correction_hint=(
                    "Re-read mapspec.workbench and re-apply your "
                    "organization edits on the server document."
                ),
            )
    mapspec = {**loaded} if loaded else {}
    # `_rev` = 本次 mutation_revision（commit 阶段落盘后与
    # revision 一致）。None 占位由 commit 路径统一盖章。
    mapspec["workbench"] = {**intent.doc, "_rev": None}
    return MutationPlan(mapspec=mapspec)


async def _handle_patchworkbenchdelta(ctx: MutationContext) -> HandlerOut:
    """PatchWorkbenchDeltaIntent 分支体（逐字迁移，见模块 docstring 的适配清单）。"""
    origin = ctx.origin
    loaded = ctx.loaded
    intent = ctx.intent

    # V6：组织态增量补丁。管线/引用检查在 collab/delta 纯函数
    # （与前端 TS 镜像共享语义）；结果经全量校验后整体替换。
    from app.services.collab.delta import DeltaError, validate_delta, apply_delta

    try:
        norm_delta = validate_delta(intent.delta)
    except DeltaError as exc:
        return MapSpecResult(
            is_error=True,
            origin=origin,
            error_code="workbench_delta_invalid",
            error_msg=str(exc),
            correction_hint=(
                "Re-read mapspec.workbench, rebuild the delta "
                "against the server document, and retry."
            ),
        )
    current_doc = (
        loaded.get("workbench")
        if isinstance(loaded, dict) and isinstance(loaded.get("workbench"), dict)
        else None
    )
    try:
        new_doc = apply_delta(current_doc, norm_delta)
    except DeltaError as exc:
        return MapSpecResult(
            is_error=True,
            origin=origin,
            error_code="workbench_delta_conflict",
            error_msg=str(exc),
            correction_hint=(
                "The referenced group does not exist server-side; "
                "re-read mapspec.workbench and rebuild the delta."
            ),
        )
    doc_error = _workbench_doc_error(new_doc)
    if doc_error is not None:
        return MapSpecResult(
            is_error=True,
            origin=origin,
            error_code="workbench_delta_invalid",
            error_msg=doc_error,
            correction_hint=(
                "The patch would produce an invalid document "
                "(cycle/depth/size); re-read and rebuild."
            ),
        )
    mapspec = {**loaded} if loaded else {}
    mapspec["workbench"] = {**new_doc, "_rev": None}
    return MutationPlan(mapspec=mapspec)


async def _handle_checkpoint(ctx: MutationContext) -> HandlerOut:
    """CheckpointIntent 分支体（逐字迁移，见模块 docstring 的适配清单）。"""
    session_id = ctx.session_id
    loaded = ctx.loaded
    intent = ctx.intent

    # V3 COW: checkpoint reads but doesn't mutate the spec
    mapspec = loaded  # no mutation, just checkpoint
    session_dir = ctx.store.get_session_dir(session_id)
    ckpt_res = await create_checkpoint(
        mapspec, session_dir, session_data_manager, intent.checkpoint_id
    )
    checkpoint_id_created = ckpt_res.get("checkpoint_id")
    ckpt_ref_count = ckpt_res.get("ref_count", 0)
    return MutationPlan(
        mapspec=mapspec,
        checkpoint_id=checkpoint_id_created,
        ckpt_ref_count=ckpt_ref_count,
    )


async def _handle_rollback(ctx: MutationContext) -> HandlerOut:
    """RollbackIntent 分支体（逐字迁移，见模块 docstring 的适配清单）。"""
    session_id = ctx.session_id
    origin = ctx.origin
    intent = ctx.intent

    # V3 COW: rollback replaces the entire spec
    session_dir = ctx.store.get_session_dir(session_id)
    rb_res = await rollback_checkpoint(
        session_dir, intent.checkpoint_id, session_data_manager
    )
    if not rb_res.get("success"):
        return MapSpecResult(
            is_error=True,
            origin=origin,
            error_msg=rb_res.get("message", "Rollback failed"),
        )
    mapspec = rb_res["mapspec"]
    ckpt_ref_count = rb_res.get("ref_count", 0)
    # rollback 恢复了 refs + mapspec；运行时 layers 需整体对齐到
    # 恢复后的 mapspec.layers（用特殊 op 标记）。
    # is_rollback 旗标由 plan.is_rollback=True 承载（原局部变量）
    return MutationPlan(
        mapspec=mapspec, is_rollback=True, ckpt_ref_count=ckpt_ref_count
    )


async def _handle_restorestyle(ctx: MutationContext) -> HandlerOut:
    """RestoreStyleIntent 分支体（逐字迁移，见模块 docstring 的适配清单）。"""
    origin = ctx.origin
    loaded = ctx.loaded
    intent = ctx.intent

    # ADR-0099 style-only restore：表达面（view/layout/
    # basemap/time/逐层 paint+presentation）来自版本快照，
    # 数据层与计算计划不动。快照里存在而当前 spec 缺席的
    # layer_id 如实跳过（记入 result notes —— 恢复是诚实
    # 的子集，不是虚构整图）。
    # review M-A1：层可见性的权威字段是 layout.visibility
    # （+ cartographic_intent 印记）—— 快照的顶层 visible
    # 不是真实 spec 字段；恢复经 _patch_layer_presentation
    # 同源语义落账（expected_visible/presentation_owner），
    # 不留过期归属印记。
    # review m6：快照分支全部 deepcopy —— 快照同时活在
    # 账本行的 JSON 列里，任何浅别名都可能把候选态的变更
    # 泄回账本（JSON 列不侦测就地突变）。
    snap = copy.deepcopy(intent.snapshot if isinstance(intent.snapshot, dict) else {})
    mapspec = {**loaded} if loaded else {}
    for branch in ("view", "basemap", "time"):
        if isinstance(snap.get(branch), dict):
            mapspec[branch] = copy.deepcopy(snap[branch])
    if isinstance(snap.get("layout"), dict):
        mapspec["layout"] = copy.deepcopy(snap["layout"])
    snap_layers = {
        str(ly.get("id")): ly
        for ly in (snap.get("layers") or [])
        if isinstance(ly, dict) and ly.get("id")
    }
    restored_layer_ids: list = []
    skipped_layer_ids: list = []
    if snap_layers:
        merged_layers = []
        for ly in mapspec.get("layers") or []:
            lid = str(ly.get("id") or "")
            src = snap_layers.get(lid)
            if src is None:
                merged_layers.append(ly)
                continue
            merged_layer = copy.deepcopy(ly)
            if isinstance(src.get("paint"), dict):
                merged_layer["paint"] = copy.deepcopy(src["paint"])
            if src.get("label") is not None:
                merged_layer["label"] = src["label"]
            # 可见性 → 权威字段（layout.visibility + intent 印记）
            snap_layout = (
                src.get("layout") if isinstance(src.get("layout"), dict) else {}
            )
            visible: Optional[bool]
            if isinstance(snap_layout.get("visibility"), str):
                visible = snap_layout["visibility"] == "visible"
            elif "visible" in src:
                visible = bool(src.get("visible"))  # 旧快照兼容
            else:
                visible = None
            if visible is not None:
                merged_layer = _patch_layer_presentation(
                    merged_layer, visible, None, origin=str(origin)
                )
            merged_layers.append(merged_layer)
            restored_layer_ids.append(lid)
        skipped_layer_ids = sorted(set(snap_layers) - set(restored_layer_ids))
        mapspec["layers"] = merged_layers
    restore_notes = {
        "restored_layers": restored_layer_ids[:64],
        "skipped_snapshot_layers": skipped_layer_ids[:64],
    }
    return MutationPlan(mapspec=mapspec, restore_notes=restore_notes)


async def _handle_setbasemap(ctx: MutationContext) -> HandlerOut:
    """SetBasemapIntent 分支体（逐字迁移，见模块 docstring 的适配清单）。"""
    loaded = ctx.loaded
    intent = ctx.intent

    # V3 COW: basemap-only mutation (#722), same discipline as
    # SetView/SetTime — shallow copy + copy touched branch.
    mapspec = {**loaded} if loaded else {}
    basemap = dict(mapspec.get("basemap", {}))
    if intent.provider_id is not None:
        basemap["providerId"] = intent.provider_id
    if intent.raster_filters is not None:
        basemap["rasterFilters"] = intent.raster_filters
    if intent.overlays is not None:
        basemap["overlays"] = intent.overlays
    if intent.vector_style_url is not None:
        basemap["vectorStyleUrl"] = intent.vector_style_url
    mapspec["basemap"] = basemap
    return MutationPlan(mapspec=mapspec)


async def _handle_settime(ctx: MutationContext) -> HandlerOut:
    """SetTimeIntent 分支体（逐字迁移，见模块 docstring 的适配清单）。"""
    loaded = ctx.loaded
    intent = ctx.intent

    # V3 COW: time-only mutation, shallow copy + copy touched branch
    mapspec = {**loaded} if loaded else {}
    time_cfg = dict(
        mapspec.get(
            "time",
            {
                "enabled": True,
                "field": "timestamp",
                "type": "continuous",
                "extent": [],
                "current": None,
                "step": 1.0,
                "speed": 1.0,
            },
        )
    )
    if intent.enabled is not None:
        time_cfg["enabled"] = intent.enabled
    if intent.field is not None:
        time_cfg["field"] = intent.field
    if intent.type is not None:
        time_cfg["type"] = intent.type
    if intent.extent is not None:
        time_cfg["extent"] = intent.extent
    if intent.current is not None:
        time_cfg["current"] = intent.current
    if intent.window is not None:
        time_cfg["window"] = intent.window
    if intent.playback is not None:
        time_cfg.setdefault("playback", {}).update(intent.playback)
    if intent.step is not None:
        time_cfg["step"] = intent.step
    if intent.speed is not None:
        time_cfg["speed"] = intent.speed
    mapspec["time"] = time_cfg
    return MutationPlan(mapspec=mapspec)


async def _handle_setscenariomode(ctx: MutationContext) -> HandlerOut:
    """SetScenarioModeIntent 分支体（逐字迁移，见模块 docstring 的适配清单）。"""
    origin = ctx.origin
    loaded = ctx.loaded
    intent = ctx.intent

    # ADR-0193：推演视图协议（COW 只拷顶层分支）。
    # 非法值整笔拒绝 —— last-known-good 不变；None = 退出
    # 推演（键移除），对齐"缺失 = 非推演视图"的 schema 语义。
    from app.lib.cartography.mapspec_schema import SCENARIO_MODES

    mode = intent.scenario_mode
    if mode is not None and mode not in SCENARIO_MODES:
        return MapSpecResult(
            is_error=True,
            origin=origin,
            error_msg=(
                f"非法 scenario_mode: {mode!r}；"
                f"支持 {list(SCENARIO_MODES)} 或 None（退出推演）。"
            ),
            correction_hint=(
                "scenario_mode 仅接受 'split_view' / 'swipe_compare'"
                "（或 None 清除推演模式）。"
            ),
        )
    mapspec = {**loaded} if loaded else {}
    if mode is None:
        mapspec.pop("scenario_mode", None)
    else:
        mapspec["scenario_mode"] = mode
    return MutationPlan(mapspec=mapspec)


async def _handle_setscene(ctx: MutationContext) -> HandlerOut:
    """SetSceneIntent 分支体（逐字迁移，见模块 docstring 的适配清单）。"""
    origin = ctx.origin
    loaded = ctx.loaded
    intent = ctx.intent

    # ADR-0199：场景协议（COW 只拷顶层分支；presentation 面）。
    # 形状经 MapSceneConfig 严格校验 —— 非法值整笔拒绝，
    # last-known-good 不变；None = 清除场景（键移除）。
    from app.lib.cartography.mapspec_schema import (
        MAX_TERRAIN_EXAGGERATION,
        MapSceneConfig,
        SCENE_MODES,
    )

    scene_value = intent.scene
    if scene_value is not None:
        if not isinstance(scene_value, dict):
            return MapSpecResult(
                is_error=True,
                origin=origin,
                error_msg=(
                    f"非法 scene 配置：期望对象，得到 {type(scene_value).__name__}。"
                ),
                correction_hint="scene 必须是 MapSceneConfig 对象或 None（清除）。",
            )
        try:
            parsed_scene = MapSceneConfig.model_validate(scene_value)
        except Exception as exc:  # noqa: BLE001 — 结构化拒绝
            return MapSpecResult(
                is_error=True,
                origin=origin,
                error_msg=f"非法 scene 配置：{exc}",
                correction_hint=(
                    f"mode 仅接受 {list(SCENE_MODES)}；terrain.source "
                    "必须是非空字符串（raster-dem 源 id）。"
                ),
            )
        terrain = parsed_scene.terrain
        if terrain is not None:
            ex = terrain.exaggeration
            if ex is not None and not (0 < float(ex) <= MAX_TERRAIN_EXAGGERATION):
                return MapSpecResult(
                    is_error=True,
                    origin=origin,
                    error_msg=(
                        f"terrain.exaggeration 越界：{ex}；"
                        f"合法区间 (0, {MAX_TERRAIN_EXAGGERATION}]。"
                    ),
                    correction_hint=(
                        "垂直夸张默认 1.0（诚实比例）；失真值 >1.5 需在输出中披露。"
                    ),
                )
        camera = parsed_scene.camera
        if camera is not None:
            pitch = camera.pitch
            if pitch is not None and not (0 <= float(pitch) <= 85.0):
                return MapSpecResult(
                    is_error=True,
                    origin=origin,
                    error_msg=(
                        f"scene.camera.pitch 越界：{pitch}；"
                        "合法区间 [0, 85]（MapLibre 硬上限）。"
                    ),
                    correction_hint="产品级建议档 ≤60；>85 会被 MapLibre 拒绝。",
                )
            bearing = camera.bearing
            if bearing is not None and not (-180.0 <= float(bearing) <= 180.0):
                return MapSpecResult(
                    is_error=True,
                    origin=origin,
                    error_msg=(
                        f"scene.camera.bearing 越界：{bearing}；合法区间 [-180, 180]。"
                    ),
                    correction_hint="方位角以正北为 0。",
                )
    mapspec = {**loaded} if loaded else {}
    if scene_value is None:
        mapspec.pop("scene", None)
    else:
        mapspec["scene"] = copy.deepcopy(scene_value)
    return MutationPlan(mapspec=mapspec)


async def _handle_applyvisualhealpatch(ctx: MutationContext) -> HandlerOut:
    """ApplyVisualHealPatchIntent 分支体（逐字迁移，见模块 docstring 的适配清单）。"""
    origin = ctx.origin
    loaded = ctx.loaded
    intent = ctx.intent

    # ADR-0186：视觉自愈微变异。锁内用权威 loaded spec 重规划
    # （防 TOCTOU），纯函数 COW 应用；后续 review / blocking
    # 校验 / checkpoint / revision+1 / 失败回滚全走既有管线。
    mapspec = {**loaded} if loaded else {}
    if not mapspec:
        return MapSpecResult(
            is_error=True,
            origin=origin,
            error_code="HEAL_PLAN_EMPTY",
            error_msg="Visual heal requires an existing MapSpec.",
            correction_hint="Initialize the project before visual self-healing.",
        )
    heal_plan = VisualHealStrategyPlanner().plan(
        mapspec, intent.defects, attempt=intent.attempt
    )
    if not heal_plan.ops:
        return MapSpecResult(
            is_error=True,
            origin=origin,
            error_code="HEAL_PLAN_EMPTY",
            error_msg="Visual heal plan has no applicable operations.",
            correction_hint=(
                "所有缺陷均被诚实跳过（未定位/已最优/不可靠目标）；"
                "详见 cartography_findings.visual_heal_skipped。"
            ),
            cartography_findings=[
                {
                    "visual_heal_skipped": list(heal_plan.skipped),
                }
            ],
        )
    mapspec, _heal_applied = apply_heal_plan(mapspec, heal_plan)

    # W15 状态三分类（§32）：transient 瞬态交互态永不持久 ——
    # 提交边界剥离（无瞬态键时零拷贝原样返回）。
    return MutationPlan(mapspec=mapspec, auto_checkpoint=True)
