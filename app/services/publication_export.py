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

import html as _html
import io
import logging
import threading
from dataclasses import dataclass, field as _dc_field
from typing import Any, Dict, List, Optional, Tuple

from app.lib.cartography.mapspec_schema import (
    MAX_SPEC_FRAMES,
    MapSpecSchemaError,
    require_parseable_mapspec,
)
from app.lib.cartography.render_diagnostics import (
    DiagnosticSink,
    diagnostic,
)
from app.services.mapspec_to_svg import compile_mapspec_to_svg_detailed

logger = logging.getLogger(__name__)

try:  # 与 report_service 同模式：可选依赖，缺席诚实降级
    import weasyprint  # noqa: F401
except ImportError:  # pragma: no cover - 环境相关
    weasyprint = None

#: 页面尺寸上限（A0 = 841×1189mm 的毫米值以内）；超限帧拒绝。
MAX_PAGE_MM = 1200.0
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


@dataclass
class PublicationPdfResult:
    pdf: bytes
    page_count: int
    diagnostics: List[Dict[str, Any]] = _dc_field(default_factory=list)
    disclosures: List[Dict[str, str]] = _dc_field(default_factory=list)
    font_cjk: bool = True
    frames_rendered: int = 0
    frames_skipped: int = 0


def _probe_cjk_font() -> bool:
    """进程级缓存（固定键集 {"cjk"}，R1-N1）：系统是否存在 CJK 字体。"""
    with _FONT_CACHE_LOCK:
        if "cjk" in _FONT_CACHE:
            return _FONT_CACHE["cjk"]
    found = False
    try:  # matplotlib 已是 requirements 依赖；fontManager 扫描结果进程内稳定
        import matplotlib.font_manager as fm

        keywords = ("cjk", "noto sans cjk", "source han", "wqy", "simhei", "simsun", "yahei", "pingfang")
        found = any(
            any(kw in f.name.lower() for kw in keywords)
            for f in fm.fontManager.ttflist
        )
    except Exception:  # pragma: no cover - 字体子系统异常时保守返回 False
        found = False
    with _FONT_CACHE_LOCK:
        _FONT_CACHE["cjk"] = found
    return found


def _frame_geometry(frame: Optional[Dict[str, Any]], doc: Dict[str, Any]) -> Tuple[float, float, Optional[List[float]]]:
    """帧 → (页宽mm, 页高mm, bounds)。缺省 A4 landscape 297×210。"""
    page_w, page_h = 297.0, 210.0
    bounds: Optional[List[float]] = None
    if isinstance(frame, dict):
        size = frame.get("pageSize")
        if isinstance(size, dict):
            try:
                w = float(size.get("width"))
                h = float(size.get("height"))
                if 10.0 < w <= MAX_PAGE_MM and 10.0 < h <= MAX_PAGE_MM:
                    page_w, page_h = w, h
            except (TypeError, ValueError):
                pass
        extent = frame.get("extent")
        if isinstance(extent, list) and len(extent) == 4:
            try:
                vals = [float(v) for v in extent]
                if all(v == v for v in vals):
                    bounds = vals
            except (TypeError, ValueError):
                pass
        elif isinstance(frame.get("view"), dict):
            # view（center/zoom）的地面范围由 zoom 换算：zoom z 下 360°/2^z
            view = frame["view"]
            try:
                center = view.get("center")
                zoom = float(view.get("zoom", 10.0))
                if isinstance(center, list) and len(center) >= 2:
                    lng, lat = float(center[0]), float(center[1])
                    span = 360.0 / (2.0 ** zoom)
                    bounds = [lng - span / 2, max(min(lat - span / 4, 85.0), -85.0),
                              lng + span / 2, max(min(lat + span / 4, 85.0), -85.0)]
            except (TypeError, ValueError):
                bounds = None
    return (page_w, page_h, bounds)


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


