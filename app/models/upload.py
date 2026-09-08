"""用户上传数据模型"""
from datetime import datetime, timezone
from sqlalchemy import Column, Integer, BigInteger, String, DateTime, JSON, CheckConstraint, Index
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
    # V4 内容身份（审计 03 R2）：上传字节 sha256 —— 同会话幂等再导入的去重键。
    # 可空：历史行与 dedup 关闭的上传没有指纹（不虚构）。探测索引见下。
    content_sha256 = Column(String(64), nullable=True)
    upload_time = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    session_id = Column(String(255), nullable=True)       # 关联的会话 ID

    __table_args__ = (
        CheckConstraint("file_type IN ('vector', 'raster')", name="ck_upload_file_type"),
        CheckConstraint("format IN ('geojson', 'shapefile', 'geotiff', 'csv', 'gpkg', 'kml')", name="ck_upload_format"),
        # #429：list_uploads 热路径是 WHERE session_id = ? ORDER BY upload_time
        # DESC + COUNT —— 无索引时每次面板打开都是全表扫描 + 排序，代价随全局
        # uploads 行数（而非单会话行数）线性增长。复合索引让按会话过滤 + 排序
        # 都走索引扫描。迁移 0015 为存量库补建同名索引。
        Index("ix_uploads_session_time", "session_id", "upload_time"),
        # V4 dedup 探测：WHERE session_id = ? AND content_sha256 = ?（非唯一：
        # 历史行可为 NULL，且跨会话不共享 —— 内容身份是会话内幂等，不是全局键）。
        Index("ix_uploads_session_content", "session_id", "content_sha256"),
    )


__all__ = ["UploadRecord"]
