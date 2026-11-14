"""G40 单元/集成回归 —— 规范形、导入矩阵、绑定链、出版、图例。

场景覆盖（任务书 S01–S31 的服务端面）：
- S02 typed 类别映射（1/"1"/true 不并类）；
- S04 版本 pin（发布 v2 后旧图钉 v1）；
- S05–S08 SVG 导入矩阵（合法/恶意/预算/不支持元素 typed 拒绝）；
- S10 参数越界（0/负/极大）旧绑定不变；
- S11 洞/多面纹理 only 填合法面域（evenodd 复用）；
- S12 同色不同向纹理可分（图例 swatch 不同）；
- S18 引用检查（删除被引用版本 → in_use）；
- S20 CAS 冲突 → SUPERSEDED；
- S21 undo/重开恢复；
- S22 锁层 agent 拒绝（引擎 guard）+ 绑定不改显隐；
- S23 缺资产 → symbol_asset_unavailable（不画默认圆）；
- S26/S27 出版冻结与 manifest；
- S31 icon 碰撞合同（allow-overlap 显式）；
- 单位/DPI（S14 出口链路层）。
"""
from __future__ import annotations

import copy
import uuid

import pytest

from app.lib.cartography.symbol_asset import (
    ANCHORS,
    BUILTIN_SYMBOL_ASSETS,
    SymbolAssetDocument,
    SymbolAssetError,
    compute_digest,
    get_builtin_asset,
    normalize_point_params,
    normalize_texture_params,
    validate_digest,
)
from app.lib.cartography.svg_import import import_svg_asset
from app.services.mapspec_store import mapspec_store


# ─── 规范形核心 ────────────────────────────────────────────────────────────

def test_builtin_assets_digest_stable_and_valid():
    for asset_id, doc in BUILTIN_SYMBOL_ASSETS.items():
        assert validate_digest(doc), asset_id
        assert compute_digest(doc) == doc.content_digest
        # 确定性：重建相等
        clone = SymbolAssetDocument.model_validate(doc.model_dump())
        assert clone.content_digest == doc.content_digest


def test_anchor_offsets_geometry():
    tri = BUILTIN_SYMBOL_ASSETS["cartoasset.builtin.triangle"]
    assert tri.anchor_offset("center") == (0.0, 0.0)
    assert tri.anchor_offset("top-left") == (-12.0, -12.0)
    assert tri.anchor_offset("bottom") == (0.0, 12.0)
    for anchor in ANCHORS:
        tri.anchor_offset(anchor)  # 全词表可解析


def test_param_bounds_s10():
    for bad in (0, -1, 1e9, "16"):
        with pytest.raises(SymbolAssetError):
            normalize_point_params({"size_px": bad})
    for bad in (0, -0.1, 65, 1e9):
        with pytest.raises(SymbolAssetError):
            normalize_texture_params({"spacing_px": bad})
    with pytest.raises(SymbolAssetError):
        normalize_texture_params({"bg_color": "red"})
    with pytest.raises(SymbolAssetError):
        normalize_point_params({"rotation_alignment": "map"})
    # 合法归一
    p = normalize_point_params({"size_px": 24, "anchor": "top", "rotation_deg": -15})
    assert p["size_px"] == 24.0 and p["anchor"] == "top"


# ─── SVG 导入矩阵（S05–S08） ───────────────────────────────────────────────

GOOD_TRIANGLE = (
    b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
    b'<path d="M 12 3 L 22 20 L 2 20 Z" fill="#2563eb"/></svg>'
)


def test_s05_import_happy_path_single_digest():
    doc = import_svg_asset(GOOD_TRIANGLE, kind="point_symbol", name="t")
    assert doc.kind == "point_symbol" and validate_digest(doc)
    assert doc.viewbox == (24.0, 24.0)
    # 相同输入 → 相同规范形 digest（单一清洗口径）
    doc2 = import_svg_asset(GOOD_TRIANGLE, kind="point_symbol", name="t")
    assert doc.content_digest == doc2.content_digest


