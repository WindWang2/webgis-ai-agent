"""G05 场景库完整性：规模/家族/负例/数据工厂/断言面覆盖。"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.cartography

from . import fixtures_data
from .contracts import Face
from .corpus import all_scenarios, scenarios_by_family


class TestCorpusIntegrity:
    def test_scale_at_least_20_scenarios(self):
        assert len(all_scenarios()) >= 20

    def test_at_least_12_families(self):
        families = scenarios_by_family()
        assert len(families) >= 12, sorted(families)

    def test_at_least_6_negative_scenarios(self):
        negatives = [s for s in all_scenarios() if s.negative]
        assert len(negatives) >= 6

    def test_ids_unique_and_stable_prefix(self):
        ids = [s.id for s in all_scenarios()]
        assert len(ids) == len(set(ids))
        assert all(i.split("-")[0].startswith("f") for i in ids)

    def test_every_scenario_has_intent_and_expectations(self):
        for s in all_scenarios():
            assert s.turns, s.id
            assert s.expectations, s.id
            kinds = {st.kind.value for t in s.turns for st in t.steps}
            assert "intent" in kinds, f"{s.id}: 场景必须从意图步开始"

    def test_dataset_kinds_registered(self):
        for s in all_scenarios():
            for d in s.datasets:
                assert d.dataset_kind in fixtures_data.ALL_DATASET_KINDS, \
                    (s.id, d.dataset_kind)
                assert d.simulated, f"{s.id}/{d.name}: 合成数据必须标注 simulated"

    def test_all_declared_datasets_actually_used(self):
        """声明未用的 dataset 是契约债（期望引用或 store 步必须存在）。"""
        for s in all_scenarios():
            used = {
                arg.get("name")
                for t in s.turns for st in t.steps
                if st.kind.value == "store_data"
                for arg in [st.arguments]
            }
            for d in s.datasets:
                assert d.name in used, f"{s.id}: dataset '{d.name}' 声明未用"

    def test_multiturn_and_recovery_present(self):
        multiturn = [s for s in all_scenarios() if len(s.turns) >= 2]
        recovery = [s for s in all_scenarios()
                    if any(t.leave_unsettled for t in s.turns)]
        assert multiturn, "DoD 要求 ≥1 多轮交互任务"
        assert recovery, "DoD 要求 ≥1 跨会话恢复路径"

    def test_every_scenario_declares_not_evaluated_boundaries(self):
        for s in all_scenarios():
            assert s.not_evaluated, f"{s.id}: 诚实边界声明缺失"


class TestFaceCoverage:
    def test_all_seven_faces_covered(self):
        covered = {
            e.face for s in all_scenarios() for e in s.expectations
            if e.expected_verdict == "pass"
        }
        missing = {f.value for f in Face} - covered
        assert not missing, f"未被任何 pass 期望覆盖的断言面: {missing}"

    def test_not_evaluated_expectations_declared_deliberately(self):
        ne = [(s.id, e.id) for s in all_scenarios() for e in s.expectations
              if e.expected_verdict == "not_evaluated"]
        assert ne, "应存在显式 not_evaluated 期望（诚实三态钉扎）"
        for s in all_scenarios():
            for e in s.expectations:
                if e.expected_verdict == "not_evaluated":
                    assert e.note, f"{s.id}/{e.id}: not_evaluated 期望必须"
                    "说明生产缺口原因"


class TestFixtureDeterminism:
    def test_vector_fixtures_byte_stable(self):
        for kind in fixtures_data.VECTOR_FACTORIES:
            once = fixtures_data.vector_fixture_json(kind)
            twice = fixtures_data.vector_fixture_json(kind)
            assert once == twice, kind

    def test_fixtures_small_and_simulated(self):
        for kind, factory in fixtures_data.VECTOR_FACTORIES.items():
            fc = factory()
            feats = fc.get("features", [])
            assert 0 < len(feats) <= 64, kind
            for f in feats:
                props = f.get("properties") or {}
                assert props.get("_simulated") is True or \
                    f.get("geometry") is None, \
                    f"{kind}: 属性必须显式标注模拟数据"

    def test_raster_factories_write_under_tmp(self, tmp_path):
        for kind in fixtures_data.RASTER_FACTORIES:
            abs_path, dispatch_path = fixtures_data.make_raster(
                kind, tmp_path / "sub")
            p = pytest.importorskip("pathlib").Path(abs_path)
            assert p.exists() and p.stat().st_size < 20_000, kind
            assert str(p) == dispatch_path
