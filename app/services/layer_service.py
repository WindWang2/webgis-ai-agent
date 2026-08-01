"""图层服务与任务管理"""
from typing import Optional, List, Tuple, Dict, Any
from sqlalchemy.orm import Session
from sqlalchemy import and_, or_
from datetime import datetime, timezone
from app.models.db_model import Layer
from app.models.pydantic_models import LayerCreate, LayerUpdate


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


# TaskService 已删除——0 生产调用方。原 AnalysisTask DB 路径（TaskCreate →
# AnalysisTask 行 → Celery dispatch → 状态轮询）端到端孤立：agent 工具直接调
# SpatialAnalyzer（ADR-0013），3 个活 Celery 任务（run_heatmap_generation /
# run_ndvi_analysis / run_change_detection）改用 UploadRecord 落库。
# AnalysisTask 表模型保留——drop table 是带数据风险的迁移，不在本清理范围。