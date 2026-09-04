"""统一 Geo 执行图契约（ADR-0096 D2）：ExecutionNode / ExecutionPlan。

这是 Data Plane 拥有的**低层执行计划**契约：
- 可序列化（pydantic，additive 演进）；
- 确定性指纹（semantic fingerprint 只含影响结果的字段 —— 类别、操作、
  输入边、数据集指纹、参数、CRS 期望；估计值/策略/deadline 不参与）；
- 节点输出可按指纹复用（reuse policy 显式声明）。

它不替代 WorkflowEngine（项目域、工具级运行时），而是可以被工具/工作流
编译和消费的下层契约（ADR-0096 D2「Rejected Alternatives」）。
"""
from __future__ import annotations

import hashlib
import json
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field

#: 节点输出指纹/复用存储的命名空间。行为语义变化时必须 bump（ADR-0089 惯例）。
EXECUTION_PLAN_VERSION = 1


class NodeCategory(str, Enum):
    """执行节点类别。无已接线执行器的类别会在执行期诚实报
    ``OPERATION_UNSUPPORTED``（见 ops.registry），绝不假装支持。"""

    SOURCE_DISCOVERY = "source_discovery"
    SOURCE_SCAN = "source_scan"
    QUERY = "query"
    FILTER = "filter"
    PROJECT = "project"
    REPROJECT = "reproject"
    SPATIAL_JOIN = "spatial_join"
    ATTRIBUTE_JOIN = "attribute_join"
    AGGREGATE = "aggregate"
    VECTOR_OPERATION = "vector_operation"
    RASTER_OPERATION = "raster_operation"
    RASTER_WINDOW_OPERATION = "raster_window_operation"
    INTERPOLATION = "interpolation"
    NETWORK_OPERATION = "network_operation"
    DECISION_OPERATION = "decision_operation"
    MATERIALIZE = "materialize"
    EXPORT = "export"
    ARTIFACT_REGISTER = "artifact_register"


class ExecutionPolicyKind(str, Enum):
    """节点执行策略。``durable_job`` 通过既有 durable-job 运行时派发
    （ADR-0052 修正案：穿过它，不加新表）。"""

    IN_PROCESS = "in_process"
    DURABLE_JOB = "durable_job"


class NodeReusePolicy(str, Enum):
    """节点结果复用策略。指纹命中 + 策略允许 → 跳过执行。"""

    ALLOW = "allow"
    DISALLOW = "disallow"


class RetryPolicy(BaseModel):
    """重试策略：只对 transient-safe 失败生效，次数硬上界。"""

    max_attempts: int = Field(default=1, ge=1, le=4)
    retry_transient_only: bool = True


class ResourceEstimate(BaseModel):
    """节点资源估计（诚实估计：未知字段留 None，不虚构精度）。"""

    rows: Optional[int] = None
    bytes: Optional[int] = None
    memory_mb: Optional[float] = None
    cpu_seconds: Optional[float] = None
    confidence: Optional[str] = None  # high | medium | assumption


class ResourceBudget(BaseModel):
    """计划级资源预算（admission control 的准入上界；M7 扩展层级作用域）。"""

    max_rows: int = Field(default=200_000, ge=1)
    max_bytes: int = Field(default=256 * 1024 * 1024, ge=1)
    deadline_s: float = Field(default=300.0, gt=0)
    max_nodes: int = Field(default=64, ge=1)


class CrsExpectation(BaseModel):
    """节点 CRS 期望：声明的输入/输出 CRS + 是否允许运行期重投影。"""

    output_crs: Optional[str] = None
    allow_reproject: bool = True


