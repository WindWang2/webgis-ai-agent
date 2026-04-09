#!/usr/bin/env python3
"""GDAL/OGR 矢量分析 MCP Server

工具列表：
  vector_info          - 读取矢量文件元数据（图层数、要素数、字段、范围、CRS 等）
  vector_translate     - 格式转换 / 投影转换 / SQL 过滤
  vector_buffer        - 要素缓冲区分析（返回 GeoJSON）
  vector_dissolve      - 按字段合并要素（返回 GeoJSON）
  vector_clip          - 用裁剪图层裁切输入图层（返回 GeoJSON）
  vector_intersection  - 求两个图层的交集（返回 GeoJSON）
  vector_union         - 求两个图层的并集（返回 GeoJSON）
  vector_centroid      - 计算要素质心（返回 GeoJSON）
  vector_area_length   - 计算要素面积和长度（返回属性统计）
  vector_crs_transform - 坐标系转换（GeoJSON 输入输出）
  vector_to_geojson    - 任意矢量文件转 GeoJSON 字符串
  vector_from_geojson  - GeoJSON 写入矢量文件（Shapefile / GeoPackage 等）
"""
import json
import os
import tempfile

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("gdal-vector")


def _geojson_to_tmp(geojson: str | dict) -> str:
    """将 GeoJSON 写入临时文件，返回路径"""
    data = geojson if isinstance(geojson, str) else json.dumps(geojson, ensure_ascii=False)
    f = tempfile.NamedTemporaryFile(suffix=".geojson", delete=False, mode="w", encoding="utf-8")
    f.write(data)
    f.close()
    return f.name


def _layer_to_geojson(layer) -> dict:
    """OGR Layer → GeoJSON dict"""
    from osgeo import ogr
    features = []
    layer.ResetReading()
    for feat in layer:
        geom = feat.GetGeometryRef()
        if geom is None:
            continue
        props = {feat.GetFieldDefnRef(i).GetName(): feat.GetField(i)
                 for i in range(feat.GetFieldCount())}
        features.append({
            "type": "Feature",
            "properties": props,
            "geometry": json.loads(geom.ExportToJson()),
        })
    return {"type": "FeatureCollection", "features": features}


@mcp.tool()
def vector_info(path: str, layer_index: int = 0) -> dict:
    """读取矢量文件元数据：图层数、要素数、字段定义、空间范围、CRS"""
    from osgeo import gdal, ogr
    gdal.UseExceptions()
    ds = ogr.Open(path)
    if ds is None:
        return {"error": f"Cannot open: {path}"}

    layers = []
    for i in range(ds.GetLayerCount()):
        lyr = ds.GetLayer(i)
        extent = lyr.GetExtent()
        srs = lyr.GetSpatialRef()
        epsg = None
        if srs:
            srs.AutoIdentifyEPSG()
            epsg = srs.GetAttrValue("AUTHORITY", 1)
        fields = [{"name": lyr.GetLayerDefn().GetFieldDefn(j).GetName(),
                   "type": lyr.GetLayerDefn().GetFieldDefn(j).GetTypeName()}
                  for j in range(lyr.GetLayerDefn().GetFieldCount())]
        layers.append({
            "index": i,
            "name": lyr.GetName(),
            "feature_count": lyr.GetFeatureCount(),
            "geometry_type": ogr.GeometryTypeToName(lyr.GetGeomType()),
            "extent": {"west": extent[0], "east": extent[1], "south": extent[2], "north": extent[3]},
            "crs_epsg": epsg,
            "fields": fields,
        })
    return {"path": path, "driver": ds.GetDriver().GetName(), "layer_count": ds.GetLayerCount(), "layers": layers}


@mcp.tool()
def vector_to_geojson(path: str, layer_index: int = 0, sql: str = "") -> dict:
    """将任意矢量文件（Shapefile/GeoPackage/KML 等）转为 GeoJSON FeatureCollection。
    sql: 可选 OGR SQL 过滤语句，如 'SELECT * FROM layer WHERE area > 100'
    """
    from osgeo import ogr
    ds = ogr.Open(path)
    if ds is None:
        return {"error": f"Cannot open: {path}"}
    layer = ds.ExecuteSQL(sql) if sql else ds.GetLayer(layer_index)
    result = _layer_to_geojson(layer)
    result["source"] = path
    result["count"] = len(result["features"])
    return result


