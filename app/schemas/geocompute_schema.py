"""GeoCompute 子系统契约模型（V9 契约基石，ADR-0138）。

请求模型从 `app/api/routes/geocompute.py` verbatim 迁出；响应由
geocompute 服务投影构成，响应模型用 extra="allow" 开放对象声明
（投影形状由 tests/unit/test_geocompute_* 服务测试覆盖）。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field


class ExecutionNodeIn(BaseModel):
    node_id: str
    category: str
    operation: str = ""
    inputs: list[str] = Field(default_factory=list)
    dataset_fingerprints: Dict[str, str] = Field(default_factory=dict)
    parameters: Dict[str, Any] = Field(default_factory=dict)
    crs: Optional[Dict[str, Any]] = None
    estimate: Optional[Dict[str, Any]] = None
    policy: str = "in_process"
    reuse: str = "allow"
    retry: Dict[str, Any] = Field(default_factory=dict)
    deadline_s: Optional[float] = None
    cancellable: bool = True
    locality_hint: Optional[str] = None
    description: Optional[str] = None
    # Wave-11（audit 08 §6.2.4）：到既有 Artifact/DatasetVersion 身份的
    # lineage 边（{ref_id, kind}，≤16）—— 与 api.build_plan_from_json 同一
    # 契约；缺省时构建侧从参数里的可证源身份诚实派生（或为空）。
    lineage_inputs: list[Dict[str, str]] = Field(default_factory=list)


class ExecutionPlanIn(BaseModel):
    plan_id: str
    nodes: list[ExecutionNodeIn] = Field(default_factory=list)
    budget: Dict[str, Any] = Field(default_factory=dict)
    description: Optional[str] = None


class ExecutePlanRequest(BaseModel):
    plan: ExecutionPlanIn
    session_id: Optional[str] = None


class ClusterSubmitRequest(ExecutePlanRequest):
    """cluster submit 专属字段（不进入同步 /plans/execute 契约 —— round1 m4：
    共享模型会让同步端点静默接受并忽略 submit 语义的字段）。"""

    # V6（cluster submit）：优先级（0/5/10）与项目归属（公平/账本键）
    priority: int = 5
    project_id: Optional[str] = None
    # V7：run 级资源 envelope（placement 准入；可选 —— 缺省 = V6 行为）
    resource: Optional[Dict[str, Any]] = None


class ClusterRunResetRequest(BaseModel):
    reason: str = Field(default="admin reset", max_length=200)


class LedgerLimitsRequest(BaseModel):
    scope_key: str = Field(min_length=1, max_length=80, pattern=r"^(global|[tp]:[A-Za-z0-9_-]{1,76})$")
    limit_rows: Optional[int] = Field(default=None, ge=0)
    limit_bytes: Optional[int] = Field(default=None, ge=0)
    limit_units: Optional[int] = Field(default=None, ge=0)
    #: V8：内存（MiB）/GPU 卡数限额（enforcing 账本的 OOM 预防与 GPU 池
    #: 计数预留的限额输入；None = 解除该维限制，与其它维同语义）。
    limit_mem_mb: Optional[int] = Field(default=None, ge=0)
    limit_gpu: Optional[int] = Field(default=None, ge=0)


# ── 响应模型（P2 新增；开放对象）──────────────────────────────────────


class _Open(BaseModel):
    model_config = ConfigDict(extra="allow")


class PlanValidateResponse(_Open):
    """POST /geocompute/plans/validate 响应。"""


class PlanExecuteResponse(_Open):
    """POST /geocompute/plans/execute 响应。"""


class PlanRunSubmitResponse(_Open):
    """POST /geocompute/plans/runs 响应。"""


class RunsListResponse(_Open):
    """GET /geocompute/runs 响应。"""


class RunDetailResponse(_Open):
    """GET /geocompute/runs/{run_id} 响应。"""


class RunCancelResponse(_Open):
    """POST /geocompute/runs/{run_id}/cancel 响应。"""


class RunSummaryResponse(_Open):
    """GET /geocompute/runs/{run_id}/summary 响应。"""


class RunEventsResponse(_Open):
    """GET /geocompute/runs/{run_id}/events 响应（事件列表投影）。"""


class PlanRunCancelResponse(_Open):
    """POST /geocompute/plans/runs/{run_id}/cancel 响应。"""


class ClusterMetricsResponse(_Open):
    """GET /geocompute/cluster/metrics 响应。"""


class ClusterWorkersResponse(_Open):
    """GET /geocompute/cluster/workers 响应。"""


class ClusterStuckRunsResponse(_Open):
    """GET /geocompute/cluster/runs/stuck 响应。"""


class ClusterRunResetResponse(_Open):
    """POST /geocompute/cluster/runs/{run_id}/reset 响应。"""


class LedgerLimitsResponse(_Open):
    """POST /geocompute/cluster/ledger/limits 响应。"""


class PlanDriftCheckResponse(_Open):
    """POST /geocompute/plans/drift-check 响应。"""
