"""survey_sampling routes — 调查工作台 HTTP 面（与 Pi 工具同一 service）。

全部端点要求会话归属（``require_owned_session``）；owner_token 经
X-Session-Token 注入。REST 与 Pi 双入口共用
``app.services.survey_sampling.service``——同权限、同状态、同证据
（goal §2 双入口硬约束）。
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query

from app.core.auth import actor_ids, get_current_user_optional
from app.models.db_model import Conversation
from app.schemas.survey_sampling_schema import (
    AdjudicateIn,
    DesignPreviewIn,
    DesignPreviewResponse,
    EstimateResponse,
    EstimatesListResponse,
    FrameBuildIn,
    FrameListResponse,
    ImportIn,
    ImportResponse,
    ProgressResponse,
    RecordIn,
    RecordResponse,
    SurveyCreateIn,
    SurveyCreateResponse,
    SurveyDetailResponse,
    SurveyFrameResponse,
    SurveyListResponse,
)
from app.services.auth_history_bridge import require_owned_session
from app.services.survey_sampling import service
from app.services.survey_sampling.contracts import SurveySamplingError

router = APIRouter()


def _fail(exc: SurveySamplingError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=exc.to_payload())


def _scope(session_id: str, project_id: Optional[str]) -> dict:
    """owner scope：恰好一维（project 优先支持项目级调查）。"""
    if project_id:
        return {"project_id": project_id}
    return {"session_id": session_id}


@router.post("/survey/frame", tags=["抽样调查"], response_model=SurveyFrameResponse)
async def build_frame(
    body: FrameBuildIn,
    session_id: str = Query(..., min_length=8, max_length=128),
    owner_token: Optional[str] = Header(None, alias="X-Session-Token"),
    _conv: Conversation = Depends(require_owned_session),
):
    try:
        payload = await service.build_survey_frame(
            session_id, owner_token, _scope(session_id, None),
            source_ref=body.source_ref, band=body.band,
            class_dictionary=body.class_dictionary)
        return SurveyFrameResponse(payload=payload)
    except SurveySamplingError as exc:
        raise _fail(exc) from exc


@router.get("/survey/frames", tags=["抽样调查"], response_model=FrameListResponse)
async def list_frames(
    session_id: str = Query(..., min_length=8, max_length=128),
    _conv: Conversation = Depends(require_owned_session),
):
    from app.services.survey_sampling import store

    return FrameListResponse(
        frames=store.list_frame_payloads({"session_id": session_id}))


@router.post("/survey/design/preview", tags=["抽样调查"],
             response_model=DesignPreviewResponse)
async def preview_design(
    body: DesignPreviewIn,
    session_id: str = Query(..., min_length=8, max_length=128),
    _conv: Conversation = Depends(require_owned_session),
):
    try:
        preview = service.preview_allocation(
            {"session_id": session_id}, body.frame_digest,
            strategy=body.strategy, n_total=body.n_total,
            target_se_oa=body.target_se_oa,
            prior_precision=body.prior_precision)
        return DesignPreviewResponse(preview=preview)
    except SurveySamplingError as exc:
        raise _fail(exc) from exc


@router.post("/survey/create", tags=["抽样调查"], response_model=SurveyCreateResponse)
async def create_survey(
    body: SurveyCreateIn,
    session_id: str = Query(..., min_length=8, max_length=128),
    user: Optional[dict] = Depends(get_current_user_optional),
    _conv: Conversation = Depends(require_owned_session),
):
    try:
        result = await service.create_survey(
            {"session_id": session_id}, body.frame_digest,
            objective=body.objective, target_time=body.target_time,
            strategy=body.strategy, n_total=body.n_total,
            target_se_oa=body.target_se_oa,
            prior_precision=body.prior_precision, seed=body.seed,
            backup_per_stratum=body.backup_per_stratum,
            blind_labelling=body.blind_labelling,
            max_nonresponse_rate=body.max_nonresponse_rate,
            notes=body.notes,
        )
        return SurveyCreateResponse(**result)
    except SurveySamplingError as exc:
        raise _fail(exc) from exc


@router.get("/survey/{survey_id}", tags=["抽样调查"],
            response_model=SurveyDetailResponse)
async def survey_detail(
    survey_id: str,
    session_id: str = Query(..., min_length=8, max_length=128),
    blind: Optional[bool] = Query(None),
    _conv: Conversation = Depends(require_owned_session),
):
    try:
        doc = service.survey_detail({"session_id": session_id}, survey_id,
                                    blind=blind)
        return SurveyDetailResponse(survey=doc)
    except SurveySamplingError as exc:
        raise _fail(exc) from exc


@router.get("/survey/{survey_id}/progress", tags=["抽样调查"],
            response_model=ProgressResponse)
async def survey_progress(
    survey_id: str,
    session_id: str = Query(..., min_length=8, max_length=128),
    _conv: Conversation = Depends(require_owned_session),
):
    try:
        return ProgressResponse(
            progress=service.survey_progress({"session_id": session_id},
                                             survey_id))
    except SurveySamplingError as exc:
        raise _fail(exc) from exc


@router.post("/survey/{survey_id}/records", tags=["抽样调查"],
             response_model=RecordResponse)
async def record_reference(
    survey_id: str,
    body: RecordIn,
    session_id: str = Query(..., min_length=8, max_length=128),
    user: Optional[dict] = Depends(get_current_user_optional),
    _conv: Conversation = Depends(require_owned_session),
):
    try:
        result = await service.record_reference(
            {"session_id": session_id}, survey_id,
            sample_id=body.sample_id, status=body.status,
            reference_class=body.reference_class,
            evidence_type=body.evidence_type,
            evidence_note=body.evidence_note, quality=body.quality,
            interpreter=body.interpreter, observed_at=body.observed_at,
            note=body.note, actor=actor_ids(user)[0] or "user",
            expected_revision=body.expected_revision)
        return RecordResponse(result=result)
    except SurveySamplingError as exc:
        raise _fail(exc) from exc


@router.post("/survey/{survey_id}/records/import", tags=["抽样调查"],
             response_model=ImportResponse)
async def import_records(
    survey_id: str,
    body: ImportIn,
    session_id: str = Query(..., min_length=8, max_length=128),
    _conv: Conversation = Depends(require_owned_session),
):
    try:
        result = await service.import_records(
            {"session_id": session_id}, survey_id, body.rows,
            expected_revision=body.expected_revision)
        return ImportResponse(result=result)
    except SurveySamplingError as exc:
        raise _fail(exc) from exc


@router.post("/survey/{survey_id}/adjudicate", tags=["抽样调查"],
             response_model=RecordResponse)
async def adjudicate(
    survey_id: str,
    body: AdjudicateIn,
    session_id: str = Query(..., min_length=8, max_length=128),
    user: Optional[dict] = Depends(get_current_user_optional),
    _conv: Conversation = Depends(require_owned_session),
):
    try:
        result = await service.adjudicate(
            {"session_id": session_id}, survey_id,
            sample_id=body.sample_id, reference_class=body.reference_class,
            actor=actor_ids(user)[0] or "user",
            expected_revision=body.expected_revision, note=body.note)
        return RecordResponse(result=result)
    except SurveySamplingError as exc:
        raise _fail(exc) from exc


@router.post("/survey/{survey_id}/estimate", tags=["抽样调查"],
             response_model=EstimateResponse)
async def estimate(
    survey_id: str,
    session_id: str = Query(..., min_length=8, max_length=128),
    user: Optional[dict] = Depends(get_current_user_optional),
    _conv: Conversation = Depends(require_owned_session),
):
    try:
        payload = await service.estimate_survey(
            {"session_id": session_id}, survey_id,
            actor=actor_ids(user)[0] or "user")
        return EstimateResponse(estimate=payload)
    except SurveySamplingError as exc:
        raise _fail(exc) from exc


@router.get("/survey/{survey_id}/estimates", tags=["抽样调查"],
            response_model=EstimatesListResponse)
async def list_estimates(
    survey_id: str,
    session_id: str = Query(..., min_length=8, max_length=128),
    _conv: Conversation = Depends(require_owned_session),
):
    from app.services.survey_sampling import store

    return EstimatesListResponse(
        estimates=store.list_estimates({"session_id": session_id}, survey_id))


@router.get("/surveys", tags=["抽样调查"], response_model=SurveyListResponse)
async def list_surveys(
    session_id: str = Query(..., min_length=8, max_length=128),
    _conv: Conversation = Depends(require_owned_session),
):
    return SurveyListResponse(
        surveys=service.list_surveys({"session_id": session_id}))
