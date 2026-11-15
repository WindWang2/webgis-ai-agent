"""G39 成果发布 — 长表 / 站位层 / 收据（WP08）。

发布纪律（先内容、后登记、后回读验证，全部完成才可见）：

1. **长表**（sample_id × source × band 复合键）→ 既有
   ``materialize_geoparquet`` 通道：原子落盘 ``ref:fabric-parquet/<id>``
   （G18 可直接交付）+ register_artifact 台账 + DataObject CAS 内容身份；
2. **站位层**（一站一要素，波段宽列 + 无效计数，不按 band 重复站位
   身份）→ 会话 ref（地图联动 lane）；会话字节预算不足时诚实降级
   （STATION_LAYER_SKIPPED —— 完整数据仍在长表）；
3. **回读验证**：parquet 行数 / schema / 抽样行值、站位层要素数全部
   与内存事实一致后才登记引用；
4. **收据**：源 revision、geometry hash、方法/算法 digest、请求/有效/
   分型无效计数、读取记账、输出摘要 —— 发布完成后 append（published）。

图表抽稀：显示曲线由 results API 从长表按需抽稀（保端点/缺口/极值 +
方法披露），不另立事实源。
"""
from __future__ import annotations

import asyncio
import random
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from app.services.raster_sampling.contracts import (
    CoverageCounts,
    ExecutionReceipt,
    MAX_CURVE_POINTS_PER_BAND,
    MAX_SAMPLE_TABLE_ROWS,
    MAX_STATION_LAYER_FEATURES,
    RasterSamplingError,
    SampleRow,
    SAMPLING_ALGORITHM_VERSION,
)

_STATION_REF_PREFIX = "raster-sampling"


def coverage_from_rows(
    rows: Sequence[SampleRow], station_count: int
) -> CoverageCounts:
    """覆盖率：分母 = 请求站位 × (源, 波段) 完整矩阵（不删坏行）。"""
    invalid: Dict[str, int] = {}
    valid = 0
    stations_valid: set = set()
    for r in rows:
        if r.valid:
            valid += 1
            stations_valid.add(r.sample_id)
        elif r.invalid_reason:
            invalid[r.invalid_reason] = invalid.get(r.invalid_reason, 0) + 1
    return CoverageCounts(
        requested_cells=len(rows),
        valid_cells=valid,
        invalid_by_reason=invalid,
        stations_requested=station_count,
        stations_with_any_valid=len(stations_valid),
    )


def rows_to_arrow_table(rows: Sequence[SampleRow]) -> Any:
    """样本行 → Arrow 表（长表 schema；raw_exact 保 int64 精度通道）。"""
    try:
        import pyarrow as pa
    except Exception as exc:  # noqa: BLE001
        raise RasterSamplingError(
            "CONTENT_WRITE_FAILED",
            "pyarrow is unavailable — the sample table cannot be persisted",
            field="outputs", status_code=500) from exc
    if len(rows) > MAX_SAMPLE_TABLE_ROWS:
        raise RasterSamplingError(
            "BUDGET_EXCEEDED",
            f"sample table has {len(rows)} rows (cap {MAX_SAMPLE_TABLE_ROWS})",
            field="outputs", status_code=413)
    cols = {
        "sample_id": [r.sample_id for r in rows],
        "station_index": [r.station_index for r in rows],
        "parent_locator": [r.parent_locator for r in rows],
        "source_ref": [r.source_ref for r in rows],
        "band": [r.band for r in rows],
        "band_label": [r.band_label for r in rows],
        "station_x": [r.station_x for r in rows],
        "station_y": [r.station_y for r in rows],
        "station_crs": [r.station_crs for r in rows],
        "part_id": [r.part_id for r in rows],
        "chainage_m": [r.chainage_m for r in rows],
        "source_x": [r.source_x for r in rows],
        "source_y": [r.source_y for r in rows],
        "row": [r.row for r in rows],
        "col": [r.col for r in rows],
        "raw_value": [r.raw_value for r in rows],
        "raw_exact": [getattr(r, "raw_exact", None) for r in rows],
        "physical_value": [r.physical_value for r in rows],
        "interpolated_value": [r.interpolated_value for r in rows],
        "value_domain": [r.value_domain for r in rows],
        "method": [r.method for r in rows],
        "unit": [r.unit for r in rows],
        "unit_status": [r.unit_status for r in rows],
        "valid": [r.valid for r in rows],
        "invalid_reason": [r.invalid_reason for r in rows],
        "support_ratio": [r.support_ratio for r in rows],
        "data_revision": [r.data_revision for r in rows],
    }
    return pa.table(cols)


