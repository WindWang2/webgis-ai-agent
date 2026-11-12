"""G30 技能作业 API —— 工作台（UI）与 Pi @tool 共享同一业务契约。

端点（全部 owner-scoped；session 资源经 verify_session_owner 归属验证，
无权一律 404 不泄露存在性）：

- ``GET  /api/v1/skill-jobs/catalog``               分类型目录（runnable/…）
- ``GET  /api/v1/skill-jobs/skills/{skill_id}``     单技能详情（参数 schema/绑定需求）
- ``POST /api/v1/skill-jobs/prepare``               零副作用 prepared call + 预览
- ``POST /api/v1/skill-jobs/start``                 启动运行（幂等；busy 会话 409）
- ``GET  /api/v1/skill-jobs/runs``                  会话运行列表（刷新回看）
- ``GET  /api/v1/skill-jobs/runs/{job_id}``         单运行视图（含 run 记录）
- ``POST /api/v1/skill-jobs/runs/{job_id}/cancel``  经既有 durable 取消面
- 预设：``GET/POST /api/v1/skill-jobs/presets``、
  ``GET/PUT/DELETE /api/v1/skill-jobs/presets/{id}``、
  ``POST /api/v1/skill-jobs/presets/{id}/prepare``（重用=重新绑定+重新校验）

生命周期：UI start 在会话有活跃 agent turn 时返回 409 SESSION_BUSY（不自
等待、不另起执行权威）；空闲会话按 durable job 生命周期启动。取消/重试经
统一任务中心既有端点（/tasks/jobs/*）同样可用 —— 这里只做本域薄包装。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user, get_owner_token
from app.core.database import get_async_db
from app.services.auth_history_bridge import verify_session_owner

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/skill-jobs", tags=["skill-jobs"])


# ── 请求/响应模型 ─────────────────────────────────────────────────────

class PrepareRequest(BaseModel):
    session_id: str = Field(..., max_length=255)
    skill_id: str = Field(..., max_length=80)
    skill_version: str = Field("", max_length=16)
    params: Dict[str, Any] = Field(default_factory=dict)
    bindings: List[Dict[str, Any]] = Field(default_factory=list)


class StartRequest(BaseModel):
    prepared_id: str = Field(..., max_length=64)
    session_id: str = Field(..., max_length=255)
    wait_timeout_s: int = Field(
        0, description="有界等待秒数（0=后台运行+轮询；>0 时同步等待至完成/超时）")


class PresetRequest(BaseModel):
    name: str = Field(..., max_length=120)
    description: str = Field("", max_length=400)
    skill_id: str = Field(..., max_length=80)
    skill_version: str = Field("", max_length=16)
    params: Dict[str, Any] = Field(default_factory=dict)
    bindings_template: Dict[str, Any] = Field(default_factory=dict)


class PresetPrepareRequest(BaseModel):
    """预设重用：显式新 bindings（refs 重新授权）+ 可覆盖参数。"""
    session_id: str = Field(..., max_length=255)
    bindings: List[Dict[str, Any]] = Field(default_factory=list)
    params_override: Dict[str, Any] = Field(default_factory=dict)


def _org_of(user: Dict[str, Any]) -> Optional[int]:
    org = user.get("org_id") or user.get("organization_id")
    try:
        return int(org) if org is not None else None
    except (TypeError, ValueError):
        return None


def _typed_error(e: Exception) -> HTTPException:
    code = getattr(e, "code", "")
    message = str(getattr(e, "message", e))[:300]
    detail = getattr(e, "detail", None) or {}
    status_code = 400
    if code == "SESSION_BUSY":
        status_code = 409
    elif code in ("PREPARED_EXPIRED", "PREPARED_SESSION_MISMATCH"):
        status_code = 410
    elif code == "REF_NOT_FOUND":
        status_code = 404
    elif code == "SKILL_NOT_FOUND":
        status_code = 404
    elif code in ("SKILL_GUIDANCE_ONLY", "SKILL_DEPRECATED"):
        status_code = 422
    return HTTPException(status_code=status_code,
                         detail={"code": str(code or "SKILL_JOB_ERROR"),
                                 "message": message, "detail": detail})


# ── 目录与详情 ────────────────────────────────────────────────────────

@router.get("/catalog")
async def get_catalog(
    domain: str = Query("", max_length=40),
    _user: Dict[str, Any] = Depends(get_current_user),
):
    """分类型目录：GIS 技能（版本/摘要/runnability）+ md 指南 + 扩展 candidate。"""
    from app.services.gis_harness.skill_jobs.catalog_views import build_catalog

    return build_catalog(domain=domain)


@router.get("/skills/{skill_id}")
async def get_skill_detail(
    skill_id: str,
    _user: Dict[str, Any] = Depends(get_current_user),
):
    """单技能详情（完整参数 schema + 绑定需求 + 过程步骤摘要；有界）。"""
    from app.services.gis_harness.skill_jobs.catalog_views import gis_skill_entry
    from app.services.gis_harness.skills.loader import get_skill_library

    skill = get_skill_library().get(skill_id)
    if skill is None:
        raise HTTPException(404, detail={"code": "SKILL_NOT_FOUND",
                                         "message": f"skill {skill_id!r} 不存在"})
    entry = gis_skill_entry(skill)
    entry["completion_evidence"] = list(skill.completion_evidence)
    entry["quality_obligations"] = [
        {"obligation_id": o.obligation_id, "kind": o.obligation_kind,
         "description": (o.description or "")[:160]}
        for o in skill.quality_obligations[:8]
    ]
    return entry


# ── prepare / start / 运行视图 ────────────────────────────────────────

async def _authorize_session(
    db: AsyncSession, session_id: str,
    user: Dict[str, Any], owner_token: Optional[str],
) -> None:
    await verify_session_owner(db, session_id,
                               user_id=str(user.get("id") or ""),
                               owner_token=owner_token)


@router.post("/prepare")
async def prepare(
    req: PrepareRequest,
    user: Dict[str, Any] = Depends(get_current_user),
    owner_token: Optional[str] = Depends(get_owner_token),
    db: AsyncSession = Depends(get_async_db),
):
    """零业务副作用 prepared call（typed 拒绝带 code）。"""
    await _authorize_session(db, req.session_id, user, owner_token)
    from app.services.gis_harness.skill_jobs.prepare import prepare_skill_job
    from app.services.gis_harness.skill_jobs.store import prepared_store

    try:
        result = await prepare_skill_job(
            session_id=req.session_id, skill_id=req.skill_id,
            skill_version=req.skill_version, params=req.params,
            bindings=req.bindings)
    except Exception as e:  # noqa: BLE001 — typed 拒绝统一形态
        raise _typed_error(e) from e
    prepared_store.put(result.prepared, result.inline_payloads)
    return {
        "prepared_id": result.prepared.prepared_id,
        "skill": {"id": result.prepared.skill_id,
                  "version": result.prepared.skill_version,
                  "digest": result.prepared.skill_digest[:16]},
        "idempotency_key": result.prepared.idempotency_key,
        "preview": result.prepared.preview,
    }


@router.post("/start")
async def start(
    req: StartRequest,
    user: Dict[str, Any] = Depends(get_current_user),
    owner_token: Optional[str] = Depends(get_owner_token),
    db: AsyncSession = Depends(get_async_db),
):
    """启动运行（幂等；活跃 agent turn → 409 SESSION_BUSY）。"""
    await _authorize_session(db, req.session_id, user, owner_token)
    from app.services.gis_harness.skill_jobs.runner import (
        SkillJobOwner,
        start_skill_job,
    )
    from app.services.gis_harness.skill_jobs.store import prepared_store

    entry = prepared_store.pop(req.prepared_id)
    if entry is None:
        raise HTTPException(410, detail={
            "code": "PREPARED_EXPIRED",
            "message": "prepared_id 不存在/已过期/已被消费 —— 重新 prepare"})
    prepared, inline_payloads = entry
    if prepared.session_id != req.session_id:
        raise HTTPException(410, detail={
            "code": "PREPARED_SESSION_MISMATCH",
            "message": "prepared call 归属另一会话"})
    owner = SkillJobOwner(
        session_id=req.session_id,
        user_id=str(user.get("id") or ""),
        org_id=_org_of(user),
        project_id=str(user.get("project_id") or ""),
        owner_token=owner_token or "",
    )
    try:
        # UI 入口：默认后台运行 + 轮询；调用方可显式有界等待。busy 会话 409。
        view = await start_skill_job(
            prepared, inline_payloads, owner=owner,
            wait_timeout_s=float(max(0, min(req.wait_timeout_s, 120))),
            session_busy_check=True)
    except Exception as e:  # noqa: BLE001
        raise _typed_error(e) from e
    return view


@router.get("/runs")
async def list_runs(
    session_id: str = Query(..., max_length=255),
    limit: int = Query(20, ge=1, le=50),
    user: Dict[str, Any] = Depends(get_current_user),
    owner_token: Optional[str] = Depends(get_owner_token),
    db: AsyncSession = Depends(get_async_db),
):
    """会话的技能作业运行列表（刷新回看权威：durable job 行）。"""
    await _authorize_session(db, session_id, user, owner_token)
    from app.services.gis_harness.skill_jobs.runner import list_skill_job_views

    runs = await list_skill_job_views(session_id=session_id, limit=limit)
    return {"runs": runs}


@router.get("/runs/{job_id}")
async def get_run(
    job_id: int,
    session_id: str = Query("", max_length=255),
    user: Dict[str, Any] = Depends(get_current_user),
    owner_token: Optional[str] = Depends(get_owner_token),
    db: AsyncSession = Depends(get_async_db),
):
    """单运行视图（含步骤/产物 refs/证据覆盖）。session_id 给出时做归属核对。"""
    from app.services.gis_harness.skill_jobs.runner import get_skill_job_view

    view = await get_skill_job_view(job_id)
    if view is None:
        raise HTTPException(404, detail={"code": "JOB_NOT_FOUND"})
    if session_id:
        await _authorize_session(db, session_id, user, owner_token)
        if view.get("session_id") and view["session_id"] != session_id:
            raise HTTPException(404, detail={"code": "JOB_NOT_FOUND"})
    return view


@router.post("/runs/{job_id}/cancel")
async def cancel_run(
    job_id: int,
    session_id: str = Query(..., max_length=255),
    user: Dict[str, Any] = Depends(get_current_user),
    owner_token: Optional[str] = Depends(get_owner_token),
    db: AsyncSession = Depends(get_async_db),
):
    """取消（既有 durable 两阶段取消；已提交合法结果不虚假回滚）。"""
    await _authorize_session(db, session_id, user, owner_token)
    from app.services.gis_harness.skill_jobs.runner import get_skill_job_view

    view = await get_skill_job_view(job_id)
    if view is None or view.get("session_id") != session_id:
        raise HTTPException(404, detail={"code": "JOB_NOT_FOUND"})
    if not view.get("cancellable"):
        return {"job_id": str(job_id), "status": view.get("status"),
                "cancelled": False,
                "message": "运行不在可取消状态（终态不可取消）"}

    from app.core.database import SessionLocal
    from app.services.jobs.store import DurableJobStore

    with SessionLocal() as sdb:
        requested, _status = DurableJobStore.request_cancel_sync(sdb, job_id)
        sdb.commit()
    return {"job_id": str(job_id), "cancel_requested": bool(requested)}


# ── 预设 ──────────────────────────────────────────────────────────────

@router.get("/presets")
async def list_presets_ep(
    project_id: str = Query("", max_length=255),
    user: Dict[str, Any] = Depends(get_current_user),
):
    from app.services.gis_harness.skill_jobs.presets import list_presets

    return {"presets": list_presets(org_id=_org_of(user), project_id=project_id)}


@router.post("/presets")
async def create_preset_ep(
    req: PresetRequest,
    user: Dict[str, Any] = Depends(get_current_user),
):
    from app.services.gis_harness.skill_jobs.presets import PresetPayload, save_preset

    try:
        return save_preset(
            PresetPayload(
                name=req.name, description=req.description,
                skill_id=req.skill_id, skill_version=req.skill_version,
                params=req.params, bindings_template=req.bindings_template),
            org_id=_org_of(user), creator_id=str(user.get("id") or ""),
            project_id=str(user.get("project_id") or ""))
    except ValueError as e:
        raise HTTPException(422, detail={"code": "PRESET_INVALID",
                                         "message": str(e)[:200]}) from e


@router.get("/presets/{preset_id}")
async def get_preset_ep(
    preset_id: str,
    user: Dict[str, Any] = Depends(get_current_user),
):
    from app.services.gis_harness.skill_jobs.presets import get_preset

    preset = get_preset(preset_id, org_id=_org_of(user))
    if preset is None:
        raise HTTPException(404, detail={"code": "PRESET_NOT_FOUND"})
    return preset


@router.put("/presets/{preset_id}")
async def update_preset_ep(
    preset_id: str,
    req: PresetRequest,
    user: Dict[str, Any] = Depends(get_current_user),
):
    from app.services.gis_harness.skill_jobs.presets import PresetPayload, save_preset

    try:
        return save_preset(
            PresetPayload(
                name=req.name, description=req.description,
                skill_id=req.skill_id, skill_version=req.skill_version,
                params=req.params, bindings_template=req.bindings_template),
            org_id=_org_of(user), creator_id=str(user.get("id") or ""),
            preset_id=preset_id)
    except ValueError as e:
        raise HTTPException(422, detail={"code": "PRESET_INVALID",
                                         "message": str(e)[:200]}) from e


@router.delete("/presets/{preset_id}")
async def delete_preset_ep(
    preset_id: str,
    user: Dict[str, Any] = Depends(get_current_user),
):
    from app.services.gis_harness.skill_jobs.presets import delete_preset

    if not delete_preset(preset_id, org_id=_org_of(user)):
        raise HTTPException(404, detail={"code": "PRESET_NOT_FOUND"})
    return {"deleted": True}


@router.post("/presets/{preset_id}/prepare")
async def preset_prepare_ep(
    preset_id: str,
    req: PresetPrepareRequest,
    user: Dict[str, Any] = Depends(get_current_user),
    owner_token: Optional[str] = Depends(get_owner_token),
    db: AsyncSession = Depends(get_async_db),
):
    """预设重用 = 显式新绑定 + 参数覆盖 → 重新 prepare（不携带任何授权）。

    预设里的旧 ref 只作「沿用哪些输入」的提示；ref 过期/不可见 →
    REF_NOT_FOUND，要求重新绑定（不泄露他人数据）。
    """
    await _authorize_session(db, req.session_id, user, owner_token)
    from app.services.gis_harness.skill_jobs.presets import get_preset
    from app.services.gis_harness.skill_jobs.prepare import prepare_skill_job
    from app.services.gis_harness.skill_jobs.store import prepared_store

    preset = get_preset(preset_id, org_id=_org_of(user))
    if preset is None:
        raise HTTPException(404, detail={"code": "PRESET_NOT_FOUND"})
    params = dict(preset.get("params") or {})
    params.update(req.params_override)
    # 参数来源标注：预设继承的参数 = preset；覆盖值 = user
    param_sources = {k: "preset" for k in (preset.get("params") or {}).keys()}
    param_sources.update({k: "user" for k in req.params_override.keys()})
    try:
        result = await prepare_skill_job(
            session_id=req.session_id, skill_id=preset["skill_id"],
            skill_version=preset.get("skill_version") or "",
            params=params, bindings=req.bindings,
            param_sources=param_sources)
    except Exception as e:  # noqa: BLE001
        raise _typed_error(e) from e
    prepared_store.put(result.prepared, result.inline_payloads)
    return {
        "prepared_id": result.prepared.prepared_id,
        "preset": {"id": preset_id, "name": preset.get("name"),
                   "skill_version": preset.get("skill_version")},
        "skill": {"id": result.prepared.skill_id,
                  "version": result.prepared.skill_version,
                  "digest": result.prepared.skill_digest[:16]},
        "preview": result.prepared.preview,
        "param_sources": [p.model_dump() for p in result.prepared.params],
    }
