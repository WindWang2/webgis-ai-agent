"""WP1 契约测试 —— DesignSpecV1 typed 校验 / 防泄漏 / digest / 引用校验。

负例纪律：每条红线至少一个可证伪的负例（fail-closed 而非静默修正）。
"""
from __future__ import annotations

from app.services.design_specs.contract import (
    DESIGN_SPEC_VERSION,
    LEAK_CREDENTIAL,
    LEAK_DATA_VALUES,
    LEAK_INLINE_DATA,
    LEAK_REF_CURSOR,
    canonical_json,
    structural_digest,
    validate_design_spec_payload,
)


def _base_payload(**overrides) -> dict:
    payload = {
        "spec_version": DESIGN_SPEC_VERSION,
        "name": "区域小学密度规范",
        "description": "区级专题图统一表达",
        "color": {
            "theme_id": "cartographic.light_interactive",
            "palette_sequential": "YlOrRd",
        },
        "classification": {
            "default_method": "quantiles",
            "k_min": 4,
            "k_max": 6,
            "shared_required": True,
            "no_data_handling": "exclude_disclosed",
        },
        "typography": {
            "font_resource": "noto-sans-cjk-sc",
            "title_size_pt": 18,
            "base_size_pt": 10,
        },
        "components": {
            "required": [
                {"component_type": "legend", "variant": "academic", "min_count": 1},
                {"component_type": "scale_bar"},
            ],
            "forbidden": ["north_arrow"],
        },
        "output": {
            "targets": ["interactive", "png", "pdf", "svg"],
            "paper_profile": "a4_landscape",
            "dpi": 300,
        },
        "data_roles": [
            {
                "name": "indicator",
                "value_type": "number",
                "measurement": "rate",
                "unit": "所/万人",
                "unit_required": True,
                "comparable_across_maps": True,
            },
            {"name": "region_key", "value_type": "text"},
        ],
        "overrides": {
            "user_overridable": ["title", "extent", "viewport"],
            "locked": ["unit_semantics", "classification_method"],
            "require_override_disclosure": True,
        },
        "lineage": {"source": "created"},
    }
    payload.update(overrides)
    return payload


class TestHappyPath:
    def test_valid_payload_passes(self):
        report = validate_design_spec_payload(_base_payload())
        assert report["ok"], report["violations"]
        assert report["spec"]["name"] == "区域小学密度规范"
        assert report["spec"]["spec_version"] == DESIGN_SPEC_VERSION

    def test_minimal_payload_passes(self):
        report = validate_design_spec_payload({"spec_version": DESIGN_SPEC_VERSION, "name": "minimal"})
        assert report["ok"], report["violations"]

    def test_digest_is_canonical_and_stable(self):
        a = _base_payload()
        b = {k: v for k, v in reversed(list(_base_payload().items()))}
        assert structural_digest(a) == structural_digest(b)
        assert structural_digest(a).startswith("dspec-sha256:")
        # 语义内容变化 → digest 变化
        a["description"] = "changed"
        assert structural_digest(a) != structural_digest(_base_payload())
        assert canonical_json({"b": 1, "a": 2}) == '{"a":2,"b":1}'


