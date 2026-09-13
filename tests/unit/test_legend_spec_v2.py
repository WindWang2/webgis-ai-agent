"""AC-03 P6：legend_spec v2（ADR-0152）加字段 + v1→v2 升级路径 + JSON Schema
快照一致性。

v2 = v1（ADR-0078）+ 纯加字段：k / palette_id / clip_policy / why /
nodata_label / out_of_range_label / out_of_range。v1 字段语义零变化
（normalize_legend_spec 保持 v1 归一语义，升级走 upgrade_legend_spec_v2）。
"""
import json
from pathlib import Path

import pytest

from app.lib.cartography.thematic_spec import (
    build_categorical_spec,
    build_continuous_spec,
    build_graduated_spec,
    build_divergent_spec,
    normalize_legend_spec,
    upgrade_legend_spec_v2,
)

SCHEMA_PATH = Path(__file__).resolve().parents[2] / "docs/dev/ac-03-legend-spec-v2.schema.json"


def _fc(values, field="pop"):
    return {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {field: v},
         "geometry": {"type": "Polygon",
                      "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1]]]}}
        for v in values
    ]}


# ── v2 字段发射 ──────────────────────────────────────────────────────────────


def test_graduated_spec_carries_v2_fields():
    spec = build_graduated_spec(
        _fc([5.0, 3.0, 8.0, 2.0, 7.0, 1.0, 9.0, 4.0, 6.0, 2.5]), "pop",
        method="natural_breaks", k=5, palette="Blues")
    assert spec["k"] == len(spec["breaks"]) - 1
    assert spec["palette_id"] == "Blues"
    assert spec["clip_policy"] == "none"
    assert spec["nodata_label"] == "No data"
    assert "out_of_range" not in spec


def test_graduated_spec_adjudicated_when_args_omitted():
    spec = build_graduated_spec(
        _fc([1.0, 1.0, 2.0, 2.0, 3.0, 3.0, 4.0, 5.0, 6.0, 8.0, 10.0,
             13.0, 20.0, 40.0, 80.0, 160.0, 320.0, 640.0, 1280.0, 5000.0]), "pop")
    # 重尾 → head_tail（引擎裁决，不再 quantiles 硬编码）
    assert spec["method"] == "head_tail"
    assert spec["clip_policy"] == "head_tail"
    assert spec["why"]


def test_clip_p99_emits_out_of_range_entry():
    values = [float(i % 10 + 1) for i in range(1195)] + [100.0] * 5
    spec = build_graduated_spec(_fc(values), "pop")
    assert spec["clip_policy"] == "clip_p99"
    entry = spec["out_of_range"]
    assert entry["count"] == 5
    assert entry["color"] == spec["palette_colors"][-1]
    assert entry["upper"] == pytest.approx(spec["breaks"][-1])
    assert spec["out_of_range_label"]


def test_log_policy_breaks_back_in_original_domain():
    values = [1.0 + i * (100000.0 - 1.0) / 199 for i in range(200)]
    spec = build_graduated_spec(_fc(values), "pop")
    assert spec["clip_policy"] == "log"
    # breaks 单调且落在原值域内（log 空间分级后回原域）
    breaks = spec["breaks"]
    assert breaks == sorted(breaks)
    assert breaks[0] == pytest.approx(min(values))
    assert breaks[-1] == pytest.approx(max(values))


# ── v1 → v2 升级路径 ─────────────────────────────────────────────────────────


def test_upgrade_v1_v2_is_additive_and_semantics_preserving():
    v1 = {
        "type": "graduated",
        "field": "pop",
        "breaks": [1.0, 2.0, 3.0],
        "palette": "Blues",
        "palette_colors": ["#eff3ff", "#6baed6", "#08519c"],
        "method": "quantiles",
    }
    snapshot = json.loads(json.dumps(v1))
    v2 = upgrade_legend_spec_v2(v1)
    # v1 字段零变化
    for key, val in snapshot.items():
        assert v2[key] == val
    # v2 字段补齐
    assert v2["k"] == 2
    assert v2["palette_id"] == "Blues"
    assert v2["clip_policy"] == "none"
    assert v2["why"] == ""
    assert v2["nodata_label"] == "No data"


def test_upgrade_categorical_v1():
    v1 = {
        "type": "categorical",
        "field": "zone",
        "categories": [
            {"key": "A", "color": "#66c2a5", "label": "住宅"},
            {"key": "B", "color": "#fc8d62", "label": "商业"},
        ],
    }
    v2 = upgrade_legend_spec_v2(v1)
    assert v2["k"] == 2
    assert v2["clip_policy"] == "none"
    assert v2["nodata_label"] == "No data"


def test_upgrade_non_dict_returns_none():
    assert upgrade_legend_spec_v2(None) is None
    assert upgrade_legend_spec_v2("not-a-spec") is None


def test_normalize_legend_spec_unchanged_by_v2():
    """normalize 保持 v1 归一语义（不注入 v2 字段）。"""
    v1 = {"type": "graduated", "breaks": [3.0, 1.0, 2.0], "colors": ["#a", "#b"]}
    out = normalize_legend_spec(v1)
    assert out["breaks"] == [1.0, 2.0, 3.0]
    assert out["palette_colors"] == ["#a", "#b"]
    assert "clip_policy" not in out and "why" not in out


