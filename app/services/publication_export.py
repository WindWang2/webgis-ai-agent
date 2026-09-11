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

from app.lib.cartography.label_collision import MAX_LABELS_PER_EXPORT
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
from app.services.mapspec_to_svg import (
    compile_mapspec_to_svg_detailed,
    resolve_spec_timeout_ms,
)

logger = logging.getLogger(__name__)

try:  # 与 report_service 同模式：可选依赖，缺席诚实降级
    import weasyprint  # noqa: F401
except ImportError:  # pragma: no cover - 环境相关
    weasyprint = None

#: 页面尺寸上限（A0 = 841×1189mm 的毫米值以内）；超限帧拒绝。
MAX_PAGE_MM = 1200.0
#: 多帧累积 SVG 字节预算（R2-M5：解析 DOM 前的内存上界锚点）。
_MAX_TOTAL_SVG_BYTES = 96 * 1024 * 1024
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
    # V7（Goal 08 Phase H）：生效 DPI（用户值被钳制后如实披露 —— 不静默）。
    target_dpi: int = 300


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


def _effective_max_features(frame_doc: Dict[str, Any]) -> int:
    """spec.thresholds.maxFeatures（合法时）否则 50000 —— 调用方再做绝对封顶。"""
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
    return 50000


def _frame_geometry(frame: Optional[Dict[str, Any]]) -> Tuple[float, float, Optional[List[float]]]:
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
                # R2-M2：zoom 夹取（-1075 下溢除零 / 巨幅 zoom 病态范围）
                zoom = max(-2.0, min(zoom, 22.0))
                if isinstance(center, list) and len(center) >= 2:
                    lng, lat = float(center[0]), float(center[1])
                    span = 360.0 / (2.0 ** zoom)
                    bounds = [lng - span / 2, max(min(lat - span / 4, 85.0), -85.0),
                              lng + span / 2, max(min(lat + span / 4, 85.0), -85.0)]
            except (TypeError, ValueError, ZeroDivisionError, OverflowError):
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