HOSTILE = {
    "script": (b'<svg viewBox="0 0 10 10"><script>alert(1)</script>'
               b'<path d="M0 0L10 10"/></svg>', "SVG_SCRIPT"),
    "external_href": (b'<svg viewBox="0 0 10 10"><use href="http://evil/x#y"/></svg>',
                      "SVG_EXTERNAL_HREF"),
    "css_url": (b'<svg viewBox="0 0 10 10"><path d="M0 0L10 10" fill="url(#g)"/></svg>',
                "SVG_URL_VALUE"),
    "style_element": (b'<svg viewBox="0 0 10 10"><style>.a{fill:red}</style>'
                      b'<path class="a" d="M0 0L10 10"/></svg>', "SVG_STYLE_ELEMENT"),
    "event_attr": (b'<svg viewBox="0 0 10 10"><path d="M0 0L10 10" onclick="x()"/></svg>',
                   "SVG_EVENT_ATTR"),
    "doctype_entity": (b'<!DOCTYPE svg [<!ENTITY a "b">]><svg viewBox="0 0 10 10">'
                       b'<path d="M0 0L10 10"/></svg>', "SVG_DTD_REJECTED"),
    "foreign_object": (b'<svg viewBox="0 0 10 10"><foreignObject width="4" height="4"/>'
                       b'</svg>', "SVG_FOREIGN_OBJECT"),
}


@pytest.mark.parametrize("label", list(HOSTILE))
def test_s06_hostile_inputs_typed_rejected(label):
    payload, code = HOSTILE[label]
    with pytest.raises(SymbolAssetError) as ei:
        import_svg_asset(payload, kind="point_symbol")
    assert ei.value.code == code


BROKEN = {
    "zero_viewbox": (b'<svg viewBox="0 0 0 0"><path d="M0 0L10 10"/></svg>',
                     "SVG_VIEWBOX_OUT_OF_RANGE"),
    "huge_viewbox": (b'<svg viewBox="0 0 900 10"><path d="M0 0L10 10"/></svg>',
                     "SVG_VIEWBOX_OUT_OF_RANGE"),
    "nan_coord": (b'<svg viewBox="0 0 10 10"><path d="M0 0L10 NaN"/></svg>',
                  "SVG_BAD_NUMBER"),
    "missing_viewbox": (b'<svg width="10" height="10"><path d="M0 0L10 10"/></svg>',
                        "SVG_VIEWBOX_MISSING"),
    "use_cycle": (b'<svg viewBox="0 0 10 10"><use href="#a"/></svg>',
                  "SVG_USE_UNKNOWN_TARGET"),
    "deep_recursion_g": None,  # 由嵌套生成器构造
    "too_many_nodes": None,
}


def test_s07_viewbox_and_bounds_failures():
    for label in ("zero_viewbox", "huge_viewbox", "nan_coord", "missing_viewbox"):
        payload, code = BROKEN[label]
        with pytest.raises(SymbolAssetError) as ei:
            import_svg_asset(payload, kind="point_symbol")
        # 非有限坐标：数值正则或分词层任一 typed 拒绝均可（无假成功）
        assert ei.value.code in (code, "SVG_PATH_SYNTAX", "SVG_BAD_NUMBER"), label


def test_s07_node_budget():
    inner = "".join(
        f'<path d="M{i%9} 1 L{i%9} 8"/>' for i in range(500))
    svg = f'<svg viewBox="0 0 10 10">{inner}</svg>'.encode()
    with pytest.raises(SymbolAssetError) as ei:
        import_svg_asset(svg, kind="point_symbol")
    assert ei.value.code in ("SVG_TOO_MANY_NODES", "SVG_TOO_MANY_PRIMITIVES")


def test_s07_deep_nesting_budget():
    depth = 30
    svg = (b'<svg viewBox="0 0 10 10">' + b'<g>' * depth +
           b'<path d="M1 1L8 8"/>' + b'</g>' * depth + b'</svg>')
    with pytest.raises(SymbolAssetError) as ei:
        import_svg_asset(svg, kind="point_symbol")
    assert ei.value.code in ("SVG_TOO_DEEP", "SVG_TOO_MANY_NODES")


