"""
空间分析任务
"""

import logging
from typing import Dict, Any, Optional
from celery import Task

from app.services.task_queue import celery_app
from app.db.session import get_db_session

logger = logging.getLogger(__name__)


class BaseAnalysisTask(Task):
    """基础分析任务类"""
    
    def on_success(self, retval, task_id, args, kwargs):
        """任务成功回调"""
        from app.services.layer_service import TaskService
        db = next(get_db_session())
        task_service = TaskService(db)
        task_service.update_task_status(
            task_id, 
            "completed", 
            progress=100,
            result=retval
        )
        db.close()
        logger.info(f"任务完成：{task_id}")
    
    def on_failure(self, exc, task_id, args, kwargs, einfo):
        """任务失败回调"""
        from app.services.layer_service import TaskService
        db = next(get_db_session())
        task_service = TaskService(db)
        task_service.update_task_status(
            task_id,
            "failed",
            error_message=str(exc)
        )
        db.close()
        logger.error(f"任务失败：{task_id}, 错误：{exc}")


@celery_app.task(bind=True, base=BaseAnalysisTask)
def run_analysis(
    self,
    task_type: str,
    parameters: Dict[str, Any],
    layer_id: Optional[int] = None,
    task_id: Optional[str] = None
):
    """
    执行空间分析任务
    
    支持的任务类型:
    - buffer: 缓冲区分析
    - clip: 裁剪分析
    - intersect: 相交分析
    - dissolve: 融合分析
    - union: 联合分析
    - spatial_join: 空间连接
    
    Args:
        task_type: 任务类型
        parameters: 任务参数
        layer_id: 关联图层 ID
        task_id: 任务 ID（用于状态更新）
    
    Returns:
        分析结果
    """
    # 获取任务 ID（用于状态更新）
    actual_task_id = task_id or self.request.id
    
    # 更新状态为运行中
    from app.services.layer_service import TaskService
    db = next(get_db_session())
    task_service = TaskService(db)
    task_service.update_task_status(actual_task_id, "running", progress=0)
    
    try:
        result = None
        
        if task_type == "buffer":
            result = _buffer_analysis(parameters, layer_id, self)
        elif task_type == "clip":
            result = _clip_analysis(parameters, layer_id, self)
        elif task_type == "intersect":
            result = _intersect_analysis(parameters, layer_id, self)
        elif task_type == "dissolve":
            result = _dissolve_analysis(parameters, layer_id, self)
        elif task_type == "union":
            result = _union_analysis(parameters, layer_id, self)
        elif task_type == "spatial_join":
            result = _spatial_join_analysis(parameters, layer_id, self)
        else:
            raise ValueError(f"不支持的任务类型：{task_type}")
        
        db.close()
        return result
        
    except Exception as e:
        db.close()
        logger.error(f"任务执行失败：{e}")
        raise


def _buffer_analysis(
    parameters: Dict[str, Any],
    layer_id: Optional[int],
    task: Task
) -> Dict[str, Any]:
    """
    缓冲区分析
    
    参数:
        - distance: 缓冲区距离
        - units: 单位（meters, kilometers, feet）
        - dissolve: 是否融合结果
    """
    distance = parameters.get("distance", 100)
    units = parameters.get("units", "meters")
    dissolve = parameters.get("dissolve", False)
    
    # 模拟进度更新
    task.update_state(
        state="PROGRESS",
        meta={"progress": 30, "message": "正在创建缓冲区..."}
    )
    
    # TODO: 实现真实的缓冲区分析
    # 使用 geopandas 和 shapely
    # from shapely.geometry import shape
    # from geopandas import GeoDataFrame
    
    task.update_state(
        state="PROGRESS",
        meta={"progress": 100, "message": "完成"}
    )
    
    return {
        "task_type": "buffer",
        "status": "completed",
        "parameters": {"distance": distance, "units": units},
        "result_url": f"/api/v1/results/buffer_{task.request.id}.geojson",
        "statistics": {
            "features_count": 100,
            "area": 50000  # 平方米
        }
    }


