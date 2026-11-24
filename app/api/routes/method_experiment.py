"""方法实验工作台 HTTP 面（WP5）—— UI 与 Pi 工具共用同一领域服务。

同链纪律：路由只做会话归属守卫（``require_owned_session``）与
载荷校验，业务语义全部走 ``method_experiment.service``（与
``webgis_method_experiment_*`` 工具面同一实现）——普通 UI 与 Agent
的参数契约、授权、审计完全一致，零分叉。

有界载荷：试验/排名投影沿用 evaluate.to_bounded_dict / state 投影，
FeatureCollection 本体绝不进响应（数据一律 ref 直通）。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.models.db_model import Conversation
from app.services.auth_history_bridge import require_owned_session
from app.services.gis_harness.method_experiment.evaluate import (
    evaluate_experiment,
)
from app.services.gis_harness.method_experiment.plan import (
    DEFAULT_MAX_TRIALS,
    PlanCompileError,
    compile_experiment_plan,
)
from app.services.gis_harness.method_experiment.service import (
    ensure_state_for_plan,
    load_state,
    record_adoption,
    rebuild_plan,
    run_window,
)

router = APIRouter(prefix="/method-experiment", tags=["方法实验工作台"])

#: typed 拒绝 → HTTP 状态映射（错误契约与仓库 http_status 家族对齐）。
_DECLINE_STATUS = {
    "MEXP_EXPERIMENT_NOT_FOUND": 404,
    "MEXP_TRIAL_NOT_FOUND": 404,
    "MEXP_ID_COLLISION": 409,
    "MEXP_PLAN_SNAPSHOT_CORRUPT": 409,
    "MEXP_PLAN_MISMATCH": 409,
    "MEXP_REGISTRY_UNAVAILABLE": 503,
    "MEXP_TRIAL_NOT_COMPLETED": 409,
}


def _decline(code: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=_DECLINE_STATUS.get(code, 400),
        detail={"reason_code": code, "message": message})


# ── 请求体 ──────────────────────────────────────────────────────────────

class InspectBody(BaseModel):
    query: str = Field(default="", max_length=300)
    family_id: str = Field(default="", max_length=64)


class PlanBody(BaseModel):
    family_id: str = Field(min_length=1, max_length=64)
    input_ref: str = Field(min_length=1, max_length=128)
    value_field: str = Field(default="", max_length=64)
    extra_inputs: Dict[str, str] = Field(default_factory=dict)
    candidate_ids: List[str] = Field(default_factory=list, max_length=6)
    scheme: str = Field(default="", max_length=32)
    folds: int = Field(default=5, ge=2, le=10)
    max_trials: int = Field(default=DEFAULT_MAX_TRIALS, ge=1, le=24)
    title: str = Field(default="", max_length=80)


class RunBody(BaseModel):
    experiment_id: str = Field(min_length=1, max_length=96)
    max_trials_per_call: int = Field(default=6, ge=1, le=6)


class AdoptBody(BaseModel):
    experiment_id: str = Field(min_length=1, max_length=96)
    trial_id: str = Field(min_length=1, max_length=16)
    reason: str = Field(default="", max_length=200)


# ── 路由 ────────────────────────────────────────────────────────────────

@router.post("/sessions/{session_id}/inspect")
async def inspect_method_cards(
    session_id: str,
    body: InspectBody,
    conv: Conversation = Depends(require_owned_session),
) -> Dict[str, Any]:
    """只读：方法卡/候选空间/协议建议/预算默认（与 inspect 工具同实现）。"""
    from app.services.gis_harness.method_experiment.cards import (
        EXPERIMENT_FAMILY_IDS,
        all_cards,
        match_family_by_question,
    )
    from app.services.gis_harness.method_experiment.plan import (
        MAX_CANDIDATES,
        MAX_TRIALS_HARD_CAP,
        MAX_VARIANTS_PER_CANDIDATE,
    )
    from app.services.gis_harness.method_experiment.protocol import (
        CV_SCHEMES,
    )

    matches = match_family_by_question(body.query) if body.query else []
    resolved = ""
    if body.family_id:
        if body.family_id not in EXPERIMENT_FAMILY_IDS:
            raise HTTPException(
                status_code=404,
                detail={"reason_code": "MEXP_UNKNOWN_FAMILY",
                        "message": f"未知族 {body.family_id}",
                        "alternatives": list(EXPERIMENT_FAMILY_IDS)})
        resolved = body.family_id
    elif matches:
        resolved = matches[0]

    cards = []
    for card in all_cards():
        if resolved and card.family_id != resolved:
            continue
        cards.append({
            "family_id": card.family_id,
            "label_zh": card.label_zh,
            "protocol_kind": card.protocol_kind,
            "default_scheme": card.default_scheme,
            "default_folds": card.default_folds,
            "default_metrics": list(card.default_metrics),
            "requires_value_field": card.requires_value_field,
            "extra_inputs": list(card.extra_inputs),
            "comparability_notes": list(card.comparability_notes)[:4],
            "candidates": [
                {
                    "candidate_id": c.candidate_id,
                    "method_id": c.method_id,
                    "eval_tool": c.eval_tool,
                    "label_zh": c.label_zh,
                    "sample_floor": c.sample_floor,
                    "param_specs": {
                        name: {
                            "role": spec.role, "unit": spec.unit,
                            "default": spec.default,
                            "grid": list(spec.grid_values()),
                            "minimum": spec.minimum,
                            "maximum": spec.maximum,
                            "note": spec.note[:80],
                        }
                        for name, spec in c.param_specs.items()},
                    "metric_ids": list(c.metric_ids),
                    "limitations": list(c.limitations)[:3],
                    "default_variants": len(c.expand_grid()),
                }
                for c in card.candidates],
        })
    return {
        "query_matched_families": matches[:5],
        "resolved_family": resolved or None,
        "family_vocab": list(EXPERIMENT_FAMILY_IDS),
        "cards": cards,
        "budget_defaults": {
            "max_trials": DEFAULT_MAX_TRIALS,
            "max_trials_hard_cap": MAX_TRIALS_HARD_CAP,
            "max_candidates": MAX_CANDIDATES,
            "max_variants_per_candidate": MAX_VARIANTS_PER_CANDIDATE,
            "cv_schemes": list(CV_SCHEMES),
        },
    }


@router.post("/sessions/{session_id}/plan")
async def plan_experiment(
    session_id: str,
    body: PlanBody,
    conv: Conversation = Depends(require_owned_session),
) -> Dict[str, Any]:
    """编译实验计划（typed 预检；同链 ensure_state_for_plan）。"""
    try:
        plan = compile_experiment_plan(
            family_id=body.family_id,
            input_ref=body.input_ref,
            value_field=body.value_field,
            extra_inputs=body.extra_inputs,
            candidate_ids=body.candidate_ids or None,
            scheme=body.scheme,
            folds=body.folds,
            max_trials=body.max_trials,
            title=body.title,
        )
    except PlanCompileError as exc:
        raise HTTPException(
            status_code=400,
            detail={"reason_code": exc.code, "message": str(exc)}) from exc
    _, decline = await ensure_state_for_plan(session_id, plan)
    if decline:
        raise _decline(decline, "实验 id 已被不同内容计划占用（指纹失配）")
    return {"plan": plan.to_bounded_summary()}


@router.post("/sessions/{session_id}/run")
async def run_experiment(
    session_id: str,
    body: RunBody,
    conv: Conversation = Depends(require_owned_session),
) -> Dict[str, Any]:
    """执行一个有界 run 窗口（同链 run_window：锁内 reload→dispatch→持久）。"""
    state, decline = await run_window(
        session_id, body.experiment_id,
        max_trials_per_call=body.max_trials_per_call)
    if state is None:
        raise _decline(decline, {
            "MEXP_EXPERIMENT_NOT_FOUND": "实验不存在——先 plan",
            "MEXP_PLAN_SNAPSHOT_CORRUPT": "计划快照不可解析（需重新 plan）",
            "MEXP_PLAN_MISMATCH": "计划快照与实验 id 不符",
            "MEXP_REGISTRY_UNAVAILABLE": "分发服务不可用",
        }.get(decline, decline))
    return {
        "experiment_id": state.experiment_id,
        "status": state.status,
        "counts": state.counts(),
        "total_wall_ms": state.total_wall_ms(),
        "trials": [
            {"trial_id": t.trial_id, "candidate_id": t.candidate_id,
             "state": t.state, "metrics": dict(t.metrics),
             "reason_code": t.reason_code}
            for t in state.trials.values()],
        "run_log_tail": state.run_log[-6:],
    }


@router.get("/sessions/{session_id}/status/{experiment_id}")
async def experiment_status(
    session_id: str,
    experiment_id: str,
    conv: Conversation = Depends(require_owned_session),
) -> Dict[str, Any]:
    """只读状态投影（诚实计数 + 真实成本 + 采纳回执）。"""
    state = await load_state(session_id, experiment_id)
    if state is None:
        raise _decline("MEXP_EXPERIMENT_NOT_FOUND",
                       f"实验 {experiment_id} 不存在")
    return {
        "experiment_id": experiment_id,
        "status": state.status,
        "counts": state.counts(),
        "total_wall_ms": state.total_wall_ms(),
        "adoption": state.adoption.model_dump() if state.adoption else None,
        "trials": [
            {"trial_id": t.trial_id, "candidate_id": t.candidate_id,
             "variant_index": t.variant_index, "params": dict(t.params),
             "state": t.state, "metrics": dict(t.metrics),
             "basis": {k: dict(v) for k, v in
                       list(t.metric_basis.items())[:4]},
             "reason_code": t.reason_code, "message": t.message[:160],
             "wall_ms": t.wall_ms, "attempts": t.attempts}
            for t in state.trials.values()],
        "run_log_tail": state.run_log[-8:],
    }


@router.get("/sessions/{session_id}/compare/{experiment_id}")
async def compare_experiment(
    session_id: str,
    experiment_id: str,
    conv: Conversation = Depends(require_owned_session),
) -> Dict[str, Any]:
    """评价投影（可比组排名 + Pareto + 推荐或弃权；同链 evaluate）。"""
    state = await load_state(session_id, experiment_id)
    if state is None:
        raise _decline("MEXP_EXPERIMENT_NOT_FOUND",
                       f"实验 {experiment_id} 不存在")
    plan, decline = rebuild_plan(state)
    if plan is None:
        raise _decline(decline, "计划快照不可解析或与实验 id 不符")
    evaluation = evaluate_experiment(plan, state)
    return {"evaluation": evaluation.to_bounded_dict()}


@router.post("/sessions/{session_id}/adopt")
async def adopt_experiment(
    session_id: str,
    body: AdoptBody,
    conv: Conversation = Depends(require_owned_session),
) -> Dict[str, Any]:
    """记录用户采纳决定（decision receipt；不自动改地图）。"""
    receipt, decline = await record_adoption(
        session_id, body.experiment_id, body.trial_id, body.reason)
    if receipt is None:
        raise _decline(decline, {
            "MEXP_EXPERIMENT_NOT_FOUND": "实验不存在",
            "MEXP_TRIAL_NOT_FOUND": "采纳目标试验不存在",
            "MEXP_TRIAL_NOT_COMPLETED": "采纳目标试验未完成——未评估的候选"
                                        "绝不进入采纳回执",
            "MEXP_PLAN_SNAPSHOT_CORRUPT": "计划快照不可解析",
        }.get(decline, decline))
    return {"adoption": receipt.model_dump()}
