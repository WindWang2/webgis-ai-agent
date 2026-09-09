"""Vector lakehouse window scan — Spatial Lakehouse V6 (Wave 4, ADR-0118).

lazy materialization 的 vector 侧读路径：**ref-only + row-group 剪枝**。

- ``scan_parquet_window``：打开 ParquetFile，读 schema metadata
  ``webgis:row_groups``（vector_carrier 写入侧从真实几何算得的 bbox 地图），
  与查询窗口求交 → **只读相交 row groups**；无剪枝元数据（外来文件/诚实
  降级）→ 有界顺序读（row 预算照常生效，正确性不受影响）。
- ``scan_fabric_parquet_ref``：``ref:fabric-parquet/<id>`` 的会话域解析 +
  扫描（路径经 ``artifact_registry.fabric_parquet_path`` 唯一派生，
  charset 白名单在边界拒绝 traversal）。

预算纪律：``max_rows`` 硬界（超出 = honest ``truncated``）；返回体携带
``row_groups_total`` / ``row_groups_read``（结构性证据：窗口读 ≠ 全量读，
测试据此断言剪枝真实发生）。
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

logger = logging.getLogger(__name__)

#: schema metadata 键（与 vector_carrier 写入侧同一常量 —— 单点真相）。
_ROW_GROUP_BBOX_META_KEY = b"webgis:row_groups"


class LakehouseScanError(RuntimeError):
    """窗口扫描失败（typed；调用方诚实披露，绝不返回半真数据）。"""

    code = "LAKEHOUSE_SCAN_FAILED"

    def __init__(self, message: str, *, code: Optional[str] = None):
        super().__init__(message)
        self.message = message
        if code:
            self.code = code

    def to_dict(self) -> dict:
        return {"success": False, "code": self.code, "message": self.message}


def _bbox_intersects(a: Sequence[float], b: Sequence[float]) -> bool:
    """闭区间 bbox 相交（[minx, miny, maxx, maxy]）。"""
    return not (a[0] > b[2] or b[0] > a[2] or a[1] > b[3] or b[1] > a[3])


def _parse_bbox(value: Any) -> Optional[Tuple[float, float, float, float]]:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    try:
        return tuple(float(v) for v in value)  # type: ignore[return-value]
    except (TypeError, ValueError):
        return None


def _row_group_plan(parquet_file: Any) -> List[Optional[Tuple[float, float, float, float]]]:
    """row-group bbox 计划（元数据缺席的组 = None ⇒ 恒读，保守正确）。"""
    meta = parquet_file.schema_arrow.metadata or {}
    raw = meta.get(_ROW_GROUP_BBOX_META_KEY)
    if not raw:
        return [None] * parquet_file.num_row_groups
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        return [None] * parquet_file.num_row_groups
    groups = parsed.get("row_groups") if isinstance(parsed, dict) else None
    if not isinstance(groups, list) or len(groups) != parquet_file.num_row_groups:
        # 元数据与物理布局不符（外来改写）→ 全部保守读。
        return [None] * parquet_file.num_row_groups
    plan: List[Optional[Tuple[float, float, float, float]]] = []
    for g in groups:
        bbox = _parse_bbox((g or {}).get("bbox"))
        plan.append(bbox)  # None（组内全空几何）= 保守读
    return plan


def scan_parquet_window(
    path: Union[str, "Path"],
    bbox: Sequence[float],
    *,
    columns: Optional[Sequence[str]] = None,
    max_rows: int = 50_000,
) -> Dict[str, Any]:
    """bbox 窗口扫描：只读相交 row groups，返回 GeoJSON features + 剪枝证据。

    - ``columns`` 投影（几何列恒含）；``max_rows`` 硬预算；
    - 返回 ``{"type": "FeatureCollection", "features": [...], "properties":
      {"row_groups_total", "row_groups_read", "truncated", "window": bbox}}``；
    - 文件缺失/不可读/窗口非法 → typed :class:`LakehouseScanError`。
    """
    window = _parse_bbox(bbox)
    if window is None:
        raise LakehouseScanError(f"invalid bbox window: {bbox!r}")
    if max_rows <= 0:
        raise LakehouseScanError("max_rows must be positive")
    p = Path(path)
    if not p.is_file():
        raise LakehouseScanError(
            f"parquet artifact not found: {p}", code="LAKEHOUSE_REF_MISSING"
        )
    try:
        import pyarrow.parquet as pq

        from app.services.data_fabric.vector_carrier import arrow_to_features

        pf = pq.ParquetFile(str(p))
    except LakehouseScanError:
        raise
    except Exception as e:  # noqa: BLE001 — 打不开的文件 = typed 失败
        raise LakehouseScanError(f"cannot open parquet artifact: {e}") from e

    plan = _row_group_plan(pf)
    total = pf.num_row_groups
    read_groups = 0
    features: List[Dict[str, Any]] = []
    truncated = False
    geom_cols = _geometry_column_names(pf)
    wanted: Optional[List[str]] = None
    if columns is not None:
        wanted = [str(c) for c in columns if c not in geom_cols]
        wanted += [c for c in geom_cols if c not in wanted]

    for rg_index in range(total):
        group_bbox = plan[rg_index]
        if group_bbox is not None and not _bbox_intersects(group_bbox, window):
            continue  # 剪枝：该 row group 与窗口无交
        if len(features) >= max_rows:
            truncated = True
            break
        try:
            table = pf.read_row_group(rg_index, columns=wanted)
        except Exception as e:  # noqa: BLE001 — 组读失败 = typed 失败
            raise LakehouseScanError(f"row group {rg_index} read failed: {e}") from e
        read_groups += 1
        batch_features = arrow_to_features(table)
        remaining = max_rows - len(features)
        if len(batch_features) > remaining:
            features.extend(batch_features[:remaining])
            truncated = True
            break
        features.extend(batch_features)
    return {
        "type": "FeatureCollection",
        "features": features,
        "properties": {
            "row_groups_total": int(total),
            "row_groups_read": int(read_groups),
            "truncated": truncated,
            "window": [float(v) for v in window],
        },
    }


def _geometry_column_names(parquet_file: Any) -> List[str]:
    """GeoParquet geo 元数据声明的几何列名（缺席回退 'geometry'）。"""
    meta = parquet_file.schema_arrow.metadata or {}
    raw = meta.get(b"geo")
    if raw:
        try:
            geo = json.loads(raw)
            names = [str(k) for k in (geo.get("columns") or {})]
            if names:
                return names
        except (ValueError, TypeError):
            pass
    schema_names = parquet_file.schema_arrow.names
    return ["geometry"] if "geometry" in schema_names else []


def scan_fabric_parquet_ref(
    session_id: str,
    ref: str,
    bbox: Sequence[float],
    *,
    columns: Optional[Sequence[str]] = None,
    max_rows: int = 50_000,
) -> Dict[str, Any]:
    """会话域 ref 解析 + 窗口扫描（owner scope = session）。

    ref 不合法 / 文件不存活 → typed ``LakehouseScanError``（绝不把死 ref
    扫成空结果假装成功 —— 真实性契约）。
    """
    from app.services.artifact_registry import (
        fabric_parquet_path,
        fabric_parquet_ref_exists,
        is_fabric_parquet_ref,
    )

    if not is_fabric_parquet_ref(ref) or not isinstance(session_id, str) or not session_id:
        raise LakehouseScanError(f"not a fabric-parquet ref: {str(ref)[:64]!r}")
    path = fabric_parquet_path(session_id, ref)
    if path is None or not fabric_parquet_ref_exists(session_id, ref):
        raise LakehouseScanError(
            f"fabric-parquet artifact not alive: {ref}",
            code="LAKEHOUSE_REF_MISSING",
        )
    return scan_parquet_window(path, bbox, columns=columns, max_rows=max_rows)
