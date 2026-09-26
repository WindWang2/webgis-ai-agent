"""F02 场景 corpus 验收（任务书 §8 的 8+1 场景；F02 DoD #1 佐证）。

每个场景跑真实的 resolve→classify→build→clarify→digest 链路；
multiturn_edits 场景跑完整多轮 patch→replay→归因链。期望即契约，
不绑定具体工具选择（IR 不是第二执行器）。
"""
from __future__ import annotations

import asyncio

import pytest

from app.services.gis_harness.intent import resolve_map_request_intent
from app.services.gis_harness.requirement_ir import (
    build_document,
    classification_from_core,
    plan_clarifications,
    record_ambiguities,
)
from app.services.gis_harness.requirement_ir import corpus as corpus_mod
from app.services.gis_harness.requirement_ir import service
from app.services.gis_harness.requirement_ir.digest import (
    canonical_core,
    document_digest,
    requirement_digest,
)


def _scenario_ids():
    return [s.id for s in corpus_mod.CORPUS]


class TestCorpusStructure:
    def test_all_eight_directions_present(self):
        expected = {
            "chengdu_primary_schools", "admin_stat_heatmap", "temporal_change",
            "landuse_categories", "two_measure_compare", "analysis_only",
            "analyze_then_map", "export_publication", "multiturn_edits",
        }
        assert set(_scenario_ids()) == expected

    def test_corpus_models_valid(self):
        for scenario in corpus_mod.CORPUS:
            assert corpus_mod.CorpusScenario.model_validate(
                scenario.model_dump()) == scenario


@pytest.mark.parametrize("scenario_id", _scenario_ids())
class TestCorpusAcceptance:
    def test_turns_satisfy_expectations(self, scenario_id):
        scenario = corpus_mod.get_scenario(scenario_id)
        assert scenario is not None
        for turn in scenario.turns:
            core = resolve_map_request_intent(turn.query)
            classification = classification_from_core(
                turn.query, core, has_document=turn.assume_document)
            if turn.expect_kind is not None:
                assert classification.kind == turn.expect_kind, (
                    f"[{scenario.id}] {turn.query!r} → "
                    f"{classification.kind}（期望 {turn.expect_kind}）")
            if turn.expect_stages:
                assert classification.stages == turn.expect_stages
            if turn.expect_clarification_codes:
                doc = build_document(turn.query, core, turn=1)
                doc = record_ambiguities(doc, plan_clarifications(doc), 1)
                codes = {a.code for a in doc.intent.ambiguities}
                missing = set(turn.expect_clarification_codes) - codes
                assert not missing, (
                    f"[{scenario.id}] {turn.query!r} 缺澄清 {missing}；"
                    f"实际 {codes}")

    def test_document_builds_and_digests(self, scenario_id):
        scenario = corpus_mod.get_scenario(scenario_id)
        for turn in scenario.turns:
            doc = build_document(
                turn.query, resolve_map_request_intent(turn.query), turn=1)
            digest = requirement_digest(doc)
            assert digest.startswith("rd1:") and len(digest) > 10
            again = build_document(
                turn.query, resolve_map_request_intent(turn.query), turn=1)
            assert requirement_digest(again) == digest


class TestSynonymVariants:
    @pytest.mark.parametrize("scenario_id", [
        "chengdu_primary_schools", "admin_stat_heatmap"])
    def test_surface_independence_of_digest(self, scenario_id):
        """digest 的真正不变量：同一语义核、不同 surface（query 文案、
        turn、provenance）→ digest 稳定。synonym_variant 场景以 base 的
        core 构造变体文档（同核异面），锁定 digest 与 canonical_core
        的表面无关性。"""
        scenario = corpus_mod.get_scenario(scenario_id)
        base = build_document(
            scenario.turns[0].query,
            resolve_map_request_intent(scenario.turns[0].query), turn=1)
        variant = build_document(
            scenario.synonym_variant,
            resolve_map_request_intent(scenario.turns[0].query), turn=9)
        # 同 core、不同 surface 文案/turn → digest 必须一致
        assert requirement_digest(base) == requirement_digest(variant)
        # canonical_core 不含 surface 文本
        core_payload = str(sorted(canonical_core(base).items()))
        assert scenario.turns[0].query not in core_payload
        assert scenario.synonym_variant not in core_payload


class TestMultiturnScenario:
    def test_full_patch_replay_attribution_chain(self):
        scenario = corpus_mod.get_scenario("multiturn_edits")
        assert scenario is not None

        class MemStore:
            def __init__(self):
                self.data = {}

            async def get_map_state(self, sid):
                return self.data.get(sid, {})

            async def set_map_state(self, sid, key, value, seq=None):
                self.data.setdefault(sid, {})[key] = value
                return True

        store = MemStore()
        results = []
        for index, turn in enumerate(scenario.turns, start=1):
            results.append(asyncio.run(service.ensure_document(
                store, "s1", turn.query, turn=index)))
        # 首轮创建；其余轮都是 edit（patch 生效、无 stale）
        assert results[0]["created"] is True
        for turn_index, result in enumerate(results[1:], start=2):
            assert result["patched"] >= 1, f"turn {turn_index} 未产生 patch"
            assert "patch_stale_revision" not in result["patch_errors"]
        # corpus 声明的 patch 面都出现
        state = asyncio.run(service.load_state(store, "s1"))
        doc = service._doc_from(state.document)
        user_paths = set(doc.intent.field_provenance)
        turn_expectations = [
            set(t.expect_patch_paths)
            for t in scenario.turns if t.expect_patch_paths]
        for expected in turn_expectations:
            assert expected & user_paths, f"{expected} 未出现在 {user_paths}"
        # replay 不变量
        rebuilt = service.replay_document(state)
        assert document_digest(rebuilt) == document_digest(doc)
        # 已确认事实存活：subject 始终 小学
        assert doc.intent.subject.category == "小学"