class TestSchemaFailures:
    def test_unknown_version_rejected(self):
        payload = _base_payload(spec_version="design_spec.v2")
        report = validate_design_spec_payload(payload)
        assert not report["ok"]
        assert any("spec_version" in v["field"] for v in report["violations"])

    def test_extra_field_rejected(self):
        payload = _base_payload()
        payload["totally_new_field"] = 1
        report = validate_design_spec_payload(payload)
        assert not report["ok"]

    def test_unknown_classification_method_rejected(self):
        payload = _base_payload()
        payload["classification"] = dict(payload["classification"], default_method="jenks_magic")
        report = validate_design_spec_payload(payload)
        assert not report["ok"]
        assert any("default_method" in v["field"] for v in report["violations"])

    def test_unknown_output_target_rejected(self):
        payload = _base_payload()
        payload["output"] = dict(payload["output"], targets=["hologram"])
        report = validate_design_spec_payload(payload)
        assert not report["ok"]

    def test_unknown_no_data_handling_rejected(self):
        payload = _base_payload()
        payload["classification"] = dict(payload["classification"], no_data_handling="drop_silently")
        report = validate_design_spec_payload(payload)
        assert not report["ok"]

    def test_k_min_gt_k_max_rejected(self):
        payload = _base_payload()
        payload["classification"] = dict(payload["classification"], k_min=8, k_max=4)
        report = validate_design_spec_payload(payload)
        assert not report["ok"]
        assert any(v["reason"] == "k_min_gt_k_max" for v in report["violations"])

    def test_duplicate_role_names_rejected(self):
        payload = _base_payload()
        payload["data_roles"] = [
            {"name": "indicator", "value_type": "number"},
            {"name": "indicator", "value_type": "text"},
        ]
        report = validate_design_spec_payload(payload)
        assert not report["ok"]

    def test_role_name_vocabulary_rejected(self):
        payload = _base_payload()
        payload["data_roles"] = [{"name": "Indicator-Value", "value_type": "number"}]
        report = validate_design_spec_payload(payload)
        assert not report["ok"]

    def test_unknown_locked_field_rejected(self):
        payload = _base_payload()
        payload["overrides"] = dict(payload["overrides"], locked=["mapspec_layers"])
        report = validate_design_spec_payload(payload)
        assert not report["ok"]

    def test_budget_limits_enforced(self):
        payload = _base_payload()
        payload["applicability"] = {"map_models": [f"model_{i}" for i in range(40)]}
        report = validate_design_spec_payload(payload)
        assert not report["ok"]
        payload2 = _base_payload()
        payload2["data_roles"] = [
            {"name": f"role_{i}", "value_type": "number"} for i in range(10)
        ]
        report2 = validate_design_spec_payload(payload2)
        assert not report2["ok"]


class TestReferenceValidation:
    def test_unknown_theme_rejected(self):
        payload = _base_payload()
        payload["color"] = dict(payload["color"], theme_id="cartographic.no_such")
        report = validate_design_spec_payload(payload)
        assert not report["ok"]
        assert any(v["reason"] == "unknown_theme" for v in report["violations"])

    def test_unknown_palette_rejected(self):
        payload = _base_payload()
        payload["color"] = dict(payload["color"], palette_sequential="NotAPalette")
        report = validate_design_spec_payload(payload)
        assert not report["ok"]
        assert any(v["reason"] == "unknown_palette" for v in report["violations"])

    def test_unknown_map_model_rejected(self):
        payload = _base_payload()
        payload["applicability"] = {"map_models": ["definitely_not_a_model"]}
        report = validate_design_spec_payload(payload)
        assert not report["ok"]
        assert any(v["reason"] == "unknown_map_model" for v in report["violations"])

    def test_planned_map_model_rejected(self):
        # planned 模型不得进入规范（native 化后才可引用）
        from app.lib.cartography.model_library import get_map_model_registry

        planned = get_map_model_registry().planned_ids()
        if not planned:
            return  # 词表无 planned 时跳过（不造假）
        payload = _base_payload()
        payload["applicability"] = {"map_models": [planned[0]]}
        report = validate_design_spec_payload(payload)
        assert any(v["reason"] == "map_model_planned" for v in report["violations"])

    def test_native_map_model_accepted(self):
        from app.lib.cartography.model_library import get_map_model_registry

        native = get_map_model_registry().native_ids()
        payload = _base_payload()
        payload["applicability"] = {"map_models": [native[0]]}
        report = validate_design_spec_payload(payload)
        assert report["ok"], report["violations"]

    def test_unknown_component_type_rejected(self):
        payload = _base_payload()
        payload["components"] = {"required": [{"component_type": "holo_deck"}]}
        report = validate_design_spec_payload(payload)
        assert not report["ok"]
        assert any(v["reason"] == "unknown_component_type" for v in report["violations"])

    def test_unknown_component_variant_rejected(self):
        payload = _base_payload()
        payload["components"] = {
            "required": [{"component_type": "legend", "variant": "holographic"}]
        }
        report = validate_design_spec_payload(payload)
        assert not report["ok"]
        assert any(v["reason"] == "unknown_component_variant" for v in report["violations"])

    def test_unknown_font_resource_rejected(self):
        payload = _base_payload()
        payload["typography"] = dict(payload["typography"], font_resource="comic_sans_ms")
        report = validate_design_spec_payload(payload)
        assert not report["ok"]
        assert any(v["reason"] == "unknown_font_resource" for v in report["violations"])

    def test_unknown_paper_profile_rejected(self):
        payload = _base_payload()
        payload["output"] = dict(payload["output"], paper_profile="banner_xl")
        report = validate_design_spec_payload(payload)
        assert not report["ok"]
        assert any(v["reason"] == "unknown_paper_profile" for v in report["violations"])

    def test_deprecated_component_warns_not_rejects(self):
        # deprecated 组件是 warning（迁移期兼容），不是 violation
        from app.lib.cartography.component_registry import get_component_registry

        registry = get_component_registry()
        deprecated = [
            cid for cid in registry.all_ids
            if getattr(registry.get(cid), "deprecated", False)
        ]
        if not deprecated:
            return  # 词表当前无 deprecated 组件（不造假；有则验证语义）
        payload = _base_payload()
        payload["components"] = {"required": [{"component_type": deprecated[0]}]}
        report = validate_design_spec_payload(payload)
        assert report["ok"], report["violations"]
        assert any(w["reason"] == "component_deprecated" for w in report["warnings"])


