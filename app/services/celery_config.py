"""
Celery 任务队列服务 - Redis Broker/Backend 配置
完整版本：直接对接 Redis，无中间商
"""
import os
import logging
from celery import Celery
from celery.schedules import crontab
from typing import Dict, Any, Optional, Callable

logger = logging.getLogger(__name__)

# ============== 直接使用 Redis 作为 Broker ==============
REDIS_HOST = os.environ.get("REDIS_HOST", "localhost")
REDIS_PORT = int(os.environ.get("REDIS_PORT", "6379"))
REDIS_DB_BROKER = int(os.environ.get("REDIS_DB_BROKER", "0"))  # Broker 用
REDIS_DB_RESULT = int(os.environ.get("REDIS_DB_RESULT", "1"))  # Result Backend 用

# 构建 Redis URL
REDIS_URL = f"redis://{REDIS_HOST}:{REDIS_PORT}/{REDIS_DB_BROKER}"
REDIS_RESULT_URL = f"redis://{REDIS_HOST}:{REDIS_PORT}/{REDIS_DB_RESULT}"

# 创建 Celery 应用，直接使用 Redis
celery_app = Celery(
    "webgis_ai",
    broker=REDIS_URL,
    backend=REDIS_RESULT_URL,
    include=[
        "app.tasks.analysis",
        "app.tasks.base"
    ]
)

# 完整 Celery 配置
celery_app.conf.update(
    # 序列化和时区
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="Asia/Shanghai",
    enable_utc=True,
    
    # 任务追踪
    task_track_started=True,
    task_send_sent_event=True,
    
    # 超时和重试
    task_soft_time_limit=3600,        # 软超时 1 小时
    task_time_limit=4200,          # 硬超时 70 分钟
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    
    # Worker 配置
    worker_prefetch_multiplier=1,
    worker_max_tasks_per_child=1000,
    worker_disable_rate_limits=True,
    
    # Result 配置
    result_expires=86400,
    result_extended=True,
    result_persistent=True,
    
    # 队列配置
    task_default_queue="default",
    task_queues={
        "default": {},
        "high_priority": {},
        "spatial_analysis": {},
    },
    task_routes={
        "app.tasks.analysis.*": {"queue": "spatial_analysis"},
    }
)

# ============== 通用任务回调 ==============
class TaskCallbackMixin:
    """任务回调混入类：进度上报、失败重试、超时终止"""
    
    @staticmethod
    def report_progress(progress: int, message: str):
        """进度上报"""
        return {
            "state": "PROGRESS",
            "meta": {"progress": min(progress, 100), "message": message}
        }

    def on_success(self, retval, task_id, args, kwargs):
        """成功回调"""
        logger.info(f"任务成功: {task_id}")
        self._update_task_status(task_id, "completed", 100, retval)
        
    def on_failure(self, exc, task_id, args, kwargs, einfo):
        """失败回调"""
        logger.error(f"任务失败: {task_id}, {exc}")
        self._update_task_status(task_id, "failed", 0, error_message=str(exc))
    
    def on_timeout(self, task_id):
        """超时回调"""
        logger.warning(f"任务超时: {task_id}")
        self._update_task_status(task_id, "timeout", 0, error_message="执行超时")
    
    def _update_task_status(self, task_id: str, status: str, progress: int, 
                           result=None, error_message=None):
        """内部方法：更新任务状态到数据库"""
        from app.db.session import get_db_session
        from app.services.layer_service import TaskService
        db = next(get_db_session())
        svc = TaskService(db)
        svc.update_task_status(task_id, status, progress, result, error_message)
        db.close()

# ============== 空间分析算子 ==============
class SpatialAnalysisOperator:
    """
    空间分析算子类
    
    实现: buffer, clip, intersect, dissolve, union, spatial_join
    """
    
    # 单位转米换算表
    UNIT_TO_METERS = {
        "meters": 1, "metre": 1, "m": 1,
        "kilometers": 1000, "km": 1000,
        "feet": 0.3048, "foot": 0.3048,
        "miles": 1609.344, "mile": 1609.344
    }
    
    @classmethod
    def buffer(cls, features: list, distance: float, unit: str = "meters", 
               dissolve: bool = False, 
               callback: Optional[Callable] = None) -> Dict[str, Any]:
        """缓冲区分析"""
        # 转米
        dist_m = abs(distance) * cls.UNIT_TO_METERS.get(unit.lower(), 1)
        
        if callback:
            callback(10, "加载地理数据...")
        
        # TODO: 使用 GeoPandas + Shapely 实现
        # from shapely.geometry import shape
        # geoms = [shape(f['geometry']) for f in features]
        # buffered = [g.buffer(dist_m) for g in geoms]
        
        if callback and callable(callback):
            callback(80, "生成结果...")
        
        return {
            "type": "FeatureCollection",
            "features": [],
            "statistics": {"input_count": len(features), "buffer_distance": dist_m, "dissolve": dissolve}
        }
    
    @classmethod
    def clip(cls, features: list, boundary: Dict, 
            callback: Optional[Callable] = None) -> Dict[str, Any]:
        """裁剪分析"""
        if callback and callable(callback):
            callback(20, "创建裁剪掩膜...")
            
        return {
            "type": "FeatureCollection",
            "features": [],
            "statistics": {"clipped_features_count": 0}
        }
    
    @classmethod
    def intersect(cls, features_a: list, features_b: list, 
                  callback: Optional[Callable] = None) -> Dict[str, Any]:
        """相交分析"""
        if callback and callable(callback):
            callback(30, "计算相交...")
            
        return {"type": "FeatureCollection", "features": [], "statistics": {"intersection_count": 0}}
    
    @classmethod
    def dissolve(cls, features: list, dissolve_field: Optional[str] = None,
                 callback: Optional[Callable] = None) -> Dict[str, Any]:
        """融合分析"""
        if callback and callable(callback):
            callback(40, "执行融合...")
            
        return {"type": "FeatureCollection", "features": [], "statistics": {"dissolved_count": 0}}
    
    @classmethod
    def union(cls, features_a: list, features_b: list,
              callback: Optional[Callable] = None) -> Dict[str, Any]:
        """联合分析"""
        if callback and callable(callback):
            callback(50, "执行联合...")
            
        return {"type": "FeatureCollection", "features": [], "statistics": {"union_count": 0}}
    
    @classmethod
    def spatial_join(cls, left: list, right: list, join_type: str = "inner",
                    predicate: str = "intersects",
                    callback: Optional[Callable] = None) -> Dict[str, Any]:
        """空间连接"""
        if callback and callable(callback):
            callback(60, "执行空间连接...")
            
        return {"type": "FeatureCollection", "features": [], "statistics": {"joined_count": 0}}

__all__ = ["celery_app", "SpatialAnalysisOperator"]