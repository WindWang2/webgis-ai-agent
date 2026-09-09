"""User-origin MapSpec mutations — thin adapter over apply_mutation (#639/#640)."""
from typing import Annotated, Any, Literal, Optional, Union

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.core.auth import require_owned_session
from app.models.db_model import Conversation
from app.services.distributed_lock import (
    LockContentionError,
    LockDegradedError,
    LockLostError,
)
from app.services.gis_harness.components import ComponentPlacement
from app.services.mapspec.lifecycle_engine import (
    InitProjectIntent,
    MapSpecLifecycleEngine,
    PatchComponentIntent,
    PatchLayerPresentationIntent,
    PatchLayerStyleIntent,
    RemoveComponentIntent,
    DuplicateComponentIntent,
    RebindComponentIntent,
    RemoveLayerIntent,
    ReorderLayersIntent,
    SetLayoutIntent,
    SetTimeIntent,
    SetViewIntent,
    SetWorkbenchStateIntent,
    PatchWorkbenchDeltaIntent,
)

router = APIRouter(prefix="/chat", tags=["对话"])
_engine = MapSpecLifecycleEngine()


class PatchLayerPresentationBody(BaseModel):
    intent: Literal["patch_layer_presentation"]
    expected_revision: int = Field(ge=0)
    layer_id: str = Field(min_length=1, max_length=200)
    visible: Optional[bool] = None
    opacity: Optional[float] = Field(default=None, ge=0.0, le=1.0)


class PatchLayerStyleBody(BaseModel):
    """#1077：spec 承载层的持久样式突变（用户样式面板 durable 通道）。

    paint 顶层键合并（部分更新）；族谓词与 presentation patch 一致。
    """

    intent: Literal["patch_layer_style"]
    expected_revision: int = Field(ge=0)
    layer_id: str = Field(min_length=1, max_length=200)
    paint: dict[str, Any] = Field(min_length=1)


class PatchComponentBody(BaseModel):
    """组件局部突变（UI 拖拽/缩放/折叠收尾提交）。

    placement 结构在边界即校验（非法布局 422 而非事务回滚）；
    enabled 支持『隐藏组件』；collapse 走 placement.collapsed。
    """

    intent: Literal["patch_component"]
    expected_revision: int = Field(ge=0)
    component_id: str = Field(min_length=1, max_length=128)
    enabled: Optional[bool] = None
    position: Optional[str] = None
    placement: Optional[ComponentPlacement] = None
    variant: Optional[str] = Field(None, max_length=64)
    style: Optional[dict[str, Any]] = None
    options: Optional[dict[str, Any]] = None
    upsert: bool = False


class SetViewBody(BaseModel):
    intent: Literal["set_view"]
    expected_revision: int = Field(ge=0)
    center: Optional[list[float]] = None
    zoom: Optional[float] = None
    pitch: Optional[float] = None
    bearing: Optional[float] = None


class RemoveLayerBody(BaseModel):
    intent: Literal["remove_layer"]
    expected_revision: int = Field(ge=0)
    layer_id: str = Field(min_length=1, max_length=200)


class RemoveComponentBody(BaseModel):
    """Component Lifecycle V3（Runtime V4 §18）：组件真删除（用户侧入口）。"""

    intent: Literal["remove_component"]
    expected_revision: int = Field(ge=0)
    component_id: str = Field(min_length=1, max_length=128)


class DuplicateComponentBody(BaseModel):
    """Component Lifecycle V3（§19）：复制多实例组件。"""

    intent: Literal["duplicate_component"]
    expected_revision: int = Field(ge=0)
    component_id: str = Field(min_length=1, max_length=128)
    new_id: Optional[str] = Field(None, min_length=1, max_length=128)


class RebindComponentBody(BaseModel):
    """Component Lifecycle V3（§19）：重绑定（chartRef/tableRef/layerId）。

    绑定字段在纯函数层按类型白名单校验；目标存在性由引擎事务内的
    pre-commit 守卫复核（layer 在场 / artifact ref 活性）。
    """

    intent: Literal["rebind_component"]
    expected_revision: int = Field(ge=0)
    component_id: str = Field(min_length=1, max_length=128)
    chart_ref: Optional[str] = Field(None, min_length=1, max_length=200)
    table_ref: Optional[str] = Field(None, min_length=1, max_length=200)
    layer_id: Optional[str] = Field(None, min_length=1, max_length=200)


