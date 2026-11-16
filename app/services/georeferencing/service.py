"""Unified domain service orchestrating UI routes and Pi tools for G48 Georeferencing."""
from __future__ import annotations

import csv
import hashlib
import io
import json
import logging
import math
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from pydantic import ValidationError

from app.core.database import SessionLocal
from app.schemas.georeferencing_schema import (
    MAX_GCPS_PER_PLAN,
    GeorefArtifactRecord,
    GeorefBindLayerResponse,
    GeorefDomainError,
    GeorefDraftPlan,
    GeorefErrorCode,
    GeorefPreviewResponse,
    GeorefSourceRecord,
    GcpInputItem,
    GcpPoint,
    validate_positive_finite_resolution,
)
from app.services.georeferencing.crs import (
    convert_wgs84_to_target,
    validate_metric_projected_crs,
)
from app.services.georeferencing.intake import ingest_source_bytes
from app.services.georeferencing.jobs import (
    cancel_georeference_job,
    cleanup_artifact_resources,
    get_job_stage,
    start_georeference_job,
)
from app.services.georeferencing.refs import (
    release_blob_ref,
    verify_file_sha256,
)
from app.services.georeferencing.solver import solve_affine_gcps
from app.services.georeferencing.store import (
    delete_source_and_plans,
    georef_session_dir,
    get_plan,
    get_source,
    list_plans,
    list_sources,
    mutate_plan_cas,
    save_plan_new,
)
from app.services.georeferencing.warp import generate_warped_preview
from app.services.jobs.store import DurableJobStore

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_gcp_inputs(
    items: List[GcpInputItem],
    *,
    width: int,
    height: int,
    target_crs: str,
) -> List[GcpPoint]:
    out: List[GcpPoint] = []
    seen_ids: set[str] = set()
    for idx, item in enumerate(items[:MAX_GCPS_PER_PLAN], start=1):
        if item.px < 0.0 or item.px > float(width) or item.py < 0.0 or item.py > float(height):
            raise GeorefDomainError(
                GeorefErrorCode.INVALID_GCP_COORDS,
                f"GCP #{idx} pixel ({item.px:.2f}, {item.py:.2f}) falls outside image bounds [0,{width}]x[0,{height}].",
                status_code=422,
            )
        gid = (item.gcp_id or "").strip() or f"gcp_{idx}_{uuid.uuid4().hex[:6]}"
        while gid in seen_ids:
            gid = f"{gid}_{uuid.uuid4().hex[:4]}"
        seen_ids.add(gid)

        prov = None
        if item.wgs84_lon is not None and item.wgs84_lat is not None:
            prov = convert_wgs84_to_target(item.wgs84_lon, item.wgs84_lat, target_crs)
            map_x, map_y = prov.map_x, prov.map_y
        elif item.map_x is not None and item.map_y is not None:
            map_x, map_y = float(item.map_x), float(item.map_y)
            import math

            if not (math.isfinite(map_x) and math.isfinite(map_y)):
                raise GeorefDomainError(
                    GeorefErrorCode.INVALID_GCP_COORDS,
                    f"GCP '{gid}' target coordinates must be finite.",
                    status_code=422,
                )
        else:
            raise GeorefDomainError(
                GeorefErrorCode.INVALID_GCP_COORDS,
                f"GCP '{gid}' requires either (map_x, map_y) or (wgs84_lon, wgs84_lat).",
                status_code=422,
            )

        out.append(
            GcpPoint(
                gcp_id=gid,
                label=(item.label or f"GCP {idx}").strip()[:120],
                px=float(item.px),
                py=float(item.py),
                map_x=float(map_x),
                map_y=float(map_y),
                role=item.role,
                enabled=bool(item.enabled),
                note=item.note,
                provenance=prov,
            )
        )
    return out


async def upload_source(
    *,
    session_id: str,
    filename: str,
    raw_bytes: bytes,
    ignore_exif: bool = False,
) -> GeorefSourceRecord:
    return await ingest_source_bytes(
        session_id=session_id,
        filename=filename,
        raw_bytes=raw_bytes,
        ignore_exif=ignore_exif,
    )