async def write_sample_table(
    session_id: str, rows: Sequence[SampleRow], *, title: str
) -> Dict[str, Any]:
    """长表 → fabric GeoParquet 工件（G18 可交付 ref + 台账 + CAS）。"""
    from app.services.data_fabric.materialization_service import (
        MaterializationService,
    )

    table = await asyncio.to_thread(rows_to_arrow_table, list(rows))
    service = MaterializationService()
    out = await service.materialize_geoparquet(session_id, table, title)
    return out


def build_station_layer(
    station_set: Any, rows: Sequence[SampleRow], *, value_decimals: int = 6
) -> Dict[str, Any]:
    """站位层 GeoJSON：一站一要素（波段宽列 + 无效计数 + 站位定位）。"""
    value_by_key: Dict[Tuple[str, str, int], Any] = {}
    invalid_by_station: Dict[str, int] = {}
    band_keys: List[Tuple[str, int]] = []
    seen_bands: set = set()
    for r in rows:
        key = (r.source_ref, r.band)
        if key not in seen_bands:
            seen_bands.add(key)
            band_keys.append(key)
        primary = None
        if r.valid:
            primary = (r.physical_value if r.physical_value is not None
                       else (r.interpolated_value
                             if r.interpolated_value is not None
                             else r.raw_value))
        value_by_key[(r.sample_id, r.source_ref, r.band)] = (
            round(primary, value_decimals)
            if isinstance(primary, float) else primary)
        if not r.valid:
            invalid_by_station[r.sample_id] = (
                invalid_by_station.get(r.sample_id, 0) + 1)

    def _col_name(source_ref: str, band: int) -> str:
        idx = sorted(band_keys).index((source_ref, band)) + 1
        return f"v{idx}_{band}"

    features: List[Dict[str, Any]] = []
    for st in station_set.stations:
        if len(features) >= MAX_STATION_LAYER_FEATURES:
            break
        props: Dict[str, Any] = {
            "sample_id": st.sample_id,
            "parent_locator": st.parent_locator,
            "part_id": st.part_id,
            "chainage_m": (round(st.chainage_m, 3)
                           if st.chainage_m is not None else None),
            "station_index": st.station_index,
            "invalid_cells": invalid_by_station.get(st.sample_id, 0),
        }
        props.update(st.parent_attrs)
        for key in sorted(band_keys):
            v = value_by_key.get((st.sample_id, key[0], key[1]))
            props[_col_name(key[0], key[1])] = v
        features.append({
            "type": "Feature",
            "id": st.sample_id,
            "properties": props,
            "geometry": {
                "type": "Point",
                "coordinates": [round(st.x, 9), round(st.y, 9)],
            },
        })
    return {
        "type": "FeatureCollection",
        "features": features,
        "_g39_band_columns": {
            _col_name(k[0], k[1]): {"source_ref": k[0], "band": k[1]}
            for k in sorted(band_keys)
        },
    }


async def register_station_layer(
    session_id: str, station_fc: Dict[str, Any]
) -> Tuple[Optional[str], List[str]]:
    """站位层 → 会话 ref（预算不足诚实降级，不截断冒充全量）。"""
    degraded: List[str] = []
    from app.services.session_data import session_data_manager

    try:
        ref_id = await session_data_manager.store(
            session_id, station_fc, prefix=_STATION_REF_PREFIX)
        return str(ref_id), degraded
    except Exception:  # noqa: BLE001 — 会话字节预算不足
        degraded.append("STATION_LAYER_SKIPPED:session_budget")
        return None, degraded


