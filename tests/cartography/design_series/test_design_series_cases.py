"""WP6 案例库测试 —— 五类案例的 ground truth 值级对账 + 负例控制。

规范经真实校验/落库；分类用生产 classify 算法对账；推荐负例必须被
拒绝或澄清；组件冲突只报不删。
"""
from __future__ import annotations

import pytest

from tests.cartography.design_series.cases import (
    case_ids,
    get_case,
)

pytestmark = pytest.mark.cartography


class TestCaseLibraryIntegrity:
    def test_five_cases_present(self):
        assert len(case_ids()) >= 5
        expected = {
            "facility_density", "district_comparison", "temporal_change",
            "flow_od", "composition_sheet",
        }
        assert expected <= set(case_ids())

    @pytest.mark.parametrize("cid", case_ids())
    def test_spec_payload_validates_and_persists(self, cid):
        from app.core.database import Base, Engine, SessionLocal
        from app.models.design_spec import CartographyDesignSpec, CartographyDesignSpecVersion
        from app.services.design_specs.contract import validate_design_spec_payload
        from app.services.design_specs.versioning import create_spec

        case = get_case(cid)
        assert case["simulated"] is True
        report = validate_design_spec_payload(case["spec_payload"])
        assert report["ok"], (cid, report["violations"])

        Base.metadata.create_all(bind=Engine)
        try:
            with SessionLocal() as db:
                spec = create_spec(db, dict(case["spec_payload"], name=f"{case['spec_payload']['name']}·{cid}"),
                                   creator_id="cases-user")
                assert spec.current_version == 1
        finally:
            with SessionLocal() as db:
                db.query(CartographyDesignSpecVersion).delete()
                db.query(CartographyDesignSpec).delete()
                db.commit()


class TestCase1FacilityDensity:
    def test_ground_truth_density_values(self):
        case = get_case("facility_density")
        truth = case["ground_truth"]["density_per_district"]
        features = case["fixtures"]["city_a"]["features"]
        for feat in features:
            props = feat["properties"]
            expected = truth[props["name"]]
            actual = round(props["schools"] / props["population_wan"], 6)
            assert actual == expected

    def test_shared_classification_union_breaks(self):
        from app.lib.cartography.classify import classify_values

        case = get_case("facility_density")
        truth = case["ground_truth"]["density_per_district"]
        values = sorted(truth.values())
        breaks = classify_values(values, method="quantiles", k=3)
        # 断点单调且覆盖数据域（k=3 类；边界数随 classify 约定 k 或 k+1）
        assert 3 <= len(breaks) <= 4
        assert all(isinstance(b, float) for b in breaks)
        assert all(breaks[i] <= breaks[i + 1] for i in range(len(breaks) - 1))
        assert breaks[0] <= min(values) and max(values) <= breaks[-1]

    def test_missing_denominator_negative(self):
        from app.services.design_specs.recommend import recommend_design

        case = get_case("facility_density")
        for neg in case["negatives"]:
            result = recommend_design(neg["facts"])
            codes = [c["code"] for c in result["clarifications"]]
            assert neg["expect"]["clarification"] in codes


