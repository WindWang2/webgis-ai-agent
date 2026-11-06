"""W2.2（ADR-0283 D3）：label_field_missing 守卫 —— 显式标注字段缺失。

纯函数 :func:`check_label_fields`（closed vocab 码 ``label_field_missing``，
warning）+ 导出孪生接线（mapspec_to_svg 标签编译入口）。调研结论见模块
``app/lib/cartography/label_field_guard.py`` docstring：semantic_checks 的
``_check_label_collision`` 对字段缺失 silently skip（avg_chars=None →
return），``_check_layer_paint_fields`` 只覆盖 paint 字段 —— 显式标注字段
的确定性缺失检查此前不存在。
"""
import pytest

from app.lib.cartography.label_field_guard import (
    LABEL_FIELD_MISSING,
    check_label_fields,
    inline_source_field_set,
)


def _provider(fields_by_source):
    def provide(source_id: str):
        return fields_by_source.get(source_id)

    return provide


def _spec(layers):
    return {"version": "1.1", "view": {"center": [116.0, 39.0], "zoom": 10},
            "sources": {"pts": {"type": "geojson"}}, "layers": layers}


SYM = {"id": "lyr1", "source": "pts", "type": "symbol",
       "layout": {"text-field": "{pop}", "text-size": 12}}


def test_explicit_text_field_missing_fires_warning():
    findings = check_label_fields(_spec([SYM]), _provider({"pts": {"name", "kind"}}))
    assert len(findings) == 1
    f = findings[0]
    assert f["code"] == LABEL_FIELD_MISSING
    assert f["severity"] == "warning"
    assert f["layer_id"] == "lyr1"
    assert f["field"] == "pop"
    assert f["evidence"]["source_field_count"] == 2
    assert set(f["evidence"]["source_fields_sample"]) == {"name", "kind"}


def test_field_present_no_finding():
    sym = {"id": "lyr1", "source": "pts", "type": "symbol",
           "layout": {"text-field": "{pop}"}}
    assert check_label_fields(_spec([sym]), _provider({"pts": {"pop", "name"}})) == []


def test_default_name_pattern_not_explicit():
    # 无 text-field 键 → 缺省 "{name}" 不是显式声明，不触发守卫
    sym = {"id": "lyr1", "source": "pts", "type": "symbol", "layout": {}}
    assert check_label_fields(_spec([sym]), _provider({"pts": set()})) == []


def test_label_strategy_field_missing_fires():
    lyr = {"id": "lyr1", "source": "pts", "type": "fill",
           "label": {"field": "名称", "mode": "all"}}
    findings = check_label_fields(_spec([lyr]), _provider({"pts": {"name"}}))
    assert [f["field"] for f in findings] == ["名称"]


def test_text_field_and_label_field_same_missing_deduped():
    lyr = {"id": "lyr1", "source": "pts", "type": "symbol",
           "layout": {"text-field": "{名称}"},
           "label": {"field": "名称"}}
    findings = check_label_fields(_spec([lyr]), _provider({"pts": {"name"}}))
    assert len(findings) == 1


def test_literal_text_field_not_field_reference():
    # 非花括号 pattern = 字面文本（编译器口径），不是字段引用
    sym = {"id": "lyr1", "source": "pts", "type": "symbol",
           "layout": {"text-field": "站点"}}
    assert check_label_fields(_spec([sym]), _provider({"pts": set()})) == []


def test_source_not_evaluable_skips_honestly():
    # provider 返回 None（如瓦片/ref 源无法枚举字段）→ 不评估、不误报
    assert check_label_fields(_spec([SYM]), _provider({})) == []


def test_inline_source_field_set_union_bounded():
    data = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [0, 0]},
         "properties": {"a": 1}},
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [1, 1]},
         "properties": {"b": 2}},
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [2, 2]},
         "properties": None},
        "junk",
    ]}
    assert inline_source_field_set(data) == {"a", "b"}
    assert inline_source_field_set(None) is None


# ── 导出孪生接线（mapspec_to_svg 标签编译入口）─────────────────────────────

def _compile_spec(text_field):
    from app.services.mapspec_to_svg import compile_mapspec_to_svg_detailed

    spec = {
        "version": "1.1",
        "view": {"center": [116.0, 39.0], "zoom": 10},
        "sources": {"pts": {"type": "geojson", "inlineData": {"type": "FeatureCollection", "features": [
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [115.8, 38.8]},
             "properties": {}},
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [116.8, 39.6]},
             "properties": {"kind": "school"}},
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [116.0, 39.0]},
             "properties": {"name": "北京"}},
        ]}}},
        "layers": [{
            "id": "pts_sym", "source": "pts", "type": "symbol",
            "layout": {"text-field": text_field, "text-size": 12},
        }],
    }
    return compile_mapspec_to_svg_detailed(spec, target_dpi=72, width=400, height=300, padding=10)


def test_compile_missing_field_emits_diagnostic():
    result = _compile_spec("{pop}")
    hits = [d for d in result.diagnostics if d["code"] == LABEL_FIELD_MISSING]
    assert hits, result.diagnostics
    assert hits[0]["layer_id"] == "pts_sym"
    assert "pop" in hits[0]["detail"]
    # A name cannot stand in for the requested population field.
    assert "北京" not in result.svg
    assert "<text " not in result.svg


def test_compile_present_field_no_diagnostic():
    result = _compile_spec("{name}")
    assert not any(d["code"] == LABEL_FIELD_MISSING for d in result.diagnostics)
    assert "北京" in result.svg


@pytest.mark.parametrize("code", [LABEL_FIELD_MISSING])
def test_code_registered_in_render_diagnostics_vocabulary(code):
    from app.lib.cartography.render_diagnostics import RENDER_DIAGNOSTICS

    assert code in RENDER_DIAGNOSTICS
    assert RENDER_DIAGNOSTICS[code].severity == "warning"


def test_incomplete_field_sample_cannot_prove_a_late_field_missing():
    features = [
        {"type": "Feature", "properties": {"name": "point"},
         "geometry": {"type": "Point", "coordinates": [i / 10000, 0]}}
        for i in range(1001)
    ]
    features[-1]["properties"]["late_name"] = "present after sampling cap"
    source = {"type": "FeatureCollection", "features": features}
    assert inline_source_field_set(source) is None
    findings = check_label_fields({"layers": [{"id": "labels", "type": "symbol", "source": "data",
        "layout": {"text-field": "{late_name}"}}]}, lambda sid: inline_source_field_set(source))
    assert findings == []
    complete = inline_source_field_set(source, max_features=1001)
    assert "late_name" in complete


def test_compiler_late_field_does_not_emit_sampled_absence():
    from app.services.mapspec_to_svg import compile_mapspec_to_svg_detailed
    features = [{"type": "Feature", "geometry": {"type": "Point", "coordinates": [116 + i / 10000, 39]},
                 "properties": {"late_name": "late real label"} if i == 1000 else {}}
                for i in range(1001)]
    result = compile_mapspec_to_svg_detailed({"version": "1.1", "view": {"center": [116, 39], "zoom": 10},
        "sources": {"pts": {"type": "geojson", "inlineData": {"type": "FeatureCollection", "features": features}}},
        "layers": [{"id": "labels", "source": "pts", "type": "symbol", "layout": {"text-field": "{late_name}"}}]},
        target_dpi=72, width=600, height=400)
    assert not any(row["code"] == LABEL_FIELD_MISSING for row in result.diagnostics)
    assert "late real label" in result.svg
