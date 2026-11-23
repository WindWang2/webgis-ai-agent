"""G48 Control-Point Georeferencing deferred Pi tools (no entry in NATIVE_TOOL_NAMES)."""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, field_validator

from app.schemas.georeferencing_schema import (
    GeorefDomainError,
    GcpInputItem,
    validate_positive_finite_resolution,
)
from app.services.georeferencing import service, store
from app.tools.registry import ToolRegistry, tool
from app.tools.upload_tools import _resolve_session_id

logger = logging.getLogger(__name__)

_MAX_LIST_ITEMS = 12
_MAX_RESIDUAL_ROWS = 24


def _sid(session_id: Optional[str]) -> str:
    return (_resolve_session_id(session_id) or "").strip()


def _err(exc: Exception) -> Dict[str, Any]:
    if isinstance(exc, GeorefDomainError):
        return {"success": False, **exc.to_payload()}
    logger.warning("[georeferencing_tools] unexpected failure", exc_info=True)
    return {"success": False, "code": "GEOREF_TOOL_FAILED", "message": str(exc)[:200]}


def _bounded_fit_summary(plan) -> Optional[Dict[str, Any]]:
    fm = plan.fit_metrics
    if fm is None:
        return None
    coeff = fm.coefficients
    return {
        "status": fm.status,
        "fit_count": fm.fit_count,
        "check_count": fm.check_count,
        "disabled_count": fm.disabled_count,
        "fit_rmse_m": round(fm.fit_rmse, 4) if fm.fit_rmse is not None else None,
        "fit_max_error_m": round(fm.fit_max_error, 4) if fm.fit_max_error is not None else None,
        "check_rmse_m": round(fm.check_rmse, 4) if fm.check_rmse is not None else None,
        "check_max_error_m": round(fm.check_max_error, 4) if fm.check_max_error is not None else None,
        "zero_residual_unverified": fm.zero_residual_unverified,
        "condition_number": round(fm.condition_number, 2) if fm.condition_number is not None else None,
        "hull_coverage_ratio": round(fm.hull_coverage_ratio, 4),
        "low_coverage_warning": fm.low_coverage_warning,
        "extrapolation_warning": fm.extrapolation_warning,
        "outlier_gcp_ids": list(fm.outlier_gcp_ids)[:12],
        "warnings": list(fm.warnings)[:8],
        "blockers": list(fm.blockers)[:8],
        "affine_summary": (
            {
                "px_scale_x_m": round(coeff.px_scale_x, 4),
                "px_scale_y_m": round(coeff.px_scale_y, 4),
                "rotation_deg": round(coeff.rotation_deg, 3),
                "shear_deg": round(coeff.shear_deg, 3),
                "determinant": coeff.determinant,
                "is_reflection": coeff.is_reflection,
            }
            if coeff
            else None
        ),
    }


def _bounded_artifact_summary(art) -> Optional[Dict[str, Any]]:
    if art is None:
        return None
    return {
        "artifact_id": art.artifact_id,
        "plan_id": art.plan_id,
        "execution_id": art.execution_id,
        "job_id": art.job_id,
        "format": art.format,
        "cog_requested": art.cog_requested,
        "cog_verified": art.cog_verified,
        "target_crs": art.target_crs,
        "width": art.width,
        "height": art.height,
        "bands": art.bands,
        "dtype": art.dtype,
        "has_alpha": art.has_alpha,
        "nodata_mode": art.nodata_mode,
        "sha256": art.sha256,
        "byte_size": art.byte_size,
        "raster_ref": art.raster_ref,
        "bounds_wgs84": art.bounds_wgs84,
        "mapspec_layer_id": art.mapspec_layer_id,
        "mapspec_revision": art.mapspec_revision,
        "download_url": f"/api/v1/georeferencing/artifacts/{art.artifact_id}/download?session_id={art.session_id}",
    }