def _svg_to_page_html(svg: str, page_w_mm: float, page_h_mm: float, title: str = "") -> str:
    """单帧页文档。R1-B1 修复：SVG **原样嵌入**（此前误加 html.escape，
    SVG 源码被当正文文本排版 —— 矢量地图根本没有进入 PDF）。"""
    body = svg
    title_tag = f"<title>{_html.escape(title)}</title>" if title else ""
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">{title_tag}
<style>
@page {{ size: {page_w_mm}mm {page_h_mm}mm; margin: 0; }}
html, body {{ margin: 0; padding: 0; font-family: {CSS_FONT_STACK}; }}
svg {{ width: 100%; height: 100%; }}
</style></head>
<body>{body}</body></html>"""


def render_pdf_exclusive(render_fn):
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



def hydrate_ref_sources_sync(payload: Dict[str, Any], session_id: str) -> Dict[str, Any]:
    """R1-M5：内联 ``ref:`` 载体矢量源（会话数据面），使导出渲染与 live
    同一份数据。同步包装（工作线程内 asyncio.run）；best-effort per source
    （水合失败的 ref 保持原样，由 unhydrated 检测诚实拒绝）。"""
    import asyncio as _asyncio
    import copy as _copy

    sources = payload.get("sources")
    if not isinstance(sources, dict):
        return payload
    spec = _copy.deepcopy(payload)
    out_sources = spec.get("sources", {})
    refs: List[Tuple[str, str]] = []
    for src in out_sources.values():
        if not isinstance(src, dict):
            continue
        if isinstance(src.get("inlineData"), dict) or isinstance(src.get("data"), dict):
            continue
        ref = src.get("ref") or src.get("ref_id")
        if isinstance(ref, str) and ref.startswith("ref:"):
            refs.append((id(src), ref))
    if not refs:
        return spec

    async def _hydrate() -> None:
        from app.services.session_data import session_data_manager

        for src, ref in refs:
            try:
                payload_data = await session_data_manager.get(session_id, ref)
            except Exception as ex:  # noqa: BLE001 - per-source best-effort
                logger.warning("publication pdf: ref %s hydration failed: %s", ref, ex)
                payload_data = None
            if isinstance(payload_data, dict):
                src["inlineData"] = payload_data

    _asyncio.run(_hydrate())
    return spec


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
    payload: Dict[str, Any],
    *,
    title: str = "",
    max_frames: int = MAX_SPEC_FRAMES,
    max_labels: int = MAX_LABELS_PER_EXPORT,
    target_dpi: int = 300,
) -> PublicationPdfResult:
    """publication PDF 主入口（同步；CPU/IO 由调用方置于工作线程）。

    ``title``：PDF 文档元数据标题（<title>）；图面标题仍由 spec 标题组件
    驱动（user-wins，不虚构）。``max_labels``：每帧标签预算上界（≤400）。
    ``target_dpi``：渲染 DPI（V7 Goal 08 Phase H 可选；72-600 之外的值
    钳到边界，生效值经 result.target_dpi 如实披露 —— 缺省 300 与既有
    输出 byte 一致）。
    """
    target_dpi = clamp_target_dpi(target_dpi)
    if weasyprint is None:
        raise PublicationUnavailableError(
            "weasyprint is not installed; vector PDF unavailable"
        )
    parsed = require_parseable_mapspec(payload)
    doc = parsed.document

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

    layout = doc.get("layout") if isinstance(doc.get("layout"), dict) else {}
    frames_raw = layout.get("frames")
    frames: List[Optional[Dict[str, Any]]] = []
    if isinstance(frames_raw, list) and frames_raw:
        frames = [f for f in frames_raw if isinstance(f, dict) and f.get("enabled") is not False]
        if len(frames_raw) > max_frames:
            frames = frames[:max_frames]
    else:
        frames = [None]  # 单帧 = 整幅

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

    from weasyprint import HTML

    page_specs: List[Tuple[str, str, float, float]] = []  # (page_name, svg, w_mm, h_mm)
    rendered = 0
    skipped = 0
    for i, frame in enumerate(frames):
        page_w, page_h, bounds = _frame_geometry(frame)
        frame_doc = _apply_frame_overrides(doc, frame)
        try:
            comp = compile_mapspec_to_svg_detailed(
                frame_doc,
                target_dpi=target_dpi,
                # 栅格密度近似 4px/mm（固定，保输出 byte 一致）；target_dpi 驱动
                # 文本/线宽的 dpi_scale（mapspec_to_svg），不改画布像素尺寸
                width=int(page_w * 4),
                height=int(page_h * 4),
                padding=24,
                include_chrome=True,
                bounds=bounds,
                max_labels=max_labels,
                # R2-M5/M7：服务端绝对封顶（spec 可声明更小预算，不得放大包络）
                max_features=min(_effective_max_features(frame_doc), 50000),
                timeout_ms=min(resolve_spec_timeout_ms(frame_doc), 30000.0),
            )
        except Exception as ex:  # 单帧失败不中断 atlas（frame skip 政策）
            logger.warning("publication pdf: frame %s compile failed: %s", i, ex)
            skipped += 1
            sink.add(diagnostic("atlas_page_skipped", detail=f"frame {i}: {type(ex).__name__}"))
            continue
        # R2-M5：累积 SVG 预算 —— 超限停止加帧（诚实披露），防 OOM 放大
        _accumulated = sum(len(s) for _n, s, _w, _h in page_specs)
        if _accumulated + len(comp.svg) > _MAX_TOTAL_SVG_BYTES:
            skipped += 1
            sink.add(diagnostic(
                "atlas_page_limit_truncated",
                detail=f"svg budget {_MAX_TOTAL_SVG_BYTES} bytes at frame {i}",
            ))
            continue
        page_specs.append((f"p{i}", comp.svg, page_w, page_h))
        # R1-M3：帧级诊断经 extend_frame（子配额 + 溢出显式元披露）
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
        sink.extend_frame(frame_items)
        rendered += 1

    if not page_specs:
        raise MapSpecSchemaError("publication_no_pages", "no frames compiled to pages")

    if len(page_specs) > 1:
        # R1-M2 修复：named pages（每帧独立 @page size）—— 此前多帧被压成
        # size:auto，帧页面尺寸静默丢失。
        rules = [
            f"@page {name} {{ size: {w_mm}mm {h_mm}mm; margin: 0; }}\n"
            f".page-{name} {{ page: {name}; }}"
            for (name, _svg, w_mm, h_mm) in page_specs
        ]
        html_doc = (
            '<!DOCTYPE html><html><head><meta charset="utf-8"><style>'
            f"html, body {{ margin: 0; font-family: {CSS_FONT_STACK}; }}\n"
            "svg { width: 100%; height: 100%; }\n"
            ".page { page-break-after: always; }\n"
            + "\n".join(rules)
            + "</style></head><body>"
            + "".join(
                f'<div class="page page-{name}">{svg}</div>'
                for (name, svg, _w, _h) in page_specs
            )
            + "</body></html>"
        )
    else:
        _name, svg0, w0, h0 = page_specs[0]
        html_doc = _svg_to_page_html(svg0, w0, h0, title=title)

    # WeasyPrint 串行化（R1-M6）：非阻塞抢锁，忙则结构化拒绝
    try:
        pdf_bytes = render_pdf_exclusive(
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
        target_dpi=target_dpi,
    )


def _deny_url_fetcher(url: str, *args: Any, **kwargs: Any):  # pragma: no cover - 防御断言
    raise ValueError(f"external resource fetch denied in publication pdf: {url!r}")
