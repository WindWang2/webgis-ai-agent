#!/usr/bin/env python3
"""GDAL 栅格分析 MCP Server

工具列表：
  raster_info         - 读取栅格文件元数据（格式、波段、分辨率、CRS、统计等）
  raster_stats        - 计算波段统计（min/max/mean/std）
  raster_translate    - 格式转换 / 裁切 / 重采样
  raster_warp         - 重投影 / 对齐栅格
  raster_calc         - 栅格波段计算（支持 NumPy 表达式）
  raster_dem_analysis - DEM 地形分析（坡度/坡向/山体阴影/TRI/TPI/粗糙度）
  raster_ndvi         - 计算 NDVI（需指定红光/近红外波段编号）
  raster_clip_by_geojson - 用 GeoJSON 矢量裁剪栅格
  raster_contour      - 从 DEM 提取等高线（返回 GeoJSON）
  raster_polygonize   - 栅格转矢量面（返回 GeoJSON）
"""
import json
import os
import tempfile
from typing import Any

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("gdal-raster")


def _open(path: str):
    from osgeo import gdal
    gdal.UseExceptions()
    ds = gdal.Open(path)
    if ds is None:
        raise FileNotFoundError(f"Cannot open: {path}")
    return ds


@mcp.tool()
def raster_info(path: str) -> dict:
    """读取栅格文件的元数据：格式、波段数、分辨率、空间参考、范围等"""
    from osgeo import gdal, osr
    gdal.UseExceptions()
    ds = _open(path)
    gt = ds.GetGeoTransform()
    srs = osr.SpatialReference(wkt=ds.GetProjection())
    bands = []
    for i in range(1, ds.RasterCount + 1):
        b = ds.GetRasterBand(i)
        bmin, bmax, bmean, bstd = b.GetStatistics(True, True)
        bands.append({
            "band": i,
            "dtype": gdal.GetDataTypeName(b.DataType),
            "nodata": b.GetNoDataValue(),
            "min": round(bmin, 4), "max": round(bmax, 4),
            "mean": round(bmean, 4), "std": round(bstd, 4),
            "color_interp": gdal.GetColorInterpretationName(b.GetColorInterpretation()),
        })
    return {
        "path": path,
        "driver": ds.GetDriver().ShortName,
        "width": ds.RasterXSize,
        "height": ds.RasterYSize,
        "band_count": ds.RasterCount,
        "pixel_size_x": abs(gt[1]),
        "pixel_size_y": abs(gt[5]),
        "origin_x": gt[0],
        "origin_y": gt[3],
        "extent": {
            "west": gt[0], "north": gt[3],
            "east": gt[0] + gt[1] * ds.RasterXSize,
            "south": gt[3] + gt[5] * ds.RasterYSize,
        },
        "crs_wkt": ds.GetProjection(),
        "crs_epsg": srs.GetAttrValue("AUTHORITY", 1) if srs.GetAttrValue("AUTHORITY", 0) == "EPSG" else None,
        "bands": bands,
    }


@mcp.tool()
def raster_stats(path: str, band: int = 1) -> dict:
    """计算指定波段的统计值（min/max/mean/std/histogram）"""
    from osgeo import gdal
    gdal.UseExceptions()
    ds = _open(path)
    b = ds.GetRasterBand(band)
    bmin, bmax, bmean, bstd = b.GetStatistics(False, True)
    hist = b.GetHistogram(min=bmin, max=bmax, buckets=20, approx_ok=True)
    return {
        "band": band,
        "min": round(bmin, 6), "max": round(bmax, 6),
        "mean": round(bmean, 6), "std": round(bstd, 6),
        "histogram_20bins": hist,
    }


@mcp.tool()
def raster_translate(
    src: str, dst: str,
    format: str = "GTiff",
    bands: str = "",
    resample: str = "nearest",
    scale: float = 1.0,
    output_type: str = "",
) -> dict:
    """栅格格式转换/波段提取/值缩放。
    bands: 逗号分隔的波段编号，如 '1,2,3'；空表示全部。
    resample: nearest/bilinear/cubic/lanczos
    output_type: Byte/UInt16/Int16/Float32/Float64，空表示保持原类型。
    """
    from osgeo import gdal
    gdal.UseExceptions()
    opts = gdal.TranslateOptions(
        format=format,
        bandList=[int(x) for x in bands.split(",") if x.strip()] or None,
        resampleAlg=resample,
        scaleParams=[[0, 1, 0, scale]] if scale != 1.0 else None,
        outputType=getattr(gdal, f"GDT_{output_type}") if output_type else gdal.GDT_Unknown,
    )
    gdal.Translate(dst, src, options=opts)
    return {"src": src, "dst": dst, "format": format}