def require_source(session_id: str, source_id: str) -> GeorefSourceRecord:
    src = get_source(session_id, source_id)
    if src is None:
        raise GeorefDomainError(
            GeorefErrorCode.FORBIDDEN_OR_NOT_FOUND,
            f"Georeference source '{source_id}' not found.",
            status_code=404,
        )
    return src


def get_source_viewer_image_path(session_id: str, source_id: str) -> Path:
    src = require_source(session_id, source_id)
    viewer = georef_session_dir(session_id) / "sources" / f"{source_id}_viewer.png"
    if viewer.is_file():
        return viewer
    p = Path(src.normalized_path)
    if p.is_file() and p.suffix.lower() == ".png":
        return p
    raise GeorefDomainError(
        GeorefErrorCode.FORBIDDEN_OR_NOT_FOUND,
        f"Viewer PNG missing for source '{source_id}'.",
        status_code=404,
    )


def _validated_target_res(target_resolution_m: Optional[float]) -> Optional[float]:
    try:
        return validate_positive_finite_resolution(target_resolution_m)
    except ValueError as exc:
        raise GeorefDomainError(
            GeorefErrorCode.INVALID_GCP_COORDS,
            str(exc),
            status_code=422,
        ) from exc


def _csv_cell_safe(val: Any) -> Any:
    if isinstance(val, str) and val and val.lstrip(" \t")[0:1] in ("=", "+", "-", "@", "\t", "\r"):
        return f"'{val}"
    return val


def create_plan(
    *,
    session_id: str,
    source_id: str,
    name: str = "Georeference Plan",
    target_crs: str = "EPSG:3857",
    resampling: str = "bilinear",
    target_resolution_m: Optional[float] = None,
    generate_cog: bool = False,
) -> GeorefDraftPlan:
    src = require_source(session_id, source_id)
    crs_spec = validate_metric_projected_crs(target_crs)
    validated_res = _validated_target_res(target_resolution_m)
    if resampling not in ("bilinear", "nearest"):
        raise GeorefDomainError(
            GeorefErrorCode.INVALID_GCP_COORDS,
            f"Unsupported resampling '{resampling}'.",
            status_code=422,
        )
    now = _now()
    plan_id = f"gplan_{uuid.uuid4().hex[:14]}"
    fit = solve_affine_gcps([], width=src.width, height=src.height)
    plan = GeorefDraftPlan(
        plan_id=plan_id,
        session_id=session_id,
        source_id=source_id,
        name=name.strip() or "Georeference Plan",
        revision=1,
        target_crs=crs_spec.canonical_crs,
        resampling=resampling,  # type: ignore[arg-type]
        target_resolution_m=validated_res,
        generate_cog=bool(generate_cog),
        ignore_exif=src.exif_overridden,
        gcps=[],
        fit_metrics=fit,
        preview_stale=True,
        created_at=now,
        updated_at=now,
    )
    return save_plan_new(plan)


def require_plan(session_id: str, plan_id: str) -> GeorefDraftPlan:
    plan = get_plan(session_id, plan_id)
    if plan is None:
        raise GeorefDomainError(
            GeorefErrorCode.FORBIDDEN_OR_NOT_FOUND,
            f"Georeference plan '{plan_id}' not found.",
            status_code=404,
        )
    return plan


