"""Raster lakehouse publication — Spatial Lakehouse V6 (Wave 5, ADR-0118).

COG/GeoTIFF → durable DataObject（blob + manifest，BlobStore CAS）：

- **内容寻址身份**：文件流式 sha256（有界预算内整读入 BlobStore，恒计算
  文件摘要）；
- **grid identity**：header-only 投影（width/height/crs/transform/dtype/
  band_count/block_shape）—— 与 chunk 契约同字段口径，零像素 IO；
- **chunk checksums**：块级摘要（block window 网格），仅对 ≤
  chunk manifest 预算的文件计算（大文件诚实跳过 —— 文件级摘要已是身份）；
  DR 由此可定位损坏块；
- **owner scope**：session/project 恰一；超界/失败 typed 或 None+reason
  （诚实降级，绝不假装持久）。

窗口读路径（lazy materialization）不在此模块：``RasterReader.read_window``
是既有唯一窗口读权威（V4 红线：无第二窗口运行时）。
"""
from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from app.services.lakehouse.data_object import (
    DataObjectError,
    normalize_owner_scope,
    publish_data_object,
)

logger = logging.getLogger(__name__)

#: chunk checksums 的文件尺寸预算（超过则只给文件级摘要，诚实缺省）。
DEFAULT_CHUNK_MANIFEST_BUDGET_BYTES = 64 * 1024 * 1024
#: chunk 摘要条数硬界（与 manifest blob 数闸同量级）。
_MAX_CHUNK_DIGESTS = 65_536


def grid_identity_from_path(path: Union[str, "Path"]) -> Dict[str, Any]:
    """header-only 网格身份（rasterio 一次 open('r')，零像素读）。"""
    import rasterio

    with rasterio.open(str(path)) as ds:
        return {
            "width": int(ds.width),
            "height": int(ds.height),
            "crs": (str(ds.crs) if ds.crs else ""),
            "transform": [float(v) for v in ds.transform[:6]],
            "dtype": str(ds.dtypes[0]) if ds.dtypes else "",
            "band_count": int(ds.count),
            "block_shape": [int(v) for v in (ds.block_shapes[0] if ds.block_shapes else (0, 0))],
            "nodata": (float(ds.nodata) if ds.nodata is not None else None),
        }


def _chunk_checksums(path: Path, file_size: int) -> Optional[Dict[str, Any]]:
    """块级摘要（≤预算才算；返回 None = 诚实跳过）。"""
    if file_size > DEFAULT_CHUNK_MANIFEST_BUDGET_BYTES:
        return None
    try:
        import rasterio

        with rasterio.open(str(path)) as ds:
            blocks = list(ds.block_windows(1))
            if len(blocks) > _MAX_CHUNK_DIGESTS:
                return None
            digests: Dict[str, str] = {}
            for (_, _), window in blocks:
                data = ds.read(1, window=window)
                digest = hashlib.sha256(data.tobytes()).hexdigest()
                digests[f"{window.col_off}_{window.row_off}"] = digest
            return {
                "block_shape": [int(v) for v in ds.block_shapes[0]],
                "digests": digests,
            }
    except Exception as e:  # noqa: BLE001 — 块摘要失败 = 诚实跳过（非致命）
        logger.warning("[raster_object] chunk checksums skipped for %s: %s", path, e)
        return None


def publish_cog_data_object(
    path: Union[str, Path],
    *,
    session_id: Optional[str] = None,
    project_id: Optional[str] = None,
    source_refs: Optional[List[str]] = None,
    producer: Optional[Dict[str, Any]] = None,
    max_total_bytes: int = 2 * 1024 ** 3,
) -> Dict[str, Any]:
    """COG/GeoTIFF 文件 → DataObject。返回身份 + 证据（诚实跳过 reason）。

    成功：``{"published": True, "data_object_id", "content_sha256",
    "manifest", "grid": {...}, "chunk_checksums": bool}``；
    跳过：``{"published": False, "reason": "oversized"|"owner_missing"|"failed"}``。
    """
    p = Path(path)
    try:
        owner_scope = normalize_owner_scope(
            session_id=session_id, project_id=project_id
        )
    except DataObjectError:
        return {"published": False, "reason": "owner_missing"}
    try:
        file_size = p.stat().st_size
    except OSError:
        return {"published": False, "reason": "failed"}
    if file_size > max_total_bytes:
        return {"published": False, "reason": "oversized"}

    content_sha256 = _streaming_sha256(p)
    try:
        grid = grid_identity_from_path(p)
        chunk_manifest = _chunk_checksums(p, file_size)
        payload: Dict[str, Any] = {
            "content_sha256": content_sha256,
            "grid": grid,
        }
        if chunk_manifest is not None:
            payload["chunk_checksums"] = chunk_manifest
        identity = publish_data_object(
            {"data.tif": p},
            kind="cog_raster",
            owner_scope=owner_scope,
            payload=payload,
            producer=producer or {"capability": "raster.cog_conversion"},
            source_refs=source_refs or [],
            input_fingerprint=content_sha256,
        )
    except DataObjectError as e:
        logger.warning("[raster_object] publish refused for %s: %s", p, e)
        return {"published": False, "reason": str(e)}
    except Exception as e:  # noqa: BLE001 — 发布失败诚实降级
        logger.warning("[raster_object] publish failed for %s: %s", p, e)
        return {"published": False, "reason": "failed"}
    return {
        "published": True,
        "data_object_id": identity.data_object_id,
        "content_sha256": content_sha256,
        "manifest": identity.manifest_location,
        "deduped": identity.deduped,
        "grid": grid,
        "chunk_checksums": chunk_manifest is not None,
    }


def _streaming_sha256(path: Path) -> str:
    from app.lib.data.fingerprints import sha256_of_file

    return sha256_of_file(path)