class GeorefInspectArgs(BaseModel):
    source_id: Optional[str] = Field(None, description="Optional source_id to inspect details")
    plan_id: Optional[str] = Field(None, description="Optional plan_id to inspect details")
    session_id: Optional[str] = Field(None, description="Injected authorized session_id")


class GeorefPlanArgs(BaseModel):
    action: Literal["create", "update"] = Field(default="create", description="Create new draft plan or CAS-update an existing plan")
    source_id: Optional[str] = Field(None, description="Required when action=create")
    plan_id: Optional[str] = Field(None, description="Required when action=update")
    expected_revision: Optional[int] = Field(None, ge=1, description="Required CAS revision when action=update")
    name: Optional[str] = Field(None, max_length=120)
    target_crs: Optional[str] = Field(None, description="Projected 2D metric CRS, e.g. EPSG:3857 or EPSG:32650")
    resampling: Optional[Literal["bilinear", "nearest"]] = None
    target_resolution_m: Optional[float] = Field(None, gt=0.0)
    clear_target_resolution: bool = False
    generate_cog: Optional[bool] = None
    gcps: Optional[List[Dict[str, Any]]] = Field(
        None,
        description="Explicit user/imported GCP objects with px, py, and either (map_x, map_y) or (wgs84_lon, wgs84_lat); never fabricate coordinates.",
    )
    session_id: Optional[str] = None

    @field_validator("target_resolution_m")
    @classmethod
    def _finite_target_res(cls, v: Optional[float]) -> Optional[float]:
        return validate_positive_finite_resolution(v)


class GeorefPreviewArgs(BaseModel):
    plan_id: str = Field(..., min_length=1)
    expected_revision: Optional[int] = Field(None, ge=1)
    session_id: Optional[str] = None


class GeorefExecuteArgs(BaseModel):
    action: Literal["execute", "cancel", "bind_layer"] = Field(default="execute")
    plan_id: str = Field(..., min_length=1)
    expected_revision: Optional[int] = Field(None, ge=1, description="Required CAS revision for action=execute")
    background: bool = Field(default=False, description="Wait for completion if False, or run asynchronously if True")
    idempotency_key: Optional[str] = Field(None, max_length=128)
    expected_mapspec_revision: Optional[int] = Field(None, ge=0, description="Required for action=bind_layer")
    layer_id: Optional[str] = Field(None, max_length=128)
    opacity: float = Field(default=0.85, ge=0.0, le=1.0)
    session_id: Optional[str] = None


class GeorefStatusArgs(BaseModel):
    plan_id: str = Field(..., min_length=1)
    session_id: Optional[str] = None


class GeorefReportArgs(BaseModel):
    plan_id: str = Field(..., min_length=1)
    session_id: Optional[str] = None