def revise_draft(
    *,
    session_id: str,
    plan_id: str,
    expected_revision: int,
    name: Optional[str] = None,
    target_crs: Optional[str] = None,
    resampling: Optional[str] = None,
    target_resolution_m: Optional[float] = None,
    clear_target_resolution: bool = False,
    generate_cog: Optional[bool] = None,
    gcps: Optional[List[GcpInputItem]] = None,
) -> GeorefDraftPlan:
    existing = require_plan(session_id, plan_id)
    src = require_source(session_id, existing.source_id)

    new_crs = existing.target_crs
    if target_crs is not None:
        new_crs = validate_metric_projected_crs(target_crs).canonical_crs
    new_gcps: Optional[List[GcpPoint]] = None
    if gcps is not None:
        new_gcps = _normalize_gcp_inputs(gcps, width=src.width, height=src.height, target_crs=new_crs)
    elif target_crs is not None and new_crs != existing.target_crs and existing.gcps:
        # If any GCP holds WGS84 provenance, transparently re-project raw WGS84 into new CRS;
        # otherwise keep explicit map coords and disclose via metrics.
        reprojected: List[GcpPoint] = []
        for g in existing.gcps:
            if g.provenance and g.provenance.source_crs == "EPSG:4326":
                prov = convert_wgs84_to_target(g.provenance.raw_x, g.provenance.raw_y, new_crs)
                g_copy = g.model_copy(
                    update={"map_x": prov.map_x, "map_y": prov.map_y, "provenance": prov}
                )
                reprojected.append(g_copy)
            else:
                reprojected.append(g)
        new_gcps = reprojected

    validated_res = _validated_target_res(target_resolution_m)

    def _mutate(cur: GeorefDraftPlan) -> GeorefDraftPlan:
        if name is not None and name.strip():
            cur.name = name.strip()[:120]
        cur.target_crs = new_crs
        if resampling is not None:
            cur.resampling = resampling  # type: ignore[assignment]
        if clear_target_resolution:
            cur.target_resolution_m = None
        elif validated_res is not None:
            cur.target_resolution_m = validated_res
        if generate_cog is not None:
            cur.generate_cog = bool(generate_cog)
        if new_gcps is not None:
            cur.gcps = new_gcps
        cur.fit_metrics = solve_affine_gcps(cur.gcps, width=src.width, height=src.height)
        cur.revision += 1
        cur.preview_stale = True
        cur.updated_at = _now()
        return cur

    return mutate_plan_cas(session_id, plan_id, expected_revision=expected_revision, mutator=_mutate)


