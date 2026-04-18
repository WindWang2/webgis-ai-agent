"""
自然资源监测工具包 - V3.0 Phase 1
提供 NDVI 植被分析及资产管理功能
"""
import logging
from typing import Optional, Any
from pydantic import BaseModel, Field
from app.tools.registry import ToolRegistry, tool
from app.services.spatial_tasks import run_ndvi_analysis

logger = logging.getLogger(__name__)

class NDVIArgs(BaseModel):
    raster_path: str = Field(..., description="遥感影像文件路径 (可从之前上传或分析结果中获取)")
    nir_band: Optional[int] = Field(None, description="近红外波段索引 (1-based)。若不指定将尝试自动探测。")
    red_band: Optional[int] = Field(None, description="红光波段索引 (1-based)。若不指定将尝试自动探测。")
    session_id: Optional[str] = Field(None, description="会话 ID")

class AssetManageArgs(BaseModel):
    asset_id: int = Field(..., description="分析资产记录 ID")
    action: str = Field(..., description="维护动作：'rename' (重命名) 或 'delete' (删除)")
    new_name: Optional[str] = Field(None, description="当动作为 rename 时必填")

def register_nature_resource_tools(registry: ToolRegistry):
    """注册自然资源监测相关工具"""

    @tool(registry, name="analyze_vegetation_index",
          description="计算影像的归一化植被指数 (NDVI)。该工具能自动识别 4 波段 RGBN 影像或 Sentinel-2 类型数据。计算结果会持久化到资产库并生成预览图。")
    def analyze_vegetation_index(raster_path: str, nir_band: Optional[int] = None, red_band: Optional[int] = None, session_id: Optional[str] = None) -> dict:
        # 触发 Celery 异步任务
        task = run_ndvi_analysis.delay(raster_path, nir_band, red_band, session_id)
        return {
            "status": "analysis_task_started",
            "task_id": task.id,
            "message": "植被指数 (NDVI) 分析任务已启动。这是后台异步计算，完成后结果会自动推送到地图并进入你的资产库。"
        }

    @tool(registry, name="list_analysis_assets",
          description="获取当前系统中保存的所有遥感分析产物（如 NDVI、NDWI 结果文件）列表。用于回答用户“我之前生成了什么”或进行资产回顾。")
    def list_analysis_assets(session_id: Optional[str] = None) -> dict:
        from app.core.database import SessionLocal
        from app.models.upload import UploadRecord
        
        db = SessionLocal()
        try:
            query = db.query(UploadRecord).filter(UploadRecord.geometry_type == "raster_analysis")
            if session_id:
                query = query.filter(UploadRecord.session_id == session_id)
            
            records = query.order_by(UploadRecord.upload_time.desc()).all()
            assets = [{
                "id": r.id,
                "name": r.original_name,
                "path": r.filename,
                "time": r.upload_time.isoformat(),
                "bbox": r.bbox
            } for r in records]
            
            return {
                "success": True,
                "assets": assets,
                "count": len(assets),
                "system_message": "这是目前的分析资产列表。你可以直接告诉用户这些成果，或建议将其加载到地图上。"
            }
        finally:
            db.close()

    @tool(registry, name="manage_analysis_asset",
          description="对已有的分析资产进行重命名或永久删除操作。")
    def manage_analysis_asset(asset_id: int, action: str, new_name: Optional[str] = None) -> dict:
        from app.core.database import SessionLocal
        from app.models.upload import UploadRecord
        import os
        from app.core.config import settings
        
        db = SessionLocal()
        try:
            record = db.query(UploadRecord).filter(UploadRecord.id == asset_id).first()
            if not record:
                return {"error": "未找到对应的分析资产记录"}
            
            if action == "rename" and new_name:
                old_name = record.original_name
                record.original_name = new_name
                db.commit()
                return {"success": True, "message": f"资产已从「{old_name}」重命名为「{new_name}」"}
            
            elif action == "delete":
                # 删除物理文件
                full_path = os.path.join(settings.DATA_DIR, record.filename)
                if os.path.exists(full_path):
                    os.remove(full_path)
                
                name = record.original_name
                db.delete(record)
                db.commit()
                return {"success": True, "message": f"资产「{name}」及物理文件已永久删除"}
            
            return {"error": f"不支持的动作或缺少必要参数: {action}"}
        finally:
            db.close()
