"""chain_gate 门裁决正反例（G08）。

``evaluate_chain_gate`` 此前只被 ``run_chain_gate_for_session`` /
``replay.chain_completeness_report`` 间接消费（README 死语料清单登记项）
—— 本文件把它作为一等入口消费：比率门 / expected 集合门 / na 披露 /
fail-closed 逐一定义域验证。
"""
from __future__ import annotations

import pytest

from app.evaluation.chain_gate import (
    DEFAULT_MIN_COMPLETENESS,
    evaluate_chain_gate,
)
from app.lib.runtime.gis_trace import ALL_STAGES

ALL_NAMES = [s.name for s in ALL_STAGES]


def _record(turn_id: str, stages) -> dict:
    return {"turn_id": turn_id, "stages": [{"stage": name} for name in stages]}


def test_full_chain_passes_default_gate():
    verdict = evaluate_chain_gate([_record("t1", ALL_NAMES)])
    assert verdict["passed"] is True
    assert verdict["per_chain"][0]["completeness"] == 1.0
    assert verdict["min_required"] == DEFAULT_MIN_COMPLETENESS == 0.95


def test_sparse_chain_fails_ratio_gate():
    verdict = evaluate_chain_gate([_record("t1", ALL_NAMES[:10])])
    assert verdict["passed"] is False
    per_chain = verdict["per_chain"][0]
    assert per_chain["covered_count"] == 10
    assert per_chain["completeness"] < DEFAULT_MIN_COMPLETENESS


def test_boundary_completeness_uses_ge_semantics():
    seventeen = ALL_NAMES[:17]  # 17/18 ≈ 0.944 < 0.95
    assert evaluate_chain_gate([_record("t", seventeen)])["passed"] is False
    relaxed = evaluate_chain_gate(
        [_record("t", seventeen)], min_completeness=0.9
    )
    assert relaxed["passed"] is True


def test_na_stages_removed_from_denominator_with_disclosure():
    na = {"MODEL_ROUTING"}
    covered = [s for s in ALL_NAMES if s not in na]
    verdict = evaluate_chain_gate([_record("t", covered)], na_stages=na)
    assert verdict["passed"] is True
    assert verdict["per_chain"][0]["effective_total"] == len(ALL_STAGES) - 1
    assert verdict["na_stages"] == ["MODEL_ROUTING"]


def test_unknown_na_stage_disclosed_not_silently_dropped():
    verdict = evaluate_chain_gate(
        [_record("t", ALL_NAMES)], na_stages={"BOGUS_STAGE"}
    )
    assert verdict["unknown_na_stages"] == ["BOGUS_STAGE"]
    # 未知 na 不扣分母：全覆盖链仍通过。
    assert verdict["passed"] is True


def test_expected_stages_mode_overrides_ratio_gate():
    expected = {"USER_INTENT", "TOOL_CALLS"}
    ok = evaluate_chain_gate(
        [_record("t", ["USER_INTENT", "TOOL_CALLS"])],
        expected_stages=expected,
    )
    assert ok["passed"] is True
    assert ok["per_chain"][0]["missing_expected"] == []
    bad = evaluate_chain_gate(
        [_record("t", ["USER_INTENT"])], expected_stages=expected
    )
    assert bad["passed"] is False
    assert bad["per_chain"][0]["missing_expected"] == ["TOOL_CALLS"]


def test_empty_records_fail_closed():
    verdict = evaluate_chain_gate([])
    assert verdict["passed"] is False
    assert verdict["chain_count"] == 0


def test_multi_chain_gate_requires_every_chain():
    verdict = evaluate_chain_gate(
        [_record("t-ok", ALL_NAMES), _record("t-bad", ALL_NAMES[:5])]
    )
    assert verdict["passed"] is False
    assert verdict["chain_count"] == 2
    by_turn = {c["turn_id"]: c for c in verdict["per_chain"]}
    assert by_turn["t-ok"]["passed"] is True
    assert by_turn["t-bad"]["passed"] is False


def test_run_chain_gate_for_session_reads_persisted_chains(monkeypatch):
    import app.evaluation.chain_gate as cg

    chains = {"sess-ok": [_record("t1", ALL_NAMES)], "sess-empty": []}
    monkeypatch.setattr(
        cg, "load_session_chains", lambda session_id: chains.get(session_id, [])
    )
    assert cg.run_chain_gate_for_session("sess-ok")["passed"] is True
    # 链缺席 = fail-closed（不伪造通过）。
    assert cg.run_chain_gate_for_session("sess-empty")["passed"] is False
    assert cg.run_chain_gate_for_session("sess-missing")["passed"] is False


@pytest.mark.parametrize(
    "stage_subset,min_required,expect_pass",
    [
        (ALL_NAMES, 0.95, True),
        (ALL_NAMES[:14], 0.95, False),
        (ALL_NAMES[:14], 0.7, True),
    ],
)
def test_ratio_gate_matrix(stage_subset, min_required, expect_pass):
    verdict = evaluate_chain_gate(
        [_record("t", stage_subset)], min_completeness=min_required
    )
    assert verdict["passed"] is expect_pass
