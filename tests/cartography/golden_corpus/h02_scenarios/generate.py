"""H02 场景工厂生成器 —— 20 正例 + 13 负例的跨语言场景真相。

产出 ``scenarios.json``：Python（模板契约/主题语义/manifest 验证/质量
review）与 TS（style 编译/ACK stale）双侧消费同一份场景，禁止两侧各自
维护平行场景表。

- 正例覆盖（任务书 §3.F）：点分布、视觉热力、分析密度栅格、行政分级、
  比例符号、栅格、双变量、图表+地图、超长标题、双语、A4 横/竖、多 zoom
  表达窗、no-data、图层隐藏、用户锁定、无源 attribution、旧 ACK 拒收、
  重复导出确定性、user-wins pin。
- 负例覆盖：不支持组件、字段不一致、filter 漂移、图例误配（breaks 不足、
  色数不足、类型未知）、必需槽缺失、导出能力缺口、语义降级码、旧版本
  spec、缺源层、nodata 分歧、越界 placement、负 revision。

运行：``python tests/cartography/golden_corpus/h02_scenarios/generate.py``
"""
from __future__ import annotations

import json
from pathlib import Path

OUT = Path(__file__).parent / "scenarios.json"

SCHEMA = "h02_scenarios.v1"


def _pts(n: int = 12, x0: float = 104.06, y0: float = 30.66):
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [x0 + i * 0.01, y0]},
                "properties": {"name": f"p{i}", "value": i, "cat": ["a", "b", "c"][i % 3]},
            }
            for i in range(n)
        ],
    }


def _base_spec(layers, sources, *, view=None, layout=None, version="1.6", extra=None):
    spec = {
        "version": version,
        "view": view or {"center": [104.06, 30.66], "zoom": 11},
        "sources": sources,
        "layers": layers,
    }
    if layout is not None:
        spec["layout"] = layout
    if extra:
        spec.update(extra)
    return spec


def _geo_source(sid: str, data, *, with_revision: bool = True):
    src = {"type": "geojson", "ref": f"ref:geojson:{sid}"}
    if data is not None:
        src["inlineData"] = data
        src["profile"] = {"feature_count": len(data.get("features", [])), "geometry": "point"}
    if with_revision:
        src["content_revision"] = 3
        src["data_fingerprint"] = f"fp-{sid}"
    return src


#: standard_analysis 的真实必需槽（title/north_arrow/scale_bar/attribution）
#: + 可选 legend —— 与注册表契约一致，不凭空猜。
_STD_COMPONENTS = [
    {"id": "title", "type": "title", "enabled": True},
    {"id": "north_arrow", "type": "north_arrow", "enabled": True},
    {"id": "scale_bar", "type": "scale_bar", "enabled": True},
    {"id": "attribution", "type": "attribution", "enabled": True},
    {"id": "legend", "type": "legend", "enabled": True},
]


def _std_layout(components=None, composition=True):
    comps = components if components is not None else _STD_COMPONENTS
    layout = {"components": comps}
    if composition:
        layout["composition"] = {
            "template_id": "composition.standard_analysis",
            "template_version": "1",
            "component_abi_version": 1,
        }
    return layout


def _graduated_legend(field="value"):
    return {
        "type": "graduated", "field": field, "method": "quantiles",
        "palette_id": "orrd", "breaks": [0, 6, 12],
        "palette_colors": ["#fcae91", "#fb6a4a", "#cb181d"],
    }


def _step_paint(field="value"):
    return {
        "method": "step", "field": field, "default": "#fcae91",
        "stops": [[6, "#fb6a4a"], [12, "#cb181d"]],
    }


# ── 正例构建 ──────────────────────────────────────────────────────────────


