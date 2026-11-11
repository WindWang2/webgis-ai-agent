"""Publication Vector PDF Export (V6, ADR-0120 W8).

MapSpec → 真矢量 PDF（可选文本、出版级整饰）的同步领域服务。

链路：authoritative schema 校验 → canonical document → 逐帧孪生编译
（publication chrome + 确定性标签碰撞，spec 驱动）→ HTML（@page = 帧尺寸，
系统字体栈）→ WeasyPrint write_pdf。

安全与资源包络（R1-C1 / R1-M6 / R1-M9）：
- WeasyPrint ``url_fetcher`` 全拒 —— PDF 引擎不发起任何网络/文件读取
  （SSRF/任意读封闭）；栅格/瓦片图层**诚实省略**并披露
  （raster_layer_unavailable_vector_pdf）。
- write_pdf 进程级互斥（官方不保证线程安全，fontconfig/pango 全局态）；
  ``acquire`` 非阻塞 → 忙则结构化拒绝（vector_pdf_busy），饱和不排队。
- 多帧诊断经 DiagnosticSink（帧级子配额 + 溢出元披露，R1-M3）。
- 预算：帧数 ≤ 50、页尺寸 ≤ A0、载荷字节上限由路由层执行；
  编译本身受孪生协作式超时约束（wait_for 仅保护调用方返回时效）。
- weasyprint 缺席 → ``PublicationUnavailableError``（调用方回退栅格 +
  vector_pdf_unavailable 披露），不伪成功。
"""
from __future__ import annotations

from typing import Callable, TypeVar

_RenderResult = TypeVar("_RenderResult")
import html as _html
import io
import logging
import threading
from dataclasses import dataclass, field as _dc_field
from typing import Any, Dict, List, Optional, Tuple

from app.lib.cartography.atlas_layout import MAX_ATLAS_PAGES
from app.lib.cartography.label_collision import MAX_LABELS_PER_EXPORT
from app.lib.cartography.publication_constraint_layout import ConstraintLayoutReport
from app.lib.cartography.mapspec_schema import (
    MAX_SPEC_FRAMES,
    MapSpecSchemaError,
    require_parseable_mapspec,
)
from app.lib.cartography.render_diagnostics import (
    RENDER_DIAGNOSTICS,
    DiagnosticSink,
    diagnostic,
)
from app.lib.cartography.data_tiers import TIER_EXPORT_FEATURES as EXPORT_MAX_FEATURES  # §8.1.1 单点归 V11
from app.services.mapspec_to_svg import (
    compile_mapspec_to_svg_detailed,
    resolve_spec_timeout_ms,
)

logger = logging.getLogger(__name__)

try:  # 与 report_service 同模式：可选依赖，缺席诚实降级
    import weasyprint  # noqa: F401
except (ImportError, OSError):  # pragma: no cover - 环境相关
    # V9 data-lifecycle 线顺带修复（#1221 D-7 同类）：Windows 无 GTK 时
    # weasyprint 在 import 期抛 OSError（缺 libpango）而非 ImportError ——
    # 只捕 ImportError 会让整个 app import 链（含测试收集）不可达。
    weasyprint = None

#: 页面尺寸上限（A0 = 841×1189mm 的毫米值以内）；超限帧拒绝。
# C14：纸型/帧几何单一真相移入 lib（publication_ir）；此处别名再导出
# （历史导入面不变），数值由消费方 golden 对账锁定。
from app.lib.cartography.plan_ir import digest_of
from app.lib.cartography.publication_ir import (  # noqa: E402
    MAX_PAGE_MM,  # noqa: F401  (历史导入面再导出：单一真相在 publication_ir)
    PAGE_PROFILES,  # noqa: F401  (同上)
    PUBLICATION_IR_VERSION,
    AtlasPolicy,
    PageLayoutIR,
    PublicationIR,
    enabled_frames,
    page_geometry as _page_geometry_lib,
    plan_publication_pages,
    publication_preflight,
)
#: vendored CJK 子集字体（无系统 CJK 字体时的 @font-face 内嵌源）。
_VENDORED_CJK_FONT = "app/lib/cartography/fonts/NotoSansSC-Regular-subset.ttf"
#: 多帧累积 SVG 字节预算（R2-M5：解析 DOM 前的内存上界锚点）。
_MAX_TOTAL_SVG_BYTES = 32 * 1024 * 1024
_MAX_PAGE_SVG_BYTES = 8 * 1024 * 1024
#: 系统字体栈（fontconfig 探测 + CSS 双保险；无内嵌字体资产）。
CSS_FONT_STACK = (
    '"Noto Sans CJK SC", "Noto Sans CJK", "Source Han Sans SC", '
    '"WenQuanYi Micro Hei", "Microsoft YaHei", "PingFang SC", sans-serif'
)

# ── WeasyPrint 串行化（R1-M6：进程级互斥，饱和即拒）───────────────────────
_WEASYPRINT_LOCK = threading.Lock()

_FONT_CACHE: Dict[str, bool] = {}
_FONT_CACHE_LOCK = threading.Lock()


class PublicationUnavailableError(Exception):
    """矢量 PDF 引擎不可用（weasyprint 缺席）—— 调用方回退栅格。"""


class PublicationBusyError(Exception):
    """WeasyPrint 渲染槽位占用中（串行化饱和）—— 调用方稍后重试。"""


# ── 页级编译接缝（C15 批量图册：同步链与 atlas batch executor 共用）────────
#
# 提取自 render_publication_pdf 的逐页循环体：封面/帧覆盖/过滤页文档解析 →
# 孪生 SVG 编译 → 累积预算闸。语义（诊断码/明细串/预算数学）逐字保持 ——
# 同步路径改走本接缝后输出字节与诊断负载不变（由既有 atlas pipeline/IR
# golden 测试锁定）。失败以 PageCompileFailure 携带 typed 证据上抛：
# 同步链按既有政策 skip + 诊断；批量链按页级失败证据化（不静默空白）。

@dataclass
class PageCompileOutcome:
    """单页编译成功结果（svg 为最终页面体；无载荷摘要随行）。

    G15：产品页额外携带 ``html_body``（CSS 定位槽位组合）—— WeasyPrint
    内嵌 SVG 渲染器不支持 g transform/多嵌套 svg（实证：只渲染首个嵌套
    svg、transform 被忽略），同页多画幅在 PDF 路径必须走 HTML 组合；
    svg 字段仍是自包含产品页 SVG（单文件导出/结构核验消费者用）。
    """

    svg: str
    width_mm: float
    height_mm: float
    page: PageLayoutIR
    svg_bytes: int = 0
    html_body: Optional[str] = None
    frame_diagnostics: List[Any] = _dc_field(default_factory=list)
    rendered_component_types: List[str] = _dc_field(default_factory=list)
    omitted_components: List[Dict[str, Any]] = _dc_field(default_factory=list)


class PageCompileFailure(Exception):
    """页编译失败（code/detail 与同步链诊断完全同形）。"""

    def __init__(self, code: str, detail: str, exc: Optional[Exception] = None) -> None:
        self.code = code
        self.detail = detail
        self.exc = exc
        super().__init__(f"{code}: {detail}")


