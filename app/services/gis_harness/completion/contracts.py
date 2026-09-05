"""Completion runtime 契约（constants + dataclasses）— ADR-0081 / ADR-0091。

本模块是完成度词表的单一来源：validator 族
（``app.services.gis_harness.completion.validators``）、pipeline、渲染观察侧
（``render_observation``）与外部调用方都从这里取 finding codes / 状态词表 /
repair action codes，不建第二份字面量表。完整设计（性能契约、有界修复、
幂等门）见 ADR-0081 与 ADR-0091（Runtime V4 §33 包拆分说明）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

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

# 语义级 QA（desired-state 语义，非槽位在场性）：组合路径被绕过
# （webgis_component_update 手工增删组件）时，槽位校验看不见
# 「图层语义 ↔ 图例类型」的匹配。
F_SEMANTIC_LEGEND_MISSING = "semantic_legend_missing"
F_SEMANTIC_LEGEND_MISMATCH = "semantic_legend_mismatch"
F_TITLE_MISSING_REPORT = "title_missing_report_product"
F_CRS_NOT_WGS84 = "crs_not_wgs84"

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

# ── Product Verdict（VNext §14 —— 专业产品裁决词表）────────────────
# 完成管线之上的**单字产品裁决**：前端/评估/发布门消费的最终状态面。
# 词表冻结（机器读契约）；推导是纯函数（derive_product_verdict）。
VERDICT_READY = "READY"
VERDICT_READY_WITH_WARNINGS = "READY_WITH_WARNINGS"
VERDICT_NEEDS_REPAIR = "NEEDS_REPAIR"
VERDICT_BLOCKED_BY_DATA = "BLOCKED_BY_DATA"
VERDICT_BLOCKED_BY_METHOD = "BLOCKED_BY_METHOD"

#: 数据族阻断码（不可自愈的数据缺席/过期/空结果/执行终态阻塞）。
_DATA_BLOCK_CODES = frozenset({
    F_ARTIFACT_MISSING,
    F_ARTIFACT_EXPIRED,
    F_EMPTY_RESULT,
    F_EXECUTION_BLOCKED,
    F_SOURCE_MISSING,
    F_RENDER_SOURCE_MISSING,
})


def derive_product_verdict(
    result: "MapCompletionResult",
    methodology_warnings: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """MapCompletionResult (+章节方法论警告) → 单字产品裁决 + 证据。

    纯函数、确定性、有界：
    - failed 且错误全部是数据族 → BLOCKED_BY_DATA；
    - failed 且存在非数据族错误 → BLOCKED_BY_METHOD（方法/表达层无法
      在修复预算内收敛）；
    - needs_repair / pending → NEEDS_REPAIR（修复中，未到裁决）；
    - complete 且零警告（含方法论警告）→ READY；
    - complete 带警告（含方法论警告）→ READY_WITH_WARNINGS —— 方法论
      披露永远压低裁决档位，不允许「带分母缺失披露的 READY」。
    """
    errors = result.error_findings
    warnings = [f for f in result.findings if f.severity == "warning"]
    mw = [w for w in (methodology_warnings or []) if isinstance(w, dict)]
    data_errors = [f.code for f in errors if f.code in _DATA_BLOCK_CODES]
    method_errors = [f.code for f in errors if f.code not in _DATA_BLOCK_CODES]

    if result.status == STATUS_FAILED:
        # 双族并存时数据先行（上游因）—— method_errors 仍随行披露。
        verdict = VERDICT_BLOCKED_BY_DATA if data_errors else VERDICT_BLOCKED_BY_METHOD
        reasons = sorted(set(data_errors + method_errors))[:6]
    elif result.status in (STATUS_NEEDS_REPAIR, STATUS_PENDING):
        verdict = VERDICT_NEEDS_REPAIR
        reasons = sorted({f.code for f in result.findings if f.severity == "error"})[:6]
    else:
        verdict = (
            VERDICT_READY if not warnings and not mw
            else VERDICT_READY_WITH_WARNINGS
        )
        reasons = sorted({f.code for f in warnings})[:6]

    return {
        "verdict": verdict,
        "reasons": reasons,
        "methodology_warning_count": len(mw),
        "methodology_warning_codes": sorted({
            str(w.get("code")) for w in mw if w.get("code")
        })[:8],
        "finding_counts": {
            "errors": len(errors),
            "warnings": len(warnings),
        },
    }


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


def _spec_layers(mapspec: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [ly for ly in (mapspec.get("layers") or []) if isinstance(ly, dict)]