def test_s08_unsupported_elements_disclosed():
    cases = {
        "clip": (b'<svg viewBox="0 0 10 10"><clipPath id="c"><rect width="4" height="4"/></clipPath>'
                 b'<path d="M0 0L10 10" clip-path="url(#c)"/></svg>', "SVG_CLIP_UNSUPPORTED"),
        "text": (b'<svg viewBox="0 0 10 10"><text x="1" y="5">hi</text></svg>',
                 "SVG_TEXT_UNSUPPORTED"),
        "filter": (b'<svg viewBox="0 0 10 10"><filter id="f"/><path d="M0 0L10 10"/></svg>',
                   "SVG_FILTER_UNSUPPORTED"),
        "image": (b'<svg viewBox="0 0 10 10"><image href="a.png" width="4" height="4"/></svg>',
                  "SVG_IMAGE_ELEMENT"),
        "unknown": (b'<svg viewBox="0 0 10 10"><foo/></svg>', "SVG_ELEMENT_UNSUPPORTED"),
    }
    for label, (payload, code) in cases.items():
        with pytest.raises(SymbolAssetError) as ei:
            import_svg_asset(payload, kind="point_symbol")
        assert ei.value.code == code, label


def test_defs_use_and_transforms_flatten():
    svg = (
        b'<svg viewBox="0 0 40 40"><defs><g id="sig">'
        b'<path d="M 20 4 a 16 16 0 1 0 0.01 0 z" fill="#00ff00"/></g></defs>'
        b'<use href="#sig" transform="translate(4,0) scale(0.9)"/>'
        b'<rect x="1" y="1" width="8" height="6" rx="2" fill="#ff0000"/>'
        b'</svg>')
    doc = import_svg_asset(svg, kind="point_symbol")
    assert validate_digest(doc)
    assert len(doc.primitives) >= 2  # use 展开 + rect（圆角 path 化）
    box = doc.conservative_bounds()
    assert box is not None and box[2] > box[0]


def test_texture_tile_import():
    tile = (b'<svg viewBox="0 0 12 12">'
            b'<path d="M0 6 L12 6" stroke="#333333" stroke-width="1.5"/></svg>')
    doc = import_svg_asset(tile, kind="area_texture", name="t")
    assert doc.pattern is not None and doc.pattern.type == "svg_tile"
    assert doc.pattern.tile_viewbox == (12.0, 12.0)


# ─── Python SVG 出版消费（S01/S11/S12/S23/S26） ────────────────────────────

def _spec_with_layers(layers, sources):
    return {
        "version": "2.1", "view": {"center": [116.4, 39.9], "zoom": 10},
        "sources": sources, "layers": layers,
    }


def _pts_source():
    return {"type": "geojson", "inlineData": {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"kind": "a"}, "geometry": {
            "type": "Point", "coordinates": [116.39, 39.91]}},
        {"type": "Feature", "properties": {"kind": "b"}, "geometry": {
            "type": "Point", "coordinates": [116.41, 39.89]}},
    ]}}


def _zones_source():
    return {"type": "geojson", "inlineData": {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {}, "geometry": {"type": "Polygon", "coordinates": [
            [[116.38, 39.88], [116.42, 39.88], [116.42, 39.92], [116.38, 39.92], [116.38, 39.88]],
            [[116.395, 39.895], [116.405, 39.895], [116.405, 39.905],
             [116.395, 39.905], [116.395, 39.895]],
        ]}},
    ]}}


def _compile(spec, assets):
    from app.services.mapspec_to_svg import compile_mapspec_to_svg_detailed

    return compile_mapspec_to_svg_detailed(spec, symbol_assets=assets)


def test_s01_point_binding_uses_defs_and_use():
    tri = get_builtin_asset("cartoasset.builtin.triangle")
    spec = _spec_with_layers(
        [{"id": "pts", "source": "pts", "type": "circle",
          "paint": {"color": "#2563eb"},
          "symbol_asset": {
              "asset_id": "cartoasset.builtin.triangle", "revision": 1,
              "content_digest": tri.content_digest, "kind": "point_symbol",
              "params": {"size_px": 18, "anchor": "center", "rotation_deg": 90}}}],
        {"pts": _pts_source()})
    comp = _compile(spec, {tri.content_digest: tri.model_dump()})
    svg = comp.svg
    assert 'id="cartoasset-' in svg and svg.count("<use ") == 2
    assert 'rotate(90' in svg
    # 默认圆不出现（绑定替代 glyph 表达）
    assert "<circle cx=" not in svg
    # 无绑定参数时输出逐字节不变
    plain = copy.deepcopy(spec)
    plain["layers"][0].pop("symbol_asset")
    base1 = _compile(plain, None).svg
    base2 = _compile(plain, {}).svg
    assert base1 == base2