def compile_publication_page(
    doc: Dict[str, Any],
    page: PageLayoutIR,
    pub_ir: PublicationIR,
    *,
    frames_lookup: List[Any],
    target_dpi: int,
    max_labels: int,
    svg_budget_remaining: int,
) -> PageCompileOutcome:
    """单页 → :class:`PageCompileOutcome`；失败抛 :class:`PageCompileFailure`。

    ``svg_budget_remaining``：本页加入前剩余的累积 SVG 字节预算
    （``_MAX_TOTAL_SVG_BYTES - 已累积``）—— 与原循环内 ``_accumulated +
    len(svg) > _MAX_TOTAL_SVG_BYTES`` 判据等价（len > remaining ⇔ 超限）。
    """
    page_w = page.paper.width_mm
    page_h = page.paper.height_mm
    if page.cover:
        svg = _render_toc_svg(pub_ir, page)
        byte_count = len(svg.encode("utf-8"))
        if byte_count > min(svg_budget_remaining, _MAX_PAGE_SVG_BYTES):
            raise PageCompileFailure("atlas_page_limit_truncated", "cover exceeds SVG byte budget")
        return PageCompileOutcome(
            svg=svg, width_mm=page_w, height_mm=page_h,
            page=page, svg_bytes=byte_count,
        )
    # G15 product 页：同页 N 画幅（frame_slots）—— 逐槽解析有效文档 +
    # 槽级编译 + 命名空间化组合（单帧编译器复用，坐标空间隔离）。
    if getattr(page, "frame_slots", None):
        return _compile_product_page(
            doc, page, pub_ir, target_dpi=target_dpi, max_labels=max_labels,
            svg_budget_remaining=svg_budget_remaining,
        )
    # 页文档解析：帧引用 / 过滤页 / 单帧整幅（原循环体逐字）
    if page.frame_index is not None:
        frame = frames_lookup[page.frame_index] if page.frame_index < len(frames_lookup) else None
        frame_doc = _apply_frame_overrides(doc, frame)
    elif getattr(page, "time_window", None):
        # GOAL 03：time 页 —— 帧窗口过滤 + 页帧 cursor（时间注记随页）。
        from app.services.gis_harness.temporal_products.time_filter import (
            apply_time_window_to_doc,
        )

        frame_doc = apply_time_window_to_doc(doc, page.time_window)
    elif page.filter_layer_id:
        frame_doc = _page_filtered_doc(doc, page)
    else:
        frame_doc = doc
    try:
        comp = compile_mapspec_to_svg_detailed(
            frame_doc,
            target_dpi=target_dpi,
            width=int(page_w * 4),
            height=int(page_h * 4),
            padding=24,
            include_chrome=True,
            bounds=page.bounds,
            max_labels=max_labels,
            max_features=min(_effective_max_features(frame_doc), EXPORT_MAX_FEATURES),
            timeout_ms=min(resolve_spec_timeout_ms(frame_doc), 30000.0),
        )
    except Exception as ex:  # noqa: BLE001 — 同步链既有 skip 政策的同一事实源
        raise PageCompileFailure(
            "atlas_page_skipped",
            f"page {page.page_id}: {type(ex).__name__}",
            exc=ex,
        ) from ex
    svg_bytes = len(comp.svg.encode("utf-8"))
    if svg_bytes > min(svg_budget_remaining, _MAX_PAGE_SVG_BYTES):
        raise PageCompileFailure(
            "atlas_page_limit_truncated",
            f"svg budget {_MAX_TOTAL_SVG_BYTES} bytes at page {page.page_id}",
        )
    from app.lib.cartography.render_diagnostics import RenderDiagnostic

    frame_items = [
        RenderDiagnostic(
            code=d.get("code", ""), severity=d.get("severity", "info"),
            message=d.get("message", ""), detail=d.get("detail", ""),
            layer_id=d.get("layer_id"), component_id=d.get("component_id"),
        )
        for d in comp.diagnostics
        if d.get("code") in RENDER_DIAGNOSTICS
    ]
    return PageCompileOutcome(
        svg=comp.svg, width_mm=page_w, height_mm=page_h, page=page,
        svg_bytes=svg_bytes,
        frame_diagnostics=frame_items,
        rendered_component_types=[str(t) for t in (comp.rendered_component_types or [])],
        omitted_components=[dict(o) for o in (comp.omitted_components or [])
                            if isinstance(o, dict)],
    )


def assemble_pages_pdf(
    page_specs: List[Tuple[str, str, float, float]],
    *,
    title: str,
    sink: DiagnosticSink,
) -> bytes:
    """页面 SVG 列表 → PDF bytes（命名 @page + WeasyPrint 互斥 + CJK 内嵌）。

    单页/多页两态与既有 render_publication_pdf 内联实现逐字一致（诊断
    ``pdf_cjk_font_embedded`` 同位落 sink）；pypdf 页数计数由调用方执行。
    """
    if len(page_specs) > 1:
        rules = [
            f"@page {name} {{ size: {w_mm}mm {h_mm}mm; margin: 0; }}\n"
            f".page-{name} {{ page: {name}; }}"
            for (name, _svg, w_mm, h_mm) in page_specs
        ]
        font_face = _cjk_font_face_css()
        font_stack = f"'Noto Sans SC Embedded', {CSS_FONT_STACK}" if font_face else CSS_FONT_STACK
        svg_text_rule = (
            f"svg, svg text {{ font-family: {font_stack}; }}\n" if font_face else ""
        )
        html_doc = (
            '<!DOCTYPE html><html><head><meta charset="utf-8"><style>'
            f"{font_face}\n"
            f"html, body {{ margin: 0; font-family: {font_stack}; }}\n"
            f"{svg_text_rule}"
            # G15：HTML 体页（fp-page）自带排版；svg 100% 规则只作用于
            # 整页 SVG 页（旧语义逐字节不变），不覆盖 fp 槽位 svg 内联尺寸。
            "div.page > svg { width: 100%; height: 100%; }\n"
            ".page { page-break-after: always; }\n"
            + "\n".join(rules)
            + "</style></head><body>"
            + "".join(
                f'<div class="page page-{name}">{body}</div>'
                for (name, body, _w, _h) in page_specs
            )
            + "</body></html>"
        )
        if font_face:
            sink.add(diagnostic("pdf_cjk_font_embedded"))
    else:
        _name, svg0, w0, h0 = page_specs[0]
        html_doc = _svg_to_page_html(svg0, w0, h0, title=title)
        if _cjk_font_face_css():
            sink.add(diagnostic("pdf_cjk_font_embedded"))

    from weasyprint import HTML

    try:
        return render_pdf_exclusive(
            lambda: HTML(
                string=html_doc,
                url_fetcher=_deny_url_fetcher,  # SSRF/外联全拒（R1-M9）
                base_url=None,
            ).write_pdf()
        )
    except PublicationBusyError:
        raise
    except Exception as ex:
        logger.error("publication pdf: weasyprint render failed: %s", ex)
        raise


def count_pdf_pages(pdf_bytes: bytes, fallback: int) -> int:
    """pypdf 页数计数（缺席/异常 → 调用方提供的渲染计数兜底）。"""
    try:
        import pypdf

        return len(pypdf.PdfReader(io.BytesIO(pdf_bytes)).pages)
    except Exception:
        return fallback


