"""Data Quality V9 REST 面 —— 本线新增路由文件（§8：不改既有文件签名）。

端点（前缀 /api/v1/data-quality）：

- ``GET  /rules``                 规则目录（16 类内置规则的形状披露）；
- ``POST /evaluate``              同步小数据集评估（可选落库）；
- ``POST /reports``               大数据集评估 → durable job（幂等）；
- ``GET  /reports``               报告列表（Page[T]，project/session 过滤）；
- ``GET  /reports/{report_id}``   报告详情（含逐规则行）；
- ``POST /autofix/dry-run``       修复建议预览（绝不改动输入）；
- ``POST /autofix/apply``         确定性修复 → 新载荷（new-ref 语义）。

信封/鉴权约定（master 现状；A 线 ADR-0138 合入后以其为准）：
列表用 ``Page[T]``（limit/offset），其余 dict 直返；读路径 optional 鉴权、
提交/应用路径强制鉴权（与 data-gc plan/execute 同纪律）。

**F 线协调预告**：本文件即质量面板的 API 形状事实源（PR 描述已声明）。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.core.auth import (
    get_current_user,
    get_current_user_optional,
    get_owner_token,
)
from app.schemas.pagination import Page, clamp_pagination

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/data-quality", tags=["数据质量 V9"])

#: 同步评估的内联要素上限（更大必须走 durable job 路径）。
_MAX_INLINE_FEATURES = 20000
_MAX_INLINE_BYTES_ESTIMATE = 8 * 1024 * 1024


# ── 请求模型 ─────────────────────────────────────────────────────────


class EvaluateRequest(BaseModel):
    geojson: Optional[Dict[str, Any]] = None
    raster_stats: Optional[Dict[str, Any]] = None
    crs: str = ""
    rules: Optional[List[Dict[str, Any]]] = None
    persist: bool = False
    project_id: Optional[str] = None
    session_id: Optional[str] = None
    target_ref: str = ""


class ReportSubmitRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=255)
    ref: str = Field(min_length=1, max_length=255)
    project_id: Optional[str] = None
    rules: Optional[List[Dict[str, Any]]] = None


class AutofixRequest(BaseModel):
    geojson: Dict[str, Any]
    crs: str = ""
    rules: Optional[List[Dict[str, Any]]] = None
    report_id: Optional[str] = None
    session_id: Optional[str] = None
    project_id: Optional[str] = None
    target_ref: str = ""


class ProfileRequest(BaseModel):
    geojson: Optional[Dict[str, Any]] = None
    raster_stats: Optional[Dict[str, Any]] = None
    crs: str = ""


# ── helpers ──────────────────────────────────────────────────────────


def _validated_rule_defs(raw: Optional[List[Dict[str, Any]]]) -> Optional[List[Dict[str, Any]]]:
    if not raw:
        return None
    from app.services.data_quality.rules import parse_rule_defs

    parse_rule_defs(raw)  # fail-fast：非法 DSL 在入口 400，不进任务队列
    return raw


def _evaluate_request_payload(body: EvaluateRequest | AutofixRequest) -> Dict[str, Any]:
    payload: Dict[str, Any] = {"crs": body.crs}
    if body.geojson is not None:
        payload["geojson"] = body.geojson
    if getattr(body, "raster_stats", None):
        payload["raster_stats"] = body.raster_stats
    if "geojson" not in payload and "raster_stats" not in payload:
        raise HTTPException(status_code=400, detail="payload 需含 geojson 或 raster_stats")
    return payload


# ── 端点 ─────────────────────────────────────────────────────────────


@router.get("/rules")
def list_rules(_user: dict = Depends(get_current_user_optional)) -> dict:
    from app.services.data_quality.rules import rule_catalog

    return {"success": True, "rules": rule_catalog()}


@router.post("/evaluate")
def evaluate_quality(
    body: EvaluateRequest,
    user: dict = Depends(get_current_user_optional),
) -> dict:
    """同步小数据集评估（大数据集走 POST /reports durable job 路径）。"""
    from app.core.database import SessionLocal
    from app.services.data_quality.engine import evaluate_payload, persist_report

    try:
        rule_defs = _validated_rule_defs(body.rules)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    payload = _evaluate_request_payload(body)
    geojson = payload.get("geojson")
    if isinstance(geojson, dict) and isinstance(geojson.get("features"), list) \
            and len(geojson["features"]) > _MAX_INLINE_FEATURES:
        raise HTTPException(
            status_code=413,
            detail=f"inline features > {_MAX_INLINE_FEATURES}；请走 POST /data-quality/reports（durable job）",
        )
    try:
        report = evaluate_payload(payload, rule_defs)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    report_id = None
    if body.persist:
        with SessionLocal() as db:
            row = persist_report(
                db, report,
                project_id=body.project_id or None,
                session_id=body.session_id or None,
                created_by=(user.get("user_id") if isinstance(user, dict) else None),
                target_ref=body.target_ref,
            )
            report_id = row.id
    return {"success": True, "report_id": report_id, "report": report}


@router.post("/reports")
def submit_quality_report(
    body: ReportSubmitRequest,
    user: dict = Depends(get_current_user),
) -> dict:
    """大数据集评估：入队 durable job（幂等；任务中心可见/可取消）。"""
    try:
        rule_defs = _validated_rule_defs(body.rules)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    from app.services.data_quality.jobs import data_quality_evaluate_task
    from app.services.jobs.submit import submit_durable_job

    created_by = user.get("user_id") if isinstance(user, dict) else None
    result = submit_durable_job(
        celery_task=data_quality_evaluate_task,
        task_type="data_quality_evaluate",
        display_name="数据质量评估",
        params={"session_id": body.session_id, "ref": body.ref,
                "project_id": body.project_id},
        task_kwargs={
            "session_id": body.session_id,
            "ref": body.ref,
            "project_id": body.project_id,
            "rule_defs": rule_defs,
            "created_by": created_by,
        },
    )
    return {"success": True, **result}


@router.get("/reports")
def list_quality_reports(
    limit: Optional[int] = None,
    offset: Optional[int] = None,
    project_id: Optional[str] = None,
    session_id: Optional[str] = None,
    _user: dict = Depends(get_current_user_optional),
) -> Page[Dict[str, Any]]:
    from app.core.database import SessionLocal
    from app.models.data_quality import QualityReport

    limit_n, offset_n = clamp_pagination(limit, offset)
    with SessionLocal() as db:
        stmt = select(QualityReport).order_by(QualityReport.created_at.desc())
        count_stmt = select(QualityReport)
        if project_id:
            stmt = stmt.where(QualityReport.project_id == project_id)
            count_stmt = count_stmt.where(QualityReport.project_id == project_id)
        if session_id:
            stmt = stmt.where(QualityReport.session_id == session_id)
            count_stmt = count_stmt.where(QualityReport.session_id == session_id)
        total = len(db.execute(count_stmt).scalars().all())
        rows = db.execute(stmt.limit(limit_n).offset(offset_n)).scalars().all()
        items = [
            {
                "id": r.id,
                "project_id": r.project_id,
                "session_id": r.session_id,
                "target_ref": r.target_ref,
                "target_kind": r.target_kind,
                "status": r.status,
                "overall_status": r.overall_status,
                "rule_count": r.rule_count,
                "failed_count": r.failed_count,
                "warn_count": r.warn_count,
                "duration_ms": r.duration_ms,
                "job_id": r.job_id,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ]
    return Page(
        items=items, total=total, limit=limit_n, offset=offset_n,
        has_more=(offset_n + limit_n) < total,
    )


@router.get("/reports/{report_id}")
def get_quality_report(
    report_id: str,
    _user: dict = Depends(get_current_user_optional),
) -> dict:
    from app.core.database import SessionLocal
    from app.models.data_quality import QualityReport, QualityRuleResult

    with SessionLocal() as db:
        row = db.get(QualityReport, report_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Report not found")
        results = db.execute(
            select(QualityRuleResult)
            .where(QualityRuleResult.report_id == report_id)
            .order_by(QualityRuleResult.id)
        ).scalars().all()
        return {
            "success": True,
            "report": {
                "id": row.id,
                "project_id": row.project_id,
                "session_id": row.session_id,
                "target_ref": row.target_ref,
                "target_kind": row.target_kind,
                "dataset_identity": row.dataset_identity,
                "ruleset_digest": row.ruleset_digest,
                "status": row.status,
                "overall_status": row.overall_status,
                "rule_count": row.rule_count,
                "failed_count": row.failed_count,
                "warn_count": row.warn_count,
                "skipped_count": row.skipped_count,
                "duration_ms": row.duration_ms,
                "summary": row.summary,
                "diagnostics": row.diagnostics,
                "job_id": row.job_id,
                "created_at": row.created_at.isoformat() if row.created_at else None,
            },
            "results": [
                {
                    "rule_id": r.rule_id,
                    "rule_type": r.rule_type,
                    "severity": r.severity,
                    "status": r.status,
                    "message": r.message,
                    "affected_count": r.affected_count,
                    "duration_ms": r.duration_ms,
                    "metric": r.metric,
                    "autofixable": r.autofixable,
                    "fix_operations": r.fix_operations,
                }
                for r in results
            ],
        }


@router.post("/autofix/dry-run")
def autofix_dry_run(
    body: AutofixRequest,
    _user: dict = Depends(get_current_user_optional),
) -> dict:
    """修复建议预览：评估 → plan → dry-run（绝不改动输入）。"""
    from app.services.data_quality.autofix import dry_run_autofix, plan_autofix
    from app.services.data_quality.engine import evaluate_payload

    try:
        rule_defs = _validated_rule_defs(body.rules)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    payload = _evaluate_request_payload(body)
    try:
        report = evaluate_payload(payload, rule_defs)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    steps = plan_autofix(report)
    preview = dry_run_autofix(payload, steps)
    return {"success": True, "plan": [s.to_bounded_dict() for s in steps],
            "preview": preview}


@router.post("/autofix/apply")
def autofix_apply(
    body: AutofixRequest,
    user: dict = Depends(get_current_user),
    owner_token: Optional[str] = Depends(get_owner_token),
) -> dict:
    """确定性修复 → **新载荷**（new-ref 语义；绝不覆写源）。

    session_id 提供时经所有权守卫（外来 session 一律 404）注册为新会话 ref。
    """
    import asyncio

    from app.services.data_quality.autofix import apply_autofix, plan_autofix
    from app.services.data_quality.engine import evaluate_payload

    try:
        rule_defs = _validated_rule_defs(body.rules)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    payload = _evaluate_request_payload(body)
    try:
        report = evaluate_payload(payload, rule_defs)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    steps = plan_autofix(report)
    new_payload, evidence = apply_autofix(payload, steps)

    new_ref = None
    if body.session_id:
        # 与 project.py repair 路由同纪律：外来 session 一律 404（不泄露存在性）。
        asyncio.run(_verify_session_access(body.session_id, user, owner_token))
        from app.services.session_data import session_data_manager

        async def _store() -> str:
            return await session_data_manager.store(
                body.session_id, new_payload.get("geojson"), prefix="data-quality-fix"
            )

        new_ref = asyncio.run(_store())

    geojson = new_payload.get("geojson")
    preview: Any = geojson
    preview_features: List[Any] = []
    if isinstance(geojson, dict) and isinstance(geojson.get("features"), list):
        preview_features = geojson["features"][:50]
        preview = None  # Fetch-on-Demand：大载荷只回预览（repair 路由同款）
    return {
        "success": True,
        "changed": evidence.get("changed"),
        "operations_applied": evidence.get("operations"),
        "new_ref": new_ref,
        "repaired_geojson_preview": preview if preview is not None
        else {"type": "FeatureCollection", "features": preview_features},
        "feature_count": len(preview_features) if preview is None else None,
        "report_overall_status": report.get("overall_status"),
    }


@router.post("/profile")
def unified_profile(
    body: ProfileRequest,
    _user: dict = Depends(get_current_user_optional),
) -> dict:
    """统一画像（P2）：矢量字段统计 + H3 空间分布 / 栅格波段统计 + nodata 映射。

    与 :func:`autofix_dry_run` 同请求形状；建议规则经 ``suggested_rules``
    字段直接可用于 POST /evaluate（P2→P1 联动）。
    """
    from app.services.data_profile.unified import (
        build_unified_profile,
        suggest_rule_params,
    )

    payload = _evaluate_request_payload(body)
    try:
        profile = build_unified_profile(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"success": True, "profile": profile,
            "suggested_rules": suggest_rule_params(profile)}


def _verify_session_access(
    session_id: str,
    user: Any,
    owner_token: Optional[str],
) -> Any:
    """SEC-08 session 所有权守卫（project.py 同款：匿名凭 owner_token、
    登录凭 user_id；失败一律 404 不泄露存在性）。返回协程供 asyncio.run。"""

    async def _run() -> None:
        from app.core.auth import verify_session_owner
        from app.core.database import AsyncSessionLocal

        user_id = user.get("user_id") if isinstance(user, dict) else None
        if AsyncSessionLocal is None:
            raise HTTPException(status_code=503, detail="async db unavailable")
        async with AsyncSessionLocal() as adb:
            await verify_session_owner(
                adb, session_id, user_id=user_id, owner_token=owner_token
            )

    return _run()


__all__ = ["router"]
