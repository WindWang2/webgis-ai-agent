"""Grammar 生产接线契约测试（F10 M1/M3/M4，design D2/D3/D4）.

锁定：create_thematic_map 统一语义面（#1480 契约键形兼容 + 新工件）、
grammar_decision 工件随结果下发、grammar_propagation 收集/组合审计/
review 消费、user palette pin 记账。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

pytestmark = pytest.mark.cartography

from app.lib.cartography.grammar_propagation import (  # noqa: E402
    GRAMMAR_LAYER_KEY,
    attach_grammar_decision,
    collect_grammar_decisions,
    decision_payload,
    grammar_auditor_for_mapspec,
)
from app.lib.cartography.grammar_solver import (  # noqa: E402
    GRAMMAR_VERSION,
    GrammarRequest,
    solve_grammar,
)
from app.lib.cartography.quality_loop import review_cartography  # noqa: E402
from app.tools.cartography import register_cartography_tools  # noqa: E402
from app.tools.registry import ToolRegistry  # noqa: E402


@pytest.fixture
def registry():
    r = ToolRegistry()
    register_cartography_tools(r)
    return r


def _polygon_fc(field: str, values: list) -> dict:
    return {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature",
             "geometry": {"type": "Polygon",
                          "coordinates": [[[i, 0], [i + 1, 0], [i + 1, 1],
                                           [i, 1], [i, 0]]]},
             "properties": {field: v}}
            for i, v in enumerate(values)
        ],
    }


SIGNED = [-500.0, -300.0, -100.0, -50.0, 10.0, 80.0,
          200.0, 400.0, 600.0, 900.0, -800.0, -200.0]
POSITIVE = [100.0, 300.0, 500.0, 800.0, 1200.0, 2000.0,
            3000.0, 500.0, 700.0, 900.0, 1500.0, 2500.0]


class TestCreateThematicMapUnified:
    @pytest.mark.asyncio
    async def test_signed_semantics_end_to_end(self, registry):
        gj = _polygon_fc("net_migration", SIGNED)
        out = await registry.dispatch("create_thematic_map",
                                      {"geojson": gj, "field": "net_migration"})
        assert "error" not in out, out.get("error")
        plan = out["classification_plan"]
        assert plan["data_kind"] == "diverging"
        # #1480 契约键形兼容：grammar.kind / grammar.source
        assert plan["grammar"]["kind"] == "signed_change"
        assert plan["grammar"]["source"] == "dataset_contract"
        # 新工件：semantic_inputs 全量证据
        assert plan["semantic_inputs"]["measurement_kind"] == "signed_change"
        assert plan["semantic_inputs"]["data_kind"] == "diverging"
        assert out["symbology_decision"]["palette"] in ("RdBu", "PuOr", "RdYlGn")

    @pytest.mark.asyncio
    async def test_sequential_zero_drift(self, registry):
        gj = _polygon_fc("population", POSITIVE)
        out = await registry.dispatch("create_thematic_map",
                                      {"geojson": gj, "field": "population"})
        assert "error" not in out
        plan = out["classification_plan"]
        assert plan["data_kind"] == "sequential"
        assert plan["grammar"]["kind"] == "ratio"  # population 词素 → ratio
        assert out["symbology_decision"]["palette"] in (
            "YlOrRd", "Blues", "Greens", "Oranges", "Purples", "Reds",
            "Viridis", "Magma", "Inferno", "Plasma")

    @pytest.mark.asyncio
    async def test_grammar_decision_payload_attached(self, registry):
        gj = _polygon_fc("net_migration", SIGNED)
        out = await registry.dispatch("create_thematic_map",
                                      {"geojson": gj, "field": "net_migration"})
        payload = out.get("grammar_decision")
        assert isinstance(payload, dict)
        assert payload["grammar_version"] == GRAMMAR_VERSION
        assert payload["consumable"] is True
        assert payload["fingerprint"]
        assert payload["data_kind"] == "diverging"
        assert payload["geometry"] == "polygon"
        assert payload["bindings"][0]["field"] == "net_migration"
        assert "GRAMMAR.REP.SIGNED_DIVERGING" in payload["reason_codes"]
        # classification_plan 指纹引用
        assert (out["classification_plan"]["semantic_inputs"] is not None)

    @pytest.mark.asyncio
    async def test_layer_meta_carries_scale_hints_and_checks(self, registry):
        gj = _polygon_fc("net_migration", SIGNED)
        out = await registry.dispatch("create_thematic_map",
                                      {"geojson": gj, "field": "net_migration"})
        meta = out.get("layer_meta") or {}
        assert meta.get("scale_visibility_hints"), meta
        assert meta.get("scale_tier") in ("world", "province", "city", "street")

    @pytest.mark.asyncio
    async def test_semantic_disclosure_reaches_layer_meta(self, registry):
        # 率名 × 双符号样本：适配层带符号率升级的披露随 layer_meta 下发
        #（no silent misleading map——测量语义与表达不一致处必须可见）。
        signed_rates = [-0.10, 0.20, 0.30, -0.40, 0.10, -0.05,
                        0.25, 0.05, -0.30, 0.15, -0.02, 0.08]
        gj = _polygon_fc("success_rate", signed_rates)
        out = await registry.dispatch("create_thematic_map",
                                      {"geojson": gj, "field": "success_rate"})
        assert "error" not in out, out.get("error")
        meta = out.get("layer_meta") or {}
        checks = meta.get("measurement_checks") or []
        codes = {str(c.get("code")) for c in checks}
        assert any("GRAMMAR" in c for c in codes), codes
        assert "GRAMMAR_SEMANTIC_DISCLOSURE" in codes

    @pytest.mark.asyncio
    async def test_user_palette_pin_recorded_in_decision(self, registry):
        gj = _polygon_fc("population", POSITIVE)
        out = await registry.dispatch("create_thematic_map",
                                      {"geojson": gj, "field": "population",
                                       "palette": "Blues"})
        payload = out.get("grammar_decision") or {}
        pins = [w for w in payload.get("user_wins", [])
                if w.get("kind") == "palette"]
        assert pins and pins[0]["value"] == "Blues"

    @pytest.mark.asyncio
    async def test_explicit_method_no_grammar_decision(self, registry):
        gj = _polygon_fc("population", POSITIVE)
        out = await registry.dispatch("create_thematic_map",
                                      {"geojson": gj, "field": "population",
                                       "method": "quantiles"})
        assert "error" not in out
        # 显式 method：grammar 不越权（无 auto 分支工件），分类法仍由引擎。
        assert "grammar_decision" not in out


class TestGrammarPropagation:
    def _decision_for(self, field: str, values: list):
        from app.lib.cartography.grammar_solver import FieldEvidence
        req = GrammarRequest(
            geometry="polygon", feature_count=len(values),
            fields=[FieldEvidence(name=field, dtype="float", values=values)],
        )
        return solve_grammar(req)

    def test_attach_collect_roundtrip(self):
        d = self._decision_for("f", [1.0, 2.0, 3.0])
        mapspec = {"layers": [{"id": "l1"}]}
        attach_grammar_decision(mapspec["layers"][0], d)
        assert GRAMMAR_LAYER_KEY in mapspec["layers"][0]
        col = collect_grammar_decisions(mapspec)
        assert len(col.refs) == 1
        assert col.refs[0].layer_id == "l1"
        assert col.refs[0].decision.fingerprint == d.fingerprint

    def test_version_mismatch_not_collected(self):
        d = self._decision_for("f", [1.0, 2.0])
        mapspec = {"layers": [{"id": "l1",
                               GRAMMAR_LAYER_KEY: decision_payload(d)}]}
        mapspec["layers"][0][GRAMMAR_LAYER_KEY]["grammar_version"] = "0.0.1"
        col = collect_grammar_decisions(mapspec)
        assert col.empty
        assert len(col.invalid) == 1

    def test_invalid_payload_reported(self):
        mapspec = {"layers": [{"id": "l1", GRAMMAR_LAYER_KEY: {"nope": 1}}]}
        col = collect_grammar_decisions(mapspec)
        assert col.empty and len(col.invalid) == 1

    def test_composite_audit_scopes_to_own_layer(self):
        # 两个层各带矛盾决策：审计各自只对自己的源层产出 findings，
        # 不跨层误报（PAIRING finding 只在层型与自身决策冲突时出现）。
        d_div = self._decision_for_signed()
        d_seq = self._decision_for("f2", [1.0, 2.0, 3.0, 4.0, 5.0, 6.0])
        layers = [
            {"id": "a", "legend_spec": {"type": "divergent"}},
            {"id": "b", "legend_spec": {"type": "graduated"}},
        ]
        attach_grammar_decision(layers[0], d_div)
        attach_grammar_decision(layers[1], d_seq)
        auditor = grammar_auditor_for_mapspec({"layers": layers})
        audit = auditor.audit(layers)
        pairing_layers = {f.layer_id for f in audit.findings
                          if f.code == "GRAMMAR.AUDIT.PAIRING"}
        assert pairing_layers <= {"a", "b"}
        # diverging 决策 + divergent 图例 = 无 PAIRING；sequential + graduated
        # 合法 = 无 PAIRING —— 两个都对则零 finding。
        assert not pairing_layers

    def _decision_for_signed(self):
        from app.lib.cartography.grammar_solver import FieldEvidence
        req = GrammarRequest(
            geometry="polygon", feature_count=12,
            fields=[FieldEvidence(
                name="net", dtype="float", values=[-5.0, 3.0, -2.0, 1.0],
                derived_measurement="signed_change")],
        )
        return solve_grammar(req)

    def test_no_decisions_returns_none(self):
        assert grammar_auditor_for_mapspec({"layers": [{"id": "x"}]}) is None
        assert grammar_auditor_for_mapspec({}) is None
        assert grammar_auditor_for_mapspec(None) is None

    def test_review_cartography_with_auditor(self):
        d = self._decision_for_signed()
        layer = {"id": "a", "legend_spec": {"type": "categorical"}}
        attach_grammar_decision(layer, d)
        mapspec = {"layers": [layer]}
        auditor = grammar_auditor_for_mapspec(mapspec)
        result = review_cartography(mapspec, grammar_decision=auditor)
        assert result.grammar_audit is not None
        assert result.grammar_audit["evaluated"] is True
        codes = {f["code"] for f in result.grammar_audit["findings"]}
        assert "GRAMMAR.AUDIT.PAIRING" in codes  # categorical×diverging 冲突可见

    def test_review_without_decision_grammar_audit_none(self):
        result = review_cartography({"layers": [{"id": "x"}]})
        assert result.grammar_audit is None


class TestDerivedMeasurementInSolver:
    def test_derived_measurement_source_not_user_wins(self):
        from app.lib.cartography.grammar_solver import FieldEvidence
        req = GrammarRequest(
            geometry="polygon", feature_count=10,
            fields=[FieldEvidence(name="delta", dtype="float",
                                  values=[1.0, 2.0],
                                  derived_measurement="signed_change")],
        )
        d = solve_grammar(req)
        assert d.data_kind == "diverging"
        assert d.bindings[0].measurement.source == "dataset_contract"
        assert "GRAMMAR.MEAS.DATASET_CONTRACT" in (
            d.bindings[0].measurement.reason_codes)
        assert not d.user_wins  # 派生语义不是用户 pin

    def test_explicit_pin_beats_derived(self):
        from app.lib.cartography.grammar_solver import FieldEvidence
        req = GrammarRequest(
            geometry="polygon", feature_count=10,
            fields=[FieldEvidence(name="delta", dtype="float",
                                  values=[1.0, 2.0],
                                  measurement="nominal",
                                  derived_measurement="signed_change")],
        )
        d = solve_grammar(req)
        assert d.bindings[0].measurement.source == "explicit"
        assert d.data_kind == "qualitative"
        assert any(w.get("kind") == "measurement" for w in d.user_wins)

    def test_derived_invalid_vocab_fail_closed(self):
        from app.lib.cartography.grammar_solver import FieldEvidence
        with pytest.raises(ValueError):
            FieldEvidence(name="x", derived_measurement="bogus")

    def test_no_derived_backcompat(self):
        # 不带 derived_measurement 的请求：行为与 1.0.0 一致（推断面）。
        from app.lib.cartography.grammar_solver import FieldEvidence
        req = GrammarRequest(
            geometry="polygon", feature_count=10,
            fields=[FieldEvidence(name="total_pop", dtype="float",
                                  values=[100.0, 200.0, 300.0])],
        )
        d = solve_grammar(req)
        assert d.bindings[0].measurement.kind == "ratio"
        assert d.bindings[0].measurement.source == "evidence"