def register_georeferencing_tools(registry: ToolRegistry) -> None:
    """Register G48 deferred tools (all kept out of NATIVE_TOOL_NAMES)."""

    @tool(
        registry,
        args_model=GeorefInspectArgs,
        name="webgis_georef_inspect",
        description=(
            "Inspect uploaded georeferencing raster sources and draft plans in the session. "
            "Returns format, pixel dimensions, EXIF canonicalization, existing GeoTIFF CRS/GCP warnings, "
            "and bounded plan summaries without modifying state or inventing coordinates."
        ),
        tier=2,
        domains=["raster", "georeferencing"],
        cost="light",
        side_effect="cacheable_read",
        network=False,
        deterministic=True,
        latency_class="fast",
        memory_class="light",
        scale_class="small",
        tags=("配准", "控制点", "georeferencing", "inspect"),
        output_semantic_type="json",
        result_size_policy="bounded",
        crs_semantics="crs_aware",
        failure_modes=("missing_data", "invalid_args"),
    )
    async def georef_inspect(
        source_id: Optional[str] = None,
        plan_id: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        sid = _sid(session_id)
        if not sid:
            return {"success": False, "code": "FORBIDDEN_OR_NOT_FOUND", "message": "No authorized session_id."}
        try:
            out: Dict[str, Any] = {"success": True, "session_id": sid}
            if source_id:
                src = await asyncio.to_thread(service.require_source, sid, source_id)
                out["source"] = {
                    "source_id": src.source_id,
                    "filename": src.filename,
                    "detected_format": src.detected_format,
                    "width": src.width,
                    "height": src.height,
                    "bands": src.bands,
                    "dtype": src.dtype,
                    "has_alpha": src.has_alpha,
                    "exif_orientation": src.exif_orientation,
                    "exif_applied_transform": src.exif_applied_transform,
                    "original_sha256": src.original_sha256,
                    "normalized_sha256": src.normalized_sha256,
                    "observations": src.observations[:10],
                    "existing_georef": src.existing_georef.model_dump(mode="json"),
                }
            else:
                srcs = await asyncio.to_thread(store.list_sources, sid)
                out["sources"] = [
                    {
                        "source_id": s.source_id,
                        "filename": s.filename,
                        "width": s.width,
                        "height": s.height,
                        "bands": s.bands,
                        "dtype": s.dtype,
                        "detected_format": s.detected_format,
                        "existing_georef_warning": bool(s.existing_georef.warnings),
                    }
                    for s in srcs[:_MAX_LIST_ITEMS]
                ]
                out["sources_truncated"] = len(srcs) > _MAX_LIST_ITEMS

            if plan_id:
                pl = await asyncio.to_thread(service.require_plan, sid, plan_id)
                out["plan"] = {
                    "plan_id": pl.plan_id,
                    "source_id": pl.source_id,
                    "revision": pl.revision,
                    "target_crs": pl.target_crs,
                    "resampling": pl.resampling,
                    "target_resolution_m": pl.target_resolution_m,
                    "generate_cog": pl.generate_cog,
                    "gcp_count": len(pl.gcps),
                    "preview_stale": pl.preview_stale,
                    "fit_metrics": _bounded_fit_summary(pl),
                    "artifact": _bounded_artifact_summary(pl.latest_artifact),
                }
            else:
                pls = await asyncio.to_thread(store.list_plans, sid)
                out["plans"] = [
                    {
                        "plan_id": p.plan_id,
                        "source_id": p.source_id,
                        "revision": p.revision,
                        "target_crs": p.target_crs,
                        "gcp_count": len(p.gcps),
                        "fit_status": p.fit_metrics.status if p.fit_metrics else "none",
                        "has_artifact": p.latest_artifact is not None,
                    }
                    for p in pls[:_MAX_LIST_ITEMS]
                ]
            return out
        except Exception as exc:  # noqa: BLE001
            return _err(exc)

    @tool(
        registry,
        args_model=GeorefPlanArgs,
        name="webgis_georef_plan",
        description=(
            "Create or CAS-update a georeferencing draft plan (target 2D metric projected CRS, "
            "resampling, output resolution, COG export toggle, and explicit GCPs). "
            "Never fabricate coordinates when none exist and never silently remove existing GCPs."
        ),
        tier=2,
        domains=["raster", "georeferencing"],
        cost="medium",
        side_effect="state_mutation",
        network=False,
        deterministic=False,
        latency_class="fast",
        memory_class="light",
        scale_class="small",
        tags=("配准", "控制点", "georeferencing", "plan"),
        output_semantic_type="json",
        result_size_policy="bounded",
        crs_semantics="crs_aware",
        failure_modes=("invalid_args", "validation_error", "superseded", "missing_data"),
    )
    async def georef_plan(
        action: Literal["create", "update"] = "create",
        source_id: Optional[str] = None,
        plan_id: Optional[str] = None,
        expected_revision: Optional[int] = None,
        name: Optional[str] = None,
        target_crs: Optional[str] = None,
        resampling: Optional[Literal["bilinear", "nearest"]] = None,
        target_resolution_m: Optional[float] = None,
        clear_target_resolution: bool = False,
        generate_cog: Optional[bool] = None,
        gcps: Optional[List[Dict[str, Any]]] = None,
        session_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        sid = _sid(session_id)
        if not sid:
            return {"success": False, "code": "FORBIDDEN_OR_NOT_FOUND", "message": "No authorized session_id."}
        try:
            parsed_gcps: Optional[List[GcpInputItem]] = None
            if gcps is not None:
                parsed_gcps = [GcpInputItem.model_validate(item) for item in gcps]

            if action == "create":
                if not source_id:
                    return {"success": False, "code": "INVALID_GCP_COORDS", "message": "source_id is required when action='create'."}
                pl = await asyncio.to_thread(
                    service.create_plan,
                    session_id=sid,
                    source_id=source_id,
                    name=name or "Georeference Plan",
                    target_crs=target_crs or "EPSG:3857",
                    resampling=resampling or "bilinear",
                    target_resolution_m=target_resolution_m,
                    generate_cog=bool(generate_cog),
                )
                if parsed_gcps is not None:
                    pl = await asyncio.to_thread(
                        service.revise_draft,
                        session_id=sid,
                        plan_id=pl.plan_id,
                        expected_revision=pl.revision,
                        gcps=parsed_gcps,
                    )
            else:
                if not plan_id or expected_revision is None:
                    return {
                        "success": False,
                        "code": "INVALID_GCP_COORDS",
                        "message": "plan_id and expected_revision are required when action='update'.",
                    }
                pl = await asyncio.to_thread(
                    service.revise_draft,
                    session_id=sid,
                    plan_id=plan_id,
                    expected_revision=int(expected_revision),
                    name=name,
                    target_crs=target_crs,
                    resampling=resampling,
                    target_resolution_m=target_resolution_m,
                    clear_target_resolution=clear_target_resolution,
                    generate_cog=generate_cog,
                    gcps=parsed_gcps,
                )
            return {
                "success": True,
                "plan_id": pl.plan_id,
                "revision": pl.revision,
                "source_id": pl.source_id,
                "target_crs": pl.target_crs,
                "resampling": pl.resampling,
                "gcp_count": len(pl.gcps),
                "preview_stale": pl.preview_stale,
                "fit_metrics": _bounded_fit_summary(pl),
            }
        except Exception as exc:  # noqa: BLE001
            return _err(exc)

    @tool(
        registry,
        args_model=GeorefPreviewArgs,
        name="webgis_georef_preview",
        description=(
            "Compute live 2D affine diagnostics and generate a bounded downsampled warped preview + WGS84 footprint."
        ),
        tier=2,
        domains=["raster", "georeferencing"],
        cost="medium",
        # Round-2 audit (#40): generate_preview persists plan state (fit_metrics,
        # preview_digest/stale/bounds/path via mutate_plan_cas) and writes the
        # preview PNG — cacheable_read/deterministic misattributed a state
        # mutation as a pure read.
        side_effect="state_mutation",
        network=False,
        deterministic=False,
        latency_class="medium",
        memory_class="medium",
        scale_class="medium",
        tags=("配准", "预览", "georeferencing", "preview"),
        output_semantic_type="json",
        result_size_policy="bounded",
        crs_semantics="crs_aware",
        failure_modes=("invalid_args", "validation_error", "superseded", "missing_data"),
    )
    async def georef_preview(
        plan_id: str,
        expected_revision: Optional[int] = None,
        session_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        sid = _sid(session_id)
        if not sid:
            return {"success": False, "code": "FORBIDDEN_OR_NOT_FOUND", "message": "No authorized session_id."}
        try:
            resp = await asyncio.to_thread(
                service.generate_preview,
                session_id=sid,
                plan_id=plan_id,
                expected_revision=expected_revision,
            )
            return {
                "success": resp.preview_available,
                "preview_available": resp.preview_available,
                "plan_id": resp.plan.plan_id,
                "revision": resp.plan.revision,
                "preview_image_url": resp.preview_image_url,
                "out_width": resp.out_width,
                "out_height": resp.out_height,
                "estimated_output_pixels": resp.estimated_output_pixels,
                "estimated_output_bytes": resp.estimated_output_bytes,
                "bounds_wgs84": resp.plan.preview_bounds_wgs84,
                "warnings": list(resp.warnings)[:8],
                "fit_metrics": _bounded_fit_summary(resp.plan),
            }
        except Exception as exc:  # noqa: BLE001
            return _err(exc)

    @tool(
        registry,
        args_model=GeorefExecuteArgs,
        name="webgis_georef_execute",
        description=(
            "Freeze a georeferencing plan at expected_revision and execute durable warp/COG export, "
            "cancel an active job, or CAS-bind the completed GeoTIFF artifact onto a MapSpec layer."
        ),
        tier=2,
        domains=["raster", "georeferencing"],
        cost="heavy",
        side_effect="state_mutation",
        network=False,
        deterministic=False,
        latency_class="slow",
        memory_class="medium",
        scale_class="medium",
        tags=("配准", "执行", "georeferencing", "execute"),
        output_semantic_type="json",
        result_size_policy="bounded",
        crs_semantics="crs_aware",
        data_mutations=("artifact_write",),
        failure_modes=("invalid_args", "validation_error", "superseded", "missing_data", "execution_failed"),
    )
    async def georef_execute(
        plan_id: str,
        action: Literal["execute", "cancel", "bind_layer"] = "execute",
        expected_revision: Optional[int] = None,
        background: bool = False,
        idempotency_key: Optional[str] = None,
        expected_mapspec_revision: Optional[int] = None,
        layer_id: Optional[str] = None,
        opacity: float = 0.85,
        session_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        sid = _sid(session_id)
        if not sid:
            return {"success": False, "code": "FORBIDDEN_OR_NOT_FOUND", "message": "No authorized session_id."}
        try:
            if action == "cancel":
                st = await service.cancel_job(sid, plan_id)
                return {"success": True, **st, "plan": None, "artifact": _bounded_artifact_summary(st.get("artifact"))}
            if action == "bind_layer":
                if expected_mapspec_revision is None:
                    return {
                        "success": False,
                        "code": "MAPSPEC_BIND_CONFLICT",
                        "message": "expected_mapspec_revision is required for action='bind_layer'.",
                    }
                res = await service.bind_mapspec_layer(
                    session_id=sid,
                    plan_id=plan_id,
                    expected_mapspec_revision=int(expected_mapspec_revision),
                    layer_id=layer_id,
                    opacity=opacity,
                    origin="agent",
                    actor="pi:webgis_georef_execute",
                    trusted_dispatch=True,
                )
                return {
                    "success": bool(res.bound),
                    "bound": res.bound,
                    "partial_success": res.partial_success,
                    "layer_id": res.layer_id,
                    "mapspec_revision": res.mapspec_revision,
                    "current_mapspec_revision": res.current_mapspec_revision,
                    "error_code": res.error_code,
                    "message": res.message,
                    "artifact": _bounded_artifact_summary(res.artifact),
                }

            if expected_revision is None:
                return {
                    "success": False,
                    "code": "CAS_CONFLICT",
                    "message": "expected_revision is required for action='execute'.",
                }
            exec_res = await service.execute_plan(
                session_id=sid,
                plan_id=plan_id,
                expected_revision=int(expected_revision),
                background=background,
                idempotency_key=idempotency_key,
            )
            return {
                "success": True,
                "plan_id": exec_res["plan_id"],
                "job_id": exec_res["job_id"],
                "execution_id": exec_res["execution_id"],
                "status": exec_res["status"],
                "background": exec_res["background"],
                "idempotent_reuse": exec_res["idempotent_reuse"],
                "artifact": _bounded_artifact_summary(exec_res.get("artifact")),
            }
        except Exception as exc:  # noqa: BLE001
            return _err(exc)

    @tool(
        registry,
        args_model=GeorefStatusArgs,
        name="webgis_georef_status",
        description="Read durable georeferencing job status, execution stage, progress, and latest artifact ref.",
        tier=2,
        domains=["raster", "georeferencing"],
        cost="light",
        side_effect="cacheable_read",
        network=False,
        deterministic=True,
        latency_class="fast",
        memory_class="light",
        scale_class="small",
        tags=("配准", "状态", "georeferencing", "status"),
        output_semantic_type="json",
        result_size_policy="bounded",
        crs_semantics="crs_aware",
        failure_modes=("missing_data", "invalid_args"),
    )
    async def georef_status(
        plan_id: str,
        session_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        sid = _sid(session_id)
        if not sid:
            return {"success": False, "code": "FORBIDDEN_OR_NOT_FOUND", "message": "No authorized session_id."}
        try:
            st = await asyncio.to_thread(service.get_status, sid, plan_id)
            return {
                "success": True,
                "plan_id": st["plan_id"],
                "job_id": st["job_id"],
                "execution_id": st["execution_id"],
                "status": st["status"],
                "stage": st["stage"],
                "progress": st["progress"],
                "error_code": st["error_code"],
                "error_message": st["error_message"],
                "artifact": _bounded_artifact_summary(st.get("artifact")),
            }
        except Exception as exc:  # noqa: BLE001
            return _err(exc)

    @tool(
        registry,
        args_model=GeorefReportArgs,
        name="webgis_georef_report",
        description=(
            "Return bounded GCP residuals, checkpoint errors, coverage/outlier warnings, "
            "and verified GeoTIFF/COG deliverable metadata."
        ),
        tier=2,
        domains=["raster", "georeferencing"],
        cost="light",
        side_effect="cacheable_read",
        network=False,
        deterministic=True,
        latency_class="fast",
        memory_class="light",
        scale_class="small",
        tags=("配准", "精度报告", "georeferencing", "report"),
        output_semantic_type="json",
        result_size_policy="bounded",
        crs_semantics="crs_aware",
        failure_modes=("missing_data", "invalid_args"),
    )
    async def georef_report(
        plan_id: str,
        session_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        sid = _sid(session_id)
        if not sid:
            return {"success": False, "code": "FORBIDDEN_OR_NOT_FOUND", "message": "No authorized session_id."}
        try:
            pl = await asyncio.to_thread(service.require_plan, sid, plan_id)
            src = await asyncio.to_thread(service.require_source, sid, pl.source_id)
            residuals = []
            if pl.fit_metrics and pl.fit_metrics.residuals:
                residuals = [
                    {
                        "gcp_id": r.gcp_id,
                        "label": r.label,
                        "role": r.role,
                        "enabled": r.enabled,
                        "px": r.px,
                        "py": r.py,
                        "radial_error_m": round(r.radial_error, 4) if r.radial_error is not None else None,
                    }
                    for r in pl.fit_metrics.residuals[:_MAX_RESIDUAL_ROWS]
                ]
            return {
                "success": True,
                "plan_id": pl.plan_id,
                "source": {
                    "source_id": src.source_id,
                    "filename": src.filename,
                    "original_sha256": src.original_sha256,
                    "normalized_sha256": src.normalized_sha256,
                },
                "target_crs": pl.target_crs,
                "resampling": pl.resampling,
                "fit_metrics": _bounded_fit_summary(pl),
                "residuals": residuals,
                "residuals_truncated": bool(pl.fit_metrics and len(pl.fit_metrics.residuals) > _MAX_RESIDUAL_ROWS),
                "artifact": _bounded_artifact_summary(pl.latest_artifact),
                "report_export_url": f"/api/v1/georeferencing/plans/{pl.plan_id}/gcps/export?session_id={sid}&fmt=json",
            }
        except Exception as exc:  # noqa: BLE001
            return _err(exc)
