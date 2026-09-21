"""GraphNode → rg.v1 ResourceEstimate 桥（R1/R4，ADR-0213 D1）。

`estimate_for_node`（规划面分类档位）与 `estimate_for_tool`（派发面数值
估算）曾是两套零互引用的口径 —— 本桥是**唯一**连接面：

- 先验数值只来自 ``governor.estimation``（``class_prior`` 公共访问器），
  禁止第二份表；
- tool 面：``classify_tool`` + ``estimate_for_tool``（与 dispatch
  ``_build_demand`` 同一条路径 —— parity 不变式的基础），graph 声明的
  ``latency_class``/``memory_class`` 作为**同表覆盖**（档位 → 同一先验
  元组的对应切片）；
- model 面：heavy 先验 + provider→gpu 推断；VRAM 在 graph 摘要缺席时是
  **unknown 维**（准入按保守地板 1GiB 计，绝不静默 0）；
- algorithm 面：complexity → 档位 → 同表先验。

分类档位投影（``latency_class_of`` / ``memory_class_of``）从 rg.v1 range
反推档位，阈值取先验表边界（light hi=2s/128MiB、medium hi=30s/768MiB），
保证「声明档位 → 数值 → 反推档位」恒等往返。
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from app.services.gis_harness.capability_graph import (
    KIND_ALGORITHM,
    KIND_MODEL,
    KIND_TOOL,
    GraphNode,
)
from app.services.governor.contract import (
    Dimension,
    DimValue,
    ResourceClass,
    ResourceEstimate,
    Subsystem,
)
from app.services.governor.dispatch_adapter import classify_tool
from app.services.governor.estimation import class_prior, estimate_for_tool

__all__ = [
    "resource_estimate_for_node",
    "latency_class_of",
    "memory_class_of",
    "current_resource_pressure",
]

#: graph 声明词表 → 先验表档位（同一张表的键）
_LATENCY_TO_CLASS = {"fast": "light", "medium": "medium", "slow": "heavy"}
_MEMORY_CLASSES = ("light", "medium", "heavy")

#: 档位反推阈值（= 先验表边界；改表必须同步 —— 由 parity/roundtrip 测试锁定）
_WALL_EXP_FAST_S = 2.0     # light hi
_WALL_EXP_MEDIUM_S = 30.0  # medium hi
_MEM_EXP_LIGHT = 128 * 1024**2
_MEM_EXP_MEDIUM = 768 * 1024**2


def resource_estimate_for_node(
    node: GraphNode,
    *,
    args: Optional[Dict[str, Any]] = None,
) -> ResourceEstimate:
    """graph 实体 → rg.v1 数值估算（唯一桥；纯函数，无 IO）。"""
    extras = getattr(node, "extras", None) or {}
    if node.kind == KIND_TOOL:
        cost = str(extras.get("cost") or "").strip().lower() or "light"
        subsystem, rclass = classify_tool(node.id, cost)
        est = estimate_for_tool(
            node.id, tool_class=cost, subsystem=subsystem, args=args,
        )
        est = est.model_copy(update={"resource_class": rclass})
        mem_class = str(extras.get("memory_class") or "").strip().lower()
        if mem_class in _MEMORY_CLASSES:
            m_lo, m_exp, m_hi = class_prior(mem_class)[:3]
            est = est.with_dim(
                Dimension.MEMORY_BYTES, DimValue.estimated(
                    m_lo, m_exp, m_hi, confidence=0.6,
                    source="declared:memory_class",
                    reason=f"graph declared memory_class={mem_class}"))
        lat = str(extras.get("latency_class") or "").strip().lower()
        if lat in _LATENCY_TO_CLASS:
            t_lo, t_exp, t_hi = class_prior(_LATENCY_TO_CLASS[lat])[3:]
            est = est.with_dim(
                Dimension.WALL_TIME_S, DimValue.estimated(
                    t_lo, t_exp, t_hi, confidence=0.6,
                    source="declared:latency_class",
                    reason=f"graph declared latency_class={lat}"))
        return est

    if node.kind == KIND_MODEL:
        provider = str(extras.get("provider_ref") or "")
        gpu = "gpu" in provider.lower() or "cuda" in provider.lower()
        est = estimate_for_tool(
            f"model:{node.id}", tool_class="heavy",
            subsystem=Subsystem.REMOTE_SENSING,
        )
        updates: Dict[str, Any] = {
            "resource_class": ResourceClass.RASTER,
            "gpu_required": gpu if provider else None,
        }
        est = est.model_copy(update=updates)
        # VRAM：graph 摘要通常只有 bands/provider —— 无声明即 unknown
        # （地板计费，不猜）；防御性读取未来可能出现的显式声明。
        vram = extras.get("vram_bytes")
        if isinstance(vram, (int, float)) and vram > 0:
            est = est.with_dim(
                Dimension.GPU_MEMORY_BYTES, DimValue.known(
                    float(vram), source="declared:vram_bytes"))
        else:
            est = est.with_dim(
                Dimension.GPU_MEMORY_BYTES,
                DimValue.unknown("vram not declared in graph summary"))
        return est

    if node.kind == KIND_ALGORITHM:
        complexity = str(extras.get("complexity") or "").strip().lower()
        cost = ("heavy" if complexity in ("high", "n_log_n_squared")
                else "medium" if complexity else "light")
        est = estimate_for_tool(
            f"algo:{node.id}", tool_class=cost,
            subsystem=Subsystem.VECTOR_COMPUTE,
        )
        return est

    # 未知 kind：light 保守档（诚实披露来源）
    return estimate_for_tool(
        f"{node.kind}:{node.id}", tool_class="light",
        subsystem=Subsystem.TOOL_DISPATCH,
    )


def latency_class_of(estimate: ResourceEstimate) -> str:
    """rg.v1 WALL_TIME_S range → fast/medium/slow（先验边界阈值）。

    unknown/unavailable → "slow" 保守档（规划面对未知宁可悲观）。
    """
    dv = estimate.dim(Dimension.WALL_TIME_S)
    if not dv.is_meaningful() or dv.certainty.value == "unknown":
        return "slow"
    exp = dv.expected if dv.expected is not None else (dv.max or 0.0)
    if exp <= _WALL_EXP_FAST_S:
        return "fast"
    if exp <= _WALL_EXP_MEDIUM_S:
        return "medium"
    return "slow"


def memory_class_of(estimate: ResourceEstimate) -> str:
    """rg.v1 MEMORY_BYTES range → light/medium/heavy（先验边界阈值）。"""
    dv = estimate.dim(Dimension.MEMORY_BYTES)
    if not dv.is_meaningful() or dv.certainty.value == "unknown":
        return "heavy"
    exp = dv.expected if dv.expected is not None else (dv.max or 0.0)
    if exp <= _MEM_EXP_LIGHT:
        return "light"
    if exp <= _MEM_EXP_MEDIUM:
        return "medium"
    return "heavy"


def current_resource_pressure() -> float:
    """governor live 内存 → [0,1] 压力标量（fail-open 0）。

    50% 水位以下 0（不影响排序），逼近 global_memory_pressure_bytes 时
    线性升至 1 —— planner 用它加权 memory_rank（R3）。同步读进程内
    ledger，无 IO。
    """
    try:
        from app.services.governor.dispatch_adapter import surface_enabled
        if not surface_enabled():
            # dispatch 面 governor 已关（GOVERNOR_TOOL_SURFACE=0）——
            # 不从侧门实例化 governor 单例（review P2-11）
            return 0.0
        from app.services.governor.config import get_governor_config
        from app.services.governor.governor import get_governor
        cap = float(get_governor_config().global_memory_pressure_bytes)
        if cap <= 0:
            return 0.0
        live = float(get_governor().ledger.live_memory_total())
        return min(1.0, max(0.0, live / cap - 0.5) * 2.0)
    except Exception:  # noqa: BLE001 — 反馈缺席中性
        return 0.0