class TestAntiLeakDiscipline:
    """红线 2：数据值 / ref 游标 / 凭证 / 内联 GeoJSON 一律 fail-closed。"""

    def test_classification_breaks_rejected(self):
        payload = _base_payload()
        payload["classification_breaks"] = [0.0, 12.5, 40.1, 120.0]
        report = validate_design_spec_payload(payload)
        assert not report["ok"]
        assert any(v["reason"] == LEAK_DATA_VALUES for v in report["violations"])

    def test_stops_inside_nested_dict_rejected(self):
        payload = _base_payload()
        payload["color"]["stops"] = [10, 20, 30]
        report = validate_design_spec_payload(payload)
        assert not report["ok"]
        assert any(v["reason"] == LEAK_DATA_VALUES for v in report["violations"])

    def test_short_number_lists_allowed(self):
        # 1–2 个数值是合法排版参数（列数/字号），不误伤
        payload = _base_payload()
        payload["layout_margins"] = [12, 12]
        report = validate_design_spec_payload(payload)
        # 顶层 extra 键被 schema 拒绝，但不产生 LEAK_DATA_VALUES
        assert not any(v["reason"] == LEAK_DATA_VALUES for v in report["violations"])

    def test_ref_cursor_rejected(self):
        payload = _base_payload()
        payload["lineage"] = {"source": "created", "note": "ref:geojson-abc"}
        report = validate_design_spec_payload(payload)
        assert not report["ok"]
        assert any(v["reason"] == LEAK_REF_CURSOR for v in report["violations"])

    def test_credential_field_rejected(self):
        payload = _base_payload()
        payload["source_credentials"] = {"api_key": "sk-123"}
        report = validate_design_spec_payload(payload)
        assert not report["ok"]
        assert any(v["reason"] == LEAK_CREDENTIAL for v in report["violations"])

    def test_inline_geojson_rejected(self):
        payload = _base_payload()
        payload["extent_data"] = {"geojson": {"type": "FeatureCollection", "features": []}}
        report = validate_design_spec_payload(payload)
        assert any(v["reason"] == LEAK_INLINE_DATA for v in report["violations"])


class TestWarnings:
    def test_comparable_roles_without_shared_classification_warns(self):
        payload = _base_payload()
        payload["classification"] = dict(payload["classification"], shared_required=False)
        report = validate_design_spec_payload(payload)
        assert report["ok"], report["violations"]
        assert any(
            w["reason"] == "comparable_roles_without_shared_classification"
            for w in report["warnings"]
        )
