"""编译前 obligations / conformance 六闸（F12 / ADR-0214 D4）。

纯函数：MapPlanIR + 当前 MapSpec → ObligationReport。任何 **blocking**
发现都会让编译器产出空 mutation 集（fail-closed：宁可不出图，不出错图）。
每条发现携带结构化 reason code 与目标引用，无自由文本结论。
"""
from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional, Sequence

from pydantic import BaseModel, ConfigDict, Field

from app.lib.cartography.plan_ir import MapPlanIR, spec_doc_of

__all__ = ["ObligationFinding", "ObligationReport", "check_obligations",
           "SUPPORTED_LAYER_TYPES", "SUPPORTED_EXPORT_FORMATS",
           "SUPPORTED_COMPONENT_TYPES"]


# ── 权威词表（引用，不自造）──────────────────────────────────────────────
#: MapSpecLayer.type 词表（mapspec_schema 的 Literal 镜像 —— schema 是权威，
#: 这里只做编译期前置闸；schema 校验仍是最终防线）。
SUPPORTED_LAYER_TYPES = frozenset((
    "circle", "line", "fill", "symbol", "heatmap",
    "raster", "fill-extrusion", "background", "hillshade",
))
#: ExportFormat 词表（gis_harness/intent.py ExportFormat）。
SUPPORTED_EXPORT_FORMATS = frozenset(("png", "pdf", "svg", "csv", "geojson"))


def _component_vocab() -> frozenset:
    try:
        from typing import get_args
        from app.services.gis_harness.components import ComponentType
        return frozenset(get_args(ComponentType))
    except Exception:  # noqa: BLE001 — 词表降级为保守核心族
        return frozenset((
            "basemap", "legend", "continuous_colorbar", "categorical_legend",
            "north_arrow", "scale_bar", "title", "subtitle", "annotation",
            "graticule", "map_border", "attribution", "statistics_panel",
            "chart_panel", "table_panel", "export_layout", "inset_map",
            "methodology_note", "uncertainty_panel", "decision_panel",
            "label_layer",
        ))


SUPPORTED_COMPONENT_TYPES = _component_vocab()

#: reason code → severity 语义（blocking 集合外一律 disclosure/warn）。
_BLOCKING = frozenset((
    "PLAN_LOCK_CONFLICT", "DATA_REF_UNRESOLVED", "COMPONENT_UNKNOWN_TYPE",
    "EXPORT_UNSUPPORTED", "LAYER_TYPE_UNSUPPORTED", "BLUEPRINT_MISSING",
))


class ObligationFinding(BaseModel):
    """一条 conformance 发现（码 + 目标 + 有界消息；确定性排序键）。"""

    model_config = ConfigDict(frozen=True)

    code: str = Field(min_length=1, max_length=64)
    severity: Literal["blocking", "warning", "info"]
    target: str = Field(default="", max_length=192)
    message: str = Field(default="", max_length=256)
    evidence_refs: List[str] = Field(default_factory=list, max_length=8)

    def sort_key(self) -> tuple:
        return (0 if self.severity == "blocking" else 1, self.code, self.target)


class ObligationReport(BaseModel):
    """编译前对账报告：status=blocked ⇒ 不产出任何 mutation。"""

    model_config = ConfigDict(frozen=True)

    status: Literal["ok", "blocked"]
    findings: List[ObligationFinding] = Field(default_factory=list)
    reason_codes: List[str] = Field(default_factory=list, max_length=24)

    @property
    def blocking(self) -> List[ObligationFinding]:
        return [f for f in self.findings if f.severity == "blocking"]

    def sort_key(self) -> tuple:
        return (0 if self.status == "blocked" else 1,)


def _finding(code: str, severity: str, target: str, message: str,
             evidence: Sequence[str] = ()) -> ObligationFinding:
    return ObligationFinding(
        code=code, severity=severity,  # type: ignore[arg-type]
        target=target[:192], message=message[:256],
        evidence_refs=[e[:192] for e in list(evidence)[:8]],
    )


def _live_source_ids(current: Optional[Dict[str, Any]]) -> set:
    sources = (current or {}).get("sources") or {}
    if isinstance(sources, dict):
        return {str(k) for k in sources.keys()}
    return set()


def _alias_ids(current: Optional[Dict[str, Any]]) -> set:
    """bound_ref 可能是别名/任务内 ref —— sources 不含时对 layers.id 与
    analysis outputs 再解析一轮（V1 判据保守：可解析即 live）。"""
    layers = (current or {}).get("layers") or []
    ids = {str(l.get("id")) for l in layers if isinstance(l, dict) and l.get("id")}
    return ids