@dataclass
class PublicationPdfResult:
    pdf: bytes
    page_count: int
    diagnostics: List[Dict[str, Any]] = _dc_field(default_factory=list)
    disclosures: List[Dict[str, str]] = _dc_field(default_factory=list)
    font_cjk: bool = True
    frames_rendered: int = 0
    frames_skipped: int = 0
    # V7（Goal 08 Phase H）：生效 DPI（用户值被钳制后如实披露 —— 不静默）。
    target_dpi: int = 300
    # F14（ADR-0211 增补 D3）：多帧聚合的组件覆盖回执 ——
    # rendered = 去重排序的已渲染组件族（≤24）；omitted = 结构化降级条目
    # {component_id, type, code}（≤16）。进 sidecar 与 lineage metadata。
    component_coverage: Dict[str, List[Any]] = _dc_field(
        default_factory=lambda: {"rendered": [], "omitted": []})
    # C14：PublicationIR 回执面 —— 版面版本、结构指纹、atlas 页摘要。
    layout_version: str = PUBLICATION_IR_VERSION
    spec_fingerprint: str = ""
    atlas: bool = False
    atlas_pages: List[Dict[str, Any]] = _dc_field(default_factory=list)
    # Explicit page_profile only. Default exports keep this as not_evaluated
    # and do not rewrite the compiled document.
    constraint_layout: ConstraintLayoutReport = _dc_field(
        default_factory=lambda: {"constraint_layout": "not_evaluated"})


def _probe_cjk_font() -> bool:
    """进程级缓存（固定键集 {"cjk"}，R1-N1）：系统是否存在 CJK 字体。"""
    with _FONT_CACHE_LOCK:
        if "cjk" in _FONT_CACHE:
            return _FONT_CACHE["cjk"]
    found = False
    # Fast path: known Debian/Ubuntu package locations (fonts-noto-cjk).
    from pathlib import Path as _Path

    for candidate in (
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    ):
        if _Path(candidate).is_file():
            found = True
            break
    if not found:
        try:  # fontconfig is what WeasyPrint/Pango actually consult
            import subprocess

            out = subprocess.run(
                ["fc-list", ":lang=zh", "file"],
                check=False,
                capture_output=True,
                text=True,
                timeout=5,
            )
            found = bool((out.stdout or "").strip())
        except Exception:  # pragma: no cover
            found = False
    if not found:
        try:  # matplotlib 已是 requirements 依赖；fontManager 扫描结果进程内稳定
            import matplotlib.font_manager as fm

            keywords = (
                "cjk", "noto sans cjk", "source han", "wqy",
                "simhei", "simsun", "yahei", "pingfang",
            )
            found = any(
                any(kw in f.name.lower() for kw in keywords)
                for f in fm.fontManager.ttflist
            )
        except Exception:  # pragma: no cover - 字体子系统异常时保守返回 False
            found = False
    with _FONT_CACHE_LOCK:
        _FONT_CACHE["cjk"] = found
    return found


_FONT_FACE_CACHE: Optional[str] = None
_FONT_FACE_LOCK = threading.Lock()


def _cjk_font_face_css() -> str:
    """无系统 CJK 字体时，返回内嵌 vendored Noto Sans SC 子集的 @font-face
    CSS（data-URI，WeasyPrint 直接解嵌 PDF 文本层）；否则空串。

    F14（ADR-0211 增补 D6）：publication 链此前只有 CSS 字体栈 + probe 披露，
    无嵌入保证 —— 无 CJK 字体环境下中文退化为 fallback（tofu 风险）。
    base64 进程内缓存（约 3.1MB 一次性成本）；字体文件缺失/读取失败 →
    空串（回退既有 pdf_font_fallback 披露路径）。
    """
    global _FONT_FACE_CACHE
    with _FONT_FACE_LOCK:
        if _FONT_FACE_CACHE is not None:
            return _FONT_FACE_CACHE
        css = ""
        if not _probe_cjk_font():
            try:
                import base64
                from pathlib import Path

                font_path = Path(__file__).resolve().parent.parent / (
                    "lib/cartography/fonts/NotoSansSC-Regular-subset.ttf")
                data = font_path.read_bytes()
                b64 = base64.b64encode(data).decode("ascii")
                css = (
                    "@font-face { font-family: 'Noto Sans SC Embedded'; "
                    f"src: url(data:font/ttf;base64,{b64}); "
                    "font-weight: normal; font-style: normal; }\n"
                )
            except Exception:  # noqa: BLE001 — 内嵌失败回退系统栈（诚实披露）
                css = ""
        _FONT_FACE_CACHE = css
        return css


def _effective_max_features(frame_doc: Dict[str, Any]) -> int:
    """spec.thresholds.maxFeatures（合法时）否则 EXPORT_MAX_FEATURES —— 调用方再做绝对封顶。"""
    import math as _math

    thr = frame_doc.get("thresholds")
    if isinstance(thr, dict):
        val = thr.get("maxFeatures")
        if isinstance(val, (int, float)) and not isinstance(val, bool):
            try:
                if _math.isfinite(float(val)) and val > 0:
                    return int(val)
            except (ValueError, TypeError, OverflowError):
                pass
    return EXPORT_MAX_FEATURES


def _frame_geometry(frame: Optional[Dict[str, Any]]) -> Tuple[float, float, Optional[List[float]]]:
    """帧 → (页宽mm, 页高mm, bounds)。缺省 A4 landscape 297×210。

    C14 起委托 lib 单一真相（``publication_ir.page_geometry``，逐字提取）。
    """
    return _page_geometry_lib(frame)


