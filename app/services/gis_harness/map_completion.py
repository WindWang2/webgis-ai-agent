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

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ── 契约常量（有界，确定性）─────────────────────────────────────────
MAX_FINALIZATION_PASSES = 2
MAX_FINDINGS = 12
MAX_FINDING_DETAIL = 160
MAX_DISCLOSED_REPAIRS = 6
# 修复记忆上限（> 披露上限：记忆是 one-shot 语义的载体，多组件/多层会话
# 需要 >6 条 —— 挤掉最老记忆会复活 B-4 回归；披露面仍按 6 条有界）。
MAX_REPAIR_MEMORY = 32
# planner 的 role 词表是 primary | secondary | reference（planner.py 的
# PlannedLayer.role）；"result" 只是历史防御值。secondary 是结果通道
# （如成都场景的行政区统计 choropleth）—— 漏掉它会让副结果层整体逃逸
# 校验、假 complete（review H-1）。reference 是语境层，不算结果。
RESULT_LAYER_ROLES = ("primary", "secondary", "result")

# 完成态：pending（DAG 未终态）→ needs_repair（存在可修复发现）→
# complete（无 error 发现；warning 允许）/ failed（存在不可修复 error）。
STATUS_PENDING = "pending"
STATUS_NEEDS_REPAIR = "needs_repair"
STATUS_COMPLETE = "complete"
STATUS_FAILED = "failed"

# finding codes（machine-readable，前端/测试依赖这些字面量）
F_NEEDS_EXECUTION = "needs_execution"
# 终态阻塞（failed/unavailable 行）：DAG 不会再自愈 —— 与 needs_execution
# （turn 中段的 open 行）分开披露，否则 blocked 场景被 pending 吞掉、
# 零产品级披露（review H-3）。
F_EXECUTION_BLOCKED = "execution_blocked"
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

# P9 渲染级 finding codes（validator 在 render_observation.py —— 单一词表
# 定义在此，避免两处漂移）。runtime 渲染缺口可自愈（re-render/re-observation
# 收敛），状态归 needs_repair 而非 failed（transient semantics）。
F_RENDER_UNVERIFIED = "render_unverified"
F_RENDER_REVISION_STALE = "render_revision_stale"
F_RENDER_LAYER_MISSING = "render_layer_missing"
F_RENDER_SOURCE_MISSING = "render_source_missing"
F_RENDER_COMPONENT_MISSING = "render_component_missing"
F_RENDER_ERROR = "render_error"

RUNTIME_RENDER_CODES = frozenset({
    F_RENDER_LAYER_MISSING,
    F_RENDER_SOURCE_MISSING,
    F_RENDER_COMPONENT_MISSING,
    F_RENDER_ERROR,
})

# render_status 词表（P9；validator 在 render_observation.py，词表同址定义）
RENDER_VERIFIED = "verified"              # 匹配 revision 的观察在场且校验通过
RENDER_ISSUES = "issues"                  # 匹配 revision 但结果层/源/必需组件缺席
RENDER_STALE = "stale"                    # observation revision ≠ 当前 revision
RENDER_UNKNOWN = "unknown"                # 无观察 / 旧客户端 / pre-revision 观察
RENDER_NOT_APPLICABLE = "not_applicable"  # 无可观察的产品面

# repair action codes（repairs_applied 里的字面量）
R_ADD_COMPONENT = "add_component"
R_ENABLE_COMPONENT = "enable_component"
R_SHOW_LAYER = "show_layer"

