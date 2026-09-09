"""Backend Variant & Scale Policy selection（Foundation V2 · A7）。

把 ``backend_variants`` 从纯 metadata 变成**可解释的运行时选择层**：

- 输入：算法 id + 规模画像（特征数/像元数/导出语境）；
- 输出：确定性 ``BackendDecision``（选中的变体、规模分层、选择理由、
  执行策略、运行通道建议），可整体进科学证据的 diagnostics 块；
- 选择规则是**纯函数**：变体声明（``min_features``/``max_features``
  规模窗口，声明序即偏好序）+ 成本模型阈值 —— 无状态、无环境读取、
  同输入必同输出；
- 不新建第二套 job/runtime：heavy/durable 路径只是复用既有
  ``cost_model`` 的执行策略与运行通道词表给出的**建议**，真正执行仍由
  GeoCompute / ToolRegistry 既有真相承担；
- 语义同一性：backend 选择**不改变算法语义** —— 所有变体必须通过同一
  conformance 套件（ADR-0099 §28）；变体失败时的回退语义仍由
  ``fallback_semantics`` 表达，与本层无关。

边界（刻意不做）：不在 resolver 中加 backend 门（中心文件冻结，且
capability→algorithm 的解析语义与「同一算法选哪个实现」正交）；不做
GPU/分布式等不存在路径的虚构。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from app.lib.gis.cost_model import (
    infer_execution_policy,
    resolve_runtime_strategy,
    scale_tier,
)

_RATIONALE_MAX = 160


@dataclass(frozen=True)
class ScaleProfile:
    """触发 backend 选择的规模画像（全部可缺省 —— 缺省按 unknown 处理）。

    V3（D10）：``raster_cells`` 从保留字段变为**真实决策输入** ——
    ``feature_count`` 缺省而 ``raster_cells`` 存在时（栅格主导型算法的
    常见调用形态），以像元数折算规模窗口与分层；两者同时存在时
    ``feature_count`` 优先（向量语义不变）。``estimated_bytes`` 是可选
    的内存估算：超过预算常量时在 rationale 里追加内存注记（本层是
    建议性诊断，不做硬闸 —— 硬闸在实现层 ResourceScaleMismatch）。
    """

    feature_count: Optional[int] = None
    raster_cells: Optional[int] = None
    export: bool = False
    estimated_bytes: Optional[int] = None


# 内存注记阈值：诊断性预算（不拒绝），>2 GiB 估算提示分块/服务端通道。
_MEMORY_NOTE_BYTES = 2 * 1024**3


@dataclass(frozen=True)
class BackendDecision:
    """一次确定的 backend 选择（可解释、可进证据、可测试）。"""

    algorithm_id: str
    variant_id: str  # "" = 算法未声明变体（默认工具路径）
    backend: str  # "" = 同上；否则 BACKEND_VOCABULARY 成员
    scale_tier: str  # cost_model.scale_tier 词表
    matched: bool  # True = n 落在某变体声明的规模窗口内
    rationale: str  # 选择理由（≤160，进证据）
    execution_policy: str  # cost_model 执行策略词表
    runtime_strategy: str  # cost_model 运行通道词表（heavy → 服务端建议）
    # ── V3（ADR-0117）：resource envelope 估算 + 近似披露（缺省兼容）──
    estimated_bytes: Optional[int] = None  # ResourceEnvelope 线性估算
    approximation_disclosure: str = ""  # 非空 = 所选路径非 exact（≤160）
    resource_warnings: tuple = ()  # pair budget / 硬上限预警文本

    def to_diagnostic(self) -> Dict[str, Any]:
        """→ ScientificEvidenceBuilder diagnostics 条目形状。"""
        text = f"variant={self.variant_id or '(default)'}; {self.rationale}"
        if self.approximation_disclosure:
            text = f"{text}; {self.approximation_disclosure}"
        return {
            "name": "backend_selection",
            "value": self.variant_id or "default",
            "text": text[:_RATIONALE_MAX],
        }

    def to_evidence(self) -> "BackendEvidence":
        """→ 结构化 BackendEvidence（进科学证据 backend 块）。"""
        return BackendEvidence(
            algorithm_id=self.algorithm_id,
            variant_id=self.variant_id,
            backend=self.backend,
            scale_tier=self.scale_tier,
            matched=self.matched,
            approximation_disclosure=self.approximation_disclosure,
            estimated_bytes=self.estimated_bytes,
            resource_warnings=list(self.resource_warnings),
            execution_policy=self.execution_policy,
            runtime_strategy=self.runtime_strategy,
            rationale=self.rationale,
        )


@dataclass(frozen=True)
class BackendEvidence:
    """一次 backend 选择的完整可审计证据（V3 ADR-0117）。

    与 ``BackendDecision.to_diagnostic()`` 的单条诊断文本互补：本类型
    承载结构化字段（近似披露/资源估算/预警），供证据块与 benchmark
    manifest 消费；序列化键序确定。
    """

    algorithm_id: str
    variant_id: str
    backend: str
    scale_tier: str
    matched: bool
    approximation_disclosure: str
    estimated_bytes: Optional[int]
    resource_warnings: List[str]
    execution_policy: str
    runtime_strategy: str
    rationale: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "algorithm_id": self.algorithm_id,
            "variant_id": self.variant_id,
            "backend": self.backend,
            "scale_tier": self.scale_tier,
            "matched": self.matched,
            "approximation_disclosure": self.approximation_disclosure,
            "estimated_bytes": self.estimated_bytes,
            "resource_warnings": list(self.resource_warnings),
            "execution_policy": self.execution_policy,
            "runtime_strategy": self.runtime_strategy,
            "rationale": self.rationale,
        }


def backend_evidence_from_decision(decision: BackendDecision) -> Dict[str, Any]:
    """兼容辅助：BackendDecision → 结构化证据字典。"""
    return decision.to_evidence().to_dict()


def _window_contains(variant: Any, n: int) -> bool:
    lo = getattr(variant, "min_features", None)
    hi = getattr(variant, "max_features", None)
    if lo is not None and n < lo:
        return False
    if hi is not None and n > hi:
        return False
    return True


def select_backend(
    algorithm_id: str,
    scale: ScaleProfile,
    *,
    algorithm_registry: Any = None,
) -> BackendDecision:
    """为算法确定性选择实现变体（纯函数；声明序即偏好序）。

    选择序：
    1. 未声明变体 → 默认工具路径（``variant_id=""``）；
    2. n 落入某变体窗口 → 声明序第一个命中者（matched=True）；
    3. n 落不进任何窗口（声明有洞）→ 第一个上界无界（``max_features``
       为 None）的变体，否则声明序第一个 —— 显式降级，理由写明（matched=False）；
    4. n 未知 → 声明序第一个变体（matched=False，理由写明 deferred）。
    """
    if algorithm_registry is None:
        from app.lib.gis.algorithm_registry import get_algorithm_registry

        algorithm_registry = get_algorithm_registry()

    n = scale.feature_count
    raster_source = False
    if n is None and scale.raster_cells is not None:
        # 栅格主导：像元数折算规模（窗口/分层语义与向量一致）
        n = scale.raster_cells
        raster_source = True
    tier = scale_tier(n)
    descriptor = algorithm_registry.get(algorithm_id)
    if descriptor is None:
        raise KeyError(f"backend_selection: unknown algorithm {algorithm_id!r}")

    policy = infer_execution_policy(
        feature_count=n,
        export=scale.export,
        deterministic_output=bool(getattr(descriptor, "deterministic", True)),
    )
    strategy = resolve_runtime_strategy(
        feature_count=n,
        artifact_type=str(getattr(descriptor, "output_artifact_type", "") or ""),
    )

    variants = list(getattr(descriptor, "backend_variants", []) or [])
    if not variants:
        return BackendDecision(
            algorithm_id=algorithm_id,
            variant_id="",
            backend="",
            scale_tier=tier,
            matched=False,
            rationale="no_variants_declared: 算法未声明实现变体，走默认工具路径",
            execution_policy=policy,
            runtime_strategy=strategy,
        )

    if n is None:
        chosen = variants[0]
        rationale = (
            f"scale_unknown: 特征数未知，按声明序偏好取 {chosen.id}（窗口待运行时复核）"
        )
    else:
        hits = [v for v in variants if _window_contains(v, n)]
        if hits:
            chosen = hits[0]
            source = "像元数" if raster_source else "n"
            rationale = (
                f"{source}={n} 落入 {chosen.id} 声明窗口 "
                f"[{chosen.min_features}, {chosen.max_features}]（声明序优先）"
            )
        else:
            unbounded = [v for v in variants if v.max_features is None]
            chosen = unbounded[0] if unbounded else variants[0]
            source = "像元数" if raster_source else "n"
            rationale = (
                f"{source}={n} 不在任何变体窗口内，降级取 {chosen.id}（无界上界优先）——"
                "科学语义不变，性能预算自行声明"
            )

    matched = bool(n is not None and _window_contains(chosen, n))
    disclosure = _approximation_disclosure(descriptor, chosen, matched)

    resource_warnings: List[str] = []
    estimated_bytes = _estimate_resource_bytes(descriptor, scale, n, resource_warnings)

    if scale.estimated_bytes is not None and scale.estimated_bytes > _MEMORY_NOTE_BYTES:
        rationale = (
            f"{rationale}; 内存估算 {scale.estimated_bytes / 1024**3:.1f} GiB "
            f"超 {_MEMORY_NOTE_BYTES / 1024**3:.0f} GiB 预算，建议分块/服务端通道"
        )

    return BackendDecision(
        algorithm_id=algorithm_id,
        variant_id=chosen.id,
        backend=str(chosen.backend),
        scale_tier=tier,
        matched=matched,
        rationale=rationale[:_RATIONALE_MAX],
        execution_policy=policy,
        runtime_strategy=strategy,
        estimated_bytes=estimated_bytes,
        approximation_disclosure=disclosure,
        resource_warnings=tuple(resource_warnings),
    )


def _approximation_disclosure(descriptor: Any, chosen: Any, matched: bool) -> str:
    """所选路径非 exact / 出窗降级时的显式近似语义披露（V3 ADR-0117）。

    原则：降级不可夸大科学等价性 —— 变体级或算法级 approximation_class
    非 exact 时必须显现在证据里；出窗（matched=False）即使 exact 也披露
    窗口外运行无规模保证。
    """
    algo_class = str(getattr(descriptor, "approximation_class", "") or "")
    variant_class = str(getattr(chosen, "approximation_class", "") or "")
    eff = variant_class or algo_class
    parts: List[str] = []
    if eff and eff != "exact":
        parts.append(f"approximation_class={eff}")
    if not matched:
        parts.append("scale_window_exceeded")
    if not parts:
        return ""
    return "近似/降级披露: " + "; ".join(parts)


def _estimate_resource_bytes(
    descriptor: Any,
    scale: ScaleProfile,
    n: Optional[int],
    warnings: List[str],
) -> Optional[int]:
    """ResourceEnvelope → 结构化资源估算（estimate-before-allocate 诊断）。

    线性系数估算主数组字节；O(n²) 对预算超限与硬上限逼近以预警文本
    返回（本层是建议性诊断，类型化硬闸仍在实现层）。失败静默跳过
    （envelope 声明错误由 registry validate() 负责，不在选择层抛）。
    """
    env = getattr(descriptor, "resource_envelope", None)
    if env is None:
        return None
    total = 0.0
    try:
        if n is not None and env.bytes_per_feature is not None:
            total += env.bytes_per_feature * n
        cells = scale.raster_cells
        if cells is not None and env.bytes_per_cell is not None:
            total += env.bytes_per_cell * cells
        if env.max_pairs is not None and n is not None and n > 1:
            pairs = n * (n - 1) // 2
            if pairs > env.max_pairs:
                warnings.append(
                    f"pair budget: n={n} 需 {pairs} 对 > 声明预算 {env.max_pairs}"
                )
        if (
            env.hard_max_features is not None
            and n is not None
            and n > env.hard_max_features
        ):
            warnings.append(
                f"hard_max_features: n={n} 超声明拒绝阈值 {env.hard_max_features}"
            )
        if (
            env.hard_max_cells is not None
            and scale.raster_cells is not None
            and scale.raster_cells > env.hard_max_cells
        ):
            warnings.append(
                f"hard_max_cells: cells={scale.raster_cells} 超声明拒绝阈值 "
                f"{env.hard_max_cells}"
            )
    except TypeError:
        return None
    if total <= 0:
        return None
    return int(math.ceil(total))


# ── science-v5 W7：ExecutionPlan（规模→执行方式的纯函数投影）──────────

# 执行方式封闭词表。**distributed 不存在**（planned）——不进词表、
# 不虚构路径；分发执行的真实引入须同时更新本词表与 descriptor。
EXECUTION_MODE_VOCABULARY = ("native", "vectorized", "chunked")


@dataclass(frozen=True)
class ExecutionPlan:
    """一次执行方式规划（select_backend 决策之上的薄投影；纯函数可测）。

    ``mode``：
    - ``vectorized``：批量/堆叠实现变体（variant id 含 "batched"）；
    - ``chunked``：估算内存超预算 → 建议分块形状（建议性，硬闸在实现层）；
    - ``native``：默认工具路径或非批量变体。
    """

    algorithm_id: str
    variant_id: str
    backend: str
    mode: str                    # EXECUTION_MODE_VOCABULARY 成员
    matched: bool                # 规模窗口命中（透传 select_backend）
    chunk_shape: Optional[Dict[str, int]] = None   # mode=chunked 时的建议形状
    memory_note: str = ""        # 内存估算/预算注记（≤160）
    approximation_disclosure: str = ""
    rationale: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "algorithm_id": self.algorithm_id,
            "variant_id": self.variant_id or "default",
            "backend": self.backend or "default",
            "mode": self.mode,
            "matched": self.matched,
            "chunk_shape": (
                dict(self.chunk_shape) if self.chunk_shape else None),
            "memory_note": self.memory_note,
            "approximation_disclosure": self.approximation_disclosure,
            "rationale": self.rationale,
        }


def plan_execution(
    algorithm_id: str,
    scale: ScaleProfile,
    *,
    memory_budget_bytes: Optional[int] = None,
    algorithm_registry: Any = None,
) -> ExecutionPlan:
    """规模画像 → 执行方式规划（纯函数；select_backend 之上的投影）。

    - 变体选择完全复用 :func:`select_backend`（单一事实源，不重判）；
    - ``mode``：批量变体 → ``vectorized``；内存估算超
      ``memory_budget_bytes``（缺省 2 GiB，与 _MEMORY_NOTE_BYTES 同底）
      → ``chunked`` + 建议分块形状（envelope 线性系数反解）；
    - 建议不执行——真实分块由实现层承担（cancellation/chunk 语义不变）。
    """
    decision = select_backend(
        algorithm_id, scale, algorithm_registry=algorithm_registry)
    budget = memory_budget_bytes if memory_budget_bytes is not None \
        else _MEMORY_NOTE_BYTES
    mode = "native"
    if "batched" in str(decision.variant_id):
        mode = "vectorized"

    chunk_shape: Optional[Dict[str, int]] = None
    memory_note = ""
    est = decision.estimated_bytes if decision.estimated_bytes is not None \
        else scale.estimated_bytes
    if est is not None and est > budget:
        mode = "chunked"
        memory_note = (
            f"内存估算 {est / 1024**3:.2f} GiB > 预算 "
            f"{budget / 1024**3:.0f} GiB——建议分块（实现层硬闸不变）")
        registry = algorithm_registry if algorithm_registry is not None \
            else _registry()
        descriptor = registry.get(algorithm_id)
        envelope = getattr(descriptor, "resource_envelope", None)
        if envelope is not None:
            n = scale.feature_count
            cells = scale.raster_cells
            if cells and envelope.bytes_per_cell:
                chunk_shape = {"chunk_cells": max(
                    1, int(budget // max(envelope.bytes_per_cell, 1.0)))}
            elif n and envelope.bytes_per_feature:
                chunk_shape = {"chunk_features": max(
                    1, int(budget // max(envelope.bytes_per_feature, 1.0)))}

    rationale = decision.rationale
    if memory_note:
        rationale = f"{rationale}; {memory_note}"[:_RATIONALE_MAX]
    return ExecutionPlan(
        algorithm_id=algorithm_id,
        variant_id=decision.variant_id,
        backend=decision.backend,
        mode=mode,
        matched=decision.matched,
        chunk_shape=chunk_shape,
        memory_note=memory_note,
        approximation_disclosure=decision.approximation_disclosure,
        rationale=rationale,
    )


def _registry() -> Any:
    from app.lib.gis.algorithm_registry import get_algorithm_registry

    return get_algorithm_registry()
