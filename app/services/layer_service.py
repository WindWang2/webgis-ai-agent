"""图层服务与任务管理"""
from typing import Optional, List, Tuple, Dict, Any
from sqlalchemy.orm import Session
from sqlalchemy import and_, or_
from datetime import datetime, timezone
from app.models.db_model import Layer, AnalysisTask
from app.models.pydantic_models import LayerCreate, LayerUpdate, TaskCreate, TaskResponse


class LayerService:
    """图层服务"""

    def __init__(self, db: Session):
        self.db = db

    def create(self, layer_data: LayerCreate, creator_id: str) -> Layer:
        """创建图层"""
        layer = Layer(
            name=layer_data.name,
            layer_type=layer_data.layer_type,
            source_url=layer_data.source_url,
            bounds=layer_data.extent,
            creator_id=creator_id,
            org_id=1,  # Default org ID placeholder
            visibility="public" if layer_data.is_public else "private",
            status="active"
        )
        self.db.add(layer)
        self.db.commit()
        self.db.refresh(layer)
        return layer

    def list_all(self, limit: int = 100, offset: int = 0, search: str = None, layer_type: str = None, is_public: bool = None):
        """列出所有图层"""
        query = self.db.query(Layer).filter(Layer.status == "active")
        if search:
            query = query.filter(Layer.name.ilike(f"%{search}%"))
        if layer_type:
            query = query.filter(Layer.layer_type == layer_type)
        if is_public is not None:
            query = query.filter(Layer.is_public == is_public)
        total = query.count()
        layers = query.order_by(Layer.created_at.desc()).limit(limit).offset(offset).all()
        return layers, total

    def get_by_id(self, layer_id: int) -> Optional[Layer]:
        """获取图层"""
        return self.db.query(Layer).filter(Layer.id == layer_id).first()

    def update(self, layer_id: int, layer_data: LayerUpdate) -> Optional[Layer]:
        """更新图层"""
        layer = self.get_by_id(layer_id)
        if not layer:
            return None
        if layer_data.name is not None:
            layer.name = layer_data.name
        if layer_data.source_url is not None:
            layer.source_url = layer_data.source_url
        if layer_data.is_public is not None:
            layer.visibility = "public" if layer_data.is_public else "private"
        layer.updated_at = datetime.now(timezone.utc)
        self.db.commit()
        self.db.refresh(layer)
        return layer

    def delete(self, layer_id: int) -> bool:
        """删除图层（改为设置为inactive）"""
        layer = self.get_by_id(layer_id)
        if not layer:
            return False
        layer.status = "inactive"
        self.db.commit()
        return True


class TaskService:
    """任务服务"""

    def __init__(self, db: Session):
        self.db = db

    def create(self, task_data: TaskCreate, creator_id: str) -> AnalysisTask:
        """创建任务"""
        task = AnalysisTask(
            task_type=task_data.task_type,
            parameters=task_data.parameters,
            status="pending",
            creator_id=creator_id,
            org_id=1,  # Default org ID
            progress=0,
            retry_count=0,
            max_retries=3
        )
        self.db.add(task)
        self.db.commit()
        self.db.refresh(task)
        return task

    def get_task(self, task_id: str) -> Optional[AnalysisTask]:
        """获取任务(按celery_task_id)"""
        return self.db.query(AnalysisTask).filter(
            AnalysisTask.celery_task_id == task_id
        ).first()

    def get_task_by_id(self, task_id: int) -> Optional[AnalysisTask]:
        """获取任务（通过自增ID）"""
        return self.db.query(AnalysisTask).filter(
            AnalysisTask.id == task_id
        ).first()

    

    def update_task_status(
        self,
        task_id: str,
        status: str,
        progress: Optional[int] = None,
        result: Optional[Dict[str, Any]] = None,
        error_message: Optional[str] = None
    ) -> Optional[AnalysisTask]:
        """更新任务状态"""
        task = self.get_task(task_id)
        if not task:
            return None
        task.status = status
        if progress is not None:
            task.progress = progress
        if result is not None:
            task.result = result
        if error_message is not None:
            task.error_message = error_message
        self.db.commit()
        self.db.refresh(task)
        return task