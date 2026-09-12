"""探索引擎子系统契约模型（V9 契约基石，ADR-0138）。

从 `app/api/routes/explorer.py` 内联迁出。
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class StartExploreRequest(BaseModel):
    """POST /explorer/start 请求体。"""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "query": "杭州西湖周边的 POI",
                    "session_id": "sess-123",
                    "expected_data_type": "poi_list",
                    "source_hint": ["osm"],
                    "auto_threshold": 0.7,
                }
            ]
        }
    )

    query: str = Field(..., min_length=1, max_length=500)
    session_id: Optional[str] = None
    expected_data_type: str = "poi_list"
    source_hint: list[str] = Field(default_factory=list)
    auto_threshold: float = 0.7


class StartExploreResponse(BaseModel):
    """POST /explorer/start 返回。"""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [{"task_id": "exp-abc123", "status": "started"}]
        }
    )

    task_id: str
    status: str


class ExploreStatusResponse(BaseModel):
    """GET /explorer/status/{task_id} 返回。"""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "task_id": "exp-abc123",
                    "status": "running",
                    "progress": 40,
                    "result": None,
                }
            ]
        }
    )

    task_id: str
    status: str
    progress: int = 0
    result: Optional[dict] = None


class ExploreAbortResponse(BaseModel):
    """POST /explorer/abort/{task_id} 返回。"""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [{"task_id": "exp-abc123", "aborted": True}]
        }
    )

    task_id: str
    aborted: bool