def import_gcps(
    *,
    session_id: str,
    plan_id: str,
    expected_revision: int,
    fmt: str,
    content: str,
    replace_existing: bool = True,
    coords_are_wgs84: bool = False,
) -> GeorefDraftPlan:
    plan = require_plan(session_id, plan_id)
    content = content.lstrip("\ufeff")
    items: List[GcpInputItem] = []

    if fmt == "json":
        try:
            raw = json.loads(content)
        except json.JSONDecodeError as exc:
            raise GeorefDomainError(
                GeorefErrorCode.INVALID_GCP_COORDS,
                f"Invalid JSON GCP payload: {exc}",
                status_code=422,
            ) from exc
        rows = raw.get("gcps", []) if isinstance(raw, dict) else raw
        if not isinstance(rows, list):
            raise GeorefDomainError(
                GeorefErrorCode.INVALID_GCP_COORDS,
                "JSON payload must be a list of GCP objects or {'gcps': [...]}.",
                status_code=422,
            )
        for row in rows[:MAX_GCPS_PER_PLAN]:
            if not isinstance(row, dict):
                raise GeorefDomainError(
                    GeorefErrorCode.INVALID_GCP_COORDS,
                    "Every JSON GCP item must be an object.",
                    status_code=422,
                )
            try:
                has_wgs84 = (
                    row.get("wgs84_lon") is not None and row.get("wgs84_lat") is not None
                ) or (row.get("lon") is not None and row.get("lat") is not None)
                if coords_are_wgs84 or (has_wgs84 and not ("map_x" in row and "map_y" in row)):
                    lon = row.get("wgs84_lon", row.get("lon"))
                    lat = row.get("wgs84_lat", row.get("lat"))
                    if lon is None or lat is None:
                        raise ValueError("Missing required WGS84 lon/lat coordinates")
                    items.append(
                        GcpInputItem(
                            gcp_id=row.get("gcp_id"),
                            label=str(row.get("label") or ""),
                            px=float(row["px"]),
                            py=float(row["py"]),
                            wgs84_lon=float(lon),
                            wgs84_lat=float(lat),
                            role=str(row.get("role") or "fit"),  # type: ignore[arg-type]
                            enabled=bool(row.get("enabled", True)),
                            note=str(row.get("note") or ""),
                        )
                    )
                else:
                    items.append(
                        GcpInputItem(
                            gcp_id=row.get("gcp_id"),
                            label=str(row.get("label") or ""),
                            px=float(row["px"]),
                            py=float(row["py"]),
                            map_x=float(row["map_x"]),
                            map_y=float(row["map_y"]),
                            role=str(row.get("role") or "fit"),  # type: ignore[arg-type]
                            enabled=bool(row.get("enabled", True)),
                            note=str(row.get("note") or ""),
                        )
                    )
            except (KeyError, TypeError, ValueError, ValidationError) as exc:
                raise GeorefDomainError(
                    GeorefErrorCode.INVALID_GCP_COORDS,
                    f"Malformed JSON GCP row: {exc}",
                    status_code=422,
                ) from exc
    else:
        reader = csv.DictReader(io.StringIO(content))
        if not reader.fieldnames:
            raise GeorefDomainError(
                GeorefErrorCode.INVALID_GCP_COORDS,
                "Malformed CSV GCP payload: missing header row.",
                status_code=422,
            )
        for row in reader:
            if len(items) >= MAX_GCPS_PER_PLAN:
                break
            if not any(str(v or "").strip() for v in row.values()):
                continue
            try:
                role = str(row.get("role") or "fit").strip().lower() or "fit"
                enabled_raw = str(row.get("enabled") if row.get("enabled") is not None else "true").strip().lower()
                enabled = enabled_raw not in ("0", "false", "no")
                px = float(row["px"])
                py = float(row["py"])
                lon_str = str(row.get("wgs84_lon") or row.get("lon") or "").strip()
                lat_str = str(row.get("wgs84_lat") or row.get("lat") or "").strip()
                mapx_str = str(row.get("map_x") or "").strip()
                mapy_str = str(row.get("map_y") or "").strip()
                if coords_are_wgs84 or (lon_str and lat_str and not (mapx_str and mapy_str)):
                    if not (lon_str and lat_str):
                        raise ValueError("Missing required WGS84 lon/lat columns")
                    items.append(
                        GcpInputItem(
                            gcp_id=row.get("gcp_id") or None,
                            label=str(row.get("label") or ""),
                            px=px,
                            py=py,
                            wgs84_lon=float(lon_str),
                            wgs84_lat=float(lat_str),
                            role=role,  # type: ignore[arg-type]
                            enabled=enabled,
                            note=str(row.get("note") or ""),
                        )
                    )
                else:
                    items.append(
                        GcpInputItem(
                            gcp_id=row.get("gcp_id") or None,
                            label=str(row.get("label") or ""),
                            px=px,
                            py=py,
                            map_x=float(mapx_str if mapx_str else row["map_x"]),
                            map_y=float(mapy_str if mapy_str else row["map_y"]),
                            role=role,  # type: ignore[arg-type]
                            enabled=enabled,
                            note=str(row.get("note") or ""),
                        )
                    )
            except (KeyError, ValueError, TypeError, ValidationError) as exc:
                raise GeorefDomainError(
                    GeorefErrorCode.INVALID_GCP_COORDS,
                    f"Malformed CSV GCP row: {exc}",
                    status_code=422,
                ) from exc

    merged_inputs = list(items)
    if not replace_existing:
        existing_inputs = [
            GcpInputItem(
                gcp_id=g.gcp_id,
                label=g.label,
                px=g.px,
                py=g.py,
                map_x=g.map_x,
                map_y=g.map_y,
                role=g.role,
                enabled=g.enabled,
                note=g.note,
            )
            for g in plan.gcps
        ]
        merged_inputs = existing_inputs + items

    return revise_draft(
        session_id=session_id,
        plan_id=plan_id,
        expected_revision=expected_revision,
        gcps=merged_inputs,
    )


