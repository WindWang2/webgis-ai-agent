"""Mutation Intent Registry — 全部 MapSpec mutation intent 的单一登记点（H02）。

IntentDescriptor 把一个 intent 的全部横切语义收拢为一条机器可读声明：
handler、effect 分类（semantic/presentation）、锁目标投影、COW 触层面、
auto-checkpoint、协作事件路由、provenance 投影、HTTP codec、journal
标签。lifecycle_engine 的 dispatch、gis_world_state 的事件/标签/溯源、
intent_codec 的 Body 映射全部从本 registry 派生 —— 新增 intent 只需
新增 descriptor + handler + 测试，不再跨 10+ 文件同步（issue #1547）。

兼容纪律：
- ``kind`` 恒等 ``intent_cls.__name__``（provenance/journal 字符串的既有
  消费面 —— 5 个读侧文件与 legacy socket —— 零破坏）；
- op_label 只登记既有 _OP_LABELS 的 20 条（缺省回退类名，行为零漂移；
  补词属行为变更，不入本线）；
- 投影函数（lock targets / collab payload / provenance detail / codec）
  逐字迁自原平行 switch，行为零漂移。

一致性 gate：tests/unit/test_mutation_registry_consistency.py 断言
registry ↔ MutationIntent Union ↔ codec ↔ parity corpus 同步。
"""
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple, Union

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
from app.services.mapspec import mutation_handlers as _h
from app.services.mapspec.intents import (
    ApplyVisualHealPatchIntent,
    CheckpointIntent,
    DuplicateComponentIntent,
    InitProjectIntent,
    MutationIntent,
    PatchComponentIntent,
    PatchLayerPresentationIntent,
    PatchLayerStyleIntent,
    PatchWorkbenchDeltaIntent,
    RebindComponentIntent,
    RemoveComponentIntent,
    RemoveLayerIntent,
    ReorderLayersIntent,
    RestoreStyleIntent,
    RollbackIntent,
    SetBasemapIntent,
    SetLayoutIntent,
    SetSceneIntent,
    SetScenarioModeIntent,
    SetTimeIntent,
    SetViewIntent,
    SetWorkbenchStateIntent,
    UpsertLayerIntent,
    UpsertSourceIntent,
)
from app.services.mapspec.mutation_contracts import (
    MapSpecResult,
    MutationContext,
    MutationPlan,
)

#: handler 协议：锁内、skeleton 处理后由 orchestration 调用；
#: candidate 计划返回 MutationPlan，终局拒绝直接返回 MapSpecResult。
MutationHandler = Callable[
    [MutationContext], Awaitable[Union[MutationPlan, MapSpecResult]]
]

#: HTTP Body → Intent 的 codec 工厂（用户路由 / proposal merge 共用）。
BodyCodec = Callable[[Any], MutationIntent]

#: 锁目标投影：intent → (目标图层 ids, 目标组件 ids)。
LockTargetsFn = Callable[[Any], Tuple[List[str], List[str]]]

#: 协作事件载荷投影：(intent, result) → 事件字段（revision/actor 等公共
#: 键由发布方统一附）。
CollabPayloadFn = Callable[[Any, Any], Dict[str, Any]]

#: provenance detail 投影（信封/override 字段由门面统一附加）。
ProvenanceDetailFn = Callable[[Any], Dict[str, Any]]

SEMANTIC = "semantic"
PRESENTATION = "presentation"


@dataclass(frozen=True)
class IntentDescriptor:
    """一个 mutation intent 的全部横切语义（单一登记点）。"""

    intent_cls: type
    handler: MutationHandler
    #: W15 override 三分类的 kind 面（semantic / presentation）。
    effect_class: str = SEMANTIC
    #: COW 判定：意图是否触及 layers（决定 rollback 快照的拷贝深度）。
    touches_layers: bool = False
    #: 提交期自动 checkpoint（原分支 auto_checkpoint 旗标的声明面）。
    auto_checkpoint: bool = False
    #: journal 中文标签（None = 回退类名 —— 与既有 _OP_LABELS 缺表行为一致）。
    op_label: Optional[str] = None
    #: 协作总线事件种类（doc|delta|presentation|None；None 仅发 op journal）。
    collab_event: Optional[str] = None
    collab_payload: Optional[CollabPayloadFn] = None
    #: provenance detail 投影（信封/override 字段由门面统一附加）。
    provenance_detail: Optional[ProvenanceDetailFn] = None
    lock_targets: Optional[LockTargetsFn] = None

    @property
    def kind(self) -> str:
        """provenance/journal 的 kind 字符串（恒等类名 —— 兼容契约）。"""
        return self.intent_cls.__name__