def verify_readback(
    parquet_out: Dict[str, Any],
    rows: Sequence[SampleRow],
    station_fc: Dict[str, Any],
    station_count: int,
) -> Dict[str, Any]:
    """回读验证：行数 / schema / 抽样行值 / 站位要素数（不一致即失败）。"""
    import pyarrow.parquet as pq

    path = parquet_out.get("path")
    if not path or not Path(str(path)).is_file():
        raise RasterSamplingError(
            "READBACK_FAILED", "sample parquet missing after write",
            field="outputs", status_code=500)
    meta = pq.read_metadata(str(path))
    if int(meta.num_rows) != len(rows):
        raise RasterSamplingError(
            "READBACK_FAILED",
            f"parquet row count {meta.num_rows} != {len(rows)} produced rows",
            field="outputs", status_code=500)
    schema_names = set(pq.read_schema(str(path)).names)
    expected = {"sample_id", "source_ref", "band", "valid", "raw_value",
                "physical_value", "unit", "invalid_reason", "value_domain",
                "method", "data_revision"}
    missing = expected - schema_names
    if missing:
        raise RasterSamplingError(
            "READBACK_FAILED",
            f"parquet schema missing columns: {sorted(missing)}",
            field="outputs", status_code=500)
    # 抽样行值核验（固定种子，可复现）
    sample_size = min(24, len(rows))
    rng = random.Random(39)
    picked = rng.sample(range(len(rows)), sample_size) if rows else []
    table = pq.read_table(str(path))
    sid_col = table.column("sample_id").to_pylist()
    band_col = table.column("band").to_pylist()
    raw_col = table.column("raw_value").to_pylist()
    valid_col = table.column("valid").to_pylist()
    for i in picked:
        r = rows[i]
        row_idx = i  # 写入保序
        if (sid_col[row_idx] != r.sample_id or band_col[row_idx] != r.band
                or bool(valid_col[row_idx]) != bool(r.valid)):
            raise RasterSamplingError(
                "READBACK_FAILED",
                f"row {i} mismatch on readback (sample_id/band/valid)",
                field="outputs", status_code=500)
        produced = r.raw_value
        stored = raw_col[row_idx]
        if produced is None or stored is None:
            if produced is not stored and not (produced is None and stored is None):
                raise RasterSamplingError(
                    "READBACK_FAILED", f"row {i} raw value None-mismatch",
                    field="outputs", status_code=500)
        elif abs(float(stored) - float(produced)) > 0.0:
            raise RasterSamplingError(
                "READBACK_FAILED", f"row {i} raw value drift",
                field="outputs", status_code=500)
    fc_features = station_fc.get("features")
    if not isinstance(fc_features, list) or len(fc_features) != min(
            station_count, MAX_STATION_LAYER_FEATURES):
        raise RasterSamplingError(
            "READBACK_FAILED",
            f"station layer has {len(fc_features) if isinstance(fc_features, list) else 0} "
            f"features, expected {station_count}",
            field="outputs", status_code=500)
    return {
        "parquet_rows": int(meta.num_rows),
        "parquet_columns": int(meta.num_columns),
        "spot_checked_rows": len(picked),
        "station_features": len(fc_features),
    }


async def publish_products(
    *,
    session_id: str,
    plan_id: str,
    execution_id: str,
    station_set: Any,
    rows: Sequence[SampleRow],
    read_metrics_payload: Dict[str, Any],
    cost_estimate: Dict[str, Any],
    params_digest: str,
    plan_revision: int,
    method: str,
    job_id: Optional[str],
) -> ExecutionReceipt:
    """内容写入 → 回读验证 → 引用登记 → 收据（顺序兑现，缺一不发）。"""
    if len(rows) == 0:
        raise RasterSamplingError(
            "SAMPLING_FAILED",
            "sampling produced no rows — refusing to publish empty product",
            field="outputs", status_code=500)
    coverage = coverage_from_rows(rows, len(station_set.stations))
    # 1) 长表（fabric GeoParquet：G18 交付 ref + 台账 + DataObject CAS）
    parquet_out = await write_sample_table(
        session_id, rows, title=f"raster-sampling {plan_id} samples")
    # materialize_geoparquet 不回显磁盘路径 —— 路径单点派生（artifact_registry）
    from app.services.artifact_registry import fabric_parquet_path

    table_path = fabric_parquet_path(session_id, str(parquet_out.get("ref") or ""))
    parquet_out["path"] = str(table_path) if table_path else ""
    # 2) 站位层构造（先内容）+ 3) 回读验证 —— 全部通过后才登记引用
    station_fc = await asyncio.to_thread(
        build_station_layer, station_set, list(rows))
    readback = await asyncio.to_thread(
        verify_readback, parquet_out, list(rows), station_fc,
        len(station_set.stations))
    station_ref, degraded = await register_station_layer(session_id, station_fc)
    # 4) 血缘台账（失败降级披露，不阻断）
    try:
        from app.services.artifact_registry import register_artifact

        await register_artifact(
            session_id,
            artifact_id=str(parquet_out.get("ref") or ""),
            artifact_type="fabric_geoparquet",
            producer_tool="raster_sampling_execute",
            inputs=[],
            descriptor={
                "g39_execution_id": execution_id,
                "g39_plan_id": plan_id,
                "row_count": len(rows),
                "station_count": len(station_set.stations),
                "coverage": coverage.model_dump(),
            },
            metadata={
                "algorithm_version": SAMPLING_ALGORITHM_VERSION,
                "method": method,
                "params_digest": params_digest,
            },
        )
    except Exception:  # noqa: BLE001
        degraded.append("ARTIFACT_REGISTER_SKIPPED")
    receipt = ExecutionReceipt(
        execution_id=execution_id,
        plan_id=plan_id,
        plan_revision=plan_revision,
        session_id=session_id,
        params_digest=params_digest,
        algorithm_version=SAMPLING_ALGORITHM_VERSION,
        method=method,
        mode=station_set.mode,
        station_count=len(station_set.stations),
        geometry_hash=station_set.geometry_hash(),
        coverage=coverage,
        read_metrics={**read_metrics_payload, "cost_estimate": cost_estimate,
                      "readback": readback},
        outputs={
            "sample_table_ref": str(parquet_out.get("ref") or ""),
            "sample_table_path": str(parquet_out.get("path") or ""),
            "sample_table_sha256": str(parquet_out.get("content_sha256") or ""),
            "data_object_id": str(parquet_out.get("data_object_id") or ""),
            "station_layer_ref": station_ref or "",
            "band_columns": station_fc.get("_g39_band_columns", {}),
            "value_decimals": 6,
        },
        job_id=job_id,
        status="completed",
        degraded=degraded,
    )
    return receipt


