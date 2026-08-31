"""map_completion 语义级 QA（validate_semantics）单元测试。

覆盖组合路径被绕过（组件手工增删）时槽位校验看不见的语义：
图例类型 ↔ 图层 legend_spec 匹配、契约必需图例的主题层覆盖、
报告产品标题、CRS 记录契约。全部 desired-state 纯函数判定。
"""
from types import SimpleNamespace

from app.services.gis_harness.map_completion import (
    F_CRS_NOT_WGS84,
    F_SEMANTIC_LEGEND_MISSING,
    F_SEMANTIC_LEGEND_MISMATCH,
    F_TITLE_MISSING_REPORT,
    validate_semantics,
)


def _contract(legend_required=False):
    return SimpleNamespace(legend_required=legend_required)


def _mapspec(layers=None, components=None):
    return {
        "layers": layers or [],
        "sources": {},
        "layout": {"components": components or []},
    }


def _heatmap_layer(lid="poi-heat"):
    return {
        "id": lid,
        "source": "s1",
        "type": "heatmap",
        "legend_spec": {"type": "continuous", "field": "density",
                        "min": 0, "max": 1, "palette_colors": ["#fff", "#000"]},
    }


def _choropleth_layer(lid="district-fill"):
    return {
        "id": lid,
        "source": "s2",
        "type": "fill",
        "legend_spec": {"type": "graduated", "field": "count",
                        "breaks": [0, 10, 20], "palette_colors": ["#fff", "#000"]},
    }


def test_no_findings_on_healthy_semantics():
    mapspec = _mapspec(
        layers=[_heatmap_layer()],
        components=[
            {"id": "colorbar-main", "type": "continuous_colorbar", "enabled": True,
             "options": {"layerId": "poi-heat"}},
        ],
    )
    assert validate_semantics({}, mapspec, []) == []


# ── 图例类型匹配 ────────────────────────────────────────────────────────


def test_categorical_legend_on_continuous_layer_is_mismatch():
    """heatmap（continuous legend_spec）挂分级图例 → semantic mismatch。"""
    mapspec = _mapspec(
        layers=[_heatmap_layer()],
        components=[
            {"id": "legend-main", "type": "legend", "enabled": True,
             "options": {"layerId": "poi-heat"}},
        ],
    )
    findings = validate_semantics({}, mapspec, [])
    assert [f.code for f in findings] == [F_SEMANTIC_LEGEND_MISMATCH]
    assert "poi-heat" in findings[0].detail


def test_graduated_layer_with_colorbar_is_mismatch():
    mapspec = _mapspec(
        layers=[_choropleth_layer()],
        components=[
            {"id": "colorbar-main", "type": "continuous_colorbar", "enabled": True,
             "options": {"layerId": "district-fill"}},
        ],
    )
    findings = validate_semantics({}, mapspec, [])
    assert [f.code for f in findings] == [F_SEMANTIC_LEGEND_MISMATCH]


def test_correct_binding_no_mismatch():
    mapspec = _mapspec(
        layers=[_choropleth_layer()],
        components=[
            {"id": "legend-main", "type": "legend", "enabled": True,
             "options": {"layerId": "district-fill"}},
        ],
    )
    assert validate_semantics({}, mapspec, []) == []


def test_unbound_legend_not_judged():
    """无 layerId 绑定（HUD 发现语义）/ 绑定层无 legend_spec：无输入不判。"""
    mapspec = _mapspec(
        layers=[{"id": "ref-boundary", "source": "s3", "type": "line"}],
        components=[
            {"id": "legend-hud", "type": "legend", "enabled": True},
            {"id": "legend-orphan", "type": "categorical_legend", "enabled": True,
             "options": {"layerId": "ref-boundary"}},
        ],
    )
    assert validate_semantics({}, mapspec, []) == []


# ── 契约必需图例的主题层覆盖 ───────────────────────────────────────────


def test_required_legend_uncovered_thematic_layer():
    """图例族在场（槽位级不缺）但未覆盖某主题层 → per-layer 欠账披露。"""
    mapspec = _mapspec(
        layers=[_heatmap_layer("layer-a"), _heatmap_layer("layer-b")],
        components=[
            {"id": "colorbar-main", "type": "continuous_colorbar", "enabled": True,
             "options": {"layerId": "layer-a"}},
        ],
    )
    findings = validate_semantics({}, mapspec, [], contract=_contract(True))
    assert [f.code for f in findings] == [F_SEMANTIC_LEGEND_MISSING]
    assert findings[0].target == "layer-b"


