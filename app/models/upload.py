"""用户上传数据模型"""
from datetime import datetime, timezone
from sqlalchemy import Column, Integer, BigInteger, String, Float, DateTime, JSON
from app.core.database import Base


class UploadRecord(Base):
    """用户上传文件记录"""
    __tablename__ = "uploads"

    id = Column(Integer, primary_key=True, autoincrement=True)
    filename = Column(String(255), nullable=False)       # 存储文件名 (upload_id/original.geojson)
    original_name = Column(String(255), nullable=False)   # 用户上传的原始文件名
    file_type = Column(String(20), nullable=False)        # vector / raster
    format = Column(String(20), nullable=False)           # geojson / shapefile / geotiff / csv / gpkg / kml
    crs = Column(String(100), default="EPSG:4326")
    geometry_type = Column(String(50))                    # Point / LineString / Polygon / MultiPolygon / raster
    feature_count = Column(BigInteger, default=0)
    bbox = Column(JSON)                                   # [west, south, east, north]
    file_size = Column(BigInteger, nullable=False)        # 字节
    upload_time = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    session_id = Column(String(255), nullable=True)       # 关联的会话 ID


__all__ = ["UploadRecord"]
