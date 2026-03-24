"""
Celery 任务队列服务
完整配置：Redis 作为 Broker 和 Result Backend
"""
import os
import logging
from celery import Celery
from celery.schedules import crontab
from typing import Dict, Any, Optional, Callable
from celery.signals import task_revoked, task_rejected
from celery.exceptions import SoftTimeLimitExceeded, TimeLimitExceeded

logger = logging.getLogger(__name__)

# 从配置获取 Redis URL
REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
REDIS_RESULT_URL = os.environ.get("REDIS_RESULT_URL", "redis://localhost:6379/1")

# 创建 Celery 应用
celery_app = Celery(
    "webgis_ai",
    broker=REDIS_URL,
    backend=REDIS_RESULT_URL,
    include=[
        "app.tasks.analysis",
        "app.tasks.base"
    ]
)

# Celery 详细配置
celery_app.conf.update(
    # 序列化配置
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    
    # 时区配置
    timezone="Asia/Shanghai",
    enable_utc=True,
    
    # 任务追踪
    task_track_started=True,
    task_send_sent_event=True,
    
    # 超时配置
    task_soft_time_limit=3600,      # 软超时 1 小时
    task_time_limit=4200,          # 硬超时 70 分钟
    task_acks_late=True,           # 延迟确认
    task_reject_on_worker_lost=True,  # Worker 丢失时拒绝
    
    # Worker 配置
    worker_prefetch_multiplier=1,
    worker_max_tasks_per_child=1000,
    worker_disable_rate_limits=True,
    
    # 结果配置
    result_expires=86400,           # 结果保存 24 小时
    result_extended=True,
    result_persistent=True,
    
    # 队列配置
    task_default_queue="default",
    task_queues={
        "default": {},
        "high_priority": {},
        "spatial_analysis": {},
        "raster_processing": {},
    },
    task_routes={
        "app.tasks.analysis.*": {"queue": "spatial_analysis"},
    },
    
    # Beat 定时任务
    beat_schedule={
        "cleanup-expired-tasks": {
            "task": "app.tasks.base.cleanup_expired_results",
            "schedule": crontab(hour=3, minute=0),
        },
    },
)


class TaskCallbackMixin:
    """
    任务回调混入类
    
    提供进度上报、失败重试、超时终止等通用逻辑
    """
    
    @staticmethod
    def setup_task_context(task_id: str, **kwargs):
        """设置任务上下文"""
        from flask import g
        g.current_task_id = task_id
        for key, value in kwargs.items():
            setattr(g, key, value)
    
    def on_progress(self, progress: int, message: str = ""):
        """
        上报进度
        
        Args:
            progress: 进度百分比 0-100
            message: 进度消息
        """
        self.update_state(
            state="PROGRESS",
            meta={
                "progress": min(progress, 100),
                "message": message,
                "percent": progress / 100.0
            }
        )
    
    def on_retry(self, exc, task_id, args, kwargs, einfo):
        """重试回调"""
        logger.warning(f"任务 {task_id} 开始重试，异常: {exc}")
        
        # 更新数据库状态
        from app.db.session import get_db_session
        from app.services.layer_service import TaskService
        db = next(get_db_session())
        task_service = TaskService(db)
        task_service.increment_retry(task_id)
        task_service.update__task_status(
            task_id,
            status="retrying",
            progress=0,
            error_message=f"任务重试中: {str(exc)}"
        )
        db.close()
    
    def on_timeout(self, task_id: str):
        """超时终止回调"""
        logger.error(f"任务 {task_id} 超时终止")
        
        from app.db.session import get_db_session
        from app.services.layer_service import TaskService
        db = next(get_db_session())
        task_service = TaskService(db)
        task_service.update__task_status(
            task_id,
            status="timeout",
            error_message="任务执行超过时限"
        )
        db.close()


