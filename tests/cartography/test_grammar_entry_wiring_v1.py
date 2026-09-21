"""Grammar 入口接线与 critique 消费测试（C6 + ADR-0205 D7/D8）。

覆盖：create_thematic_map 的 data_kind 推导（signed→diverging 修正 +
sequential 零漂移回归）、classification_plan 的 grammar 摘要、
quality_loop review 的只读 grammar_audit 段（有 decision 才评、缺失
None、不影响 status）。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

pytestmark = pytest.mark.cartography

from app.lib.cartography.grammar_solver import (  # noqa: E402
    FieldEvidence,
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


def _polygon_fc(field: str, values: list[float]) -> dict:
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


class TestCreateThematicMapWiring:
    @pytest.mark.asyncio
    async def test_signed_values_get_diverging_family(self, registry):
        gj = _polygon_fc("net_migration",
                         [-500.0, -300.0, -100.0, -50.0, 10.0, 80.0,
                          200.0, 400.0, 600.0, 900.0, -800.0, -200.0])
        out = await registry.dispatch("create_thematic_map",
                                      {"geojson": gj, "field": "net_migration"})
        assert "error" not in out, out.get("error")
        plan = out["classification_plan"]
        assert plan["data_kind"] == "diverging"
        assert plan["grammar"]["kind"] == "signed_change"
        assert out["symbology_decision"]["palette"] in ("RdBu", "PuOr", "RdYlGn")

    @pytest.mark.asyncio
    async def test_sequential_values_unchanged(self, registry):
        """零漂移回归：正数总量路径仍走 sequential 族（既有行为不变）。"""
        gj = _polygon_fc("population",
                         [100.0, 300.0, 500.0, 800.0, 1200.0, 2000.0,
                          3000.0, 500.0, 700.0, 900.0, 1500.0, 2500.0])
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
    async def test_explicit_method_skips_grammar(self, registry):
        """显式 method 时不进 grammar 推导块（显式决策受尊重）。"""
        gj = _polygon_fc("net_migration",
                         [-500.0, -300.0, 10.0, 80.0, 200.0, 400.0])
        out = await registry.dispatch("create_thematic_map", {
            "geojson": gj, "field": "net_migration",
            "method": "equal_interval", "k": 3,
        })
        assert "error" not in out
        assert "classification_plan" not in out


class TestQualityLoopGrammarAudit:
    def _mapspec(self, legend_type: str) -> dict:
        return {
            "sources": {"s1": {"type": "geojson", "ref": "ref:x"}},
            "layers": [{
                "id": "l1", "source": "s1", "type": "fill",
                "paint": {"fill-color": {"field": "net_migration"}},
                "legend_spec": {"type": legend_type, "field": "net_migration"},
            }],
        }

    def _decision(self):
        return solve_grammar(GrammarRequest(
            geometry="polygon", zoom=9.0,
            fields=[FieldEvidence(name="net_migration", dtype="float",
                                  values=[-5.0] * 6 + [5.0] * 6)]))

    def test_audit_consumed_when_decision_present(self):
        d = self._decision()
        result = review_cartography(
            self._mapspec("categorical"), grammar_decision=d)
        audit = result.grammar_audit
        assert audit is not None and audit["evaluated"] is True
        assert any(f["code"] == "GRAMMAR.AUDIT.PAIRING" for f in audit["findings"])
        # 审计段随 to_dict 下发（trace 反查面）
        assert result.to_dict()["grammar_audit"] is not None

    def test_correct_pairing_clean_audit(self):
        d = self._decision()
        result = review_cartography(
            self._mapspec("divergent"), grammar_decision=d)
        audit = result.grammar_audit
        assert audit is not None
        assert not any(f["code"] == "GRAMMAR.AUDIT.PAIRING"
                       for f in audit["findings"])

    def test_no_decision_means_none(self):
        result = review_cartography(self._mapspec("graduated"))
        assert result.grammar_audit is None
        assert result.to_dict()["grammar_audit"] is None

    def test_audit_never_changes_status(self):
        """只读对账纪律：同一 mapspec 带/不带 decision 的 status 完全一致，
        不新增阻断、不输出第二 verdict（ADR-0200 D2 / ADR-0205 D7）。"""
        mapspec = self._mapspec("categorical")
        without = review_cartography(mapspec)
        with_decision = review_cartography(mapspec,
                                           grammar_decision=self._decision())
        assert without.status == with_decision.status
        assert without.review["status"] == with_decision.review["status"]