def _apply_frame_overrides(doc: Dict[str, Any], frame: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """帧 layerOverrides 逐键 deep-merge（R1-M5：未提及键保留）。

    返回轻拷贝文档（layers 列表浅拷贝 + 命中层浅拷贝）—— 不改写入参。
    """
    overrides = frame.get("layerOverrides") if isinstance(frame, dict) else None
    if not isinstance(overrides, dict) or not overrides:
        return doc
    out = dict(doc)
    layers = list(out.get("layers") or [])
    new_layers: List[Dict[str, Any]] = []
    for layer in layers:
        if not isinstance(layer, dict):
            new_layers.append(layer)
            continue
        ov = overrides.get(layer.get("id"))
        if not isinstance(ov, dict):
            new_layers.append(layer)
            continue
        merged = dict(layer)
        for k, v in ov.items():
            if k != "id":  # id 不允许覆写
                merged[k] = v
        new_layers.append(merged)
    out["layers"] = new_layers
    return out


_SLOT_ID_RE = None  # lazy compile


def _namespace_slot_svg(svg: str, prefix: str) -> str:
    """槽 SVG 的 id/url(#ref) 命名空间化（同页多幅不碰撞；防御性 ——
    当前编译器不发射 id，未来发射时自动被覆盖）。"""
    import re

    def _sub_id(m: "re.Match[str]") -> str:
        return f'id="{prefix}{m.group(1)}"'

    def _sub_ref(m: "re.Match[str]") -> str:
        return f'url(#{prefix}{m.group(1)})'

    svg = re.sub(r'id="([^"]+)"', _sub_id, svg)
    svg = re.sub(r'url\(#([^)]+)\)', _sub_ref, svg)
    return svg


def _slot_inner(svg: str) -> "tuple[str, str]":
    """完整 <svg> → (内部内容, 原始 viewBox)。

    编译器按自身几何（mm 口径）计算 viewBox 并靠 preserveAspectRatio
    缩放到 width/height —— 重组必须携带原 viewBox（截面积内容坐标在
    原坐标系，替换成槽像素坐标会让内容错位/溢出 = 空白帧）。
    """
    import re

    m = re.search(r"<svg[^>]*>", svg)
    vb = ""
    if m:
        vbm = re.search(r'viewBox="([^"]+)"', m.group(0))
        if vbm:
            vb = vbm.group(1)
    c = re.search(r"<svg[^>]*>(.*)</svg>", svg, re.DOTALL)
    return (c.group(1) if c else svg, vb)


def _shared_legend_band_svg(
    doc: Dict[str, Any], page: PageLayoutIR, w_px: int,
    shared_legend_spec: Optional[Dict[str, Any]],
) -> str:
    """共享图例带（产品 shared_expression 的单一图例；有界、确定性）。

    图例事实 = 共享槽**有效文档**里被 resolver 盖章的 legend_spec
    （breaks/labels/palette_colors 与共享域同源 —— 不读主文档层图例，
    那是局部分级）。"""
    from app.lib.cartography.render_scene import derive_legend_items

    product_raw = doc.get("frame_product")
    product = product_raw if isinstance(product_raw, dict) else {}
    shared = product.get("shared_expression")
    if not isinstance(shared, dict) or not isinstance(shared_legend_spec, dict):
        return ""
    model = derive_legend_items(shared_legend_spec)
    entries = (model or {}).get("entries") or []
    if not entries:
        return ""
    unit = str(shared.get("unit") or "")
    parts: List[str] = []
    x = 24
    y = 10
    for e in entries[:12]:
        color = str(e.get("color") or "#cccccc")
        label = str(e.get("label") or "")
        parts.append(
            f'<rect x="{x}" y="{y}" width="18" height="12" fill="{color}" '
            f'fill-opacity="0.6" stroke="#94a3b8" stroke-width="0.5"/>'
            f'<text x="{x + 24}" y="{y + 10}" font-family="sans-serif" '
            f'font-size="12" fill="#0f172a">{label}</text>'
        )
        x += 24 + 8 * min(len(label), 18) + 24
        if x > w_px - 160:
            break
    field_label = str(shared.get("field") or "") + (f"（{unit}）" if unit else "")
    header = (
        f'<text x="24" y="-2" font-family="sans-serif" font-size="11" '
        f'fill="#475569">Shared classification · {field_label}</text>'
    )
    return header + "".join(parts)


def _compile_product_page(
    doc: Dict[str, Any],
    page: PageLayoutIR,
    pub_ir: PublicationIR,
    *,
    target_dpi: int,
    max_labels: int,
    svg_budget_remaining: int,
) -> PageCompileOutcome:
    """G15 产品页编译：逐槽有效文档 → 槽 SVG → 单页组合。

    每槽：resolver 解析（layerOverrides/where/time_window/共享分级）→
    单帧编译器在槽像素尺寸编译（shared 槽 include_chrome=False —— 图例
    由页面共享带承载；independent/shared_plus_frame 槽带各自 chrome）→
    槽内容命名空间化 + clipPath 隔离坐标空间。任一槽失败 → 整页失败
    （typed 携 frame_id；「某帧失败时请求按定义失败或有显著部分产物披露」
    的严格取失败 + 诊断，不留静默空槽）。
    """
    from app.services.gis_harness.frame_products.resolver import (
        FrameResolutionError,
        resolve_frame_document,
    )

    page_w = page.paper.width_mm
    page_h = page.paper.height_mm
    w_px = page.paper.width_px
    h_px = page.paper.height_px
    margin_px = int(0.06 * w_px)

    slot_parts: List[str] = []
    frame_diagnostics: List[Any] = []
    total_bytes = 0
    rendered_types: set = set()
    shared_legend_spec: Optional[Dict[str, Any]] = None
    _slot_svgs: List[str] = []
    _slot_viewboxes: List[str] = []
    for i, slot in enumerate(page.frame_slots or []):
        try:
            res = resolve_frame_document(doc, slot.frame_id)
        except FrameResolutionError as ex:
            raise PageCompileFailure(
                "product_frame_unresolvable",
                f"frame {slot.frame_id}: {ex.code} — {ex}",
                exc=ex,
            ) from ex
        include_chrome = slot.legend in ("independent", "shared_plus_frame")
        slot_w = max(32, int(slot.w * w_px))
        slot_h = max(32, int(slot.h * h_px))
        try:
            comp = compile_mapspec_to_svg_detailed(
                res.document,
                target_dpi=target_dpi,
                width=slot_w,
                height=slot_h,
                padding=6,
                include_chrome=include_chrome,
                bounds=slot.bounds,
                max_labels=max_labels,
                max_features=min(_effective_max_features(res.document),
                                 EXPORT_MAX_FEATURES),
                timeout_ms=min(resolve_spec_timeout_ms(res.document), 30000.0),
            )
        except Exception as ex:  # noqa: BLE001 — 槽失败 = 页失败（typed 证据）
            raise PageCompileFailure(
                "product_frame_render_failed",
                f"frame {slot.frame_id}: {type(ex).__name__}",
                exc=ex,
            ) from ex
        if shared_legend_spec is None and not include_chrome:
            # 第一个 shared 槽的有效文档图例 = 页面共享图例事实源。
            for layer in (res.document.get("layers") or []):
                if isinstance(layer, dict) and isinstance(
                        layer.get("legend_spec"), dict):
                    shared_legend_spec = dict(layer["legend_spec"])
                    break
        _slot_svgs.append(comp.svg)
        inner, slot_vb = _slot_inner(comp.svg)
        _slot_viewboxes.append(slot_vb)
        inner = _namespace_slot_svg(inner, f"fp{i}-")
        x = int(slot.x * w_px)
        # 槽标题带：槽顶上方独立行（与页标题保持 ≥ 一行距，不与页标题
        # 同带 —— 视觉验收：标题/画幅标题重叠是缺陷）。
        title_y = max(int(slot.y * h_px) - int(0.022 * h_px), 14)
        map_y = int(slot.y * h_px)
        clip_id = f"fpclip{i}"
        title_esc = (
            str(slot.title or slot.frame_id)
            .replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        )
        vb_attr = f'viewBox="{slot_vb}"' if slot_vb else (
            f'viewBox="0 0 {slot_w} {slot_h}"')
        slot_parts.append(
            f'<text x="{x}" y="{title_y}" font-family="sans-serif" '
            f'font-size="{max(12, slot_h // 24)}" font-weight="600" '
            f'fill="#0f172a">{title_esc}</text>'
            f'<g clip-path="url(#{clip_id})" transform="translate({x},{map_y})">'
            f'<svg width="{slot_w}" height="{slot_h}" {vb_attr}>{inner}</svg></g>'
            f'<defs><clipPath id="{clip_id}"><rect x="{x}" y="{map_y}" '
            f'width="{slot_w}" height="{slot_h}"/></clipPath></defs>'
        )
        total_bytes += len(comp.svg.encode("utf-8"))
        for d in comp.diagnostics:
            if isinstance(d, dict) and d.get("code") in RENDER_DIAGNOSTICS:
                from app.lib.cartography.render_diagnostics import (
                    RenderDiagnostic,
                )

                frame_diagnostics.append(RenderDiagnostic(
                    code=d.get("code", ""), severity=d.get("severity", "info"),
                    message=d.get("message", ""), detail=d.get("detail", ""),
                    layer_id=d.get("layer_id"), component_id=d.get("component_id"),
                ))
        rendered_types.update(str(t) for t in (comp.rendered_component_types or []))

    # 页头标题（产品 grid.title）+ 共享图例带（页面底部）。
    product_raw = doc.get("frame_product")
    product = product_raw if isinstance(product_raw, dict) else {}
    page_title = str((product.get("grid") or {}).get("title") or "")
    header = ""
    if page_title:
        t_esc = (page_title.replace("&", "&amp;").replace("<", "&lt;")
                 .replace(">", "&gt;"))
        header = (
            f'<text x="{w_px // 2}" y="{int(margin_px * 0.52)}" '
            f'text-anchor="middle" font-family="sans-serif" '
            f'font-size="{max(16, h_px // 18)}" font-weight="700" '
            f'fill="#0f172a">{t_esc}</text>'
        )
    legend_band = ""
    has_shared_band = any(
        s.legend in (None, "shared", "shared_plus_frame")
        for s in (page.frame_slots or [])
    ) and isinstance(product.get("shared_expression"), dict) \
        and isinstance(product["shared_expression"].get("breaks"), list)
    if has_shared_band:
        band_y = int(h_px * 0.88)
        legend_band = (
            f'<g transform="translate({margin_px},{band_y})">'
            + _shared_legend_band_svg(
                doc, page, w_px - 2 * margin_px, shared_legend_spec)
            + "</g>"
        )

    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w_px}" height="{h_px}" '
        f'viewBox="0 0 {w_px} {h_px}">'
        f'<rect width="{w_px}" height="{h_px}" fill="#ffffff"/>'
        + header + "".join(slot_parts) + legend_band + "</svg>"
    )
    byte_count = len(svg.encode("utf-8"))
    if byte_count > min(svg_budget_remaining, _MAX_PAGE_SVG_BYTES):
        raise PageCompileFailure(
            "atlas_page_limit_truncated",
            f"product page svg budget at page {page.page_id}",
        )
    # ── PDF 面：HTML 组合（CSS 定位槽位 div —— WeasyPrint 内嵌 SVG 渲染
    #    器不支持 g transform/多嵌套 svg；HTML 是其原生排版模型）。svg
    #    字段（上文）服务单文件 SVG 导出与结构核验，两消费面同数据源。──
    def _esc_html(s: str) -> str:
        return (s.replace("&", "&amp;").replace("<", "&lt;")
                .replace(">", "&gt;"))

    html_parts: List[str] = [
        f'<div style="position:relative;width:{page_w}mm;height:{page_h}mm;'
        f'background:#ffffff;overflow:hidden">']
    if page_title:
        t_esc_h = _esc_html(page_title)
        html_parts.append(
            f'<div style="position:absolute;left:6%;right:6%;top:1.2%;'
            f'text-align:center;font-weight:700;'
            f'font-size:{max(7, page_h / 26):.1f}pt;color:#0f172a">{t_esc_h}</div>')

    slot_html: List[str] = []
    for i, slot in enumerate(page.frame_slots or []):
        left = slot.x * page_w
        top = slot.y * page_h
        sw = slot.w * page_w
        sh = slot.h * page_h
        title_y = max(slot.y * page_h - 0.022 * page_h, 0.008) * 1.0
        slot_html.append(
            f'<div style="position:absolute;left:{left:.2f}mm;'
            f'top:{title_y:.2f}mm;width:{sw:.2f}mm;font-weight:600;'
            f'font-size:{max(6, sh / 18):.1f}pt;color:#0f172a">'
            f'{_esc_html(str(slot.title or slot.frame_id))}</div>')
        # 槽位 svg：编译器原 viewBox（内容坐标系）+ 槽尺寸（preserve
        # aspect 默认 meet，与单帧页渲染同一几何口径）；div overflow
        # 即裁剪（clip 语义）。
        vb_attr = _slot_viewboxes[i] or f"0 0 {slot_w} {slot_h}"
        slot_html.append(
            f'<div style="position:absolute;left:{left:.2f}mm;'
            f'top:{top:.2f}mm;width:{sw:.2f}mm;height:{sh:.2f}mm;'
            f'overflow:hidden">'
            f'<svg xmlns="http://www.w3.org/2000/svg" '
            f'style="width:{sw:.2f}mm;height:{sh:.2f}mm" '
            f'viewBox="{vb_attr}">{_namespace_slot_svg(_slot_inner(_slot_svgs[i])[0], f"fp{i}-")}</svg>'
            f"</div>")
    html_parts.extend(slot_html)
    if has_shared_band:
        band_top = 0.88 * page_h
        html_parts.append(
            f'<div style="position:absolute;left:6%;right:6%;'
            f'top:{band_top:.2f}mm">'
            + _shared_legend_band_html(doc, page, shared_legend_spec)
            + "</div>")
    html_parts.append("</div>")
    html_body = "".join(html_parts)
    return PageCompileOutcome(
        svg=svg, width_mm=page_w, height_mm=page_h, page=page,
        svg_bytes=byte_count, frame_diagnostics=frame_diagnostics,
        rendered_component_types=sorted(rendered_types),
        html_body=html_body,
    )


