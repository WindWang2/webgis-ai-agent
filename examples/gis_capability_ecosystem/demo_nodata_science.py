"""离线演示 #2：严格量纲 / nodata 科学场景（G06 / CAP-03 + CAP-06）。

真实链路：生成含 nodata 的单波段 GeoTIFF → 真实 dispatch
``conditional_quantile_zonal_stats`` → 分位数 + 有效样本分母 + 覆盖率 +
科学方法披露 + 预算 → 秒≠米量纲冲突 typed 错误回归。

无 API Key、无 PostGIS、无网络。运行::

    python examples/gis_capability_ecosystem/demo_nodata_science.py
"""
from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import numpy as np  # noqa: E402
import rasterio  # noqa: E402
from rasterio.transform import from_origin  # noqa: E402

from app.tools import init_tools  # noqa: E402
from app.tools.registry import ToolRegistry  # noqa: E402

ZONES = {
    "type": "FeatureCollection",
    "features": [
        {"type": "Feature", "properties": {"zone_id": "A"},
         "geometry": {"type": "Polygon", "coordinates": [[
             [0, 10], [10, 10], [10, 0], [0, 0], [0, 10]]]}},
    ],
}
NETWORK = {
    "type": "FeatureCollection",
    "features": [
        {"type": "Feature", "properties": {"edge": "e1"},
         "geometry": {"type": "LineString", "coordinates": [[0, 0], [2, 0]]}},
        {"type": "Feature", "properties": {"edge": "e2"},
         "geometry": {"type": "LineString", "coordinates": [[2, 0], [2, 2]]}},
    ],
}
FACILITIES = {
    "type": "FeatureCollection",
    "features": [
        {"type": "Feature", "properties": {"scheme": "A", "id": "f1"},
         "geometry": {"type": "Point", "coordinates": [0, 0]}},
        {"type": "Feature", "properties": {"scheme": "B", "id": "f2"},
         "geometry": {"type": "Point", "coordinates": [0, 0]}},
    ],
}


def run() -> dict:
    reg = ToolRegistry()
    init_tools(reg)
    out: dict = {"scenario": "nodata + 条件分位数 + 秒≠米量纲冲突（离线）"}

    # 新原语复用既有 validate_data_path（只允许 ./data 与 ./tmp），
    # 因此把离线 fixture 写在仓库 ./tmp 工作区（.gitignore 已忽略）。
    work = Path(__file__).resolve().parents[2] / "tmp" / "g06_demo"
    work.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=str(work)) as td:
        path = Path(td) / "grid.tif"
        data = np.arange(100, dtype="float32").reshape(10, 10)
        data[0, 0] = -9999.0   # nodata
        data[1, 1] = np.nan    # 非有限值
        with rasterio.open(
            path, "w", driver="GTiff", height=10, width=10, count=1,
            dtype="float32", nodata=-9999.0, crs="EPSG:3857",
            transform=from_origin(0, 10, 1, 1),
        ) as ds:
            ds.write(data, 1)

        for where in ("value > 50", "value > 90"):
            result = asyncio.run(reg.dispatch(
                "conditional_quantile_zonal_stats", {
                    "zones": ZONES, "raster_path": str(path),
                    "quantiles": [0.25, 0.5, 0.75],
                    "where": where, "nodata": -9999.0,
                }))
            if not isinstance(result, dict):
                raise RuntimeError(f"unexpected result: {type(result).__name__}")
            rows = (result.get("data") or {}).get("zones") or []
            out[f"quantiles_{where.replace(' ', '_').replace('>', 'gt')}"] = {
                "valid_pixel_count": rows[0].get("valid_pixel_count") if rows else None,
                "quantiles": rows[0].get("quantiles") if rows else None,
                "effective_denominator": (result.get("science") or {}).get(
                    "effective_denominator"),
                "artifact_ref": (result.get("artifact") or {}).get("ref"),
                "budget": result.get("budget"),
            }

    # CAP-O06：秒（时间量纲）+ 请求米制距离输出 → typed 错误，绝不静默成功。
    conflict = asyncio.run(reg.dispatch(
        "network_service_coverage_consistency", {
            "network": NETWORK, "facilities": FACILITIES,
            "cutoff": 2.0, "cost_unit": "seconds", "metric": "distance",
        }))
    out["unit_dimension_conflict"] = {
        "code": conflict.get("code") if isinstance(conflict, dict) else None,
        "message": conflict.get("message") if isinstance(conflict, dict) else None,
        "expected": "UNIT_DIMENSION_CONFLICT",
        "honest_disclosure": "秒≠米；时间成本不可冒充空间距离",
    }

    # 正常米制调用（一致性应为 1.0）。
    ok = asyncio.run(reg.dispatch(
        "network_service_coverage_consistency", {
            "network": NETWORK, "facilities": FACILITIES,
            "cutoff": 2.0, "cost_unit": "meters",
        }))
    out["metric_consistency"] = {
        "jaccard": (ok.get("data") or {}).get("jaccard") if isinstance(ok, dict) else None,
        "science": (ok.get("science") or {}).get("unit_contract")
        if isinstance(ok, dict) else None,
    }
    out["data_source_disclosure"] = (
        "本地合成 GeoTIFF / GeoJSON fixture；非真实观测数据，"
        "外部数据缺席时标 not_evaluated，不宣称已验证的科学方法")
    return out


if __name__ == "__main__":
    sys.stdout.write(
        json.dumps(run(), ensure_ascii=False, indent=2, default=str) + "\n")
