"""决策溯源记录 + 链结构化通道契约测试（方向 8 / ADR-0204 决策一、二）。

覆盖：
- DecisionRecord 确定性（同输入同 id；任一证据面变化即新 id）；
- 有界投影（超长串/超多键/超深嵌套不进链）；
- GisTraceChain 决策键的结构化通道（bound_meta 的 repr 化旁路 =
  有界 + 秘密键精确名单 REDACTED；其余载荷键行为逐位不变）；
- as_dict 的 schema_version（链记录本体版本位）。
"""
from __future__ import annotations

import pytest

from app.lib.runtime.decision_record import (
    DECISION_KIND_CAPABILITY_DISPATCH_DENIAL,
    DECISION_KIND_PLAN_SELECTION,
    DECISION_MARKER_KEY,
    alternative_entry,
    decision_record,
    reason_code,
)
from app.lib.runtime.gis_trace import (
    GIS_TRACE_CHAIN_SCHEMA_VERSION,
    GisTraceChain,
    Stage,
)

pytestmark = pytest.mark.cartography


class TestDecisionRecordContract:
    def test_deterministic_id_same_inputs(self):
        kwargs = dict(
            selected="poi_distribution_overview",
            alternatives=[{"id": "a", "rank": 0}, {"id": "b", "rank": 1}],
            inputs={"task": "heatmap", "situation": {"crs": "EPSG:4326"}},
            policy_version="recipe_select.v1",
        )
        r1 = decision_record(DECISION_KIND_PLAN_SELECTION, **kwargs)
        r2 = decision_record(DECISION_KIND_PLAN_SELECTION, **kwargs)
        assert r1 == r2
        assert r1["decision_id"].startswith("dec_")
        assert len(r1["decision_id"]) == 16

    def test_id_changes_when_evidence_changes(self):
        base = dict(
            selected="r1",
            alternatives=[{"id": "a"}],
            inputs={"capability": "buffer"},
            policy_version="v1",
        )
        same_kind = decision_record(
            DECISION_KIND_CAPABILITY_DISPATCH_DENIAL, **base)
        selected_changed = decision_record(
            DECISION_KIND_CAPABILITY_DISPATCH_DENIAL,
            **{**base, "selected": "r2"})
        policy_changed = decision_record(
            DECISION_KIND_CAPABILITY_DISPATCH_DENIAL,
            **{**base, "policy_version": "v2"})
        alt_changed = decision_record(
            DECISION_KIND_CAPABILITY_DISPATCH_DENIAL,
            **{**base, "alternatives": [{"id": "b"}]})
        ids = {same_kind["decision_id"], selected_changed["decision_id"],
               policy_changed["decision_id"], alt_changed["decision_id"]}
        assert len(ids) == 4

    def test_bounded_projection(self):
        big_inputs = {
            f"key_{i}": "x" * 500 for i in range(64)
        }
        record = decision_record(
            DECISION_KIND_PLAN_SELECTION,
            selected="r",
            alternatives=[{"id": f"a{i}", "blob": "y" * 999} for i in range(64)],
            inputs=big_inputs,
        )
        assert len(record["alternatives"]) <= 8
        assert len(record["inputs"]) <= 32
        for value in record["inputs"].values():
            assert len(value) <= 96 + 16  # str 上限 + 截断标记余量
        blob = str(record)
        assert len(blob) < 8000  # 决不无界

    def test_secret_values_not_inlined_in_reason_codes(self):
        record = decision_record(
            DECISION_KIND_CAPABILITY_DISPATCH_DENIAL,
            selected="",
            reason_codes=[reason_code(
                "check", "observed sk-abcdefgh12345678", "expected")],
        )
        observed = str(record["reason_codes"][0]["observed"])
        assert "sk-abcdefgh12345678" not in observed
        assert "[REDACTED]" in observed

    def test_alternative_entry_optional_score(self):
        entry = alternative_entry("a", rank=1)
        assert entry == {"id": "a", "rank": 1}
        scored = alternative_entry("b", score=1.23456, status="eligible")
        assert scored["score"] == 1.2346
        assert "rank" not in scored


