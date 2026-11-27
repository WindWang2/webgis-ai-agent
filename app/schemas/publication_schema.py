"""发布 API 请求模型（04 线·交互式只读地图发布）。"""
from typing import Dict, List, Optional

from pydantic import BaseModel, Field


class PublicationPreviewRequest(BaseModel):
    """发布预览（纯规划；无公开副作用）。"""

    session_id: str = Field(min_length=8, max_length=128)
    selected_layer_ids: Optional[List[str]] = None
    field_whitelists: Optional[Dict[str, List[str]]] = None


class PublicationCreateRequest(PublicationPreviewRequest):
    """创建发布草稿（private；无链接签发）。"""

    title: str = Field(default="", max_length=300)
    purpose: str = Field(default="", max_length=600)
    attribution: str = Field(default="", max_length=300)
    license: str = Field(default="", max_length=120)
    missing_values_note: str = Field(default="", max_length=300)
    alt_text: str = Field(default="", max_length=400)
    palette: str = Field(default="", max_length=60)
    base_publication_id: Optional[str] = Field(default=None, max_length=64)


class PublicationPublishRequest(BaseModel):
    """发布（显式确认；confirm=false → typed 400）。"""

    confirm: bool = False


class PublicationLinkRequest(BaseModel):
    """签发可撤销只读链接。"""

    ttl_days: Optional[int] = Field(default=None, ge=1, le=30)


__all__ = [
    "PublicationPreviewRequest",
    "PublicationCreateRequest",
    "PublicationPublishRequest",
    "PublicationLinkRequest",
]
