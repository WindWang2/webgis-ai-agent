"""统一 Geo 执行图契约（ADR-0096 D2；ADR-0101 D2 扩展）：ExecutionNode / ExecutionPlan。

这是 Data Plane 拥有的**低层执行计划**契约：
- 可序列化（pydantic，additive 演进）；
- 确定性指纹（semantic fingerprint 只含影响结果的字段 —— 类别、操作、
  输入边、数据集指纹、参数、CRS 期望、载荷类型契约；估计值/策略/deadline
  不参与），指纹前经 ``normalization.canonicalize`` 归一化；
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

from app.services.geocompute.normalization import canonical_dumps, normalize_crs_ref

#: 节点输出指纹/复用存储的命名空间。行为语义变化时必须 bump（ADR-0089 惯例）。
#: V2（ADR-0101）：canonical 归一化进入指纹 + 载荷类型契约（produces/accepts）
#: 参与语义指纹 —— 与 V1 的指纹值域不相交，旧条目自然失效。
EXECUTION_PLAN_VERSION = 2


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
    """重试策略：只对 transient-safe 失败生效，次数硬上界。

    V4（ADR-0101 D5）：有界指数退避；``jitter`` 仅在不需要确定性重放时
    开启（默认开 —— 控制重试风暴；重放型执行应显式关闭）。
    """

    max_attempts: int = Field(default=1, ge=1, le=4)
    retry_transient_only: bool = True
    backoff_s: float = Field(default=0.05, ge=0, le=60.0)
    backoff_multiplier: float = Field(default=2.0, ge=1.0, le=10.0)
    max_backoff_s: float = Field(default=2.0, ge=0, le=300.0)
    jitter: bool = True


class PayloadKind(str, Enum):
    """节点载荷类型契约（V4 §5 typed inputs/outputs 的最小诚实形式）。

    与 ops 的 payload 真值对齐：features | rows | ref_id（session ref）|
    raster_path | none。``ANY`` 仅用于 accepts（不过问上游类型）。
    """

    FEATURES = "features"
    ROWS = "rows"
    REF = "ref"
    RASTER_PATH = "raster_path"
    NONE = "none"
    ANY = "any"


class ResourceClass(BaseModel):
    """节点资源类别（调度/路由提示，1..5；不参与语义指纹）。"""

    memory: int = Field(default=1, ge=1, le=5)
    cpu: int = Field(default=1, ge=1, le=5)
    io: int = Field(default=1, ge=1, le=5)


class LineageLink(BaseModel):
    """到既有身份真相的 lineage 边（不建第二 lineage 存储，ADR-0101 D8）。

    ``ref_id`` 是 ArtifactRef / DatasetVersion / session ref 的既有 id，
    ``kind`` 声明其身份族；执行期 lineage 投影据此连接 ArtifactLineage。
    """

    model_config = {"extra": "forbid"}

    ref_id: str = Field(min_length=1, max_length=256)
    kind: str = Field(default="artifact")  # artifact | dataset_version | ref


class ResourceEstimate(BaseModel):
    """节点资源估计（诚实估计：未知字段留 None，不虚构精度）。"""

    rows: Optional[int] = None
    bytes: Optional[int] = None
    memory_mb: Optional[float] = None
    cpu_seconds: Optional[float] = None
    confidence: Optional[str] = None  # high | medium | assumption


class ResourceBudget(BaseModel):
    """计划级资源预算（admission control 的准入上界；层级作用域见 budgets.py）。

    上界（``le``）是**服务端**红线：预算是调用方与运行时协商的配额，
    不是无界声明（安全评审 F7 —— 客户端不得自授 1e9 行 / 1e9 秒）。
    """

    max_rows: int = Field(default=200_000, ge=1, le=10_000_000)
    max_bytes: int = Field(default=256 * 1024 * 1024, ge=1, le=2 * 1024 * 1024 * 1024)
    deadline_s: float = Field(default=300.0, gt=0, le=3600.0)
    max_nodes: int = Field(default=64, ge=1, le=256)


class CrsExpectation(BaseModel):
    """节点 CRS 期望：声明的输入/输出 CRS + 是否允许运行期重投影。"""

    output_crs: Optional[str] = None
    allow_reproject: bool = True


class PartitionSpec(BaseModel):
    """V8 空间分区声明（节点级；Phase D，ADR-0133 §4）。

    节点声明 ``partition`` 后，durable 执行在该节点上做空间 fan-out：
    输入按 scheme 切成 N 个空间分区（每个分区一个独立 durable job，
    幂等键 = 节点指纹 + 分区索引），完成后按明确的 seam 语义合并：
    - ``raster_grid``：像素窗口网格 + halo（halo 参与计算、合并时裁除，
      重叠区 first-wins）；输出栅格 CRS/transform 与源一致；
    - ``vector_grid``：bbox 网格 + halo_ratio（中心点分配；halo 邻域
      复制，合并按内容指纹去重 —— 内容相同的要素塌缩为一个）。

    ``target_tiles`` 是请求值；执行期 ``adaptive_tile_count`` 可按资源
    估计收缩（小输入不值得 fan-out），上限 256（事件/证据基数有界）。
    """

    model_config = {"extra": "forbid"}

    scheme: str = Field(pattern="^(raster_grid|vector_grid)$")
    target_tiles: int = Field(default=4, ge=1, le=256)
    #: raster halo（像素；参与邻域计算，合并时裁除）
    halo_px: int = Field(default=0, ge=0, le=4096)
    #: vector halo（bbox 外扩比例 ≤0.25；合并按内容去重）
    halo_ratio: float = Field(default=0.0, ge=0.0, le=0.25)
    #: 分区元数据 CRS（缺省继承节点 crs.output_crs / 栅格头）
    crs: Optional[str] = Field(default=None, max_length=128)
    #: 自适应下界：估计单 tile 内存超过该预算（MiB）时增加 tile 数
    per_tile_mem_budget_mb: Optional[float] = Field(default=None, gt=0)
    #: 单 tile 最小行数（低于则收缩 tile 数 —— 防 fan-out 开销倒挂）
    min_rows_per_tile: int = Field(default=1000, ge=0)


class ExecutionNode(BaseModel):
    """可执行节点：有界、可序列化、可指纹化（ADR-0101 D2 完整契约）。"""

    node_id: str = Field(min_length=1, max_length=128)
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
    # ---- V4 additive（ADR-0101 D2）：契约补全，向后兼容（全部有默认值）----
    #: 载荷类型契约：produces 声明输出形态；accepts 声明可接受的输入形态
    #: （空 = 不过问；ANY = 任意）。校验期做边级兼容检查。
    produces: Optional[PayloadKind] = None
    accepts: list[PayloadKind] = Field(default_factory=list, max_length=8)
    #: 资源类别（调度/路由提示；不参与语义指纹）。
    resource_class: ResourceClass = Field(default_factory=ResourceClass)
    #: 确定性声明：False 的节点禁止结果复用（校验期强制 reuse=DISALLOW）。
    deterministic: bool = True
    #: 已知上游内容指纹集（node_id → fingerprint）：checkpoint 校验 /
    #: 部分重跑时验证缓存结果仍与上游一致。不参与语义指纹（可由图推导）。
    upstream_fingerprints: dict[str, str] = Field(default_factory=dict, max_length=64)
    #: 到既有身份真相的 lineage 边（不建第二 lineage 存储）。
    lineage_inputs: list[LineageLink] = Field(default_factory=list, max_length=16)
    #: 节点证据 schema（字段 → 类型名，≤32 项）：消费者契约声明。
    evidence_schema: dict[str, str] = Field(default_factory=dict, max_length=32)
    #: V8 空间分区声明（durable 执行 fan-out；None = 不分区）。
    #: **参与语义指纹** —— 分区方案改变 seam 合并语义（halo/去重边界），
    #: 与 parameters 同等地位。
    partition: Optional[PartitionSpec] = None

    def semantic_fingerprint(self) -> str:
        """确定性语义指纹：只含影响输出的字段。

        排除 estimate/policy/deadline/reuse/locality/resource_class/
        deterministic/upstream_fingerprints/lineage/evidence_schema ——
        换执行策略或调度提示不改变结果语义；数据集指纹、参数、分区方案
        或载荷类型契约变化 → 指纹变化 → 后代失效。
        """
        payload = {
            "v": EXECUTION_PLAN_VERSION,
            "category": self.category.value,
            "operation": self.operation,
            "inputs": sorted(self.inputs),
            "dataset_fingerprints": dict(sorted(self.dataset_fingerprints.items())),
            "parameters": self.parameters,
            "crs": self._normalized_crs(),
            "produces": self.produces.value if self.produces else None,
            "accepts": sorted(a.value for a in self.accepts),
            "partition": (
                canonical_dumps(self.partition.model_dump())
                if self.partition is not None else None
            ),
        }
        canonical = canonical_dumps(payload)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]

    def _normalized_crs(self) -> Optional[dict[str, Any]]:
        """CRS 期望归一化（等价拼写 → 同一指纹）。"""
        if self.crs is None:
            return None
        dumped = self.crs.model_dump()
        if dumped.get("output_crs"):
            dumped["output_crs"] = normalize_crs_ref(dumped["output_crs"])
        return dumped


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
    # ---- V4 additive（ADR-0101 D2/D5）：重试与 checkpoint 证据 ----
    #: 每次失败尝试的 error_code 序列（≤ max_attempts 项；重试证据）。
    failure_codes: list[str] = Field(default_factory=list, max_length=4)
    #: 复用命中时：缓存条目的上游指纹是否与当前计划一致（stale 拒绝证据）。
    checkpoint_verified: Optional[bool] = None
    # ---- V5 additive（audit 06 §6.1）：异构调度与跨进程复用证据 ----
    #: 执行后端变体（诚实披露）：durable 节点在 eager（无 Redis）下为
    #: "in_process_eager"（durable 语义降级为进程内同步执行，必须可见）；
    #: 正常 broker 派发为 None。
    backend_variant: Optional[str] = None
    #: 复用来源（命中时）：in_process | cross_process_index。
    reuse_source: Optional[str] = None
    #: 复用被拒时的类型化原因（upstream_changed:<nodes> /
    #: result_ref_unresolvable）；未尝试复用或命中时为 None。
    reuse_skipped_reason: Optional[str] = None


class ExecutionRunStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    #: V6（cluster runtime）：在安全点（节点边界）被 coordinator 抢占。
    #: additive 成员 —— 消费方均按具体值等值匹配，无穷举切换依赖。
    PREEMPTED = "preempted"


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
    # ---- V5 additive（audit 06 §6.1 step 2）----
    #: 读取来源：None = 进程内活注册表；"snapshot" = 终态证据快照回放
    #: （进程重启后的持久化读取路径）。
    source: Optional[str] = None

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