class TestChainStructuredChannel:
    def _chain_dict_with_decision(self, **extra):
        chain = GisTraceChain(turn_id="t-dc", session_id="s-dc")
        chain.record(
            Stage.CANDIDATE_WORKFLOWS,
            candidates=["r1", "r2"],
            selected="r1",
            decision=decision_record(
                DECISION_KIND_PLAN_SELECTION,
                selected="r1",
                alternatives=[{"id": "r1", "rank": 0}],
                inputs={"task": "kde", "situation": {"crs": "EPSG:4326"}},
                policy_version="recipe_select.v1",
            ),
            **extra,
        )
        return chain.as_dict()

    def test_decision_survives_as_structured_dict(self):
        d = self._chain_dict_with_decision()
        rec = next(s for s in d["stages"]
                   if s.get("stage") == "CANDIDATE_WORKFLOWS")
        assert isinstance(rec[DECISION_MARKER_KEY], dict)
        assert rec[DECISION_MARKER_KEY]["selected"] == "r1"
        assert rec[DECISION_MARKER_KEY]["inputs"]["task"] == "kde"

    def test_secret_keys_redacted_in_structured_channel(self):
        chain = GisTraceChain(turn_id="t-dc2", session_id="s-dc")
        chain.record(
            Stage.CANDIDATE_WORKFLOWS,
            decision={"decision_id": "dec_x", "inputs": {
                "api_key": "sk-real-secret", "password": "hunter2",
                "task": "ok"}},
        )
        stored = chain.as_dict()["stages"][0][DECISION_MARKER_KEY]
        assert stored["inputs"]["api_key"] == "[REDACTED]"
        assert stored["inputs"]["password"] == "[REDACTED]"
        assert stored["inputs"]["task"] == "ok"

    def test_string_leaf_value_scrub_in_structured_channel(self):
        """值级兜底（review P2-1）：非命中键下的秘密值同样被剥离。"""
        chain = GisTraceChain(turn_id="t-dc2b", session_id="s-dc")
        chain.record(
            Stage.CANDIDATE_WORKFLOWS,
            decision={"decision_id": "dec_x2", "inputs": {
                "note": "config uses sk-abcdefgh12345678 for upstream"}},
        )
        stored = chain.as_dict()["stages"][0][DECISION_MARKER_KEY]
        assert "sk-abcdefgh12345678" not in stored["inputs"]["note"]

    def test_domain_fact_keys_not_redacted(self):
        """auth_tier / owner_scope_key 是重推导的行为输入，不得误伤。"""
        chain = GisTraceChain(turn_id="t-dc3", session_id="s-dc")
        chain.record(
            Stage.CANDIDATE_WORKFLOWS,
            decision={"decision_id": "dec_y", "inputs": {
                "situation": {"auth_tier": 2, "owner_scope_key": "u-1",
                              "offline": False}},
            },
        )
        stored = chain.as_dict()["stages"][0][DECISION_MARKER_KEY]
        situation = stored["inputs"]["situation"]
        assert situation["auth_tier"] == 2
        assert situation["owner_scope_key"] == "u-1"

    def test_other_payload_keys_keep_bound_meta_semantics(self):
        """非决策键逐位既有行为：嵌套 dict 仍被 bound_meta repr 化。"""
        chain = GisTraceChain(turn_id="t-dc4", session_id="s-dc")
        chain.record(Stage.MAP_MUTATIONS, tool="t",
                     actions=["a1"], commands=["fly_to"])
        rec = chain.as_dict()["stages"][0]
        assert isinstance(rec["commands"], str)  # repr 形态（既有语义）
        assert "fly_to" in rec["commands"]

    def test_chain_schema_version_present(self):
        d = self._chain_dict_with_decision()
        assert d["schema_version"] == GIS_TRACE_CHAIN_SCHEMA_VERSION == 1

    def test_decisions_list_payload_structured(self):
        chain = GisTraceChain(turn_id="t-dc5", session_id="s-dc")
        chain.record(
            Stage.SELECTED_WORKFLOW,
            recipe_id="r1",
            decisions=[decision_record(
                DECISION_KIND_PLAN_SELECTION, selected="r1",
                inputs={"capability": "buffer"},
            )],
        )
        rec = chain.as_dict()["stages"][0]
        assert isinstance(rec["decisions"], list)
        assert rec["decisions"][0]["inputs"]["capability"] == "buffer"