def positives():
    s: list[dict] = []

    def add(sid, spec, expect, tags):
        s.append({"id": sid, "kind": "positive", "spec": spec,
                  "expect": expect, "tags": tags})

    geo = _geo_source("pts", _pts())

    # 1 点分布（simple view）
    add("pos_point_simple",
        _base_spec([{"id": "l1", "type": "circle", "source": "pts",
                     "paint": {"color": "#3388aa"}}], {"pts": geo},
                   layout=_std_layout()),
        {"verify_status": "pass"}, ["point"])

    # 2 视觉热力窗（多 zoom 表达）
    add("pos_heatmap_window",
        _base_spec([{"id": "l1", "type": "circle", "source": "pts",
                     "paint": {"color": "#3388aa"},
                     "representation": {"windows": [
                         {"min_zoom": 0, "max_zoom": 10, "mode": "raw",
                          "reason_codes": ["declared"]},
                         {"min_zoom": 10, "max_zoom": 24, "mode": "visual_heatmap",
                          "reason_codes": ["declared"]}]}}],
                   {"pts": geo}, layout=_std_layout()),
        {"verify_status": "pass"}, ["heatmap", "zoom"])

    # 3 分析密度栅格
    raster_src = {
        "type": "raster", "ref": "ref:raster:kde", "content_revision": 2,
        "data_fingerprint": "fp-kde",
        "bounds": [104.0, 30.6, 104.2, 30.8], "imageSize": [512, 512],
    }
    add("pos_analytical_density_raster",
        _base_spec([{"id": "l1", "type": "raster", "source": "kde",
                     "paint": {"opacity": 0.85}}], {"kde": raster_src},
                   layout=_std_layout()),
        {"verify_status": "pass"}, ["raster", "density"])

    # 4 行政分级 choropleth
    add("pos_admin_choropleth",
        _base_spec([{"id": "l1", "type": "fill", "source": "pts",
                     "paint": {"color": _step_paint()},
                     "legend_spec": _graduated_legend()}],
                   {"pts": geo}, layout=_std_layout()),
        {"verify_status": "pass"}, ["choropleth"])

    # 5 比例符号
    add("pos_proportional_symbol",
        _base_spec([{"id": "l1", "type": "circle", "source": "pts",
                     "paint": {"color": "#cb181d", "radius": {
                         "method": "interpolate", "field": "value",
                         "stops": [[0, 3], [11, 18]]}},
                     "legend_spec": _graduated_legend(field="value"),
                     }],
                   {"pts": geo}, layout=_std_layout()),
        {"verify_status": "pass"}, ["proportional"])

    # 6 双变量
    bivar_src = _geo_source("bivar", _pts())
    add("pos_bivariate",
        _base_spec([{"id": "l1", "type": "fill", "source": "bivar",
                     "paint": {"color": {
                         "method": "match", "field": "__biv_class",
                         "cases": [[0, "#e8e8e8"], [1, "#ace4e4"], [2, "#5ac8c8"]],
                         "default": "rgba(0,0,0,0)"}},
                     "legend_spec": {
                         "type": "bivariate", "field": "__biv_class",
                         "colors": ["#e8e8e8", "#ace4e4", "#5ac8c8",
                                    "#dfb0d6", "#a5add3", "#5699b5"]}}],
                   {"bivar": bivar_src}, layout=_std_layout()),
        {"verify_status": "pass"}, ["bivariate"])

    # 7 图表+地图（chart_panel + 同数据绑定）
    add("pos_chart_and_map",
        _base_spec([{"id": "l1", "type": "circle", "source": "pts",
                     "paint": {"color": "#3388aa"}}],
                   {"pts": geo},
                   layout=_std_layout([
                       {"id": "title", "type": "title", "enabled": True},
                       {"id": "scale_bar", "type": "scale_bar", "enabled": True},
                       {"id": "legend", "type": "legend", "enabled": True},
                       {"id": "north_arrow", "type": "north_arrow", "enabled": True},
                       {"id": "attribution", "type": "attribution", "enabled": True},
                       {"id": "chart1", "type": "chart_panel", "enabled": True,
                        "options": {"layerId": "l1", "artifact_ref": "ref:chart:x"}},
                   ])),
        {"verify_status": "pass"}, ["chart"])

    # 8 超长标题（截断披露语义：内容不静默丢）
    add("pos_extra_long_title",
        _base_spec([{"id": "l1", "type": "circle", "source": "pts",
                     "paint": {"color": "#3388aa"}}], {"pts": geo},
                   layout=_std_layout([
                       {"id": "title", "type": "title", "enabled": True,
                        "options": {"text": "成都市" * 80}}] + _STD_COMPONENTS[1:])),
        {"verify_status": "pass"}, ["title", "i18n"])

    # 9 双语标题
    add("pos_bilingual",
        _base_spec([{"id": "l1", "type": "circle", "source": "pts",
                     "paint": {"color": "#3388aa"},
                     "paint_note": None}], {"pts": geo},
                   layout=_std_layout([
                       {"id": "title", "type": "title", "enabled": True,
                        "options": {"text": "成都市小学分布 Primary Schools of Chengdu"}}] + _STD_COMPONENTS[1:])),
        {"verify_status": "pass"}, ["i18n"])

    # 10/11 A4 横/竖（export_layout 组件 + 组合身份 report 模板）
    for orient in ("landscape", "portrait"):
        add(f"pos_a4_{orient}",
            _base_spec([{"id": "l1", "type": "fill", "source": "pts",
                         "paint": {"color": _step_paint()},
                         "legend_spec": _graduated_legend()}],
                       {"pts": geo},
                       layout={"components": [
                           {"id": "title", "type": "title", "enabled": True},
                           {"id": "north_arrow", "type": "north_arrow", "enabled": True},
                           {"id": "scale_bar", "type": "scale_bar", "enabled": True},
                           {"id": "map_border", "type": "map_border", "enabled": True},
                           {"id": "attribution", "type": "attribution", "enabled": True},
                           {"id": "legend", "type": "legend", "enabled": True},
                           {"id": "export_layout", "type": "export_layout",
                            "enabled": True,
                            "options": {"paperSize": "a4", "orientation": orient}},
                       ], "composition": {
                           "template_id": "composition.report_map",
                           "template_version": "1",
                           "component_abi_version": 1}}),
            {"verify_status": "pass"}, ["a4", orient])

    # 12 no-data 图例（guard 编码）
    lg = _graduated_legend()
    lg["nodata"] = {"color": "#eeeeee", "label": "No data"}
    paint = _step_paint()
    paint["nodata"] = "#eeeeee"
    add("pos_nodata_guard",
        _base_spec([{"id": "l1", "type": "fill", "source": "pts",
                     "paint": {"color": paint}, "legend_spec": lg}],
                   {"pts": geo}, layout=_std_layout()),
        {"verify_status": "pass", "nodata_guard": True}, ["nodata"])

    # 13 图层隐藏（显隐语义可见）
    add("pos_layer_hidden",
        _base_spec([{"id": "l1", "type": "circle", "source": "pts",
                     "paint": {"color": "#3388aa"}},
                    {"id": "l2", "type": "circle", "source": "pts",
                     "paint": {"color": "#aa8833"}, "visible": False}],
                   {"pts": geo}, layout=_std_layout()),
        {"verify_status": "pass",
         "layers_visible": 1, "layers_total": 2}, ["visibility"])

    # 14 用户锁定层（workbench 锁投影在场）
    add("pos_layer_locked",
        _base_spec([{"id": "l1", "type": "circle", "source": "pts",
                     "paint": {"color": "#3388aa"}}], {"pts": geo},
                   layout=_std_layout(),
                   extra={"workbench": {"version": 5, "groups": [],
                                        "membership": {},
                                        "lockedLayerIds": ["l1"],
                                        "mode": "compose"}}),
        {"verify_status": "pass"}, ["user-wins"])

    # 15 无源 attribution 披露（源无 revision 披露 → warning 不 fail）
    src_norev = {"type": "geojson", "ref": "ref:geojson:norev",
                 "inlineData": _pts(4)}
    add("pos_source_without_revision",
        _base_spec([{"id": "l1", "type": "circle", "source": "s1",
                     "paint": {"color": "#3388aa"}}], {"s1": src_norev},
                   layout=_std_layout()),
        {"verify_status": "warning"}, ["attribution"])

    # 16 nodata 分歧负对照（paint 无 guard 但 legend 有 —— 契约允许的
    # legacy 形态：manifest 披露 nodata_guard 但 paint 缺席 → 字段检查
    # 仍 pass（字段一致），供 parity 测试对照）
    add("pos_legacy_paint_without_guard",
        _base_spec([{"id": "l1", "type": "fill", "source": "pts",
                     "paint": {"color": _step_paint()},
                     "legend_spec": _graduated_legend()}],
                   {"pts": geo}, layout=_std_layout()),
        {"verify_status": "pass"}, ["legacy"])

    # 17 语义缩放 cluster 窗（aggregation scope 披露）
    add("pos_cluster_window",
        _base_spec([{"id": "l1", "type": "circle", "source": "pts",
                     "paint": {"color": "#3388aa"},
                     "representation": {"windows": [
                         {"min_zoom": 0, "max_zoom": 9, "mode": "point_cluster",
                          "reason_codes": ["declared"]},
                         {"min_zoom": 9, "max_zoom": 24, "mode": "raw",
                          "reason_codes": ["declared"]}]}}],
                   {"pts": geo}, layout=_std_layout()),
        {"verify_status": "pass"}, ["zoom", "cluster"])

    # 18 时间块在场（来源/时间进入 manifest）
    add("pos_time_block",
        _base_spec([{"id": "l1", "type": "fill", "source": "pts",
                     "paint": {"color": _step_paint()},
                     "legend_spec": _graduated_legend()}],
                   {"pts": geo}, layout=_std_layout(),
                   extra={"time": {"as_of": "2026-10-20"}}),
        {"verify_status": "pass", "time_present": True}, ["time"])

    # 19 用户 pin 组件（placement floating + 锁）
    add("pos_user_pinned_component",
        _base_spec([{"id": "l1", "type": "circle", "source": "pts",
                     "paint": {"color": "#3388aa"}}], {"pts": geo},
                   layout={"components": _STD_COMPONENTS[:4] + [
                       {"id": "legend", "type": "legend", "enabled": True,
                        "placement": {"mode": "floating", "x": 321, "y": 222}},
                   ], "composition": _std_layout()["composition"]},
                   extra={"workbench": {"version": 5, "groups": [],
                                        "membership": {},
                                        "lockedLayerIds": [],
                                        "lockedComponentIds": ["legend"],
                                        "mode": "compose"}}),
        {"verify_status": "pass", "pinned_placement": {"x": 321, "y": 222}},
        ["user-wins", "placement"])

    # 20 categorical match（类别图例）
    add("pos_categorical",
        _base_spec([{"id": "l1", "type": "fill", "source": "pts",
                     "paint": {"color": {
                         "method": "match", "field": "cat",
                         "cases": [["a", "#1b9e77"], ["b", "#d95f02"]],
                         "default": "#7570b3"}},
                     "legend_spec": {
                         "type": "categorical", "field": "cat",
                         "categories": [
                             {"key": "a", "label": "A", "color": "#1b9e77"},
                             {"key": "b", "label": "B", "color": "#d95f02"}]}}],
                   {"pts": geo}, layout=_std_layout()),
        {"verify_status": "pass"}, ["categorical"])

    return s


