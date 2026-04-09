#!/usr/bin/env python3
"""DEM 数据获取 MCP Server

支持两个免费数据源：
  1. Copernicus DEM GLO-30（30m 全球，AWS S3 公开 COG，无需认证）
  2. OpenTopography API（支持 SRTMGL1/COP30/NASADEM/AW3D30 等，需免费 API Key）

工具列表：
  fetch_dem_copernicus      - 从 Copernicus DEM 按 bbox 获取 GeoTIFF（无需账号）
  fetch_dem_opentopography  - 从 OpenTopography API 获取多种 DEM（需 API Key）
  dem_quick_stats           - 快速读取 DEM 文件高程统计（最高/最低/平均）
"""
import json
import math
import os
import tempfile

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("gdal-dem-source")

# OpenTopography API Key - 优先读环境变量，其次读项目 .env
_OT_API_KEY = os.environ.get("OPENTOPOGRAPHY_API_KEY", "")

# Copernicus DEM S3 公开 URL 模板（eu-central-1，无需认证）
_COP_URL = (
    "https://copernicus-dem-30m.s3.amazonaws.com/"
    "Copernicus_DSM_COG_10_{NS}{lat:02d}_00_{EW}{lon:03d}_00_DEM/"
    "Copernicus_DSM_COG_10_{NS}{lat:02d}_00_{EW}{lon:03d}_00_DEM.tif"
)


def _cop_tile_url(lat_floor: int, lon_floor: int) -> str:
    """生成单个 Copernicus DEM 瓦片的 URL（1°×1° 瓦片）"""
    ns = "N" if lat_floor >= 0 else "S"
    ew = "E" if lon_floor >= 0 else "W"
    return _COP_URL.format(
        NS=ns, lat=abs(lat_floor),
        EW=ew, lon=abs(lon_floor),
    )


def _bbox_tiles(west: float, south: float, east: float, north: float) -> list[tuple[int, int]]:
    """根据 bbox 返回需要的所有 1°×1° 瓦片坐标（lat_floor, lon_floor）"""
    tiles = []
    for lat in range(math.floor(south), math.ceil(north)):
        for lon in range(math.floor(west), math.ceil(east)):
            tiles.append((lat, lon))
    return tiles


@mcp.tool()
def fetch_dem_copernicus(
    west: float, south: float, east: float, north: float,
    output: str = "",
    clip_to_bbox: bool = True,
) -> dict:
    """从 Copernicus DEM GLO-30 按 bbox 下载 30m 分辨率 DEM，返回 GeoTIFF 路径。

    无需任何账号，直接从 AWS S3 公开 COG 拉取。
    bbox 较大时自动合并多个瓦片（每个瓦片约 40MB）。

    west/south/east/north: WGS84 经纬度范围
    output: 输出文件路径，空则写入临时目录
    clip_to_bbox: 是否裁剪到精确 bbox（默认 True，避免多余数据）
    """
    import httpx
    from osgeo import gdal
    gdal.UseExceptions()

    if not output:
        fd, output = tempfile.mkstemp(suffix="_cop30.tif", prefix="dem_")
        os.close(fd)

    tiles = _bbox_tiles(west, south, east, north)
    if not tiles:
        return {"error": "Invalid bbox"}

    # 用 httpx 直接下载瓦片（绕过 GDAL vsicurl curl 版本冲突）
    tile_paths = []
    tmp_files = []
    for lat_floor, lon_floor in tiles:
        url = _cop_tile_url(lat_floor, lon_floor)
        resp = httpx.head(url, timeout=10, follow_redirects=True)
        if resp.status_code != 200:
            continue  # 海洋/无数据区域
        resp = httpx.get(url, timeout=300, follow_redirects=True)
        if resp.status_code != 200:
            continue
        fd, tile_path = tempfile.mkstemp(suffix=".tif", prefix="cop30_tile_")
        os.close(fd)
        with open(tile_path, "wb") as f:
            f.write(resp.content)
        tile_paths.append(tile_path)
        tmp_files.append(tile_path)

    if not tile_paths:
        return {"error": "No Copernicus DEM tiles found for this bbox (may be ocean area)"}

    try:
        if len(tile_paths) == 1:
            src = tile_paths[0]
        else:
            vrt_path = output.replace(".tif", ".vrt")
            vrt = gdal.BuildVRT(vrt_path, tile_paths)
            vrt.FlushCache()
            vrt = None
            src = vrt_path
            tmp_files.append(vrt_path)

        if clip_to_bbox:
            gdal.Warp(
                output, src,
                options=gdal.WarpOptions(
                    outputBounds=(west, south, east, north),
                    outputBoundsSRS="EPSG:4326",
                    dstNodata=-9999,
                    resampleAlg="bilinear",
                ),
            )
        else:
            gdal.Translate(output, src)
    finally:
        for p in tmp_files:
            try:
                os.unlink(p)
            except OSError:
                pass

    ds = gdal.Open(output)
    band = ds.GetRasterBand(1)
    bmin, bmax, bmean, bstd = band.GetStatistics(False, True)
    gt = ds.GetGeoTransform()
    return {
        "output": output,
        "source": "Copernicus DEM GLO-30",
        "resolution_m": 30,
        "bbox": {"west": west, "south": south, "east": east, "north": north},
        "size": {"width": ds.RasterXSize, "height": ds.RasterYSize},
        "pixel_size_deg": abs(gt[1]),
        "elevation": {
            "min_m": round(bmin, 1),
            "max_m": round(bmax, 1),
            "mean_m": round(bmean, 1),
            "std_m": round(bstd, 1),
        },
        "tiles_used": len(tile_paths),
    }


