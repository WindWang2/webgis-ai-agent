"""数据源适配器抽象基类"""
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field
from app.services.explorer.models import (
    DataSourceQualityScore,
    RawContent,
    StructuredData,
    FieldInfo,
    SearchContext,
)


class DataSource(BaseModel):
    """数据源描述"""
    id: str
    name: str
    description: str = ""
    url: str = ""
    format: str = ""  # csv, xlsx, json, etc.
    published_at: Optional[datetime] = None
    spatial_bbox: Optional[str] = None
    estimated_rows: int = 0
    metadata: dict = Field(default_factory=dict)


class BaseDataAdapter(ABC):
    """数据源适配器抽象基类"""

    name: str = "base"
    supported_query_types: list[str] = []

    @abstractmethod
    async def discover(self, query: str, context: SearchContext) -> list[DataSource]:
        """发现匹配的数据源"""

    @abstractmethod
    async def quick_assess(self, query: str, source: DataSource) -> DataSourceQualityScore:
        """快速质量预评估（不下载完整数据）"""

    @abstractmethod
    async def fetch(self, source: DataSource) -> RawContent:
        """下载原始内容"""

    @abstractmethod
    async def parse(self, raw: RawContent) -> StructuredData:
        """解析为结构化数据"""

    async def get_field_schema(self, raw: RawContent) -> list[FieldInfo]:
        """获取字段结构（可选实现）"""
        return []
