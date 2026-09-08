"""Render Diagnostics Contract V5 (ADR-0118 D1).

唯一权威的渲染/导出降级诊断词表。任何 renderer / exporter 在产物
无法忠实表达 spec 语义时，必须发出结构化诊断而不是静默降级或伪成功。

三层消费（同一词表，不另造第二词表）：
1. 后端导出孪生（mapspec_to_svg / report 链）编译期诊断；
2. 前端 exporter（ExportDegradation，经 component-catalog.generated.json
   的 ``renderDiagnostics`` 段对齐 —— registry-parity 测试锁定子集关系）；
3. ``POST /api/v1/export`` sidecar 持久化（导出证据锚点）。

本模块是**词表与载荷形状**的唯一真相；不承载渲染逻辑。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

#: 诊断严重级（封闭词表）。error = 产物与 spec 语义明显不符；
#: warning = 有损降级；info = 忠实但需要读者知晓的披露。
DIAGNOSTIC_SEVERITIES = ("info", "warning", "error")

#: 单次导出诊断条目上限（防诊断本身变成 DoS 载荷）。
MAX_DIAGNOSTICS_PER_EXPORT = 64
#: detail 文本上限（与前端 ExportDegradation.detail ≤200 对齐）。
MAX_DETAIL_CHARS = 200


@dataclass(frozen=True)
class RenderDiagnosticSpec:
    """单个诊断码的权威元数据。"""

    code: str
    severity: str
    #: 中文用户可读模板（{detail} 为可选插值位）。
    message: str


#: 权威词表。新增码必须： severity 恰当、message 中文、并被对应渲染/导出
#: 路径真实发射（禁止无发射器的死码 —— 死码由测试锁定为空）。
RENDER_DIAGNOSTICS: Dict[str, RenderDiagnosticSpec] = {
    spec.code: spec
    for spec in (
        # —— 前端既有词表（ExportDegradation）升为权威 ——
        RenderDiagnosticSpec(
            "chart_ref_unavailable", "warning",
            "图表组件引用的数据在导出时不可用，图表未进入导出件",
        ),
        RenderDiagnosticSpec(
            "table_ref_unavailable", "warning",
            "表格组件引用的数据在导出时不可用，表格未进入导出件",
        ),
        RenderDiagnosticSpec(
            "chart_kind_unsupported_export", "warning",
            "该图表类型暂不支持导出，已以占位披露",
        ),
        RenderDiagnosticSpec(
            "component_skipped_invalid", "warning",
            "组件配置无效，已跳过渲染",
        ),
        # —— label engine V5（长文本不再静默溢出）——
        RenderDiagnosticSpec(
            "label_truncated", "warning",
            "标注文本过长，已截断显示（{detail}）",
        ),
        RenderDiagnosticSpec(
            "label_suppressed_too_long", "warning",
            "标注文本过长且无法放入任何候选位置，已省略（{detail}）",
        ),
        # —— 图例 / 内容截断 ——
        RenderDiagnosticSpec(
            "legend_entries_truncated", "info",
            "图例条目过多，导出件仅绘制前 {detail} 条",
        ),
        RenderDiagnosticSpec(
            "features_truncated", "warning",
            "要素数量超出导出预算，已截断至 {detail} 条",
        ),
        # —— 导出过程完整性 ——
        RenderDiagnosticSpec(
            "export_timeout_partial", "error",
            "导出超时，产物可能不完整（{detail}）",
        ),
        RenderDiagnosticSpec(
            "vector_svg_fallback_raster", "warning",
            "矢量 SVG 合成失败，已回退位图包装（{detail}）",
        ),
        # —— 真矢量 SVG 披露 ——
        RenderDiagnosticSpec(
            "basemap_omitted_vector_svg", "info",
            "矢量 SVG 不含栅格底图瓦片（仅数据层与图面要素）",
        ),
        # —— PDF 披露 ——
        RenderDiagnosticSpec(
            "pdf_text_rasterized_cjk", "info",
            "PDF 含中文文本，以位图形式嵌入（未内嵌 CJK 字体）",
        ),
        # —— 对比 / 多视图 ——
        RenderDiagnosticSpec(
            "comparison_second_view_not_exported", "warning",
            "对比模式第二视图未进入导出件（静态导出为单幅主图）",
        ),
        RenderDiagnosticSpec(
            "comparison_export_composed", "info",
            "对比模式导出为显式左右组合（分界位于 {detail}）",
        ),
        # —— 多画幅运行时诚实降级 ——
        RenderDiagnosticSpec(
            "cartogram_unsupported", "warning",
            "cartogram（变形地图）渲染运行时未实现，按未变形几何导出",
        ),
        RenderDiagnosticSpec(
            "small_multiple_panel_skipped", "warning",
            "小倍数面板帧渲染失败，已跳过（{detail}）",
        ),
        RenderDiagnosticSpec(
            "atlas_page_skipped", "warning",
            "atlas 页面渲染失败，已跳过（{detail}）",
        ),
        RenderDiagnosticSpec(
            "atlas_page_limit_truncated", "warning",
            "atlas 页数超出上限，仅渲染前 {detail} 页",
        ),
        # —— 地形 / 3D 披露 ——
        RenderDiagnosticSpec(
            "terrain_3d_scale_caveat", "info",
            "3D 地形/倾斜视角下比例尺按平面口径计算，可能与视觉距离不符",
        ),
    )
}


@dataclass
class RenderDiagnostic:
    """一条渲染/导出诊断（可序列化载荷）。"""

    code: str
    severity: str
    message: str
    detail: str = ""
    layer_id: Optional[str] = None
    component_id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
        }
        if self.detail:
            out["detail"] = self.detail
        if self.layer_id:
            out["layer_id"] = self.layer_id
        if self.component_id:
            out["component_id"] = self.component_id
        return out


def diagnostic(
    code: str,
    detail: str = "",
    layer_id: Optional[str] = None,
    component_id: Optional[str] = None,
) -> Optional[RenderDiagnostic]:
    """按权威词表构造诊断；未知码返回 None（调用方不得自造码）。"""
    spec = RENDER_DIAGNOSTICS.get(code)
    if spec is None:
        return None
    message = spec.message.format(detail=detail) if detail else spec.message
    return RenderDiagnostic(
        code=code,
        severity=spec.severity,
        message=message,
        detail=detail[:MAX_DETAIL_CHARS],
        layer_id=layer_id,
        component_id=component_id,
    )


def catalog_section() -> List[Dict[str, str]]:
    """component-catalog.generated.json 的 ``renderDiagnostics`` 段。"""
    return [
        {"code": s.code, "severity": s.severity, "message": s.message}
        for s in (
            RENDER_DIAGNOSTICS[k] for k in sorted(RENDER_DIAGNOSTICS)
        )
    ]


def normalize_render_diagnostics(
    payload: Any,
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """校验/规整外部提交的诊断载荷（export sidecar 入口用）。

    返回 ``(accepted, rejected_reasons)``：未知码、缺码、非 dict、超限
    条目一律拒绝并记录原因，绝不静默改写。
    """
    accepted: List[Dict[str, Any]] = []
    rejected: List[str] = []
    if not isinstance(payload, list):
        return accepted, ["payload must be a list"]
    for i, item in enumerate(payload):
        if len(accepted) >= MAX_DIAGNOSTICS_PER_EXPORT:
            rejected.append(f"item {i}: exceeds max {MAX_DIAGNOSTICS_PER_EXPORT}")
            break
        if not isinstance(item, dict):
            rejected.append(f"item {i}: not an object")
            continue
        code = item.get("code")
        if not isinstance(code, str) or code not in RENDER_DIAGNOSTICS:
            rejected.append(f"item {i}: unknown code {code!r}")
            continue
        spec = RENDER_DIAGNOSTICS[code]
        detail = item.get("detail", "")
        if not isinstance(detail, str):
            detail = str(detail)
        entry = RenderDiagnostic(
            code=code,
            severity=spec.severity,
            message=spec.message.format(detail=detail) if detail else spec.message,
            detail=detail[:MAX_DETAIL_CHARS],
            layer_id=item.get("layer_id")
            if isinstance(item.get("layer_id"), str)
            else None,
            component_id=item.get("component_id")
            if isinstance(item.get("component_id"), str)
            else None,
        )
        accepted.append(entry.to_dict())
    return accepted, rejected
