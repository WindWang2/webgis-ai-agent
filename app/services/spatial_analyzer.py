"""
T003 矢量数据智能识别模块 + T004 基础空间分析算子
实现矢量数据自动识别、格式校验、几何修复 + 5个核心GIS算子
结果自动生成矢量图层入库
"""
import logging
import math
import os
from typing import Dict, List, Any, Optional, Callable, Tuple
from dataclasses import dataclass
import geopandas as gpd
from shapely.geometry import shape, mapping
from shapely import Geometry
from shapely.validation import explain_validity, make_valid

logger = logging.getLogger(__name__)
@dataclass
class AnalysisResult:
    """分析结果封装"""
    success: bool
    data: Optional[Dict] = None
    error_message: Optional[str] = None
    stats: Optional[Dict] = None
class SpatialAnalyzer:
    """
    空间分析算子类 - 包含矢量数据智能识别 + 核心GIS分析能力
    """
    # 单位和转换系数
    UNIT_METERS = {
        "m": 1.0,
        "meter": 1.0,
        "km": 1000.0,
        "kilometer": 1000.0,
        "ft": 0.3048,
        "feet": 0.3048,
        "mi": 1609.344,
        "mile": 1609.344,
    }

    # 支持的矢量格式
    SUPPORTED_VECTOR_FORMATS = {
        "geojson": "GeoJSON",
        "shapefile": "ESRI Shapefile",
        "kml": "KML",
        "gpx": "GPX",
        "csv": "CSV (with geometry column)",
        "parquet": "GeoParquet"
    }

    @classmethod
    def recognize_vector_data(
        cls,
        features: List[Dict],
        auto_repair: bool = True,
        callback: Optional[Callable] = None
    ) -> AnalysisResult:
        """
        矢量数据智能识别:
        1. 识别几何类型(点/线/面/集合)
        2. 校验几何有效性
        3. 自动修复无效几何(可选)
        4. 统计属性字段类型
        5. 生成数据质量报告

        参数:
            features: 输入GeoJSON要素列表
            auto_repair: 是否自动修复无效几何
            callback: 进度回调函数

        返回:
            识别结果 + 修复后的要素列表
        """
        try:
            if callback:
                callback(10, "开始识别矢量数据...")

            if not features:
                return AnalysisResult(
                    success=False,
                    error_message="输入要素为空"
                )

            # 转换为GeoDataFrame进行批量处理
            gdf = gpd.GeoDataFrame.from_features(features)

            if callback:
                callback(30, "校验几何有效性...")

            # 识别几何类型
            geom_types = gdf.geometry.type.unique().tolist()
            crs = gdf.crs.to_string() if gdf.crs else "Unknown"

            # 检查有效性
            valid_mask = gdf.geometry.is_valid
            valid_count = valid_mask.sum()
            invalid_count = len(gdf) - valid_count

            # 自动修复无效几何
            repaired_count = 0
            if auto_repair and invalid_count > 0:
                if callback:
                    callback(50, f"修复{invalid_count}个无效几何...")

                # 修复无效几何
                def repair_geom(geom: Optional[Geometry]) -> Optional[Geometry]:
                    nonlocal repaired_count
                    if geom is None or geom.is_empty:
                        return None
                    if not geom.is_valid:
                        repaired = make_valid(geom)
                        if repaired.is_valid:
                            repaired_count += 1
                        return repaired
                    return geom

                gdf.geometry = gdf.geometry.apply(repair_geom)

                # 重新校验
                valid_mask = gdf.geometry.is_valid
                valid_count = valid_mask.sum()
                invalid_count = len(gdf) - valid_count

            if callback:
                callback(70, "分析属性字段...")

            # 分析属性字段类型
            field_info = {}
            for col in gdf.columns:
                if col == "geometry":
                    continue
                dtype = str(gdf[col].dtype)
                non_null_count = gdf[col].notna().sum()
                unique_count = gdf[col].nunique()
                field_info[col] = {
                    "type": dtype,
                    "non_null_count": int(non_null_count),
                    "unique_count": int(unique_count),
                    "null_rate": float((len(gdf) - non_null_count) / len(gdf))
                }

            if callback:
                callback(90, "生成识别报告...")

            # 转换回GeoJSON
            repaired_features = gdf.__geo_interface__["features"]

            return AnalysisResult(
                success=True,
                data={
                    "features": repaired_features,
                    "quality_report": {
                        "geometry_types": geom_types,
                        "crs": crs,
                        "total_features": len(features),
                        "valid_features": int(valid_count),
                        "invalid_features": int(invalid_count),
                        "repaired_features": int(repaired_count),
                        "fields": field_info
                    }
                },
                stats={
                    "total": len(features),
                    "valid": int(valid_count),
                    "repaired": int(repaired_count),
                    "field_count": len(field_info)
                }
            )

        except Exception as e:
            logger.error(f"矢量数据识别失败: {e}")
            return AnalysisResult(
                success=False,
                error_message=f"矢量数据识别失败: {str(e)}"
            )
    @classmethod
    def buffer(
        cls, 
        features: List[Dict],
        distance: float,
        unit: str = "m",
        dissolve: bool = False,
        callback: Optional[Callable] = None,
        source_crs: Optional[str] = None
    ) -> AnalysisResult:
        """
        缓冲区分析 - 生成给定几何对象的缓冲区域
        
        ⚠️ 正确处理CRS投影转换：
        - 如果输入是地理坐标系（WGS84/EPSG:4326，单位度），需要先投影到投影坐标系
        - 投影后在平面坐标系（单位米）上计算缓冲区
        - 计算完成后再投影回原坐标系
        
        参数:
            - features: 输入要素列表 GeoJSON Feature[]
            - distance: 缓冲距离
            - unit: 距离单位(m/km/ft/mi)
            - dissolve: 是否融合结果
            - source_crs: 输入数据的坐标系（EPSG code，如 "EPSG:4326")
        
        返回:
            {type: FeatureCollection, features: [...], stats: {...}}
        """
        try:
            if callback: callback(10, "准备数据...")
            
            # 单位转换为米
            factor = cls.UNIT_METERS.get(unit.lower(), 1.0)
            distance_m = abs(distance) * factor
            
            if callback: callback(20, "读取地理数据...")
            
            # 转换为GeoDataFrame
            gdf = gpd.GeoDataFrame.from_features(features)
            
            # 获取/推断CRS
            if source_crs:
                gdf.set_crs(source_crs, inplace=True)
            elif gdf.crs is None:
                # 如果没有CRS信息，默认是WGS84
                gdf.set_crs("EPSG:4326", inplace=True)
            
            crs = gdf.crs
            needs_reproject = False
            original_crs = crs
            
            # 判断是否需要投影转换
            # 如果是地理坐标系（单位是度），需要转换到投影坐标系计算buffer
            if crs.is_geographic:
                if callback: callback(40, "投影转换: 地理坐标系 → 投影坐标系...")
                
                # 获取数据范围，选择合适的UTM zone
                bounds = gdf.total_bounds  # (minx, miny, maxx, maxy)
                center_lon = (bounds[0] + bounds[2]) / 2
                center_lat = (bounds[1] + bounds[3]) / 2
                
                # 计算UTM zone
                utm_zone = int((center_lon + 180) / 6) + 1
                # 北半球南半球判断
                utm_crs_code = 32600 + utm_zone if center_lat >= 0 else 32700 + utm_zone
                utm_crs = f"EPSG:{utm_crs_code}"
                
                # 投影到UTM（米单位坐标系）
                gdf_utm = gdf.to_crs(utm_crs)
                needs_reproject = True
            else:
                # 已经是投影坐标系，直接计算
                gdf_utm = gdf
            
            if callback: callback(60, "计算缓冲区...")
            
            # 在投影坐标系（米单位）上计算缓冲区
            gdf_utm.geometry = gdf_utm.geometry.buffer(distance_m)
            
            # 添加缓冲距离属性
            gdf_utm["buffer_distance"] = distance_m
            
            # 融合如果需要
            if dissolve:
                if callback: callback(70, "融合缓冲区...")
                gdf_utm = gdf_utm.dissolve()
            
            # 如果进行了投影转换，转换回原坐标系
            if needs_reproject:
                if callback: callback(80, "投影转换: 投影坐标系 → 原坐标系...")
                gdf_result = gdf_utm.to_crs(original_crs)
            else:
                gdf_result = gdf_utm
            
            if callback: callback(90, "整理结果...")
            
            # 转换回GeoJSON
            result_features = gdf_result.__geo_interface__["features"]
            
            return AnalysisResult(success=True, data={
                "type": "FeatureCollection",
                "features": result_features
            }, stats={
                "input_count": len(features),
                "output_count": len(result_features),
                "buffer_distance_m": distance_m,
                "dissolve": dissolve,
                "reprojected": needs_reproject,
                "original_crs": str(original_crs) if original_crs else None,
                "working_crs": utm_crs if needs_reproject else str(crs)
            })
        except Exception as e:
            logger.error(f"Buffer分析失败: {e}")
            return AnalysisResult(success=False, error_message=str(e))
    @classmethod
    def clip(
        cls,
        features: List[Dict],
        boundary: Dict,
        callback: Optional[Callable] = None
    ) -> AnalysisResult:
        """裁剪分析 - 用边界多边形裁剪输入要素"""
        try:
            if callback: callback(20, "构建裁剪掩膜...")

            # 转换为GeoDataFrame
            gdf = gpd.GeoDataFrame.from_features(features)
            
            # 构建裁剪边界
            boundary_geom = shape(boundary.get('geometry', boundary))
            
            if callback: callback(40, "执行裁剪操作...")
            
            # 执行裁剪
            clipped_gdf = gdf.clip(boundary_geom)
            
            if callback: callback(80, "生成结果...")

            # 转换回GeoJSON
            clipped_features = clipped_gdf.__geo_interface__['features']

            return AnalysisResult(success=True, data={
                "type": "FeatureCollection",
                "features": clipped_features
            }, stats={
                "input_count": len(features),
                "clipped_count": len(clipped_features),
                "boundary_area": boundary_geom.area,
            })
        except Exception as e:
            logger.error(f"Clip分析失败: {e}")
            return AnalysisResult(success=False, error_message=str(e))
    @classmethod
    def overlay(
        cls,
        features_a: List[Dict],
        features_b: List[Dict],
        how: str = "intersection",
        callback: Optional[Callable] = None
    ) -> AnalysisResult:
        """叠加分析 - 执行相交、联合、差异等空间叠加强制操作
        
        参数:
            how: 'intersection', 'union', 'identity', 'symmetric_difference', 'difference'
        """
        try:
            if callback: callback(20, f"开始进行 {how} 叠加操作...")
            
            gdf_a = gpd.GeoDataFrame.from_features(features_a)
            gdf_b = gpd.GeoDataFrame.from_features(features_b)
            
            if gdf_a.crs is None: gdf_a.set_crs("EPSG:4326", inplace=True)
            if gdf_b.crs is None: gdf_b.set_crs("EPSG:4326", inplace=True)
            
            # 对齐坐标系
            if gdf_a.crs != gdf_b.crs:
                gdf_b = gdf_b.to_crs(gdf_a.crs)
            
            if callback: callback(50, "执行空间叠加...")
            
            # 执行叠加
            result_gdf = gpd.overlay(gdf_a, gdf_b, how=how)
            
            if callback: callback(80, "整理分析结果...")
            
            features = result_gdf.__geo_interface__["features"]
            
            return AnalysisResult(success=True, data={
                "type": "FeatureCollection",
                "features": features
            }, stats={
                "layer_a_count": len(features_a),
                "layer_b_count": len(features_b),
                "result_count": len(features),
                "how": how
            })
        except Exception as e:
            logger.error(f"Overlay分析失败({how}): {e}")
            return AnalysisResult(success=False, error_message=str(e))

    @classmethod
    def attribute_filter(
        cls,
        features: List[Dict],
        query: str,
        callback: Optional[Callable] = None
    ) -> AnalysisResult:
        """属性过滤 - 基于查询表达式筛选要素"""
        try:
            if callback: callback(30, f"正在执行属性过滤: {query}")
            
            gdf = gpd.GeoDataFrame.from_features(features)
            
            # 使用 pandas query
            filtered_gdf = gdf.query(query)
            
            if callback: callback(80, "生成结果集...")
            
            result_features = filtered_gdf.__geo_interface__["features"]
            
            return AnalysisResult(success=True, data={
                "type": "FeatureCollection",
                "features": result_features
            }, stats={
                "input_count": len(features),
                "filtered_count": len(result_features),
                "query": query
            })
        except Exception as e:
            logger.error(f"属性过滤失败: {e}")
            return AnalysisResult(success=False, error_message=f"查询语法错误或字段不存在: {str(e)}")

    @classmethod
    def intersect(
        cls,
        features_a: List[Dict],
        features_b: List[Dict],
        callback: Optional[Callable] = None
    ) -> AnalysisResult:
        """相交分析 - 计算两个圖層交集 (遗留接口，建议使用 overlay)"""
        return cls.overlay(features_a, features_b, how="intersection", callback=callback)

    @classmethod
    def dissolve(
        cls,
        features: List[Dict],
        dissolve_field: Optional[str] = None,
        callback: Optional[Callable] = None
    ) -> AnalysisResult:
        """融合分析 - 按字段聚合几何"""
        try:
            if callback: callback(20, "准备数据...")
            
            # 转换为GeoDataFrame
            gdf = gpd.GeoDataFrame.from_features(features)
            
            if callback: callback(40, "执行融合...")

            # 执行融合操作
            if dissolve_field and dissolve_field in gdf.columns:
                dissolved_gdf = gdf.dissolve(by=dissolve_field)
            else:
                dissolved_gdf = gdf.dissolve()

            if callback: callback(80, "整理结果...")

            # 转换回GeoJSON
            dissolved_features = dissolved_gdf.__geo_interface__['features']

            return AnalysisResult(success=True, data={
                "type": "FeatureCollection",
                "features": dissolved_features
            }, stats={
                "input_count": len(features),
                "output_count": len(dissolved_features),
                "dissolve_field": dissolve_field,
            })
        except Exception as e:
            logger.error(f"Dissolve分析失败: {e}")
            return AnalysisResult(success=False, error_message=str(e))

    @classmethod
    def union(
        cls,
        features_a: List[Dict],
        features_b: List[Dict],
        callback: Optional[Callable] = None
    ) -> AnalysisResult:
        """联合分析 - 合并两个图层"""
        try:
            if callback: callback(50, "执行联合...")

            united = features_a + features_b

            return AnalysisResult(success=True, data={
                "type": "FeatureCollection",
                "features": united
            }, stats={
                "layer_a_count": len(features_a),
                "layer_b_count": len(features_b),
                "union_count": len(united),
            })
        except Exception as e:
            return AnalysisResult(success=False, error_message=str(e))
    @classmethod
    def spatial_join(
        cls,
        left: List[Dict],
        right: List[Dict],
        join_type: str = "inner",
        predicate: str = "intersects",
        callback: Optional[Callable] = None,
        left_crs: Optional[str] = None,
        right_crs: Optional[str] = None
    ) -> AnalysisResult:
        """空间连接 - 基于空间关系的属性联接"""
        try:
            allowed_joins = {"inner", "left", "right"}
            allowed_preds = {"intersects", "within", "contains", "touches", "crosses"}

            if join_type.lower() not in allowed_joins:
                join_type = "inner"
            if predicate.lower() not in allowed_preds:
                predicate = "intersects"

            if callback:
                callback(20, "格式化与投影校验...")

            # 转换为 GeoDataFrame
            gdf_left = gpd.GeoDataFrame.from_features(left)
            gdf_right = gpd.GeoDataFrame.from_features(right)

            # 统一与识别坐标系
            def get_fallback_crs(gdf):
                bounds = gdf.total_bounds
                if len(bounds) == 4 and -181 <= bounds[0] <= 181 and -91 <= bounds[1] <= 91:
                    return "EPSG:4326"
                return None

            if left_crs: gdf_left.set_crs(left_crs, inplace=True)
            elif gdf_left.crs is None: 
                fallback = get_fallback_crs(gdf_left)
                if fallback: gdf_left.set_crs(fallback, inplace=True)
            
            if right_crs: gdf_right.set_crs(right_crs, inplace=True)
            elif gdf_right.crs is None:
                fallback = get_fallback_crs(gdf_right)
                if fallback: gdf_right.set_crs(fallback, inplace=True)

            # 如果仍然缺失，默认 4326
            if gdf_left.crs is None: gdf_left.set_crs("EPSG:4326", inplace=True)
            if gdf_right.crs is None: gdf_right.set_crs("EPSG:4326", inplace=True)

            # 如果坐标系不一致，将右图层转换为左图层坐标系
            if gdf_left.crs != gdf_right.crs:
                if callback: callback(40, f"正在进行重投影: {gdf_right.crs} -> {gdf_left.crs}")
                gdf_right = gdf_right.to_crs(gdf_left.crs)

            if callback:
                callback(60, f"执行{join_type}连接(predicate:{predicate})...")

            # 建立空间索引
            from shapely.strtree import STRtree

            # 从 GeoDataFrame 提取几何对象
            left_geoms = gdf_left.geometry.tolist()
            right_geoms = gdf_right.geometry.tolist()
            right_features = gdf_right.__geo_interface__['features']

            results = []
            
            # 构建空间索引树
            valid_right = [(i, g) for i, g in enumerate(right_geoms) if g is not None]
            if valid_right:
                strtree = STRtree([g for _, g in valid_right])
                
                predicate_map = {
                    "intersects": lambda l, r: l.intersects(r),
                    "within": lambda l, r: l.within(r),
                    "contains": lambda l, r: l.contains(r),
                    "touches": lambda l, r: l.touches(r),
                    "crosses": lambda l, r: l.crosses(r),
                }
                spatial_pred = predicate_map.get(predicate.lower(), predicate_map["intersects"])
                
                for idx, l_geom in enumerate(left_geoms):
                    if l_geom is None:
                        continue
                    
                    matched = False
                    candidates = strtree.query(l_geom)
                    
                    for c_idx in candidates:
                        r_geom = valid_right[c_idx][1]
                        if r_geom is not None and spatial_pred(l_geom, r_geom):
                            r_feat = right_features[c_idx]
                            results.append({
                                "type": "Feature",
                                "properties": {**left[idx].get("properties", {}), **r_feat.get("properties", {})},
                                "geometry": left[idx].get("geometry", {})
                            })
                            matched = True
                    
                    if not matched and join_type.lower() == "left":
                        results.append({
                            "type": "Feature",
                            "properties": left[idx].get("properties", {}),
                            "geometry": left[idx].get("geometry", {})
                        })
            else:
                if join_type.lower() == "right":
                    for r_feat in right:
                        results.append({
                            "type": "Feature",
                            "properties": {},
                            "geometry": r_feat.get("geometry", {})
                        })

            if callback:
                callback(90, "完成空间连接...")

            return AnalysisResult(success=True, data={
                "type": "FeatureCollection",
                "features": results
            }, stats={
                "left_count": len(left),
                "right_count": len(right),
                "joined_count": len(results),
                "join_type": join_type,
                "predicate": predicate
            })
        except Exception as e:
            logger.error(f"空间连接失败: {e}")
            return AnalysisResult(success=False, error_message=str(e))

    @classmethod
    def statistics(
        cls,
        features: List[Dict],
        field: Optional[str] = None,
        spatial_stats: bool = False,
        callback: Optional[Callable] = None
    ) -> AnalysisResult:
        """统计分析 - 属性统计和空间统计"""
        try:
            if callback: callback(20, "计算统计...")

            # 属性统计
            prop_values = []
            for f in features:
                props = f.get("properties", {})
                if field and field in props:
                    val = props[field]
                    if isinstance(val, (int, float)):
                        prop_values.append(val)

            attr_stats = {}
            if prop_values:
                attr_stats = {
                    "sum": sum(prop_values),
                    "mean": sum(prop_values) / len(prop_values),
                    "min": min(prop_values),
                    "max": max(prop_values),
                    "count": len(prop_values),
                }

            # 空间统计
            spatial_stats_result = {}
            if spatial_stats:
                if callback: callback(50, "执行空间几何分析...")
                
                # 预处理 features 确保 GeoPandas 兼容性
                processed_features = []
                for f in features:
                    if not isinstance(f, dict): continue
                    if "properties" not in f:
                        processed_features.append({**f, "properties": {}})
                    else:
                        processed_features.append(f)

                gdf = gpd.GeoDataFrame.from_features(processed_features)
                if gdf.empty:
                    return AnalysisResult(success=False, error_message="No valid features for statistics")

                if gdf.crs is None: gdf.set_crs("EPSG:4326", inplace=True)
                
                # 为计算面积和长度，投影到适合的世界投影 (Molleweide) 或 UTM
                if gdf.crs.is_geographic:
                    gdf_proj = gdf.to_crs("ESRI:54009") # Mollweide
                else:
                    gdf_proj = gdf

                def count_vertices(geom):
                    if geom is None: return 0
                    if geom.geom_type == 'Point': return 1
                    if geom.geom_type == 'LineString': return len(geom.coords)
                    if geom.geom_type == 'Polygon': return len(geom.exterior.coords)
                    if geom.geom_type.startswith('Multi'):
                        return sum(count_vertices(part) for part in geom.geoms)
                    return 0

                total_vertices = sum(count_vertices(g) for g in gdf.geometry)

                spatial_stats_result = {
                    "total_features": len(processed_features),
                    "total_vertices": int(total_vertices),
                    "total_area_sqkm": float(gdf_proj.area.sum() / 1e6),
                    "total_length_km": float(gdf_proj.length.sum() / 1000),
                    "avg_area_sqkm": float(gdf_proj.area.mean() / 1e6) if len(gdf) > 0 else 0,
                }

            if callback: callback(90, "完成...")

            return AnalysisResult(success=True, data={
                "attribute_statistics": attr_stats,
                "spatial_statistics": spatial_stats_result,
            }, stats={
                "analyzed_fields": [field] if field else [],
                "feature_count": len(features),
            })
        except Exception as e:
            return AnalysisResult(success=False, error_message=str(e))

    @classmethod
    def nearest(
        cls,
        source_features: List[Dict],
        target_features: List[Dict],
        max_distance: Optional[float] = None,
        unit: str = "m",
        count: int = 1,
        callback: Optional[Callable] = None,
        source_crs: Optional[str] = None,
        target_crs: Optional[str] = None
    ) -> AnalysisResult:
        """最近邻分析 - 查找最近的目标要素
        
        参数:
            source_features: 源要素列表
            target_features: 目标要素列表
            max_distance: 最大距离限制
            unit: 距离单位 (m/km)
            count: 返回最近邻的数量
            callback: 进度回调
        """
        try:
            if callback: callback(20, "准备数据...")
            
            # 转换为GeoDataFrame
            source_gdf = gpd.GeoDataFrame.from_features(source_features)
            target_gdf = gpd.GeoDataFrame.from_features(target_features)
            
            # CRS 校验与重投影
            if source_crs: source_gdf.set_crs(source_crs, inplace=True)
            if target_crs: target_gdf.set_crs(target_crs, inplace=True)
            
            # Fallback 探测
            for g in [source_gdf, target_gdf]:
                if g.crs is None:
                    bounds = g.total_bounds
                    if len(bounds) == 4 and -181 <= bounds[0] <= 181 and -91 <= bounds[1] <= 91:
                        g.set_crs("EPSG:4326", inplace=True)
            
            # 最终兜底
            if source_gdf.crs is None: source_gdf.set_crs("EPSG:4326", inplace=True)
            if target_gdf.crs is None: target_gdf.set_crs("EPSG:4326", inplace=True)
            
            if source_gdf.crs != target_gdf.crs:
                if callback: callback(30, f"重投影目标图层: {target_gdf.crs} -> {source_gdf.crs}")
                target_gdf = target_gdf.to_crs(source_gdf.crs)

            if callback: callback(40, "查找最近邻...")
            
            # 计算最近邻
            results = []
            # 单位转换
            factor = cls.UNIT_METERS.get(unit.lower(), 1.0)
            max_distance_m = max_distance * factor if max_distance else None
            
            for idx, source_row in source_gdf.iterrows():
                source_geom = source_row.geometry
                
                # 计算到所有目标的距离
                distances = target_gdf.geometry.distance(source_geom)
                
                # 找到最近的
                min_idx = distances.idxmin()
                min_distance = distances[min_idx]
                
                # 转换到指定单位
                min_distance_in_unit = min_distance / factor
                
                # 如果设置了最大距离限制
                if max_distance_m is not None and min_distance > max_distance_m:
                    continue
                
                # 合并属性
                result_feature = {
                    "type": "Feature",
                    "geometry": mapping(source_geom),
                    "properties": {
                        **source_row.drop('geometry').to_dict(),
                        "nearest_distance": float(min_distance_in_unit)
                    }
                }
                results.append(result_feature)

            if callback: callback(90, "完成最近邻分析...")

            return AnalysisResult(success=True, data={
                "type": "FeatureCollection",
                "features": results
            }, stats={
                "source_count": len(source_features),
                "target_count": len(target_features),
                "nearest_count": len(results),
                "unit": unit
            })
        except Exception as e:
            logger.error(f"最近邻分析失败: {e}")
            return AnalysisResult(success=False, error_message=str(e))

    @classmethod
    def export(
        cls,
        features: List[Dict],
        format: str = "geojson",
        output_path: Optional[str] = None,
        callback: Optional[Callable] = None
    ) -> AnalysisResult:
        """导出分析结果"""
        try:
            import tempfile
            import zipfile
            import base64
            import os
            
            format_normalized = format.lower()
            gdf = gpd.GeoDataFrame.from_features(features)

            if not output_path:
                if format_normalized in ["shp", "shapefile"]:
                    with tempfile.TemporaryDirectory() as tmpdir:
                        shp_path = os.path.join(tmpdir, "output.shp")
                        gdf.to_file(shp_path, driver="ESRI Shapefile")
                        
                        # 创建ZIP文件
                        zip_path = os.path.join(tmpdir, "output.zip")
                        with zipfile.ZipFile(zip_path, 'w') as zf:
                            for ext in ['.shp', '.shx', '.dbf', '.prj']:
                                file_path = os.path.join(tmpdir, f"output{ext}")
                                if os.path.exists(file_path):
                                    zf.write(file_path, f"output{ext}")
                        
                        # 读取并编码
                        with open(zip_path, 'rb') as f:
                            zip_content = f.read()
                        
                        base64_content = base64.b64encode(zip_content).decode('utf-8')
                        
                        return AnalysisResult(success=True, data={
                            "format": "shp",
                            "content": base64_content,
                            "encoding": "base64"
                        })
                else:
                    return AnalysisResult(
                        success=False,
                        error_message=f"该格式需要指定 output_path: {format_normalized}"
                    )
            
            # 导出到文件
            if format_normalized == "geojson":
                gdf.to_file(output_path, driver="GeoJSON")
            elif format_normalized in ["shp", "shapefile"]:
                gdf.to_file(output_path, driver="ESRI Shapefile")
            elif format_normalized == "csv":
                gdf['geometry_wkt'] = gdf.geometry.astype(str)
                gdf.drop(columns=['geometry']).to_csv(output_path, index=False)
            
            if callback: callback(90, "导出完成...")
            
            return AnalysisResult(success=True, data={
                "format": format_normalized,
                "output_path": output_path,
                "feature_count": len(features)
            }, stats={
                "exported_features": len(features),
                "format": format_normalized
            })
        except Exception as e:
            logger.error(f"Export失败: {e}")
            return AnalysisResult(success=False, error_message=str(e))

    @classmethod
    def path_analysis(
        cls,
        network_features: List[Dict],
        start_point: List[float],
        end_point: List[float],
        analysis_type: str = "shortest",
        callback: Optional[Callable] = None
    ) -> AnalysisResult:
        """路径分析 - 使用 networkx 实现真实路网寻径"""
        try:
            import networkx as nx
            from shapely.geometry import shape, Point, LineString
            from shapely.ops import nearest_points
            
            if callback: callback(20, "构建路网拓扑结构...")
            
            G = nx.Graph()
            # 存储节点和其实际坐标
            nodes_data = {}
            
            if not network_features:
                return AnalysisResult(False, "网络要素集为空")

            for feat in network_features:
                geom = shape(feat["geometry"])
                if not isinstance(geom, LineString): continue
                
                coords = list(geom.coords)
                for i in range(len(coords) - 1):
                    u, v = coords[i], coords[i+1]
                    dist = Point(u).distance(Point(v))
                    G.add_edge(u, v, weight=dist)
            
            if G.number_of_nodes() == 0:
                return AnalysisResult(False, "无法从要素中提取有效的路网节点")

            if callback: callback(50, "匹配起点与终点到路网...")
            
            # 使用 scipy 或简单的点对点匹配最近节点
            import numpy as np
            all_nodes = list(G.nodes)
            nodes_arr = np.array(all_nodes)
            
            def find_nearest_node(pt):
                dists = np.sum((nodes_arr - np.array(pt))**2, axis=1)
                return all_nodes[np.argmin(dists)]

            u_node = find_nearest_node(start_point)
            v_node = find_nearest_node(end_point)
            
            if callback: callback(70, "执行最短路径计算...")
            
            try:
                path = nx.shortest_path(G, source=u_node, target=v_node, weight='weight')
                path_length = nx.shortest_path_length(G, source=u_node, target=v_node, weight='weight')
                
                result_feature = {
                    "type": "Feature",
                    "geometry": {
                        "type": "LineString",
                        "coordinates": path
                    },
                    "properties": {
                        "analysis_type": "shortest_path",
                        "distance_deg": round(path_length, 6),
                        "node_count": len(path)
                    }
                }
                
                if callback: callback(100, "路径生成成功")
                return AnalysisResult(success=True, data={
                    "type": "FeatureCollection",
                    "features": [result_feature]
                }, stats={"distance": path_length, "nodes": len(path)})
            except nx.NetworkXNoPath:
                return AnalysisResult(False, "路网中不存在连接起终点的路径")

        except Exception as e:
            logger.error(f"Path分析失败: {e}")
            return AnalysisResult(success=False, error_message=str(e))

    @classmethod
    def zonal_statistics(
        cls,
        zones: List[Dict],
        raster_path: str,
        stats: List[str] = ["mean", "sum", "count"],
        callback: Optional[Callable] = None
    ) -> AnalysisResult:
        """区域统计 - 矢量区域与栅格数据的交叉统计"""
        try:
            import rasterio
            from rasterio.mask import mask
            from shapely.geometry import shape
            import numpy as np
            
            if callback: callback(20, f"打开栅格文件: {raster_path}")
            
            with rasterio.open(raster_path) as src:
                results = []
                geoms = [shape(f["geometry"]) for f in zones]
                
                for i, geom in enumerate(geoms):
                    if callback: callback(30 + int(60 * i/len(geoms)), f"处理区域 {i+1}...")
                    
                    try:
                        out_image, out_transform = mask(src, [geom], crop=True)
                        data = out_image[0]
                        # 排除 NoData
                        valid_data = data[data != src.nodata]
                        
                        if valid_data.size > 0:
                            s = {
                                "id": zones[i].get("id", i),
                                "mean": float(np.mean(valid_data)),
                                "sum": float(np.sum(valid_data)),
                                "count": int(valid_data.size),
                                "min": float(np.min(valid_data)),
                                "max": float(np.max(valid_data))
                            }
                            results.append(s)
                    except Exception:
                        continue
                
                return AnalysisResult(success=True, data={"zonal_stats": results})
        except Exception as e:
            return AnalysisResult(False, str(e))

    @classmethod
    def classify_breaks(
        cls,
        values: List[float],
        method: str = "quantiles",
        k: int = 5
    ) -> List[float]:
        """计算分类间断点 (Breaks)"""
        import numpy as np
        if not values: return []
        arr = np.array(values)
        if method == "quantiles":
            return np.unique(np.quantile(arr, np.linspace(0, 1, k + 1))).tolist()
        elif method == "equal_interval":
            return np.linspace(arr.min(), arr.max(), k + 1).tolist()
        return []

    @classmethod
    def geometry_ops(
        cls,
        features: List[Dict],
        op_type: str = "centroid",
        callback: Optional[Callable] = None
    ) -> AnalysisResult:
        """基础几何操作: centroid, convex_hull, simplify"""
        from shapely.geometry import shape, mapping
        try:
            results = []
            for f in features:
                geom = shape(f["geometry"])
                if op_type == "centroid":
                    res_geom = geom.centroid
                elif op_type == "convex_hull":
                    res_geom = geom.convex_hull
                elif op_type == "simplify":
                    res_geom = geom.simplify(0.001)
                else:
                    continue
                
                results.append({
                    "type": "Feature",
                    "geometry": mapping(res_geom),
                    "properties": f.get("properties", {})
                })
            
            return AnalysisResult(True, {"type": "FeatureCollection", "features": results})
        except Exception as e:
            return AnalysisResult(False, str(e))

