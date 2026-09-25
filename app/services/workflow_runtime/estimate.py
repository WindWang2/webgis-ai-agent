"""Workflow 节点 → rg.v1 ResourceEstimate 桥（ADR-0214 D1）。

#1484（ADR-0213）把估算统一到 ``governor.estimation`` 的先验表，但
workflow_runtime 节点执行面没有数值口径 —— 本桥是 workflow 节点的唯一
估算投影点，纪律与 ``gis_harness/estimate_bridge.py`` 相同：

- **先验表唯一驻留** ``governor.estimation``（``estimate_for_tool``/
  ``class_prior``）—— 本模块禁止出现第二份数值表；
- tool 面：``classify_tool`` + ``estimate_for_tool`` + DF cost 投影，与
  dispatch ``GovernorDispatchAdapter._build_demand`` **同一条路径** ——
  parity 不变式（同一工具/参数/证据 → dims 逐维全等 + resource_class 相同）
  由测试锁定；subsystem 归因诚实标注 ``Subsystem.WORKFLOW``；
- render 面：``render_budget.render_input_from_spec_summary``（既有投影）；
  export 面：``export_budget.export_work_from_summary``（ADR-0214 D7）；
- 节点声明覆盖（``resources.memory_class``/``latency_class``/
  ``estimated_memory_mb``/``estimated_wall_s``/``vram_bytes``）＝ 同表切片
  或显式申报，绝无隐式表；
- unknown 维（如声明 GPU 却无 VRAM 证据）→ 保守地板计费（unknown≠0）。

纯函数、确定性、零 IO —— 估算缺失时按 kind 保守档兜底，宁可粗而诚实。
"""
from __future__ import annotations

from typing import Any, Dict, Mapping, Optional

from app.services.governor.contract import (
    Dimension,
    DimValue,
    ExecutionPriority,
    ResourceClass,
    ResourceEstimate,
    Subsystem,
)
from app.services.governor.dispatch_adapter import (
    classify_tool,
    project_df_cost,
)
from app.services.governor.estimation import class_prior, estimate_for_tool

__all__ = [
    "resource_estimate_for_workflow_node",
    "execution_priority_for_node",
    "node_estimate_key",
    "node_declared_profile",
]

#: graph/workflow 声明词表 → 先验表档位（与 estimate_bridge 同一张表的键）
_LATENCY_TO_CLASS = {"fast": "light", "medium": "medium", "slow": "heavy"}
_MEMORY_CLASSES = ("light", "medium", "heavy")

#: heavy 档 profile 词表（与 dispatch.choose_dispatch 的 heavy 判定同源）
_HEAVY_PROFILES = ("raster", "heavy_cpu", "high_memory")

#: 输入行数 → wall 细化的保守吞吐先验（行/秒；provisional，仅兜底无证据时）
_ROWS_THROUGHPUT = 50_000.0


def node_declared_profile(node: Mapping[str, Any]) -> str:
    """节点声明的主 profile（与 dispatch.node_profile 同源语义）。"""
    resources = (node.get("resources") or {}) if isinstance(node, dict) else {}
    profile = str(resources.get("profile", "") or "") if isinstance(
        resources, dict) else ""
    return profile


def node_declared_cost(node: Mapping[str, Any]) -> str:
    """节点声明的 cost 档（light/medium/heavy；缺省按 profile/kind 推导）。"""
    resources = (node.get("resources") or {}) if isinstance(node, dict) else {}
    cost = ""
    if isinstance(resources, dict):
        cost = str(resources.get("cost", "") or "").strip().lower()
    if cost in ("light", "medium", "heavy"):
        return cost
    if node_declared_profile(node) in _HEAVY_PROFILES:
        return "heavy"
    kind = str((node.get("kind") or "")).lower() if isinstance(node, dict) else ""
    if "raster" in kind:
        return "heavy"
    if kind in ("analysis", "subworkflow"):
        return "medium"
    return "light"


def _declared_resources(node: Mapping[str, Any]) -> Dict[str, Any]:
    resources = (node.get("resources") or {}) if isinstance(node, dict) else {}
    return resources if isinstance(resources, dict) else {}