def _shared_legend_band_html(
    doc: Dict[str, Any],
    page: PageLayoutIR,
    shared_legend_spec: Optional[Dict[str, Any]],
) -> str:
    """共享图例带（PDF/HTML 面）—— 与 SVG 面同数据源（resolver 盖章的
    共享图例，不读主文档局部分级 —— review#2：两面包同源）。"""
    from app.lib.cartography.render_scene import derive_legend_items

    product_raw = doc.get("frame_product")
    product = product_raw if isinstance(product_raw, dict) else {}
    shared = product.get("shared_expression")
    if not isinstance(shared_legend_spec, dict):
        return ""

    def _esc(s: str) -> str:
        return (s.replace("&", "&amp;").replace("<", "&lt;")
                .replace(">", "&gt;"))

    header = ""
    field_label = ""
    if isinstance(shared, dict):
        field_label = str(shared.get("field") or "")
        unit = str(shared.get("unit") or "")
        header = (
            f'<div style="font-size:5.5pt;color:#475569;margin-bottom:1mm">'
            f'Shared classification &middot; {_esc(field_label)}'
            + (f'（{_esc(unit)}）' if unit else "")
            + "</div>")
    model = derive_legend_items(shared_legend_spec)
    entries = (model or {}).get("entries") or []
    swatches = []
    for e in entries[:12]:
        color = str(e.get("color") or "#cccccc")
        label = str(e.get("label") or "")
        swatches.append(
            f'<span style="display:inline-block;margin-right:6mm">'
            f'<span style="display:inline-block;width:6mm;height:3mm;'
            f'background:{_esc(color)};opacity:0.6;'
            f'border:0.2pt solid #94a3b8;'
            f'vertical-align:middle"></span>'
            f'<span style="font-size:5.5pt;color:#0f172a;margin-left:1mm">'
            f'{_esc(label)}</span></span>')
    return header + "".join(swatches)