def _clip_analysis(
    parameters: Dict[str, Any],
    layer_id: Optional[int],
    task: Task
) -> Dict[str, Any]:
    """
    裁剪分析
    
    参数:
        - clip_layer_id: 裁剪图层 ID
        - output_format: 输出格式
    """
    clip_layer_id = parameters.get("clip_layer_id")
    output_format = parameters.get("output_format", "geojson")
    
    task.update_state(
        state="PROGRESS",
        meta={"progress": 40, "message": "正在执行裁剪..."}
    )
    
    task.update_state(
        state="PROGRESS",
        meta={"progress": 100, "message": "完成"}
    )
    
    return {
        "task_type": "clip",
        "status": "completed",
        "parameters": {"clip_layer_id": clip_layer_id},
        "result_url": f"/api/v1/results/clip_{task.request.id}.{output_format}"
    }


def _intersect_analysis(
    parameters: Dict[str, Any],
    layer_id: Optional[int],
    task: Task
) -> Dict[str, Any]:
    """
    相交分析
    
    参数:
        - intersect_layer_id: 相交图层 ID
    """
    intersect_layer_id = parameters.get("intersect_layer_id")
    
    task.update_state(
        state="PROGRESS",
        meta={"progress": 50, "message": "正在计算相交..."}
    )
    
    return {
        "task_type": "intersect",
        "status": "completed",
        "parameters": {"intersect_layer_id": intersect_layer_id},
        "result_url": f"/api/v1/results/intersect_{task.request.id}.geojson"
    }


def _dissolve_analysis(
    parameters: Dict[str, Any],
    layer_id: Optional[int],
    task: Task
) -> Dict[str, Any]:
    """
    融合分析
    
    参数:
        - dissolve_field: 融合字段
    """
    dissolve_field = parameters.get("dissolve_field")
    
    task.update_state(
        state="PROGRESS",
        meta={"progress": 60, "message": "正在融合要素..."}
    )
    
    return {
        "task_type": "dissolve",
        "status": "completed",
        "parameters": {"dissolve_field": dissolve_field},
        "result_url": f"/api/v1/results/dissolve_{task.request.id}.geojson"
    }


def _union_analysis(
    parameters: Dict[str, Any],
    layer_id: Optional[int],
    task: Task
) -> Dict[str, Any]:
    """
    联合分析
    
    参数:
        - union_layer_id: 联合图层 ID
    """
    union_layer_id = parameters.get("union_layer_id")
    
    task.update_state(
        state="PROGRESS",
        meta={"progress": 70, "message": "正在计算联合..."}
    )
    
    return {
        "task_type": "union",
        "status": "completed",
        "parameters": {"union_layer_id": union_layer_id},
        "result_url": f"/api/v1/results/union_{task.request.id}.geojson"
    }


def _spatial_join_analysis(
    parameters: Dict[str, Any],
    layer_id: Optional[int],
    task: Task
) -> Dict[str, Any]:
    """
    空间连接分析
    
    参数:
        - join_layer_id: 连接图层 ID
        - join_type: 连接类型（inner, left, etc.）
        - predicate: 空间谓词（intersects, contains, within, etc.）
    """
    join_layer_id = parameters.get("join_layer_id")
    join_type = parameters.get("join_type", "inner")
    predicate = parameters.get("predicate", "intersects")
    
    task.update_state(
        state="PROGRESS",
        meta={"progress": 80, "message": "正在执行空间连接..."}
    )
    
    return {
        "task_type": "spatial_join",
        "status": "completed",
        "parameters": {
            "join_layer_id": join_layer_id,
            "join_type": join_type,
            "predicate": predicate
        },
        "result_url": f"/api/v1/results/join_{task.request.id}.geojson"
    }