class TestCase2DistrictComparison:
    def test_same_unit_bindings_pass(self, monkeypatch):
        from app.services.design_specs.binding import validate_role_bindings

        case = get_case("district_comparison")
        spec = case["spec_payload"]

        def _doc(field: str, unit: str = "所"):
            return {
                "version": "1.1",
                "sources": {"src": {"type": "geojson", "profile": {
                    "fields": {
                        field: {"type": "number", "stats": {"min": 4.0, "max": 15.0, "mean": 8.0}},
                        "population_wan": {"type": "number", "stats": {"min": 20.0, "max": 70.0, "mean": 40.0}},
                    },
                }}},
                "layers": [{
                    "id": "thematic", "source": "src", "type": "fill",
                    "legend_spec": {"type": "graduated", "field": field,
                                    "unit": unit, "method": "quantiles"},
                    "data_binding": {"field": field, "field_type": "number"},
                }],
            }

        docs = {"city_a": _doc("primaries"), "city_b": _doc("schools_total")}
        captured = {}

        async def fake_get_mapspec(session_id):
            return docs[session_id]

        monkeypatch.setattr(
            "app.services.mapspec_store.mapspec_store.get_mapspec", fake_get_mapspec)

        import asyncio

        result = asyncio.get_event_loop().run_until_complete(validate_role_bindings(
            [
                {"role": "indicator", "session_id": "city_a",
                 "layer_id": "thematic", "field": "primaries"},
                {"role": "indicator", "session_id": "city_b",
                 "layer_id": "thematic", "field": "schools_total"},
            ],
            spec,
        ))
        assert result["ok"], result["results"]
        assert all(r["status"] == "bound" for r in result["results"])
        _ = captured

    def test_unit_conflict_negative_detected(self, monkeypatch):
        from app.services.design_specs.binding import UNIT_MISMATCH, validate_role_bindings

        case = get_case("district_comparison")
        spec = case["spec_payload"]

        def _doc(field: str, unit: str):
            return {
                "version": "1.1",
                "sources": {"src": {"type": "geojson", "profile": {
                    "fields": {field: {"type": "number",
                                       "stats": {"min": 4.0, "max": 15.0, "mean": 8.0}}},
                }}},
                "layers": [{
                    "id": "thematic", "source": "src", "type": "fill",
                    "legend_spec": {"type": "graduated", "field": field,
                                    "unit": unit, "method": "quantiles"},
                }],
            }

        docs = {"city_a": _doc("primaries", "所"),
                "city_b": _doc("schools_total", "间")}  # 单位冲突

        async def fake_get_mapspec(session_id):
            return docs[session_id]

        monkeypatch.setattr(
            "app.services.mapspec_store.mapspec_store.get_mapspec", fake_get_mapspec)
        import asyncio

        result = asyncio.get_event_loop().run_until_complete(validate_role_bindings(
            [
                {"role": "indicator", "session_id": "city_a",
                 "layer_id": "thematic", "field": "primaries"},
                {"role": "indicator", "session_id": "city_b",
                 "layer_id": "thematic", "field": "schools_total"},
            ],
            spec,
        ))
        codes = [r["code"] for r in result["results"]]
        assert UNIT_MISMATCH in codes
        assert result["comparability_findings"]


class TestCase3TemporalChange:
    def test_delta_ground_truth(self):
        case = get_case("temporal_change")
        truth = case["ground_truth"]["delta_per_district"]
        f2020 = {f["properties"]["name"]: f["properties"]["schools_2020"]
                 for f in case["fixtures"]["y2020"]["features"]}
        f2025 = {f["properties"]["name"]: f["properties"]["schools_2025"]
                 for f in case["fixtures"]["y2025"]["features"]}
        for name, delta in truth.items():
            assert f2025[name] - f2020[name] == delta

    def test_independent_breaks_must_be_disclosed(self):
        # 共享表达契约：shared_required=False 时 comparable 角色必须触发
        # 规范层 warning（规范自身都过不了 '比较' 语义）
        from app.services.design_specs.contract import validate_design_spec_payload

        case = dict(get_case("temporal_change"))
        payload = dict(case["spec_payload"])
        payload["classification"] = dict(payload["classification"], shared_required=False)
        report = validate_design_spec_payload(payload)
        assert report["ok"]  # 不拒绝，但必须披露
        assert any(
            w["reason"] == "comparable_roles_without_shared_classification"
            for w in report["warnings"]
        )


