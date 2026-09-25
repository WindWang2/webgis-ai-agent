"""确定性编译器：MapPlanIR → 有序最小 MapSpec mutations（F12 / ADR-0214 D3）。

纯函数、零 I/O、零 LLM。同输入（``ir.ir_fingerprint`` + base spec +
``base_revision``）字节级同输出：

- diff desired（IR 意图面）vs current（MapSpec 扁平真值）；
- 逐目标产**最小** mutation —— paint 只携带变化的顶层键、组件只携带
  变化字段、desired 已满足的目标产 no-op 记录而非 mutation；
- 相位排序：新增数据层 → 层表达面修正 → 组件补齐/修正 → 显式删除
  （保证删除永远最后，中途状态不引用已删对象）；
- 每步铸确定性 ``client_mutation_id = pmc.<ir_id>.<step>.<intent>``，
  引擎侧以 ``c:<id>`` 幂等去重 —— 同编译重放 = duplicate no-op。

现状组件图消费 ``app/lib/cartography/component_graph``（只读投影，不建
第二存储）；删除/隐藏等破坏性动作已在 obligations 层被 user-lock 闸
拦下，本模块对锁目标不再二次放行（防御性断言）。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple

from pydantic import BaseModel, ConfigDict, Field

from app.lib.cartography.component_graph import build_component_graph
from app.lib.cartography.plan_ir import MapPlanIR, spec_doc_of
from app.lib.cartography.quality_loop import cartographic_fingerprint
from app.services.map_plan_compiler.obligations import (
    ObligationReport,
    check_obligations,
)

__all__ = ["PlanMutation", "NoOpRecord", "DisplayExpectation", "PlanCompilation",
           "compile_plan"]

#: 组件默认锚位（与 grammar_solver._DEFAULT_ZONES 同惯例；user pin 优先）。
_DEFAULT_COMPONENT_ZONES: Dict[str, str] = {
    "title": "top-center",
    "subtitle": "top-center",
    "legend": "top-left",
    "continuous_colorbar": "top-left",
    "scale_bar": "bottom-left",
    "north_arrow": "top-right",
    "attribution": "bottom-right",
}

_PHASE_PRESENT_LAYERS = 0
_PHASE_LAYER_PATCHES = 1
_PHASE_COMPONENTS = 2
_PHASE_REMOVALS = 3
_PHASE_COUNT = 4


class PlanMutation(BaseModel):
    """一条编译产物 mutation（可序列化、确定性 id、经引擎 CAS 提交）。"""

    model_config = ConfigDict(frozen=True)

    step: int = Field(ge=0)  # 0 = 构造占位；收尾统一铸号（≥1）
    phase: int = Field(ge=0, lt=_PHASE_COUNT)
    client_mutation_id: str = Field(min_length=8, max_length=128,
                                    pattern=r'^[A-Za-z0-9._:-]+$')
    intent: str = Field(min_length=1, max_length=48)
    target: str = Field(default="", max_length=200)
    payload: Dict[str, Any] = Field(default_factory=dict)
    reason_codes: List[str] = Field(default_factory=list, max_length=12)
    evidence_refs: List[str] = Field(default_factory=list, max_length=8)


class NoOpRecord(BaseModel):
    """desired 已满足的目标（证明性记录：最小 diff 的负空间）。"""

    model_config = ConfigDict(frozen=True)

    target: str = Field(max_length=200)
    intent_id: str = Field(default="", max_length=128)
    code: str = Field(default="ALREADY_SATISFIED", max_length=64)


class DisplayExpectation(BaseModel):
    """编译期解析的期望显示面（finalization 的输入，layer_id 已落定）。"""

    model_config = ConfigDict(frozen=True)

    layers: Dict[str, bool] = Field(default_factory=dict)   # layer_id → expected visible
    components: List[str] = Field(default_factory=list)     # 必须在场且 enabled 的 component_id


class PlanCompilation(BaseModel):
    """一次编译的完整产物（receipt 的上游；本身可 replay）。"""

    model_config = ConfigDict(frozen=True)

    ir_id: str
    ir_fingerprint: str
    supersedes: str = ""
    base_revision: int = Field(ge=0)
    base_fingerprint: str = Field(default="", max_length=96)
    obligations: ObligationReport
    status: str = Field(default="compiled", max_length=24)  # compiled|blocked
    mutations: List[PlanMutation] = Field(default_factory=list)
    no_ops: List[NoOpRecord] = Field(default_factory=list)
    display_expectations: DisplayExpectation = Field(default_factory=DisplayExpectation)
    component_graph_summary: Dict[str, Any] = Field(default_factory=dict)
    compile_digest: str = Field(default="", max_length=80)
    compile_id: str = Field(default="", max_length=48)
    disclosures: List[str] = Field(default_factory=list, max_length=12)
    reason_codes: List[str] = Field(default_factory=list, max_length=24)


# ── diff 原语 ────────────────────────────────────────────────────────────


def _layer_visible(layer: Dict[str, Any]) -> bool:
    """schema ``visible`` 布尔与 presentation patch 的 ``layout.visibility``
    双形态并存（upsert 写前者，patch 写后者）：任一声明隐藏即隐藏。"""
    if layer.get("visible") is False:
        return False
    layout = layer.get("layout") if isinstance(layer.get("layout"), dict) else {}
    return layout.get("visibility", "visible") != "none"


def _layer_opacity(layer: Dict[str, Any]) -> Optional[float]:
    paint = layer.get("paint") if isinstance(layer.get("paint"), dict) else {}
    value = paint.get("opacity")
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def _paint_delta(desired: Dict[str, Any], current: Dict[str, Any]) -> Dict[str, Any]:
    """paint 最小 delta：desired 中与 current 顶层键不同的条目（新键 +
    值变键；current 独有键**不触碰**）。"""
    delta: Dict[str, Any] = {}
    for key, value in (desired or {}).items():
        if key not in (current or {}) or current.get(key) != value:
            delta[str(key)[:48]] = value
    return delta


def _blueprint_layer_dict(ir_layer: Any) -> Dict[str, Any]:
    """LayerIntent + LayerBlueprint → 新建层 dict（确定性；无数据 payload）。"""
    bp = ir_layer.blueprint
    layer: Dict[str, Any] = {
        "id": ir_layer.layer_id,
        "source": ir_layer.source_ref,
        "type": bp.layer_type,
        "visible": bool(ir_layer.expected_visible),
    }
    if ir_layer.title:
        layer["name"] = ir_layer.title
    if bp.paint:
        layer["paint"] = dict(bp.paint)
    if bp.legend_spec:
        layer["legend_spec"] = dict(bp.legend_spec)
    elif bp.classification:
        layer["legend_spec"] = {"classification": dict(bp.classification)}
    if bp.label_spec:
        layer["label"] = dict(bp.label_spec)
    layer["cartographic_intent"] = {
        "expected_visible": bool(ir_layer.expected_visible),
        "source": "plan_compiler",
    }
    return layer


def _merged_layer_dict(current: Dict[str, Any], ir_layer: Any) -> Dict[str, Any]:
    """非 paint 层键（legend_spec/thresholds 等）变化时的最小合并 upsert：
    current 层 dict 原样保留，只覆写 delta 键（engine COW 只拷贝变更分支）。"""
    merged = dict(current)
    bp = ir_layer.blueprint
    if bp is None:
        return merged
    delta = _paint_delta(bp.paint or {}, current.get("paint") or {})
    if delta:
        merged["paint"] = {**(current.get("paint") or {}), **delta}
    desired_legend = dict(bp.legend_spec) if bp.legend_spec else (
        {"classification": dict(bp.classification)} if bp.classification else {}
    )
    if desired_legend and desired_legend != (current.get("legend_spec") or {}):
        merged["legend_spec"] = desired_legend
    if bp.label_spec and bp.label_spec != (current.get("label") or {}):
        merged["label"] = dict(bp.label_spec)
    return merged


def _component_position(ci: Any) -> str:
    return ci.pinned_zone or _DEFAULT_COMPONENT_ZONES.get(ci.component_type, "none")


def _component_delta(ci: Any, current: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """组件最小 patch 字段集（只含变化的键；无变化 = 空 dict）。"""
    delta: Dict[str, Any] = {}
    if current is None:
        delta["upsert"] = True
        delta["component_type"] = ci.component_type
        delta["position"] = _component_position(ci)
        delta["enabled"] = True
        options = dict(ci.options or {})
        if ci.title:
            options.setdefault("title", ci.title)
        if options:
            delta["options"] = options
        return delta
    cur_options = current.get("options") if isinstance(current.get("options"), dict) else {}
    desired_enabled = False if ci.action == "hide" else True
    if bool(current.get("enabled", True)) != desired_enabled:
        delta["enabled"] = desired_enabled
    desired_options = dict(ci.options or {})
    if ci.title:
        desired_options.setdefault("title", ci.title)
    option_delta = _paint_delta(desired_options, cur_options or {})
    if option_delta:
        delta["options"] = option_delta
    desired_position = _component_position(ci)
    if desired_position != "none" and current.get("position") != desired_position:
        delta["position"] = desired_position
    return delta


# ── 主编译 ───────────────────────────────────────────────────────────────


def compile_plan(
    ir: MapPlanIR,
    current: Optional[Dict[str, Any]],
    *,
    base_revision: int = 0,
    analysis_output_ids: Sequence[str] = (),
) -> PlanCompilation:
    """MapPlanIR + 当前 MapSpec → PlanCompilation（纯函数）。"""
    current = spec_doc_of(current)
    report = check_obligations(ir, current, analysis_output_ids=analysis_output_ids)
    base_fp = cartographic_fingerprint(current)
    ir_fp = ir.ir_fingerprint()

    def _empty(mutations: List[PlanMutation], no_ops: List[NoOpRecord],
               expectations: DisplayExpectation,
               graph_summary: Dict[str, Any]) -> PlanCompilation:
        return _finalize_compilation(ir, report, base_revision, base_fp, ir_fp,
                                     mutations, no_ops, expectations, graph_summary,
                                     status="blocked" if report.status == "blocked" else "compiled")

    layers_now: List[Dict[str, Any]] = [
        dict(l) for l in (current.get("layers") or []) if isinstance(l, dict)
    ]
    layout = current.get("layout") if isinstance(current.get("layout"), dict) else {}
    components_now: List[Dict[str, Any]] = [
        dict(c) for c in (layout.get("components") or []) if isinstance(c, dict)
    ]
    layer_by_id = {str(l.get("id")): l for l in layers_now if l.get("id")}
    component_by_id = {str(c.get("id")): c for c in components_now if c.get("id")}

    # 现状组件图（只读投影；供 graph summary 与排序参考，不建第二存储）
    try:
        graph = build_component_graph(current)
        graph_summary = {
            "nodes": len(graph.nodes),
            "links": len(graph.links),
        }
    except Exception:  # noqa: BLE001 — 图投影失败不阻塞编译（防御性）
        graph_summary = {"nodes": 0, "links": 0}

    if report.status == "blocked":
        return _empty([], [], DisplayExpectation(), graph_summary)

    present: List[PlanMutation] = []
    patches: List[PlanMutation] = []
    component_mutations: List[PlanMutation] = []
    removals: List[PlanMutation] = []
    no_ops: List[NoOpRecord] = []

    def _mk(phase: int, bucket: List[PlanMutation], intent: str, target: str,
            payload: Dict[str, Any], reason_codes: List[str],
            evidence: Sequence[str]) -> None:
        bucket.append(PlanMutation(
            step=0,  # 统一铸号在收尾
            phase=phase,
            client_mutation_id="pending-0",  # 占位（收尾按 step 重铸）
            intent=intent,
            target=target[:200],
            payload=payload,
            reason_codes=[str(r)[:64] for r in reason_codes[:12]],
            evidence_refs=[str(e)[:192] for e in list(evidence)[:8]],
        ))

    # ── 层意图（IR 顺序 = plan 权威顺序）────────────────────────────────
    for li in ir.layer_intents:
        tid = li.layer_id
        cur = layer_by_id.get(tid)
        ev = [f"layer_intent:{li.intent_id}"]

        if li.action == "remove":
            if cur is None:
                no_ops.append(NoOpRecord(target=f"layer:{tid}", intent_id=li.intent_id,
                                         code="ALREADY_ABSENT"))
            else:
                _mk(_PHASE_REMOVALS, removals, "remove_layer", tid,
                    {}, ["AMEND_REMOVE"], ev)
            continue

        if li.action.startswith("present_"):
            if cur is None:
                _mk(_PHASE_PRESENT_LAYERS, present, "upsert_layer", tid,
                    {"layer": _blueprint_layer_dict(li)}, ["PLAN_PRESENT"], ev)
                continue
            # 已在场 → 收敛为最小修正
        if li.action == "set_visibility" or li.action.startswith("present_") \
                or li.action == "restyle" or li.action == "rebind":
            if cur is None and li.action in ("set_visibility", "restyle"):
                no_ops.append(NoOpRecord(target=f"layer:{tid}", intent_id=li.intent_id,
                                         code="TARGET_ABSENT"))
                continue
            desired_visible = bool(li.expected_visible)
            desired_opacity = li.expected_opacity
            vis_changed = _layer_visible(cur) != desired_visible  # type: ignore[arg-type]
            opacity_changed = (
                desired_opacity is not None
                and abs((_layer_opacity(cur) if _layer_opacity(cur) is not None
                         else 1.0) - float(desired_opacity)) > 1e-9
            )
            if vis_changed or opacity_changed:
                payload: Dict[str, Any] = {}
                if vis_changed:
                    payload["visible"] = desired_visible
                if opacity_changed:
                    payload["opacity"] = float(desired_opacity)
                _mk(_PHASE_LAYER_PATCHES, patches, "patch_layer_presentation", tid,
                    payload, ["PLAN_PRESENTATION"], ev)
            bp = li.blueprint
            if bp is not None and (li.action == "restyle" or li.action.startswith("present_")):
                paint_delta = _paint_delta(bp.paint or {}, (cur or {}).get("paint") or {})
                desired_legend = dict(bp.legend_spec) if bp.legend_spec else (
                    {"classification": dict(bp.classification)} if bp.classification else {}
                )
                legend_changed = bool(desired_legend) and \
                    desired_legend != ((cur or {}).get("legend_spec") or {})
                label_changed = bool(bp.label_spec) and \
                    bp.label_spec != ((cur or {}).get("label") or {})
                if legend_changed or label_changed:
                    # 非 paint 键变化：引擎唯一 durable 通道 = 层级最小合并 upsert
                    merged = _merged_layer_dict(cur or {}, li)
                    _mk(_PHASE_LAYER_PATCHES, patches, "upsert_layer", tid,
                        {"layer": merged, "merge": True},
                        ["PLAN_LEGEND_MERGE"] if legend_changed else ["PLAN_LABEL_MERGE"], ev)
                elif paint_delta:
                    _mk(_PHASE_LAYER_PATCHES, patches, "patch_layer_style", tid,
                        {"paint": paint_delta}, ["PLAN_STYLE"], ev)
                elif not vis_changed and not opacity_changed:
                    no_ops.append(NoOpRecord(target=f"layer:{tid}", intent_id=li.intent_id))
        if li.action == "rebind" and cur is not None:
            # 换数据绑定：引擎唯一 durable 通道 = 层级最小合并 upsert（source 键覆写）
            if li.source_ref and li.source_ref != (cur or {}).get("source"):
                merged = dict(cur or {})
                merged["source"] = li.source_ref
                _mk(_PHASE_LAYER_PATCHES, patches, "upsert_layer", tid,
                    {"layer": merged, "merge": True}, ["PLAN_REBIND"], ev)
            else:
                no_ops.append(NoOpRecord(target=f"layer:{tid}", intent_id=li.intent_id))

    # ── 组件意图 ────────────────────────────────────────────────────────
    for ci in ir.component_intents:
        cid = ci.component_id
        cur = component_by_id.get(cid)
        ev = [f"component_intent:{ci.intent_id}"]
        if ci.action == "remove":
            if cur is None:
                no_ops.append(NoOpRecord(target=f"component:{cid}", intent_id=ci.intent_id,
                                         code="ALREADY_ABSENT"))
            else:
                _mk(_PHASE_REMOVALS, removals, "remove_component", cid, {},
                    ["AMEND_REMOVE"], ev)
            continue
        if ci.action == "hide" and cur is None:
            # 没有在场组件可藏 —— 记 no-op，绝不反向 upsert 一个隐藏组件
            no_ops.append(NoOpRecord(target=f"component:{cid}", intent_id=ci.intent_id,
                                     code="TARGET_ABSENT"))
            continue
        delta = _component_delta(ci, cur)
        if not delta:
            no_ops.append(NoOpRecord(target=f"component:{cid}", intent_id=ci.intent_id))
            continue
        payload = {"component_id": cid, **delta}
        _mk(_PHASE_COMPONENTS, component_mutations, "patch_component", cid,
            payload, ["PLAN_COMPONENT_ENSURE" if ci.action == "ensure" else "PLAN_COMPONENT_PATCH"],
            ev)

    # ── 汇总 + 统一铸号（相位内保持插入序；step 全局单调）───────────────
    ordered: List[PlanMutation] = []
    for phase_bucket in (present, patches, component_mutations, removals):
        ordered.extend(phase_bucket)
    final: List[PlanMutation] = []
    for i, m in enumerate(ordered, start=1):
        final.append(m.model_copy(update={
            "step": i,
            "client_mutation_id": f"pmc.{ir.ir_id}.{i:02d}.{m.intent}",
        }))

    expectations = DisplayExpectation(
        layers=_resolve_layer_expectations(ir, layer_by_id),
        components=[
            ci.component_id for ci in ir.component_intents
            if ci.required and ci.action == "ensure"
        ],
    )
    return _finalize_compilation(ir, report, base_revision, base_fp, ir_fp,
                                 final, no_ops, expectations, graph_summary,
                                 status="compiled")


def _resolve_layer_expectations(
    ir: MapPlanIR, layer_by_id: Dict[str, Dict[str, Any]],
) -> Dict[str, bool]:
    """intent_id → layer_id 落定期望可见性；remove 的层不进入期望面。"""
    out: Dict[str, bool] = {}
    for li in ir.layer_intents:
        if li.action == "remove":
            continue
        tid = li.layer_id or li.intent_id
        out[tid] = bool(li.expected_visible)
    return out


def _finalize_compilation(
    ir: MapPlanIR,
    report: ObligationReport,
    base_revision: int,
    base_fp: str,
    ir_fp: str,
    mutations: List[PlanMutation],
    no_ops: List[NoOpRecord],
    expectations: DisplayExpectation,
    graph_summary: Dict[str, Any],
    *,
    status: str,
) -> PlanCompilation:
    from app.lib.cartography.plan_ir import digest_of

    digest_payload = {
        "ir_fingerprint": ir_fp,
        "base_revision": int(base_revision),
        "base_fingerprint": base_fp,
        "mutations": [m.model_dump() for m in mutations],
        "no_ops": [n.model_dump() for n in no_ops],
        "expectations": expectations.model_dump(),
    }
    digest = digest_of(digest_payload)
    return PlanCompilation(
        ir_id=ir.ir_id,
        ir_fingerprint=ir_fp,
        supersedes=ir.supersedes,
        base_revision=int(base_revision),
        base_fingerprint=base_fp,
        obligations=report,
        status=status,
        mutations=mutations,
        no_ops=no_ops,
        display_expectations=expectations,
        component_graph_summary=graph_summary,
        compile_digest=digest[:40],
        compile_id=f"pmcc-{digest[:12]}",
        disclosures=list(ir.disclosures)[:12],
        reason_codes=(["OBLIGATIONS_BLOCKED"] if status == "blocked"
                      else ["PLAN_COMPILED"]) + list(report.reason_codes)[:23],
    )