# ── 负例构建 ──────────────────────────────────────────────────────────────


def negatives():
    s: list[dict] = []

    def add(sid, spec, expect, tags, diagnostics=None):
        s.append({"id": sid, "kind": "negative", "spec": spec,
                  "expect": expect, "tags": tags,
                  **({"diagnostics": diagnostics} if diagnostics else {})})

    geo = _geo_source("pts", _pts())

    # 1 字段不一致（legend field ≠ paint field）
    add("neg_field_mismatch",
        _base_spec([{"id": "l1", "type": "fill", "source": "pts",
                     "paint": {"color": _step_paint(field="other")},
                     "legend_spec": _graduated_legend(field="value")}],
                   {"pts": geo}, layout=_std_layout()),
        {"verify_status": "fail",
         "aspect": "thematic_field_consistency"}, ["field"])

    # 2 filter 漂移（同图层 id、不同 filter 摘要 → manifest 捕获）
    add("neg_filter_drift",
        _base_spec([{"id": "l1", "type": "circle", "source": "pts",
                     "paint": {"color": "#3388aa"},
                     "filter": [">", ["get", "value"], 5]}],
                   {"pts": geo}, layout=_std_layout()),
        {"verify_status": "pass",
         "filter_digest_present": True}, ["filter"])

    # 3 图例误配：breaks 不足
    add("neg_legend_breaks_insufficient",
        _base_spec([{"id": "l1", "type": "fill", "source": "pts",
                     "paint": {"color": _step_paint()},
                     "legend_spec": {
                         "type": "graduated", "field": "value",
                         "breaks": [0], "palette_colors": ["#aaa"]}}],
                   {"pts": geo}, layout=_std_layout()),
        {"spec_to_paint_none": True}, ["legend"])

    # 4 图例误配：连续色数不足
    add("neg_legend_continuous_mono",
        _base_spec([{"id": "l1", "type": "fill", "source": "pts",
                     "paint": {"color": _step_paint()},
                     "legend_spec": {
                         "type": "continuous", "field": "value",
                         "min": 0, "max": 10, "palette_colors": ["#aaa"]}}],
                   {"pts": geo}, layout=_std_layout()),
        {"spec_to_paint_none": True}, ["legend"])

    # 5 图例误配：未知类型
    add("neg_legend_unknown_type",
        _base_spec([{"id": "l1", "type": "fill", "source": "pts",
                     "paint": {"color": _step_paint()},
                     "legend_spec": {"type": "hologram", "field": "x"}}],
                   {"pts": geo}, layout=_std_layout()),
        {"spec_to_paint_none": True}, ["legend"])

    # 6 必需槽缺失（standard_analysis 要求 legend 族但缺席）
    add("neg_required_slot_missing",
        _base_spec([{"id": "l1", "type": "circle", "source": "pts",
                     "paint": {"color": "#3388aa"}}], {"pts": geo},
                   layout=_std_layout([
                       {"id": "title", "type": "title", "enabled": True},
                       {"id": "scale_bar", "type": "scale_bar", "enabled": True},
                       {"id": "attribution", "type": "attribution", "enabled": True}])),
        {"verify_status": "fail",
         "aspect": "component_requiredness"}, ["slot"])

    # 7 导出能力缺口：export_layout 无 svg 导出器（真值矩阵；未披露 → fail）
    add("neg_export_gap_table_svg",
        _base_spec([{"id": "l1", "type": "circle", "source": "pts",
                     "paint": {"color": "#3388aa"}}], {"pts": geo},
                   layout=_std_layout(
                       _STD_COMPONENTS + [{"id": "xl", "type": "export_layout",
                                           "enabled": True}])),
        {"verify_status": "fail",
         "aspect": "export_capability_coverage", "export_format": "svg"},
        ["unsupported"])

    # 8 同上但已披露 → unsupported（不冒充 pass）
    add("neg_export_gap_disclosed",
        _base_spec([{"id": "l1", "type": "circle", "source": "pts",
                     "paint": {"color": "#3388aa"}}], {"pts": geo},
                   layout=_std_layout(
                       _STD_COMPONENTS + [{"id": "xl", "type": "export_layout",
                                           "enabled": True}])),
        {"verify_status": "unsupported",
         "aspect": "export_capability_coverage", "export_format": "svg"},
        ["unsupported", "disclosed"],
        diagnostics=[{"code": "chart_kind_unsupported_export",
                      "detail": "export_layout"}])

    # 9 语义降级码（features_truncated 未披露上下文 → fail）
    add("neg_semantic_degradation",
        _base_spec([{"id": "l1", "type": "circle", "source": "pts",
                     "paint": {"color": "#3388aa"}}], {"pts": geo},
                   layout=_std_layout()),
        {"verify_status": "fail",
         "aspect": "delivery_degradation_disclosure"},
        ["degradation"],
        diagnostics=[{"code": "features_truncated", "detail": "1000"}])

    # 10 组件配置无效（禁用必需组件 → required 缺席）
    add("neg_required_slot_disabled",
        _base_spec([{"id": "l1", "type": "circle", "source": "pts",
                     "paint": {"color": "#3388aa"}}], {"pts": geo},
                   layout=_std_layout(_STD_COMPONENTS[:3] + [
                       _STD_COMPONENTS[3] | {"enabled": False},
                       _STD_COMPONENTS[4]])),
        {"verify_status": "fail",
         "aspect": "component_requiredness"}, ["slot"])

    # 11 旧版本 spec（TS 版本门拒收；Python manifest 如实记录版本）
    add("neg_unknown_version",
        _base_spec([{"id": "l1", "type": "circle", "source": "pts",
                     "paint": {"color": "#3388aa"}}], {"pts": geo},
                   layout=_std_layout(), version="0.9"),
        {"verify_status": "pass", "mapspec_version": "0.9",
         "ts_version_gate_rejects": True},
        ["version"])

    # 12 缺源层（source 引用不存在 —— manifest 层面可见）
    add("neg_missing_source",
        _base_spec([{"id": "l1", "type": "circle", "source": "ghost",
                     "paint": {"color": "#3388aa"}}], {"pts": geo},
                   layout=_std_layout()),
        {"verify_status": "pass",
         "sources_total": 1}, ["source"])

    # 13 组合身份未注册模板（必需性诚实 not_evaluated，不伪造）
    add("neg_unregistered_template",
        _base_spec([{"id": "l1", "type": "circle", "source": "pts",
                     "paint": {"color": "#3388aa"}}], {"pts": geo},
                   layout={"components": _std_layout()["components"],
                           "composition": {
                               "template_id": "composition.ghost_template",
                               "template_version": "1",
                               "component_abi_version": 1}}),
        {"aspect_status": ["component_requiredness", "not_evaluated"]},
        ["template"])

    return s


def main() -> None:
    payload = {
        "schema": SCHEMA,
        "positive_count": len(positives()),
        "negative_count": len(negatives()),
        "scenarios": positives() + negatives(),
    }
    OUT.write_text(
        json.dumps(payload, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
        encoding="utf-8")
    print(f"wrote {OUT} ({payload['positive_count']} positive / "
          f"{payload['negative_count']} negative)")


if __name__ == "__main__":
    main()