ANALYSIS_OPERATORS = {
    "buffer": SpatialAnalyzer.buffer,
    "clip": SpatialAnalyzer.clip,
    "intersect": SpatialAnalyzer.intersect,
    "dissolve": SpatialAnalyzer.dissolve,
    "union": SpatialAnalyzer.union,
    "statistic": SpatialAnalyzer.statistics,
    "statistics": SpatialAnalyzer.statistics,
    "spatial_join": SpatialAnalyzer.spatial_join,
    "spatial-join": SpatialAnalyzer.spatial_join,
    "nearest": SpatialAnalyzer.nearest,
    "export": SpatialAnalyzer.export,
    "path_analysis": SpatialAnalyzer.path_analysis,
    "zonal_stats": SpatialAnalyzer.zonal_statistics,
    "geometry_ops": SpatialAnalyzer.geometry_ops,
    "overlay": SpatialAnalyzer.overlay,
    "attribute_filter": SpatialAnalyzer.attribute_filter,
}
def execute_analysis(
    task_type: str,
    parameters: Dict,
    input_data: Dict,
    callback: Optional[Callable] = None
) -> AnalysisResult:
    """
    执行分析的统一入口函数
    """
    op_func = ANALYSIS_OPERATORS.get(task_type.lower())
    if not op_func:
        return AnalysisResult(False, error_message=f"未知分析类型: {task_type}")

    kwargs = {"callback": callback} if callback else {}

    if task_type == "buffer":
        kwargs.update({
            "features": input_data.get("features", []),
            "distance": parameters.get("distance", 100),
            "unit": parameters.get("unit", "m"),
            "dissolve": parameters.get("dissolve", False),
        })
    elif task_type == "clip":
        kwargs.update({
            "features": input_data.get("features", []),
            "boundary": parameters.get("boundary", {}),
        })
    elif task_type in ["intersect", "union", "overlay"]:
        kwargs.update({
            "features_a": input_data.get("features_a", []),
            "features_b": input_data.get("features_b", []),
            "how": parameters.get("how", "intersection")
        })
    elif task_type in ["spatial_join", "spatial-join"]:
        kwargs.update({
            "left": input_data.get("left", []),
            "right": input_data.get("right", []),
            "join_type": parameters.get("join_type", "inner"),
            "predicate": parameters.get("predicate", "intersects"),
        })
    elif task_type == "statistics":
        kwargs.update({
            "features": input_data.get("features", []),
            "field": parameters.get("field"),
            "spatial_stats": parameters.get("spatial_stats", False),
        })
    elif task_type == "path_analysis":
        kwargs.update({
            "network_features": input_data.get("network_features", []),
            "start_point": parameters.get("start_point"),
            "end_point": parameters.get("end_point"),
        })
    elif task_type == "zonal_stats":
        kwargs.update({
            "zones": input_data.get("features", []),
            "raster_path": parameters.get("raster_path"),
        })
    elif task_type == "geometry_ops":
        kwargs.update({
            "features": input_data.get("features", []),
            "op_type": parameters.get("op_type", "centroid"),
        })

    return op_func(**kwargs)

__all__ = ["SpatialAnalyzer", "execute_analysis", "ANALYSIS_OPERATORS", "AnalysisResult"]