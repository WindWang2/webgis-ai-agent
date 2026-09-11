"""Qualification Engine + ExecutionEstimate —— Harness V8（ADR-0136）。

V8.3 统一资格判断：graph 实体（capability/algorithm/tool/model）× 上下文
六面（Task/Data/Map/Runtime/Resource/UserConstraint）→ 结构化结论
（eligible / ineligible(reason) / degraded(reason) / unknown(reason)）。
禁止 bool —— 每个非 eligible 结论必须可解释（reason 结构化）。

V8.4 统一资源/成本投影：tool cost/latency/memory class + algorithm
ResourceEnvelope + ModelOps resource estimate + GeoCompute capacity →
单一 ExecutionEstimate，**basis 披露**（measured | declared | estimated |
unknown）—— 不把猜测伪装成测量。

两者都是纯函数（无 I/O、无 LLM）：resource safety / owner security /
schema validation / artifact validity 的裁决确定性代码承载（§24）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from app.services.gis_harness.capability_graph import (
    KIND_ALGORITHM,
    KIND_MODEL,
    KIND_TOOL,
    CapabilityGraph,
    GraphNode,
)

__all__ = [
    "QualificationStatus", "QualificationReason", "QualificationResult",
    "QualificationContext", "qualify_node", "qualify_model_for_input",
    "ExecutionEstimate", "estimate_for_node",
    "ESTIMATE_BASIS",
]


# ── V8.3 Qualification Engine ───────────────────────────────────────────


class QualificationStatus:
    ELIGIBLE = "eligible"
    INELIGIBLE = "ineligible"
    DEGRADED = "degraded"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class QualificationReason:
    """结构化理由（可解释性契约：check / observed / expected / hint）。"""

    check: str            # 检查维度（geometry/crs/bands/resolution/...）
    observed: str         # 上下文观察到的事实
    expected: str         # 实体声明的要求
    hint: str = ""        # 修复指引（correction 方向）

    def to_dict(self) -> Dict[str, str]:
        return {"check": self.check, "observed": self.observed,
                "expected": self.expected, "hint": self.hint}


@dataclass
class QualificationResult:
    status: str
    reasons: List[QualificationReason] = field(default_factory=list)

    @property
    def eligible(self) -> bool:
        return self.status == QualificationStatus.ELIGIBLE

    def to_dict(self) -> Dict[str, Any]:
        return {"status": self.status,
                "reasons": [r.to_dict() for r in self.reasons]}


@dataclass
class QualificationContext:
    """资格判断的六面上下文（全部可选 —— 缺席面 → unknown 诚实披露）。"""

    # TaskContext
    task_hint: str = ""                     # 意图/任务语义提示
    # DataContext
    geometry_kinds: List[str] = field(default_factory=list)
    crs: str = ""
    crs_is_geographic: Optional[bool] = None
    field_names: List[str] = field(default_factory=list)
    feature_count: Optional[int] = None
    raster_bands: Optional[int] = None
    resolution_m_per_px: Optional[float] = None   # 0 = 未知（地理 CRS）
    sensor: str = ""
    temporal_inputs: int = 0
    data_bytes: Optional[int] = None
    # MapContext
    map_layer_count: Optional[int] = None
    # RuntimeContext
    dependency_available: Dict[str, bool] = field(default_factory=dict)
    credentials_present: Dict[str, bool] = field(default_factory=dict)
    # ResourceContext
    gpu_available: bool = False
    vram_bytes: Optional[int] = None
    memory_bytes: Optional[int] = None
    # UserConstraint
    max_latency_class: str = ""             # "" = 无约束（fast/medium/slow）
    owner_scope_key: str = ""               # 模型可见域（owner 隔离面）


def _reason(check: str, observed: str, expected: str, hint: str = "") -> QualificationReason:
    return QualificationReason(check=check, observed=observed,
                               expected=expected, hint=hint)


def qualify_node(
    node: GraphNode,
    ctx: QualificationContext,
    graph: Optional[CapabilityGraph] = None,
) -> QualificationResult:
    """graph 实体 × 上下文 → 结构化资格结论（纯函数）。

    检查维度（按 kind 取子集）：geometry / crs / fields / min_features /
    raster bands / resolution / data volume / dependency / credentials /
    gpu / memory / model-compat / latency constraint / owner scope。
    """
    reasons: List[QualificationReason] = []
    degraded: List[QualificationReason] = []
    unknown: List[QualificationReason] = []

    # ── 通用：延迟约束（tool 段的 latency_class 声明）──
    if node.kind == KIND_TOOL:
        declared = str(node.extras.get("latency_class", "")).lower()
        if ctx.max_latency_class and declared:
            rank = {"fast": 0, "medium": 1, "slow": 2}
            want = rank.get(ctx.max_latency_class.lower())
            got = rank.get(declared)
            if want is not None and got is not None and got > want:
                reasons.append(_reason(
                    "latency_constraint",
                    f"tool latency_class={declared}",
                    f"user constraint ≤ {ctx.max_latency_class}",
                    "choose a faster alternative or relax the constraint"))
        # tier 可见性（tier>=3 需显式确认 —— 资格层只披露，不裁决）
        if int(node.extras.get("tier", 1)) >= 3:
            degraded.append(_reason(
                "confirm_required",
                f"tier={node.extras.get('tier')}",
                "tier<3 for autonomous dispatch",
                "explicit user confirmation required"))

    # ── 算法段（algorithm registry 声明的 preconditions）──
    if node.kind == KIND_ALGORITHM:
        # 声明面来自 node.extras（V8 build 时按需扩投影；当前 complexity
        # 只有摘要 —— 详细 preconditions 由 capability_descriptors 的
        # check_preconditions 承载（V7 既有实现，V8 不重复））。此处做
        # graph 侧的通用检查：min_features（经 graph 查 algorithm 节点
        # 时由调用方在 extras 提供）。
        min_features = node.extras.get("min_features")
        if isinstance(min_features, int) and ctx.feature_count is not None:
            if ctx.feature_count < min_features:
                reasons.append(_reason(
                    "min_features",
                    f"feature_count={ctx.feature_count}",
                    f"≥ {min_features}",
                    "collect more samples or choose a smaller-input method"))

    # ── 模型段（V8.2 核心：Model 兼容性资格）──
    if node.kind == KIND_MODEL:
        result = qualify_model_for_input(node, ctx)
        if result.status == QualificationStatus.INELIGIBLE:
            reasons.extend(result.reasons)
        elif result.status == QualificationStatus.UNKNOWN:
            unknown.extend(result.reasons)
        else:
            degraded.extend(result.reasons)

    # ── 数据量（graph 侧有界披露：>500MB 大数据建议 geocompute 路径）──
    if ctx.data_bytes is not None and ctx.data_bytes > 500 * 1024 * 1024:
        degraded.append(_reason(
            "data_volume",
            f"data≈{ctx.data_bytes >> 20}MB",
            "≤512MB for inline path",
            "prefer geocompute distributed execution"))

    if reasons:
        return QualificationResult(QualificationStatus.INELIGIBLE, reasons)
    if degraded:
        return QualificationResult(QualificationStatus.DEGRADED, degraded)
    if unknown:
        return QualificationResult(QualificationStatus.UNKNOWN, unknown)
    return QualificationResult(QualificationStatus.ELIGIBLE, [])


def qualify_model_for_input(
    model_node: GraphNode,
    ctx: QualificationContext,
) -> QualificationResult:
    """Model × 输入上下文的兼容性资格（modelops descriptor 声明面）。

    检查：bands / resolution range / sensor / temporal inputs / GPU /
    owner scope。每项缺席 → unknown 诚实披露（不猜）。
    """
    reasons: List[QualificationReason] = []
    soft: List[QualificationReason] = []
    unknown: List[QualificationReason] = []

    # bands（Review A RA-4：实体有声明而上下文缺席 → unknown 诚实披露）
    expected_bands = model_node.extras.get("input_bands")
    if expected_bands is not None and ctx.raster_bands is None:
        unknown.append(_reason(
            "raster_bands_unknown",
            "input band count not observed",
            f"≥ {expected_bands}（{model_node.id}）",
            "profile the raster before model selection"))
    if expected_bands is not None and ctx.raster_bands is not None:
        if int(ctx.raster_bands) < int(expected_bands):
            reasons.append(_reason(
                "raster_bands",
                f"input bands={ctx.raster_bands}",
                f"≥ {expected_bands}（{model_node.id}）",
                "choose a band-compatible model or composite first"))

    # resolution（0 = 地理 CRS 未知口径 —— R1-C3/B-6 同一纪律）
    lo = model_node.extras.get("min_m_per_px")
    hi = model_node.extras.get("max_m_per_px")
    res = ctx.resolution_m_per_px
    if res is not None and res > 0 and lo is not None and hi is not None:
        if not (float(lo) <= res <= float(hi)):
            reasons.append(_reason(
                "resolution",
                f"{res} m/px",
                f"[{lo}, {hi}] m/px",
                "resample to the model range or pick another model"))
    elif res is None and (lo is not None or hi is not None):
        unknown.append(_reason(
            "resolution_unknown",
            "resolution context absent",
            f"[{lo}, {hi}] m/px",
            "provide resolution or projected CRS context"))
    elif res == 0:
        # 软结论（degraded 面）：地理 CRS 无法判定米制口径 —— 不硬拒
        #（R1-C3/B-6 同一纪律：未知 ≠ 不兼容），由 qualify_node 聚合为
        # degraded 并保留可解释 reason。
        soft.append(_reason(
            "resolution_unknown",
            "geographic CRS（degrees/px, 未知米制口径）",
            f"[{lo}, {hi}] m/px",
            "declare/reproject to projected CRS for resolution gating"))

    # temporal inputs（双时相任务需要 source_b）
    task_types = model_node.extras.get("task_types") or []
    if any(t in ("change_detection", "sar_optical_fusion") for t in task_types) \
            and ctx.temporal_inputs < 2:
        reasons.append(_reason(
            "temporal_inputs",
            f"temporal_inputs={ctx.temporal_inputs}",
            "≥ 2（双时相任务）",
            "provide a second temporal raster"))

    # GPU（required=cpu 的模型不查；graph 摘要无 device 字段时跳过 —
    # 详细 device 资格在 modelops check_compatibility，此处只做投影级披露）
    if model_node.extras.get("requires_gpu") and not ctx.gpu_available:
        reasons.append(_reason(
            "gpu",
            "gpu_available=False",
            "GPU required",
            "enable GPU backend or choose a CPU model"))

    # owner scope（模型注册域 vs 请求域 —— 跨域不可见）
    owner_key = str(model_node.extras.get("owner_scope_key", ""))
    if owner_key and ctx.owner_scope_key and owner_key != ctx.owner_scope_key:
        # 种子模型（modelops.seeds / builtin 全局域）对所有人可见
        if "modelops.seeds" not in owner_key and "global-builtin" not in owner_key:
            reasons.append(_reason(
                "owner_scope",
                f"request scope={ctx.owner_scope_key}",
                f"model registered under {owner_key}",
                "register the model under the requesting scope"))
    elif owner_key and not ctx.owner_scope_key:
        # RA-4：请求域缺席 → 可见性未知（不静默 eligible）；builtin 种子豁免
        if "modelops.seeds" not in owner_key and "global-builtin" not in owner_key:
            unknown.append(_reason(
                "owner_scope_unknown",
                "request owner scope absent",
                f"model registered under {owner_key}",
                "provide the requesting owner scope for visibility gating"))

    if reasons:
        return QualificationResult(QualificationStatus.INELIGIBLE, reasons)
    if soft:
        return QualificationResult(QualificationStatus.DEGRADED, soft)
    if unknown:
        return QualificationResult(QualificationStatus.UNKNOWN, unknown)
    return QualificationResult(QualificationStatus.ELIGIBLE, [])


# ── V8.4 ExecutionEstimate（basis 诚实披露）─────────────────────────────

ESTIMATE_BASIS = ("measured", "declared", "estimated", "unknown")


@dataclass
class ExecutionEstimate:
    """统一资源/成本投影（§23）：值 + 置信度 + 基础披露。"""

    cpu: str = "medium"                     # low/medium/high 档位
    memory: str = "medium"
    gpu: bool = False
    vram_bytes: Optional[int] = None
    io: str = "medium"
    network: str = "none"                   # none/low/medium/high
    estimated_tiles: Optional[int] = None
    estimated_rows: Optional[int] = None
    latency_class: str = "medium"           # fast/medium/slow
    confidence: float = 0.0                 # 0..1
    basis: Dict[str, str] = field(default_factory=dict)  # 维度 → basis

    def to_dict(self) -> Dict[str, Any]:
        return {
            "cpu": self.cpu, "memory": self.memory, "gpu": self.gpu,
            "vram_bytes": self.vram_bytes, "io": self.io, "network": self.network,
            "estimated_tiles": self.estimated_tiles,
            "estimated_rows": self.estimated_rows,
            "latency_class": self.latency_class,
            "confidence": round(self.confidence, 2),
            "basis": dict(self.basis),
        }


def estimate_for_node(node: GraphNode) -> ExecutionEstimate:
    """graph 实体 → 统一 estimate（纯函数；来源收敛，不复制声明）。

    basis 语义：declared = registry 声明（tool class / algorithm envelope）；
    estimated = 派生近似（tiles/rows 由上下文推 —— 当前投影级无数据上下文，
    留 None）；measured = 观测（reliability/性能台账接入后回填）；unknown =
    无任何来源。低置信度如实披露（confidence 按有据维度占比）。
    """
    est = ExecutionEstimate()
    dims_with_basis = 0

    if node.kind == KIND_TOOL:
        latency = str(node.extras.get("latency_class", "")).lower()
        memory = str(node.extras.get("memory_class", "")).lower()
        if latency:
            est.latency_class = latency
            est.basis["latency_class"] = "declared"
            dims_with_basis += 1
        if memory:
            est.memory = memory
            est.basis["memory"] = "declared"
            dims_with_basis += 1
        # gpu 推断：execution_policy 含 celery/async 的重面 + modelops 工具
        if "celery" in str(node.extras.get("execution_policy", "")):
            est.cpu = "high"
            est.basis["cpu"] = "declared"

    elif node.kind == KIND_MODEL:
        # modelops 侧的 MemoryEstimate/DeviceRequirements 是精确源 ——
        # graph 摘要只有 bands/provider；estimate 投影为声明档位 +
        # gpu=provider 语义（local_reference = CPU 参考实现）。
        provider = str(node.extras.get("provider_ref", ""))
        est.gpu = "gpu" in provider.lower() or "cuda" in provider.lower()
        est.latency_class = "slow"
        est.basis["latency_class"] = "declared"
        est.basis["gpu"] = "declared"
        est.io = "medium"
        est.basis["io"] = "estimated"
        dims_with_basis += 2

    elif node.kind == KIND_ALGORITHM:
        # algorithm registry 的 ResourceEnvelope（cpu/memory/io 枚举档位）
        # 在 V7 capability_descriptors 投影里 —— graph 侧当前摘要仅
        # complexity；有 complexity 时按档位近似并披露 estimated。
        complexity = str(node.extras.get("complexity", "")).lower()
        if complexity:
            est.cpu = "high" if complexity in ("high", "n_log_n_squared") else est.cpu
            est.latency_class = "slow" if complexity == "high" else est.latency_class
            est.basis["cpu"] = "estimated"
            est.basis["latency_class"] = "estimated"
            dims_with_basis += 1

    # 无据维度 → unknown（诚实披露，不猜）
    for dim in ("cpu", "memory", "latency_class", "gpu"):
        if dim not in est.basis:
            est.basis[dim] = "unknown"
    est.confidence = min(1.0, dims_with_basis / 4.0)
    return est