def export_gcps(session_id: str, plan_id: str, fmt: str = "json") -> Tuple[bytes, str, str]:
    """Export GCPs, affine coefficients, and residuals in JSON or CSV."""
    plan = require_plan(session_id, plan_id)
    if fmt == "csv":
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow([
            "gcp_id", "label", "role", "enabled", "px", "py", "map_x", "map_y",
            "pred_x", "pred_y", "dx", "dy", "radial_error", "target_crs", "note",
        ])
        res_by_id = {r.gcp_id: r for r in (plan.fit_metrics.residuals if plan.fit_metrics else [])}
        for g in plan.gcps:
            r = res_by_id.get(g.gcp_id)
            writer.writerow([
                _csv_cell_safe(g.gcp_id),
                _csv_cell_safe(g.label),
                _csv_cell_safe(g.role),
                g.enabled,
                g.px,
                g.py,
                g.map_x,
                g.map_y,
                r.pred_x if r else "",
                r.pred_y if r else "",
                r.dx if r else "",
                r.dy if r else "",
                r.radial_error if r else "",
                _csv_cell_safe(plan.target_crs),
                _csv_cell_safe(g.note),
            ])
        return buf.getvalue().encode("utf-8"), "text/csv; charset=utf-8", f"{plan_id}_gcps.csv"

    payload = {
        "plan_id": plan.plan_id,
        "source_id": plan.source_id,
        "revision": plan.revision,
        "target_crs": plan.target_crs,
        "resampling": plan.resampling,
        "target_resolution_m": plan.target_resolution_m,
        "gcps": [g.model_dump(mode="json") for g in plan.gcps],
        "fit_metrics": plan.fit_metrics.model_dump(mode="json") if plan.fit_metrics else None,
    }
    return (
        json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"),
        "application/json",
        f"{plan_id}_gcps_report.json",
    )


def generate_preview(
    *,
    session_id: str,
    plan_id: str,
    expected_revision: Optional[int] = None,
) -> GeorefPreviewResponse:
    plan = require_plan(session_id, plan_id)
    if expected_revision is not None and plan.revision != expected_revision:
        raise GeorefDomainError(
            GeorefErrorCode.CAS_CONFLICT,
            f"Revision mismatch: expected {expected_revision}, current is {plan.revision}.",
            status_code=409,
        )
    src = require_source(session_id, plan.source_id)
    fit = solve_affine_gcps(plan.gcps, width=src.width, height=src.height)
    if fit.status != "valid" or fit.coefficients is None:
        return GeorefPreviewResponse(
            plan=plan.model_copy(update={"fit_metrics": fit}),
            preview_available=False,
            warnings=fit.blockers + fit.warnings,
        )

    # R 审计 round-2（P2-4c）：预览先写唯一临时名，CAS 胜出后再原子改名到
    # 规范路径 —— 否则并发预览的败者会在 CAS 检查前覆盖胜者已发布的 PNG。
    tmp_name = f"{plan.plan_id}_preview.cas-{uuid.uuid4().hex[:8]}.png"
    tmp_preview_path, grid = generate_warped_preview(
        session_id=session_id,
        plan_id=plan.plan_id,
        source=src,
        coeffs=fit.coefficients,
        target_crs=plan.target_crs,
        resampling=plan.resampling,
        target_resolution_m=plan.target_resolution_m,
        preview_name=tmp_name,
    )
    preview_path = tmp_preview_path.with_name(f"{plan.plan_id}_preview.png")
    digest = hashlib.sha256(tmp_preview_path.read_bytes()).hexdigest()[:16]

    def _save_preview(cur: GeorefDraftPlan) -> GeorefDraftPlan:
        if cur.revision != plan.revision:
            raise GeorefDomainError(
                GeorefErrorCode.CAS_CONFLICT,
                f"Plan updated concurrently during preview (rev {plan.revision} -> {cur.revision}).",
                status_code=409,
            )
        cur.fit_metrics = fit
        cur.preview_digest = digest
        cur.preview_stale = False
        cur.preview_footprint_wgs84 = grid.footprint_wgs84
        cur.preview_bounds_wgs84 = grid.bounds_wgs84
        cur.preview_path = str(preview_path)
        cur.updated_at = _now()
        return cur

    try:
        updated = mutate_plan_cas(session_id, plan_id, expected_revision=plan.revision, mutator=_save_preview)
    except Exception:
        tmp_preview_path.unlink(missing_ok=True)
        raise
    os.replace(tmp_preview_path, preview_path)
    return GeorefPreviewResponse(
        plan=updated,
        preview_available=True,
        preview_image_url=f"/api/v1/georeferencing/plans/{plan_id}/preview/image?session_id={session_id}&v={digest}",
        out_width=grid.out_width,
        out_height=grid.out_height,
        estimated_output_pixels=grid.estimated_pixels,
        estimated_output_bytes=grid.estimated_bytes,
        warnings=fit.warnings,
    )