@mcp.tool()
def vector_from_geojson(geojson: str, dst: str, format: str = "GPKG") -> dict:
    """将 GeoJSON 写入矢量文件（GPKG/ESRI Shapefile/GML/KML 等）。
    format: GDAL 驱动名，如 GPKG / 'ESRI Shapefile' / GML / KML
    """
    from osgeo import ogr
    tmp = _geojson_to_tmp(geojson)
    try:
        src_ds = ogr.Open(tmp)
        driver = ogr.GetDriverByName(format)
        if driver is None:
            return {"error": f"Unknown driver: {format}"}
        if os.path.exists(dst):
            driver.DeleteDataSource(dst)
        driver.CopyDataSource(src_ds, dst)
    finally:
        os.unlink(tmp)
    return {"dst": dst, "format": format}


@mcp.tool()
def vector_translate(
    src: str, dst: str,
    format: str = "GPKG",
    target_epsg: int = 0,
    sql: str = "",
    where: str = "",
) -> dict:
    """矢量格式转换 / 投影转换 / 条件过滤。
    target_epsg: 目标 EPSG 代码，0 表示不转换。
    sql: OGR SQL 语句（与 where 互斥）。
    where: 属性过滤条件，如 'population > 10000'。
    """
    from osgeo import gdal
    gdal.UseExceptions()
    opts = gdal.VectorTranslateOptions(
        format=format,
        dstSRS=f"EPSG:{target_epsg}" if target_epsg else None,
        SQLStatement=sql or None,
        where=where or None,
    )
    gdal.VectorTranslate(dst, src, options=opts)
    return {"src": src, "dst": dst, "format": format}


@mcp.tool()
def vector_crs_transform(geojson: str, src_epsg: int, dst_epsg: int) -> dict:
    """将 GeoJSON 从源坐标系转换到目标坐标系，返回新 GeoJSON。"""
    from osgeo import ogr, osr
    src_srs = osr.SpatialReference()
    src_srs.ImportFromEPSG(src_epsg)
    src_srs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    dst_srs = osr.SpatialReference()
    dst_srs.ImportFromEPSG(dst_epsg)
    dst_srs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    transform = osr.CoordinateTransformation(src_srs, dst_srs)

    data = json.loads(geojson) if isinstance(geojson, str) else geojson
    new_features = []
    for feat in data.get("features", []):
        geom = ogr.CreateGeometryFromJson(json.dumps(feat["geometry"]))
        geom.Transform(transform)
        new_features.append({
            "type": "Feature",
            "properties": feat.get("properties", {}),
            "geometry": json.loads(geom.ExportToJson()),
        })
    return {"type": "FeatureCollection", "features": new_features,
            "src_epsg": src_epsg, "dst_epsg": dst_epsg}