def _apply_declared_overrides(est: ResourceEstimate,
                              resources: Mapping[str, Any]) -> ResourceEstimate:
    """节点显式资源声明 → 同表切片/显式申报（estimate_bridge 同语义）。"""
    mem_class = str(resources.get("memory_class", "") or "").strip().lower()
    if mem_class in _MEMORY_CLASSES:
        m_lo, m_exp, m_hi = class_prior(mem_class)[:3]
        est = est.with_dim(
            Dimension.MEMORY_BYTES, DimValue.estimated(
                m_lo, m_exp, m_hi, confidence=0.6,
                source="declared:memory_class",
                reason=f"node declared memory_class={mem_class}"))
    lat = str(resources.get("latency_class", "") or "").strip().lower()
    if lat in _LATENCY_TO_CLASS:
        t_lo, t_exp, t_hi = class_prior(_LATENCY_TO_CLASS[lat])[3:]
        est = est.with_dim(
            Dimension.WALL_TIME_S, DimValue.estimated(
                t_lo, t_exp, t_hi, confidence=0.6,
                source="declared:latency_class",
                reason=f"node declared latency_class={lat}"))
    mem_mb = resources.get("estimated_memory_mb")
    if isinstance(mem_mb, (int, float)) and mem_mb > 0:
        exp = float(mem_mb) * 1024**2
        est = est.with_dim(
            Dimension.MEMORY_BYTES, DimValue.estimated(
                exp * 0.6, exp, exp * 2.5, confidence=0.6,
                source="declared:estimated_memory_mb",
                reason="node declared memory estimate"))
    wall_s = resources.get("estimated_wall_s")
    if isinstance(wall_s, (int, float)) and wall_s > 0:
        exp = float(wall_s)
        est = est.with_dim(
            Dimension.WALL_TIME_S, DimValue.estimated(
                exp * 0.5, exp, exp * 2.0, confidence=0.6,
                source="declared:estimated_wall_s",
                reason="node declared wall estimate"))
    return est


def _refine_gpu(est: ResourceEstimate,
                resources: Mapping[str, Any]) -> ResourceEstimate:
    """GPU 声明 → gpu_required + VRAM 维（无证据 = unknown 保守地板）。"""
    gpu = resources.get("gpu_required")
    if not isinstance(gpu, bool):
        return est
    est = est.model_copy(update={"gpu_required": gpu})
    if not gpu:
        return est
    vram = resources.get("vram_bytes")
    if isinstance(vram, (int, float)) and vram > 0:
        return est.with_dim(
            Dimension.GPU_MEMORY_BYTES, DimValue.known(
                float(vram), source="declared:vram_bytes"))
    # 声明要 GPU 却无 VRAM 证据：unknown≠0（准入按 1GiB 保守地板计）
    return est.with_dim(
        Dimension.GPU_MEMORY_BYTES,
        DimValue.unknown("gpu declared without vram_bytes evidence"))


def _refine_input_rows(est: ResourceEstimate, input_rows: int) -> ResourceEstimate:
    """输入身份行数 → FEATURE_COUNT / WALL 兜底细化（仅在无更强证据时）。

    parity 语义：dispatch 面可见的证据（args hint / DF cost）优先 —— 已有
    FEATURE_COUNT 时行数不覆盖（同工具同证据下与 dispatch 逐维全等）。
    """
    if input_rows <= 0:
        return est
    feats = est.dim(Dimension.FEATURE_COUNT)
    if not feats.is_meaningful() or feats.certainty.value == "unavailable":
        est = est.with_dim(
            Dimension.FEATURE_COUNT, DimValue.estimated(
                input_rows * 0.8, float(input_rows), input_rows * 1.2,
                confidence=0.6, source="workflow:input_identity",
                reason="input content identity rows"))
    wall = est.dim(Dimension.WALL_TIME_S)
    if not wall.is_meaningful() or wall.certainty.value == "unavailable":
        exp = max(0.5, input_rows / _ROWS_THROUGHPUT)
        est = est.with_dim(
            Dimension.WALL_TIME_S, DimValue.estimated(
                exp * 0.5, exp, exp * 2.0, confidence=0.4,
                source="workflow:rows_throughput_prior",
                reason="rows / provisional throughput prior"))
    return est


def _tool_backed_estimate(node: Mapping[str, Any], *,
                          tool_name: str, cost: str,
                          params: Mapping[str, Any]) -> ResourceEstimate:
    """capability 节点 → dispatch 同路径估算（parity 的唯一事实来源）。"""
    subsystem, rclass = classify_tool(tool_name, cost)
    args = dict(params) if isinstance(params, Mapping) else {}
    df_cost = (
        project_df_cost(args)
        if subsystem in (Subsystem.DATA_FABRIC, Subsystem.DOWNLOAD)
        else None
    )
    est = estimate_for_tool(
        tool_name, tool_class=cost, subsystem=subsystem, args=args,
        df_cost=df_cost,
    )
    # 与 dispatch._build_demand 收尾同款：resource_class 以分类词表为准
    return est.model_copy(update={"resource_class": rclass})


