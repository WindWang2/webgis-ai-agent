"""上传数据管理工具 - 供 AI Agent 调用"""
import json
import logging
from pathlib import Path
from typing import Optional

from app.tools.registry import ToolRegistry, tool
from app.core.config import settings
from app.core.database import SessionLocal
from app.models.upload import UploadRecord

logger = logging.getLogger(__name__)


def register_upload_tools(registry: ToolRegistry):
    """注册上传数据相关工具"""

    @tool(registry,
          name="list_uploaded_data",
          description="列出当前会话中用户上传的 GIS 数据文件列表。返回文件名、类型、格式、要素数量等摘要信息。")
    def list_uploaded_data(session_id: Optional[str] = None) -> dict:
        """列出上传数据"""
        db = SessionLocal()
        try:
            query = db.query(UploadRecord).order_by(UploadRecord.upload_time.desc())
            if session_id:
                query = query.filter(UploadRecord.session_id == session_id)
            records = query.limit(50).all()
        finally:
            db.close()

        if not records:
            return {
                "success": True,
                "uploads": [],
                "count": 0,
                "message": "当前没有上传的数据文件。您可以在左侧面板上传 GeoJSON、Shapefile、GeoTIFF、CSV 等格式的 GIS 数据。"
            }

        items = []
        for r in records:
            item = {
                "id": r.id,
                "original_name": r.original_name,
                "file_type": r.file_type,
                "format": r.format,
                "crs": r.crs,
                "geometry_type": r.geometry_type,
                "feature_count": r.feature_count,
                "bbox": r.bbox,
                "file_size_mb": round(r.file_size / 1024 / 1024, 2),
            }
            items.append(item)

        return {
            "success": True,
            "uploads": items,
            "count": len(items),
        }

    @tool(registry,
          name="get_upload_info",
          description="获取某个上传数据文件的详细信息，包括坐标范围、属性字段等。可用于分析用户上传的数据概况。",
          param_descriptions={
              "upload_id": "上传记录的 ID（从 list_uploaded_data 获取）"
          })
    def get_upload_info(upload_id: int, session_id: Optional[str] = None) -> dict:
        """获取上传数据详情"""
        db = SessionLocal()
        try:
            record = db.query(UploadRecord).filter(UploadRecord.id == upload_id).first()
        finally:
            db.close()

        if not record:
            return {"error": f"未找到 ID 为 {upload_id} 的上传记录"}

        info = {
            "id": record.id,
            "original_name": record.original_name,
            "file_type": record.file_type,
            "format": record.format,
            "crs": record.crs,
            "geometry_type": record.geometry_type,
            "feature_count": record.feature_count,
            "bbox": record.bbox,
            "file_size_mb": round(record.file_size / 1024 / 1024, 2),
            "upload_time": record.upload_time.isoformat() if record.upload_time else None,
        }

        # 矢量数据：读取属性字段和 meta.json
        if record.file_type == "vector":
            geojson_path = Path(record.filename)
            meta_path = geojson_path.parent / "meta.json"

            if meta_path.exists():
                with open(meta_path, "r", encoding="utf-8") as f:
                    meta = json.load(f)
                info["attributes"] = meta.get("attributes", [])

            # 读取 GeoJSON 前几条特征的属性作为示例
            if geojson_path.exists():
                try:
                    with open(geojson_path, "r", encoding="utf-8") as f:
                        geojson = json.load(f)
                    features = geojson.get("features", [])
                    if features:
                        info["sample_properties"] = [
                            f.get("properties", {}) for f in features[:3]
                        ]
                except Exception as e:
                    logger.warning(f"读取 GeoJSON 示例失败: {e}")

        # 栅格数据：读取 meta.json
        elif record.file_type == "raster":
            meta_path = Path(record.filename).parent / "meta.json"
            if meta_path.exists():
                with open(meta_path, "r", encoding="utf-8") as f:
                    meta = json.load(f)
                info["band_count"] = meta.get("band_count")
                info["width"] = meta.get("width")
                info["height"] = meta.get("height")
                info["dtype"] = meta.get("dtype")

        return {"success": True, **info}
