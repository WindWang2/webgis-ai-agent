"""知识库（RAG）子系统契约模型（V9 契约基石，ADR-0138）。

从 `app/api/routes/knowledge.py` 内联迁出；端点响应统一走 ApiResponse 信封。
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

# 应用级 content 上限，与 nginx client_max_body_size 100M
# (deploy/nginx/nginx.conf) 对齐。分块/嵌入对 content 做 O(content) 的
# CPU/IO 工作，无界请求体可被单用户放大，拖垮 RAG 入库管线 (#591)。
MAX_CONTENT_LENGTH = 100 * 1024 * 1024


class AddDocumentRequest(BaseModel):
    """POST /knowledge/documents 请求体。"""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "title": "watershed-notes",
                    "content": "流域治理要点……",
                    "file_type": "text",
                }
            ]
        }
    )

    title: str
    content: str = Field(..., max_length=MAX_CONTENT_LENGTH)
    file_type: str = "text"  # text/markdown/json


class SearchRequest(BaseModel):
    """POST /knowledge/search 请求体。"""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [{"query": "流域治理", "top_k": 5, "document_id": None}]
        }
    )

    query: str
    top_k: int = 5
    document_id: Optional[str] = None


class DeleteRequest(BaseModel):
    """DELETE 请求体（按 document_id）。"""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [{"document_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6"}]
        }
    )

    document_id: str