def _kind_fallback_estimate(node: Mapping[str, Any], *, kind: str,
                            cost: str) -> ResourceEstimate:
    """无 capability/render/export 声明 → kind 保守档（诚实披露来源）。"""
    est = estimate_for_tool(
        f"workflow:{kind}", tool_class=cost, subsystem=Subsystem.WORKFLOW,
    )
    if cost == "heavy":
        est = est.model_copy(update={"resource_class": ResourceClass.RASTER})
    return est


def resource_estimate_for_workflow_node(
    node: Mapping[str, Any],
    *,
    input_rows: int = 0,
    params: Optional[Mapping[str, Any]] = None,
) -> ResourceEstimate:
    """workflow 节点 → rg.v1 数值估算（唯一桥；纯函数，无 IO）。

    优先级：显式 export/render 声明 > capability（工具同路径）> kind
    保守档；其后叠加节点声明覆盖、GPU 细化、输入身份兜底。
    """
    if not isinstance(node, Mapping):
        node = {}
    kind = str(node.get("kind") or "")
    resources = _declared_resources(node)
    params = params if isinstance(params, Mapping) else {}
    cost = node_declared_cost(node)

    export_shape = resources.get("export")
    render_shape = resources.get("render")
    capability = str(node.get("capability") or node.get("algorithm_id")
                     or "").strip()

    if export_shape is not None or kind == "export":
        from app.services.governor.export_budget import (
            export_work_from_summary,
            estimate_export,
        )

        shape = export_shape if isinstance(export_shape, Mapping) else {}
        est = estimate_export(export_work_from_summary(dict(shape)))
        est = est.model_copy(update={
            "subsystem": Subsystem.WORKFLOW,
            "reason": f"workflow export node; {est.reason}",
        })
        return _refine_gpu(_apply_declared_overrides(est, resources),
                           resources)

    if render_shape is not None or kind == "cartography":
        from app.services.governor.render_budget import (
            estimate_render,
            render_input_from_spec_summary,
        )

        shape = render_shape if isinstance(render_shape, Mapping) else {}
        # 未声明时以 params 兜底（cartography 节点常把摘要放参数里）
        summary = dict(shape) or dict(params)
        est = estimate_render(render_input_from_spec_summary(summary))
        est = est.model_copy(update={
            "subsystem": Subsystem.WORKFLOW,
            "reason": f"workflow render node; {est.reason}",
        })
        return _refine_gpu(_apply_declared_overrides(est, resources),
                           resources)

    if capability:
        est = _tool_backed_estimate(
            node, tool_name=capability, cost=cost, params=params)
        est = est.model_copy(update={
            "subsystem": Subsystem.WORKFLOW,
            "source": f"{est.source};workflow_node",
        })
        est = _apply_declared_overrides(est, resources)
        est = _refine_gpu(est, resources)
        return _refine_input_rows(est, input_rows)

    est = _apply_declared_overrides(
        _kind_fallback_estimate(node, kind=kind or "transform", cost=cost),
        resources)
    est = _refine_gpu(est, resources)
    return _refine_input_rows(est, input_rows)


def execution_priority_for_node(node: Mapping[str, Any]) -> ExecutionPriority:
    """节点 priority（-10..10，默认 5）→ governor ExecutionPriority。

    确定性有界映射：>=8 互动档（用户在等）、<=2 批处理档（离线/导出）、
    其余 NORMAL。映射只影响公平排队的权重/ aging，绝不越过准入裁决。
    """
    raw = 5
    if isinstance(node, Mapping):
        try:
            raw = int(node.get("priority", 5))
        except (TypeError, ValueError):
            raw = 5
    if raw >= 8:
        return ExecutionPriority.INTERACTIVE
    if raw <= 2:
        return ExecutionPriority.BATCH
    return ExecutionPriority.NORMAL


def node_estimate_key(node: Mapping[str, Any]) -> str:
    """校准键（bounded；CalibrationStore 的 workflow 域命名空间）。"""
    kind = str(node.get("kind", "") or "")[:32] if isinstance(node, Mapping) else ""
    capability = str(node.get("capability") or node.get("algorithm_id")
                     or "")[:64] if isinstance(node, Mapping) else ""
    return f"workflow:{kind}:{capability}"[:128]