class ExecutionNode(BaseModel):
    """可执行节点：有界、可序列化、可指纹化。"""

    node_id: str
    category: NodeCategory
    operation: str = ""
    inputs: list[str] = Field(default_factory=list)
    dataset_fingerprints: dict[str, str] = Field(default_factory=dict)
    parameters: dict[str, Any] = Field(default_factory=dict)
    crs: Optional[CrsExpectation] = None
    estimate: Optional[ResourceEstimate] = None
    policy: ExecutionPolicyKind = ExecutionPolicyKind.IN_PROCESS
    reuse: NodeReusePolicy = NodeReusePolicy.ALLOW
    retry: RetryPolicy = Field(default_factory=RetryPolicy)
    deadline_s: Optional[float] = None
    cancellable: bool = True
    locality_hint: Optional[str] = None
    description: Optional[str] = None

    def semantic_fingerprint(self) -> str:
        """确定性语义指纹：只含影响输出的字段。

        排除 estimate/policy/deadline/reuse/locality —— 换执行策略不改变
        结果语义；数据集指纹变化或参数变化 → 指纹变化 → 后代失效。
        """
        payload = {
            "v": EXECUTION_PLAN_VERSION,
            "category": self.category.value,
            "operation": self.operation,
            "inputs": sorted(self.inputs),
            "dataset_fingerprints": dict(sorted(self.dataset_fingerprints.items())),
            "parameters": self.parameters,
            "crs": self.crs.model_dump() if self.crs else None,
        }
        canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


class ExecutionPlan(BaseModel):
    """执行计划：节点集合 + 依赖边（node.inputs），整体可指纹化。"""

    plan_id: str
    nodes: list[ExecutionNode] = Field(default_factory=list)
    budget: ResourceBudget = Field(default_factory=ResourceBudget)
    description: Optional[str] = None

    def node_map(self) -> dict[str, ExecutionNode]:
        return {n.node_id: n for n in self.nodes}

    def graph_fingerprint(self) -> str:
        """图指纹：节点语义指纹的有序集合 + 边集（与节点书写顺序无关）。"""
        node_fps = sorted(n.semantic_fingerprint() for n in self.nodes)
        edges = sorted(
            f"{src}->{n.node_id}" for n in self.nodes for src in n.inputs
        )
        payload = {
            "v": EXECUTION_PLAN_VERSION,
            "nodes": node_fps,
            "edges": edges,
        }
        canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]

    def node_by_fingerprint(self, fingerprint: str) -> Optional[ExecutionNode]:
        for n in self.nodes:
            if n.semantic_fingerprint() == fingerprint:
                return n
        return None


class NodeEvidence(BaseModel):
    """节点执行证据（结构化、有界、无载荷）。"""

    status: str  # pending|ready|running|completed|reused|failed|cancelled|skipped
    attempts: int = 0
    duration_s: Optional[float] = None
    rows_emitted: Optional[int] = None
    bytes_emitted: Optional[int] = None
    output_ref: Optional[str] = None
    output_summary: dict[str, Any] = Field(default_factory=dict)
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    retry_safe: Optional[bool] = None
    fingerprint: Optional[str] = None
    policy: Optional[str] = None


class ExecutionRunStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ExecutionRun(BaseModel):
    """一次计划执行的可观察结果（有界摘要，绝不含完整载荷）。"""

    run_id: str
    plan_id: str
    plan_fingerprint: str
    status: ExecutionRunStatus = ExecutionRunStatus.PENDING
    evidence: dict[str, NodeEvidence] = Field(default_factory=dict)
    wall_time_s: Optional[float] = None
    error_code: Optional[str] = None
    error_message: Optional[str] = None

    def summary_lines(self) -> list[str]:
        """人读摘要（类似 QueryPlan.summary_lines；无秘密）。"""
        lines = [
            f"run {self.run_id} plan={self.plan_id} fp={self.plan_fingerprint} status={self.status.value}"
        ]
        for node_id, ev in self.evidence.items():
            line = f"  {node_id}: {ev.status}"
            if ev.status in {"completed", "reused"}:
                line += f" rows={ev.rows_emitted} dur={ev.duration_s}"
                if ev.output_ref:
                    line += f" ref={ev.output_ref}"
            elif ev.status == "failed":
                line += f" error={ev.error_code} retry_safe={ev.retry_safe}"
            lines.append(line)
        if self.error_code:
            lines.append(f"  error: {self.error_code} {self.error_message or ''}")
        return lines