# ─── 锁目标投影（原 intent_lock_targets 分支逐字迁入） ────────────────────────
def _lt_layer_id(intent):
    return ([intent.layer_id], [])


def _lt_upsert_layer(intent: UpsertLayerIntent):
    layer = intent.layer if isinstance(intent.layer, dict) else {}
    lid = layer.get("id")
    return ([str(lid)] if isinstance(lid, str) and lid else [], [])


def _lt_visual_heal(intent: ApplyVisualHealPatchIntent):
    # ADR-0186：锁面 = 全部缺陷靶图层 + 遮挡者（遮挡者会被重排/压透明度）
    targets: List[str] = []
    for defect in intent.defects:
        for lid in defect.layer_ids:
            if isinstance(lid, str) and lid:
                targets.append(lid)
        if isinstance(defect.occluder_layer_id, str) and defect.occluder_layer_id:
            targets.append(defect.occluder_layer_id)
    return (targets, [])


def _lt_component_id(intent):
    return ([], [intent.component_id])


def _lt_set_layout(intent: SetLayoutIntent):
    ids = [
        str(c.get("id"))
        for c in (intent.components or [])
        if isinstance(c, dict) and isinstance(c.get("id"), str)
    ]
    return ([], ids)


def _lt_reorder_layers(intent: ReorderLayersIntent):
    return ([lid for lid in intent.layer_ids if isinstance(lid, str)], [])


def _lt_restore_style(intent: RestoreStyleIntent):
    snap = intent.snapshot if isinstance(intent.snapshot, dict) else {}
    layer_ids = [
        str(lay.get("id"))
        for lay in (snap.get("layers") or [])
        if isinstance(lay, dict) and isinstance(lay.get("id"), str)
    ]
    layout = snap.get("layout") if isinstance(snap.get("layout"), dict) else {}
    comp_ids = [
        str(c.get("id"))
        for c in (layout.get("components") or [])
        if isinstance(c, dict) and isinstance(c.get("id"), str)
    ]
    return (layer_ids, comp_ids)


# ─── 协作事件载荷投影（原 _publish_collab_events 分支逐字迁入） ────────────────
def _cp_workbench_doc(intent, result):
    doc = None
    if isinstance(result.mapspec, dict) and isinstance(
        result.mapspec.get("workbench"), dict
    ):
        doc = result.mapspec["workbench"]
    return {"doc": doc}


def _cp_workbench_delta(intent, result):
    # R1-M3：delta 事件只携带 delta（绝对值语义 + revision 门控保证重放
    # 安全）—— 捎带全量 doc 会让 >64KB 场景的每次小 delta 触发全体协作者
    # 全量 refetch。
    return {"delta": intent.delta}


def _cp_presentation(intent, result):
    return {
        "layerId": intent.layer_id,
        "visible": intent.visible,
        "opacity": intent.opacity,
    }


# ─── provenance detail 投影（原 apply_gis_mutation detail 构造逐字迁入） ───────
def _pd_presentation(intent):
    detail: Dict[str, Any] = {}
    if intent.visible is not None:
        detail["visible"] = bool(intent.visible)
    if intent.opacity is not None:
        detail["opacity"] = float(intent.opacity)
    return detail


def _pd_remove_component(intent):
    # V4：用户真删除的组件族（finalizer 的 user-wins 修复依据）。
    return {"removed_component_id": str(intent.component_id)}


def _pd_none(intent):
    return {}


# ─── HTTP Body codec（原 intent_codec.body_to_intent 分支逐字迁入，
#     含全部 ValueError 消息原文 —— 路由层 400 消息零漂移） ─────────────────────
def _codec_patch_layer_style(req):
    return PatchLayerStyleIntent(
        layer_id=req.layer_id, paint=dict(req.paint),
    )