def _render_toc_svg(ir: PublicationIR, cover: PageLayoutIR) -> str:
    """封面/目录页 SVG（确定性文本页；4px/mm 与地图页同密度）。

    矢量文本、无数据访问、无外部资源；条目 = 非封面页
    ``{page_number}. {title}``（有界 ≤ MAX_ATLAS_PAGES）。
    """
    w = cover.paper.width_px
    h = cover.paper.height_px
    title_text = ir.atlas_title or cover.title or "Atlas"

    def _esc(s: str) -> str:
        return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                .replace('"', "&quot;"))

    lines = [
        f'<text x="{w / 2:.1f}" y="{h * 0.18:.1f}" text-anchor="middle" '
        f'font-family="sans-serif" font-size="36" font-weight="bold" '
        f'fill="#0f172a">{_esc(title_text)}</text>',
    ]
    entries = [p for p in ir.pages if not p.cover][:MAX_ATLAS_PAGES]
    if entries:
        lines.append(
            f'<text x="{w * 0.12:.1f}" y="{h * 0.30:.1f}" font-family="sans-serif" '
            f'font-size="15" fill="#475569">Contents</text>')
        row_h = min(34.0, (h * 0.55) / max(len(entries), 1))
        y0 = h * 0.34
        for i, p in enumerate(entries):
            label = f"{p.page_number}. {p.title or p.page_id}"
            lines.append(
                f'<text x="{w * 0.14:.1f}" y="{y0 + i * row_h:.1f}" '
                f'font-family="sans-serif" font-size="13" fill="#0f172a">'
                f'{_esc(label)}</text>')
    footer = f"{len(entries)} pages · PublicationIR {ir.ir_version}"
    lines.append(
        f'<text x="{w / 2:.1f}" y="{h * 0.95:.1f}" text-anchor="middle" '
        f'font-family="sans-serif" font-size="11" fill="#94a3b8">{_esc(footer)}</text>')
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" '
        f'viewBox="0 0 {w} {h}"><rect width="{w}" height="{h}" fill="#ffffff"/>'
        + "".join(lines) + "</svg>"
    )


def _atlas_page_entry(page: PageLayoutIR) -> Dict[str, Any]:
    """atlas 页摘要（回执面；有界、无载荷）。"""
    return {
        "page_id": page.page_id[:64],
        "title": page.title[:96],
        "page_number": page.page_number,
        "cover": page.cover,
        "bounds": [round(v, 6) for v in page.bounds] if page.bounds else None,
    }


def _page_filtered_doc(doc: Dict[str, Any], page: PageLayoutIR) -> Dict[str, Any]:
    """atlas 过滤页文档：按 IR 描述符过滤内联要素（触达路径浅拷贝，有界）。

    category：``properties[filter_property] == filter_value``；
    feature：``features[feature_offset : feature_offset+feature_limit]``。
    只拷贝触达壳（doc/sources/目标 source/payload 四层浅拷贝 + 新 features
    列表），不做全文档 deepcopy —— 重载荷不按页数放大。描述符悬空 → 诚实
    按整幅渲染（不虚构过滤语义）。
    """
    sources = doc.get("sources") if isinstance(doc.get("sources"), dict) else None
    layers = doc.get("layers")
    target_sid = None
    if isinstance(layers, list):
        for layer in layers:
            if isinstance(layer, dict) and layer.get("id") == page.filter_layer_id:
                target_sid = layer.get("source")
                break
    if sources is None or not isinstance(target_sid, str) or target_sid not in sources:
        return doc
    src = sources[target_sid]
    if not isinstance(src, dict):
        return doc
    payload = src.get("inlineData")
    payload_key = "inlineData"
    if not isinstance(payload, dict):
        payload = src.get("data") if isinstance(src.get("data"), dict) else None
        payload_key = "data"
    if payload is None:
        return doc
    feats = payload.get("features")
    if not isinstance(feats, list):
        return doc
    new_payload = dict(payload)
    if page.feature_offset is not None:
        lo = page.feature_offset
        hi = lo + page.feature_limit if page.feature_limit is not None else None
        new_payload["features"] = feats[lo:hi]
    elif page.filter_property:
        kept = []
        for f in feats:
            if not isinstance(f, dict):
                continue
            props_raw = f.get("properties")
            props = props_raw if isinstance(props_raw, dict) else {}
            val = props.get(page.filter_property)
            # 与 planner 分组判据对齐（review P3）：bool/容器值不入任何类别页
            if val is None or isinstance(val, (bool, dict, list)):
                continue
            if str(val)[:160] == page.filter_value:
                kept.append(f)
        new_payload["features"] = kept
    else:
        return doc
    new_src = dict(src)
    new_src[payload_key] = new_payload
    new_sources = dict(sources)
    new_sources[target_sid] = new_src
    out = dict(doc)
    out["sources"] = new_sources
    return out


def _structural_fingerprint(doc: Dict[str, Any]) -> str:
    """导出载荷的结构指纹（有界投影；不做全 payload 序列化）。

    覆盖 version/sources 描述符（type + content_revision + descriptor
    fingerprint + 特征计数）/layers/layout/thresholds；内联要素**内容**不入
    指纹（数据内容身份由 content_revision / descriptor_fingerprint 承载，
    会话级 revision 由血缘 record 的 mapspec_revision 承载）。
    """
    sources_raw = doc.get("sources")
    sources = sources_raw if isinstance(sources_raw, dict) else {}
    proj_sources: Dict[str, Any] = {}
    for key, src in sources.items():
        if not isinstance(src, dict):
            continue
        feats = None
        payload = src.get("inlineData")
        if isinstance(payload, dict) and isinstance(payload.get("features"), list):
            feats = payload["features"]
        proj_sources[str(key)[:64]] = {
            "type": str(src.get("type") or "")[:24],
            "content_revision": src.get("content_revision"),
            "descriptor_fingerprint": src.get("descriptor_fingerprint"),
            "feature_count": len(feats) if isinstance(feats, list) else None,
        }
    projection = {
        "version": doc.get("version"),
        "sources": proj_sources,
        "layers": doc.get("layers"),
        "layout": doc.get("layout"),
        "thresholds": doc.get("thresholds"),
        # C14 review P2：渲染相关顶层键入投影（view/scenario_mode/scene 影响
        # 整幅编译输出 —— 缺席会使指纹对这两类 spec 形态失真）。
        "view": doc.get("view"),
        "scenario_mode": doc.get("scenario_mode"),
        "scene": doc.get("scene"),
    }
    return "pubspec-sha256:" + digest_of(projection)[:40]