@mcp.tool()
def raster_warp(
    src: str, dst: str,
    target_epsg: int,
    resample: str = "bilinear",
    x_res: float = 0.0,
    y_res: float = 0.0,
) -> dict:
    """重投影栅格到指定 EPSG 坐标系。
    x_res/y_res: 目标分辨率（目标 CRS 单位），0 表示自动。
    resample: nearest/bilinear/cubic/lanczos
    """
    from osgeo import gdal
    gdal.UseExceptions()
    opts = gdal.WarpOptions(
        dstSRS=f"EPSG:{target_epsg}",
        resampleAlg=resample,
        xRes=x_res or None,
        yRes=y_res or None,
    )
    gdal.Warp(dst, src, options=opts)
    return {"src": src, "dst": dst, "target_epsg": target_epsg}


@mcp.tool()
def raster_calc(
    expression: str,
    output: str,
    inputs: str,
    no_data: float = -9999.0,
    output_type: str = "Float32",
) -> dict:
    """栅格波段计算（NumPy 表达式）。
    inputs: JSON 字符串，变量名到文件路径的映射，如 '{"A": "/path/a.tif", "B": "/path/b.tif"}'。
    expression: NumPy 表达式，如 '(A - B) / (A + B)'（NDVI 等）。
    输出为 Float32 GeoTIFF。
    """
    import numpy as np
    from osgeo import gdal
    gdal.UseExceptions()

    file_map: dict[str, str] = json.loads(inputs)
    datasets: dict[str, Any] = {}
    arrays: dict[str, Any] = {}
    ref_ds = None

    for var, fpath in file_map.items():
        ds = gdal.Open(fpath)
        if ds is None:
            return {"error": f"Cannot open: {fpath}"}
        datasets[var] = ds
        if ref_ds is None:
            ref_ds = ds
        arrays[var] = ds.GetRasterBand(1).ReadAsArray().astype(np.float32)

    result: np.ndarray = eval(expression, {"__builtins__": {}}, {**arrays, "np": np})  # noqa: S307
    result = np.where(np.isnan(result), no_data, result).astype(np.float32)

    dtype_map = {"Float32": gdal.GDT_Float32, "Float64": gdal.GDT_Float64,
                 "Byte": gdal.GDT_Byte, "Int16": gdal.GDT_Int16}
    gdal_type = dtype_map.get(output_type, gdal.GDT_Float32)

    driver = gdal.GetDriverByName("GTiff")
    out_ds = driver.Create(output, ref_ds.RasterXSize, ref_ds.RasterYSize, 1, gdal_type)
    out_ds.SetGeoTransform(ref_ds.GetGeoTransform())
    out_ds.SetProjection(ref_ds.GetProjection())
    band = out_ds.GetRasterBand(1)
    band.SetNoDataValue(no_data)
    band.WriteArray(result)
    band.FlushCache()
    out_ds = None

    stats = {"min": float(result[result != no_data].min()) if (result != no_data).any() else None,
             "max": float(result[result != no_data].max()) if (result != no_data).any() else None,
             "mean": float(result[result != no_data].mean()) if (result != no_data).any() else None}
    return {"output": output, "expression": expression, "stats": stats}


@mcp.tool()
def raster_dem_analysis(
    src: str, dst: str,
    mode: str = "slope",
    scale: float = 1.0,
    az: float = 315.0,
    alt: float = 45.0,
) -> dict:
    """DEM 地形分析。
    mode: slope（坡度°）/ aspect（坡向°）/ hillshade（山体阴影）/
          TRI（地形粗糙指数）/ TPI（地形位置指数）/ roughness（粗糙度）
    scale: 水平/垂直单位比（地理坐标系通常需填 111120）
    az: 光源方位角（hillshade）
    alt: 光源高度角（hillshade）
    """
    from osgeo import gdal
    gdal.UseExceptions()
    mode_map = {
        "slope": "slope", "aspect": "aspect", "hillshade": "hillshade",
        "TRI": "TRI", "TPI": "TPI", "roughness": "roughness",
    }
    if mode not in mode_map:
        return {"error": f"Unknown mode '{mode}'. Choose from: {list(mode_map.keys())}"}
    opts = gdal.DEMProcessingOptions(
        slopeFormat="degree" if mode == "slope" else None,
        scale=scale,
        azimuth=az if mode == "hillshade" else None,
        altitude=alt if mode == "hillshade" else None,
    )
    gdal.DEMProcessing(dst, src, mode_map[mode], options=opts)
    return {"src": src, "dst": dst, "mode": mode}