def test_legend_family_absent_delegates_to_slot_level():
    """图例族整体缺席 → 不重复披露（component_missing 槽位级已覆盖）。"""
    mapspec = _mapspec(layers=[_heatmap_layer()], components=[])
    findings = validate_semantics({}, mapspec, [], contract=_contract(True))
    assert F_SEMANTIC_LEGEND_MISSING not in [f.code for f in findings]


def test_no_contract_no_coverage_finding():
    mapspec = _mapspec(
        layers=[_heatmap_layer("a"), _heatmap_layer("b")],
        components=[
            {"id": "colorbar-main", "type": "continuous_colorbar", "enabled": True,
             "options": {"layerId": "a"}},
        ],
    )
    assert validate_semantics({}, mapspec, [], contract=_contract(False)) == []


# ── 报告产品标题 ────────────────────────────────────────────────────────


def test_report_product_without_title_warns():
    chapter = {"intent": {"report_product": True}}
    findings = validate_semantics(chapter, _mapspec(), [])
    assert [f.code for f in findings] == [F_TITLE_MISSING_REPORT]


def test_report_title_not_duplicated_when_slot_required():
    chapter = {"intent": {"report_product": True}}
    findings = validate_semantics(chapter, _mapspec(), [["title"]])
    assert findings == []


def test_non_report_product_no_title_finding():
    assert validate_semantics({"intent": {}}, _mapspec(), []) == []


# ── CRS 契约 ────────────────────────────────────────────────────────────


def test_non_wgs84_record_feeding_spec_warns():
    record = SimpleNamespace(crs="EPSG:3857")
    mapspec = _mapspec(layers=[{
        "id": "l1", "source": "s1", "type": "circle",
        "provenance": {"result_ref": "ref:geojson-x"},
    }])
    findings = validate_semantics({}, mapspec, [], records={"ref:geojson-x": record})
    assert [f.code for f in findings] == [F_CRS_NOT_WGS84]


def test_wgs84_and_unknown_crs_pass():
    ok = SimpleNamespace(crs="EPSG:4326")
    unknown = SimpleNamespace(crs="")
    mapspec = _mapspec(layers=[
        {"id": "l1", "source": "s1", "type": "circle",
         "provenance": {"result_ref": "ref:a"}},
        {"id": "l2", "source": "s2", "type": "circle",
         "provenance": {"result_ref": "ref:b"}},
    ])
    assert validate_semantics({}, mapspec, [], records={"ref:a": ok, "ref:b": unknown}) == []


def test_non_wgs84_record_not_feeding_spec_is_quiet():
    record = SimpleNamespace(crs="EPSG:3857")
    mapspec = _mapspec(layers=[{
        "id": "l1", "source": "s1", "type": "circle",
        "provenance": {"result_ref": "ref:other"},
    }])
    assert validate_semantics({}, mapspec, [], records={"ref:geojson-x": record}) == []


# ── review hardening：CRS 词表与未绑定图例 ──────────────────────────────


def test_crs_allowlist_accepts_cgcs2000_and_ogc_urn_forms():
    """EPSG:4490（CGCS2000）同为经纬度地理坐标 —— 中文地理数据标准，
    渲染与 WGS84 等价；OGC urn/URL 归一化到尾随 EPSG 码再判。"""
    from app.services.gis_harness.map_completion import _normalize_crs_for_wgs84

    for crs in ("EPSG:4326", "epsg: 4326", "WGS84", "CRS84", "EPSG:4490",
                "urn:ogc:def:crs:EPSG::4326", "http://www.opengis.net/def/crs/EPSG/0/4326",
                "urn:ogc:def:crs:EPSG::4490"):
        assert _normalize_crs_for_wgs84(crs), crs
    for crs in ("EPSG:3857", "urn:ogc:def:crs:EPSG::3857", ""):
        assert not _normalize_crs_for_wgs84(crs), crs


def test_crs_4490_record_does_not_warn():
    record = SimpleNamespace(crs="EPSG:4490")
    mapspec = _mapspec(layers=[{
        "id": "l1", "source": "s1", "type": "circle",
        "provenance": {"result_ref": "ref:geojson-cgcs"},
    }])
    assert validate_semantics({}, mapspec, [], records={"ref:geojson-cgcs": record}) == []


def test_unbound_legend_covers_thematic_layers():
    """未绑定 layerId 的图例（HUD 发现语义）按渲染现实覆盖全部主题层 ——
    不为它制造 per-layer 欠账噪声。"""
    mapspec = _mapspec(
        layers=[_heatmap_layer("a"), _heatmap_layer("b")],
        components=[
            {"id": "legend-hud", "type": "legend", "enabled": True},  # 未绑定
        ],
    )
    findings = validate_semantics({}, mapspec, [], contract=_contract(True))
    assert F_SEMANTIC_LEGEND_MISSING not in [f.code for f in findings]
