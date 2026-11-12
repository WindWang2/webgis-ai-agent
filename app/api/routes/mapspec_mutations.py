"""User-origin MapSpec mutations — thin adapter over apply_mutation (#639/#640)."""
from typing import Any, Dict, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.services.auth_history_bridge import require_owned_session
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
    UndoAgentCommitBody,
    MutationApplyResponse,
    UserMapSpecMutationRequest,
    WorkbenchArtifactStatusResponse,
    WorkbenchStateResponse,
)
from app.services.mapspec.lifecycle_engine import MapSpecLifecycleEngine

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
    # Body→Intent 映射统一走 intent_codec（ADR-0203：与 review proposal
    # merge 回放共用同一映射源，杜绝两套语义漂移）；ValueError = 原 400 文案。
    from app.services.mapspec.intent_codec import body_to_intent

    try:
        intent = body_to_intent(req)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    # GISWorldState 门面（C2）：语义与 engine.apply_mutation 一致，额外记录
    # provenance（用户决策链——user-wins 守卫与 reload 审计的依据）。
    # 方向 8（ADR-0257）：客户端幂等键 `c:<client_mutation_id>` —— 引擎锁内
    # 去重，同 id 重放（弱网重试）返回已提交世代而非二次执行；缺席时门面
    # 铸造服务端 id（行为与 master 一致）。
    from app.services.gis_world_state import apply_gis_mutation


    client_mutation_id = getattr(req, "client_mutation_id", None)
    try:
        result = await apply_gis_mutation(
            session_id,
            intent,
            origin="user",
            actor="mapspec_route",
            expected_revision=req.expected_revision,
            engine=_engine,
            mutation_id=(f"c:{client_mutation_id}" if client_mutation_id else None),
            client_optimistic_id=client_mutation_id,
        )
    except (TimeoutError, LockContentionError):
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
        # API-09：此前本分支被上一 except 的元组包含而不可达 —— 现将
        # 竞争类与锁不可用类分开，session_lock_unavailable 可达。
        raise HTTPException(
            status_code=503,
            detail={
                "error": "session_lock_unavailable",
                "message": "会话锁暂不可用（分布式所有权无法证明），状态未修改，请稍后重试。",
            },
        )
    payload = result.to_dict()
    # 方向 8：信封回声（client_mutation_id 原样返回供前端对账）。
    if client_mutation_id:
        payload["client_mutation_id"] = client_mutation_id
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


# ── G27：按尺度表达（UI 与 Agent 共用同一 service / 同一 CAS 链）──────────


class ScalePortrayalApplyBody(BaseModel):
    layer: str = Field(..., max_length=128)
    policy: Optional[Dict[str, Any]] = None
    mode: Literal["preview", "commit"] = "preview"
    expected_revision: Optional[int] = Field(None, ge=0)
    operation_id: Optional[str] = Field(None, max_length=64)
    note: Optional[str] = Field(None, max_length=160)


@router.get("/sessions/{session_id}/scale-portrayal")
async def get_scale_portrayal(
    session_id: str,
    layer: str = Query(..., max_length=128),
    observed_zoom: Optional[float] = Query(None, ge=0, le=24),
    crs: str = Query("EPSG:3857", max_length=32),
    target_dpi: float = Query(96.0, ge=1, le=600),
    _conv: Conversation = Depends(require_owned_session),
) -> dict[str, Any]:
    """只读有效策略（与 UI / Agent 同一解析面）；session 归属验权。"""
    from app.services.gis_harness.scale_portrayal.service import (
        ScalePortrayalError,
        inspect_scale_portrayal,
    )
    try:
        return await inspect_scale_portrayal(
            session_id, layer,
            observed_zoom=observed_zoom, crs=crs, target_dpi=target_dpi)
    except ScalePortrayalError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/sessions/{session_id}/scale-portrayal")
async def apply_scale_portrayal_route(
    session_id: str,
    req: ScalePortrayalApplyBody,
    _conv: Conversation = Depends(require_owned_session),
) -> dict[str, Any]:
    """局部受控写：preview 零突变 / commit 走既有 CAS 与 undo 栈。"""
    from app.services.gis_harness.scale_portrayal.service import (
        ScalePortrayalError,
        apply_scale_portrayal,
    )
    try:
        return await apply_scale_portrayal(
            session_id, req.layer,
            policy=req.policy,
            mode=req.mode,
            expected_revision=req.expected_revision,
            operation_id=req.operation_id,
            note=req.note or "",
            origin="user",
            actor="mapspec_route",
        )
    except ScalePortrayalError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