def _codec_patch_layer_presentation(req):
    if req.visible is None and req.opacity is None:
        raise ValueError(
            "patch_layer_presentation requires visible and/or opacity"
        )
    return PatchLayerPresentationIntent(
        layer_id=req.layer_id,
        visible=req.visible,
        opacity=req.opacity,
    )


def _codec_patch_component(req):
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


def _codec_set_view(req):
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


def _codec_remove_layer(req):
    return RemoveLayerIntent(layer_id=req.layer_id)


def _codec_remove_component(req):
    return RemoveComponentIntent(component_id=req.component_id)


def _codec_duplicate_component(req):
    return DuplicateComponentIntent(
        component_id=req.component_id, new_id=req.new_id,
    )


def _codec_rebind_component(req):
    bindings: dict = {}
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


def _codec_reorder_layers(req):
    return ReorderLayersIntent(layer_ids=req.layer_ids)


def _codec_set_layout(req):
    return SetLayoutIntent(
        legend=req.legend, controls=req.controls, margins=req.margins,
        components=req.components,
    )


def _codec_set_time(req):
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


def _codec_init_project(req):
    return InitProjectIntent(view=req.view)


def _codec_set_workbench_state(req):
    return SetWorkbenchStateIntent(
        doc=req.doc,
        base_workbench_revision=req.base_workbench_revision,
    )


def _codec_patch_workbench_delta(req):
    return PatchWorkbenchDeltaIntent(delta=req.delta)


class MutationRegistry:
    """intent 类 → descriptor 的封闭注册表（fail-closed：未注册即拒绝）。

    扩展点：``register`` 幂等拒绝重复类 —— 新增 intent = 一个 descriptor
    （+ 可选 Body codec）+ 一个 handler + 测试，全部语义随声明走。
    """

    def __init__(self) -> None:
        self._by_cls: Dict[type, IntentDescriptor] = {}
        self._body_codecs: Dict[type, BodyCodec] = {}

    def register(
        self,
        descriptor: IntentDescriptor,
        *,
        body_cls: Optional[type] = None,
        body_codec: Optional[BodyCodec] = None,
    ) -> None:
        cls = descriptor.intent_cls
        if cls in self._by_cls:
            raise ValueError(f"intent 已注册: {cls.__name__}")
        self._by_cls[cls] = descriptor
        if body_cls is not None:
            self.register_body_codec(body_cls, body_codec)

    def register_body_codec(self, body_cls: type, codec: BodyCodec) -> None:
        """为已注册 intent 补登 HTTP Body codec（无对应引擎类新增）。"""
        if body_cls in self._body_codecs:
            raise ValueError(f"body codec 已注册: {body_cls.__name__}")
        self._body_codecs[body_cls] = codec

    def unregister(self, intent_cls: type) -> None:
        """撤销登记（动态扩展 / 测试装配的对称操作；生产路径不调用）。"""
        self._by_cls.pop(intent_cls, None)

    def descriptor_for(self, intent: Any) -> Optional[IntentDescriptor]:
        return self._by_cls.get(type(intent))

    def descriptor_by_kind(self, kind: str) -> Optional[IntentDescriptor]:
        for d in self._by_cls.values():
            if d.kind == kind:
                return d
        return None

    def all(self) -> List[IntentDescriptor]:
        return list(self._by_cls.values())

    def intent_classes(self) -> Tuple[type, ...]:
        return tuple(self._by_cls.keys())

    def intent_union(self) -> type:
        """registry 全集的 Union（与 intents.MutationIntent 由一致性 gate
        断言同步 —— union 本体保持 intents.py 静态定义，避免 import 环）。"""
        return Union[self.intent_classes()]  # type: ignore[valid-type]

    # ── 派生投影（原平行 switch 的唯一事实源）───────────────────────────────
    def lock_targets(self, intent: Any) -> Tuple[List[str], List[str]]:
        d = self.descriptor_for(intent)
        if d is None or d.lock_targets is None:
            return ([], [])
        return d.lock_targets(intent)

    def effect_class(self, intent: Any) -> str:
        d = self.descriptor_for(intent)
        return d.effect_class if d is not None else SEMANTIC

    def op_label(self, intent: Any) -> str:
        d = self.descriptor_for(intent)
        if d is not None and d.op_label:
            return d.op_label
        return type(intent).__name__

    def collab_event(self, intent: Any) -> Optional[str]:
        d = self.descriptor_for(intent)
        return d.collab_event if d is not None else None

    def collab_payload(self, intent: Any, result: Any) -> Dict[str, Any]:
        d = self.descriptor_for(intent)
        if d is not None and d.collab_payload is not None:
            return d.collab_payload(intent, result)
        return {}

    def provenance_detail(self, intent: Any) -> Dict[str, Any]:
        d = self.descriptor_for(intent)
        if d is not None and d.provenance_detail is not None:
            return d.provenance_detail(intent)
        return {}

    def body_to_intent(self, req: Any) -> MutationIntent:
        """Body discriminated union → 引擎 intent（fail-closed ValueError）。

        与 intent_codec.body_to_intent 同语义（同一 codec 表派生）；
        未知 Body → ValueError("unsupported mapspec mutation intent")。
        """
        codec = self._body_codecs.get(type(req))
        if codec is None:
            raise ValueError("unsupported mapspec mutation intent")
        return codec(req)