class TestCase4FlowOD:
    def test_od_ground_truth_math(self):
        case = get_case("flow_od")
        truth = case["ground_truth"]
        rows = [f["properties"] for f in case["fixtures"]["od_table"]["features"]]
        total = sum(r["flow"] for r in rows)
        assert total == truth["total_flow"]
        top = max(rows, key=lambda r: r["flow"])
        assert top["origin"] == truth["top_flow"]["origin"]
        assert top["destination"] == truth["top_flow"]["destination"]
        for name, net in truth["netflow"].items():
            calc = (sum(r["flow"] for r in rows if r["origin"] == name)
                    - sum(r["flow"] for r in rows if r["destination"] == name))
            assert calc == net

    def test_g21_flow_tool_available(self):
        # G21 已实现 —— 引用而非重做：确认工具面在场
        from app.tools import init_tools
        from app.tools.registry import ToolRegistry

        registry = ToolRegistry()
        init_tools(registry)
        schemas = {s.get("function", {}).get("name") for s in registry.get_schemas()}
        assert "od_flow_edges" in schemas


class TestCase5CompositionSheet:
    def test_apply_plan_ensures_all_requirements(self):
        from app.services.design_specs.apply import build_apply_plan

        case = get_case("composition_sheet")
        document = {
            "version": "1.1",
            "sources": {},
            "layers": [{
                "id": "thematic", "source": "src", "type": "fill",
                "legend_spec": {"type": "graduated", "field": "v",
                                "palette": "Blues", "method": "quantiles"},
            }],
            "layout": {"components": [
                {"id": "t", "type": "title"},
                {"id": "lg", "type": "legend"},
                {"id": "sb", "type": "scale_bar"},
                {"id": "at", "type": "attribution"},
                {"id": "na", "type": "north_arrow"},  # 触发禁用冲突负例
            ]},
        }
        plan = build_apply_plan(case["spec_payload"], document)
        ensured = {e["component_type"] for e in plan["component_ensures"]}
        assert ensured == set(), f"四要件都在场，不应有 ensure：{ensured}"
        conflicts = [c for c in plan["conflicts"]
                     if c["kind"] == "forbidden_component_present"]
        assert conflicts, "禁用组件在场必须报冲突"
        assert all("不自动移除" in c["message"] for c in conflicts)

    def test_missing_requirements_are_ensured(self):
        from app.services.design_specs.apply import build_apply_plan

        case = get_case("composition_sheet")
        document = {
            "version": "1.1",
            "sources": {},
            "layers": [{
                "id": "thematic", "source": "src", "type": "fill",
                "legend_spec": {"type": "graduated", "field": "v"},
            }],
            "layout": {"components": []},
        }
        plan = build_apply_plan(case["spec_payload"], document)
        ensured = {e["component_type"] for e in plan["component_ensures"]}
        assert {"title", "legend", "scale_bar", "attribution"} <= ensured


class TestCaseExtractRoundtrip:
    """验收 A1：从真实地图（案例 fixture）提取规范草案并落库。"""

    def test_extract_from_case_document(self):
        from app.services.design_specs.contract import validate_design_spec_payload
        from app.services.design_specs.extract import extract_design_spec_draft_sync

        case = get_case("facility_density")
        document = {
            "version": "1.1",
            "sources": {"src": {"type": "geojson"}},
            "layers": [{
                "id": "thematic", "source": "src", "type": "fill",
                "legend_spec": {"type": "graduated", "field": "density",
                                "method": "quantile", "unit": "所/万人",
                                "colors": ["#ffffb2", "#feb24c", "#bd0026"],
                                "classes": 3},
                "data_binding": {"field": "density", "field_type": "number"},
            }],
            "layout": {"components": [
                {"id": "t", "type": "title"},
                {"id": "lg", "type": "legend"},
            ]},
        }
        result = extract_design_spec_draft_sync(document, session_id="sess-case")
        draft = result["draft_payload"]
        assert draft["classification"]["default_method"] == "quantiles"
        assert draft["data_roles"][0]["unit"] == "所/万人"
        # 草案必须通过规范校验（改合法名后）
        draft["name"] = "案例提取规范"
        report = validate_design_spec_payload(draft)
        assert report["ok"], report["violations"]