@mcp.tool()
def fetch_dem_opentopography(
    west: float, south: float, east: float, north: float,
    dem_type: str = "COP30",
    output: str = "",
    api_key: str = "",
) -> dict:
    """从 OpenTopography API 获取 DEM 数据，返回 GeoTIFF 路径。

    需要免费 API Key（https://opentopography.org 注册，立即可得）。
    API Key 可通过环境变量 OPENTOPOGRAPHY_API_KEY 配置，或直接传入 api_key 参数。

    dem_type 可选值：
      COP30     - Copernicus DEM 30m（推荐）
      SRTMGL1   - SRTM 30m（NASA，60°N~56°S）
      SRTMGL3   - SRTM 90m（NASA）
      NASADEM   - NASADEM 30m（SRTM 改进版）
      AW3D30    - ALOS World 3D 30m（JAXA）
      SRTM15Plus - SRTM15+ 500m（含海底地形）

    west/south/east/north: WGS84 经纬度范围（建议不超过 5°×5°，否则文件很大）
    """
    import httpx
    from osgeo import gdal
    gdal.UseExceptions()

    key = api_key or _OT_API_KEY
    if not key:
        return {
            "error": "OpenTopography API Key 未配置。"
                     "请在 https://opentopography.org 免费注册，"
                     "然后设置环境变量 OPENTOPOGRAPHY_API_KEY=your_key，"
                     "或将 api_key 参数传入本工具。"
        }

    if not output:
        fd, output = tempfile.mkstemp(suffix=f"_{dem_type.lower()}.tif", prefix="dem_")
        os.close(fd)

    area_deg2 = (east - west) * (north - south)
    if area_deg2 > 25:
        return {"error": f"请求区域过大（{area_deg2:.1f}°²），建议不超过 5°×5°（25°²）以避免超时"}

    url = "https://portal.opentopography.org/API/globaldem"
    params = {
        "demtype": dem_type,
        "south": south, "north": north,
        "west": west, "east": east,
        "outputFormat": "GTiff",
        "API_Key": key,
    }

    resp = httpx.get(url, params=params, timeout=120.0, follow_redirects=True)
    if resp.status_code != 200:
        return {"error": f"OpenTopography API error {resp.status_code}: {resp.text[:300]}"}

    with open(output, "wb") as f:
        f.write(resp.content)

    ds = gdal.Open(output)
    if ds is None:
        return {"error": "Downloaded file is not a valid GeoTIFF"}

    band = ds.GetRasterBand(1)
    bmin, bmax, bmean, bstd = band.GetStatistics(False, True)
    gt = ds.GetGeoTransform()
    return {
        "output": output,
        "source": f"OpenTopography / {dem_type}",
        "dem_type": dem_type,
        "bbox": {"west": west, "south": south, "east": east, "north": north},
        "size": {"width": ds.RasterXSize, "height": ds.RasterYSize},
        "pixel_size_deg": abs(gt[1]),
        "elevation": {
            "min_m": round(bmin, 1),
            "max_m": round(bmax, 1),
            "mean_m": round(bmean, 1),
            "std_m": round(bstd, 1),
        },
    }


@mcp.tool()
def dem_quick_stats(path: str) -> dict:
    """快速读取已下载的 DEM 文件，返回高程统计和基本信息。
    适合在 fetch_dem_* 之后调用，确认数据质量。
    """
    from osgeo import gdal, osr
    gdal.UseExceptions()
    ds = gdal.Open(path)
    if ds is None:
        return {"error": f"Cannot open: {path}"}

    band = ds.GetRasterBand(1)
    bmin, bmax, bmean, bstd = band.GetStatistics(False, True)
    gt = ds.GetGeoTransform()
    srs = osr.SpatialReference(wkt=ds.GetProjection())
    epsg = srs.GetAttrValue("AUTHORITY", 1) if srs.GetAttrValue("AUTHORITY", 0) == "EPSG" else None

    pixel_m = abs(gt[1]) * 111320  # 粗略换算（赤道附近）
    return {
        "path": path,
        "width": ds.RasterXSize,
        "height": ds.RasterYSize,
        "crs_epsg": epsg,
        "pixel_size_deg": round(abs(gt[1]), 7),
        "pixel_size_m_approx": round(pixel_m, 1),
        "extent": {
            "west": gt[0], "north": gt[3],
            "east": gt[0] + gt[1] * ds.RasterXSize,
            "south": gt[3] + gt[5] * ds.RasterYSize,
        },
        "elevation": {
            "min_m": round(bmin, 1),
            "max_m": round(bmax, 1),
            "mean_m": round(bmean, 1),
            "std_m": round(bstd, 1),
            "range_m": round(bmax - bmin, 1),
        },
        "nodata": band.GetNoDataValue(),
        "file_size_mb": round(os.path.getsize(path) / 1e6, 2),
    }


if __name__ == "__main__":
    mcp.run(transport="stdio")
