"""Workflow resume 契约模型（V9 契约基石，ADR-0138）。

对应 `app/api/routes/workflow_resume.py`；返回体形状镜像
`app/services/gis_harness/resume_anchor.py` 的返回 dict。
"""
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict


class ResumeAnchorResponse(BaseModel):
    """POST /chat/sessions/{session_id}/workflow-resume-anchor 返回。"""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "anchor_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
                    "trace_last_seq": 42,
                    "ref_count": 3,
                }
            ]
        }
    )

    anchor_id: str
    trace_last_seq: int
    ref_count: int


class WorkflowResumeResponse(BaseModel):
    """POST /chat/workflow-resume/{anchor_id} 返回（恢复产物 + 验证披露）。"""

    model_config = ConfigDict(
        extra="allow",
        json_schema_extra={
            "examples": [
                {
                    "session_id": "resume-3fa85f64-1b2c3d4e",
                    "source_session_id": "sess-old",
                    "owner_token": None,
                    "user_goal": "继续中断的工作流",
                    "restored_chapter_keys": ["gis_chapter"],
                    "restored_refs": ["ref:abc"],
                    "ref_map": {"ref:abc": "ref:xyz"},
                    "missing_refs": [],
                    "trace_last_seq": 42,
                    "ref_verdicts": {},
                    "workflow_verify": {},
                    "mapspec_verify": {},
                    "stale_nodes": [],
                    "recompute_plan": {},
                    "verify_disclosures": [],
                    "recovery_budget_carried": 0,
                }
            ]
        },
    )

    session_id: str
    source_session_id: str
    owner_token: Optional[str] = None
    user_goal: Optional[str] = None
    restored_chapter_keys: list[str] = []
    restored_refs: list[str] = []
    ref_map: dict[str, str] = {}
    missing_refs: list[str] = []
    trace_last_seq: int = 0
    ref_verdicts: dict[str, Any] = {}
    workflow_verify: dict[str, Any] = {}
    mapspec_verify: dict[str, Any] = {}
    stale_nodes: list[Any] = []
    recompute_plan: dict[str, Any] = {}
    verify_disclosures: list[Any] = []
    recovery_budget_carried: int = 0
