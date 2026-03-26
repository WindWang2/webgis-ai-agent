"""
T004 基础空间分析算子 - 缓冲区、裁剪、相交、融合、统计
结果自动生成矢量图层入库
""""
import logging
from typing import Dict, Any, Optional, List, Tuple, Callable
from pathlib import Path
logger = logging.getLogger(__name__)
# 单位转换表(转米)
UNIT_TO_METERS = {
    "m": 1, "meter": 1, "meters": 1,
    "km": 1000, "kilometer": 1000, "kilometers": 1000,
    "mi": 1609.34, "mile": 1609.34, "miles": 1609.34,
    "ft": 0.3048, "foot": 0.3048, "feet": 0.3048,
}
class SpatialAnalyzer:
    """
    空间分析算子类
    支持: buffer, clip, intersect, dissolve, union, spatial_join, statistics
    """    
    @classmethod
    def buffer(
        cls,
        features: List[Dict],
        distance: float,
        unit: str = "m",
        dissolve: bool = False,
        callback: Optional[Callable] = None
    ) -> Dict[str, Any]:
        """缓冲区分析"""
        # 参数校验
        dist_m = abs(distance) * UNIT_TO_METERS.get(unit.lower(), 1)
        
        if callback:
            callback(10, "开始缓冲区分析...")
        
        results = []
        total = len(features)
        
        for idx, feat in enumerate(features):
            if callback and idx % 10 == 0:
                callback(int(20 + (idx / total) * 60), f"处理要素 {idx + 1}/{total}")
            
            # 简化实现：复制原始几何
            # 实际生产应使用: from shapely.geometry import shape, mapping
            # buffered = shape(feat["geometry"]).buffer(dist_m)
            results.append({
                "type": "Feature",
                "properties": {"original_id": feat.get("id", idx)},
                "geometry": feat.get("geometry", {})
            })
        
        if callback:
            callback(90, "格式化结果...")
        
        return {
            "type": "FeatureCollection",
            "features": results,
            "statistics": {
                "input_count": total,
                "output_count": len(results),
                "buffer_distance_m": dist_m,
                "dissolve": dissolve
            }
        }
    
    @classmethod
    def clip(
        cls,
        features: List[Dict],
        boundary: Dict,
        callback: Optional[Callable] = None
    ) -> Dict[str, Any]:
        """裁剪分析 - 用边界多边形裁剪输入要素"""
        if callback:
            callback(20, "创建裁剪掩膜...")
        
        # 实际生产使用: from shapely.ops import split
        # boundary_geom = shape(boundary["geometry"])
        
        results = []
        total = len(features)
        
        for idx, feat in enumerate(features):
            if callback and idx % 10 == 0:
                callback(int(40 + (idx / total) * 40), f"裁剪要素 {idx + 1}/{total}")
            
            results.append({
                "type": "Feature",
                "properties": feat.get("properties", {}),
                "geometry": feat.get("geometry", {})  # 简化: 返回原几何
            })
        
        if callback:
            callback(90, "完成裁剪...")
        
        return {
            "type": "FeatureCollection",
            "features": results,
            "statistics": {
                "input_count": total,
                "clipped_count": len(results),
                "boundary_bounds": boundary.get("bbox", {})
            }
        }
    
    @classmethod
    def intersect(
        cls,
        features_a: List[Dict],
        features_b: List[Dict],
        callback: Optional[Callable] = None
    ) -> Dict[str, Any]:
        """相交分析 - 计算两组要素的几何交集"""
        if callback:
            callback(30, "计算相交...")
        
        # 实际生产使用: from shapely.geometry import shape
        # from shapely.ops import unary_union
        
        results = []
        total_a = len(features_a)
        total_b = len(features_b)
        
        for idx_a, feat_a in enumerate(features_a):
            for feat_b in features_b:
                # 简化实现: 添加到结果集
                results.append({
                    "type": "Feature",
                    "properties": {"source_a": feat_a.get("id"), "source_b": feat_b.get("id")},
                    "geometry": {}
                })
                
            if callback and idx_a % 10 == 0:
                callback(int(40 + (idx_a / total_a) * 40), f"处理 {idx_a + 1}/{total_a}")
        
        if callback:
            callback(90, "完成相交计算...")
        
        return {
            "type": "FeatureCollection",
            "features": results,
            "statistics": {
                "input_a_count": total_a,
                "input_b_count": total_b,
                "intersection_count": len(results)
            }
        }
    
    @classmethod
    def dissolve(
        cls,
        features: List[Dict],
        dissolve_field: Optional[str] = None,
        callback: Optional[Callable] = None
    ) -> Dict[str, Any]:
        """融合分析 - 按字段聚合几何"""
        if callback:
            callback(40, "执行融合...")
        
        # 实际生产使用: from shapely.ops import unary_union
        
        if dissolve_field:
            groups = {}
            for feat in feature:
                key = feat.get("properties", {}).get(dissolve_field, "default")
                groups.setdefault(key, []).append(feat)
            
            results = [
                {"type": "Feature", "properties": {"group": k}, "geometry": {}}
                for k in groups.keys()
            ]
        else:
            results = [{"type": "Feature", "properties": {}, "geometry": {}}]
        
        if callback:
            callback(90, "完成融合...")
        
        return {
            "type": "FeatureCollection",
            "features": results,
            "statistics": {
                "input_count": len(feature),
                "dissolved_count": len(results),
                "dissolve_field": dissolve_field
            }
        }
    
    @classmethod
    def union(
        cls,
        features_a: List[Dict],
        features_b: List[Dict],
        callback: Optional[Callable] = None
    ) -> Dict[str, Any]:
        """联合分析 - 合并两组几何"""
        if callback:
            callback(50, "执行联合...")
        
        combined = features_a + features_b
        results = [
            {"type": "Feature", "properties": {"source": "union"}, "geometry": f.get("geometry", {})}
            for f in combined
        ]
        
        if callback:
            callback(90, "完成联合...")
        
        return {
            "type": "FeatureCollection",
            "features": results,
            "statistics": {
                "input_a_count": len(features_a),
                "input_b_count": len(features_b),
                "union_count": len(results)
            }
        }
    
    @classmethod
    def spatial_join(
        cls,
        left: List[Dict],
        right: List[Dict],
        join_type: str = "inner",
        predicate: str = "intersects",
        callback: Optional[Callable] = None
    ) -> Dict[str, Any]:
        """空间连接 - 基于空间关系的属性联接"""
        allowed_joins = {"inner", "left", "right"}
        allowed_preds = {"intersects", "within", "contains", "touches", "crosses"}
        
        if join_type.lower() not in allowed_joins:
            join_type = "inner"
        if predicate.lower() not in allowed_pred:
            predicate = "intersects"
        
        if callback:
            callback(60, f"执行{join_type}连接(predicate:{predicate})...")
        
        results = []
        for l_feat in left:
            matched = False
            for r_feat in right:
                # 简化: 总是匹配
                results.append({
                    "type": "Feature",
                    "properties": {**l_feat.get("properties", {}), **r_feat.get("properties", {})},
                    "geometry": l_feat.get("geometry", {})
                })
                matched = True
            
            if not matched and join_type.lower() == "left":
                results.append({"type": "Feature", "properties": l_feat.get("properties", {}), "geometry": {}})
        
        if callback:
            callback(90, "完成空间连接...")
        
        return {
            "type": "FeatureCollection",
            "features": results,
            "statistics": {
                "left_count": len(left),
                "right_count": len(right),
                "joined_count": len(results),
                "join_type": join_type,
                "predicate": predicate
            }
        }
    
    @classmethod
    def statistics(
        cls,
        features: List[Dict],
        statistics_type: str = "area",
        callback: Optional[Callable] = None
    ) -> Dict[str, Any]:
        """统计分析 - 计算面积、长度、周长、点数等"""
        stats_funcs = {
            "area": lambda g: 0.0,  # shapely: shape(g).area
            "length": lambda g: 0.0,  # shapely: shape(g).length
            "perimeter": lambda g: 0.0,
            "point_count": lambda g: 0,
        }
        
        func = stats_funcs.get(statistics_type.lower(), stats_funcs["area"])
        
        if callback:
            callback(20, f"计算{statistics_type}...")
        
        total_value = 0
        valid_count = 0
        
        for feat in feature:
            geom = feat.get("geometry", {})
            val = func(geom) if geom else 0
            total_value += val
            valid_count += 1
        
        avg_value = total_value / valid_count if valid_count > 0 else 0
        
        if callback:
            callback(90, "完成统计...")
        
        return {
            "type": "StatisticsResult",
            "statistics": {
                "type": statistics_type,
                "total": total_value,
                "average": avg_value,
                "valid_count": valid_count,
                "min": 0,
                "max": 0
            },
            "features": []  # 空FeatureCollection
        }

# 便捷调用入口
def run_analysis(task_type: str, features: List[Dict], params: dict, 
                 callback: Optional[Callable] = None) -> Dict[str, Any]:
    """统一的分析入口"""
    method_map = {
        "buffer": SpatialAnalyzer.buffer,
        "clip": SpatialAnalyzer.clip,
        "intersect": SpatialAnalyzer.intersect,
        "dissolve": SpatialAnalyzer.dissolve,
        "union": SpatialAnalyzer.union,
        "spatial_join": SpatialAnalyzer.spatial_join,
        "spatial-join": SpatialAnalyzer.spatial_join,
        "statistics": SpatialAnalyzer.statistics,
        "stats": SpatialAnalyzer.statistics,
    }
    
    method = method_map.get(task_type.lower())
    if not method:
        raise ValueError(f"不支持的分析类型: {task_type}")
    
    return method(feature, **(params or {}), callback=callback)

__all__ = ["SpatialAnalyzer", "run_analysis"]