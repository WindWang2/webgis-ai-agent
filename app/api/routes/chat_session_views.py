"""会话只读视图端点（自 api/routes/chat.py 拆分 —— god-module 棘轮收敛）。

一组同类的「会话恢复 / 工件读取 / 计划投影」端点：

- GET  /sessions/{id}/map-state        恢复用持久化地图状态（含指纹）
- GET  /sessions/{id}/chart-artifacts/{ref_id}   chart_panel ref 数据通道
- GET  /sessions/{id}/table-artifacts/{ref_id}   table_panel ref 数据通道
- GET  /sessions/{id}/plan             SessionPlan 信封只读投影
- POST /sessions/{id}/plans/{plan_id}/confirm    Tier-3 破坏性步骤批准（SEC-03）

路由不自带前缀（挂载方式：``chat.router.include_router(router)``，
继承 /chat 前缀与对话 tag —— 任何只挂 chat.router 的旧装配/测试自动
获得这组端点）；chat.py 保留 re-export（inspect.getsource 兼容）。
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Response

from app.models.db_model import Conversation
from app.schemas.chat_schema import (
    ChartArtifactResponse,
    PlanConfirmRequest,
    SessionMapStateResponse,
    SessionPlanStepView,
    SessionPlanViewResponse,
    TableArtifactResponse,
)
from app.services.auth_history_bridge import require_owned_session
from app.services.session_plan import load_session_plan

router = APIRouter()  # tags/前缀由挂载方（chat.router）提供，避免 include 时重复


@router.get("/sessions/{session_id}/map-state", response_model=SessionMapStateResponse)
async def get_session_map_state(
    session_id: str,
    _conv: Conversation = Depends(require_owned_session),
) -> dict:
    """Return persisted map state (viewport, layers) for session restoration.

    审计 S31：之前 _user 注入但未做所有权校验 —— 任何认证用户知道 session_id
    就能读取他人的 viewport/layers。复用 get_session_detail 的同款检查。
    SEC-08：匿名会话需匹配 owner_token。
    """
    from app.services.session_data import session_data_manager
    state = await session_data_manager.get_map_state(session_id)
    # Restoration may use a prior runtime observation only when it belongs to
    # the current authoritative desired state. Expose the bounded cartographic
    # fingerprint alongside the state so the browser never revives an older
    # MapSpec generation after a reload race.
    response_state = dict(state)
    # ADR-0180 review P2-1（窄化）：situation 内部态（快照 ≤64KB + 交互环）
    # 不随会话恢复下发 —— 前端无消费方，白添 ~80KB 恢复载荷。其余
    # _cartographic_* 键是前端 restore 契约的一部分，保持原样。
    # ADR-0214：视觉观察环内部态（截图 ref 索引 / recurrence 账本 / 修复
    # 提案）同为后端私有 —— 前端无消费方，不下发（ref+sha 级载荷虽无
    # 字节，restore 面无谓增重）。
    for _internal_key in ("_situation_snapshot", "_situation_interactions",
                          "_visual_screenshots", "_visual_observation_state",
                          "_visual_repair_proposals"):
        response_state.pop(_internal_key, None)
    # ADR-0225：Interaction IR ledger 三键（环 128 条 / 96KB 预算）+ 伴生
    # _updated_at 时间戳同为后端私有 —— 前端无消费方，不下发恢复面
    # （agent 消费走 GET interaction-events/projection 有界投影）。
    # （rebase #1616：map-state GET 迁至本模块，剥离面随之迁移。）
    for _ir_key in ("_interaction_ir_ledger", "_interaction_ir_clients",
                    "_interaction_ir_meta"):
        response_state.pop(_ir_key, None)
        response_state.pop(f"{_ir_key}_updated_at", None)
    mapspec = state.get("mapspec")
    if isinstance(mapspec, dict):
        from app.lib.cartography.quality_loop import cartographic_fingerprint

        response_state["_current_cartographic_fingerprint"] = (
            cartographic_fingerprint(mapspec)
        )
    return {"session_id": session_id, "map_state": response_state}


@router.get("/sessions/{session_id}/chart-artifacts/{ref_id}", response_model=ChartArtifactResponse)
async def get_session_chart_artifact(
    session_id: str,
    ref_id: str,
    _conv: Conversation = Depends(require_owned_session),
) -> dict:
    """chart_panel 的 ref-backed 数据通道（D2 ref 路径）。

    与 /layers/data/{ref}（GeoJSON 序列化面）不同，chart artifact 是
    {chart: ChartData} 小载荷（生成时已受 100KB/500 点上限约束）——
    直接 JSON 返回，无分块序列化需求。所有权校验复用 require_owned_session。
    """
    if not ref_id or not ref_id.startswith("ref:chart-") or len(ref_id) > 128:
        raise HTTPException(status_code=400, detail="非法 chart artifact ref")
    from app.services.session_data import session_data_manager

    res = await session_data_manager.get_ref_data(session_id, ref_id)
    if not res.success:
        status_code = 403 if res.error_type == "PermissionDenied" else 404
        raise HTTPException(status_code=status_code, detail=res.error or "图表数据不可用")
    data = res.data
    if isinstance(data, dict) and isinstance(data.get("chart"), dict):
        return data
    raise HTTPException(status_code=404, detail="ref 不是图表 artifact")


@router.get("/sessions/{session_id}/table-artifacts/{ref_id}", response_model=TableArtifactResponse)
async def get_session_table_artifact(
    session_id: str,
    ref_id: str,
    _conv: Conversation = Depends(require_owned_session),
) -> dict:
    """table_panel 的 ref-backed 数据通道（Runtime V4 §10）。

    与 chart-artifacts 同款纪律：小载荷 JSON 直返（存储侧已有界）。载荷
    形态宽容（{table:{columns,rows}} / 记录数组），边界规整在前端
    table-data 适配器完成 —— 服务端只做所有权与存在性。
    """
    # review 修复：前缀收紧到表族（chart 有专属端点；任意 ref:* 直通是
    # 纵深防御回退）。表族 = table/stats/grid/admin 前缀。
    _TABLE_REF_PREFIXES = ("ref:table-", "ref:stats-", "ref:grid-", "ref:admin-")
    if (
        not ref_id
        or len(ref_id) > 200
        or not ref_id.startswith(_TABLE_REF_PREFIXES)
    ):
        raise HTTPException(status_code=400, detail="非法 table artifact ref")
    from app.services.session_data import session_data_manager

    res = await session_data_manager.get_ref_data(session_id, ref_id)
    if not res.success:
        status_code = 403 if res.error_type == "PermissionDenied" else 404
        raise HTTPException(status_code=status_code, detail=res.error or "表格数据不可用")
    data = res.data
    if isinstance(data, dict) and isinstance(data.get("table"), (dict, list)):
        return data
    if isinstance(data, list):
        return {"table": data}
    raise HTTPException(status_code=404, detail="ref 不是表格 artifact")


@router.get("/sessions/{session_id}/plan", response_model=SessionPlanViewResponse)
async def get_session_plan(
    session_id: str,
    _conv: Conversation = Depends(require_owned_session),
) -> Response | dict:
    """当前 SessionPlan 信封投影（只读，#1047 —— Pi 路径面板水合源）。

    只返回 CURRENT_ALIAS 指向的当前信封 —— 历史信封（supersede 归档行）
    永不出现在响应里。无信封 → 204 显式空结果（同 map-state 写路由的
    空响应惯用法）："没有计划"是正常态，不是错误。信封存在但 GIS 章节
    为空（槽位已开、intent 未跑）→ query/plan_id/recipe_id 为 null 的
    显式空投影。CanonicalPlan（plan_* HUD）与此端点无关。
    """
    plan = await load_session_plan(session_id)
    if plan is None:
        return Response(status_code=204)
    gis = plan.gis_chapter
    # ADR-0180 additive：kernel 步骤行（无步骤 → None，前端零漂移）。
    steps_payload: Optional[list] = None
    if getattr(plan, "steps", None):
        steps_payload = []
        for s in plan.steps:
            latest = s.latest_evidence()
            steps_payload.append(SessionPlanStepView(
                id=s.id,
                goal=s.goal,
                capability=s.capability,
                tool=s.tool,
                status=s.status,
                depends_on=list(s.depends_on or []),
                attempts=s.attempts,
                ref=(latest.ref if latest else ""),
                host=s.host,
                turn_id=s.turn_id,
            ).model_dump())
    return {
        "session_id": plan.session_id,
        "envelope_id": plan.envelope_id,
        "user_goal": plan.user_goal,
        # 章节缺席时三字段为 null —— 前端据此区分「无章节」与「章节为空串」。
        "query": str(gis.get("query") or "") if gis else None,
        "plan_id": str(gis.get("plan_id") or "") if gis else None,
        "recipe_id": str(gis.get("recipe_id") or "") if gis else None,
        "progress": [row.model_dump() for row in plan.progress],
        "replaced": plan.replaced,
        "superseded": plan.superseded,
        "updated_at": plan.updated_at,
        "steps": steps_payload,
    }


@router.post("/sessions/{session_id}/plans/{plan_id}/confirm")
async def confirm_destructive_plan(
    session_id: str,
    plan_id: str,
    req: PlanConfirmRequest,
    _conv: Conversation = Depends(require_owned_session),
) -> dict:
    """SEC-03：会话所有者批准计划中的 Tier 3 破坏性步骤。

    仅会话所有者（登录 user_id 或 owner_token）可批准；挑战 id 来自
    execute_plan 的 CONFIRMATION_REQUIRED 响应。批准写入服务端计划
    payload 后，execute_plan 才会执行 tier-3 步骤 —— 模型自带的布尔
    参数不再是授权依据。
    """
    from app.services import plan_mode as plan_svc

    result = await plan_svc.approve_destructive_confirmation(
        session_id, plan_id, req.challenge_id
    )
    if not result.get("success"):
        code = result.get("code")
        status_code = 404 if code == "NOT_FOUND" else 409
        raise HTTPException(status_code=status_code, detail=result.get("message") or code)
    return result