@mcp.tool()
def vector_buffer(geojson: str, distance: float, src_epsg: int = 4326) -> dict:
    """对 GeoJSON 要素做缓冲区分析，返回缓冲区 GeoJSON。
    distance: 缓冲距离（单位与 src_epsg 一致；WGS84 时请先转为投影坐标系）
    src_epsg: 输入数据的 EPSG 代码（建议使用米制投影坐标系，如 32649 = UTM49N）
    """
    from osgeo import ogr, osr
    data = json.loads(geojson) if isinstance(geojson, str) else geojson

    # 若输入为地理坐标（度），自动转换到合适 UTM
    auto_proj = False
    if src_epsg == 4326:
        feats = data.get("features", [])
        if feats:
            first_geom = ogr.CreateGeometryFromJson(json.dumps(feats[0]["geometry"]))
            env = first_geom.GetEnvelope()
            lon = (env[0] + env[1]) / 2
            lat = (env[2] + env[3]) / 2
            zone = int((lon + 180) / 6) + 1
            utm_epsg = 32600 + zone if lat >= 0 else 32700 + zone
            src_srs = osr.SpatialReference(); src_srs.ImportFromEPSG(4326)
            src_srs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
            utm_srs = osr.SpatialReference(); utm_srs.ImportFromEPSG(utm_epsg)
            fwd = osr.CoordinateTransformation(src_srs, utm_srs)
            inv = osr.CoordinateTransformation(utm_srs, src_srs)
            auto_proj = True

    new_features = []
    for feat in data.get("features", []):
        geom = ogr.CreateGeometryFromJson(json.dumps(feat["geometry"]))
        if auto_proj:
            geom.Transform(fwd)
        buf = geom.Buffer(distance)
        if auto_proj:
            buf.Transform(inv)
        new_features.append({
            "type": "Feature",
            "properties": feat.get("properties", {}),
            "geometry": json.loads(buf.ExportToJson()),
        })
    return {"type": "FeatureCollection", "features": new_features,
            "distance": distance, "auto_projected_utm": auto_proj}


@mcp.tool()
def vector_centroid(geojson: str) -> dict:
    """计算每个要素的质心，返回点要素 GeoJSON。"""
    from osgeo import ogr
    data = json.loads(geojson) if isinstance(geojson, str) else geojson
    new_features = []
    for feat in data.get("features", []):
        geom = ogr.CreateGeometryFromJson(json.dumps(feat["geometry"]))
        centroid = geom.Centroid()
        new_features.append({
            "type": "Feature",
            "properties": feat.get("properties", {}),
            "geometry": json.loads(centroid.ExportToJson()),
        })
    return {"type": "FeatureCollection", "features": new_features}


@mcp.tool()
def vector_area_length(geojson: str, src_epsg: int = 4326) -> dict:
    """计算每个要素的面积（km²）和周长/长度（km），返回属性统计。
    src_epsg: 输入数据 EPSG，WGS84(4326) 时自动转 UTM 计算。
    """
    from osgeo import ogr, osr
    data = json.loads(geojson) if isinstance(geojson, str) else geojson
    features = data.get("features", [])
    if not features:
        return {"error": "No features"}

    transform = None
    if src_epsg == 4326:
        first_geom = ogr.CreateGeometryFromJson(json.dumps(features[0]["geometry"]))
        env = first_geom.GetEnvelope()
        lon = (env[0] + env[1]) / 2
        lat = (env[2] + env[3]) / 2
        zone = int((lon + 180) / 6) + 1
        utm_epsg = 32600 + zone if lat >= 0 else 32700 + zone
        src_srs = osr.SpatialReference(); src_srs.ImportFromEPSG(4326)
        src_srs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
        utm_srs = osr.SpatialReference(); utm_srs.ImportFromEPSG(utm_epsg)
        transform = osr.CoordinateTransformation(src_srs, utm_srs)

    results = []
    total_area = total_length = 0.0
    for feat in features:
        geom = ogr.CreateGeometryFromJson(json.dumps(feat["geometry"]))
        if transform:
            geom = geom.Clone()
            geom.Transform(transform)
        area_km2 = round(geom.GetArea() / 1e6, 4)
        length_km = round(geom.Length() / 1e3, 4)
        total_area += area_km2
        total_length += length_km
        results.append({
            "name": feat.get("properties", {}).get("name", ""),
            "area_km2": area_km2,
            "length_km": length_km,
        })

    return {
        "features": results,
        "total_area_km2": round(total_area, 4),
        "total_length_km": round(total_length, 4),
        "feature_count": len(results),
    }