def get_preview_image_path(session_id: str, plan_id: str) -> Path:
    plan = require_plan(session_id, plan_id)
    if plan.preview_stale:
        raise GeorefDomainError(
            GeorefErrorCode.PREVIEW_STALE,
            "Preview is stale after recent plan edits; regenerate preview before reading.",
            status_code=409,
        )
    if not plan.preview_path or not Path(plan.preview_path).is_file():
        raise GeorefDomainError(
            GeorefErrorCode.FORBIDDEN_OR_NOT_FOUND,
            "Preview image does not exist yet.",
            status_code=404,
        )
    return Path(plan.preview_path)


async def execute_plan(
    *,
    session_id: str,
    plan_id: str,
    expected_revision: int,
    background: bool = True,
    idempotency_key: Optional[str] = None,
    owner_id: Optional[str] = None,
    owner_token: Optional[str] = None,
    org_id: Optional[int] = None,
    project_id: Optional[str] = None,
) -> Dict[str, Any]:
    plan = require_plan(session_id, plan_id)
    if plan.revision != expected_revision:
        raise GeorefDomainError(
            GeorefErrorCode.CAS_CONFLICT,
            f"Revision mismatch for plan '{plan_id}': expected {expected_revision}, current is {plan.revision}.",
            status_code=409,
        )
    src = require_source(session_id, plan.source_id)
    fit = solve_affine_gcps(plan.gcps, width=src.width, height=src.height)
    plan = plan.model_copy(update={"fit_metrics": fit})
    return await start_georeference_job(
        session_id=session_id,
        plan=plan,
        source=src,
        expected_revision=expected_revision,
        background=background,
        idempotency_key=idempotency_key,
        owner_id=owner_id,
        owner_token=owner_token,
        org_id=org_id,
        project_id=project_id,
    )


async def cancel_job(session_id: str, plan_id: str) -> Dict[str, Any]:
    res = await cancel_georeference_job(session_id, plan_id)
    return get_status(session_id, plan_id)


def get_status(session_id: str, plan_id: str) -> Dict[str, Any]:
    plan = require_plan(session_id, plan_id)
    jid_str = plan.active_job_id or (plan.latest_artifact.job_id if plan.latest_artifact else None)
    status_val = "idle"
    progress = 0
    err_code = None
    err_msg = None
    stage = ""
    if jid_str:
        with SessionLocal() as db:
            row = DurableJobStore.get_sync(db, int(jid_str))
            if row is not None:
                status_val = str(row.status)
                progress = int(row.progress or 0)
                trace = row.error_trace or row.progress_message
                if status_val == "failed" and trace:
                    err_msg = str(trace)
                    if ":" in err_msg:
                        prefix = err_msg.split(":", 1)[0].strip()
                        if prefix and prefix.replace("_", "").isupper():
                            err_code = prefix
        stage = get_job_stage(int(jid_str)) or status_val
    return {
        "plan_id": plan.plan_id,
        "job_id": jid_str,
        "execution_id": plan.active_execution_id or (plan.latest_artifact.execution_id if plan.latest_artifact else None),
        "status": status_val,
        "stage": stage,
        "progress": progress,
        "error_code": err_code,
        "error_message": err_msg,
        "artifact": plan.latest_artifact,
        "plan": plan,
    }


