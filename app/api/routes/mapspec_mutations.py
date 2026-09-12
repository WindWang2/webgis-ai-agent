"""User-origin MapSpec mutations — thin adapter over apply_mutation (#639/#640)."""
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from app.core.auth import require_owned_session
from app.models.db_model import Conversation
from app.services.distributed_lock import (
    LockContentionError,
    LockDegradedError,
    LockLostError,
)
from app.schemas.mapspec_mutation_schema import (  # noqa: F401 - 模块属性保持
    DuplicateComponentBody,
    InitProjectBody,
    MapSpecMutationUnion,
    PatchComponentBody,
    PatchLayerPresentationBody,
    PatchLayerStyleBody,
    PatchWorkbenchDeltaBody,
    RebindComponentBody,
    RemoveComponentBody,
    RemoveLayerBody,
    ReorderLayersBody,
    SetLayoutBody,
    SetTimeBody,
    SetViewBody,
    SetWorkbenchStateBody,
    MutationApplyResponse,
    UserMapSpecMutationRequest,
    WorkbenchArtifactStatusResponse,
    WorkbenchStateResponse,
)
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


@router.post(
    "/sessions/{session_id}/mapspec/mutations",
    response_model=MutationApplyResponse,
)
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
    # Workflow Runtime V5（fail-open；Epic workflow-v5）：呈现态突变 →
    # style 维变更（科学子图零触碰）。附加事实通道，失败不影响本响应。
    try:
        from app.services.workflow_runtime.hooks import record_style_change_safe

        target = getattr(intent, "layer_id", "") or getattr(
            intent, "component_id", "")
        await record_style_change_safe(session_id, target=str(target)[:64])
    except Exception:  # noqa: BLE001 — 附加事实通道
        pass
    return payload


@router.get("/sessions/{session_id}/workbench/state", response_model=WorkbenchStateResponse)
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

    # R2-M-5：meta 分支不做 L1 失效 —— get_state_field 直连 HGET 天然新鲜；
    # 失效会把热路径 map_state 一并打穿（对账轮询的读放大防护）。
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


@router.get(
    "/sessions/{session_id}/workbench/artifact-status",
    response_model=WorkbenchArtifactStatusResponse,
)
async def get_workbench_artifact_status(
    session_id: str,
    _conv: Conversation = Depends(require_owned_session),
) -> dict[str, Any]:
    """工作台 artifact/workflow 感知投影（V6 Must-have H；派生投影，零新真相）。

    SEC-KG-01：经 collab service 缝（artifact_status）只读投影 —— 路由不直调
    产物注册表。字段与有界性见 ``collab/artifact_status.py``。
    """
    from app.services.collab.artifact_status import build_artifact_status

    return await build_artifact_status(session_id)