class SpatialAnalysisOperator:
    """
    空间分析算子类
    
    实现各类空间分析算法
    """
    
    @staticmethod
    def buffer(
        features: list,
        distance: float,
        unit: str = "meters",
        dissolve: bool = False,
        callback: Optional[Callable] = None
    ) -> Dict[str, Any]:
        """
        缓冲区分析
        
        Args:
            features: 输入要素列表
            distance: 缓冲距离
            unit: 距离单位 (meters/kilometers/feet/miles)
            dissolve: 是否融合结果
            callback: 进度回调函数
        
        Returns:
            分析结果字典
        """
        # 单位转换为米
        conversion = {"meters": 1, "kilometers": 1000, "feet": 0.3048, "miles": 1609.34}
        distance_m = distance * conversion.get(unit, 1)
        
        if callback:
            callback(10, "加载地理数据...")
        
        # TODO: 使用 GeoPandas + Shapely 实现真正的缓冲区分析
        # import geopandas as gpd
        # from shapely.geometry import shape
        
        # 1. 解析输入要素为 Geometry 对象
        # geometries = [shape(f['geometry']) for f in features]
        
        # 2. 对每个几何对象创建缓冲区
        # buffered = [g.buffer(distance_m) for g in geometries]
        
        # 3. 如果需要融合
        # if dissolve:
        #     buffered = [g.union_all(buffered)]
        
        if callback:
            callback(80, "生成缓冲区结果...")
        
        # 模拟返回结果
        result_features = [
            {
                "type": "Feature",
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]]
                },
                "properties": {"distance": distance_m}
            }
        ]
        
        if callback:
            callback(100, "缓冲区分析完成")
        
        return {
            "type": "FeatureCollection",
            "features": result_feature,
            "statistics": {
                "input_count": len(features),
                "output_count": len(result_feature),
                "buffer_distance": distance_m
            }
        }
    
    @staticmethod
    def clip(
        features: list,
        clip_boundary: Dict,
        callback: Optional[Callable] = None
    ) -> Dict[str, Any]:
        """
        裁剪分析
        
        Args:
            features: 被裁剪要素
            clip_boundary: 裁剪边界几何
            callback: 进度回调函数
        
        Returns:
            裁剪结果
        """
        if callback:
            callback(20, "创建裁剪掩膜...")
        
        # TODO: 使用 Rasterio/GeoPandas 实现真正的裁剪
        # import rasterio
        # from rasterio.mask import mask
        
        if callback:
            callback(90, "提取裁剪结果...")
        
        return {
            "type": "FeatureCollection",
            "features": [],
            "statistics": {"clipped_count": 0}
        }
    
    @staticmethod
    def intersect(
        features_a: list,
        features_b: list,
        callback: Optional[Callable] = None
    ) -> Dict[str, Any]:
        """
        相交分析
        
        计算两组几何对象的交集
        """
        if callback:
            callback(30, "计算相交...")
        
        # TODO: 实现真正的相交分析
        return {
            "type": "FeatureCollection",
            "features": []
        }
    
    @staticmethod
    def dissolve(
        features: list,
        dissolve_field: Optional[str] = None,
        callback: Optional[Callable] = None
    ) -> Dict[str, Any]:
        """
        融合分析
        
        按字段融合相同属性的几何对象
        """
        if callback:
            callback(40, "执行要素融合...")
        
        return {
            "type": "FeatureCollection",
            "features": []
        }
    
    @staticmethod
    def union(
        features_a: list,
        features_b: list,
        callback: Optional[Callable] = None
    ) -> Dict[str, Any]:
        """
        联合分析
        
        合并两组几何对象
        """
        if callback:
            callback(50, "执行联合运算...")
        
        return {
            "type": "FeatureCollection",
            "features": []
        }
    
    @staticmethod
    def spatial_join(
        features_left: list,
        features_right: list,
        join_type: str = "inner",
        predicate: str = "intersects",
        callback: Optional[Callable] = None
    ) -> Dict[str, Any]:
        """
        空间连接
        
        基于空间关系连接两个数据集
        """
        if callback:
            callback(60, "执行空间连接...")
        
        return {
            "type": "FeatureCollection",
            "features": []
        }


# 导出全局_celery_app实例
__all__ = ["celery_app", "SpatialAnalysisOperator"]