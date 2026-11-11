"""G10 项目数据刷新 · REST 请求契约（服务端严格复核，客户端字段非权威）。

红线（A5）：客户端或模型传来的 ``verified`` / ``approved`` / ``owner_id``
一律不作权威；owner 由 ``get_current_user`` + 项目鉴权给出。所有数组长度、
数值范围、字符串长度、DAG 形状在这里收紧，领域层再做一次版本/revision/
权限漂移复核。
"""
from __future__ import annotations

import math
import re
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.services.project_runtime.refresh.records import (
    AdoptionReceipt,
    CheckReceipt,
    RefreshCandidate,
    RefreshProposal,
)

_NODE_ID_RE = re.compile(r"^[A-Za-z0-9_:.\-]{1,64}$")
MAX_DAG_NODES = 64
MAX_DAG_EDGES = 256


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CheckUpdatesRequest(_Strict):
    session_id: str = Field(..., min_length=1, max_length=128)
    establish_baseline: bool = True


class PreviewRefreshRequest(_Strict):
    session_id: str = Field(..., min_length=1, max_length=128)
    style_only: bool = False
    #: 可选显式分析图（服务端严格复核节点/边数量与形状；缺席时读真实会话分析图）
    dag: Optional[Dict[str, Any]] = None
    max_features: int = Field(default=50_000, ge=1, le=1_000_000)
    max_bytes: int = Field(default=8 * 1024 * 1024, ge=1, le=256 * 1024 * 1024)

    @field_validator("dag")
    @classmethod
    def _bounded_dag(cls, v: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if v is None:
            return None
        nodes, edges = v.get("nodes"), v.get("edges")
        if not isinstance(nodes, list) or not isinstance(edges, list):
            raise ValueError("dag.nodes and dag.edges must be lists")
        if len(nodes) > MAX_DAG_NODES or len(edges) > MAX_DAG_EDGES:
            raise ValueError("dag exceeds bounded node/edge limits")
        safe_nodes: List[Dict[str, Any]] = []
        known: set[str] = set()
        for n in nodes:
            if not isinstance(n, dict):
                raise ValueError("dag.nodes entries must be objects")
            nid = str(n.get("node_id") or "")
            if not _NODE_ID_RE.match(nid):
                raise ValueError("invalid dag node_id")
            params = n.get("parameters") or []
            deps = n.get("depends_on") or []
            if not isinstance(params, list) or not isinstance(deps, list):
                raise ValueError("invalid dag parameters/depends_on")
            if len(params) > 32 or len(deps) > 16:
                raise ValueError("dag node exceeds bounded lists")
            known.add(nid)
            safe_nodes.append({"node_id": nid, "parameters": params,
                               "depends_on": [str(x)[:64] for x in deps]})
        safe_edges: List[Dict[str, str]] = []
        for e in edges:
            if not isinstance(e, dict):
                raise ValueError("dag.edges entries must be objects")
            src, dst = str(e.get("from") or ""), str(e.get("to") or "")
            if not _NODE_ID_RE.match(src) or not _NODE_ID_RE.match(dst):
                raise ValueError("invalid dag edge endpoint")
            safe_edges.append({"from": src, "to": dst})
        return {"nodes": safe_nodes, "edges": safe_edges}


class ExecuteRefreshRequest(_Strict):
    cancel: bool = False


class AdoptRefreshRequest(_Strict):
    """客户端声明的 CAS 锚点；服务端一律重新读取权威 revision/指纹复核。"""

    expected_revision: Optional[int] = Field(default=None, ge=0, le=1_000_000_000)
    proposal_fingerprint: Optional[str] = Field(default=None, max_length=128)


class ExecuteRefreshResponse(_Strict):
    candidate: Optional[RefreshCandidate] = None
    replayed: bool = False
    conflicts: List[Dict[str, Any]] = Field(default_factory=list)
    preserved: List[str] = Field(default_factory=list)
    receipt: Dict[str, Any] = Field(default_factory=dict)


class ProposalListResponse(_Strict):
    project_id: str
    count: int = 0
    items: List[Dict[str, Any]] = Field(default_factory=list)


class StatusResponse(_Strict):
    proposal_id: str
    proposal_status: str
    proposal_fingerprint: str = ""
    mapspec_revision: int = 0
    candidate_status: str = "not_started"
    candidate_attempts: int = 0
    progress: List[Dict[str, Any]] = Field(default_factory=list)
    adopted_revision: Optional[int] = None
    recoverable: bool = True


def reject_non_finite_numbers(node: Any) -> None:
    """服务端拒绝 NaN / Inf（契约 A5：有限数复核）。"""
    if isinstance(node, float) and not math.isfinite(node):
        raise ValueError("non-finite number is not accepted")
    if isinstance(node, dict):
        for v in node.values():
            reject_non_finite_numbers(v)
    elif isinstance(node, list):
        for v in node:
            reject_non_finite_numbers(v)


__all__ = [
    "CheckUpdatesRequest", "PreviewRefreshRequest", "ExecuteRefreshRequest",
    "AdoptRefreshRequest", "ExecuteRefreshResponse", "ProposalListResponse",
    "StatusResponse", "CheckReceipt", "RefreshProposal", "AdoptionReceipt",
    "reject_non_finite_numbers",
]