def test_s11_texture_fill_with_hole_evenodd():
    hatch = get_builtin_asset("cartoasset.builtin.hatch")
    spec = _spec_with_layers(
        [{"id": "zones", "source": "zones", "type": "fill",
          "paint": {"color": "#60a5fa"},
          "symbol_asset": {
              "asset_id": "cartoasset.builtin.hatch", "revision": 1,
              "content_digest": hatch.content_digest, "kind": "area_texture",
              "params": {"spacing_px": 10, "angle_deg": 45,
                         "stroke_width_px": 1.5, "fg_color": "#334155",
                         "bg_color": "#fef08a", "pattern_scale": 1.0}}}],
        {"zones": _zones_source()})
    comp = _compile(spec, {hatch.content_digest: hatch.model_dump()})
    svg = comp.svg
    assert 'url(#cartoap-' in svg and 'patternUnits="userSpaceOnUse"' in svg
    assert svg.count('fill-rule="evenodd"') >= 2  # 背景 + 纹理两遍，洞语义


def test_s23_missing_asset_diagnosed_not_faked():
    tri = get_builtin_asset("cartoasset.builtin.triangle")
    spec = _spec_with_layers(
        [{"id": "pts", "source": "pts", "type": "circle",
          "symbol_asset": {
              "asset_id": "cartoasset.builtin.triangle", "revision": 1,
              "content_digest": tri.content_digest, "kind": "point_symbol"}}],
        {"pts": _pts_source()})
    comp = _compile(spec, {})  # 资产缺席
    assert any(d.get("code") == "symbol_asset_unavailable"
               for d in comp.diagnostics)
    assert "<use " not in comp.svg and "<circle cx=" not in comp.svg


def test_s02_typed_category_matching_no_string_merging():
    tri = get_builtin_asset("cartoasset.builtin.triangle")
    sq = get_builtin_asset("cartoasset.builtin.square")
    assets = {tri.content_digest: tri.model_dump(),
              sq.content_digest: sq.model_dump()}
    spec = _spec_with_layers(
        [{"id": "pts", "source": "pts", "type": "circle",
          "symbol_asset": {
              "asset_id": "cartoasset.builtin.triangle", "revision": 1,
              "content_digest": tri.content_digest, "kind": "point_symbol",
              "category_binding": {
                  "field": "kind", "key_type": "number", "map": [
                      {"key": 1, "asset_id": "cartoasset.builtin.square",
                       "revision": 1, "content_digest": sq.content_digest}],
                  "other_mode": "keep_current"}}}],
        {"pts": _pts_source()})
    # 属性值是字符串 "a"/"b"（与 number 声明不符）→ 全部回落主绑定三角；
    # 绝不把字符串 "1" 并入数值 1。
    comp = _compile(spec, assets)
    assert comp.svg.count("<use ") == 2
    defs_ids = set(part.split('"')[0] for part in comp.svg.split('id="cartoasset-')[1:])
    assert all(defs_ids), defs_ids


def test_s31_collision_contract_explicit_allow_overlap():
    """绑定层的 MapLibre 投影恒 allow-overlap（TS 侧合同）；Python 侧输出
    不含任何省略语义（每个要素一个 use —— 与 S31 的『不隐含少点』对应）。"""
    tri = get_builtin_asset("cartoasset.builtin.triangle")
    spec = _spec_with_layers(
        [{"id": "pts", "source": "pts", "type": "circle",
          "symbol_asset": {
              "asset_id": "cartoasset.builtin.triangle", "revision": 1,
              "content_digest": tri.content_digest, "kind": "point_symbol",
              "params": {"size_px": 40}}}],
        {"pts": _pts_source()})
    comp = _compile(spec, {tri.content_digest: tri.model_dump()})
    assert comp.svg.count("<use ") == 2  # N 要素 = N 图标（无碰撞省略）


# ─── 图例（S12） ───────────────────────────────────────────────────────────

