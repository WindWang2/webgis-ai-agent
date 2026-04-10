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
    def intersect(
        cls,
        features_a: List[Dict],
        features_b: List[Dict],
        callback: Optional[Callable] = None
    ) -> AnalysisResult:
        """相交分析 - 计算两个图层交集"""
        try:
            if callback: callback(20, "准备数据...")
            
            # 转换为GeoDataFrame
            gdf_a = gpd.GeoDataFrame.from_features(features_a)
            gdf_b = gpd.GeoDataFrame.from_features(features_b)

            if callback: callback(40, "计算交集...")

            # 执行相交操作
            intersected = gpd.overlay(gdf_a, gdf_b, how='intersection')
            
            if callback: callback(80, "整理结果...")

            # 转换回GeoJSON
            intersection_features = intersected.__geo_interface__['features']

            return AnalysisResult(success=True, data={
                "type": "FeatureCollection",
                "features": intersection_features
            }, stats={
                "layer_a_count": len(features_a),
                "layer_b_count": len(features_b),
                "intersection_count": len(intersection_features),
            })
        except Exception as e:
            logger.error(f"Intersect分析失败: {e}")
            return AnalysisResult(success=False, error_message=str(e))
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
        callback: Optional[Callable] = None
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
                callback(60, f"执行{join_type}连接(predicate:{predicate})...")

            # 建立空间索引优化性能
            from shapely.strtree import STRtree
            
            # 创建几何对象
            left_geoms = []
            right_geoms = []
            right_features = []
            
            results = []

            for l_feat in left:
                geom = l_feat.get("geometry", {})
                if geom:
                    try:
                        left_geoms.append(shape(geom))
                    except Exception:
                        left_geoms.append(None)
                else:
                    left_geoms.append(None)
            
            for r_feat in right:
                geom = r_feat.get("geometry", {})
                if geom:
                    try:
                        right_geoms.append(shape(geom))
                        right_features.append(r_feat)
                    except Exception:
                        right_geoms.append(None)
                        right_features.append(None)
                else:
                    right_geoms.append(None)
                    right_features.append(None)
            
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
                spatial_stats_result = {
                    "total_features": len(features),
                    "total_vertices": 0,  # 需遍历计算
                    "avg_area": 0,
                    "avg_length": 0,
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
        callback: Optional[Callable] = None
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
                        "nearest_distance": float(min_distance_in_unit),
                        "nearest_target_id": int(min_idx)
                    }
                }
                results.append(result_feature)
            
            if callback: callback(90, "完成...")
            
            return AnalysisResult(success=True, data={
                "type": "FeatureCollection",
                "features": results
            }, stats={
                "source_count": len(source_features),
                "target_count": len(target_features),
                "result_count": len(results),
                "max_distance_m": max_distance_m,
                "unit": unit
            })
        except Exception as e:
            logger.error(f"Nearest分析失败: {e}")
            return AnalysisResult(success=False, error_message=str(e))

    @classmethod
    def export(
        cls,
        features: List[Dict],
        format: str,
        output_path: Optional[str] = None,
        callback: Optional[Callable] = None
    ) -> AnalysisResult:
        """导出功能 - 支持GeoJSON/Shapefile/CSV格式"""
        try:
            if callback: callback(20, "准备导出数据...")
            
            # 验证格式
            format_lower = format.lower()
            # 支持多种格式名称
            format_mapping = {
                "shp": "shapefile",
                "shapefile": "shapefile",
                "geojson": "geojson",
                "csv": "csv"
            }
            format_normalized = format_mapping.get(format_lower, format_lower)
            
            if format_normalized not in ["geojson", "shapefile", "csv"]:
                return AnalysisResult(
                    success=False,
                    error_message=f"不支持的导出格式: {format}. 支持的格式: geojson, shp/shapefile, csv"
                )
            
            # 转换为GeoDataFrame
            gdf = gpd.GeoDataFrame.from_features(features)
            
            if callback: callback(50, f"导出为{format_normalized}格式...")
            
            # 如果没有指定输出路径，返回数据
            if not output_path:
                if format_normalized == "geojson":
                    output_data = gdf.__geo_interface__
                    return AnalysisResult(success=True, data={
                        "format": format_lower,
                        "content": output_data
                    })
                elif format_normalized == "shapefile":
                    # Shapefile需要导出到临时目录并打包
                    import tempfile
                    import base64
                    import zipfile
                    import shutil
                    
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
                        error_message=f"导出{format_normalized}格式需要指定output_path"
                    )
            
            # 导出到文件
            if format_normalized == "geojson":
                gdf.to_file(output_path, driver="GeoJSON")
            elif format_normalized == "shapefile":
                gdf.to_file(output_path, driver="ESRI Shapefile")
            elif format_normalized == "csv":
                # 导出为CSV（包含WKT几何列）
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
        start_point: Dict,
        end_point: Dict,
        analysis_type: str = "shortest",
        callback: Optional[Callable] = None
    ) -> AnalysisResult:
        """路径分析 - 最短路径/服务区分析（可选功能，需要networkx）
        
        参数:
            network_features: 网络要素（线要素）
            start_point: 起点坐标
            end_point: 终点坐标
            analysis_type: 分析类型（shortest/service_area）
            callback: 进度回调
        """
        try:
            if callback: callback(20, "检查路径分析依赖...")
            
            # 检查是否有networkx
            try:
                import networkx as nx
                has_networkx = True
            except ImportError:
                has_networkx = False
                return AnalysisResult(
                    success=False,
                    error_message="路径分析需要安装networkx: pip install networkx"
                )
            
            if callback: callback(40, "构建网络图...")
            
            # 简化实现：返回直线连接
            # 实际应用中需要构建网络拓扑并使用networkx计算
            result_feature = {
                "type": "Feature",
                "geometry": {
                    "type": "LineString",
                    "coordinates": [
                        start_point.get("coordinates", [0, 0]),
                        end_point.get("coordinates", [0, 0])
                    ]
                },
                "properties": {
                    "analysis_type": analysis_type,
                    "distance": 0,  # 需要实际计算
                    "note": "简化实现 - 需要networkx支持"
                }
            }
            
            if callback: callback(90, "完成...")
            
            return AnalysisResult(success=True, data={
                "type": "FeatureCollection",
                "features": [result_feature]
            }, stats={
                "analysis_type": analysis_type,
                "network_available": has_networkx
            })
        except Exception as e:
            logger.error(f"Path分析失败: {e}")
            return AnalysisResult(success=False, error_message=str(e))

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
    "path-analysis": SpatialAnalyzer.path_analysis,
    "path": SpatialAnalyzer.path_analysis,
}
def execute_analysis(
    task_type: str,
    parameters: Dict,
    input_data: Dict,
    callback: Optional[Callable] = None
) -> AnalysisResult:
    """
    执行分析的统一入口函数
    根据 task_type 调用对应算子
    """
    op_func = ANALYSIS_OPERATORS.get(task_type.lower())
    if not op_func:
        return AnalysisResult(False, error_message=f"未知分析类型: {task_type}")

    # 路由参数 - 根据不同任务类型设置不同参数
    kwargs = {"callback": callback} if callback else {}

    if task_type == "buffer":
        kwargs.update({
            "features": input_data.get("features", []),
            "distance": parameters.get("distance", 100),
            "unit": parameters.get("unit", "m"),
            "dissolve": parameters.get("dissolve", False),
            "source_crs": parameters.get("source_crs") or input_data.get("source_crs"),
        })
    elif task_type == "clip":
        kwargs.update({
            "features": input_data.get("features", []),
            "boundary": parameters.get("boundary", {}),
        })
    elif task_type in ["intersect", "union"]:
        kwargs.update({
            "features_a": input_data.get("features_a", []),
            "features_b": input_data.get("features_b", []),
        })
    elif task_type in ["spatial_join", "spatial-join"]:
        kwargs.update({
            "left": input_data.get("left", []),
            "right": input_data.get("right", []),
            "join_type": parameters.get("join_type", "inner"),
            "predicate": parameters.get("predicate", "intersects"),
        })
    elif task_type == "dissolve":
        kwargs.update({
            "features": input_data.get("features", []),
            "dissolve_field": parameters.get("dissolve_field"),
        })
    elif task_type in ["statistics", "statistic"]:
        kwargs.update({
            "features": input_data.get("features", []),
            "field": parameters.get("field"),
            "spatial_stats": parameters.get("spatial_stats", False),
        })
    elif task_type == "nearest":
        kwargs.update({
            "source_features": input_data.get("source_features", []),
            "target_features": input_data.get("target_features", []),
            "max_distance": parameters.get("max_distance"),
        })
    elif task_type == "export":
        kwargs.update({
            "features": input_data.get("features", []),
            "format": parameters.get("format", "geojson"),
            "output_path": parameters.get("output_path"),
        })

    return op_func(**kwargs)
__all__ = ["SpatialAnalyzer", "execute_analysis", "ANALYSIS_OPERATORS", "AnalysisResult"]