def _build_default_registry() -> MutationRegistry:
    reg = MutationRegistry()
    reg.register(IntentDescriptor(
        intent_cls=ApplyVisualHealPatchIntent,
        handler=_h._handle_applyvisualhealpatch,
        effect_class=PRESENTATION, touches_layers=True, auto_checkpoint=True,
        lock_targets=_lt_visual_heal, provenance_detail=_pd_none,
    ))
    reg.register(IntentDescriptor(
        intent_cls=CheckpointIntent,
        handler=_h._handle_checkpoint,
        op_label="创建检查点",
    ))
    reg.register(IntentDescriptor(
        intent_cls=DuplicateComponentIntent,
        handler=_h._handle_duplicatecomponent,
        effect_class=PRESENTATION, op_label="复制组件",
        lock_targets=_lt_component_id, provenance_detail=_pd_none,
    ), body_cls=DuplicateComponentBody, body_codec=_codec_duplicate_component)
    reg.register(IntentDescriptor(
        intent_cls=InitProjectIntent,
        handler=_h._handle_initproject,
        touches_layers=True, op_label="初始化项目",
        provenance_detail=_pd_none,
    ))
    reg.register(IntentDescriptor(
        intent_cls=PatchComponentIntent,
        handler=_h._handle_patchcomponent,
        effect_class=PRESENTATION, op_label="调整组件",
        lock_targets=_lt_component_id, provenance_detail=_pd_none,
    ))
    reg.register(IntentDescriptor(
        intent_cls=PatchLayerPresentationIntent,
        handler=_h._handle_patchlayerpresentation,
        effect_class=PRESENTATION, touches_layers=True, auto_checkpoint=True,
        op_label="调整显隐/透明度", collab_event="presentation",
        collab_payload=_cp_presentation, provenance_detail=_pd_presentation,
        lock_targets=_lt_layer_id,
    ), body_cls=PatchLayerPresentationBody, body_codec=_codec_patch_layer_presentation)
    reg.register(IntentDescriptor(
        intent_cls=PatchLayerStyleIntent,
        handler=_h._handle_patchlayerstyle,
        # touches_layers=False：原 _layers_touching 元组不含本类（style
        # patch 构造新层 dict + 新 paint dict，绝不就地变更层，浅拷贝足够
        # —— 与基线行为逐字一致，不因语义直觉放宽 COW 判定）。
        effect_class=PRESENTATION, touches_layers=False, auto_checkpoint=True,
        op_label="修改图层样式", provenance_detail=_pd_none,
        lock_targets=_lt_layer_id,
    ), body_cls=PatchLayerStyleBody, body_codec=_codec_patch_layer_style)
    reg.register(IntentDescriptor(
        intent_cls=PatchWorkbenchDeltaIntent,
        handler=_h._handle_patchworkbenchdelta,
        collab_event="delta", collab_payload=_cp_workbench_delta,
        provenance_detail=_pd_none,
    ), body_cls=PatchWorkbenchDeltaBody, body_codec=_codec_patch_workbench_delta)
    reg.register(IntentDescriptor(
        intent_cls=RebindComponentIntent,
        handler=_h._handle_rebindcomponent,
        effect_class=PRESENTATION, op_label="重绑定组件",
        lock_targets=_lt_component_id, provenance_detail=_pd_none,
    ), body_cls=RebindComponentBody, body_codec=_codec_rebind_component)
    reg.register(IntentDescriptor(
        intent_cls=RemoveComponentIntent,
        handler=_h._handle_removecomponent,
        effect_class=PRESENTATION, op_label="移除组件",
        lock_targets=_lt_component_id, provenance_detail=_pd_remove_component,
    ), body_cls=RemoveComponentBody, body_codec=_codec_remove_component)
    reg.register(IntentDescriptor(
        intent_cls=RemoveLayerIntent,
        handler=_h._handle_removelayer,
        touches_layers=True, auto_checkpoint=True, op_label="移除图层",
        lock_targets=_lt_layer_id, provenance_detail=_pd_none,
    ), body_cls=RemoveLayerBody, body_codec=_codec_remove_layer)
    reg.register(IntentDescriptor(
        intent_cls=ReorderLayersIntent,
        handler=_h._handle_reorderlayers,
        touches_layers=True, auto_checkpoint=True, op_label="调整图层顺序",
        lock_targets=_lt_reorder_layers, provenance_detail=_pd_none,
    ), body_cls=ReorderLayersBody, body_codec=_codec_reorder_layers)
    reg.register(IntentDescriptor(
        intent_cls=RollbackIntent,
        handler=_h._handle_rollback,
        touches_layers=True, op_label="回滚",
        provenance_detail=_pd_none,
    ))
    reg.register(IntentDescriptor(
        intent_cls=RestoreStyleIntent,
        handler=_h._handle_restorestyle,
        touches_layers=True, op_label="恢复样式版本",
        lock_targets=_lt_restore_style, provenance_detail=_pd_none,
    ))
    reg.register(IntentDescriptor(
        intent_cls=SetBasemapIntent,
        handler=_h._handle_setbasemap,
        op_label="切换底图", provenance_detail=_pd_none,
    ))
    reg.register(IntentDescriptor(
        intent_cls=SetLayoutIntent,
        handler=_h._handle_setlayout,
        effect_class=PRESENTATION, op_label="调整版面",
        lock_targets=_lt_set_layout, provenance_detail=_pd_none,
    ), body_cls=SetLayoutBody, body_codec=_codec_set_layout)
    reg.register(IntentDescriptor(
        intent_cls=SetSceneIntent,
        handler=_h._handle_setscene,
        effect_class=PRESENTATION, provenance_detail=_pd_none,
    ))
    reg.register(IntentDescriptor(
        intent_cls=SetScenarioModeIntent,
        handler=_h._handle_setscenariomode,
        op_label="切换推演模式", provenance_detail=_pd_none,
    ))
    reg.register(IntentDescriptor(
        intent_cls=SetTimeIntent,
        handler=_h._handle_settime,
        op_label="调整时间维度", provenance_detail=_pd_none,
    ), body_cls=SetTimeBody, body_codec=_codec_set_time)
    reg.register(IntentDescriptor(
        intent_cls=SetViewIntent,
        handler=_h._handle_setview,
        op_label="调整视图", provenance_detail=_pd_none,
    ), body_cls=SetViewBody, body_codec=_codec_set_view)
    reg.register(IntentDescriptor(
        intent_cls=SetWorkbenchStateIntent,
        handler=_h._handle_setworkbenchstate,
        op_label="更新工作台组织", collab_event="doc",
        collab_payload=_cp_workbench_doc, provenance_detail=_pd_none,
    ), body_cls=SetWorkbenchStateBody, body_codec=_codec_set_workbench_state)
    reg.register(IntentDescriptor(
        intent_cls=UpsertLayerIntent,
        handler=_h._handle_upsertlayer,
        touches_layers=True, auto_checkpoint=True, op_label="挂载图层",
        lock_targets=_lt_upsert_layer, provenance_detail=_pd_none,
    ))
    reg.register(IntentDescriptor(
        intent_cls=UpsertSourceIntent,
        handler=_h._handle_upsertsource,
        auto_checkpoint=True, op_label="挂载数据源",
        provenance_detail=_pd_none,
    ))
    # InitProject / PatchComponent 的 Body codec（引擎类已登记，仅补
    # Body 映射面）。
    reg.register_body_codec(InitProjectBody, _codec_init_project)
    reg.register_body_codec(PatchComponentBody, _codec_patch_component)
    return reg


MUTATION_REGISTRY = _build_default_registry()
