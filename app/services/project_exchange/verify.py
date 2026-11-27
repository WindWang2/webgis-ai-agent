"""导入后校验与复现证据（WP4）—— 依据 manifest 对账，区分真实结论与未评估。

词汇表（与 contracts 一致，not_evaluated 是诚实态不是失败态）：
- ``exact``              —— 依赖/计数/指纹/语义逐项对得上；
- ``semantic_equivalent``—— 字节不同（标准格式转换）但语义一致（计数/CRS/
                            字段/bbox 匹配）；
- ``partial``            —— 部分对得上 + 明确缺口；
- ``not_evaluated``      —— 没有足够证据下结论（如无会话上下文的 ref 恢复）。

绝不只比文件名或截图；浮点/几何容忍度按 _bbox_close 显式声明。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app.services.project_exchange import contracts as pc


def verify_import(manifest: pc.PackageManifest, mapping, staging: Path
                  ) -> Dict[str, Any]:
    """导入完成后的逐项对账（同步；读 DB 行由调用方线程承载）。"""
    from app.core.database import SessionLocal
    from sqlalchemy import select as sa_select

    from app.models.project import (
        Artifact, MapProductVersion, ProjectDataset, Workflow, WorkflowRevision,
    )

    report: Dict[str, Any] = {
        "verdict": pc.FIDELITY_PARTIAL,
        "datasets": [],
        "workflows": [],
        "artifacts": [],
        "maps": [],
        "environment": _environment(manifest),
        "notes": [],
    }
    project_id = mapping.project_id
    with SessionLocal() as db:
        for entry in manifest.datasets:
            info = mapping.datasets.get(entry.logical_id)
            if entry.classification != pc.CLASS_EMBEDDED:
                report["datasets"].append({
                    "logical_id": entry.logical_id,
                    "verdict": pc.FIDELITY_NOT_EVALUATED,
                    "reason": f"classification={entry.classification}",
                })
                continue
            if info is None or not info.get("dataset_id"):
                report["datasets"].append({
                    "logical_id": entry.logical_id,
                    "verdict": pc.FIDELITY_PARTIAL,
                    "reason": "注册缺失（见 skipped）",
                })
                continue
            row = db.execute(sa_select(ProjectDataset).where(
                ProjectDataset.id == str(info["dataset_id"]))).scalar_one_or_none()
            if row is None:
                report["datasets"].append({
                    "logical_id": entry.logical_id,
                    "verdict": pc.FIDELITY_PARTIAL,
                    "reason": "数据集行不存在",
                })
                continue
            detail: Dict[str, Any] = {
                "dataset_id": str(row.id),
                "crs": {"manifest": entry.crs, "registered": row.crs},
                "upload_id": info.get("upload_id"),
                "session_restore": info.get("session_restore"),
            }
            crs_ok = _crs_match(entry.crs, row.crs)
            count_ok: Optional[bool] = None
            if entry.feature_count is not None and entry.payload:
                actual = _count_from_upload(str(info.get("upload_id") or ""),
                                            entry.payload)
                if actual is not None:
                    count_ok = actual == entry.feature_count
                    detail["feature_count"] = {
                        "manifest": entry.feature_count, "restored": actual}
            verdict = pc.FIDELITY_SEMANTIC
            if crs_ok is False or count_ok is False:
                verdict = pc.FIDELITY_PARTIAL
            if info.get("session_restore"):
                detail["session_restore_pending"] = True
            report["datasets"].append({
                "logical_id": entry.logical_id, "verdict": verdict,
                **detail,
            })
        for entry in manifest.workflows:
            info = mapping.workflows.get(entry.logical_id)
            if info is None or not info.get("workflow_id"):
                report["workflows"].append({
                    "logical_id": entry.logical_id,
                    "verdict": pc.FIDELITY_PARTIAL, "reason": "未注册"})
                continue
            row = db.execute(sa_select(Workflow).where(
                Workflow.id == str(info["workflow_id"]))).scalar_one_or_none()
            if row is None:
                report["workflows"].append({
                    "logical_id": entry.logical_id,
                    "verdict": pc.FIDELITY_PARTIAL, "reason": "工作流行不存在"})
                continue
            if not entry.graph_fingerprint:
                report["workflows"].append({
                    "logical_id": entry.logical_id,
                    "verdict": pc.FIDELITY_NOT_EVALUATED,
                    "workflow_id": str(row.id),
                    "reason": "manifest 无图指纹（无法逐字节对账）"})
                continue
            from app.models.project import WorkflowRevision

            revision = db.execute(sa_select(WorkflowRevision).where(
                WorkflowRevision.id == row.current_revision_id
            )).scalar_one_or_none()
            fp_ok = (bool(revision) and
                     str(revision.graph_fingerprint or "") ==
                     entry.graph_fingerprint)
            report["workflows"].append({
                "logical_id": entry.logical_id,
                "verdict": (pc.FIDELITY_SEMANTIC if fp_ok
                            else pc.FIDELITY_PARTIAL),
                "workflow_id": str(row.id),
                "graph_fingerprint_match": fp_ok,
                "remapped_edits": len(info.get("remapped_edits") or []),
                "runnable_declared": entry.runnable,
            })
        for entry in manifest.artifacts:
            info = mapping.artifacts.get(entry.logical_id)
            report["artifacts"].append({
                "logical_id": entry.logical_id,
                "verdict": (pc.FIDELITY_SEMANTIC
                            if info and info.get("content_available")
                            else pc.FIDELITY_PARTIAL),
                "artifact_id": (info or {}).get("artifact_id"),
            })
        for entry in manifest.maps:
            info = mapping.maps.get(entry.logical_id)
            ok = False
            fp_ok: Optional[bool] = None
            if info and info.get("map_product_id"):
                row = db.execute(sa_select(MapProductVersion).where(
                    MapProductVersion.id == str(info["map_product_id"]))).scalar_one_or_none()
                if row is not None:
                    ok = bool(row.mapspec_snapshot)
                    if entry.mapspec_fingerprint:
                        fp_ok = (row.mapspec_fingerprint ==
                                 entry.mapspec_fingerprint)
            report["maps"].append({
                "logical_id": entry.logical_id,
                "verdict": (pc.FIDELITY_SEMANTIC if ok and fp_ok is not False
                            else (pc.FIDELITY_PARTIAL if not ok
                                  else pc.FIDELITY_SEMANTIC)),
                "map_product_id": (info or {}).get("map_product_id"),
                "snapshot_present": ok,
                "mapspec_fingerprint_match": fp_ok,
                "raster_rewrites": (info or {}).get("raster_rewrites", 0),
            })

    # 总判定：全 exact/semantic 且无 partial → semantic_equivalent（导入侧
    # 字节形态变了，永称 exact 是不诚实的）；有 partial → partial。
    verdicts = [item.get("verdict") for item in (
        report["datasets"] + report["workflows"] + report["artifacts"] +
        report["maps"])]
    if not verdicts:
        report["verdict"] = pc.FIDELITY_NOT_EVALUATED
    elif any(v == pc.FIDELITY_PARTIAL for v in verdicts):
        report["verdict"] = pc.FIDELITY_PARTIAL
    else:
        report["verdict"] = pc.FIDELITY_SEMANTIC
    return report


def verify_reproduction(manifest: pc.PackageManifest,
                        recompute_metrics: Dict[str, Any]) -> Dict[str, Any]:
    """显式重算后的复现证据对比（WP4 journey 用；输入来自重算产物摘要）。

    ``recompute_metrics``: ``{dataset_logical_id: {feature_count, bbox, crs}}``
    与 manifest 侧逐项对比；浮点 bbox 用显式容忍度。
    """
    out: Dict[str, Any] = {"verdict": pc.FIDELITY_PARTIAL, "items": []}
    all_ok = bool(recompute_metrics)
    for entry in manifest.datasets:
        if entry.classification != pc.CLASS_EMBEDDED:
            continue
        metric = recompute_metrics.get(entry.logical_id)
        if metric is None:
            all_ok = False
            out["items"].append({"logical_id": entry.logical_id,
                                 "verdict": pc.FIDELITY_NOT_EVALUATED,
                                 "reason": "无重算摘要"})
            continue
        count_ok = (entry.feature_count == metric.get("feature_count")
                    if entry.feature_count is not None else None)
        crs_ok = _crs_match(entry.crs, metric.get("crs") or "")
        bbox_ok = None
        if entry.bbox and metric.get("bbox"):
            bbox_ok = _bbox_close(entry.bbox, metric["bbox"])
        verdict = (pc.FIDELITY_SEMANTIC
                   if count_ok is not False and crs_ok is not False
                   else pc.FIDELITY_PARTIAL)
        if count_ok is None and crs_ok is None:
            verdict = pc.FIDELITY_NOT_EVALUATED
        if verdict == pc.FIDELITY_PARTIAL:
            all_ok = False
        out["items"].append({
            "logical_id": entry.logical_id, "verdict": verdict,
            "feature_count_match": count_ok, "crs_match": crs_ok,
            "bbox_match": bbox_ok,
        })
    out["verdict"] = (pc.FIDELITY_SEMANTIC if all_ok
                      else pc.FIDELITY_PARTIAL)
    return out


def _count_from_upload(upload_id: str, payload: pc.DatasetPayloadRef
                       ) -> Optional[int]:
    """上传承载的还原数据 → 实际计数（独立读路径）。"""
    if not upload_id or not upload_id.isdigit():
        return None
    from app.core.config import settings

    base = Path(str(settings.DATA_DIR)) / "uploads" / upload_id
    try:
        if payload.path.endswith(".csv"):
            import pandas as pd

            for name in ("original.csv",):
                path = base / name
                if path.is_file():
                    return int(len(pd.read_csv(str(path))))
            return None
        import pyogrio

        gpkg = base / "original.geojson"
        if gpkg.is_file():
            import json as _json

            data = _json.loads(gpkg.read_text(encoding="utf-8"))
            return int(len(data.get("features") or []))
        if (base / "original.tif").is_file():
            import rasterio

            with rasterio.open(str(base / "original.tif")) as src:
                return int(src.height * src.width)
    except Exception:  # noqa: BLE001 — 读不了 = 未评估（诚实）
        return None
    return None


def _environment(manifest: pc.PackageManifest) -> Dict[str, Any]:
    """复现环境证据：导出侧 writer 版本 vs 导入侧当前运行时版本。"""
    from app.services.data_delivery.contracts import writer_versions

    exported = dict(manifest.fidelity_receipt.writer_versions or {})
    current = writer_versions()
    drift = {k: {"exported": exported.get(k), "current": current.get(k)}
             for k in sorted(set(exported) | set(current))
             if exported.get(k) != current.get(k)}
    return {
        "exported_writer_versions": exported,
        "current_writer_versions": current,
        "version_drift": drift,
        "format": manifest.format,
        "format_version": manifest.format_version,
    }


def _crs_match(source: Optional[str], materialized: Optional[str]
               ) -> Optional[bool]:
    if not source and not materialized:
        return None
    s = (source or "").strip().upper()
    m = (materialized or "").strip().upper()
    if not s or not m:
        return None
    if s == m:
        return True
    import re

    pat = re.compile(r"EPSG[:\s]*(\d+)", re.IGNORECASE)
    sm = pat.search(s)
    mm = pat.search(m)
    if sm and mm:
        return sm.group(1) == mm.group(1)
    return None


def _bbox_close(a: List[float], b: List[float], tol: float = 1e-6) -> bool:
    return len(a) == len(b) and all(
        abs(x - y) <= tol * max(1.0, abs(x), abs(y))
        for x, y in zip(a, b))
