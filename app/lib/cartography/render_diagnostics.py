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

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

#: 诊断严重级（封闭词表）。error = 产物与 spec 语义明显不符；
#: warning = 有损降级；info = 忠实但需要读者知晓的披露。
DIAGNOSTIC_SEVERITIES = ("info", "warning", "error")

#: 单次导出诊断条目上限（防诊断本身变成 DoS 载荷）。
MAX_DIAGNOSTICS_PER_EXPORT = 64
#: detail 文本上限（与前端 ExportDegradation.detail ≤200 对齐）。
MAX_DETAIL_CHARS = 200
#: layer_id / component_id 文本上限（外部提交的标识符同样有界入库）。
MAX_ID_CHARS = 200


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
        # review-r1：移除 label_suppressed_too_long —— 词表内唯一无发射器的
        # 死码（孪生编译器只截断不省略，fit_label_text 无"放不下"路径；
        # solve_labels 生产零接线）。ADR-0118 D1 死码禁止：待放置求解器
        # 真实接入导出链时再随发射器一并回归词表。
        RenderDiagnosticSpec(
            "label_truncated", "warning",
            "标注文本过长，已截断显示（{detail}）",
        ),
        # —— 图例 / 内容截断 ——
        RenderDiagnosticSpec(
            "legend_entries_truncated", "info",
            # review-r2 修复：原文案「导出件仅绘制前 {detail} 条」与事实相反
            # —— live 图例仅示前 8 条（legends.tsx entries.slice(0, 8) +
            # 「…+N」指示），导出件（drawChromeLegend / svg marginalia 图例）
            # 画全集。本码披露的是这一 live↔导出差异，不是导出侧截断。
            "图例条目较多：live 界面仅显示前 8 条，导出件绘制全部条目",
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
            # review-r1 修复：发射端 detail 是「请求→实际上限」箭头对（如
            # 55→50），原模板「仅渲染前 {detail} 页」会插值成病句
            # 「仅渲染前 55→50 页」。模板改为对 detail 做原样括注。
            "atlas 页数超出上限（{detail}），仅保留上限内页面",
        ),
        # —— 地形 / 3D 披露 ——
        RenderDiagnosticSpec(
            "terrain_3d_scale_caveat", "info",
            "3D 地形/倾斜视角下比例尺按平面口径计算，可能与视觉距离不符",
        ),
        # —— V6（ADR-0120）多帧聚合元披露 ——
        RenderDiagnosticSpec(
            "diagnostics_truncated", "warning",
            "诊断条目超出导出预算，超出部分未进入产物披露（上限 {detail} 条）",
        ),
        # —— V6（ADR-0120 W6）确定性标签碰撞 ——
        RenderDiagnosticSpec(
            "label_collision_relaxed", "info",
            "标签碰撞求解：部分标签位移或省略（{detail}）",
        ),
        RenderDiagnosticSpec(
            "label_budget_exceeded", "warning",
            "标签数量超出导出预算（{detail}），超出部分未渲染",
        ),
        # —— 高 DPI 渲染策略（ADR-0157 P1）——
        RenderDiagnosticSpec(
            "highdpi_rerender_timeout_degraded", "warning",
            "高 DPI 重渲染等待超时，已降级为当前分辨率画布导出（{detail}）",
        ),
        RenderDiagnosticSpec(
            "raster_tile_detail_limited_highdpi", "info",
            "高 DPI 导出下栅格瓦片源仍按原 zoom 取图，瓦片细节不随分辨率提升（{detail}）",
        ),
        RenderDiagnosticSpec(
            "extent_overflow_data", "warning",
            "数据范围超出出图范围，超界部分以背景呈现（{detail}）",
        ),
        RenderDiagnosticSpec(
            "extent_fit_timeout_degraded", "warning",
            "出图范围相机适配未在截止内完成，已回退视口裁切语义（{detail}）",
        ),
        RenderDiagnosticSpec(
            "cmyk_approximate_raster", "info",
            "栅格导出件无法承载真 CMYK 分色，出版档仅提供出血/裁切几何与近似色彩标记",
        ),
        RenderDiagnosticSpec(
            "pdf_cjk_font_embedded", "info",
            "PDF 文本层嵌入 Noto Sans SC 子集字体，中文可选取/可检索",
        ),
        # —— V6（ADR-0120 W8）矢量 PDF publication ——
        RenderDiagnosticSpec(
            "raster_layer_unavailable_vector_pdf", "warning",
            "栅格/瓦片图层无法以矢量形式进入 PDF，已在导出件中省略（layer: {detail}）",
        ),
        RenderDiagnosticSpec(
            "pdf_font_fallback", "info",
            "PDF 文本使用回退字体渲染（未找到首选 CJK 字体）",
        ),
        RenderDiagnosticSpec(
            "vector_pdf_unavailable", "warning",
            "服务端矢量 PDF 引擎不可用，已回退栅格导出",
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
    # review-r2 修复：detail 先截断再插值 —— message 与 detail 同界，防止
    # 超长 detail 经 message 模板进入持久化载荷/sidecar（有界披露不变式）。
    detail = (detail or "")[:MAX_DETAIL_CHARS]
    message = spec.message.format(detail=detail) if detail else spec.message
    return RenderDiagnostic(
        code=code,
        severity=spec.severity,
        message=message,
        detail=detail,
        layer_id=layer_id[:MAX_ID_CHARS] if layer_id else None,
        component_id=component_id[:MAX_ID_CHARS] if component_id else None,
    )


def catalog_section() -> List[Dict[str, str]]:
    """component-catalog.generated.json 的 ``renderDiagnostics`` 段。"""
    return [
        {"code": s.code, "severity": s.severity, "message": s.message}
        for s in (
            RENDER_DIAGNOSTICS[k] for k in sorted(RENDER_DIAGNOSTICS)
        )
    ]


#: V6（ADR-0120 W1）死码门：code → 真实发射点清单。
#: 后端条目为可导入模块路径（测试断言该模块源码含此码）；前端条目以
#: ``frontend/`` 前缀标记（测试断言文件存在且源码含此码）。
#: 新增诊断码必须同时登记 ≥1 个真实发射点，否则契约测试红灯
#: （ADR-0118 D1 死码禁止的自动化收口，取代仅注释级约束）。
EMITTER_REGISTRY: Dict[str, Tuple[str, ...]] = {
    "chart_ref_unavailable": ("frontend/lib/map-kit/export-chrome.ts",),
    "table_ref_unavailable": ("frontend/lib/map-kit/export-chrome.ts",),
    "chart_kind_unsupported_export": ("frontend/lib/map-kit/export-chrome.ts",),
    "component_skipped_invalid": ("frontend/lib/map-kit/export-chrome.ts",),
    "label_truncated": (
        "app.services.mapspec_to_svg",
        "frontend/lib/map-kit/export-chrome.ts",
        "frontend/lib/map-kit/vector-svg-export.ts",
    ),
    "legend_entries_truncated": ("frontend/lib/map-kit/export-chrome.ts",),
    "features_truncated": (
        "app.services.mapspec_to_svg",
        "frontend/lib/map-kit/export-chrome.ts",
    ),
    "export_timeout_partial": (
        "app.services.mapspec_to_svg",
        "app.services.report_service",
        "frontend/lib/map-kit/export-chrome.ts",
    ),
    "vector_svg_fallback_raster": (
        "frontend/lib/map-kit/exporter.ts",
        "frontend/lib/map-kit/export-chrome.ts",
        "frontend/lib/map-kit/vector-svg-export.ts",
    ),
    "basemap_omitted_vector_svg": (
        "frontend/lib/map-kit/export-chrome.ts",
        "frontend/lib/map-kit/vector-svg-export.ts",
    ),
    "pdf_text_rasterized_cjk": (
        "frontend/lib/map-kit/exporter.ts",
        "frontend/lib/map-kit/export-chrome.ts",
    ),
    "comparison_second_view_not_exported": (
        "frontend/lib/map-kit/exporter.ts",
        "frontend/lib/map-kit/export-chrome.ts",
    ),
    "comparison_export_composed": (
        "frontend/lib/map-kit/exporter.ts",
        "frontend/lib/map-kit/export-chrome.ts",
    ),
    "cartogram_unsupported": (
        "frontend/lib/map-kit/frame-composer.ts",
        "frontend/lib/map-kit/export-chrome.ts",
    ),
    "small_multiple_panel_skipped": (
        "frontend/lib/map-kit/exporter.ts",
        "frontend/lib/map-kit/frame-composer.ts",
        "frontend/lib/map-kit/export-chrome.ts",
    ),
    "atlas_page_skipped": (
        "frontend/lib/map-kit/exporter.ts",
        "frontend/lib/map-kit/frame-composer.ts",
        "frontend/lib/map-kit/export-chrome.ts",
    ),
    "atlas_page_limit_truncated": (
        "frontend/lib/map-kit/frame-composer.ts",
        "frontend/lib/map-kit/export-chrome.ts",
    ),
    "terrain_3d_scale_caveat": (
        "frontend/lib/map-kit/exporter.ts",
        "frontend/lib/map-kit/export-chrome.ts",
    ),
    # ADR-0157 P1：高 DPI 渲染策略 —— 发射器在 lib/export/highdpi.ts 单源；
    # export-chrome.ts 持有前端词表联合类型（同一字面量）。
    "highdpi_rerender_timeout_degraded": (
        "frontend/lib/export/highdpi.ts",
        "frontend/lib/map-kit/export-chrome.ts",
    ),
    "raster_tile_detail_limited_highdpi": (
        "frontend/lib/export/highdpi.ts",
        "frontend/lib/map-kit/export-chrome.ts",
    ),
    # ADR-0157 P4：所见即所得范围契约 —— 版面描述中间层装配器发射。
    "extent_overflow_data": (
        "frontend/lib/export/layout-description.ts",
        "frontend/lib/map-kit/export-chrome.ts",
    ),
    "extent_fit_timeout_degraded": (
        "frontend/lib/map-kit/exporter.ts",
        "frontend/lib/map-kit/export-chrome.ts",
    ),
    "cmyk_approximate_raster": (
        "frontend/lib/map-kit/exporter.ts",
        "frontend/lib/map-kit/export-chrome.ts",
    ),
    "pdf_cjk_font_embedded": (
        "frontend/lib/map-kit/exporter.ts",
        "frontend/lib/map-kit/export-chrome.ts",
    ),
    # diagnostics_truncated 的发射器是本模块 DiagnosticSink（publication
    # 多帧聚合路径的真实消费方，见 render_publication_pdf / vector-pdf 链）。
    "diagnostics_truncated": ("app.lib.cartography.render_diagnostics",),
    "label_collision_relaxed": (
        "app.services.mapspec_to_svg",
        "frontend/lib/mapspec-compiler/mapspec-to-svg.ts",
    ),
    "label_budget_exceeded": (
        "app.services.mapspec_to_svg",
        "frontend/lib/mapspec-compiler/mapspec-to-svg.ts",
    ),
    "raster_layer_unavailable_vector_pdf": ("app.services.publication_export",),
    "pdf_font_fallback": ("app.services.publication_export",),
    # vector_pdf_unavailable 的发射器是导出路由（503 结构化错误回退提示）
    "vector_pdf_unavailable": ("app.api.routes.map",),
}


#: 多帧聚合时单帧诊断子上限（publication 路径逐帧编译的披露预算）。
#: 设计约束（R1-M3）：全局 64 条封顶下，50 帧的高频逐帧披露可能把失败类
#: 诊断挤出预算 —— 聚合器因此按帧划分子配额并在溢出时显式发
#: diagnostics_truncated 元披露，而不是静默丢弃。
MAX_DIAGNOSTICS_PER_FRAME = 8


class DiagnosticSink:
    """多帧/多阶段导出的诊断聚合器（全局封顶 + 溢出显式披露）。

    - ``add``：超全局封顶后停止接收，置 ``truncated`` 并（首次溢出时）
      追加一条 ``diagnostics_truncated`` 元诊断；不静默吞。
    - ``extend_frame``：单帧子配额入口（``MAX_DIAGNOSTICS_PER_FRAME``），
      帧级溢出同样计入 truncation 披露（publication render_publication_pdf
      逐帧消费 —— R1-M3 接线）。
    """

    def __init__(self, max_total: int = MAX_DIAGNOSTICS_PER_EXPORT) -> None:
        self._items: List[RenderDiagnostic] = []
        self._max_total = max_total
        self.truncated: bool = False

    def add(self, item: Optional[RenderDiagnostic]) -> bool:
        """接收一条诊断；返回是否被接收（None/超限返回 False）。"""
        if item is None:
            return False
        if len(self._items) >= self._max_total:
            self._mark_truncated()
            return False
        self._items.append(item)
        return True

    def extend_frame(self, frame_items: List[RenderDiagnostic]) -> int:
        """接收单帧诊断清单（受帧级子配额约束），返回实际接收条数。"""
        accepted = 0
        for item in frame_items[:MAX_DIAGNOSTICS_PER_FRAME]:
            if not self.add(item):
                break
            accepted += 1
        if len(frame_items) > MAX_DIAGNOSTICS_PER_FRAME:
            self._mark_truncated()
        return accepted

    def _mark_truncated(self) -> None:
        if self.truncated:
            return
        self.truncated = True
        meta = diagnostic(
            "diagnostics_truncated",
            detail=str(MAX_DIAGNOSTICS_PER_EXPORT),
        )
        # 元披露不受全局封顶约束（cap+1）：溢出本身必须可观测，
        # 否则静默丢弃恰好吞掉最关键的失败披露（R1-M3）。
        if meta is not None:
            self._items.append(meta)

    def items(self) -> List[RenderDiagnostic]:
        return list(self._items)

    def to_payload(self) -> List[Dict[str, Any]]:
        return [item.to_dict() for item in self._items]


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
        # review-r2 修复：与 diagnostic() 同口径 —— 先截断再插值 message，
        # 标识符同界；外部载荷无论如何形状，入库的每一段都有界。
        detail = detail[:MAX_DETAIL_CHARS]
        layer_id = item.get("layer_id")
        layer_id = layer_id[:MAX_ID_CHARS] if isinstance(layer_id, str) and layer_id else None
        component_id = item.get("component_id")
        component_id = (
            component_id[:MAX_ID_CHARS]
            if isinstance(component_id, str) and component_id
            else None
        )
        entry = RenderDiagnostic(
            code=code,
            severity=spec.severity,
            message=spec.message.format(detail=detail) if detail else spec.message,
            detail=detail,
            layer_id=layer_id,
            component_id=component_id,
        )
        accepted.append(entry.to_dict())
    return accepted, rejected