def _svg_to_page_html(svg: str, page_w_mm: float, page_h_mm: float, title: str = "") -> str:
    """单帧页文档。R1-B1 修复：SVG **原样嵌入**（此前误加 html.escape，
    SVG 源码被当正文文本排版 —— 矢量地图根本没有进入 PDF）。"""
    body = svg
    title_tag = f"<title>{_html.escape(title)}</title>" if title else ""
    # F14：CJK 内嵌字体置前（@font-face 命中时 CSS 栈里的回退项不再触发）
    font_face = _cjk_font_face_css()
    font_stack = f"'Noto Sans SC Embedded', {CSS_FONT_STACK}" if font_face else CSS_FONT_STACK
    # review P2-4：svg text 覆盖规则只在内嵌字体时输出 —— 否则会无声改变
    # 所有 publication PDF 的文本字体解析面（presentation attribute 让位）。
    svg_text_rule = f"svg, svg text {{ font-family: {font_stack}; }}\n" if font_face else ""
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">{title_tag}
<style>
{font_face}
@page {{ size: {page_w_mm}mm {page_h_mm}mm; margin: 0; }}
html, body {{ margin: 0; padding: 0; font-family: {font_stack}; }}
{svg_text_rule}</style></head>
<body>{body}</body></html>"""


def render_pdf_exclusive(render_fn: Callable[[], _RenderResult]) -> _RenderResult:
    """R1-M6：WeasyPrint write_pdf 进程级互斥的公共入口。

    官方不保证线程安全（fontconfig/pango 全局态）；本仓库**所有**
    write_pdf 调用（publication 端点 + report_service）必须经此串行。
    非阻塞抢锁 → 忙则 PublicationBusyError（调用方映射 429），饱和不排队。
    """
    if not _WEASYPRINT_LOCK.acquire(blocking=False):
        raise PublicationBusyError("vector pdf renderer is busy; retry later")
    try:
        return render_fn()
    finally:
        _WEASYPRINT_LOCK.release()



async def hydrate_ref_sources(payload: Dict[str, Any], session_id: str) -> Dict[str, Any]:
    """R1-M5：内联 ``ref:`` 载体矢量源（会话数据面），使导出渲染与 live
    同一份数据。**async 本体**（R2-A：async 路由直接 await，不再经 run_sync
    桥）；best-effort per source（水合失败的 ref 保持原样，由 unhydrated
    检测诚实拒绝）。"""
    import copy as _copy

    sources = payload.get("sources")
    if not isinstance(sources, dict):
        return payload
    spec = _copy.deepcopy(payload)
    out_sources = spec.get("sources", {})
    # #1220（audit3 C-5）：必须收集 dict 引用本身 —— 此前误存 id(src)（int），
    # 消费端 `src["inlineData"] = ...` 对 int 赋值直接 TypeError（接线即炸）。
    refs: List[Tuple[Dict[str, Any], str]] = []
    for src in out_sources.values():
        if not isinstance(src, dict):
            continue
        if isinstance(src.get("inlineData"), dict) or isinstance(src.get("data"), dict):
            continue
        ref = src.get("ref") or src.get("ref_id")
        if isinstance(ref, str) and ref.startswith("ref:"):
            refs.append((src, ref))
    if not refs:
        return spec

    from app.services.session_data import session_data_manager

    for src, ref in refs:
        try:
            payload_data = await session_data_manager.get(session_id, ref)
        except Exception as ex:  # noqa: BLE001 - per-source best-effort
            logger.warning("publication pdf: ref %s hydration failed: %s", ref, ex)
            payload_data = None
        if isinstance(payload_data, dict):
            src["inlineData"] = payload_data
    return spec


def hydrate_ref_sources_sync(payload: Dict[str, Any], session_id: str) -> Dict[str, Any]:
    """同步包装（worker 线程无运行 loop 时经 run_sync 持久 loop）。"""
    from app.core.async_runner import run_sync

    return run_sync(hydrate_ref_sources(payload, session_id))


def _unhydrated_geojson_keys(doc: Dict[str, Any]) -> List[str]:
    """无 inlineData/data 的 geojson/vector 源键（将静默渲染为空 → 拒绝）。"""
    sources = doc.get("sources")
    if not isinstance(sources, dict):
        return []
    out: List[str] = []
    for key, src in sources.items():
        if not isinstance(src, dict):
            continue
        stype = src.get("type")
        if stype not in ("geojson", "vector"):
            continue
        if isinstance(src.get("inlineData"), dict) or isinstance(src.get("data"), dict):
            continue
        out.append(str(key))
    return out


def validate_publication_payload(payload: Dict[str, Any]):
    """出版链载荷公共校验入口（C15 批量图册 executor 复用）。

    权威 schema 校验 + ref 载体源内联检查 —— 与
    ``render_publication_pdf`` 前置段同一事实源；失败沿
    ``MapSpecSchemaError`` typed 上抛（路由/executor 映射 400/页级证据）。
    """
    parsed = require_parseable_mapspec(payload)
    empty = _unhydrated_geojson_keys(parsed.document)
    if empty:
        raise MapSpecSchemaError(
            "mapspec_ref_sources_unhydrated",
            "geojson/vector sources carry no inline data: "
            + ",".join(empty)
            + " — provide sessionId for hydration or inline the data",
        )
    return parsed


#: DPI 合法域（印刷 72 线屏下限到喷墨 600 上限；越界钳制并披露生效值）。
DPI_MIN, DPI_MAX, DPI_DEFAULT = 72, 600, 300


def clamp_target_dpi(value: Any) -> int:
    """渲染 DPI 钳制（None/非法 → 缺省 300；越界钳到 [72, 600]）。"""
    try:
        dpi = int(value)
    except (TypeError, ValueError):
        return DPI_DEFAULT
    return max(DPI_MIN, min(DPI_MAX, dpi))


def render_publication_pdf(
    payload: Dict[str, Any], *, title: str = "", max_frames: int = MAX_SPEC_FRAMES,
    max_labels: int = MAX_LABELS_PER_EXPORT, target_dpi: int = 300,
    atlas: Optional[AtlasPolicy] = None,
    page_profile: Optional[str] = None,
) -> PublicationPdfResult:
    """Render a publication and observe successful and failed export duration.

    ``page_profile`` is optional. Absent means the constraint solver is not
    evaluated and the compiled document is the caller's document.
    """
    import time
    from app.core.sre_metrics import observe_export_duration

    started = time.monotonic()
    try:
        return _render_publication_pdf(payload, title=title, max_frames=max_frames,
            max_labels=max_labels, target_dpi=target_dpi, atlas=atlas,
            page_profile=page_profile)
    finally:
        observe_export_duration(time.monotonic() - started)


def _render_publication_pdf(
    payload: Dict[str, Any],
    *,
    title: str = "",
    max_frames: int = MAX_SPEC_FRAMES,
    max_labels: int = MAX_LABELS_PER_EXPORT,
    target_dpi: int = 300,
    atlas: Optional[AtlasPolicy] = None,
    page_profile: Optional[str] = None,
) -> PublicationPdfResult:
    """publication PDF 主入口（同步；CPU/IO 由调用方置于工作线程）。

    ``title``：PDF 文档元数据标题（<title>）；图面标题仍由 spec 标题组件
    驱动（user-wins，不虚构）。``max_labels``：每帧标签预算上界（≤400）。
    ``target_dpi``：渲染 DPI（V7 Goal 08 Phase H 可选；72-600 之外的值
    钳到边界，生效值经 result.target_dpi 如实披露 —— 缺省 300 与既有
    输出 byte 一致）。``atlas``：C14 PublicationIR 分页策略（frames/
    category/feature 驱动；页面版面单一模型 —— 见 publication_ir）。
    """
    target_dpi = clamp_target_dpi(target_dpi)
    if weasyprint is None:
        raise PublicationUnavailableError(
            "weasyprint is not installed; vector PDF unavailable"
        )
    parsed = require_parseable_mapspec(payload)
    doc = parsed.document
    from app.lib.cartography.publication_constraint_layout import (
        prepare_publication_document,
    )

    doc, layout_report = prepare_publication_document(doc, page_profile=page_profile)

    # R1-M5 / R2-M3+M8：水合**不在本服务发生**（工作线程触碰会话数据面
    # 非线程安全 + sessionId 属主校验面收窄）—— 调用方（路由/报告链）负责
    # 内联 ref 载体源；仍无法物化的 geojson/vector 源 → typed 拒绝
    # （不渲染只剩 chrome 的空白出版页）。
    _empty_sources = _unhydrated_geojson_keys(doc)
    if _empty_sources:
        raise MapSpecSchemaError(
            "mapspec_ref_sources_unhydrated",
            "geojson/vector sources carry no inline data: "
            + ",".join(_empty_sources)
            + " — provide sessionId for hydration or inline the data",
        )

    # 栅格/瓦片源披露（deny-all fetcher → 省略；R1-M9：detail 指名源键）
    sink = DiagnosticSink()
    _sources = doc.get("sources")
    _src_keys: List[str] = []
    for key, src in (_sources.items() if isinstance(_sources, dict) else []):
        stype = src.get("type") if isinstance(src, dict) else None
        if stype in ("raster", "data_fabric", "wms", "wmts", "pmtiles"):
            _src_keys.append(str(key))
    for key in _src_keys:
        sink.add(diagnostic("raster_layer_unavailable_vector_pdf", detail=key))
    if not _probe_cjk_font():
        sink.add(diagnostic("pdf_font_fallback"))

    # C14：页面规划统一走 PublicationIR（单一版面模型）。无 atlas 策略时
    # frames 驱动与既有帧循环 1:1 同序同帽（输出字节不变）；atlas 策略在场
    # 时由 category/feature/frames 驱动多页（页数诚实封顶 + 降级披露）。
    _atlas_effective = atlas if (
        isinstance(atlas, AtlasPolicy)
        and (atlas.driver != "frames" or atlas.include_cover or atlas.atlas_title)
    ) else None
    try:
        pub_ir = plan_publication_pages(doc, atlas=_atlas_effective,
                                        frames_cap=max_frames)
    except MapSpecSchemaError:
        raise
    for deg in pub_ir.degradations:
        sink.add(diagnostic(str(deg.get("code") or "atlas_truncated"),
                            detail=str(deg.get("detail") or "")[:200]))
    for warning in publication_preflight(pub_ir, doc):
        sink.add(diagnostic(
            "publication_preflight",
            detail=f"{warning.get('code')}: {warning.get('detail')}"[:200],
        ))

    # ── G27（P5）：出版等价视窗 —— 仅当 spec 显式声明 layout.scale_viewport
    # 时解析（旧项目零变化）。实际 frame extent / CRS / 逻辑 viewport /
    # target_dpi 经 plan_scale_contexts 传入；DPI 只影响采样与物理尺寸，
    # 不参与任何可见层/表达裁决（A15/A16/A21）。解释面不阻断导出。
    _g27_layout = doc.get("layout") if isinstance(doc.get("layout"), dict) else {}
    _g27_viewport = _g27_layout.get("scale_viewport")
    if isinstance(_g27_viewport, dict):
        try:
            from app.lib.cartography.publication_ir import plan_scale_contexts

            _g27_contexts = plan_scale_contexts(
                pub_ir, doc,
                crs=str(_g27_viewport.get("crs") or "EPSG:3857")[:32],
                target_dpi=float(target_dpi),
                logical_viewport_px=_g27_viewport.get("logical_viewport_px"),
            )
            sink.add(diagnostic(
                "g27_scale_context",
                detail=";".join(
                    f"{c.page_id}:{c.scale_denominator}" for c in _g27_contexts
                )[:200],
            ))
        except Exception as g27_exc:  # noqa: BLE001 - 解释面不阻断导出
            logger.warning("publication pdf: scale context skipped: %s", g27_exc)

    # 帧查找表 = enabled+封顶后的同一序列（page.frame_index 的对齐口径；
    # category/feature 驱动不使用帧下标）。
    if _atlas_effective is not None and _atlas_effective.driver != "frames":
        frames_lookup: List[Any] = []
    else:
        frames_lookup, _lookup_trunc = enabled_frames(
            doc, cap=_atlas_effective.page_budget if _atlas_effective else max_frames)

    page_specs: List[Tuple[str, str, float, float]] = []  # (page_name, svg, w_mm, h_mm)
    rendered = 0
    skipped = 0
    _accumulated = 0
    coverage_rendered: set = set()
    coverage_omitted: List[Dict[str, Any]] = []
    _omitted_seen: set = set()
    atlas_pages_summary: List[Dict[str, Any]] = []

    for page in pub_ir.pages:
        # C15：逐页编译统一走接缝（compile_publication_page）—— 同步链与
        # atlas batch executor 同一事实源；预算数学（含已累积封面页）与
        # 诊断串逐字保持。
        try:
            outcome = compile_publication_page(
                doc, page, pub_ir,
                frames_lookup=frames_lookup,
                target_dpi=target_dpi,
                max_labels=max_labels,
                svg_budget_remaining=_MAX_TOTAL_SVG_BYTES - _accumulated,
            )
        except PageCompileFailure as pcf:  # 单帧失败不中断 atlas（frame skip 政策）
            logger.warning("publication pdf: page %s compile failed: %s",
                           page.page_id, pcf.detail)
            skipped += 1
            sink.add(diagnostic(pcf.code, detail=pcf.detail))
            continue
        _accumulated += len(outcome.svg.encode("utf-8"))
        page_specs.append((
            f"p{page.page_number - 1}",
            # G15：产品页优先 HTML 体（WeasyPrint SVG 内嵌限制，见
            # PageCompileOutcome.html_body docstring）；既有页 = svg。
            outcome.html_body or outcome.svg,
            outcome.width_mm, outcome.height_mm,
        ))
        if page.cover:
            rendered += 1
            atlas_pages_summary.append(_atlas_page_entry(page))
            continue
        if pub_ir.atlas:
            atlas_pages_summary.append(_atlas_page_entry(page))
        # R1-M3：帧级诊断经 extend_frame（子配额 + 溢出显式元披露）
        sink.extend_frame(outcome.frame_diagnostics)
        rendered += 1
        # F14 D3：帧覆盖聚合（rendered 并集；omitted 按组件去重保序，有界）
        for _t in outcome.rendered_component_types:
            coverage_rendered.add(str(_t))
        for _o in outcome.omitted_components:
            key = (str(_o.get("component_id") or ""), str(_o.get("type") or ""),
                   str(_o.get("code") or ""))
            if key in _omitted_seen:
                continue
            _omitted_seen.add(key)
            if len(coverage_omitted) < 16:
                coverage_omitted.append({
                    "component_id": key[0][:64],
                    "type": key[1][:32],
                    "code": key[2][:48],
                })

    if not page_specs:
        raise MapSpecSchemaError("publication_no_pages", "no frames compiled to pages")

    # C15：组装统一走接缝（assemble_pages_pdf：命名 @page + WeasyPrint 互斥
    # + CJK 内嵌诊断），与 atlas batch executor 同一事实源；输出字节不变。
    pdf_bytes = assemble_pages_pdf(page_specs, title=title, sink=sink)

    page_count = count_pdf_pages(pdf_bytes, fallback=rendered)

    return PublicationPdfResult(
        pdf=pdf_bytes,
        page_count=page_count,
        diagnostics=sink.to_payload(),
        disclosures=parsed.disclosures_payload(),
        font_cjk=_probe_cjk_font(),
        frames_rendered=rendered,
        frames_skipped=skipped,
        target_dpi=target_dpi,
        component_coverage={
            "rendered": sorted(coverage_rendered)[:24],
            "omitted": coverage_omitted,
        },
        layout_version=PUBLICATION_IR_VERSION,
        spec_fingerprint=_structural_fingerprint(doc),
        atlas=bool(pub_ir.atlas),
        atlas_pages=atlas_pages_summary,
        constraint_layout=layout_report,
    )


def _deny_url_fetcher(url: str, *args: Any, **kwargs: Any) -> Any:  # pragma: no cover - 防御断言
    if url.startswith("data:font/ttf;base64,"):
        import base64

        payload = url.partition(",")[2]
        if len(payload) > 8 * 1024 * 1024:
            raise ValueError("embedded publication font exceeds byte budget")
        return {"string": base64.b64decode(payload, validate=True), "mime_type": "font/ttf"}
    raise ValueError(f"external resource fetch denied in publication pdf: {url!r}")
