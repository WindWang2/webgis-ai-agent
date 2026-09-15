"""StoryMap API 请求 schema（ADR-0196 §5.2）。"""
from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, model_validator


class StoryCompileRequest(BaseModel):
    """POST /storymap/compile 请求体。

    `session_id` 为预留的会话溯源字段（无状态编译不校验所有权；会话路径
    走 /storymap/sessions/{id}/compile 挂 require_owned_session）。
    """

    session_id: str = ""
    turn_id: str = ""
    trace: Optional[Dict[str, Any]] = None
    messages: Optional[List[Dict[str, Any]]] = None
    title: Optional[str] = None

    @model_validator(mode="after")
    def _require_source(self) -> "StoryCompileRequest":
        if not self.trace and not self.messages:
            raise ValueError("trace or messages required")
        return self


class StoryExportRequest(BaseModel):
    """POST /storymap/export 请求体：spec 由调用方编译后直供。"""

    spec: Dict[str, Any]
    layers: List[Dict[str, Any]] = Field(default_factory=list)
    mapspec: Optional[Dict[str, Any]] = None
    format: Literal["json", "html"] = "json"