def _svg_to_page_html(svg: str, page_w_mm: float, page_h_mm: float) -> str:
    body = _html.escape(svg, quote=False)  # 不转义引号不必要；svg 自身已转义
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<style>
@page {{ size: {page_w_mm}mm {page_h_mm}mm; margin: 0; }}
html, body {{ margin: 0; padding: 0; font-family: {CSS_FONT_STACK}; }}
svg {{ width: 100%; height: 100%; }}
</style></head>
<body>{body}</body></html>"""


def render_publication_pdf(
    payload: Dict[str, Any],
    *,
    title: str = "",
    max_frames: int = MAX_SPEC_FRAMES,
) -> PublicationPdfResult:
    """publication PDF 主入口（同步；CPU/IO 由调用方置于工作线程）。"""
    if weasyprint is None:
        raise PublicationUnavailableError(
            "weasyprint is not installed; vector PDF unavailable"
        )
    parsed = require_parseable_mapspec(payload)
    doc = parsed.document

    layout = doc.get("layout") if isinstance(doc.get("layout"), dict) else {}
    frames_raw = layout.get("frames")
    frames: List[Optional[Dict[str, Any]]] = []
    if isinstance(frames_raw, list) and frames_raw:
        frames = [f for f in frames_raw if isinstance(f, dict) and f.get("enabled") is not False]
        if len(frames_raw) > max_frames:
            frames = frames[:max_frames]
    else:
        frames = [None]  # 单帧 = 整幅

    # 栅格/瓦片源披露（deny-all fetcher → 省略；R1-M9）
    sink = DiagnosticSink()
    for src in (doc.get("sources") or {}).values():
        stype = src.get("type") if isinstance(src, dict) else None
        if stype in ("raster", "data_fabric", "wms", "wmts", "pmtiles"):
            sink.add(diagnostic("raster_layer_unavailable_vector_pdf", detail=stype))
    if not _probe_cjk_font():
        sink.add(diagnostic("pdf_font_fallback"))

    from weasyprint import HTML

    page_htmls: List[str] = []
    rendered = 0
    skipped = 0
    for i, frame in enumerate(frames):
        page_w, page_h, bounds = _frame_geometry(frame, doc)
        frame_doc = _apply_frame_overrides(doc, frame)
        try:
            comp = compile_mapspec_to_svg_detailed(
                frame_doc,
                target_dpi=300,
                width=int(page_w * 4),   # mm→px 近似 4px/mm（≥300dpi 视觉）
                height=int(page_h * 4),
                padding=24,
                include_chrome=True,
                bounds=bounds,
            )
        except Exception as ex:  # 单帧失败不中断 atlas（frame skip 政策）
            logger.warning("publication pdf: frame %s compile failed: %s", i, ex)
            skipped += 1
            sink.add(diagnostic("atlas_page_skipped", detail=f"frame {i}: {type(ex).__name__}"))
            continue
        page_htmls.append(_svg_to_page_html(comp.svg, page_w, page_h))
        for d in comp.diagnostics[:8]:
            from app.lib.cartography.render_diagnostics import RenderDiagnostic

            sink.add(RenderDiagnostic(
                code=d.get("code", ""), severity=d.get("severity", "info"),
                message=d.get("message", ""), detail=d.get("detail", ""),
                layer_id=d.get("layer_id"), component_id=d.get("component_id"),
            ))
        rendered += 1

    if not page_htmls:
        raise MapSpecSchemaError("publication_no_pages", "no frames compiled to pages")

    if rendered > 1:
        html_doc = (
            '<!DOCTYPE html><html><head><meta charset="utf-8"><style>'
            f"@page {{ size: auto; margin: 0; }} html, body {{ margin: 0; font-family: {CSS_FONT_STACK}; }}"
            ".page { page-break-after: always; }"
            "</style></head><body>"
            + "".join(f'<div class="page">{h.split("<body>", 1)[1].split("</body>", 1)[0]}</div>' for h in page_htmls)
            + "</body></html>"
        )
    else:
        html_doc = page_htmls[0]

    # WeasyPrint 串行化：非阻塞抢锁，忙则结构化拒绝（不排队不阻塞事件循环）
    if not _WEASYPRINT_LOCK.acquire(blocking=False):
        raise PublicationBusyError("vector pdf renderer is busy; retry later")
    try:
        pdf_bytes = HTML(
            string=html_doc,
            url_fetcher=_deny_url_fetcher,  # SSRF/外联全拒（R1-M9）
            base_url=None,
        ).write_pdf()
    finally:
        _WEASYPRINT_LOCK.release()

    try:
        import pypdf

        page_count = len(pypdf.PdfReader(io.BytesIO(pdf_bytes)).pages)
    except Exception:
        page_count = rendered

    return PublicationPdfResult(
        pdf=pdf_bytes,
        page_count=page_count,
        diagnostics=sink.to_payload(),
        disclosures=parsed.disclosures_payload(),
        font_cjk=_probe_cjk_font(),
        frames_rendered=rendered,
        frames_skipped=skipped,
    )


def _deny_url_fetcher(url: str, *args: Any, **kwargs: Any):  # pragma: no cover - 防御断言
    raise ValueError(f"external resource fetch denied in publication pdf: {url!r}")
