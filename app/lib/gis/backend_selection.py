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

from dataclasses import dataclass
from typing import Any, Dict, Optional

from app.lib.gis.cost_model import (
    infer_execution_policy,
    resolve_runtime_strategy,
    scale_tier,
)

_RATIONALE_MAX = 160


@dataclass(frozen=True)
class ScaleProfile:
    """触发 backend 选择的规模画像（全部可缺省 —— 缺省按 unknown 处理）。

    ``raster_cells`` 目前是**保留字段**：变体规模窗口只按
    ``feature_count`` 匹配（与 BackendVariant.min/max_features 的语义
    一致）；栅格类调用方应把像元规模折算进 feature_count 或直接以
    feature_count 传入。未消费前不参与任何判定（评审 R1 MINOR-3）。
    """

    feature_count: Optional[int] = None
    raster_cells: Optional[int] = None
    export: bool = False


@dataclass(frozen=True)
class BackendDecision:
    """一次确定的 backend 选择（可解释、可进证据、可测试）。"""

    algorithm_id: str
    variant_id: str          # "" = 算法未声明变体（默认工具路径）
    backend: str             # "" = 同上；否则 BACKEND_VOCABULARY 成员
    scale_tier: str          # cost_model.scale_tier 词表
    matched: bool            # True = n 落在某变体声明的规模窗口内
    rationale: str           # 选择理由（≤160，进证据）
    execution_policy: str    # cost_model 执行策略词表
    runtime_strategy: str    # cost_model 运行通道词表（heavy → 服务端建议）

    def to_diagnostic(self) -> Dict[str, Any]:
        """→ ScientificEvidenceBuilder diagnostics 条目形状。"""
        text = f"variant={self.variant_id or '(default)'}; {self.rationale}"
        return {
            "name": "backend_selection",
            "value": self.variant_id or "default",
            "text": text[:_RATIONALE_MAX],
        }


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
            algorithm_id=algorithm_id, variant_id="", backend="", scale_tier=tier,
            matched=False,
            rationale="no_variants_declared: 算法未声明实现变体，走默认工具路径",
            execution_policy=policy, runtime_strategy=strategy,
        )

    if n is None:
        chosen = variants[0]
        rationale = (
            f"scale_unknown: 特征数未知，按声明序偏好取 {chosen.id}（窗口待运行时复核）")
    else:
        hits = [v for v in variants if _window_contains(v, n)]
        if hits:
            chosen = hits[0]
            rationale = (
                f"n={n} 落入 {chosen.id} 声明窗口 "
                f"[{chosen.min_features}, {chosen.max_features}]（声明序优先）")
        else:
            unbounded = [v for v in variants if v.max_features is None]
            chosen = unbounded[0] if unbounded else variants[0]
            rationale = (
                f"n={n} 不在任何变体窗口内，降级取 {chosen.id}（无界上界优先）——"
                "科学语义不变，性能预算自行声明")

    return BackendDecision(
        algorithm_id=algorithm_id,
        variant_id=chosen.id,
        backend=str(chosen.backend),
        scale_tier=tier,
        matched=bool(
            n is not None and _window_contains(chosen, n)
        ) if n is not None else False,
        rationale=rationale[:_RATIONALE_MAX],
        execution_policy=policy,
        runtime_strategy=strategy,
    )
