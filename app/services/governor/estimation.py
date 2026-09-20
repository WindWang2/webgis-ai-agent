"""统一成本估算投影（R2，ADR-0182 D2/D4）。

把各域**已有的**成本语义投影为 governor 契约的 :class:`ResourceEstimate`：

- 数据获取 → 消费 Data Fabric ``estimate_cost``（ADR-0173，不重做成本模型）；
- 工具执行 → ``ToolCost``（light/medium/heavy）档位先验 + 参数细化；
- 制图渲染 → :mod:`governor.render_budget` 纯函数估工；
- LLM token → 调用方供给 token 数（context_budget 单一估算语义），
  governor 只做定价投影。

估算允许粗糙，但必须 evidence-backed：每维带 confidence + range + reason +
source（``estimated_memory: min/expected/max`` 优于伪精确）。校准脚本
（R17）产出的实测分布会以 ``measured`` 先验回填 —— V1 全部为 provisional
先验，provisional 预算模式（只告警不硬拒）兜底。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from app.services.governor.contract import (
    Dimension,
    DimValue,
    RasterWindow,
    ResourceClass,
    ResourceEstimate,
    Subsystem,
)
from app.services.governor.render_budget import RenderWorkInput, estimate_render

logger = logging.getLogger(__name__)

#: 档位先验版本（进 estimate.source）
TOOL_PRIOR_VERSION = "tool_prior.v1"
PRICE_TABLE_VERSION = "llm_price.v1"

#: ToolCost 档位 → (mem_lo, mem_exp, mem_hi, t_lo, t_exp, t_hi)
#: 证据：TOOL_TIMEOUT_S=300 封顶；heavy 占 2 wave 槽的调度现实；
#: provisional —— 校准脚本回填。
_TOOL_CLASS_PRIOR: Dict[str, Tuple[float, float, float, float, float, float]] = {
    "light": (16 * 1024**2, 48 * 1024**2, 128 * 1024**2, 0.05, 0.5, 2.0),
    "medium": (64 * 1024**2, 256 * 1024**2, 768 * 1024**2, 1.0, 5.0, 30.0),
    "heavy": (256 * 1024**2, 1.2 * 1024**3, 4 * 1024**3, 10.0, 60.0, 300.0),
}

#: LLM 定价先验（USD / 1k tokens，组合 input+output 粗口径；provisional）。
#: 只用于预算权重与 cost 维展示 —— 绝不做计费。
_LLM_USD_PER_1K_TOKENS = 0.002

#: 参数白名单：feature 语义的参数键（args 细化用）
_FEATURE_HINT_KEYS = ("limit", "feature_limit", "max_features", "maxfeat", "k")
_PIXEL_HINT_KEYS = ("width", "height", "size", "resolution", "zoom")
_DPI_KEYS = ("dpi", "export_dpi")


@dataclass
class DfCostView:
    """Data Fabric cost model 的窄投影（duck-typed，不 import DF 内部类型）。

    ``rows``/``bytes``/``latency_s`` 来自 ``planning/cost_model.estimate_cost``
    的既有产物；provider 自有估算是 Data Supply owner 的职责。
    """

    rows: Optional[float] = None
    bytes: Optional[float] = None
    latency_s: Optional[float] = None
    source: str = "df.cost_model.v1"


#: ResourceClass 严重度序（ADR-0204 review P1-1：字符串字典序与资源档
#: 无关，max by .value 会把全 heavy 聚合折算成 LIGHT）。
_RCLASS_SEVERITY = {
    ResourceClass.LIGHT: 0,
    ResourceClass.MEDIUM: 1,
    ResourceClass.HEAVY: 2,
    ResourceClass.RASTER: 3,
    ResourceClass.BROWSER: 3,
    ResourceClass.EXPORT: 3,
    ResourceClass.LLM: 3,
}


def _prior(tool_class: str) -> Tuple[float, float, float, float, float, float]:
    return _TOOL_CLASS_PRIOR.get(
        (tool_class or "light").strip().lower(), _TOOL_CLASS_PRIOR["light"]
    )


def class_prior(tool_class: str) -> Tuple[float, float, float, float, float, float]:
    """档位 → (mem_lo, mem_exp, mem_hi, t_lo, t_exp, t_hi) 先验（公共访问器）。

    唯一先验表纪律（ADR-0204 D1）：harness 侧桥/投影只经本访问器消费
    档位数值，禁止复制第二份表。
    """
    return _prior(tool_class)


def _resource_class_for(tool_class: str, subsystem: Subsystem) -> ResourceClass:
    if subsystem in (Subsystem.BROWSER,):
        return ResourceClass.BROWSER
    if subsystem in (Subsystem.EXPORT,):
        return ResourceClass.EXPORT
    if subsystem in (Subsystem.RASTER_COMPUTE, Subsystem.REMOTE_SENSING):
        return ResourceClass.RASTER
    if subsystem in (Subsystem.LLM_CONTEXT, Subsystem.VLM_JUDGE):
        return ResourceClass.LLM
    tc = (tool_class or "light").lower()
    if tc == "heavy":
        return ResourceClass.HEAVY
    if tc == "medium":
        return ResourceClass.MEDIUM
    return ResourceClass.LIGHT


def estimate_for_tool(
    tool_name: str,
    *,
    tool_class: str = "light",
    subsystem: Subsystem = Subsystem.TOOL_DISPATCH,
    args: Optional[Dict[str, Any]] = None,
    df_cost: Optional[DfCostView] = None,
    render_input: Optional[RenderWorkInput] = None,
    context_tokens: Optional[int] = None,
    output_tokens: Optional[int] = None,
    browser_required: Optional[bool] = None,
    gpu_required: Optional[bool] = None,
) -> ResourceEstimate:
    """工具级 ResourceEstimate：档位先验 + 可用细化证据。

    细化优先级：DF cost > 参数 hint > 档位先验。所有路径产出的都是 range，
    confidence 随证据分层：先验 0.4 / +参数 0.5 / +DF 成本 0.7。
    """
    args = args if isinstance(args, dict) else {}
    mem_lo, mem_exp, mem_hi, t_lo, t_exp, t_hi = _prior(tool_class)
    rclass = _resource_class_for(tool_class, subsystem)
    confidence = 0.4
    sources: List[str] = [f"{TOOL_PRIOR_VERSION}:{tool_class or 'light'}"]
    notes: List[str] = [f"class={tool_class or 'light'}"]

    dims: Dict[Dimension, DimValue] = {
        Dimension.MEMORY_BYTES: DimValue.estimated(
            mem_lo, mem_exp, mem_hi, confidence=confidence,
            source=sources[-1], reason=f"tool prior for {tool_name}",
        ),
        Dimension.WALL_TIME_S: DimValue.estimated(
            t_lo, t_exp, t_hi, confidence=confidence,
            source=sources[-1], reason=f"tool prior for {tool_name}",
        ),
    }

    # ---- 参数 hint 细化 -----------------------------------------------------
    feature_hint = _first_numeric(args, _FEATURE_HINT_KEYS)
    if feature_hint is not None and feature_hint > 0:
        dims[Dimension.FEATURE_COUNT] = DimValue.estimated(
            min(feature_hint, 1000), feature_hint, feature_hint * 1.2,
            confidence=0.6, source="args:feature_hint",
            reason=f"arg hint feature cap {feature_hint:g}",
        )
        notes.append(f"feat<={feature_hint:g}")
        confidence = max(confidence, 0.5)

    pixel_hint = _pixel_hint(args)
    if pixel_hint is not None:
        px = max(1.0, float(pixel_hint))
        dims[Dimension.PIXEL_COUNT] = DimValue.estimated(
            px * 0.5, px, px * 2.0,
            confidence=0.5, source="args:pixel_hint",
            reason="extent/size arg derivation",
        )
        confidence = max(confidence, 0.5)

    # ---- DF 成本投影（owner 语义，只消费）------------------------------------
    if df_cost is not None:
        if df_cost.rows is not None:
            dims[Dimension.FEATURE_COUNT] = DimValue.estimated(
                df_cost.rows * 0.6, df_cost.rows, df_cost.rows * 1.5,
                confidence=0.75, source=df_cost.source,
                reason="data fabric cost model rows projection",
            )
        if df_cost.bytes is not None:
            dims[Dimension.NETWORK_BYTES] = DimValue.estimated(
                df_cost.bytes * 0.6, df_cost.bytes, df_cost.bytes * 1.5,
                confidence=0.75, source=df_cost.source,
                reason="data fabric cost model bytes projection",
            )
        if df_cost.latency_s is not None:
            dims[Dimension.WALL_TIME_S] = DimValue.estimated(
                df_cost.latency_s * 0.7, df_cost.latency_s, df_cost.latency_s * 2.0,
                confidence=0.7, source=df_cost.source,
                reason="data fabric cost model latency projection",
            )
        confidence = max(confidence, 0.7)
        sources.append(df_cost.source)

    # ---- render 细化（R13）--------------------------------------------------
    if render_input is not None:
        rest = estimate_render(render_input)
        dims[Dimension.RENDER_WORK_UNITS] = rest.dim(Dimension.RENDER_WORK_UNITS)
        dims[Dimension.MEMORY_BYTES] = rest.dim(Dimension.MEMORY_BYTES)
        dims[Dimension.WALL_TIME_S] = rest.dim(Dimension.WALL_TIME_S)
        confidence = max(confidence, 0.55)
        sources.append(rest.source)
        browser_required = True if browser_required is None else browser_required

    # ---- LLM token 投影（R12 协同面）----------------------------------------
    if context_tokens is not None or output_tokens is not None:
        ctx = float(context_tokens or 0)
        out = float(output_tokens or 0)
        dims[Dimension.CONTEXT_TOKENS] = DimValue.known(ctx, source="caller:context_budget")
        dims[Dimension.OUTPUT_TOKENS] = DimValue.estimated(
            out * 0.5, out, out * 2.0 if out else 4096.0,
            confidence=0.5, source="caller:output_hint",
        )
        dims[Dimension.ESTIMATED_LLM_COST] = DimValue.estimated(
            0.0, (ctx + out) / 1000.0 * _LLM_USD_PER_1K_TOKENS,
            (ctx + out * 2.0) / 1000.0 * _LLM_USD_PER_1K_TOKENS,
            confidence=0.4, source=PRICE_TABLE_VERSION,
            reason="provisional combined price; budget weight only, never billing",
        )

    return ResourceEstimate(
        subsystem=subsystem,
        resource_class=rclass,
        dims=dims,
        cpu_class="heavy_cpu" if rclass is ResourceClass.HEAVY else "light",
        io_class="network_io" if df_cost is not None else "local_io",
        gpu_required=gpu_required,
        browser_required=browser_required,
        confidence=confidence,
        source=";".join(sources),
        reason=";".join(notes),
    )


def sum_estimates(parts: List[ResourceEstimate], *,
                  subsystem: Subsystem = Subsystem.TOOL_DISPATCH) -> ResourceEstimate:
    """计划级聚合（R4/R8）：逐维 expected 相加、range 收敛加宽、confidence 取短板。

    unknown 维参与求和（保守地板），unavailable 维跳过 —— 与单维 adjudged
    语义一致；聚合结果标注 source=plan_sum。
    """
    if not parts:
        return ResourceEstimate(subsystem=subsystem, source="plan_sum:empty")
    dims: Dict[Dimension, DimValue] = {}
    all_dims: set = set()
    for p in parts:
        all_dims.update(p.dims.keys())
    for d in all_dims:
        lo = exp = hi = 0.0
        conf: Optional[float] = None
        any_meaningful = False
        for p in parts:
            dv = p.dims.get(d)
            if dv is None or not dv.is_meaningful():
                continue
            any_meaningful = True
            lo += dv.min if dv.min is not None else (dv.expected or 0.0)
            exp += dv.expected if dv.expected is not None else (dv.max or 0.0)
            hi += dv.max if dv.max is not None else (dv.expected or 0.0)
            if dv.confidence is not None:
                conf = dv.confidence if conf is None else min(conf, dv.confidence)
        if any_meaningful:
            dims[d] = DimValue.estimated(
                lo, exp, hi,
                confidence=conf if conf is not None else 0.4,
                source="plan_sum",
                reason=f"aggregate of {len(parts)} estimates",
            )
    confs = [p.overall_confidence() for p in parts]
    return ResourceEstimate(
        subsystem=subsystem,
        resource_class=max((p.resource_class for p in parts),
                           key=lambda rc: _RCLASS_SEVERITY.get(rc, 0)),
        dims=dims,
        confidence=min(confs) if confs else 0.4,
        source="plan_sum",
        reason=f"sum of {len(parts)} step estimates (shortboard confidence)",
    )


def _first_numeric(args: Dict[str, Any], keys: Tuple[str, ...]) -> Optional[float]:
    for k in keys:
        v = args.get(k)
        if isinstance(v, (int, float)) and v > 0:
            return float(v)
    return None


def _pixel_hint(args: Dict[str, Any]) -> Optional[float]:
    w = args.get("width")
    h = args.get("height")
    if isinstance(w, (int, float)) and isinstance(h, (int, float)) and w > 0 and h > 0:
        return float(w) * float(h)
    for k in _PIXEL_HINT_KEYS:
        v = args.get(k)
        if isinstance(v, (int, float)) and v > 0:
            # 单值（如 resolution/size）→ 平方近似
            return float(v) ** 2
    return None


def raster_window_from_args(args: Dict[str, Any]) -> Optional[RasterWindow]:
    """args → RasterWindow（width/height/bands 可解析时；否则 None）。"""
    w = args.get("width")
    h = args.get("height")
    if isinstance(w, int) and isinstance(h, int) and w > 0 and h > 0:
        bands = args.get("bands") if isinstance(args.get("bands"), int) else 1
        return RasterWindow(width=w, height=h, bands=max(1, bands))
    return None


__all__ = [
    "TOOL_PRIOR_VERSION",
    "class_prior",
    "PRICE_TABLE_VERSION",
    "DfCostView",
    "estimate_for_tool",
    "sum_estimates",
    "raster_window_from_args",
]
