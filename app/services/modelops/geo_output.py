"""Geo vector output sinks —— GeoJSON → PostGIS / 文件（V3 §D）。

发布通道（honest degradation）：

- **PostGIS**：``MODELOPS_POSTGIS_DSN``（或显式 dsn 参数）存在且
  geopandas + sqlalchemy + psycopg 驱动可用时，经
  ``geopandas.GeoDataFrame.to_postgis`` 落库；任一前置缺失 →
  ``{"published": False, "reason": ...}`` 如实返回（不抛、不虚报）；
- 表名白名单 ``^[a-z_][a-z0-9_]{0,62}$``（标识符注入面封闭）；
- ``if_exists`` 封闭词表 {fail, append}——**没有 replace**（静默毁表
  不在本平面授权范围内）。

GeoJSON 文件产物由 ``artifacts.write_geojson_output`` 负责（始终可用）。
"""
from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

POSTGIS_DSN_ENV = "MODELOPS_POSTGIS_DSN"
_TABLE_NAME_RE = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")
_IF_EXISTS = frozenset({"fail", "append"})


def validate_table_name(table: str) -> str:
    """PostGIS 表名白名单（非法 = typed 拒绝，绝不拼接进 SQL）。"""
    if not table or not _TABLE_NAME_RE.match(table):
        raise ValueError(
            f"invalid PostGIS table name {table!r} "
            "(expected ^[a-z_][a-z0-9_]{0,62}$)"
        )
    return table


def publish_geojson_to_postgis(
    feature_collection: Dict[str, Any],
    *,
    table: str,
    dsn: Optional[str] = None,
    if_exists: str = "fail",
) -> Dict[str, Any]:
    """GeoJSON FeatureCollection → PostGIS 表（前置缺失 = honest skip）。

    返回 ``{"published": bool, ...}``：失败/跳过**不抛异常**（产物已
    有 GeoJSON 文件兜底；PostGIS 是增量通道，不是唯一真相）。
    """
    validate_table_name(table)
    if if_exists not in _IF_EXISTS:
        raise ValueError(f"if_exists must be one of {sorted(_IF_EXISTS)} (got {if_exists!r})")
    # 校验失败也走 honest skip（返回 reason 而非抛——引擎在推理完成后
    # 调用本函数，抛错会毁掉已完成的推理产物）。
    features = feature_collection.get("features")
    if not isinstance(features, list) or not features:
        return {"published": False, "reason": "empty feature collection"}
    if not _TABLE_NAME_RE.match(table or ""):
        return {"published": False, "reason": f"invalid table name {table!r}"}
    if if_exists not in _IF_EXISTS:
        return {"published": False, "reason": f"invalid if_exists {if_exists!r}"}
    resolved_dsn = dsn or os.environ.get(POSTGIS_DSN_ENV, "")
    if not resolved_dsn:
        return {"published": False, "reason": f"no DSN ({POSTGIS_DSN_ENV} unset)"}
    try:
        import geopandas as gpd
    except Exception as exc:  # noqa: BLE001 — 重依赖缺席是常态
        return {"published": False, "reason": f"geopandas unavailable: {exc}"}
    try:
        import sqlalchemy  # noqa: F401 — 可用性探测（驱动在 create_engine 时消费）
    except Exception as exc:  # noqa: BLE001
        return {"published": False, "reason": f"sqlalchemy unavailable: {exc}"}

    try:
        crs = feature_collection.get("crs")
        if isinstance(crs, dict):
            crs = (crs.get("properties") or {}).get("name")
        frame = gpd.GeoDataFrame.from_features(features, crs=crs)
    except Exception as exc:  # noqa: BLE001
        return {"published": False, "reason": f"invalid features: {exc}"}
    if frame.geometry.isna().any():
        return {"published": False, "reason": "feature with null geometry"}
    try:
        from sqlalchemy import create_engine

        engine = create_engine(resolved_dsn)
        frame.to_postgis(table, engine, if_exists=if_exists, index=False)
    except Exception as exc:  # noqa: BLE001 — 连接/驱动失败如实上报
        logger.warning("postgis publish to %s failed: %s", table, exc)
        return {"published": False, "reason": f"postgis write failed: {exc}"}
    return {
        "published": True,
        "table": table,
        "feature_count": int(len(frame)),
        "crs": frame.crs and str(frame.crs) or None,
    }


def write_geojson_vector_output(path: Path, feature_collection: Dict[str, Any]) -> Path:
    """GeoJSON 文件兜底通道（UTF-8；compact）。"""
    import json

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(feature_collection, ensure_ascii=False), encoding="utf-8")
    return path


__all__ = [
    "POSTGIS_DSN_ENV",
    "publish_geojson_to_postgis",
    "validate_table_name",
    "write_geojson_vector_output",
]