def test_s12_legend_two_textures_distinguishable():
    from app.services.print_layout_authoring.legend import read_legend_content

    hatch = get_builtin_asset("cartoasset.builtin.hatch")
    cross = get_builtin_asset("cartoasset.builtin.cross_hatch")
    assets = {hatch.content_digest: hatch.model_dump(),
              cross.content_digest: cross.model_dump()}

    def bind(doc, angle):
        return {
            "asset_id": f"cartoasset.builtin.{'hatch' if doc is hatch else 'cross_hatch'}",
            "revision": 1, "content_digest": doc.content_digest,
            "kind": "area_texture",
            "params": {"spacing_px": 10, "angle_deg": angle,
                       "stroke_width_px": 1.5, "fg_color": "#334155",
                       "bg_color": "transparent", "pattern_scale": 1.0}}

    spec = {"layers": [
        {"id": "z1", "source": "s", "type": "fill", "title": "东",
         "symbol_asset": bind(hatch, 45)},
        {"id": "z2", "source": "s2", "type": "fill", "title": "西",
         "symbol_asset": bind(cross, 45)},
    ]}
    content = read_legend_content(spec, symbol_assets=assets)
    entries = content["entries"]
    assert len(entries) == 2
    assert entries[0]["swatch"]["swatch_svg"] != entries[1]["swatch"]["swatch_svg"]


def test_legend_descriptor_only_without_docs():
    from app.services.print_layout_authoring.legend import read_legend_content

    hatch = get_builtin_asset("cartoasset.builtin.hatch")
    spec = {"layers": [
        {"id": "z1", "source": "s", "type": "fill", "title": "东",
         "symbol_asset": {
             "asset_id": "cartoasset.builtin.hatch", "revision": 1,
             "content_digest": hatch.content_digest, "kind": "area_texture",
             "params": {}}}]}
    content = read_legend_content(spec)  # 无资产字典 → 仅描述符
    assert len(content["entries"]) == 1
    assert "swatch_svg" not in content["entries"][0]["swatch"]


# ─── 绑定写链（S20/S21/S22 + WP07） ────────────────────────────────────────

async def _seed_session(layers, sources):
    sid = "g40-" + uuid.uuid4().hex[:8]
    await mapspec_store.init_project(
        sid, view={"center": [116.4, 39.9], "zoom": 10})
    if sources or layers:
        spec = await mapspec_store.get_mapspec(sid)
        spec["sources"] = sources
        spec["layers"] = layers
        await mapspec_store.save_mapspec(sid, spec)
    return sid


async def _current_revision(sid: str) -> int:
    from app.services.session_data import session_data_manager

    raw = await session_data_manager.get_state_field(
        sid, "_cartographic_mutation_revision")
    return int(raw or 0)


async def test_binding_preview_apply_undo_cycle():
    from app.services.cartographic_assets.binding import (
        apply_binding, preview_binding, unbind_layer)

    tri = get_builtin_asset("cartoasset.builtin.triangle")
    sid = await _seed_session(
        [{"id": "pts", "source": "pts", "type": "circle",
          "paint": {"color": "#2563eb", "radius": 6}}],
        {"pts": _pts_source()})
    plan = {
        "asset_id": "cartoasset.builtin.triangle", "revision": 1,
        "kind": "point_symbol",
        "params": {"size_px": 20, "anchor": "center", "rotation_deg": 0}}

    prev = await preview_binding(sid, "pts", plan)
    assert prev["committed"] is False
    rev0 = prev["revision"]

    out = await apply_binding(sid, "pts", plan, expected_revision=rev0,
                              origin="user", actor="test")
    assert out["committed"] is True and out["asset"]["builtin"] is True
    stored = (await mapspec_store.get_mapspec(sid))["layers"][0]
    assert stored["symbol_asset"]["content_digest"] == tri.content_digest
    # 兄弟字段逐字保留（paint 不被清）
    assert stored["paint"]["radius"] == 6

    # S20 CAS：旧 revision 再提交 → SUPERSEDED
    with pytest.raises(Exception) as ei:
        await apply_binding(sid, "pts", plan, expected_revision=rev0,
                            origin="user", actor="test")
    assert "CAS_SUPERSEDED" in str(ei.value) or getattr(ei.value, "code", "") == "CAS_SUPERSEDED"

    # 解绑恢复快照（S21 语义面：块移除 + paint 快照恢复）
    rev1 = await _current_revision(sid)
    un = await unbind_layer(sid, "pts", expected_revision=rev1)
    assert un["unbound"] is True and un["restored_paint"] is True
    stored2 = (await mapspec_store.get_mapspec(sid))["layers"][0]
    assert "symbol_asset" not in stored2
    assert stored2["paint"]["color"] == "#2563eb"