# 组件 upsert 的默认 id（与 gis_harness.components 工厂一致；不引入第二
# 套默认值 —— 修复走 mutate_component 的同一工厂入口）。
# 与 gis_harness.components 工厂的默认 id 同表（review P2：categorical_legend
# 工厂默认 legend-categorical、statistics_panel 工厂默认 statistics —— 此前
# 手抄表与工厂漂移，add_component 修复会错配到别的组件 id 上）。
_COMPONENT_DEFAULT_IDS: Dict[str, str] = {
    "title": "title",
    "subtitle": "subtitle",
    "legend": "legend-main",
    "categorical_legend": "legend-categorical",
    "continuous_colorbar": "colorbar-main",
    "scale_bar": "scale-bar",
    "north_arrow": "north-arrow",
    "attribution": "attribution",
    "statistics_panel": "statistics",
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
    # 组件族（slot 的 allowed_component_types）—— family-aware 修复用。
    family: Optional[List[str]] = None

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
    # P9 render observation：verified | issues | stale | unknown | not_applicable
    # （render_observation.py 词表；unknown = 无观察/旧客户端，向后兼容披露）
    render_status: str = "unknown"
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
            "render_status": self.render_status,
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
            # P9：stale 观察下的 final 是诚实披露（瞬态、re-observation 自愈）；
            # unknown（无观察能力）保持旧行文案 —— 旧客户端完成语义零漂移。
            if self.render_status == RENDER_STALE:
                return "Map product: final (render:stale)"
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
    map_state: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """读取完成度校验所需的全部既有真相（不新建状态）。

    - MapSpec（layers / sources / layout.components / visibility）；
    - bound refs 的 O(1) descriptor（存在性 + feature_count + bbox）；
    - 组合模板的 required/optional 组件契约（复用 composition cardinality，
      不发明第二套 required/optional schema）；
    - render observation + 当前 mutation revision（P9：渲染级校验输入，
      与 revision 读取共用一次 map_state 读，不重复拉全量状态）。
    """
    from app.services.session_data import session_data_manager

    if map_state is None:
        try:
            map_state = await session_data_manager.get_map_state(session_id)
        except Exception:  # noqa: BLE001 — 状态读失败按缺席处理
            map_state = None

    render_observation = None
    mapspec_revision = 0
    if isinstance(map_state, dict):
        from app.services.gis_harness.render_observation import (
            load_render_observation,
        )

        render_observation = await load_render_observation(session_id, map_state)
        try:
            mapspec_revision = int(
                map_state.get("_cartographic_mutation_revision") or 0
            )
        except (TypeError, ValueError):
            mapspec_revision = 0

    if mapspec is None:
        from app.services.mapspec_store import mapspec_store

        mapspec = await mapspec_store.get_mapspec(session_id) or {}

    refs: Dict[str, Optional[dict]] = {}
    pending_refs: List[str] = []

    def _collect(ref: Any) -> None:
        if (
            isinstance(ref, str)
            and ref.startswith("ref:")
            # 磁盘态栅格（ref:raster/*）不在 session store —— 由
            # artifact_lifecycle 的 mtime 巡检负责，这里不强判过期。
            and not ref.startswith("ref:raster/")
            and ref not in refs
        ):
            refs[ref] = None
            pending_refs.append(ref)

    for row in list(chapter.get("data_requirements") or []) + list(
        chapter.get("analysis_steps") or []
    ):
        if not isinstance(row, dict):
            continue
        _collect(row.get("bound_ref"))
    # MapSpec source refs（P1/ADR-0082）：source 指针是第二个绑定面 ——
    # 行 ref 存活不代表 spec source 的 ref 存活（TTL/LRU 按 ref 独立驱逐）。
    raw_sources = mapspec.get("sources")
    if isinstance(raw_sources, dict):
        source_defs = [v for v in raw_sources.values() if isinstance(v, dict)]
    else:
        source_defs = [s for s in (raw_sources or []) if isinstance(s, dict)]
    for src in source_defs:
        for key in ("ref", "ref_id", "image_ref", "imageRef", "result_ref"):
            _collect(src.get(key))

    if pending_refs:
        # 并发取 descriptor（review F-2）：逐个 await 在 Redis 后端是每 ref
        # 一个串行往返 —— 1k 节点 ≈ 1k 次 RTT 且挂在工具回调关键路径上。
        # 三态区分（review 终审 F4）：ok（拿到 descriptor）/ missing（两次
        # 探测都返回 None —— 确认驱逐，→ 过期 finding）/ unknown（持续
        # 异常 —— 存储抖动，**从 refs 移除**：validators 对未知跳过，绝不
        # 把瞬态错误判成过期并持久化假 failed）。
        async def _fetch(ref: str) -> tuple[str, str, Optional[dict]]:
            try:
                desc = await session_data_manager.get_ref_descriptor(session_id, ref)
            except Exception:  # noqa: BLE001
                desc = None
                try:
                    desc = await session_data_manager.get_ref_descriptor(
                        session_id, ref
                    )
                    if desc is not None:
                        return ref, "ok", desc
                    return ref, "missing", None
                except Exception:  # noqa: BLE001
                    return ref, "unknown", None
            if desc is not None:
                return ref, "ok", desc
            # None 可能是驱逐也可能是抖动 —— 复核一次
            try:
                recheck = await session_data_manager.get_ref_descriptor(
                    session_id, ref
                )
                if recheck is not None:
                    return ref, "ok", recheck
                return ref, "missing", None
            except Exception:  # noqa: BLE001
                return ref, "unknown", None

        fetched = await asyncio.gather(*(_fetch(r) for r in pending_refs))
        for ref, state, desc in fetched:
            if state == "ok":
                refs[ref] = desc
            elif state == "missing":
                refs[ref] = None
            else:  # unknown：从 refs 移除（validators 按未知跳过）
                refs.pop(ref, None)

    # required 组件以 composition slot 族语义表达（slot id ≠ 组件类型名：
    # "legend" 槽可由 legend/categorical_legend/continuous_colorbar 任一满足
    # —— 校验/修复按 allowed_component_types 族判定，不发明第二套 schema）。
    required_slots: List[List[str]] = []
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
                for slot in tpl.component_slots:
                    if slot.cardinality != "required":
                        continue
                    allowed = [str(t) for t in (slot.allowed_component_types or [])]
                    required_slots.append(allowed or [str(slot.id)])
        except Exception:  # noqa: BLE001 — 模板缺失退化为无 required 断言
            required_slots = []
    if not required_slots:
        # 兜底：组合证据缺失时按最小契约断言（title + scale_bar）—— 与
        # composition seeds 一致，避免旧章节误报。
        required_slots = [["title"], ["scale_bar"]]

    return {
        "mapspec": mapspec,
        "descriptors": refs,
        "required_slots": required_slots,
        "render_observation": render_observation,
        "mapspec_revision": mapspec_revision,
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
                    code=F_EXECUTION_BLOCKED,
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
    # 兜底（无图）：行状态直读（unavailable 与 failed 同为阻塞态）
    open_rows = [r for r in rows if str(r.get("status") or "") == "pending"]
    failed_rows = [
        r for r in rows if str(r.get("status") or "") in ("failed", "unavailable")
    ]
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
                code=F_EXECUTION_BLOCKED,
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
    """artifact 校验：required artifact 已绑定、ref 存活、空结果有明确语义。

    registry-driven（review 6 P1）：只有产出**空间 feature set** 的能力才
    要求 bound_ref —— `stats_table`（spatial_stats/point_profile 等）、
    `od_matrix`、raster 家族（heatmap 栅格走独立通道，geojson_ref 恒空）
    的完成证据是工具成功本身，不落 FC ref；对它们强求 bound_ref 会把
    常规 recipe 路径误判为 failed。
    """
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
        policy = _capability_fc_ref_policy(cap)
        if policy == "none":
            continue
        ref = str(row.get("bound_ref") or "")
        if not ref:
            if policy == "required":
                findings.append(
                    MapCompletionFinding(
                        code=F_ARTIFACT_MISSING,
                        severity="error",
                        target=cap,
                        detail="capability marked complete without a bound artifact ref",
                    )
                )
            # optional（raster 通道）：无 FC ref 是合法完成形态 —— 完成证据
            # 是工具成功 + 已挂载的栅格图层（validate_layers 覆盖后者）。
            continue
        desc = descriptors.get(ref)
        if desc is None:
            if ref not in descriptors:
                continue  # unknown（探测失败）：未知 ≠ 过期，跳过不判
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
                    severity="warning",
                    target=cap,
                    detail=f"artifact {ref[:48]} has zero features — nothing to map",
                )
            )
    return findings