@mcp.tool()
def raster_ndvi(
    src: str, dst: str,
    red_band: int = 3,
    nir_band: int = 4,
) -> dict:
    """计算 NDVI = (NIR - Red) / (NIR + Red)，输出 Float32 GeoTIFF [-1, 1]。
    red_band: 红光波段编号（Landsat-8 通常为 4，Sentinel-2 通常为 3）
    nir_band: 近红外波段编号（Landsat-8 通常为 5，Sentinel-2 通常为 4）
    """
    import numpy as np
    from osgeo import gdal
    gdal.UseExceptions()
    ds = _open(src)
    red = ds.GetRasterBand(red_band).ReadAsArray().astype(np.float32)
    nir = ds.GetRasterBand(nir_band).ReadAsArray().astype(np.float32)

    denom = nir + red
    ndvi = np.where(denom == 0, -9999.0, (nir - red) / denom)

    driver = gdal.GetDriverByName("GTiff")
    out = driver.Create(dst, ds.RasterXSize, ds.RasterYSize, 1, gdal.GDT_Float32)
    out.SetGeoTransform(ds.GetGeoTransform())
    out.SetProjection(ds.GetProjection())
    b = out.GetRasterBand(1)
    b.SetNoDataValue(-9999.0)
    b.WriteArray(ndvi.astype(np.float32))
    b.FlushCache()
    out = None

    valid = ndvi[ndvi != -9999.0]
    return {
        "dst": dst,
        "red_band": red_band, "nir_band": nir_band,
        "ndvi_min": round(float(valid.min()), 4),
        "ndvi_max": round(float(valid.max()), 4),
        "ndvi_mean": round(float(valid.mean()), 4),
        "vegetation_coverage_pct": round(float((valid > 0.3).mean()) * 100, 2),
    }


@mcp.tool()
def raster_clip_by_geojson(
    src: str, dst: str,
    geojson: str,
    all_touched: bool = False,
    crop_to_cutline: bool = True,
) -> dict:
    """用 GeoJSON 面要素裁剪栅格（cutline）。
    geojson: GeoJSON 字符串（FeatureCollection 或 Feature）。
    """
    from osgeo import gdal
    gdal.UseExceptions()

    with tempfile.NamedTemporaryFile(suffix=".geojson", delete=False, mode="w") as f:
        f.write(geojson if isinstance(geojson, str) else json.dumps(geojson))
        cutline_path = f.name

    try:
        opts = gdal.WarpOptions(
            cutlineDSName=cutline_path,
            cropToCutline=crop_to_cutline,
            allTouched=all_touched,
            dstNodata=-9999,
        )
        gdal.Warp(dst, src, options=opts)
    finally:
        os.unlink(cutline_path)

    return {"src": src, "dst": dst}


@mcp.tool()
def raster_contour(
    src: str,
    interval: float,
    band: int = 1,
    base: float = 0.0,
    attribute: str = "elev",
) -> dict:
    """从 DEM 提取等高线，返回 GeoJSON FeatureCollection。
    interval: 等高距（与 DEM 单位相同，通常为米）
    base: 基准高程
    attribute: 等高线高程属性字段名
    """
    import numpy as np
    from osgeo import gdal, ogr, osr
    gdal.UseExceptions()

    ds = _open(src)
    src_srs = osr.SpatialReference(wkt=ds.GetProjection())

    mem_driver = ogr.GetDriverByName("Memory")
    mem_ds = mem_driver.CreateDataSource("out")
    dst_srs = osr.SpatialReference()
    dst_srs.ImportFromEPSG(4326)
    layer = mem_ds.CreateLayer("contour", srs=dst_srs if src_srs.IsGeographic() else src_srs)
    layer.CreateField(ogr.FieldDefn("id", ogr.OFTInteger))
    layer.CreateField(ogr.FieldDefn(attribute, ogr.OFTReal))

    band = ds.GetRasterBand(band)
    gdal.ContourGenerate(band, interval, base, [], 0, 0.0, layer, 0, 1)

    features = []
    for feat in layer:
        geom = feat.GetGeometryRef()
        if geom is None:
            continue
        features.append({
            "type": "Feature",
            "properties": {attribute: feat.GetField(attribute)},
            "geometry": json.loads(geom.ExportToJson()),
        })

    return {
        "type": "FeatureCollection",
        "features": features,
        "count": len(features),
        "interval": interval,
    }


@mcp.tool()
def raster_polygonize(
    src: str,
    band: int = 1,
    mask_nodata: bool = True,
) -> dict:
    """将栅格分类图转为矢量面，返回 GeoJSON FeatureCollection。
    适用于分类栅格（土地利用、提取结果等）。
    mask_nodata: 是否跳过 nodata 区域
    """
    from osgeo import gdal, ogr
    gdal.UseExceptions()

    ds = _open(src)
    src_band = ds.GetRasterBand(band)
    mask_band = src_band.GetMaskBand() if mask_nodata else None

    mem_driver = ogr.GetDriverByName("Memory")
    mem_ds = mem_driver.CreateDataSource("out")
    layer = mem_ds.CreateLayer("polygons")
    layer.CreateField(ogr.FieldDefn("value", ogr.OFTInteger))

    gdal.Polygonize(src_band, mask_band, layer, 0, [], callback=None)

    features = []
    for feat in layer:
        geom = feat.GetGeometryRef()
        if geom is None:
            continue
        features.append({
            "type": "Feature",
            "properties": {"value": feat.GetField("value")},
            "geometry": json.loads(geom.ExportToJson()),
        })

    return {
        "type": "FeatureCollection",
        "features": features,
        "count": len(features),
    }


if __name__ == "__main__":
    mcp.run(transport="stdio")