def check_obligations(
    ir: MapPlanIR,
    current: Optional[Dict[str, Any]],
    *,
    analysis_output_ids: Sequence[str] = (),
) -> ObligationReport:
    """六闸：锁冲突 / data refs / 组件词表 / renderer / export / scale-CRS。"""
    findings: List[ObligationFinding] = []
    current = spec_doc_of(current)
    live_sources = _live_source_ids(current)
    live_layers = _alias_ids(current)
    live_outputs = {str(o) for o in analysis_output_ids} | {
        o.output_id for o in ir.analysis_outputs
    }

    # ── 闸 1：user lock 冲突（fail-closed，user-wins）────────────────────
    locked_layers = set(ir.user_locks.layer_ids)
    locked_components = set(ir.user_locks.component_ids)
    for li in ir.layer_intents:
        if li.locked or (li.layer_id and li.layer_id in locked_layers):
            findings.append(_finding(
                "PLAN_LOCK_CONFLICT", "blocking", f"layer:{li.layer_id}",
                "layer intent 目标被用户锁定；编译拒绝（user-wins）",
                ("lock_snapshot",)))
    for ci in ir.component_intents:
        if ci.locked or (ci.component_id and ci.component_id in locked_components):
            if ci.action != "ensure":  # ensure 已在场组件 = 无操作语义，放行
                findings.append(_finding(
                    "PLAN_LOCK_CONFLICT", "blocking", f"component:{ci.component_id}",
                    "component intent 目标被用户锁定；编译拒绝（user-wins）",
                    ("lock_snapshot",)))

    # ── 闸 2：data refs 活性 ────────────────────────────────────────────
    for li in ir.layer_intents:
        if li.action == "remove":
            continue
        needs_blueprint = li.action.startswith("present_") or li.action == "restyle"
        if needs_blueprint and li.blueprint is None:
            findings.append(_finding(
                "BLUEPRINT_MISSING", "blocking", f"layer:{li.layer_id or li.intent_id}",
                "present/restyle 意图缺少 layer blueprint（表达面无从编译）"))
            continue
        ref = li.source_ref
        if ref and ref not in live_sources and ref not in live_layers \
                and ref not in live_outputs:
            findings.append(_finding(
                "DATA_REF_UNRESOLVED", "blocking", f"layer:{li.layer_id or li.intent_id}",
                f"source_ref {ref!r} 不在当前 sources/layers/analysis outputs",
                (ref,)))
        if li.action.startswith("present_") and not ref and not li.layer_id:
            findings.append(_finding(
                "DATA_REF_UNRESOLVED", "blocking", f"layer:{li.intent_id}",
                "新建层意图无 source_ref 且无既有 layer_id（无处绑定数据）"))

    # ── 闸 3/4：组件词表 + renderer（layer type）支持 ─────────────────────
    for ci in ir.component_intents:
        if ci.component_type not in SUPPORTED_COMPONENT_TYPES:
            findings.append(_finding(
                "COMPONENT_UNKNOWN_TYPE", "blocking", f"component:{ci.component_id}",
                f"component_type {ci.component_type!r} 不在 registry 词表"))
    for li in ir.layer_intents:
        if li.blueprint is None:
            continue
        if li.blueprint.layer_type not in SUPPORTED_LAYER_TYPES:
            findings.append(_finding(
                "LAYER_TYPE_UNSUPPORTED", "blocking",
                f"layer:{li.layer_id or li.intent_id}",
                f"layer_type {li.blueprint.layer_type!r} 不受渲染面支持"))

    # ── 闸 5：export 支持 ───────────────────────────────────────────────
    for eo in ir.exports:
        if eo.fmt not in SUPPORTED_EXPORT_FORMATS:
            findings.append(_finding(
                "EXPORT_UNSUPPORTED", "blocking", f"export:{eo.fmt}",
                f"导出格式 {eo.fmt!r} 不在支持词表 {sorted(SUPPORTED_EXPORT_FORMATS)}"))

    # ── 闸 6：scale/CRS（advisory —— 提示不阻塞，披露给 receipt）──────────
    for ds in ir.datasets:
        crs = (ds.crs or "").upper()
        if crs.startswith("EPSG:4326") and ir.layout.output_purpose.startswith("print"):
            findings.append(_finding(
                "SCALE_CRS_ADVISORY", "warning", f"dataset:{ds.dataset_id}",
                "地理坐标系（EPSG:4326）用于打印版面：比例尺/量算可能失真"))

    blocked = any(f.severity == "blocking" for f in findings)
    findings.sort(key=lambda f: f.sort_key())
    reason_codes: List[str] = []
    for f in findings:
        if f.code not in reason_codes:
            reason_codes.append(f.code)
    return ObligationReport(
        status="blocked" if blocked else "ok",
        findings=findings[:64],
        reason_codes=reason_codes[:24],
    )
