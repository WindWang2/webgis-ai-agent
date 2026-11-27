"""pack plan / inspect（WP1）——零写入的闭包检视与打包计划。

inspect = 摘要（分类计数/复现分级/预估体积/披露）；
plan   = 完整 manifest 预览（逻辑 ID 分配 + 逐项载荷预估 + 工作流可运行判定）。

两者都不取载荷、不落盘；逻辑 ID 分配函数与 materialize 阶段共用同一实现
（同输入同输出），保证计划 → 执行的 ID 一致性。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from app.services.project_exchange import contracts as pc
from app.services.project_exchange.closure import (
    ProjectClosure, read_project_closure,
)


def assign_logical_ids(closure: ProjectClosure) -> Dict[str, Dict[str, str]]:
    """真实 ID → 包内稳定逻辑 ID（kind 前缀 + slug 化名；冲突追加序号）。

    同一闭包输入下分配是确定性的（按 name+真实 ID 排序后逐一分配）。
    """
    out: Dict[str, Dict[str, str]] = {"datasets": {}, "workflows": {},
                                      "artifacts": {}, "maps": {}}
    for kind, prefix, bucket in (
        ("datasets", "ds", closure.datasets),
        ("workflows", "wf", closure.workflows),
        ("artifacts", "art", closure.artifacts),
        ("maps", "map", closure.maps),
    ):
        used_counts: Dict[str, int] = {}
        taken: set = set()
        ids = sorted(bucket.keys(), key=lambda k: (
            str(getattr(bucket[k], "name", "") or ""),
            k,
        ))
        for real_id in ids[:pc.MAX_LOGICAL_IDS]:
            name = str(getattr(bucket[real_id], "name", "") or "")
            base = pc.slugify(name, default=kind)
            n = used_counts.get(base, 0)
            used_counts[base] = n + 1
            candidate = f"{prefix}-{base}" if n == 0 else f"{prefix}-{base[:40]}-{n + 1}"
            while candidate in taken:
                n += 1
                used_counts[base] = n + 1
                candidate = f"{prefix}-{base[:40]}-{n + 1}"
            taken.add(candidate)
            out[kind][real_id] = candidate
    return out


def _estimated_payload_bytes(closure: ProjectClosure) -> Tuple[int, int]:
    """(已估字节, 未知数量) —— 计划期体积预估（诚实标注未知）。"""
    known = 0
    unknown = 0
    for node in closure.datasets.values():
        if node.estimated_bytes:
            known += int(node.estimated_bytes)
        elif node.classification in (pc.CLASS_EMBEDDED, "session_ref"):
            unknown += 1
    for node in closure.artifacts.values():
        if node.byte_size:
            known += int(node.byte_size)
    return known, unknown


def inspect_project(
    db: Session,
    project_id: str,
    *,
    selection: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """零写入检视：分类计数 + 复现分级 + 预估体积 + 披露。"""
    closure = read_project_closure(db, project_id, selection=selection)
    counts = closure.classification_counts()
    known, unknown = _estimated_payload_bytes(closure)
    level = closure.repro_level()
    return {
        "project_id": closure.project_id,
        "project_name": closure.project_name,
        "selection": closure.selection,
        "reproduction_level": level,
        "classification_counts": counts,
        "totals": {
            "datasets": len(closure.datasets),
            "workflows": len(closure.workflows),
            "artifacts": len(closure.artifacts),
            "maps": len(closure.maps),
        },
        "runnable_workflows": sum(
            1 for wf in closure.workflows.values() if closure._workflow_runnable(wf)),
        "estimated_payload_bytes": known,
        "estimated_unknown_bytes_count": unknown,
        "disclosure": closure.disclosure[:100],
        "truncated": closure.truncated,
        "exportable": closure.embedded_dataset_count() > 0 or bool(closure.maps),
    }


def plan_project(
    db: Session,
    project_id: str,
    *,
    selection: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """零写入打包计划：逻辑 ID 分配 + 逐项行（含载荷路径预览）。"""
    closure = read_project_closure(db, project_id, selection=selection)
    logical = assign_logical_ids(closure)
    known, unknown = _estimated_payload_bytes(closure)
    gpkg_layers: List[Dict[str, Any]] = []
    raster_files: List[Dict[str, Any]] = []
    dataset_rows: List[Dict[str, Any]] = []
    for real_id, node in closure.datasets.items():
        lid = logical["datasets"][real_id]
        row: Dict[str, Any] = {
            "real_id": real_id,
            "logical_id": lid,
            "name": node.name,
            "classification": node.classification,
            "crs": node.crs,
            "estimated_bytes": node.estimated_bytes,
            "reason": node.reason,
            "license": node.license,
            "payload": None,
        }
        if node.classification == pc.CLASS_EMBEDDED and node.upload_path:
            if (node.upload_path or "").endswith(".tif"):
                path = f"{pc.RASTERS_DIR}{lid}.tif"
                raster_files.append({"logical_id": lid, "path": path})
            else:
                path = pc.LAYERS_GPKG_PATH
                gpkg_layers.append({"logical_id": lid, "layer": lid})
            row["payload"] = {"path": path, "member": (lid if path.endswith(
                ".gpkg") else None)}
        elif node.classification == "session_ref":
            row["payload"] = {"path": "(导出时按载荷形态物化)",
                              "member": None}
        dataset_rows.append(row)

    workflow_rows = []
    for real_id, node in closure.workflows.items():
        workflow_rows.append({
            "real_id": real_id,
            "logical_id": logical["workflows"][real_id],
            "name": node.name,
            "version": node.version,
            "graph_fingerprint": node.graph_fingerprint,
            "runnable": closure._workflow_runnable(node),
            "graph_spec_bytes_estimate": len(pc.json_bytes(node.graph_spec)),
        })

    artifact_rows = []
    for real_id, node in closure.artifacts.items():
        artifact_rows.append({
            "real_id": real_id,
            "logical_id": logical["artifacts"][real_id],
            "name": node.name,
            "artifact_type": node.artifact_type,
            "revision_no": node.revision_no,
            "byte_size": node.byte_size,
            "content_available": bool(node.content_location),
        })

    map_rows = []
    for real_id, node in closure.maps.items():
        map_rows.append({
            "real_id": real_id,
            "logical_id": logical["maps"][real_id],
            "name": node.name,
            "version_no": node.version_no,
            "has_snapshot": bool(node.mapspec_snapshot),
            "mapspec_fingerprint": node.mapspec_fingerprint,
        })

    return {
        "project_id": closure.project_id,
        "project_name": closure.project_name,
        "selection": closure.selection,
        "reproduction_level": closure.repro_level(),
        "estimated_payload_bytes": known,
        "estimated_unknown_bytes_count": unknown,
        "datasets": dataset_rows,
        "workflows": workflow_rows,
        "artifacts": artifact_rows,
        "maps": map_rows,
        "standard_files": {
            "layers_gpkg": pc.LAYERS_GPKG_PATH if gpkg_layers else None,
            "gpkg_layers": gpkg_layers,
            "rasters": raster_files,
        },
        "disclosure": closure.disclosure[:100],
        "truncated": closure.truncated,
        "limits": {
            "max_package_bytes": pc.DEFAULT_MAX_PACKAGE_BYTES,
            "max_entries": pc.MAX_PACKAGE_ENTRIES,
            "max_entry_bytes": pc.MAX_ENTRY_BYTES,
        },
    }