async def bind_mapspec_layer(
    *,
    session_id: str,
    plan_id: str,
    expected_mapspec_revision: int,
    layer_id: Optional[str] = None,
    layer_name: Optional[str] = None,
    opacity: float = 0.85,
    owner_token: Optional[str] = None,
    origin: str = "user",
    actor: str = "georeferencing_workbench",
    trusted_dispatch: bool = False,
) -> GeorefBindLayerResponse:
    """Register published GeoRaster onto MapSpec via CAS, cleanly separating data gen vs CAS failures (R31)."""
    plan = require_plan(session_id, plan_id)
    art = plan.latest_artifact
    if art is None:
        raise GeorefDomainError(
            GeorefErrorCode.ARTIFACT_NOT_READY,
            "Plan has no completed GeoTIFF artifact yet; run georeferencing before binding a MapSpec layer.",
            status_code=409,
        )

    from app.services.raster_authoring.service import (
        RasterExpressionError,
        commit_expression,
        current_revision,
    )

    lid = (layer_id or "").strip() or f"georef_{plan.source_id}"
    req: Dict[str, Any]
    if art.bands >= 3 and art.dtype == "uint8":
        req = {
            "schema_version": "raster_expression.v1",
            "mode": "rgb",
            "bands": [1, 2, 3],
            "stretch": {"min": 0.0, "max": 255.0, "per_channel": [[0.0, 255.0], [0.0, 255.0], [0.0, 255.0]]},
            "resampling": plan.resampling,
            "opacity": float(opacity),
        }
    elif art.dtype == "uint8":
        req = {
            "schema_version": "raster_expression.v1",
            "mode": "single",
            "bands": [1],
            "stretch": {"min": 0.0, "max": 255.0, "per_channel": [[0.0, 255.0]]},
            "colormap": "gray",
            "resampling": plan.resampling,
            "opacity": float(opacity),
        }
    else:
        req = {
            "schema_version": "raster_expression.v1",
            "mode": "single",
            "bands": [1],
            "stretch": "auto",
            "colormap": "gray",
            "resampling": plan.resampling,
            "opacity": float(opacity),
        }

    try:
        receipt = await commit_expression(
            session_id=session_id,
            ref_id=art.raster_ref,
            layer_id=lid,
            request=req,
            expected_revision=int(expected_mapspec_revision),
            owner_token=owner_token,
            origin=origin,
            actor=actor,
            mutation_id=f"georef:{plan_id}:{art.artifact_id}:r{expected_mapspec_revision}",
            trusted_dispatch=trusted_dispatch,
        )
    except RasterExpressionError as exc:
        if exc.code == "CAS_SUPERSEDED":
            cur_rev = int(await current_revision(session_id))
            def _mark_partial(cur: GeorefDraftPlan) -> GeorefDraftPlan:
                cur.mapspec_bind_status = f"cas_conflict_r{cur_rev}"
                return cur
            mutate_plan_cas(session_id, plan_id, expected_revision=None, mutator=_mark_partial)
            return GeorefBindLayerResponse(
                bound=False,
                partial_success=True,
                error_code=GeorefErrorCode.MAPSPEC_BIND_CONFLICT.value,
                current_mapspec_revision=cur_rev,
                message=(
                    f"GeoTIFF artifact '{art.artifact_id}' succeeded and remains ready for download, "
                    f"but MapSpec bind collided at expected revision {expected_mapspec_revision} (current={cur_rev})."
                ),
                artifact=art,
            )
        raise GeorefDomainError(
            GeorefErrorCode.MAPSPEC_BIND_CONFLICT,
            f"MapSpec raster layer registration failed ({exc.code}): {exc.message}",
            status_code=int(getattr(exc, "status_code", 409) or 409),
        ) from exc

    new_rev = int(receipt.get("mapspec_revision") or receipt.get("revision") or expected_mapspec_revision)
    display_name = (layer_name or "").strip() or plan.name or lid
    retired_for_layer: List[GeorefArtifactRecord] = []

    def _save_bind(cur: GeorefDraftPlan) -> GeorefDraftPlan:
        if cur.latest_artifact:
            cur.latest_artifact.mapspec_layer_id = lid
            cur.latest_artifact.mapspec_revision = new_rev
        kept: List[GeorefArtifactRecord] = []
        for old in cur.superseded_artifacts:
            if old.mapspec_layer_id == lid and old.artifact_id != art.artifact_id:
                retired_for_layer.append(old)
            else:
                kept.append(old)
        cur.superseded_artifacts = kept
        cur.mapspec_bind_status = "bound"
        cur.updated_at = _now()
        return cur

    updated = mutate_plan_cas(session_id, plan_id, expected_revision=None, mutator=_save_bind)
    for old in retired_for_layer:
        await cleanup_artifact_resources(session_id, old)
    return GeorefBindLayerResponse(
        bound=True,
        partial_success=False,
        layer_id=lid,
        mapspec_revision=new_rev,
        current_mapspec_revision=new_rev,
        message=f"Bound GeoRaster layer '{display_name}' ({lid}) to MapSpec revision {new_rev}.",
        artifact=updated.latest_artifact,
        mapspec=receipt.get("mapspec") if isinstance(receipt.get("mapspec"), dict) else None,
    )


