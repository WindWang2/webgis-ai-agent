"""PublicationIR —— 出版页面级版面中间表示（C14，单一 Live/Export 版面模型）。

职责（ADR-0165 §4 多图版面 + ADR-0160 C2 IR v2 之上的**页面层**）：

- 把 MapSpec（单帧或多帧）+ 可选 atlas 策略确定性编译成 ``PublicationIR``：
  逐页纸张几何（mm）、范围（WSEN）、帧引用/特征过滤描述符、C2 组件 IR、
  页码/封面。**导出渲染（PNG/PDF/SVG）与页面规划消费同一份 IR** ——
  页面级版面不再存在第二套参数体系。
- 复用冻结契约，不重新发明几何：纸张 profile/帧几何 ``page_geometry``
  逐字提取自 publication_export（字节不变）；组件 IR 一律经
  ``atlas_layout.plan_atlas_pages``（C2 IR v2，``build_layout_ir``）装配。
- refs-only / 有界：IR 不携带要素载荷 —— 页面只引用帧下标或过滤描述符，
  重载荷留在原始文档单份持有；指纹 = canonical JSON sha256（plan_ir 同口径）。
- 诚实失败：非法策略/缺属性 → typed 拒绝（``MapSpecSchemaError``，路由映射
  400）；页数超 ``MAX_ATLAS_PAGES`` 截断并记 ``atlas_truncated``；要素扫描
  超 ``MAX_ATLAS_SCAN_FEATURES`` 停止并记 ``atlas_feature_scan_truncated``。
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Literal, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field

from app.lib.cartography.atlas_layout import MAX_ATLAS_PAGES, plan_atlas_pages
from app.lib.cartography.render_scene import MAX_LEGEND_ITEMS_PER_BOX
from app.lib.cartography.layout_description import (
    build_layout_ir,
    export_bounds_for_frame,
)
from app.lib.cartography.mapspec_schema import MAX_SPEC_FRAMES, MapSpecSchemaError
from app.lib.cartography.plan_ir import digest_of

#: IR schema 版本（语义变更 = bump + 迁移说明；消费方按版本分派）。
PUBLICATION_IR_VERSION = "1.0.0"

#: 页面像素换算密度（px/mm）—— 与 publication_export 编译面同约定
#: （``width=int(page_w * 4)``）；改此值 = 全部导出几何变更。
PX_PER_MM = 4

#: 纸型 profile 预设（mm）。单一真相在本模块；publication_export 以别名
#: 再导出（历史导入面不变，数值与 mapspec_schema.PageProfile 词表对账锁定）。
PAGE_PROFILES: Dict[str, Tuple[float, float]] = {
    "a4_portrait": (210.0, 297.0),
    "a4_landscape": (297.0, 210.0),
    "a3_portrait": (297.0, 420.0),
    "a3_landscape": (420.0, 297.0),
    "a2_landscape": (594.0, 420.0),
    "a1_landscape": (841.0, 594.0),
    "a0_landscape": (1189.0, 841.0),
}

#: 页面尺寸上限（mm；与 publication_export.MAX_PAGE_MM 同值）。
MAX_PAGE_MM = 1200.0

#: 类别/特征扫描的要素数上界（一次性单遍扫描；超过即停 + 诚实披露，
#: 绝不为分页无限读原始表格）。
MAX_ATLAS_SCAN_FEATURES = 5000

#: 封面/目录页 id（保留字；场景 id 与其冲突时由 plan 前缀消歧）。
COVER_PAGE_ID = "cover"

#: 页标题长度软上界（超过 → preflight 记 title_wrap_expected）。
_TITLE_WRAP_CHARS = 40


class _Bounded(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


# ── atlas 策略（构造期封闭校验）──────────────────────────────────────────


class AtlasPolicy(_Bounded):
    """atlas 分页策略（drivers 封闭词表；字段全部有界）。

    - ``frames``：spec 显式多帧即页（用户定义页序，封顶 ``page_budget``）。
    - ``category``：按 ``category_property`` 的去重值分页（每页一值，
      页范围 = 该值要素 bbox 拟合纸张纵横比；页文档 = 值过滤后的要素）。
    - ``feature``：按 ``features_per_page`` 分块分页（每页一块 bbox）。
    """

    driver: Literal["frames", "category", "feature"] = "frames"
    layer_id: str = Field(default="", max_length=120)
    """类别/特征驱动的目标 layer id；空 = 自动定位首个内联 geojson 图层。"""
    category_property: str = Field(default="", max_length=120)
    """category 驱动必填：要素属性名（去重值 = 页）。"""
    features_per_page: int = Field(default=50, ge=1, le=500)
    """feature 驱动：每页要素块大小。"""
    page_budget: int = Field(default=MAX_ATLAS_PAGES, ge=1, le=MAX_ATLAS_PAGES)
    include_cover: bool = False
    """封面/目录页（确定性文本页，前置）。"""
    atlas_title: str = Field(default="", max_length=160)


class PagePaper(_Bounded):
    """单页纸张几何（mm；profile 词表 = PAGE_PROFILES 键或裸尺寸时为空）。"""

    width_mm: float = Field(gt=0, le=MAX_PAGE_MM)
    height_mm: float = Field(gt=0, le=MAX_PAGE_MM)
    profile: str = Field(default="", max_length=32)
    orientation: Literal["landscape", "portrait"] = "landscape"

    @property
    def width_px(self) -> int:
        return int(self.width_mm * PX_PER_MM)

    @property
    def height_px(self) -> int:
        return int(self.height_mm * PX_PER_MM)


class PageLayoutIR(_Bounded):
    """单页版面（决策面；无要素载荷 —— 引用帧下标或过滤描述符）。"""

    page_id: str = Field(min_length=1, max_length=120)
    title: str = Field(default="", max_length=200)
    page_number: int = Field(ge=1)
    """1 起页码（含封面时封面 = 1）。"""
    page_count: int = Field(ge=1)
    paper: PagePaper
    bounds: Optional[List[float]] = None
    """页面地图范围 WSEN（封面 = None）。"""
    frame_index: Optional[int] = None
    """frames 驱动：对应 enabled ``layout.frames`` 下标；None = 单帧整幅或过滤页。"""
    filter_layer_id: str = Field(default="", max_length=120)
    """category/feature 驱动：被过滤图层 id。"""
    filter_property: str = Field(default="", max_length=120)
    filter_value: str = Field(default="", max_length=160)
    """category 驱动：本页属性值。"""
    feature_offset: Optional[int] = Field(default=None, ge=0)
    feature_limit: Optional[int] = Field(default=None, ge=1)
    """feature 驱动：本页要素切片 [offset, offset+limit)。"""
    cover: bool = False
    component_ir: Dict[str, Any] = Field(default_factory=dict)
    """C2 组件 IR v2（``build_layout_ir`` 产物；封面为文本页 IR）。

    有界诚实声明：atlas 页与 ≤20 帧的帧路径必有 IR（场景数 ≤ C2 规划帽）；
    非 atlas 帧路径超出 20 帧 时超界页为空 dict（C2 规划场景帽 20）——
    导出渲染循环不消费本字段（渲染面走 MapSpec 编译链），空缺不影响成品。
    """


class PublicationIR(_Bounded):
    """出版页面模型根文档（versioned / content-addressed / refs-only）。"""

    ir_version: str = PUBLICATION_IR_VERSION
    atlas_title: str = Field(default="", max_length=160)
    pages: List[PageLayoutIR] = Field(max_length=MAX_SPEC_FRAMES + 1)
    """封面（可选）+ 内容页。上界 = 帧驱动既有帽（MAX_SPEC_FRAMES）+ 封面；
    atlas 驱动的 20 页诚实封顶在 plan 层由 ``page_budget`` 强制（构造期
    截断 + ``atlas_truncated`` 披露），本模型界只承载帧路径的既有语义。"""
    degradations: List[Dict[str, str]] = Field(
        default_factory=list, max_length=MAX_ATLAS_PAGES)
    atlas: bool = False
    """是否 atlas 分页（False = 单页/显式多帧的常规 publication 链）。"""

    def ir_fingerprint(self) -> str:
        return "pubir-sha256:" + digest_of(self.model_dump())[:40]


# ── 帧几何（publication_export._frame_geometry 的逐字提取；单一真相移入 lib）


def page_geometry(frame: Optional[Dict[str, Any]]) -> Tuple[float, float, Optional[List[float]]]:
    """帧 → (页宽mm, 页高mm, bounds)。缺省 A4 landscape 297×210。

    逐字提取自 publication_export（含 profile 预设、裸尺寸钳制、extent/view
    两表达、zoom 夹取）—— 提取不改任何数值语义（导出字节不变）。
    """
    page_w, page_h = 297.0, 210.0
    bounds: Optional[List[float]] = None
    if isinstance(frame, dict):
        size = frame.get("pageSize")
        if isinstance(size, dict):
            # F14：profile 优先（整体纸型预设），裸宽高兜底
            profile = size.get("profile")
            preset = PAGE_PROFILES.get(profile) if isinstance(profile, str) else None
            if preset is not None:
                page_w, page_h = preset
            else:
                try:
                    w = float(size.get("width") or 0)  # type: ignore[arg-type]
                    h = float(size.get("height") or 0)  # type: ignore[arg-type]
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


def _paper_of(page_w: float, page_h: float, frame: Optional[Dict[str, Any]]) -> PagePaper:
    profile = ""
    size = frame.get("pageSize") if isinstance(frame, dict) else None
    if isinstance(size, dict) and isinstance(size.get("profile"), str):
        profile = size["profile"][:32]
    return PagePaper(
        width_mm=page_w, height_mm=page_h, profile=profile,
        orientation="portrait" if page_h > page_w else "landscape",
    )


# ── 文档面提取（有界；与 publication_export 帧循环同口径）────────────────


def enabled_frames(document: Dict[str, Any], *, cap: int) -> Tuple[List[Any], bool]:
    """``layout.frames`` → (enabled 帧, 是否截断)。过滤后再封顶（同序）。"""
    layout_raw = document.get("layout")
    layout = layout_raw if isinstance(layout_raw, dict) else {}
    frames_raw = layout.get("frames")
    if not (isinstance(frames_raw, list) and frames_raw):
        return [], False
    frames = [f for f in frames_raw if isinstance(f, dict) and f.get("enabled") is not False]
    truncated = len(frames) > cap
    return frames[:cap], truncated


def _inline_feature_collection(
    document: Dict[str, Any], layers: List[Any], layer_id: str,
) -> Tuple[str, List[Dict[str, Any]], bool]:
    """定位内联 geojson 图层 → (layer_id, features 引用列表, 是否截断读)。

    layer.source 是 spec.sources 键引用；geojson 载荷在 ``inlineData``/``data``
    （与 publication_export 未水合检测同键口径）。列表按引用返回（计划面
    只读，不拷贝）。扫描有界（``MAX_ATLAS_SCAN_FEATURES``，超出截断）。
    """
    sources_raw = document.get("sources")
    sources = sources_raw if isinstance(sources_raw, dict) else {}
    target_layer: Optional[Dict[str, Any]] = None
    for layer in layers:
        if not isinstance(layer, dict):
            continue
        if layer_id:
            if layer.get("id") == layer_id:
                target_layer = layer
                break
            continue
        sid = layer.get("source")
        src = sources.get(sid) if isinstance(sid, str) else None
        if isinstance(src, dict) and src.get("type") == "geojson" and _source_features(src):
            target_layer = layer
            break
    if target_layer is None:
        return "", [], False
    sid = target_layer.get("source")
    src_raw = sources.get(sid) if isinstance(sid, str) else None
    src = src_raw if isinstance(src_raw, dict) else {}
    feats = _source_features(src)
    if len(feats) > MAX_ATLAS_SCAN_FEATURES:
        return str(target_layer.get("id") or ""), feats[:MAX_ATLAS_SCAN_FEATURES], True
    return str(target_layer.get("id") or ""), feats, False


def _source_features(src: Dict[str, Any]) -> List[Dict[str, Any]]:
    """geojson 源 → 内联 FeatureCollection 的 features 引用列表（只读）。"""
    payload = src.get("inlineData")
    if not isinstance(payload, dict):
        data_raw = src.get("data")
        payload = data_raw if isinstance(data_raw, dict) else {}
    raw = payload.get("features")
    if not isinstance(raw, list):
        return []
    return [f for f in raw if isinstance(f, dict)]


def _feature_bbox(feats: List[Dict[str, Any]]) -> Optional[List[float]]:
    """要素集合 → WSEN bbox（坐标面有界遍历；非有限值/退化跳过）。"""
    w = s = math.inf
    e = n = -math.inf
    count = 0

    def _walk(coords: Any) -> None:
        nonlocal w, s, e, n, count
        if count > MAX_ATLAS_SCAN_FEATURES * 64:  # 坐标点数硬上界（病态几何防线）
            return
        if isinstance(coords, (list, tuple)) and coords:
            if isinstance(coords[0], (int, float)):
                count += 1
                if len(coords) >= 2:
                    x, y = float(coords[0]), float(coords[1])
                    if math.isfinite(x) and math.isfinite(y):
                        w, e = min(w, x), max(e, x)
                        s, n = min(s, y), max(n, y)
                return
            for c in coords:
                _walk(c)

    for f in feats:
        geom_raw = f.get("geometry")
        geom = geom_raw if isinstance(geom_raw, dict) else {}
        _walk(geom.get("coordinates"))
    # 单点/共线要素（零面积 bbox）合法 —— 页范围由 _fit_bounds 外扩拟合；
    # 只有零有效坐标才判无几何。
    if count == 0 or not math.isfinite(w) or not math.isfinite(s):
        return None
    return [w, s, e, n]


def _fit_bounds(bbox: List[float], aspect_wh: float) -> List[float]:
    """bbox → 纸张纵横比拟合范围（复用冻结 Mercator 数学；退化 → 轻微外扩）。"""
    fitted = export_bounds_for_frame(mask=(bbox[0], bbox[1], bbox[2], bbox[3]),
                                     aspect_wh=aspect_wh)
    if fitted is not None:
        return list(fitted)
    w, s, e, n = bbox
    pad_lng = max((e - w) * 0.5, 1e-4)
    pad_lat = max((n - s) * 0.5, 1e-4)
    padded = [w - pad_lng, s - pad_lat, e + pad_lng, n + pad_lat]
    fitted = export_bounds_for_frame(mask=(padded[0], padded[1], padded[2], padded[3]),
                                     aspect_wh=aspect_wh)
    return list(fitted) if fitted is not None else padded


# ── 计划主入口 ───────────────────────────────────────────────────────────


def _cover_ir(page_ids: List[str], page_titles: List[str], paper: PagePaper) -> Dict[str, Any]:
    """封面/目录页 C2 IR（确定性文本页版面；title + 逐页 text_note，有界）。

    build_layout_ir 冻结契约只承载几何/排版决策（不携带文本载荷）—— 目录
    文本由 PublicationIR 自身字段派生（``atlas_title`` + 页序/页题），与
    导出面 ``_render_toc_svg``、live 渲染器同一事实源。内容页场景
    ≤ MAX_ATLAS_PAGES，组件数 ≤ 1 + 20 < build_layout_ir 的 32 上界。
    """
    components: List[Dict[str, Any]] = [{
        "id": "cover__title", "kind": "title", "role": "secondary",
        "frame": {"x": 0.04, "y": 0.03, "width": 0.92, "height": 0.08,
                  "anchor": "top_left", "z": 10},
        "typography": {"fontFamily": "system-ui", "fontSizePx": 28,
                       "fontWeight": 700, "lineHeightPx": 36,
                       "align": "left", "wrapMode": "cjk_char"},
    }]
    row_h = min(0.04, 0.8 / max(len(page_ids), 1))
    for i in range(len(page_ids)):
        components.append({
            "id": f"cover__toc_{i}", "kind": "text_note", "role": "decorative",
            "frame": {"x": 0.06, "y": 0.14 + i * row_h, "width": 0.88,
                      "height": row_h, "anchor": "top_left", "z": 5},
            "typography": {"fontFamily": "system-ui", "fontSizePx": 14,
                           "fontWeight": 400, "lineHeightPx": 20,
                           "align": "left", "wrapMode": "cjk_char"},
        })
    return build_layout_ir(canvas={"widthPx": paper.width_px,
                                   "heightPx": paper.height_px},
                           components=components)


def plan_publication_pages(
    document: Dict[str, Any],
    *,
    atlas: Optional[AtlasPolicy] = None,
    frames_cap: int = MAX_SPEC_FRAMES,
) -> PublicationIR:
    """MapSpec 文档 → PublicationIR（确定性、有界、typed 失败）。

    无 ``atlas``：frames 驱动（与既有 publication 帧循环 1:1 同序同帽 ——
    ``frames_cap`` 透传调用方帧预算，缺省 MAX_SPEC_FRAMES）。``atlas`` 在场：
    frames/category/feature 驱动分页，封面可选，页数诚实封顶。
    """
    if not isinstance(document, dict):
        raise MapSpecSchemaError("atlas_policy_invalid", "MapSpec 文档必须为对象")
    policy = atlas or AtlasPolicy()
    layers_raw = document.get("layers")
    layers = layers_raw if isinstance(layers_raw, list) else []

    degradations: List[Dict[str, str]] = []
    page_specs: List[Dict[str, Any]] = []  # 中间描述符（paper 为 PagePaper）

    if not atlas or policy.driver == "frames":
        cap = (policy.page_budget if atlas
               else min(max(1, int(frames_cap)), MAX_SPEC_FRAMES))
        frames, truncated = enabled_frames(document, cap=cap)
        layout_raw = document.get("layout")
        layout = layout_raw if isinstance(layout_raw, dict) else {}
        frames_present = isinstance(layout.get("frames"), list) and bool(layout.get("frames"))
        if not frames:
            if frames_present and not atlas:
                # 既有链同语义：frames 在场但全 disabled → typed 拒绝
                # （不回退整幅 —— 用户显式关闭了所有帧）。
                raise MapSpecSchemaError(
                    "publication_no_pages", "layout.frames 存在但无 enabled 帧")
            if atlas:
                # C14 review P3：显式 atlas 请求 + 无帧 → 诚实 typed 拒绝
                # （不静默退化为整幅单页）。
                raise MapSpecSchemaError(
                    "atlas_no_pages",
                    "atlas frames 驱动需要 layout.frames（spec 无帧）")
            # 单帧 = 整幅（与既有链同语义：frames 缺席 → [None]）
            page_w, page_h, bounds = page_geometry(None)
            page_specs.append({
                "page_id": "main", "title": "", "frame_index": None,
                "paper": _paper_of(page_w, page_h, None), "bounds": bounds,
            })
        else:
            if truncated:
                degradations.append({
                    "code": "atlas_truncated",
                    "detail": f"帧数超上界 {cap}，已截断",
                })
            for i, frame in enumerate(frames):
                page_w, page_h, bounds = page_geometry(frame)
                fid = str(frame.get("id") or f"frame{i + 1}")[:120]
                page_specs.append({
                    "page_id": fid, "title": str(frame.get("title") or "")[:200],
                    "frame_index": i,
                    "paper": _paper_of(page_w, page_h, frame), "bounds": bounds,
                })
    elif policy.driver == "category":
        page_specs, degradations = _category_pages(
            document, layers, policy, degradations)
    else:
        page_specs, degradations = _feature_pages(
            document, layers, policy, degradations)

    if not page_specs:
        raise MapSpecSchemaError(
            "atlas_no_pages", "atlas 策略未产出任何页面（检查图层/属性/要素）")

    # C14 review P2：页 id 确定性去重（重复帧 id / 截断类别键碰撞 / 与保留
    # 封面 id 冲突 → 后缀 -2、-3…），杜绝 component_ir 按 page_id 映射串页。
    _reserve_cover = bool(atlas and policy.include_cover)
    _used: set = {"cover"} if _reserve_cover else set()
    for sp in page_specs:
        pid = str(sp["page_id"])
        base, n = pid, 2
        while pid in _used:
            pid = f"{base[:112]}-{n}"
            n += 1
        _used.add(pid)
        sp["page_id"] = pid

    cover_spec: Optional[Dict[str, Any]] = None
    if _reserve_cover:
        cover_spec = {
            "page_id": COVER_PAGE_ID, "title": policy.atlas_title,
            "cover": True, "frame_index": None,
            "paper": page_specs[0]["paper"], "bounds": None,
        }

    # 场景 → C2 组件 IR（plan_atlas_pages 生产接线点）。场景 = 内容页
    # （≤ page_budget ≤ MAX_ATLAS_PAGES，封面 IR 单独装配）—— plan_atlas_pages
    # 的内部截断因此永不触发，不丢任何页的组件 IR。
    first_paper: PagePaper = page_specs[0]["paper"]
    canvas = {"widthPx": first_paper.width_px, "heightPx": first_paper.height_px}
    scenarios = [
        {"id": str(sp["page_id"]), "title": str(sp.get("title") or ""),
         "map_kind": "default"}
        for sp in page_specs
    ]
    planned = plan_atlas_pages(scenarios, canvas=canvas,
                               atlas_title=policy.atlas_title if atlas else "")
    ir_by_page = {p["page_id"]: p["ir"] for p in planned.get("pages", [])}

    if cover_spec is not None:
        cover_spec["component_ir"] = _cover_ir(
            [sp["page_id"] for sp in page_specs],
            [str(sp.get("title") or "") for sp in page_specs],
            first_paper,
        )
        page_specs.insert(0, cover_spec)

    total = len(page_specs)
    pages: List[PageLayoutIR] = []
    for idx, sp in enumerate(page_specs):
        pages.append(PageLayoutIR(
            page_id=str(sp["page_id"]),
            title=str(sp.get("title") or ""),
            page_number=idx + 1,
            page_count=total,
            paper=sp["paper"],
            bounds=sp.get("bounds"),
            frame_index=sp.get("frame_index"),
            filter_layer_id=str(sp.get("filter_layer_id") or ""),
            filter_property=str(sp.get("filter_property") or ""),
            filter_value=str(sp.get("filter_value") or ""),
            feature_offset=sp.get("feature_offset"),
            feature_limit=sp.get("feature_limit"),
            cover=bool(sp.get("cover")),
            component_ir=sp.get("component_ir")
            or ir_by_page.get(str(sp["page_id"]), {}),
        ))

    return PublicationIR(
        atlas_title=policy.atlas_title,
        pages=pages,
        degradations=degradations,
        atlas=bool(atlas),
    )


def _category_pages(
    document: Dict[str, Any],
    layers: List[Any],
    policy: AtlasPolicy,
    degradations: List[Dict[str, str]],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, str]]]:
    """category 驱动：属性去重值 → 逐页（bbox 拟合 + 值过滤描述符）。"""
    prop = policy.category_property
    if not prop:
        raise MapSpecSchemaError(
            "atlas_category_property_missing",
            "category 驱动需要 category_property（要素属性名）")
    fid, feats, scan_truncated = _inline_feature_collection(
        document, layers, policy.layer_id)
    if not fid or not feats:
        raise MapSpecSchemaError(
            "atlas_source_unavailable",
            "category 驱动需要内联 geojson 图层"
            + (f"（layer {policy.layer_id!r} 不存在或无内联数据）" if policy.layer_id else
               "（未找到携带内联 FeatureCollection 的图层）"))
    if scan_truncated:
        degradations.append({
            "code": "atlas_feature_scan_truncated",
            "detail": f"要素扫描停在上界 {MAX_ATLAS_SCAN_FEATURES}，超出的类别未参与分页",
        })
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for f in feats:
        props_raw = f.get("properties")
        props = props_raw if isinstance(props_raw, dict) else {}
        val = props.get(prop)
        if val is None or isinstance(val, (dict, list, bool)):
            continue
        key = str(val)[:160]
        groups.setdefault(key, []).append(f)
    if not groups:
        raise MapSpecSchemaError(
            "atlas_category_empty",
            f"属性 {prop!r} 在内联要素中无合法值（检查 category_property）")
    ordered = sorted(groups.keys())
    truncated = len(ordered) > policy.page_budget
    if truncated:
        degradations.append({
            "code": "atlas_truncated",
            "detail": f"类别数 {len(ordered)} 超页预算 {policy.page_budget}，已截断",
        })
        ordered = ordered[:policy.page_budget]

    paper = _atlas_paper(document)
    page_specs: List[Dict[str, Any]] = []
    for key in ordered:
        bbox = _feature_bbox(groups[key])
        if bbox is None:
            continue
        page_specs.append({
            "page_id": f"cat_{key}"[:120], "title": key,
            "frame_index": None,
            "filter_layer_id": fid, "filter_property": prop, "filter_value": key,
            "paper": paper,
            "bounds": _fit_bounds(bbox, paper.width_mm / paper.height_mm),
        })
    if not page_specs:
        raise MapSpecSchemaError(
            "atlas_category_empty", f"属性 {prop!r} 的要素均无有效几何")
    return page_specs, degradations


def _feature_pages(
    document: Dict[str, Any],
    layers: List[Any],
    policy: AtlasPolicy,
    degradations: List[Dict[str, str]],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, str]]]:
    """feature 驱动：要素切片分块 → 逐页（块 bbox 拟合 + 切片描述符）。"""
    fid, feats, scan_truncated = _inline_feature_collection(
        document, layers, policy.layer_id)
    if not fid or not feats:
        raise MapSpecSchemaError(
            "atlas_source_unavailable",
            "feature 驱动需要内联 geojson 图层"
            + (f"（layer {policy.layer_id!r} 不存在或无内联数据）" if policy.layer_id else
               "（未找到携带内联 FeatureCollection 的图层）"))
    if scan_truncated:
        degradations.append({
            "code": "atlas_feature_scan_truncated",
            "detail": f"要素扫描停在上界 {MAX_ATLAS_SCAN_FEATURES}，超出的要素未参与分页",
        })
    per_page = policy.features_per_page
    chunks = [feats[i:i + per_page] for i in range(0, len(feats), per_page)]
    truncated = len(chunks) > policy.page_budget
    if truncated:
        degradations.append({
            "code": "atlas_truncated",
            "detail": f"分块数 {len(chunks)} 超页预算 {policy.page_budget}，已截断",
        })
        chunks = chunks[:policy.page_budget]

    paper = _atlas_paper(document)
    page_specs: List[Dict[str, Any]] = []
    for idx, chunk in enumerate(chunks):
        bbox = _feature_bbox(chunk)
        if bbox is None:
            continue
        page_specs.append({
            "page_id": f"feat_{idx + 1}",
            "title": f"{idx * per_page + 1}-{idx * per_page + len(chunk)}",
            "frame_index": None,
            "filter_layer_id": fid,
            "feature_offset": idx * per_page, "feature_limit": per_page,
            "paper": paper,
            "bounds": _fit_bounds(bbox, paper.width_mm / paper.height_mm),
        })
    if not page_specs:
        raise MapSpecSchemaError("atlas_no_pages", "feature 驱动未产出有效页面")
    return page_specs, degradations


def _atlas_paper(document: Dict[str, Any]) -> PagePaper:
    """atlas 页纸张：继承 spec 级 pageSize 表达（帧 0 profile 优先，
    裸尺寸兜底），保证纸型 user-wins；缺省 A4 landscape。"""
    layout_raw = document.get("layout")
    layout = layout_raw if isinstance(layout_raw, dict) else {}
    frames_raw = layout.get("frames")
    size: Optional[Dict[str, Any]] = None
    if isinstance(frames_raw, list) and frames_raw and isinstance(frames_raw[0], dict):
        candidate = frames_raw[0].get("pageSize")
        if isinstance(candidate, dict):
            size = candidate
    page_w, page_h, _ = page_geometry({"pageSize": size} if size else None)
    return _paper_of(page_w, page_h, {"pageSize": size} if size else None)


# ── preflight（确定性布局检查；溢出/缺席 → 显式 warning，不 silent crop）──


def publication_preflight(ir: PublicationIR, document: Dict[str, Any]) -> List[Dict[str, str]]:
    """导出前确定性检查（有界 warning 列表；不改版面，只披露）。

    - ``attribution_missing``：spec 无非空 attribution 组件；
    - ``title_wrap_expected``：页标题超软上界（导出面将断行 —— 预期行为披露）；
    - ``legend_overflow``：图例条目超单盒上限（导出面截断 + truncation 披露）。
    """
    warnings: List[Dict[str, str]] = []
    layers_raw = document.get("layers")
    layers = layers_raw if isinstance(layers_raw, list) else []
    layout_raw = document.get("layout")
    layout = layout_raw if isinstance(layout_raw, dict) else {}
    comps_raw = layout.get("components")
    comps = comps_raw if isinstance(comps_raw, list) else []

    has_attribution = any(
        isinstance(c, dict) and c.get("type") == "attribution"
        and str((c.get("options") or {}).get("text") or "").strip()
        for c in comps
    )
    if not has_attribution:
        warnings.append({
            "code": "attribution_missing",
            "detail": "spec 无非空 attribution 组件（出版 completeness 检查）",
        })

    for page in ir.pages[:MAX_ATLAS_PAGES]:
        if page.cover:
            continue
        if len(page.title) > _TITLE_WRAP_CHARS:
            warnings.append({
                "code": "title_wrap_expected",
                "detail": f"页 {page.page_number} 标题长度 {len(page.title)} 超软上界 "
                          f"{_TITLE_WRAP_CHARS}，导出面将按 wrapMode 断行",
            })

    from app.lib.cartography.render_scene import derive_legend_items

    legend_warned = False
    for layer in layers:
        if not isinstance(layer, dict) or not isinstance(layer.get("legend_spec"), dict):
            continue
        model = derive_legend_items(layer["legend_spec"])
        n = len((model or {}).get("entries") or [])
        if n > MAX_LEGEND_ITEMS_PER_BOX and not legend_warned:
            warnings.append({
                "code": "legend_overflow",
                "detail": f"图层 {str(layer.get('id') or '')[:64]} 图例 {n} 条超单盒上限 "
                          f"{MAX_LEGEND_ITEMS_PER_BOX}，导出面截断并披露",
            })
            legend_warned = True
    return warnings[:MAX_ATLAS_PAGES]


__all__ = [
    "PUBLICATION_IR_VERSION",
    "PX_PER_MM",
    "PAGE_PROFILES",
    "MAX_PAGE_MM",
    "MAX_ATLAS_SCAN_FEATURES",
    "COVER_PAGE_ID",
    "AtlasPolicy",
    "PagePaper",
    "PageLayoutIR",
    "PublicationIR",
    "page_geometry",
    "enabled_frames",
    "plan_publication_pages",
    "publication_preflight",
]
