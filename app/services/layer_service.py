"""图层服务与任务管理"""
from typing import Optional, List, Tuple, Dict, Any
from sqlalchemy.orm import Session
from sqlalchemy import and_, or_
from datetime import datetime
from app.models.db_model import Layer, AnalysisTask
from app.models.pydantic_model import LayerCreate, LayerUpdate, TaskCreate, TaskResponse


class LayerService:
    """图层服务"""

    def __init__(self, db: Session):
        self.db = db

    def create(self, layer_data: LayerCreate, creator_id: int) -> Layer:
        """创建图层"""
        layer = Layer(
            name=layer_data.name,
            layer_type=layer_data.layer_type,
            source=layer_data.source,
            style=layer_data.style,
            bounds=layer_data.bounds,
            creator_id=creator_id,
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
        if layer_data.style is not None:
            layer.style = layer_data.style
        if layer_data.bounds is not None:
            layer.bounds = layer_data.bounds
        layer.updated_at = datetime.utcnow()
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

    def create(self, task_data: TaskCreate, creator_id: int) -> AnalysisTask:
        """创建任务"""
        task = AnalysisTask(
            task_type=task_data.task_type,
            params=task_data.params,
            status="pending",
            creator_id=creator_id,
            progress=0,
            retry_count=0,
            max_retries=3
        )
        self.db.add(task)
        self.db.commit()
        self.db.refresh(task)
        return task

    def get_task(self, task_id: str) -> Optional[AnalysisTask]:
        """获取任务"""
        return self.db.query(AnalysisTask).filter(
            AnalysisTask.task_id == task_id
        ).first()

        """获取任务（通过UUID）"""
        return self.db.query(AnalysisTask).filter(
            AnalysisTask.task_id == task_id
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