# ── 显示曲线抽稀（保端点 / 缺口 / 极值；方法披露） ────────────────────

def thin_series(
    points: List[Dict[str, Any]], *, limit: int = MAX_CURVE_POINTS_PER_BAND
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """显示抽稀：必保首末点、断口后首点、局部极值；其余均匀。

    ``points``: [{x, y, sid, gap_before}]（已按 x 排序；y=None 表示无效
    站位 —— 保留为缺口，不连线）。
    """
    n = len(points)
    if n <= limit:
        return points, {"method": "none", "kept": n, "total": n}
    keep_idx: set = set()
    keep_idx.add(0)
    keep_idx.add(n - 1)
    # 缺口边界（gap_before 或 y is None 的前后点）
    for i, p in enumerate(points):
        if p.get("gap_before") or p.get("y") is None:
            keep_idx.add(i)
            if i > 0:
                keep_idx.add(i - 1)
            if i < n - 1:
                keep_idx.add(i + 1)
    # 相邻保留点间取局部极值
    sorted_idx = sorted(keep_idx)
    for a, b in zip(sorted_idx, sorted_idx[1:]):
        span = points[a:b + 1]
        finite = [(p["y"], i) for i, p in zip(range(a, b + 1), span)
                  if p.get("y") is not None]
        if not finite:
            continue
        vmax = max(finite, key=lambda t: t[0])
        vmin = min(finite, key=lambda t: t[0])
        keep_idx.add(vmax[1])
        keep_idx.add(vmin[1])
    # 仍超限 → 在保留点间均匀补间
    if len(keep_idx) > limit:
        keep_idx = set(sorted(keep_idx)[:limit])
    kept_sorted = sorted(keep_idx)
    if len(kept_sorted) < limit:
        need = limit - len(kept_sorted)
        candidates = [i for i in range(n) if i not in keep_idx]
        if candidates:
            step = max(1, len(candidates) // max(1, need))
            for c in candidates[::step][:need]:
                keep_idx.add(c)
    final = sorted(keep_idx)
    return ([points[i] for i in final],
            {"method": "endpoint_gap_extrema_uniform",
             "kept": len(final), "total": n})


def load_rows_for_curves(parquet_path: str) -> List[Dict[str, Any]]:
    """长表 → 曲线原料（仅曲线列；分页读，行组流式）。"""
    import pyarrow.parquet as pq

    table = pq.read_table(
        parquet_path,
        columns=["sample_id", "source_ref", "band", "chainage_m",
                 "station_index", "physical_value", "interpolated_value",
                 "raw_value", "valid", "part_id"])
    return table.to_pylist()


def build_display_curves(
    rows: List[Dict[str, Any]], *, part_breaks: Dict[str, Any] = None
) -> Dict[str, Any]:
    """(source, band) → 抽稀曲线 + 单位/缺口事实（结果 API 用）。"""
    series: Dict[Tuple[str, int], List[Dict[str, Any]]] = {}
    for r in rows:
        key = (r["source_ref"], int(r["band"]))
        primary = (r.get("physical_value")
                   if r.get("physical_value") is not None
                   else (r.get("interpolated_value")
                         if r.get("interpolated_value") is not None
                         else r.get("raw_value")))
        x = (r.get("chainage_m") if r.get("chainage_m") is not None
             else r.get("station_index"))
        series.setdefault(key, []).append({
            "sid": r["sample_id"],
            "part": r.get("part_id") or "",
            "x": x,
            "y": primary if r.get("valid") else None,
            "valid": bool(r.get("valid")),
        })
    out: Dict[str, Any] = {"bands": [], "thinning": {}}
    for key in sorted(series):
        pts = series[key]
        pts.sort(key=lambda p: (p["part"], p["x"] if p["x"] is not None else 0))
        # part 断口：与前一站位 part 不同 → gap_before
        for i in range(1, len(pts)):
            pts[i]["gap_before"] = pts[i]["part"] != pts[i - 1]["part"]
        pts[0]["gap_before"] = False
        thinned, facts = thin_series(pts)
        out["bands"].append({
            "source_ref": key[0], "band": key[1],
            "points": [{"sid": p["sid"], "x": p["x"], "y": p["y"],
                        "part": p["part"], "gap": bool(p.get("gap_before"))}
                       for p in thinned],
        })
        out["thinning"][f"{key[0]}#{key[1]}"] = facts
    return out
