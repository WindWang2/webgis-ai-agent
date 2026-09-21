"""Grammar Constraint Solver 测试（C2，ADR-0204）。

覆盖：signed→diverging 端到端（过 resolve_symbology 验证色带族）、
count vs rate、legend↔colorbar 配对、密集/低 N 点表达切换、类别收纳、
表达词表对齐 MapModel 注册表、user-wins pin、audit 只读对账、确定性
与指纹。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

pytestmark = pytest.mark.cartography

from app.lib.cartography.grammar_solver import (  # noqa: E402
    FieldEvidence,
    GrammarRequest,
    solve_grammar,
)
from app.lib.cartography.grammar_types import (  # noqa: E402
    GRAMMAR_VERSION,
    MAX_THEMATIC_FIELDS,
    REPRESENTATION_MODEL_IDS,
)
from app.lib.cartography.model_library import get_map_model  # noqa: E402
from app.lib.cartography.semantic_checks import (  # noqa: E402
    _VISUALVAR_FAIL_COUNT,
    _VISUALVAR_WARN_COUNT,
)
from app.lib.cartography.symbology import (  # noqa: E402
    symbology_decision_from_values,
)


def _polygon_request(field: str, values: list[float], **kw) -> GrammarRequest:
    return GrammarRequest(
        geometry="polygon", feature_count=len(values) or 50, zoom=9.0,
        fields=[FieldEvidence(name=field, dtype="float", values=values)], **kw
    )


class TestSignedDiverging:
    def test_data_kind_derived_diverging(self):
        d = solve_grammar(_polygon_request(
            "net_migration", [-500.0] * 10 + [300.0] * 10))
        assert d.data_kind == "diverging"
        assert d.bindings[0].measurement.kind == "signed_change"
        assert "GRAMMAR.REP.SIGNED_DIVERGING" in d.reason_codes
        assert d.legend.form == "divergent"

    def test_end_to_end_diverging_family_via_resolve_symbology(self):
        """修正的端到端证据：grammar data_kind → resolve_symbology 选
        diverging 族色带（RdBu/PuOr/RdYlGn）；缺省 sequential 则选不出。"""
        values = [-500.0, -300.0, -100.0, -50.0, 10.0, 80.0, 200.0, 400.0,
                  600.0, 900.0, -800.0, -200.0, 30.0, 150.0, 250.0, 350.0]
        d = solve_grammar(_polygon_request("net_migration", values))
        decided = symbology_decision_from_values(
            values, data_kind=d.data_kind)
        assert decided.palette in ("RdBu", "PuOr", "RdYlGn")
        legacy = symbology_decision_from_values(values)
        assert legacy.palette not in ("RdBu", "PuOr", "RdYlGn")

    def test_symbology_inputs_shape(self):
        d = solve_grammar(_polygon_request(
            "net_migration", [-1.0] * 8 + [1.0] * 8))
        assert d.symbology_inputs["data_kind"] == "diverging"
        assert d.symbology_inputs["origin"].startswith(f"grammar@{GRAMMAR_VERSION}")


class TestCountVsRate:
    def test_rate_normalized_disclosure(self):
        d = solve_grammar(_polygon_request(
            "pop_density_per_km2", [5.0, 10.0, 20.0, 40.0, 8.0, 3.0]))
        assert d.representation.selected == "administrative_choropleth"
        assert "GRAMMAR.REP.RATE_NORMALIZED" in d.representation.reason_codes
        assert any("归一化" in x for x in d.disclosures)

    def test_count_on_polygon_advisory(self):
        d = solve_grammar(_polygon_request(
            "building_count", [10.0, 20.0, 30.0, 40.0, 25.0, 5.0]))
        assert "GRAMMAR.REP.COUNT_VS_RATE_ADVISORY" in d.representation.reason_codes


class TestLegendPairing:
    def test_qualitative_gets_categorical_legend(self):
        req = GrammarRequest(
            geometry="polygon", zoom=9.0,
            fields=[FieldEvidence(name="landuse_type", dtype="string",
                                  values=[], unique_count=5)])
        d = solve_grammar(req)
        assert d.legend.form == "categorical"
        assert d.data_kind == "qualitative"

    def test_cyclic_graduated_disclosed(self):
        req = GrammarRequest(
            geometry="polygon", zoom=9.0,
            fields=[FieldEvidence(name="birth_month", dtype="int",
                                  values=[1.0, 4.0, 7.0])])
        d = solve_grammar(req)
        assert d.legend.form == "graduated"
        assert "GRAMMAR.PAIR.CYCLIC_GRADUATED_DISCLOSURE" in d.legend.reason_codes


class TestPointRepresentation:
    def test_dense_points_switch_to_heatmap_at_world(self):
        req = GrammarRequest(
            geometry="point", feature_count=5000, zoom=6.0,
            fields=[FieldEvidence(name="pm25", dtype="float",
                                  values=[10.0, 20.0, 30.0])])
        d = solve_grammar(req)
        assert d.scale.is_dense_points
        assert d.scale.tier == "world"
        assert d.representation.selected == "visual_heatmap"
        assert "GRAMMAR.REP.DENSE_POINTS_AGGREGATE" in d.representation.reason_codes

    def test_low_n_symbol_map(self):
        req = GrammarRequest(
            geometry="point", feature_count=30, zoom=15.0,
            fields=[FieldEvidence(name="well_depth", dtype="float",
                                  values=[10.0, 20.0, 30.0])])
        d = solve_grammar(req)
        assert not d.scale.is_dense_points
        assert d.representation.selected == "proportional_symbol"

    def test_dense_nominal_rejects_heatmap(self):
        req = GrammarRequest(
            geometry="point", feature_count=5000, zoom=6.0,
            fields=[FieldEvidence(name="poi_category", dtype="string",
                                  values=[], unique_count=6)])
        d = solve_grammar(req)
        assert d.representation.selected != "visual_heatmap"
        assert any(r["reason_code"] == "GRAMMAR.REP.HEATMAP_NOMINAL"
                   for r in d.representation.rejected)

    def test_insufficient_evidence_disclosed(self):
        req = GrammarRequest(
            geometry="point", feature_count=5, zoom=15.0,
            fields=[FieldEvidence(name="depth", dtype="float",
                                  values=[1.0, 2.0])])
        d = solve_grammar(req)
        assert any("证据不足" in x for x in d.disclosures)


class TestExcessiveCategories:
    def test_collapse_top_n_plus_other(self):
        req = GrammarRequest(
            geometry="polygon", zoom=9.0,
            fields=[FieldEvidence(name="landuse_type", dtype="string",
                                  values=[], unique_count=23)])
        d = solve_grammar(req)
        assert d.representation.collapse is not None
        assert d.representation.collapse["keep_classes"] == 7
        assert d.representation.collapse["other_label"] == "Other"
        assert "GRAMMAR.REP.TOO_MANY_CATEGORIES" in d.representation.reason_codes
        assert any(r["reason_code"] == "GRAMMAR.REP.TOO_MANY_CATEGORIES"
                   for r in d.representation.rejected)

    def test_within_capacity_no_collapse(self):
        req = GrammarRequest(
            geometry="polygon", zoom=9.0,
            fields=[FieldEvidence(name="landuse_type", dtype="string",
                                  values=[], unique_count=6)])
        d = solve_grammar(req)
        assert d.representation.collapse is None
        assert d.representation.selected == "categorical_thematic"


class TestVocabularyAlignment:
    @pytest.mark.parametrize("model_id", REPRESENTATION_MODEL_IDS)
    def test_representation_ids_resolvable(self, model_id: str):
        assert get_map_model(model_id) is not None, model_id

    def test_thematic_field_bound_matches_semantic_checks_fail_threshold(self):
        # 有界请求下限 = carto.visualvar.overload 的 fail 阈值（同源不漂移）
        assert MAX_THEMATIC_FIELDS >= _VISUALVAR_FAIL_COUNT
        assert _VISUALVAR_WARN_COUNT < _VISUALVAR_FAIL_COUNT


class TestUserWins:
    def test_quantitative_primary_binds_declared_first_channel(self):
        """评审修正回归：quantitative 主通道应绑声明首位的 position，
        而非字典序重排后的 lightness。"""
        d = solve_grammar(_polygon_request(
            "median_income", [40000.0, 50000.0, 60000.0, 45000.0, 52000.0,
                              48000.0, 55000.0, 47000.0]))
        assert d.bindings[0].measurement.kind == "quantitative"
        assert d.bindings[0].variable == "position"

    def test_pinned_channel_recorded_and_conflict_disclosed(self):
        req = GrammarRequest(
            geometry="polygon", zoom=9.0,
            fields=[FieldEvidence(name="landuse_type", dtype="string",
                                  values=[], unique_count=5)],
            pinned_channels={"landuse_type": "size"})
        d = solve_grammar(req)
        assert d.bindings[0].variable == "size"  # 尊重用户
        assert any(u["kind"] == "channel" for u in d.user_wins)
        assert "GRAMMAR.PIN.CHANNEL_CONFLICT" in d.bindings[0].reason_codes
        assert any("冲突" in x for x in d.disclosures)

    def test_pinned_palette_flows_to_symbology_inputs(self):
        d = solve_grammar(GrammarRequest(
            geometry="polygon", zoom=9.0,
            fields=[FieldEvidence(name="v", dtype="float", values=[1.0, 2.0])],
            pinned_palette="Blues"))
        assert d.symbology_inputs["recommended_palette"] == "Blues"
        assert any(u["kind"] == "palette" for u in d.user_wins)

    def test_pinned_representation_unknown_id_fail_closed(self):
        with pytest.raises(ValueError):
            solve_grammar(GrammarRequest(
                geometry="polygon", zoom=9.0,
                fields=[FieldEvidence(name="v", dtype="float", values=[1.0])],
                pinned_representation="not_a_model"))

    def test_vocab_fail_closed(self):
        with pytest.raises(ValueError):
            solve_grammar(GrammarRequest(purpose="bogus"))
        with pytest.raises(ValidationError):
            GrammarRequest(output_purpose="bogus")
        with pytest.raises(ValidationError):
            GrammarRequest(pinned_channels={"f": "bogus"})
        with pytest.raises(ValidationError):
            GrammarRequest(fields=[
                FieldEvidence(name=f"f{i}", dtype="float", values=[1.0])
                for i in range(MAX_THEMATIC_FIELDS + 1)])
        with pytest.raises(ValidationError):
            GrammarRequest(fields=[
                FieldEvidence(name="f0", dtype="float", values=[1.0],
                              measurement="bogus")])


class TestAudit:
    def _decision(self):
        return solve_grammar(GrammarRequest(
            geometry="polygon", zoom=9.0,
            fields=[FieldEvidence(name="net_migration", dtype="float",
                                  values=[-5.0] * 6 + [5.0] * 6)]))

    def test_pairing_finding(self):
        d = self._decision()
        assert d.data_kind == "diverging"
        audit = d.audit([{"id": "l1", "type": "fill",
                          "legend_spec": {"type": "categorical"}}])
        codes = [f.code for f in audit.findings]
        assert "GRAMMAR.AUDIT.PAIRING" in codes
        assert audit.has_blocking_risk  # warn 级发现可见（但非 verdict）

    def test_channel_load_aligned_with_semantic_checks(self):
        d = self._decision()
        overload_paint = {
            "fill-color": {"method": "interpolate", "field": "a", "stops": []},
            "fill-opacity": {"method": "interpolate", "field": "b", "stops": []},
            "circle-radius": {"method": "interpolate", "field": "c", "stops": []},
            "circle-stroke-width": {"method": "interpolate", "field": "d", "stops": []},
        }
        audit = d.audit([{"id": "l1", "type": "fill", "paint": overload_paint}])
        assert any(f.code == "GRAMMAR.AUDIT.CHANNEL_LOAD"
                   and f.severity == "warning" for f in audit.findings)

    def test_no_second_verdict_shape(self):
        d = self._decision()
        audit = d.audit([{"id": "l1", "type": "fill",
                          "legend_spec": {"type": "divergent"}}])
        dump = audit.model_dump()
        assert set(dump) == {"decision_fingerprint", "grammar_version",
                             "evaluated", "findings"}
        assert "status" not in dump and "verdict" not in dump


class TestDeterminism:
    def test_same_input_same_dump_and_fingerprint(self):
        req = GrammarRequest(
            geometry="point", feature_count=3000, zoom=6.0,
            fields=[FieldEvidence(name="pm25", dtype="float",
                                  values=[1.0, 2.0, 3.0])])
        a = solve_grammar(req).model_dump()
        b = solve_grammar(req).model_dump()
        assert a == b

    def test_different_input_different_fingerprint(self):
        a = solve_grammar(GrammarRequest(
            geometry="polygon", zoom=9.0,
            fields=[FieldEvidence(name="v", dtype="float", values=[1.0])]))
        b = solve_grammar(GrammarRequest(
            geometry="polygon", zoom=12.0,
            fields=[FieldEvidence(name="v", dtype="float", values=[1.0])]))
        assert a.fingerprint != b.fingerprint