class ReorderLayersBody(BaseModel):
    intent: Literal["reorder_layers"]
    expected_revision: int = Field(ge=0)
    layer_ids: list[str] = Field(min_length=1, max_length=128)


class SetLayoutBody(BaseModel):
    intent: Literal["set_layout"]
    expected_revision: int = Field(ge=0)
    legend: Optional[dict[str, Any]] = None
    controls: Optional[list[dict[str, Any]]] = None
    margins: Optional[dict[str, Any]] = None
    # CartographyComponent 列表（id/type/enabled/position/priority/style/options）
    components: Optional[list[dict[str, Any]]] = None


class SetTimeBody(BaseModel):
    intent: Literal["set_time"]
    expected_revision: int = Field(ge=0)
    enabled: Optional[bool] = None
    field: Optional[str] = None
    type: Optional[str] = None
    extent: Optional[list[Any]] = None
    current: Optional[Any] = None
    window: Optional[Any] = None
    playback: Optional[dict[str, Any]] = None
    step: Optional[float] = None
    speed: Optional[float] = None


class InitProjectBody(BaseModel):
    intent: Literal["init_project"]
    expected_revision: int = Field(ge=0)
    view: Optional[dict[str, Any]] = None


class SetWorkbenchStateBody(BaseModel):
    """Workbench V5 组织态持久化（分组树/成员/锁/模式；引擎内结构+256KB 校验）。

    V6：base_workbench_revision —— workbench 级 CAS（引擎比对存储 doc 的
    `_rev` 盖章）；提供且不一致 → 409 superseded（回灌当前 doc）。缺省 =
    V5 语义。
    """

    intent: Literal["patch_workbench_state"]
    expected_revision: int = Field(ge=0)
    doc: dict[str, Any]
    base_workbench_revision: Optional[int] = Field(default=None, ge=0)


class PatchWorkbenchDeltaBody(BaseModel):
    """Workbench V6 组织态**增量**补丁（绝对值语义；引擎内管线应用+全量校验）。

    见 app/services/collab/delta.py：setGroups（部分字段 create/patch）/
    removeGroupIds（级联）/ membershipSet / membershipClear / locksAdd /
    locksRemove。mode 不在 delta 域。patch ≤64KB、各列表 ≤2000。
    """

    intent: Literal["patch_workbench_delta"]
    expected_revision: int = Field(ge=0)
    delta: dict[str, Any]


UserMapSpecMutationRequest = Annotated[
    Union[
        PatchLayerPresentationBody,
        PatchLayerStyleBody,
        PatchComponentBody,
        SetViewBody,
        RemoveLayerBody,
        RemoveComponentBody,
        DuplicateComponentBody,
        RebindComponentBody,
        ReorderLayersBody,
        SetLayoutBody,
        SetTimeBody,
        InitProjectBody,
        SetWorkbenchStateBody,
        PatchWorkbenchDeltaBody,
    ],
    Field(discriminator="intent"),
]