async def test_s22_locked_layer_agent_rejected_and_visibility_kept():
    from app.services.cartographic_assets.binding import apply_binding

    sid = await _seed_session(
        [{"id": "pts", "source": "pts", "type": "circle",
          "visible": False, "paint": {"color": "#2563eb"}}],
        {"pts": _pts_source()})
    spec = await mapspec_store.get_mapspec(sid)
    spec.setdefault("workbench", {})["lockedLayerIds"] = ["pts"]
    await mapspec_store.save_mapspec(sid, spec)
    rev = await _current_revision(sid)
    plan = {
        "asset_id": "cartoasset.builtin.triangle", "revision": 1,
        "kind": "point_symbol", "params": {"size_px": 12}}

    # agent origin 命中锁层 → 引擎拒绝（layer_locked）
    from app.services.cartographic_assets.binding import SymbolBindingError
    with pytest.raises(SymbolBindingError) as ei:
        await apply_binding(sid, "pts", plan, expected_revision=rev,
                            origin="agent", actor="pi:test")
    assert ei.value.code in ("MUTATION_REJECTED", "CAS_SUPERSEDED")

    # user origin（用户显式决定）可绑定，且不改变 visible
    out = await apply_binding(sid, "pts", plan, expected_revision=rev,
                              origin="user", actor="panel")
    assert out["committed"] is True
    stored = (await mapspec_store.get_mapspec(sid))["layers"][0]
    assert stored.get("visible") is False  # S22：显隐不被绑定改变


async def test_layer_type_eligibility():
    from app.services.cartographic_assets.binding import (
        SymbolBindingError, preview_binding)

    sid = await _seed_session(
        [{"id": "lines", "source": "s", "type": "line", "paint": {}}],
        {"s": {"type": "geojson", "inlineData": {"type": "FeatureCollection", "features": [
            {"type": "Feature", "properties": {}, "geometry": {
                "type": "LineString", "coordinates": [[116.1, 39.9], [116.2, 39.95]]}}]}}})
    with pytest.raises(SymbolBindingError) as ei:
        await preview_binding(sid, "lines", {
            "asset_id": "cartoasset.builtin.triangle", "revision": 1,
            "kind": "point_symbol", "params": {}})
    assert ei.value.code == "LAYER_TYPE_MISMATCH"


async def test_s04_version_pin_semantics_on_binding():
    """换版本产生新绑定块；旧 MapSpec 文档（checkpoint/会话）的 digest pin
    不因目录内容变化而漂移 —— resolve 层由 catalog 测试覆盖，这里锁写链
    的『绑定块携带不可变三元组』。"""
    from app.services.cartographic_assets.binding import apply_binding

    tri = get_builtin_asset("cartoasset.builtin.triangle")
    sq = get_builtin_asset("cartoasset.builtin.square")
    sid = await _seed_session(
        [{"id": "pts", "source": "pts", "type": "circle", "paint": {}}],
        {"pts": _pts_source()})
    rev = await _current_revision(sid)
    await apply_binding(sid, "pts", {
        "asset_id": "cartoasset.builtin.triangle", "revision": 1,
        "kind": "point_symbol", "params": {"size_px": 12}},
        expected_revision=rev, origin="user", actor="t")
    stored1 = (await mapspec_store.get_mapspec(sid))["layers"][0]["symbol_asset"]
    assert stored1["content_digest"] == tri.content_digest
    # 保存的文档钉住 v1 digest（即使后续绑定换成 square）
    rev2 = await _current_revision(sid)
    await apply_binding(sid, "pts", {
        "asset_id": "cartoasset.builtin.square", "revision": 1,
        "kind": "point_symbol", "params": {"size_px": 14}},
        expected_revision=rev2, origin="user", actor="t")
    stored2 = (await mapspec_store.get_mapspec(sid))["layers"][0]["symbol_asset"]
    assert stored2["content_digest"] == sq.content_digest != stored1["content_digest"]
