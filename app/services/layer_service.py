"""
图层服务层 - 实现图层的 CRUD 操作
"""

from typing import List, Optional, Dict, Any
from datetime import datetime
from sqlalchemy.orm import Session
from sqlalchemy import and_, or_

from app.models.db_models import Layer, AnalysisTask
from app.models.pydantic_models import LayerCreate, LayerUpdate


class LayerService:
    """图层服务类"""
    
    def __init__(self, db: Session):
        self.db = db
    
    def create(self, layer_data: LayerCreate, owner_id: Optional[int] = None) -> Layer:
        """创建新图层"""
        layer = Layer(
            name=layer_data.name,
            description=layer_data.description,
            layer_type=layer_data.layer_type,
            geometry_type=layer_data.geometry_type,
            source_url=layer_data.source_url,
            source_format=layer_data.source_format,
            crs=layer_data.crs,
            extent=layer_data.extent,
            attributes=layer_data.attributes,
            owner_id=owner_id,
            is_public=layer_data.is_public,
            permission=layer_data.permission,
            is_active=True
        )
        self.db.add(layer)
        self.db.commit()
        self.db.refresh(layer)
        return layer
    
    def get(self, layer_id: int) -> Optional[Layer]:
        """获取单个图层"""
        return self.db.query(Layer).filter(Layer.id == layer_id).first()
    
    def get_by_owner(self, owner_id: int, limit: int = 100, offset: int = 0) -> List[Layer]:
        """获取用户拥有的图层"""
        return self.db.query(Layer).filter(
            Layer.owner_id == owner_id,
            Layer.is_active == True
        ).offset(offset).limit(limit).all()
    
    def list_all(
        self, 
        limit: int = 100, 
        offset: int = 0,
        layer_type: Optional[str] = None,
        is_public: Optional[bool] = None,
        search: Optional[str] = None
    ) -> tuple[List[Layer], int]:
        """
        获取图层列表
        
        Returns:
            (图层列表，总数)
        """
        query = self.db.query(Layer).filter(Layer.is_active == True)
        
        if layer_type:
            query = query.filter(Layer.layer_type == layer_type)
        
        if is_public is not None:
            query = query.filter(Layer.is_public == is_public)
        
        if search:
            query = query.filter(
                or_(
                    Layer.name.ilike(f"%{search}%"),
                    Layer.description.ilike(f"%{search}%")
                )
            )
        
        total = query.count()
        layers = query.order_by(Layer.created_at.desc()).offset(offset).limit(limit).all()
        
        return layers, total
    
    def update(self, layer_id: int, update_data: LayerUpdate) -> Optional[Layer]:
        """更新图层"""
        layer = self.get(layer_id)
        if not layer:
            return None
        
        update_dict = update_data.model_dump(exclude_unset=True)
        for key, value in update_dict.items():
            setattr(layer, key, value)
        
        layer.updated_at = datetime.utcnow()
        self.db.commit()
        self.db.refresh(layer)
        return layer
    
    def delete(self, layer_id: int) -> bool:
        """软删除图层"""
        layer = self.get(layer_id)
        if not layer:
            return False
        
        layer.is_active = False
        layer.updated_at = datetime.utcnow()
        self.db.commit()
        return True
    
    def check_permission(
        self, 
        layer_id: int, 
        user_id: int, 
        required_permission: str
    ) -> bool:
        """检查用户权限"""
        layer = self.get(layer_id)
        if not layer:
            return False
        
        # 公开图层任何人都可读
        if layer.is_public and required_permission == "read":
            return True
        
        # 所有者有完全权限
        if layer.owner_id == user_id:
            return True
        
        # 检查角色表
        from app.models.db_models import UserRole
        user_role = self.db.query(UserRole).filter(
            UserRole.user_id == user_id,
            UserRole.resource_type == "layer",
            UserRole.resource_id == layer_id
        ).first()
        
        if user_role:
            permission_levels = {"viewer": 1, "editor": 2, "owner": 3}
            required_level = permission_levels.get(required_permission, 0)
            user_level = permission_levels.get(user_role.role, 0)
            return user_level >= required_level
        
        return False


class TaskService:
    """任务服务类"""
    
    def __init__(self, db: Session):
        self.db = db
    
    def create_task(
        self, 
        task_type: str, 
        parameters: Dict[str, Any],
        layer_id: Optional[int] = None
    ) -> AnalysisTask:
        """创建分析任务"""
        import uuid
        task = AnalysisTask(
            task_id=str(uuid.uuid4()),
            layer_id=layer_id,
            task_type=task_type,
            parameters=parameters,
            status="pending",
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
        
        if status == "running" and task.started_at is None:
            task.started_at = datetime.utcnow()
        elif status in ["completed", "failed"] and task.completed_at is None:
            task.completed_at = datetime.utcnow()
        
        self.db.commit()
        self.db.refresh(task)
        return task
    
    def list_tasks(
        self,
        limit: int = 100,
        offset: int = 0,
        status: Optional[str] = None,
        layer_id: Optional[int] = None
    ) -> tuple[List[AnalysisTask], int]:
        """获取任务列表"""
        query = self.db.query(AnalysisTask)
        
        if status:
            query = query.filter(AnalysisTask.status == status)
        
        if layer_id:
            query = query.filter(AnalysisTask.layer_id == layer_id)
        
        total = query.count()
        tasks = query.order_by(AnalysisTask.created_at.desc()).offset(offset).limit(limit).all()
        
        return tasks, total
    
    def increment_retry(self, task_id: str) -> bool:
        """增加重试计数"""
        task = self.get_task(task_id)
        if not task:
            return False
        
        task.retry_count += 1
        self.db.commit()
        return True
