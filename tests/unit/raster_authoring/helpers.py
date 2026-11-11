"""G20 GeoTIFF 工厂与 ref 登记（测试间共享，不签入二进制）。"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

import numpy as np

from app.services.session_data import session_data_manager


def write_tif(
    path: Path,
    *,
    bands: int = 1,
    width: int = 8,
    height: int = 8,
    dtype: str = "float32",
    nodata: Optional[float] = None,
    crs: Optional[str] = "EPSG:4326",
    transform: Optional[Any] = None,
    tags: Optional[Dict[str, str]] = None,
    band_tags: Optional[Dict[int, Dict[str, str]]] = None,
    values: Optional[np.ndarray] = None,
) -> Path:
    """生成已知答案栅格：第 b 波段 = (b+1)*100 + row*10 + col。"""
    import rasterio
    from rasterio.transform import from_origin

    if transform is None:
        transform = from_origin(10.0, 20.0, 0.01, 0.01)
    data = values
    if data is None:
        rows, cols = np.mgrid[0:height, 0:width]
        data = np.stack(
            [(b + 1) * 100 + rows * 10 + cols for b in range(bands)]
        ).astype(dtype)
    kwargs: Dict[str, Any] = dict(
        driver="GTiff", width=width, height=height, count=bands,
        dtype=dtype, crs=crs, transform=transform,
    )
    if nodata is not None:
        kwargs["nodata"] = nodata
    with rasterio.open(path, "w", **kwargs) as dst:
        dst.write(data)
        for b in range(1, bands + 1):
            dst.set_band_description(b, f"src band {b}")
            dst.update_tags(b, **(band_tags or {}).get(b, {}))
        if tags:
            dst.update_tags(**tags)
    return path


async def register_raster_ref(session_id: str, path: Path) -> str:
    ref_id = await session_data_manager.store(
        session_id,
        {"kind": "numeric_raster", "file_path": str(path)},
        prefix="raster",
    )
    return str(ref_id)