# 非空间输出类型：完成证据 = 工具成功（无 FC ref 可绑；见 dispatch 的
# geojson_ref 语义）。raster 家族走 heatmap 栅格通道，同样不落 FC ref。
_NON_SPATIAL_ARTIFACT_TYPES = {"stats_table", "od_matrix", "raster_surface", "terrain_surface"}
# FC ref 可选的能力：density_surface 有两条产物通道 —— vector 通道落
# FC ref、raster 渲染通道（density.visual.heatmap → heatmap_data 工具）
# 刻意不落 geojson_ref（产物是 ref:heatmap-* / ref:raster/*）。对它强求
# bound_ref 会把成功挂载的栅格热力图误判成 artifact_missing → 假 failed
# （review C-1）；绑了 ref 时仍照常校验存在性/空结果。
_OPTIONAL_FC_REF_TYPES = {"density_surface"}


def _capability_fc_ref_policy(capability: str) -> str:
    """该能力的 FC ref 策略：required / optional / none。

    - required：输出含空间 feature set 且无 raster 旁路 → 必须绑定 ref；
    - optional：存在 raster/栅格产物通道 → ref 有则校验、无则不判缺失；
    - none：纯非空间输出（stats/od/raster 家族）→ 完成证据 = 工具成功。
    """
    try:
        from app.lib.gis.capability_registry import get_capability_registry

        desc = get_capability_registry().get(capability)
        if desc is None:
            return "required"  # 未知能力保守要求（缺 ref 时由 finding 披露）
        outputs = [str(t) for t in (desc.output_artifact_types or [])]
        if not outputs:
            return "required"
        if any(t in _OPTIONAL_FC_REF_TYPES for t in outputs):
            return "optional"
        if all(t in _NON_SPATIAL_ARTIFACT_TYPES for t in outputs):
            return "none"
        return "required"
    except Exception:  # noqa: BLE001 — registry 读失败保守要求
        return "required"