def test_print_context_transform_applies_to_emitted_colors():
    """§0.4 print 强制降饱和作用于**输出色**（不只是校验）。"""
    from app.lib.cartography.palettes import (
        grayscale_ramp_separation, print_desaturate)
    from app.lib.cartography.symbology import (
        SymbologyIntent, SymbologyProfile, resolve_symbology)

    values = [float(i) + 1 for i in range(20)]
    profile = SymbologyProfile(values=values)
    spec_screen = build_graduated_spec(
        _fc(values), "pop", decision=resolve_symbology(profile))
    spec_print = build_graduated_spec(
        _fc(values), "pop",
        decision=resolve_symbology(profile, SymbologyIntent(context="print")))
    assert spec_print["context"] == "print"
    # print 输出色 ≠ screen 输出色（变换真实作用），且仍灰度可分级
    assert spec_print["palette_colors"] != spec_screen["palette_colors"]
    assert grayscale_ramp_separation(spec_print["palette_colors"]) >= 0.06
    # 与独立执行的 print_desaturate 一致（screen 色为变换输入）
    assert spec_print["palette_colors"] == print_desaturate(
        spec_screen["palette_colors"])


def test_invalid_context_fails_loud_at_boundary():
    """fail-closed：非法上下文在 pydantic 边界即拒绝（Literal 校验）。"""
    import pytest as _pytest
    from pydantic import ValidationError as _VE
    from app.lib.cartography.symbology import SymbologyIntent

    with _pytest.raises(_VE):
        SymbologyIntent(context="holodeck")


# ── JSON Schema 快照 ─────────────────────────────────────────────────────────


def _minimal_validator(instance, schema, root=None):
    """极简 JSON Schema 结构校验（仓内无 jsonschema 依赖）：required /
    type / enum / discriminant 条件分支。"""
    root = root or schema
    if schema is True:
        return
    if schema is False or schema is None:
        raise AssertionError(f"schema forbids value: {instance!r}")
    if not isinstance(schema, dict):
        return
    if "enum" in schema:
        assert instance in schema["enum"], f"{instance!r} not in {schema['enum']}"
    t = schema.get("type")
    checks = {
        "object": lambda v: isinstance(v, dict),
        "array": lambda v: isinstance(v, list),
        "string": lambda v: isinstance(v, str),
        "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
        "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
        "null": lambda v: v is None,
    }
    if t in checks and instance is not None:
        assert checks[t](instance), f"type {t} expected, got {type(instance).__name__}"
    if isinstance(instance, dict):
        for req in schema.get("required", []):
            assert req in instance, f"missing required key {req!r} in {sorted(instance)}"
        for key, sub in (schema.get("properties") or {}).items():
            if key in instance:
                _minimal_validator(instance[key], sub, root)
        for sub in schema.get("allOf", []):
            _minimal_validator(instance, sub, root)
        cond, then = schema.get("if"), schema.get("then")
        if cond and then:
            props = cond.get("properties", {})
            if all(instance.get(k) == spec.get("const") for k, spec in props.items()):
                _minimal_validator(instance, then, root)


def test_emitted_specs_conform_to_frozen_schema():
    """三类发射口（graduated/continuous/categorical）+ 升级后的 divergent
    都符合冻结的 v2 schema（docs/dev/ac-03-legend-spec-v2.schema.json）。"""
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))

    graduated = build_graduated_spec(
        _fc([float(i) + 1 for i in range(20)]), "pop",
        method="natural_breaks", k=5, palette="YlOrRd")
    continuous = build_continuous_spec(0.0, 10.0, "Blues", field="dens")
    categorical = build_categorical_spec("zone", [
        {"key": "A", "color": "#66c2a5", "label": "住宅"},
        {"key": "B", "color": "#fc8d62", "label": "商业"},
    ])
    divergent = upgrade_legend_spec_v2(
        build_divergent_spec([-3.0, -1.0, 0.0, 2.0, 4.0], "anom", 0.0, "RdBu"))

    for spec in (graduated, continuous, categorical, divergent):
        assert spec is not None
        _minimal_validator(spec, schema)

    # clip 场景的 out_of_range 形状
    values = [float(i % 10 + 1) for i in range(1195)] + [100.0] * 5
    clipped = build_graduated_spec(_fc(values), "pop")
    _minimal_validator(clipped, schema)
    assert "out_of_range" in clipped

    # schema 快照自身是合法 JSON 且 v2 字段全部登记
    for f in ("k", "palette_id", "clip_policy", "why", "nodata_label",
              "out_of_range_label", "out_of_range"):
        assert f in schema["properties"], f


def test_v2_field_tuple_matches_frozen_schema():
    """LEGEND_SPEC_V2_FIELDS 必须与冻结 schema 中 description 以 "v2:" 标记的
    字段全集一致（P3 审查修复：此前漏 context / out_of_range）。"""
    from app.lib.cartography.thematic_spec import LEGEND_SPEC_V2_FIELDS

    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    v2_marked = sorted(
        f for f, sub in schema["properties"].items()
        if (sub.get("description") or "").startswith("v2:")
    )
    assert sorted(LEGEND_SPEC_V2_FIELDS) == v2_marked