@router.post("/sessions/{session_id}/mapspec/mutations")
async def apply_user_mapspec_mutation(
    session_id: str,
    req: UserMapSpecMutationRequest,
    include_review: bool = False,
    _conv: Conversation = Depends(require_owned_session),
) -> dict[str, Any]:
    if isinstance(req, PatchLayerStyleBody):
        intent = PatchLayerStyleIntent(
            layer_id=req.layer_id, paint=dict(req.paint),
        )
    elif isinstance(req, PatchLayerPresentationBody):
        if req.visible is None and req.opacity is None:
            raise HTTPException(
                status_code=400,
                detail="patch_layer_presentation requires visible and/or opacity",
            )
        intent = PatchLayerPresentationIntent(
            layer_id=req.layer_id,
            visible=req.visible,
            opacity=req.opacity,
        )
    elif isinstance(req, PatchComponentBody):
        if all(
            f is None
            for f in (req.enabled, req.position, req.placement, req.variant, req.style, req.options)
        ):
            raise HTTPException(
                status_code=400,
                detail="patch_component requires at least one mutation field",
            )
        intent = PatchComponentIntent(
            component_id=req.component_id,
            enabled=req.enabled,
            position=req.position,
            placement=req.placement.model_dump(exclude_none=True) if req.placement else None,
            variant=req.variant,
            style=req.style,
            options=req.options,
            upsert=req.upsert,
        )
    elif isinstance(req, SetViewBody):
        if (
            req.center is None
            and req.zoom is None
            and req.pitch is None
            and req.bearing is None
        ):
            raise HTTPException(
                status_code=400,
                detail="set_view requires center, zoom, pitch, and/or bearing",
            )
        intent = SetViewIntent(
            center=req.center,
            zoom=req.zoom,
            pitch=req.pitch,
            bearing=req.bearing,
        )
    elif isinstance(req, RemoveLayerBody):
        intent = RemoveLayerIntent(layer_id=req.layer_id)
    elif isinstance(req, RemoveComponentBody):
        intent = RemoveComponentIntent(component_id=req.component_id)
    elif isinstance(req, DuplicateComponentBody):
        intent = DuplicateComponentIntent(
            component_id=req.component_id, new_id=req.new_id,
        )
    elif isinstance(req, RebindComponentBody):
        bindings: dict[str, str] = {}
        if req.chart_ref:
            bindings["chartRef"] = req.chart_ref
        if req.table_ref:
            bindings["tableRef"] = req.table_ref
        if req.layer_id:
            bindings["layerId"] = req.layer_id
        if not bindings:
            raise HTTPException(
                status_code=400,
                detail="rebind_component requires chart_ref, table_ref, or layer_id",
            )
        intent = RebindComponentIntent(
            component_id=req.component_id, bindings=bindings,
        )
    elif isinstance(req, ReorderLayersBody):
        intent = ReorderLayersIntent(layer_ids=req.layer_ids)
    elif isinstance(req, SetLayoutBody):
        intent = SetLayoutIntent(
            legend=req.legend, controls=req.controls, margins=req.margins,
            components=req.components,
        )
    elif isinstance(req, SetTimeBody):
        intent = SetTimeIntent(
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
    elif isinstance(req, InitProjectBody):
        intent = InitProjectIntent(view=req.view)
    elif isinstance(req, SetWorkbenchStateBody):
        intent = SetWorkbenchStateIntent(
            doc=req.doc,
            base_workbench_revision=req.base_workbench_revision,
        )
    elif isinstance(req, PatchWorkbenchDeltaBody):
        intent = PatchWorkbenchDeltaIntent(delta=req.delta)
    else:
        raise HTTPException(status_code=400, detail="unsupported mapspec mutation intent")
    # GISWorldState 门面（C2）：语义与 engine.apply_mutation 一致，额外记录
    # provenance（用户决策链——user-wins 守卫与 reload 审计的依据）。
    from app.services.gis_world_state import apply_gis_mutation

    try:
        result = await apply_gis_mutation(
            session_id,
            intent,
            origin="user",
            actor="mapspec_route",
            expected_revision=req.expected_revision,
            engine=_engine,
        )
    except (TimeoutError, LockContentionError, LockDegradedError, LockLostError):
        # #1071: 用户在 agent 持锁（大栅格摄取可 >30s）期间切换可见度，
        # 等满获取预算后收到裸 500 —— 锁竞争是背压不是服务端故障，与
        # chat.py 全部同型点一致映射 503 + retry 指引。
        raise HTTPException(
            status_code=503,
            detail={
                "error": "session_busy",
                "message": "会话正被 Agent 操作占用，请稍后重试。",
            },
        )
    except (LockDegradedError, LockLostError):
        # v2(audit F2): engine fail-closed 抛出（#1071 引入）此前无路由
        # 捕获 → 裸 500。同 503 语义：状态未写，客户端重读后重试安全。
        raise HTTPException(
            status_code=503,
            detail={
                "error": "session_lock_unavailable",
                "message": "会话锁暂不可用（分布式所有权无法证明），状态未修改，请稍后重试。",
            },
        )
    payload = result.to_dict()
    if not include_review:
        # #732: the user chrome route fires per slider tick — the frontend
        # consumers (user-mutation.ts) read only mutation_revision + mapspec;
        # the full cartographic_review (checks/attempts/repair payloads) and
        # findings were re-serialized on every toggle for nothing. Agent-path
        # evidence (tool results) is unaffected; opt back in with
        # ?include_review=true.
        payload.pop("cartographic_review", None)
        payload.pop("cartography_findings", None)
    if result.superseded:
        raise HTTPException(status_code=409, detail=payload)
    if result.is_error:
        raise HTTPException(status_code=400, detail=payload)
    return payload


@router.get("/sessions/{session_id}/workbench/state")
async def get_workbench_state(
    session_id: str,
    meta: bool = Query(default=False, description="仅返回 revision（轻量对账探测）"),
    _conv: Conversation = Depends(require_owned_session),
) -> dict[str, Any]:
    """Workbench 组织态权威读（V6 协作对账通道）。

    与 map-state GET 的区别：**新鲜读** —— 先失效进程内 L1（2s 陈旧窗口）
    再读，重连/对账路径不得回灌陈旧基线（R1-M2）；meta=1 只返回 revision
    （定向单字段读，不物化 1MiB 级 mapspec）。doc 含服务端 `_rev` 盖章
    （workbench 级 CAS 锚点）。
    """
    from app.services.session_data import session_data_manager

    invalidate = getattr(session_data_manager, "invalidate_local_cache", None)
    if callable(invalidate):
        invalidate(session_id)
    if meta:
        get_field = getattr(session_data_manager, "get_state_field", None)
        raw_rev = (
            await get_field(session_id, "_cartographic_mutation_revision")
            if callable(get_field)
            else None
        )
        try:
            revision = int(raw_rev or 0)
        except (TypeError, ValueError):
            revision = 0
        return {"session_id": session_id, "revision": revision}
    pre_state = await session_data_manager.get_map_state(session_id)
    try:
        revision = int(pre_state.get("_cartographic_mutation_revision", 0))
    except (TypeError, ValueError):
        revision = 0
    # 引擎权威读（含磁盘复活路径；Redis 过期时 spec 从盘上恢复）。
    loaded = await _engine.store.get_mapspec(session_id, state_hint=pre_state)
    workbench = loaded.get("workbench") if isinstance(loaded, dict) else None
    return {
        "session_id": session_id,
        "revision": revision,
        "doc": workbench if isinstance(workbench, dict) else None,
    }


@router.get("/sessions/{session_id}/workbench/artifact-status")
async def get_workbench_artifact_status(
    session_id: str,
    _conv: Conversation = Depends(require_owned_session),
) -> dict[str, Any]:
    """工作台 artifact/workflow 感知投影（V6 Must-have H；派生投影，零新真相）。

    来源（既有权威）：
    - artifact_registry（会话产物台账）：status（valid/stale/superseded/…）、
      producer_capability/producer_node（工作流节点）、inputs（血缘边）、replaces；
    - 有界：artifacts ≤200（stale 优先，其后按 updated_at 倒序）；metadata 不透出。

    前端以 layer._refId == artifact_id 直接 join —— stale/updated 徽标与
    「为何变化」（inputs 血缘 + producer 节点）入口。跨浏览器刷新由总线
    ``artifact`` 事件（ref_lifecycle 失效路径发布）驱动。
    """
    from app.services.artifact_registry import list_artifacts

    records = await list_artifacts(session_id)
    items = [
        {
            "artifactId": r.artifact_id,
            "type": r.artifact_type,
            "status": r.status,
            "producerCapability": r.producer_capability,
            "producerNode": r.producer_node,
            "producerTool": r.producer_tool,
            "inputs": list(r.inputs)[:8],
            "replaces": r.replaces,
            "revision": r.revision,
            "updatedAt": r.updated_at,
        }
        for r in records
    ]
    stale_first = sorted(
        items,
        key=lambda it: (
            0 if it["status"] not in ("valid",) else 1,
            -(it["updatedAt"] or 0.0),
        ),
    )[:200]
    return {
        "session_id": session_id,
        "artifacts": stale_first,
        "staleCount": sum(1 for it in items if it["status"] not in ("valid",)),
        "total": len(items),
    }