def _spec_layers(mapspec: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [ly for ly in (mapspec.get("layers") or []) if isinstance(ly, dict)]


def _layer_declared_visible(layer: Dict[str, Any]) -> bool:
    layout = layer.get("layout") or {}
    return layer.get("visible") is not False and layout.get("visibility") != "none"


def validate_layers(
    chapter: Dict[str, Any],
    mapspec: Dict[str, Any],
    descriptors: Optional[Dict[str, Optional[dict]]] = None,
) -> List[MapCompletionFinding]:
    """图层校验：结果层存在、source 在册、可见、source ref 存活。

    ``descriptors``（P1/ADR-0082）：MapSpec source 的 ref 指针也过存活
    校验 —— 行 ref 存活而 source ref 被 TTL/LRU 驱逐时，此前会假
    complete（review C-2）。缺省 None 时退化为旧行为（兼容直调测试）。
    """
    findings: List[MapCompletionFinding] = []
    layers = _spec_layers(mapspec)
    raw_sources = mapspec.get("sources")
    if isinstance(raw_sources, dict):
        source_by_id = {
            str(k): v for k, v in raw_sources.items() if isinstance(v, dict)
        }
    else:
        source_by_id = {
            str(s.get("id") or ""): s
            for s in (raw_sources or [])
            if isinstance(s, dict)
        }
    sources = set(source_by_id.keys())
    sources.discard("")
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
            elif src and descriptors is not None:
                src_def = source_by_id.get(src) or {}
                src_ref = next(
                    (
                        src_def.get(k)
                        for k in ("ref", "ref_id", "image_ref", "imageRef", "result_ref")
                        if isinstance(src_def.get(k), str)
                        and src_def.get(k).startswith("ref:")
                        and not src_def.get(k).startswith("ref:raster/")
                    ),
                    None,
                )
                if (
                    src_ref
                    and src_ref in descriptors
                    and descriptors[src_ref] is None
                ):
                    findings.append(
                        MapCompletionFinding(
                            code=F_ARTIFACT_EXPIRED,
                            severity="error",
                            target=lid,
                            detail=(
                                f"layer source ref '{src_ref[:48]}' expired from "
                                "session store (TTL/LRU eviction)"
                            ),
                        )
                    )
            if not _layer_declared_visible(layer):
                intent = layer.get("cartographic_intent")
                user_owned = (
                    isinstance(intent, dict)
                    and intent.get("presentation_owner") == "user"
                )
                if user_owned:
                    # user-wins（review B-6）：显隐权威在用户 —— 用户的隐藏
                    # 就是期望状态。降级为 warning、不修复不对抗，否则每个
                    # 触发点都重放一个注定被 owner 守卫拒绝的突变、永续
                    # needs_repair + 重复 toast。
                    findings.append(
                        MapCompletionFinding(
                            code=F_LAYER_HIDDEN,
                            severity="warning",
                            target=lid,
                            detail="result layer hidden by user (user-wins, disclosed only)",
                        )
                    )
                else:
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


def _family_renderable(family: List[str]) -> bool:
    """slot 族内是否有任一类型存在 live 渲染器或导出消费方（支持矩阵）。"""
    try:
        from app.lib.cartography.component_renderers import (
            get_component_renderer_registry,
        )

        registry = get_component_renderer_registry()
        for t in family:
            support = registry.support_for(t)
            if support and (support.renderers or support.exporters):
                return True
        return False
    except Exception:  # noqa: BLE001 — 矩阵缺失按可修复处理（保守）
        return True


def validate_components(
    mapspec: Dict[str, Any],
    required_slots: List[List[str]],
    layer_ids: List[str],
) -> List[MapCompletionFinding]:
    """制图组件校验：模板 required 槽在场且启用（slot 族语义）。

    required 槽由 allowed_component_types 表达（如 "legend" 槽可由
    legend/categorical_legend/continuous_colorbar 任一满足）；缺失/禁用均
    可修复（修复取族的第一个类型）。
    """
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
    for family in required_slots:
        family = [t for t in family if t] or ["title"]
        primary = family[0]
        # review P2：两侧都无渲染/导出消费方的类型（map_border /
        # export_layout / graticule / inset_map）不修也不判 error ——
        # "修复"一个永远不可见的组件是完成度表演；降级为 warning 披露。
        repairable = _family_renderable(family)
        if not any(t in present_types for t in family):
            findings.append(
                MapCompletionFinding(
                    code=F_COMPONENT_MISSING,
                    severity="error" if repairable else "warning",
                    target=primary,
                    detail=(
                        f"required component slot '{primary}' absent "
                        f"(any of {', '.join(family[:3])})"
                    ),
                    repair=R_ADD_COMPONENT if repairable else None,
                    family=family,
                )
            )
        elif not any(t in enabled_types for t in family):
            findings.append(
                MapCompletionFinding(
                    code=F_COMPONENT_DISABLED,
                    severity="error" if repairable else "warning",
                    target=primary,
                    detail=f"required component slot '{primary}' is disabled",
                    repair=R_ENABLE_COMPONENT if repairable else None,
                    family=family,
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
            if ref not in descriptors:
                continue  # unknown（探测失败）：跳过不判（不虚构 bbox）
            continue  # 确认缺失：无 bbox 可贡献（过期 finding 由 artifacts 侧报）
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
    findings.extend(validate_layers(chapter, mapspec, inputs["descriptors"]))
    findings.extend(
        validate_components(
            mapspec,
            inputs["required_slots"],
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
    prior_repairs: Optional[List[str]] = None,
) -> List[str]:
    """执行可自动修复的发现；返回实际应用的 repair action codes。

    只做三类低风险修复（都经既有突变通道，复用 owner/CAS 守卫）：
    - add_component：required 组件缺失 → 工厂默认值 upsert；
    - enable_component：required 组件被禁用 → 重新启用；
    - show_layer：结果层 desired-visibility=none → GISMutationBatch（用户
      显式隐藏会被既有 user-wins 守卫拒绝并如实保留）。

    ``prior_repairs``（上一持久化块里已应用过的修复）提供组件修复的
    one-shot 语义：用户在 finalizer 启用后再次禁用时不形成修复对抗，
    转为 needs_repair 披露（组件通道没有 layer 那样的 owner 守卫）。
    """
    prior = list(prior_repairs or [])
    applied: List[str] = []
    from app.services.mapspec_store import mapspec_store

    components = [
        c
        for c in ((mapspec.get("layout") or {}).get("components") or [])
        if isinstance(c, dict)
    ]
    for f in findings:
        family = f.family or [f.target]
        if f.repair == R_ADD_COMPONENT and f.code == F_COMPONENT_MISSING:
            # one-shot（review P1）：上一轮已尝试过同族修复而 finding 仍在
            # （典型：用户在 finalizer 启用后再次禁用）→ 不再对抗，披露
            # needs_repair —— 用户显式决策优先。
            if any(p.startswith(f"{R_ADD_COMPONENT}:{t}") for t in family for p in prior):
                continue
            repair_type = family[0]
            default_id = _COMPONENT_DEFAULT_IDS.get(repair_type, f"{repair_type}-main")
            try:
                res = await mapspec_store.patch_component(
                    session_id,
                    component_id=default_id,
                    component_type=repair_type,
                    enabled=True,
                    upsert=True,
                )
                if res.get("success"):
                    applied.append(f"{R_ADD_COMPONENT}:{repair_type}")
            except Exception:  # noqa: BLE001 — 单项修复失败留给下一轮披露
                logger.warning(
                    "[MapFinalizer] add_component repair failed type=%s", repair_type
                )
        elif f.repair == R_ENABLE_COMPONENT and f.code == F_COMPONENT_DISABLED:
            if any(p.startswith(f"{R_ENABLE_COMPONENT}:{t}") for t in family for p in prior):
                continue
            # family-aware（review P2）：禁用的成员可能是族内非 primary 类型
            # （如 categorical_legend），只按 primary 找会命中不存在的 id。
            member = next(
                (
                    c
                    for c in components
                    if c.get("type") in family and c.get("enabled") is False
                ),
                None,
            )
            if member is not None:
                target_id = str(member.get("id") or "")
                target_type = str(member.get("type") or f.target)
            else:
                target_type = family[0]
                target_id = _COMPONENT_DEFAULT_IDS.get(target_type, target_type)
            try:
                res = await mapspec_store.patch_component(
                    session_id,
                    component_id=target_id,
                    component_type=target_type,
                    enabled=True,
                )
                if res.get("success"):
                    applied.append(f"{R_ENABLE_COMPONENT}:{target_type}")
            except Exception:  # noqa: BLE001
                logger.warning(
                    "[MapFinalizer] enable_component repair failed type=%s", target_type
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
    prior_repairs: Optional[List[str]] = None,
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
        blocked = [f for f in exec_findings if f.code == F_EXECUTION_BLOCKED]
        has_open = any(f.code == F_NEEDS_EXECUTION for f in exec_findings)
        if has_open or not blocked:
            result.status = STATUS_PENDING
            result.findings = exec_findings[:MAX_FINDINGS]
            result.summary = "DAG not terminal — execution still owed"
            result.passes = 0
            return result
        # blocked-only（failed/unavailable 终态行）：DAG 已终态、执行欠账
        # 不会自愈 —— pending 会被静默吞掉（不落块不披露），turn 结束时
        # 零产品级披露（review H-3）。按 failed 披露欠重试/欠降级，交还
        # DAG/重试语义；finalizer 绝不自己重跑算法（ADR-0081）。
        result.status = STATUS_FAILED
        result.findings = blocked[:MAX_FINDINGS]
        result.summary = f"{len(blocked)} blocked nodes await retry/replan"
        result.passes = 0
        result.viewport_status = "not_applicable"
        result.layer_status = "unknown"
        result.component_status = "unknown"
        result.export_status = "unknown"
        return result


    inputs = await gather_completion_inputs(session_id, chapter)
    all_repairs: List[str] = []
    findings: List[MapCompletionFinding] = []
    passes = 0
    repaired_last_pass = False
    while passes < max_passes:
        passes += 1
        findings = _validate_all(inputs, chapter)
        fatal = [
            f
            for f in findings
            if f.severity == "error"
            and f.repair is None
            and f.code in (F_NO_RESULT_LAYER, F_LAYER_MISSING, F_SOURCE_MISSING)
        ]
        if fatal:
            # review P3：存在不可修复的结构性 error 时不再做组件修复 ——
            # 修复只会白付两轮 revision 而 status 仍 failed。
            repaired_last_pass = False
            break
        repairable = [f for f in findings if f.repair is not None]
        if not repairable or not findings:
            repaired_last_pass = False
            break
        repairs = await _apply_repairs(
            session_id, findings, inputs["mapspec"], prior_repairs=prior_repairs
        )
        all_repairs.extend(repairs)
        if not repairs:
            repaired_last_pass = False
            break  # 修复通道全部失败 → 再验也不会变，避免空转
        # 修复改变了 desired state —— 重读输入再验
        inputs = await gather_completion_inputs(session_id, chapter)
        repaired_last_pass = True

    # review P1：末轮刚应用过修复时，findings 还是修复前的快照 —— 用
    # 重读后的输入做一次终验（纯函数，零 I/O），状态才与新 desired state
    # 一致（否则 repairs_applied 与 findings 自相矛盾）。
    if repaired_last_pass:
        findings = _validate_all(inputs, chapter)

    # P9 渲染级校验（ADR-0086）：RenderObservation 是观察不是真相 —— 只产
    # 出披露 findings，无修复动作；stale/unknown 如实降级（不 false
    # complete），runtime 缺席归 needs_repair（可自愈），不落 failed。
    try:
        from app.services.gis_harness.render_observation import (
            validate_render_observation,
        )

        render_status, render_findings = validate_render_observation(
            chapter,
            inputs["mapspec"],
            inputs.get("render_observation"),
            int(inputs.get("mapspec_revision") or 0),
            inputs["required_slots"],
        )
        result.render_status = render_status
        findings = list(findings) + list(render_findings)
    except Exception:  # noqa: BLE001 — 渲染校验是增值披露，绝不阻断终验
        logger.warning("[MapFinalizer] render validation failed session=%s", session_id)
        result.render_status = "unknown"

    result.passes = passes
    result.result_bbox = derive_result_bbox(chapter, inputs["descriptors"])
    result.export_status = assess_export_parity(inputs["mapspec"])

    has_layers = bool(_spec_layers(inputs["mapspec"]))
    if result.result_bbox:
        # 相机真相在前端：bbox 已导出 → 前端 finalizer 校验并（必要时）修复
        result.viewport_status = "repairable"
    elif has_layers:
        result.viewport_status = "invalid"
        findings.append(
            MapCompletionFinding(
                code=F_VIEWPORT_NO_BBOX,
                severity="warning",
                target="viewport",
                detail="no artifact bbox available to verify result visibility",
            )
        )
    else:
        result.viewport_status = "not_applicable"

    # 状态先于披露截断计算（review 终审 F6）：findings[:MAX_FINDINGS] 只是
    # 披露上界 —— 用全量 findings 判状态，否则 >12 条发现时第 13 条起的
    # error 会被静默丢弃、误判 complete。
    all_errors = [f for f in findings if f.severity == "error"]
    result.findings = findings[:MAX_FINDINGS]
    result.repairs_applied = all_repairs[:MAX_DISCLOSED_REPAIRS]

    layer_err = [f for f in findings if f.code in (
        F_NO_RESULT_LAYER, F_LAYER_MISSING, F_SOURCE_MISSING, F_LAYER_HIDDEN,
    )]
    result.layer_status = "issues" if layer_err else ("valid" if has_layers else "unknown")
    comp_err = [f for f in findings if f.code in (
        F_COMPONENT_MISSING, F_COMPONENT_DISABLED,
    )]
    result.component_status = "issues" if comp_err else "valid"

    unrepairable = [
        f
        for f in all_errors
        # P9：runtime 渲染缺口（层/源/组件未挂载、观察错误）不进 failed ——
        # 期望态正确、可经 re-render/re-observation 自愈，归 needs_repair。
        if f.repair is None and f.code not in RUNTIME_RENDER_CODES
    ]
    still_repairable = [f for f in all_errors if f.repair is not None]
    runtime_render = [f for f in all_errors if f.code in RUNTIME_RENDER_CODES]
    if not all_errors:
        result.status = STATUS_COMPLETE
        result.summary = "map product validated"
    elif unrepairable:
        # 不可修复 error 在场 → failed 优先于 needs_repair（只靠修复到不了
        # complete，"needs repair" 会误导下一动作）。
        result.status = STATUS_FAILED
        result.summary = f"{len(all_errors)} blocking findings ({len(unrepairable)} unrepairable)"
    elif runtime_render and not still_repairable:
        result.status = STATUS_NEEDS_REPAIR
        result.summary = (
            f"{len(runtime_render)} render findings await runtime re-observation"
        )
    else:
        result.status = STATUS_NEEDS_REPAIR
        result.summary = f"{len(still_repairable)} repairable findings remain"

    logger.info(
        "[MapFinalizer] finalization_pass session=%s status=%s passes=%d repairs=%d",
        session_id, result.status, result.passes, len(result.repairs_applied),
    )
    return result


def _rows_fingerprint(chapter: Dict[str, Any]) -> str:
    """行状态指纹（去重门输入）：capability 行的状态/ref 绑定变化即改变。

    比「行全终态」检查更强（review A-2/B-3/F-4）：行回退（重试标 failed、
    重绑定新 ref）都会改变指纹 → 触发重验；同时让 needs_repair/failed
    会话在无变化时跳过整轮重跑（此前只有 complete 享受去重门，异常会话
    每个工具结果都重放整轮 finalization + SSE + toast）。
    """
    parts: List[str] = []
    for row in list(chapter.get("data_requirements") or []) + list(
        chapter.get("analysis_steps") or []
    ):
        if not isinstance(row, dict):
            continue
        parts.append(
            f"{row.get('capability')}:{row.get('status')}:{row.get('bound_ref') or ''}"
        )
    return "|".join(sorted(parts))


def map_product_block(
    result: MapCompletionResult,
    checked_revision: int,
    *,
    all_repairs: Optional[List[str]] = None,
    rows_fingerprint: str = "",
    render_observation_seq: int = 0,
) -> Dict[str, Any]:
    """章节持久化块（additive、bounded、单一键 ``map_product``）。

    ``all_repairs``：跨轮累积修复记忆（prior ∪ 本轮 applied）。one-shot
    语义依赖它跨轮存活 —— 只写本轮 applied 时，下一次无修复运行会把
    记忆清零，finalizer 将隔轮重新对抗用户决策（review B-4）。披露面
    ``repairs`` 有界（≤6）；完整记忆落 ``repair_memory``（≤32 —— 6 条
    上限会在多组件/多层会话里挤掉最老记忆，复活同一回归，review 终审 F7）。

    ``render_observation_seq``（P9）：验证所依据的 render observation 代次 ——
    幂等门的第三把钥匙：新观察到达（seq 前进）即打破门，重验把披露从
    unverified/stale 升级为 verified（或反向暴露 render 缺席）。
    """
    block = result.to_dict()
    if all_repairs is not None:
        merged = list(dict.fromkeys(all_repairs))
        block["repairs"] = merged[:MAX_DISCLOSED_REPAIRS]
        block["repair_memory"] = merged[:MAX_REPAIR_MEMORY]
    block["checked_revision"] = int(checked_revision)
    block["render_observation_seq"] = int(render_observation_seq)
    if rows_fingerprint:
        block["rows_fingerprint"] = rows_fingerprint[:512]
    block["projection"] = result.projection_line()
    return block


async def _current_mapspec_revision(session_id: str) -> int:
    from app.services.session_data import session_data_manager

    try:
        state = await session_data_manager.get_map_state(session_id)
        return int(state.get("_cartographic_mutation_revision") or 0)
    except Exception:  # noqa: BLE001 — revision 读失败按 0 处理（只影响去重）
        return 0


def _stored_checked_revision(stored: Dict[str, Any]) -> Optional[int]:
    """合法 0 不被误判（review P3：``int(x or -1)`` 把 0 洗成 -1）。"""
    raw = stored.get("checked_revision")
    if isinstance(raw, bool) or raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _stored_render_seq(stored: Dict[str, Any]) -> int:
    """已存块记录的 render observation 代次（旧块无键 → -1 触发一次重验自愈）。"""
    raw = stored.get("render_observation_seq")
    if isinstance(raw, bool) or raw is None:
        return -1
    try:
        return int(raw)
    except (TypeError, ValueError):
        return -1


async def maybe_finalize_map_product(
    session_id: str,
    *,
    reason: str = "tool_result",
    force: bool = False,
) -> Optional[MapCompletionResult]:
    """Harness 侧触发入口：廉价门 + 终验 + 章节持久化（幂等、有界）。

    去重门（review 加固）：章节已有终态 ``map_product``（不止 complete）
    且 checked_revision 与当前 MapSpec revision 一致、行指纹一致 → 跳过。
    行状态/ref 或 spec revision 任一变化都会打破门 → 重验。

    pending 不持久化、不披露 —— 除非章节里已有终态结论（review A-2/B-3：
    重试把行标 failed 后，陈旧的 "final" 投影必须收回，落降级 pending 块；
    [GIS Plan] 的行投影已披露未完成态，不发 SSE）。

    写入路径复用 SessionPlan 的 per-session lock（fail-closed）；只覆盖
    ``gis_chapter["map_product"]`` 单键，不触碰行状态（无第二事实源）。
    锁内做两道守卫：goal 变化（supersede 竞态）与 revision 漂移（验证后
    突变）都不落块 —— 让下一个触发点对真实状态重新终验。
    """
    from app.services.session_plan import goal_key, load_session_plan, save_session_plan
    from app.services.distributed_lock import session_lock_registry
    from app.services.session_data import session_data_manager
    from app.services.gis_harness.render_observation import (
        load_render_observation,
        observation_sequence,
    )

    if not session_id:
        return None
    plan = await load_session_plan(session_id)
    if plan is None or not isinstance(plan.gis_chapter, dict):
        return None
    chapter = plan.gis_chapter
    # P9：revision + render observation 一次读取（门输入同源，不双拉状态）。
    try:
        map_state = await session_data_manager.get_map_state(session_id)
    except Exception:  # noqa: BLE001 — 状态读失败按 0/None 处理（只影响去重门）
        map_state = None
    try:
        revision = int((map_state or {}).get("_cartographic_mutation_revision") or 0)
    except (TypeError, ValueError):
        revision = 0
    render_obs = await load_render_observation(session_id, map_state)
    render_seq = observation_sequence(render_obs)
    stored = chapter.get("map_product")
    # 去重门（review 加固 + F-4）：任何终态结论（不止 complete）在
    # 「MapSpec revision 一致 + 行指纹一致 + render observation 代次一致」
    # 时跳过重验 —— 行状态/ref 变化、任何 cartographic 突变或新观察到达
    # 都会打破门，交给下一触发点重验。
    # 旧块无 rows_fingerprint 键 → 首次不跳过，重验一次即自愈补齐。
    # 比较双侧截断（review 终审 F2）：存储侧 [:512]，比较侧同宽 ——
    # 此前存储截断/比较全量，≥8 行章节永不匹配 → 门失效、每触发点重跑。
    if (
        not force
        and isinstance(stored, dict)
        and stored.get("status")
        in (STATUS_COMPLETE, STATUS_NEEDS_REPAIR, STATUS_FAILED)
        and _stored_checked_revision(stored) == revision
        and _stored_render_seq(stored) == render_seq
        and str(stored.get("rows_fingerprint") or "")
        == _rows_fingerprint(chapter)[:512]
    ):
        return None

    validated_fingerprint = _rows_fingerprint(chapter)
    result = await run_map_finalization(
        session_id,
        chapter=chapter,
        reason=reason,
        prior_repairs=(
            list(
                dict.fromkeys(
                    list(stored.get("repair_memory") or [])
                    + list(stored.get("repairs") or [])
                )
            )
            if isinstance(stored, dict)
            else None
        ),
    )
    if result is None:
        return None
    stored_terminal = isinstance(stored, dict) and stored.get("status") in (
        STATUS_COMPLETE,
        STATUS_NEEDS_REPAIR,
        STATUS_FAILED,
    )
    if result.status == STATUS_PENDING and not stored_terminal:
        # 不持久化、不披露（见 docstring）；调用方拿 result 只做日志。
        return result
    demoted = result.status == STATUS_PENDING
    if demoted:
        # 回退降级（review A-2/B-3）：已存终态结论的章节出现新的执行缺口
        # （重试把行标 failed / 新增 pending 行）→ 陈旧的 "final" 投影必须
        # 收回。落 pending 块（行投影已表达欠执行，不发 SSE、不 toast）。
        result.summary = "execution re-owed — prior verdict withdrawn"

    validated_goal = goal_key(chapter, plan.user_goal)
    revision_after_run = await _current_mapspec_revision(session_id)

    # 持久化（锁内重读——终验本身的 repair 突变可能已推进 revision）
    try:
        async with session_lock_registry.lock(session_id, fail_on_degraded=True) as lock:
            fresh = await load_session_plan(session_id)
            if fresh is not None and isinstance(fresh.gis_chapter, dict):
                if lock.lost:
                    return result
                # supersede/replace 竞态：验证的章节已不是当前章节 → 不落块
                if goal_key(fresh.gis_chapter, fresh.user_goal) != validated_goal:
                    logger.info(
                        "[MapFinalizer] chapter superseded mid-run session=%s — persist skipped",
                        session_id,
                    )
                    return result
                # 验证后 revision 又被并发突变 → complete@R' 会盖住未验证的
                # 状态；留给下一触发点重验。
                if await _current_mapspec_revision(session_id) != revision_after_run:
                    logger.info(
                        "[MapFinalizer] revision moved mid-run session=%s — persist skipped",
                        session_id,
                    )
                    return result
                # 行漂移守卫（review 终审 F1）：终验期间并行工具回调改了行
                # 状态（行不推 revision）—— 旧指纹的结论不得盖上新指纹的
                # 章节（否则陈旧 failed/complete 被门永久保护）。
                if _rows_fingerprint(fresh.gis_chapter)[:512] != validated_fingerprint[:512]:
                    logger.info(
                        "[MapFinalizer] rows changed mid-run session=%s — persist skipped",
                        session_id,
                    )
                    return result
                # P9 观察漂移守卫：验证依据的 render observation 已被更新的
                # 观察覆盖（新 POST 在锁外落账、等锁写入）→ 旧观察的结论不得
                # 盖章 —— 留给下一触发点（含 POST 触发本身）按新观察重验。
                try:
                    fresh_state = await session_data_manager.get_map_state(session_id)
                except Exception:  # noqa: BLE001 — 读失败按无漂移处理
                    fresh_state = None
                if observation_sequence(
                    await load_render_observation(session_id, fresh_state)
                ) != render_seq:
                    logger.info(
                        "[MapFinalizer] render observation advanced mid-run session=%s — persist skipped",
                        session_id,
                    )
                    return result
                prior_repairs_merged = (
                    list(stored.get("repair_memory") or [])
                    + list(stored.get("repairs") or [])
                    if isinstance(stored, dict)
                    else []
                )
                merged_repairs = list(
                    dict.fromkeys(prior_repairs_merged + list(result.repairs_applied))
                )
                fresh.gis_chapter["map_product"] = map_product_block(
                    result,
                    revision_after_run,
                    all_repairs=merged_repairs,
                    rows_fingerprint=_rows_fingerprint(fresh.gis_chapter),
                    render_observation_seq=render_seq,
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


async def read_stored_map_product(session_id: str) -> Optional[Dict[str, Any]]:
    """读取已持久化的完成块（turn 收尾的 task_complete 披露兜底）。

    幂等门跳过终验时（complete + revision 一致），task_complete 仍应携带
    完成态 —— 否则 happy path 下该字段永远缺席（review P2）。
    """
    from app.services.session_plan import load_session_plan

    if not session_id:
        return None
    plan = await load_session_plan(session_id)
    stored = plan.gis_chapter.get("map_product") if plan is not None else None
    if not isinstance(stored, dict):
        return None
    return {
        # session_id 参与 frontend INV-2 跨会话守卫（review B-P3）：缺 sid
        # 的载荷绕过守卫，可能把别的会话相机 fit 走 / 弹错 toast。
        "session_id": session_id,
        "status": str(stored.get("status") or STATUS_PENDING),
        "summary": str(stored.get("summary") or "")[:120],
    }


def finalization_sse_payload(
    result: MapCompletionResult,
    session_id: str = "",
    *,
    mapspec: Optional[Dict[str, Any]] = None,
    mutation_revision: Optional[int] = None,
) -> Dict[str, Any]:
    """前端 finalizer 消费的有界载荷（视口修复需要 bbox 与状态）。

    session_id 参与 frontend INV-2 跨会话守卫（review P1：载荷缺 sid 时
    旧会话的迟到事件会把新会话相机 fit 走）。repair 改写了 desired state
    时携带 mapspec + mutation_revision —— 前端通用 spec 提交通道
    （use-sse-stream 对 data.mapspec 的既有消费）会把修复同步到 live
    chrome/exporter，否则"complete"对着一张用户看不见的 spec 宣称。
    """
    payload = {
        "status": result.status,
        "viewport_status": result.viewport_status,
        "result_bbox": result.result_bbox,
        "summary": result.summary[:120],
        "issues": [f.to_dict() for f in result.findings[:4]],
        "repairs": list(result.repairs_applied[:4]),
    }
    if session_id:
        payload["session_id"] = session_id
    if mapspec is not None and result.repairs_applied:
        payload["mapspec"] = mapspec
        payload["mutation_revision"] = mutation_revision
    return payload


async def current_mapspec_for_disclosure(session_id: str) -> tuple[Optional[Dict[str, Any]], Optional[int]]:
    """修复披露用的当前 spec 快照（只在实际应用过修复时被读取）。"""
    from app.services.mapspec_store import mapspec_store

    try:
        spec = await mapspec_store.get_mapspec(session_id)
        return spec, await _current_mapspec_revision(session_id)
    except Exception:  # noqa: BLE001 — 快照失败只影响附带披露
        return None, None
