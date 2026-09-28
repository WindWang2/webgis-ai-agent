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

from app.lib.cartography.atlas_layout import MAX_ATLAS_PAGES
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
    MAX_PAGE_MM,
    PAGE_PROFILES,
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
        hi = lo + (page.feature_limit or 0)
        new_payload["features"] = feats[lo:hi]
    elif page.filter_property:
        kept = []
        for f in feats:
            if not isinstance(f, dict):
                continue
            props = f.get("properties") if isinstance(f.get("properties"), dict) else {}
            val = props.get(page.filter_property)
            if val is not None and str(val)[:160] == page.filter_value:
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
    sources = doc.get("sources") if isinstance(doc.get("sources"), dict) else {}
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
    同一份数据。同步包装（持久 loop 经 run_sync，连接池跨调用复用）；best-effort per source
    （水合失败的 ref 保持原样，由 unhydrated 检测诚实拒绝）。"""
    from app.core.async_runner import run_sync
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

    run_sync(_hydrate())
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
    atlas: Optional[AtlasPolicy] = None,
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

    layout = doc.get("layout") if isinstance(doc.get("layout"), dict) else {}
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
        sink.add(diagnostic("publication_preflight_" + str(warning.get("code") or "issue"),
                            detail=str(warning.get("detail") or "")[:200]))

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
    coverage_rendered: set = set()
    coverage_omitted: List[Dict[str, Any]] = []
    _omitted_seen: set = set()
    atlas_pages_summary: List[Dict[str, Any]] = []
    from app.lib.cartography.render_diagnostics import RenderDiagnostic

    for page in pub_ir.pages:
        page_w = page.paper.width_mm
        page_h = page.paper.height_mm
        if page.cover:
            # 封面/目录页：确定性文本页（无地图编译面；无数据访问）。
            page_specs.append((
                f"p{page.page_number - 1}", _render_toc_svg(pub_ir, page), page_w, page_h,
            ))
            rendered += 1
            atlas_pages_summary.append(_atlas_page_entry(page))
            continue
        # 页文档解析：帧引用 / 过滤页 / 单帧整幅
        if page.frame_index is not None:
            frame = frames_lookup[page.frame_index] if page.frame_index < len(frames_lookup) else None
            frame_doc = _apply_frame_overrides(doc, frame)
        elif page.filter_layer_id:
            frame_doc = _page_filtered_doc(doc, page)
        else:
            frame_doc = doc
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
                bounds=page.bounds,
                max_labels=max_labels,
                # R2-M5/M7：服务端绝对封顶（spec 可声明更小预算，不得放大包络）
                max_features=min(_effective_max_features(frame_doc), EXPORT_MAX_FEATURES),
                timeout_ms=min(resolve_spec_timeout_ms(frame_doc), 30000.0),
            )
        except Exception as ex:  # 单帧失败不中断 atlas（frame skip 政策）
            logger.warning("publication pdf: page %s compile failed: %s",
                           page.page_id, ex)
            skipped += 1
            sink.add(diagnostic("atlas_page_skipped",
                                detail=f"page {page.page_id}: {type(ex).__name__}"))
            continue
        # R2-M5：累积 SVG 预算 —— 超限停止加帧（诚实披露），防 OOM 放大
        _accumulated = sum(len(s) for _n, s, _w, _h in page_specs)
        if _accumulated + len(comp.svg) > _MAX_TOTAL_SVG_BYTES:
            skipped += 1
            sink.add(diagnostic(
                "atlas_page_limit_truncated",
                detail=f"svg budget {_MAX_TOTAL_SVG_BYTES} bytes at page {page.page_id}",
            ))
            continue
        page_specs.append((f"p{page.page_number - 1}", comp.svg, page_w, page_h))
        if pub_ir.atlas:
            atlas_pages_summary.append(_atlas_page_entry(page))
        # R1-M3：帧级诊断经 extend_frame（子配额 + 溢出显式元披露）
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
        # F14 D3：帧覆盖聚合（rendered 并集；omitted 按组件去重保序，有界）
        for _t in comp.rendered_component_types or []:
            coverage_rendered.add(str(_t))
        for _o in comp.omitted_components or []:
            if not isinstance(_o, dict):
                continue
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

    if len(page_specs) > 1:
        # R1-M2 修复：named pages（每帧独立 @page size）—— 此前多帧被压成
        # size:auto，帧页面尺寸静默丢失。
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
        if font_face:
            sink.add(diagnostic("pdf_cjk_font_embedded"))
    else:
        _name, svg0, w0, h0 = page_specs[0]
        html_doc = _svg_to_page_html(svg0, w0, h0, title=title)
        if _cjk_font_face_css():
            sink.add(diagnostic("pdf_cjk_font_embedded"))

    # WeasyPrint 串行化（R1-M6）：非阻塞抢锁，忙则结构化拒绝
    from weasyprint import HTML

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
        component_coverage={
            "rendered": sorted(coverage_rendered)[:24],
            "omitted": coverage_omitted,
        },
        layout_version=PUBLICATION_IR_VERSION,
        spec_fingerprint=_structural_fingerprint(doc),
        atlas=bool(pub_ir.atlas),
        atlas_pages=atlas_pages_summary,
    )


def _deny_url_fetcher(url: str, *args: Any, **kwargs: Any):  # pragma: no cover - 防御断言
    raise ValueError(f"external resource fetch denied in publication pdf: {url!r}")