def resolve_authorized_artifact_download(session_id: str, artifact_id: str) -> Tuple[Path, GeorefArtifactRecord]:
    """Verify completed plan artifact ownership, readiness, and SHA256 before serving GeoTIFF download (R40)."""
    for plan in list_plans(session_id):
        art = plan.latest_artifact
        if art is not None and art.artifact_id == artifact_id:
            if plan.active_job_id:
                with SessionLocal() as db:
                    row = DurableJobStore.get_sync(db, int(plan.active_job_id))
                    if row is not None and str(row.status) in ("pending", "queued", "running", "cancelling"):
                        raise GeorefDomainError(
                            GeorefErrorCode.ARTIFACT_NOT_READY,
                            "A georeferencing job is currently active; wait for completion before downloading.",
                            status_code=409,
                        )
            p = Path(art.file_path)
            if not p.is_file():
                raise GeorefDomainError(
                    GeorefErrorCode.ARTIFACT_NOT_READY,
                    f"Artifact file '{artifact_id}' is no longer available on disk.",
                    status_code=404,
                )
            try:
                verify_file_sha256(p, art.sha256)
            except Exception as exc:  # noqa: BLE001
                raise GeorefDomainError(
                    GeorefErrorCode.SOURCE_CORRUPT_OR_TAMPERED,
                    f"Download integrity verification failed for '{artifact_id}': {exc}",
                    status_code=409,
                ) from exc
            return p, art
    raise GeorefDomainError(
        GeorefErrorCode.FORBIDDEN_OR_NOT_FOUND,
        f"Georeference artifact '{artifact_id}' not found for session.",
        status_code=404,
    )


async def delete_source(session_id: str, source_id: str) -> int:
    """Delete a source and all associated plans, releasing session refs and BlobStore pins cleanly (R28)."""
    from app.services.artifact_registry import mark_status
    from app.services.session_data import session_data_manager

    plans = [p for p in list_plans(session_id) if p.source_id == source_id]
    # R 审计 round-2（P2-4b）：删除前先请求协作取消仍活跃的 job —— 否则
    # in-flight warp 会在源/计划状态消失后继续执行，落到提交期 CAS 失败，
    # 白做整幅 warp 并留下孤儿产物。取消是尽力而为：job 已终态或缺失时
    # 不阻塞删除。
    for p in plans:
        if getattr(p, "active_job_id", None):
            try:
                await cancel_georeference_job(session_id, p.plan_id)
            except Exception:  # noqa: BLE001
                logger.warning(
                    "[georef] cancel-before-delete failed plan=%s", p.plan_id, exc_info=True
                )
    deleted, shas = delete_source_and_plans(session_id, source_id)
    if not deleted:
        raise GeorefDomainError(
            GeorefErrorCode.FORBIDDEN_OR_NOT_FOUND,
            f"Georeference source '{source_id}' not found.",
            status_code=404,
        )
    for p in plans:
        for art in [p.latest_artifact, *p.superseded_artifacts]:
            if art and art.raster_ref:
                await session_data_manager.delete_ref(session_id, art.raster_ref)
                await mark_status(session_id, art.raster_ref, "superseded")
    released = 0
    for sha in shas:
        if release_blob_ref(sha, session_id):
            released += 1
    return released
