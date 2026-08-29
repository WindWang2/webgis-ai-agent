"""GIS Map Product Completion Runtime — ADR-0081.

确定性回答一个问题：**最终地图产品是否真的完成了？**

在此之前，系统的"完成"信号止步于行状态：所有能力行 complete（DAG
complete）即被认为任务可以结束（turn 在 ``agent_settled`` 收尾，对地图
产品零检查）。本模块把「DAG 完成」与「地图成品完成」拆开：

    mandatory DAG complete
            ↓
    Map Product Finalizer（本模块）
            ↓  artifact / layer / viewport / component / layout validators
    bounded repair（≤ MAX_FINALIZATION_PASSES 轮，只做确定性 desired-state 修复）
            ↓
    PASS → gis_chapter["map_product"] 披露 + 投影一行

边界（刻意收窄，全部为派生运行时逻辑）：
- 不 fork Pi、不建第二 agent loop —— 触发点在 harness（bridge 工具结果
  后 / turn settle），Pi 只看投影里的一行有界披露；
- 不新建第二 MapSpec / SessionPlan / runtime-layer truth —— 只读既有
  真相（章节扁平行 / MapSpec / session artifact descriptors），结果写回
  章节的 additive ``map_product`` 键；
- repair 绝不重跑 GIS 算法 —— 需要重新执行的发现以 ``needs_execution``
  披露，交还 DAG/重试语义裁决；
- 用户显式决策优先（user-wins）：结果层的显示修复走 GISMutationBatch
  的既有 owner 守卫，被拒即如实上报。

性能契约：普通地图 validation 毫秒级 —— 图层/组件校验 O(N)、布局校验
O(C²)（C = chrome 组件数，个位数量级）、bbox 全部来自既有 ref descriptor
元数据，不复制 GeoJSON、不逐 feature 扫描。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ── 契约常量（有界，确定性）─────────────────────────────────────────
MAX_FINALIZATION_PASSES = 2
MAX_FINDINGS = 12
MAX_FINDING_DETAIL = 160
MAX_DISCLOSED_REPAIRS = 6
RESULT_LAYER_ROLES = ("primary", "result")

# 完成态：pending（DAG 未终态）→ needs_repair（存在可修复发现）→
# complete（无 error 发现；warning 允许）/ failed（存在不可修复 error）。
STATUS_PENDING = "pending"
STATUS_NEEDS_REPAIR = "needs_repair"
STATUS_COMPLETE = "complete"
STATUS_FAILED = "failed"

# finding codes（machine-readable，前端/测试依赖这些字面量）
F_NEEDS_EXECUTION = "needs_execution"
F_ARTIFACT_MISSING = "artifact_missing"
F_ARTIFACT_EXPIRED = "artifact_expired"
F_EMPTY_RESULT = "empty_result"
F_NO_RESULT_LAYER = "no_result_layer"
F_LAYER_MISSING = "layer_missing"
F_SOURCE_MISSING = "source_missing"
F_LAYER_HIDDEN = "layer_hidden"
F_COMPONENT_MISSING = "component_missing"
F_COMPONENT_DISABLED = "component_disabled"
F_LAYOUT_CONFLICT = "layout_conflict"
F_ORPHAN_BINDING = "orphan_binding"
F_VIEWPORT_NO_BBOX = "viewport_no_bbox"

# repair action codes（repairs_applied 里的字面量）
R_ADD_COMPONENT = "add_component"
R_ENABLE_COMPONENT = "enable_component"
R_SHOW_LAYER = "show_layer"

# 组件 upsert 的默认 id（与 gis_harness.components 工厂一致；不引入第二
# 套默认值 —— 修复走 mutate_component 的同一工厂入口）。
_COMPONENT_DEFAULT_IDS: Dict[str, str] = {
    "title": "title",
    "subtitle": "subtitle",
    "legend": "legend-main",
    "categorical_legend": "legend-main",
    "continuous_colorbar": "colorbar-main",
    "scale_bar": "scale-bar",
    "north_arrow": "north-arrow",
    "attribution": "attribution",
    "statistics_panel": "statistics-panel",
    "chart_panel": "chart-panel",
}

# 单例组件（重复出现本身就是布局错误 —— 与 layout_constraints 同表）
_SINGLETON_TYPES = ("title", "subtitle", "north_arrow", "scale_bar", "attribution")


@dataclass
class MapCompletionFinding:
    """单条机器可读发现（bounded：detail 截断）。"""

    code: str
    severity: str  # "error" | "warning"
    target: str = ""
    detail: str = ""
    repair: Optional[str] = None  # 适用/已应用的 repair action code

    def to_dict(self) -> Dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity,
            "target": str(self.target)[:64],
            "detail": self.detail[:MAX_FINDING_DETAIL],
            "repair": self.repair,
        }


@dataclass
class MapCompletionResult:
    """完成态契约（deterministic / serializable / bounded）。

    不要求 LLM 解析长文本：``summary`` 单行有界，Pi 侧只消费投影行
    （``projection_line``），完整结构供 SessionPanel/测试/日志使用。
    """

    status: str = STATUS_PENDING
    findings: List[MapCompletionFinding] = field(default_factory=list)
    repairs_applied: List[str] = field(default_factory=list)
    viewport_status: str = "unknown"  # valid | repairable | invalid | not_applicable | unknown
    layer_status: str = "unknown"  # valid | issues | unknown
    component_status: str = "unknown"  # valid | issues | unknown
    export_status: str = "unknown"  # parity | divergent | unknown
    passes: int = 0
    result_bbox: Optional[List[float]] = None
    summary: str = ""

    # ── 派生 ─────────────────────────────────────────────────────────
    @property
    def error_findings(self) -> List[MapCompletionFinding]:
        return [f for f in self.findings if f.severity == "error"]

    @property
    def repairable_findings(self) -> List[MapCompletionFinding]:
        return [f for f in self.findings if f.repair is not None]

    def to_dict(self) -> Dict[str, Any]:
        """序列化投影（bounded：findings ≤ MAX_FINDINGS，repairs ≤ 6）。"""
        return {
            "status": self.status,
            "summary": self.summary[:120],
            "viewport_status": self.viewport_status,
            "layer_status": self.layer_status,
            "component_status": self.component_status,
            "export_status": self.export_status,
            "passes": self.passes,
            "result_bbox": self.result_bbox,
            "repairs": list(self.repairs_applied[:MAX_DISCLOSED_REPAIRS]),
            "issues": [f.to_dict() for f in self.findings[:MAX_FINDINGS]],
        }

    def projection_line(self) -> str:
        """Pi 投影行（单行、有界；只进 [GIS Plan] 块尾部）。"""
        codes = sorted({f.code for f in self.error_findings})[:3]
        tail = f" ({','.join(codes)})" if codes else ""
        if self.status == STATUS_COMPLETE:
            return "Map product: final"
        if self.status == STATUS_NEEDS_REPAIR:
            return f"Map product: needs repair{tail}"
        if self.status == STATUS_FAILED:
            return f"Map product: incomplete{tail}"
        return "Map product: pending"


# ── 输入聚合（一次读齐，validators 全部纯函数）───────────────────────


async def gather_completion_inputs(
    session_id: str,
    chapter: Dict[str, Any],
    *,
    mapspec: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """读取完成度校验所需的全部既有真相（不新建状态）。

    - MapSpec（layers / sources / layout.components / visibility）；
    - bound refs 的 O(1) descriptor（存在性 + feature_count + bbox）；
    - 组合模板的 required/optional 组件契约（复用 composition cardinality，
      不发明第二套 required/optional schema）。
    """
    from app.services.session_data import session_data_manager

    if mapspec is None:
        from app.services.mapspec_store import mapspec_store

        mapspec = await mapspec_store.get_mapspec(session_id) or {}

    refs: Dict[str, Optional[dict]] = {}
    for row in list(chapter.get("data_requirements") or []) + list(
        chapter.get("analysis_steps") or []
    ):
        if not isinstance(row, dict):
            continue
        ref = str(row.get("bound_ref") or "")
        if ref and ref not in refs:
            try:
                refs[ref] = await session_data_manager.get_ref_descriptor(
                    session_id, ref
                )
            except Exception:  # noqa: BLE001 — 单 ref 失败不阻断整体校验
                refs[ref] = None

    required_types: List[str] = []
    compo_id = str(
        (chapter.get("template_selection") or {}).get("composition_template_id")
        or ""
    )
    if compo_id:
        try:
            from app.lib.cartography.composition_templates import (
                get_composition_template_registry,
            )

            tpl = get_composition_template_registry().get(compo_id)
            if tpl is not None:
                required_types = [
                    slot.id
                    for slot in tpl.component_slots
                    if slot.cardinality in ("required",)
                ]
        except Exception:  # noqa: BLE001 — 模板缺失退化为无 required 断言
            required_types = []
    if not required_types:
        # 兜底：组合证据缺失时按产品模板默认组件的交集子集（title/scale）
        # 断言 —— 与 composition seeds 的最小契约一致，避免旧章节误报。
        required_types = ["title", "scale_bar"]

    return {
        "mapspec": mapspec,
        "descriptors": refs,
        "required_types": required_types,
    }


# ── Validators（纯函数；输入 gather_completion_inputs 的产物）────────


def validate_execution(chapter: Dict[str, Any]) -> List[MapCompletionFinding]:
    """DAG 终态门（复用 plan_graph 投影 —— 单一计算源，不重推行状态）。

    mandatory 节点未全部 complete/skipped → pending（needs_execution 披露）。
    failed 行是可重试的执行缺口、unavailable 是能力缺口 —— finalizer 不
    自己重跑算法，交还 DAG/Harness 的重试/降级语义。
    """
    rows = [
        r
        for r in list(chapter.get("data_requirements") or [])
        + list(chapter.get("analysis_steps") or [])
        if isinstance(r, dict)
    ]
    if not rows:
        return []
    try:
        from app.services.gis_harness.plan_graph import build_plan_graph

        graph = build_plan_graph(chapter)
        nodes = graph.nodes
    except Exception:  # noqa: BLE001 — 图构建失败退回行状态判别
        nodes = []
    findings: List[MapCompletionFinding] = []
    if nodes:
        mandatory = [n for n in nodes if not n.optional]
        open_nodes = [
            n for n in mandatory if n.status.value in ("pending", "ready", "running")
        ]
        blocked = [n for n in mandatory if n.status.value in ("failed", "unavailable")]
        if open_nodes:
            caps = ",".join(n.capability for n in open_nodes[:4])
            findings.append(
                MapCompletionFinding(
                    code=F_NEEDS_EXECUTION,
                    severity="error",
                    target=caps,
                    detail=f"{len(open_nodes)} mandatory nodes not terminal",
                )
            )
        if blocked:
            caps = ",".join(n.capability for n in blocked[:4])
            findings.append(
                MapCompletionFinding(
                    code=F_NEEDS_EXECUTION,
                    severity="error",
                    target=caps,
                    detail=(
                        f"{len(blocked)} mandatory nodes "
                        + ",".join(sorted({n.status.value for n in blocked}))
                        + " — retry or replan owed"
                    ),
                )
            )
        return findings
    # 兜底（无图）：行状态直读
    open_rows = [r for r in rows if str(r.get("status") or "") == "pending"]
    failed_rows = [r for r in rows if str(r.get("status") or "") == "failed"]
    if open_rows:
        caps = ",".join(str(r.get("capability") or "?") for r in open_rows[:4])
        findings.append(
            MapCompletionFinding(
                code=F_NEEDS_EXECUTION,
                severity="error",
                target=caps,
                detail=f"{len(open_rows)} capability rows pending",
            )
        )
    if failed_rows:
        caps = ",".join(str(r.get("capability") or "?") for r in failed_rows[:4])
        findings.append(
            MapCompletionFinding(
                code=F_NEEDS_EXECUTION,
                severity="error",
                target=caps,
                detail=f"{len(failed_rows)} failed rows await retry",
            )
        )
    return findings


def validate_artifacts(
    chapter: Dict[str, Any],
    descriptors: Dict[str, Optional[dict]],
) -> List[MapCompletionFinding]:
    """artifact 校验：required artifact 已绑定、ref 存活、空结果有明确语义。"""
    findings: List[MapCompletionFinding] = []
    rows = [
        r
        for r in list(chapter.get("data_requirements") or [])
        + list(chapter.get("analysis_steps") or [])
        if isinstance(r, dict)
        and str(r.get("status") or "") in ("available", "done")
        and not bool(r.get("optional"))
    ]
    seen: set[str] = set()
    for row in rows:
        cap = str(row.get("capability") or "?")
        if cap in seen:
            continue
        seen.add(cap)
        ref = str(row.get("bound_ref") or "")
        if not ref:
            findings.append(
                MapCompletionFinding(
                    code=F_ARTIFACT_MISSING,
                    severity="error",
                    target=cap,
                    detail="capability marked complete without a bound artifact ref",
                )
            )
            continue
        desc = descriptors.get(ref)
        if desc is None:
            findings.append(
                MapCompletionFinding(
                    code=F_ARTIFACT_EXPIRED,
                    severity="error",
                    target=cap,
                    detail=f"bound ref {ref[:48]} not present in session store",
                )
            )
            continue
        count = desc.get("feature_count")
        if isinstance(count, int) and count == 0:
            findings.append(
                MapCompletionFinding(
                    code=F_EMPTY_RESULT,
                    severity="error",
                    target=cap,
                    detail=f"artifact {ref[:48]} has zero features — nothing to map",
                )
            )
    return findings


def _spec_layers(mapspec: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [ly for ly in (mapspec.get("layers") or []) if isinstance(ly, dict)]


def _layer_declared_visible(layer: Dict[str, Any]) -> bool:
    layout = layer.get("layout") or {}
    return layer.get("visible") is not False and layout.get("visibility") != "none"


def validate_layers(
    chapter: Dict[str, Any],
    mapspec: Dict[str, Any],
) -> List[MapCompletionFinding]:
    """图层校验：结果层存在、source 在册、可见；输出 machine-readable findings。"""
    findings: List[MapCompletionFinding] = []
    layers = _spec_layers(mapspec)
    raw_sources = mapspec.get("sources")
    if isinstance(raw_sources, dict):
        sources = set(raw_sources.keys())
    else:
        sources = {
            s.get("id")
            for s in (raw_sources or [])
            if isinstance(s, dict)
        }
        sources.discard(None)
    by_id = {str(ly.get("id") or ""): ly for ly in layers}

    result_ids = [
        str(ly.get("layer_id") or "")
        for ly in (chapter.get("map_layers") or [])
        if isinstance(ly, dict)
        and ly.get("layer_id")
        and str(ly.get("role") or "") in RESULT_LAYER_ROLES
        and ly.get("enabled") is not False
    ]
    if result_ids:
        for lid in result_ids:
            layer = by_id.get(lid)
            if layer is None:
                findings.append(
                    MapCompletionFinding(
                        code=F_LAYER_MISSING,
                        severity="error",
                        target=lid,
                        detail="planned result layer not present in MapSpec",
                    )
                )
                continue
            src = str(layer.get("source") or "")
            if src and src not in sources:
                findings.append(
                    MapCompletionFinding(
                        code=F_SOURCE_MISSING,
                        severity="error",
                        target=lid,
                        detail=f"layer source '{src[:48]}' not registered in MapSpec sources",
                    )
                )
            if not _layer_declared_visible(layer):
                findings.append(
                    MapCompletionFinding(
                        code=F_LAYER_HIDDEN,
                        severity="error",
                        target=lid,
                        detail="result layer desired-visibility is none",
                        repair=R_SHOW_LAYER,
                    )
                )
    elif layers:
        # 无 planned result layer 绑定（旧章节 / 纯展示路径）：退化为
        # “至少一个可见数据层”断言（basemap/label 子层不算 —— 有 source
        # 引用且非 none-position 的数据层）。
        if not any(_layer_declared_visible(ly) for ly in layers):
            findings.append(
                MapCompletionFinding(
                    code=F_NO_RESULT_LAYER,
                    severity="error",
                    target="layers",
                    detail="no visible data layer in final MapSpec",
                )
            )
    else:
        findings.append(
            MapCompletionFinding(
                code=F_NO_RESULT_LAYER,
                severity="error",
                target="layers",
                detail="MapSpec has no layers",
            )
        )
    return findings


def validate_components(
    mapspec: Dict[str, Any],
    required_types: List[str],
    layer_ids: List[str],
) -> List[MapCompletionFinding]:
    """制图组件校验：模板 required 组件在场且启用（复用组合 cardinality）。"""
    findings: List[MapCompletionFinding] = []
    components = [
        c
        for c in ((mapspec.get("layout") or {}).get("components") or [])
        if isinstance(c, dict)
    ]
    enabled_types = {
        str(c.get("type") or "") for c in components if c.get("enabled") is not False
    }
    present_types = {str(c.get("type") or "") for c in components}
    for t in required_types:
        if t not in present_types:
            findings.append(
                MapCompletionFinding(
                    code=F_COMPONENT_MISSING,
                    severity="error",
                    target=t,
                    detail=f"required component type '{t}' absent",
                    repair=R_ADD_COMPONENT,
                )
            )
        elif t not in enabled_types:
            findings.append(
                MapCompletionFinding(
                    code=F_COMPONENT_DISABLED,
                    severity="error",
                    target=t,
                    detail=f"required component type '{t}' is disabled",
                    repair=R_ENABLE_COMPONENT,
                )
            )
    # 单例重复（desired-state 即可评）
    for t in _SINGLETON_TYPES:
        n = sum(1 for c in components if c.get("type") == t and c.get("enabled") is not False)
        if n > 1:
            findings.append(
                MapCompletionFinding(
                    code=F_LAYOUT_CONFLICT,
                    severity="warning",
                    target=t,
                    detail=f"{n} enabled singleton components of type '{t}'",
                )
            )
    # 孤儿绑定（layerId 指向已删层）
    known = set(layer_ids)
    for c in components:
        if c.get("enabled") is False:
            continue
        lid = str((c.get("options") or {}).get("layerId") or "")
        if lid and known and lid not in known:
            findings.append(
                MapCompletionFinding(
                    code=F_ORPHAN_BINDING,
                    severity="warning",
                    target=str(c.get("id") or lid),
                    detail=f"component layerId '{lid[:48]}' not in spec layers",
                )
            )
    return findings


def validate_layout(mapspec: Dict[str, Any]) -> List[MapCompletionFinding]:
    """第一版布局冲突检测：floating 矩形重叠 + zone 容量/exclusive 超限。

    复用 semantic_checks 已有的 desired-state 判定（同一几何语义，不建第
    二套碰撞模型）。修复原则：user-pinned（floating）组件不自动挪动——
    只披露；anchor 默认组件的超限同样披露（auto 重排在导出布局引擎里处
    理，见 ADR-0081 deferred）。
    """
    findings: List[MapCompletionFinding] = []
    components = [
        c
        for c in ((mapspec.get("layout") or {}).get("components") or [])
        if isinstance(c, dict)
    ]
    if not components:
        return findings

    floating: List[Dict[str, Any]] = []
    for c in components:
        if c.get("enabled") is False:
            continue
        placement = c.get("placement") or {}
        if not isinstance(placement, dict) or placement.get("mode") != "floating":
            continue
        try:
            floating.append(
                {
                    "id": str(c.get("id") or ""),
                    "x": float(placement.get("x") or 0),
                    "y": float(placement.get("y") or 0),
                    "w": float(placement.get("width") or 0),
                    "h": float(placement.get("height") or 0),
                }
            )
        except (TypeError, ValueError):
            continue
    for i in range(len(floating)):
        for j in range(i + 1, len(floating)):
            a, b = floating[i], floating[j]
            if a["w"] <= 0 or a["h"] <= 0 or b["w"] <= 0 or b["h"] <= 0:
                continue
            if (
                a["x"] < b["x"] + b["w"]
                and b["x"] < a["x"] + a["w"]
                and a["y"] < b["y"] + b["h"]
                and b["y"] < a["y"] + a["h"]
            ):
                findings.append(
                    MapCompletionFinding(
                        code=F_LAYOUT_CONFLICT,
                        severity="warning",
                        target=f"{a['id']}+{b['id']}",
                        detail="floating components overlap (user-pinned, disclosed only)",
                    )
                )
    return findings


def derive_result_bbox(
    chapter: Dict[str, Any],
    descriptors: Dict[str, Optional[dict]],
) -> Optional[List[float]]:
    """主结果 bbox：全部 bound ref descriptor 的 bbox 并集（元数据 O(N)）。

    不逐 feature 扫描、不复制 GeoJSON —— descriptor 缺 bbox 时该 ref 跳过；
    全部缺失 → None（viewport finding 由调用方判定）。
    """
    best: Optional[List[float]] = None
    for row in list(chapter.get("data_requirements") or []) + list(
        chapter.get("analysis_steps") or []
    ):
        if not isinstance(row, dict):
            continue
        ref = str(row.get("bound_ref") or "")
        if not ref:
            continue
        desc = descriptors.get(ref)
        if not desc:
            continue
        bbox = desc.get("bbox")
        if not (isinstance(bbox, (list, tuple)) and len(bbox) == 4):
            continue
        try:
            w, s, e, n = (float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3]))
        except (TypeError, ValueError):
            continue
        if not (w <= e and s <= n):
            continue
        if best is None:
            best = [w, s, e, n]
        else:
            best = [
                min(best[0], w),
                min(best[1], s),
                max(best[2], e),
                max(best[3], n),
            ]
    return best


def assess_export_parity(mapspec: Dict[str, Any]) -> str:
    """导出一致性：enabled 组件是否全部被导出管线支持（support matrix 派生）。

    这是 desired-state 的静态判定（哪些组件类型有导出消费方），不是渲染
    证据；渲染级 parity 由 exporter 的共享 resolver + 测试锁定。
    """
    try:
        from app.lib.cartography.component_renderers import (
            get_component_renderer_registry,
        )

        registry = get_component_renderer_registry()
        components = [
            c
            for c in ((mapspec.get("layout") or {}).get("components") or [])
            if isinstance(c, dict) and c.get("enabled") is not False
        ]
        unsupported = []
        for c in components:
            t = str(c.get("type") or "")
            support = registry.support_for(t)
            if support is not None and t not in (
                "export_layout",
                "basemap",
                "graticule",
                "map_border",
                "inset_map",
                "annotation",
            ) and not support.exporters:
                unsupported.append(t)
        if not components or not unsupported:
            return "parity"
        return "divergent"
    except Exception:  # noqa: BLE001 — 矩阵缺失不阻断完成度判定
        return "unknown"


def _validate_all(inputs: Dict[str, Any], chapter: Dict[str, Any]) -> List[MapCompletionFinding]:
    mapspec = inputs["mapspec"]
    findings: List[MapCompletionFinding] = []
    findings.extend(validate_artifacts(chapter, inputs["descriptors"]))
    findings.extend(validate_layers(chapter, mapspec))
    findings.extend(
        validate_components(
            mapspec,
            inputs["required_types"],
            [str(ly.get("id") or "") for ly in _spec_layers(mapspec)],
        )
    )
    findings.extend(validate_layout(mapspec))
    return findings[:MAX_FINDINGS]


# ── Repair（确定性 desired-state 修复；有界轮数内执行）───────────────


async def _apply_repairs(
    session_id: str,
    findings: List[MapCompletionFinding],
    mapspec: Dict[str, Any],
) -> List[str]:
    """执行可自动修复的发现；返回实际应用的 repair action codes。

    只做三类低风险修复（都经既有突变通道，复用 owner/CAS 守卫）：
    - add_component：required 组件缺失 → 工厂默认值 upsert；
    - enable_component：required 组件被禁用 → 重新启用；
    - show_layer：结果层 desired-visibility=none → GISMutationBatch（用户
      显式隐藏会被既有 user-wins 守卫拒绝并如实保留）。
    """
    applied: List[str] = []
    from app.services.mapspec_store import mapspec_store

    components = [
        c
        for c in ((mapspec.get("layout") or {}).get("components") or [])
        if isinstance(c, dict)
    ]
    for f in findings:
        if f.repair == R_ADD_COMPONENT and f.code == F_COMPONENT_MISSING:
            default_id = _COMPONENT_DEFAULT_IDS.get(f.target, f"{f.target}-main")
            try:
                res = await mapspec_store.patch_component(
                    session_id,
                    component_id=default_id,
                    component_type=f.target,
                    enabled=True,
                    upsert=True,
                )
                if res.get("success"):
                    applied.append(f"{R_ADD_COMPONENT}:{f.target}")
            except Exception:  # noqa: BLE001 — 单项修复失败留给下一轮披露
                logger.warning(
                    "[MapFinalizer] add_component repair failed type=%s", f.target
                )
        elif f.repair == R_ENABLE_COMPONENT and f.code == F_COMPONENT_DISABLED:
            target_id = next(
                (
                    str(c.get("id") or "")
                    for c in components
                    if c.get("type") == f.target
                ),
                _COMPONENT_DEFAULT_IDS.get(f.target, f.target),
            )
            try:
                res = await mapspec_store.patch_component(
                    session_id,
                    component_id=target_id,
                    component_type=f.target,
                    enabled=True,
                )
                if res.get("success"):
                    applied.append(f"{R_ENABLE_COMPONENT}:{f.target}")
            except Exception:  # noqa: BLE001
                logger.warning(
                    "[MapFinalizer] enable_component repair failed type=%s", f.target
                )
        elif f.repair == R_SHOW_LAYER and f.code == F_LAYER_HIDDEN:
            try:
                from app.services.gis_world_state.mutation import (
                    apply_gis_mutation_batch,
                )
                from app.services.mapspec.lifecycle_engine import (
                    PatchLayerPresentationIntent,
                )

                batch = await apply_gis_mutation_batch(
                    session_id,
                    [PatchLayerPresentationIntent(layer_id=f.target, visible=True)],
                    origin="agent",
                    actor="map_finalizer",
                )
                if batch.committed and any(
                    o.status == "applied" for o in batch.outcomes
                ):
                    applied.append(f"{R_SHOW_LAYER}:{f.target}")
            except Exception:  # noqa: BLE001 — user-wins 拒绝/事务失败留给下一轮披露
                logger.warning(
                    "[MapFinalizer] show_layer repair failed layer=%s", f.target
                )
    return applied


# ── 编排（validate → repair → revalidate，≤ MAX_FINALIZATION_PASSES）──


async def run_map_finalization(
    session_id: str,
    *,
    chapter: Optional[Dict[str, Any]] = None,
    max_passes: int = MAX_FINALIZATION_PASSES,
    reason: str = "manual",
) -> Optional[MapCompletionResult]:
    """对一个会话运行完成度终验。无 GIS 章节 → None（无事可终验）。

    有界：至多 ``max_passes`` 轮 validate→repair→revalidate；每轮 repair
    后重读 MapSpec（修复改变 desired state）。不可修复的 error 直接落
    needs_repair/failed，绝不循环。
    """
    from app.services.session_plan import load_session_plan

    if chapter is None:
        plan = await load_session_plan(session_id)
        chapter = plan.gis_chapter if plan is not None else None
    if not isinstance(chapter, dict) or not chapter:
        return None

    logger.info("[MapFinalizer] finalization_started session=%s reason=%s", session_id, reason)
    result = MapCompletionResult()
    exec_findings = validate_execution(chapter)
    if exec_findings:
        result.status = STATUS_PENDING
        result.findings = exec_findings[:MAX_FINDINGS]
        result.summary = "DAG not terminal — execution still owed"
        result.passes = 0
        return result


    inputs = await gather_completion_inputs(session_id, chapter)
    all_repairs: List[str] = []
    findings: List[MapCompletionFinding] = []
    passes = 0
    while passes < max_passes:
        passes += 1
        findings = _validate_all(inputs, chapter)
        repairable = [f for f in findings if f.repair is not None]
        if not repairable or not findings:
            break
        repairs = await _apply_repairs(session_id, findings, inputs["mapspec"])
        all_repairs.extend(repairs)
        if not repairs:
            break  # 修复通道全部失败 → 再验也不会变，避免空转
        # 修复改变了 desired state —— 重读输入再验
        inputs = await gather_completion_inputs(session_id, chapter)

    result.passes = passes
    result.findings = findings[:MAX_FINDINGS]
    result.repairs_applied = all_repairs[:MAX_DISCLOSED_REPAIRS]
    result.result_bbox = derive_result_bbox(chapter, inputs["descriptors"])
    result.export_status = assess_export_parity(inputs["mapspec"])

    has_layers = bool(_spec_layers(inputs["mapspec"]))
    if result.result_bbox:
        # 相机真相在前端：bbox 已导出 → 前端 finalizer 校验并（必要时）修复
        result.viewport_status = "repairable"
    elif has_layers:
        result.viewport_status = "invalid"
        result.findings.append(
            MapCompletionFinding(
                code=F_VIEWPORT_NO_BBOX,
                severity="warning",
                target="viewport",
                detail="no artifact bbox available to verify result visibility",
            )
        )
    else:
        result.viewport_status = "not_applicable"

    layer_err = [f for f in result.findings if f.code in (
        F_NO_RESULT_LAYER, F_LAYER_MISSING, F_SOURCE_MISSING, F_LAYER_HIDDEN,
    )]
    result.layer_status = "issues" if layer_err else ("valid" if has_layers else "unknown")
    comp_err = [f for f in result.findings if f.code in (
        F_COMPONENT_MISSING, F_COMPONENT_DISABLED,
    )]
    result.component_status = "issues" if comp_err else "valid"

    errors = [f for f in result.findings if f.severity == "error"]
    still_repairable = [f for f in errors if f.repair is not None]
    if not errors:
        result.status = STATUS_COMPLETE
        result.summary = "map product validated"
    elif still_repairable:
        result.status = STATUS_NEEDS_REPAIR
        result.summary = f"{len(still_repairable)} repairable findings remain"
    else:
        result.status = STATUS_FAILED
        result.summary = f"{len(errors)} blocking findings"

    logger.info(
        "[MapFinalizer] finalization_pass session=%s status=%s passes=%d repairs=%d",
        session_id, result.status, result.passes, len(result.repairs_applied),
    )
    return result


def map_product_block(result: MapCompletionResult, checked_revision: int) -> Dict[str, Any]:
    """章节持久化块（additive、bounded、单一键 ``map_product``）。"""
    block = result.to_dict()
    block["checked_revision"] = int(checked_revision)
    block["projection"] = result.projection_line()
    return block


async def _current_mapspec_revision(session_id: str) -> int:
    from app.services.session_data import session_data_manager

    try:
        state = await session_data_manager.get_map_state(session_id)
        return int(state.get("_cartographic_mutation_revision") or 0)
    except Exception:  # noqa: BLE001 — revision 读失败按 0 处理（只影响去重）
        return 0


async def maybe_finalize_map_product(
    session_id: str,
    *,
    reason: str = "tool_result",
    force: bool = False,
) -> Optional[MapCompletionResult]:
    """Harness 侧触发入口：廉价门 + 终验 + 章节持久化（幂等、有界）。

    去重门：章节已有 ``map_product`` 且 status=complete 且 checked_revision
    与当前 MapSpec revision 一致 → 跳过（同一 desired state 不重复终验）。
    任何后续突变（revision 变化）自然重新满足终验条件。

    写入路径复用 SessionPlan 的 per-session lock（fail-closed）；只覆盖
    ``gis_chapter["map_product"]`` 单键，不触碰行状态（无第二事实源）。
    """
    from app.services.session_plan import load_session_plan, save_session_plan
    from app.services.distributed_lock import session_lock_registry

    if not session_id:
        return None
    plan = await load_session_plan(session_id)
    if plan is None or not isinstance(plan.gis_chapter, dict):
        return None
    chapter = plan.gis_chapter
    revision = await _current_mapspec_revision(session_id)
    stored = chapter.get("map_product")
    if (
        not force
        and isinstance(stored, dict)
        and stored.get("status") == STATUS_COMPLETE
        and int(stored.get("checked_revision") or -1) == revision
    ):
        return None

    result = await run_map_finalization(session_id, chapter=chapter, reason=reason)
    if result is None:
        return None

    # 持久化（锁内重读——终验本身的 repair 突变可能已推进 revision）
    try:
        async with session_lock_registry.lock(session_id, fail_on_degraded=True) as lock:
            fresh = await load_session_plan(session_id)
            if fresh is not None and isinstance(fresh.gis_chapter, dict):
                if lock.lost:
                    return result
                fresh.gis_chapter["map_product"] = map_product_block(
                    result, await _current_mapspec_revision(session_id)
                )
                await save_session_plan(fresh)
    except Exception:  # noqa: BLE001 — 披露失败不阻断 turn；下一触发点重试
        logger.warning(
            "[MapFinalizer] chapter persist failed session=%s (will retry on next trigger)",
            session_id,
        )
    if result.status == STATUS_COMPLETE:
        logger.info("[MapFinalizer] finalization_complete session=%s", session_id)
    else:
        logger.info(
            "[MapFinalizer] finalization_failed session=%s status=%s",
            session_id, result.status,
        )
    return result


def finalization_sse_payload(result: MapCompletionResult) -> Dict[str, Any]:
    """前端 finalizer 消费的有界载荷（视口修复需要 bbox 与状态）。"""
    return {
        "status": result.status,
        "viewport_status": result.viewport_status,
        "result_bbox": result.result_bbox,
        "summary": result.summary[:120],
        "issues": [f.to_dict() for f in result.findings[:4]],
        "repairs": list(result.repairs_applied[:4]),
    }
