"""G17 导入工作台 —— 有界预览（与 commit 同解释器）。

预览不产生正式 ref、不建数据集（合同 2）。样本即样本（合同 3）：
- 行数有界（默认 200 / 硬帽 2000），总数未知如实标 unknown；
- 候选编码回退在披露里呈现，不升级为用户确认；
- 位置摘要仅当 CRS 证据确认（declared/source_metadata/rfc7946_default），
  unknown 时呈现表格并要求补充 CRS（场景 11）。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.services.data_import import budgets
from app.services.data_import.interpretation import (
    InterpretResult,
    InterpretationError,
    interpret_table,
    interpret_vector,
)
from app.services.data_import.models import (
    MemberKind,
    MemberRecord,
    SourceFile,
    TableInterpretation,
    VectorInterpretation,
)

_CONFIRMED_CRS_SOURCES = {"source_metadata", "declared", "rfc7946_default"}


def _bbox_summary(result: InterpretResult) -> Optional[Dict[str, float]]:
    geoms = [r.geometry for r in result.records if r.geometry is not None]
    if not geoms:
        return None
    xs: List[float] = []
    ys: List[float] = []
    for g in geoms[: budgets.PREVIEW_MAX_ROWS]:
        minx, miny, maxx, maxy = g.bounds
        xs.extend((minx, maxx))
        ys.extend((miny, maxy))
    return {
        "minx": min(xs), "miny": min(ys), "maxx": max(xs), "maxy": max(ys),
    }


def build_preview(
    *,
    member: MemberRecord,
    source: SourceFile,
    source_path: Any,
    interpretation: Any,
    row_limit: int,
    extract_root=None,
    cancel=lambda: None,
) -> Dict[str, Any]:
    """有界预览载荷（表样本 + 列摘要 + 拒绝行 + 坐标/几何披露）。"""
    limit = max(1, min(row_limit, budgets.PREVIEW_MAX_ROWS))

    if member.kind == MemberKind.VECTOR:
        import pyogrio

        read_path = source_path
        layer = None
        if member.container.value in ("gpkg", "kml"):
            layer = member.container_ref
            if not layer:
                raise InterpretationError("CONTAINER_REF_MISSING", member.member_key)
        elif member.container.value == "zipset":
            read_path = _zipset_shp_path(member, source, extract_root)
        kwargs: Dict[str, Any] = {"max_features": limit + 1}
        if layer is not None:
            kwargs["layer"] = layer
        try:
            gdf = pyogrio.read_dataframe(str(read_path), **kwargs)
        except Exception as e:  # noqa: BLE001
            raise InterpretationError("VECTOR_READ_FAILED", f"{member.display_name}: {e}") from e
        interp = (
            interpretation
            if isinstance(interpretation, VectorInterpretation)
            else VectorInterpretation()
        )
        crs_hint = (
            "rfc7946_default"
            if member.crs_source == "rfc7946_default"
            else "source_metadata"
        )
        result = interpret_vector(
            gdf, interp, row_limit=limit, cancel=cancel, crs_source_hint=crs_hint
        )
        result.truncated = len(gdf) > limit
    else:
        interp = (
            interpretation
            if isinstance(interpretation, TableInterpretation)
            else TableInterpretation()
        )
        sheet = member.container_ref if member.container.value == "sheet" else None
        result = interpret_table(
            source_path, interp, sheet=sheet, hints=member.hints,
            row_limit=limit, cancel=cancel,
        )

    ev = result.crs_evidence
    crs_confirmed = bool(ev and ev.crs_source in _CONFIRMED_CRS_SOURCES and ev.crs)

    sample_rows = [
        r.properties for r in result.records[: budgets.PREVIEW_DEFAULT_ROWS]
    ]
    rejects_inline = [r.to_dict() for r in result.rejects[: budgets.REJECT_INLINE_MAX]]
    reject_kinds: Dict[str, int] = {}
    for r in result.rejects:
        reject_kinds[r.reason_code] = reject_kinds.get(r.reason_code, 0) + 1

    payload: Dict[str, Any] = {
        "member_key": member.member_key,
        "kind": member.kind.value,
        "columns_total": member.fields_total if member.fields_total else len(result.columns),
        "effective_mode": result.effective_mode or ("vector" if member.kind == MemberKind.VECTOR else ""),
        "columns": result.columns,
        "column_kinds": result.column_kinds,
        "sample_rows": sample_rows,
        "sample_rows_returned": len(sample_rows),
        "rows_scanned": result.rows_scanned,
        "rows_total": result.rows_total,       # None = 未知（未全量扫描）
        "truncated": result.truncated,
        "accepted_rows": len(result.records),
        "rejected_rows": len(result.rejects),
        "reject_kinds": reject_kinds,
        "rejects_inline": rejects_inline,
        "rejects_truncated": len(result.rejects) > budgets.REJECT_INLINE_MAX,
        "sampling_disclosure": (
            f"预览仅解释前 {result.rows_scanned} 行（上限 {limit}）；"
            + ("总行数未知（未全量扫描）。" if result.rows_total is None else f"总行数 {result.rows_total}。")
            + "预览不发现全部类型/错误。"
        ),
        "crs": {
            "crs_source": ev.crs_source if ev else "unknown",
            "crs": ev.crs if ev else None,
            "confirmed": crs_confirmed,
        },
        "geometry": {
            "family": result.geometry_family or None,
            "has_z": result.has_z,
        },
        "encoding_used": result.encoding_used,
        "delimiter_used": result.delimiter_used,
    }

    if member.kind == MemberKind.VECTOR or result.effective_mode in ("points", "wkt"):
        if crs_confirmed:
            bbox = _bbox_summary(result)
            if bbox:
                payload["location_preview"] = {"bbox": bbox, "crs": "EPSG:4326(样本坐标系下)"}
        else:
            payload["location_preview"] = None
            payload["requires_crs_confirmation"] = True
            payload["notes"] = ["坐标 CRS 未确认：可预览为表；落图/空间分析需先声明源 CRS"]
    return payload


def _zipset_shp_path(member: MemberRecord, source: SourceFile, extract_root) -> Any:
    """zipset 成员 → 提取目录中该套组件的 .shp 实路径（确定式 container_ref 定位）。"""
    from pathlib import Path

    if extract_root is None or not member.container_ref or not member.container_ref_detail:
        raise InterpretationError("EXTRACT_ROOT_REQUIRED", member.member_key)
    shp = (
        Path(extract_root)
        / source.source_id
        / member.container_ref
        / f"{member.container_ref_detail}.shp"
    )
    if not shp.exists():
        raise InterpretationError("ZIPSET_NOT_FOUND", member.member_key)
    return shp