@mcp.tool()
def vector_clip(input_geojson: str, clip_geojson: str) -> dict:
    """用裁剪图层（面要素）裁切输入图层，返回裁切后的 GeoJSON。"""
    from osgeo import ogr
    inp_tmp = _geojson_to_tmp(input_geojson)
    clp_tmp = _geojson_to_tmp(clip_geojson)
    try:
        inp_ds = ogr.Open(inp_tmp)
        clp_ds = ogr.Open(clp_tmp)
        inp_layer = inp_ds.GetLayer(0)
        clp_layer = clp_ds.GetLayer(0)

        mem = ogr.GetDriverByName("Memory").CreateDataSource("out")
        out_layer = mem.CreateLayer("clip", geom_type=inp_layer.GetGeomType())
        inp_layer.GetLayerDefn()
        for i in range(inp_layer.GetLayerDefn().GetFieldCount()):
            out_layer.CreateField(inp_layer.GetLayerDefn().GetFieldDefn(i))

        inp_layer.Clip(clp_layer, out_layer)
        result = _layer_to_geojson(out_layer)
        result["count"] = len(result["features"])
    finally:
        os.unlink(inp_tmp)
        os.unlink(clp_tmp)
    return result


@mcp.tool()
def vector_intersection(geojson_a: str, geojson_b: str) -> dict:
    """求两个矢量图层的交集要素，返回 GeoJSON。"""
    from osgeo import ogr
    tmp_a = _geojson_to_tmp(geojson_a)
    tmp_b = _geojson_to_tmp(geojson_b)
    try:
        ds_a = ogr.Open(tmp_a); ds_b = ogr.Open(tmp_b)
        lyr_a = ds_a.GetLayer(0); lyr_b = ds_b.GetLayer(0)
        mem = ogr.GetDriverByName("Memory").CreateDataSource("out")
        out = mem.CreateLayer("intersection")
        lyr_a.Intersection(lyr_b, out)
        result = _layer_to_geojson(out)
        result["count"] = len(result["features"])
    finally:
        os.unlink(tmp_a); os.unlink(tmp_b)
    return result


@mcp.tool()
def vector_union(geojson_a: str, geojson_b: str) -> dict:
    """求两个矢量图层的并集，返回 GeoJSON。"""
    from osgeo import ogr
    tmp_a = _geojson_to_tmp(geojson_a)
    tmp_b = _geojson_to_tmp(geojson_b)
    try:
        ds_a = ogr.Open(tmp_a); ds_b = ogr.Open(tmp_b)
        lyr_a = ds_a.GetLayer(0); lyr_b = ds_b.GetLayer(0)
        mem = ogr.GetDriverByName("Memory").CreateDataSource("out")
        out = mem.CreateLayer("union")
        lyr_a.Union(lyr_b, out)
        result = _layer_to_geojson(out)
        result["count"] = len(result["features"])
    finally:
        os.unlink(tmp_a); os.unlink(tmp_b)
    return result


@mcp.tool()
def vector_dissolve(geojson: str, field: str = "") -> dict:
    """按指定字段合并相邻同值要素（dissolve），field 为空则合并全部。返回 GeoJSON。"""
    from osgeo import ogr
    tmp = _geojson_to_tmp(geojson)
    try:
        ds = ogr.Open(tmp)
        lyr = ds.GetLayer(0)

        if not field:
            # 合并所有要素为一个
            union_geom = None
            for feat in lyr:
                geom = feat.GetGeometryRef()
                union_geom = geom.Union(union_geom) if union_geom else geom.Clone()
            return {
                "type": "FeatureCollection",
                "features": [{
                    "type": "Feature", "properties": {},
                    "geometry": json.loads(union_geom.ExportToJson()),
                }],
                "count": 1,
            }

        # 按字段分组合并
        groups: dict[str, list] = {}
        lyr.ResetReading()
        for feat in lyr:
            key = str(feat.GetField(field)) if feat.GetField(field) is not None else "__null__"
            geom = feat.GetGeometryRef()
            if key not in groups:
                groups[key] = {"geom": geom.Clone(), "props": {field: feat.GetField(field)}}
            else:
                groups[key]["geom"] = groups[key]["geom"].Union(geom)

        features = [{"type": "Feature", "properties": v["props"],
                     "geometry": json.loads(v["geom"].ExportToJson())}
                    for v in groups.values()]
    finally:
        os.unlink(tmp)

    return {"type": "FeatureCollection", "features": features, "count": len(features)}


if __name__ == "__main__":
    mcp.run(transport="stdio")
