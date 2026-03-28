"""
T004 基础空间分析算子 - 缓冲区、裁剪、相交、融合、统计
结果自动生成矢量图层入库
"""
import logging
from typing import Dict, Any, Optional, List, Tuple, Callable
from app.services.spatial_analyzer import SpatialAnalyzer, AnalysisResult

logger = logging.getLogger(__name__)

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
    
    # 调用服务层方法
    result: AnalysisResult = method(features, **(params or {}), callback=callback)
    
    if not result.success:
        raise RuntimeError(f"分析执行失败: {result.error_message}")
    
    # 转换为旧格式兼容
    return {
        **result.data,
        "statistics": result.stats
    }

__all__ = ["SpatialAnalyzer", "run_analysis"]
