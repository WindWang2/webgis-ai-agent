"""F04 receipt + governor link tests — digest determinism, settle-once,
estimate/actual consistency, ledger actuals."""
import pytest

from app.services.context_assembly.contract import ContextDomain, bounded_item
from app.services.context_assembly.governor_link import (
    plan_turn_context,
    settle_turn_context,
)
from app.services.context_assembly.receipt import (
    ContextAssemblyReceipt,
    ItemDecisionLine,
    build_item_lines,
)


def _line(item_id, decision="included", reason="included"):
    return ItemDecisionLine(
        item_id=item_id, provider_id="p", domain="session_plan",
        decision=decision, reason_code=reason, fingerprint="ab" * 8,
        est_tokens=10,
    )


def test_digest_deterministic_and_settle_once():
    a = ContextAssemblyReceipt(
        session_id="s", turn_id="t",
        decision_lines=[_line("i1")],
        planned_tokens=100, actual_tokens=110,
    ).settle()
    b = ContextAssemblyReceipt(
        session_id="s", turn_id="t",
        decision_lines=[_line("i1")],
        planned_tokens=100, actual_tokens=110,
    ).settle()
    assert a.digest == b.digest and a.digest
    # settle-once: further calls never recompute
    assert a.settle() is a


def test_digest_diverges_on_decisions():
    a = ContextAssemblyReceipt(decision_lines=[_line("i1", "included")]).settle()
    b = ContextAssemblyReceipt(
        decision_lines=[_line("i1", "omitted", "omitted:pool_cap")]
    ).settle()
    assert a.digest != b.digest


def test_bounded_dict_never_carries_content():
    receipt = ContextAssemblyReceipt(
        session_id="s", turn_id="t",
        decision_lines=[_line("i1")],
        pool_usage={"SESSION_PLAN": 12},
        governor={"decision": "accept"},
    ).settle()
    bound = receipt.as_bounded_dict()
    assert bound["digest"]
    assert bound["included"] == 1
    assert bound["pool_usage"] == {"SESSION_PLAN": 12}
    assert "governor" in bound
    # only bounded item dicts — no content field anywhere
    for line in bound["items"]:
        assert "content" not in line


def test_build_item_lines_recovers_omitted_items():
    kept = [bounded_item(item_id="keep", provider_id="p",
                         domain=ContextDomain.SESSION_PLAN, content="k")]
    pre = kept + [
        bounded_item(item_id="gone", provider_id="p",
                     domain=ContextDomain.GIS_MEMORY, content="g" * 400)
    ]

    class Rec:
        def __init__(self, item_id, decision, reason_code):
            self.item_id = item_id
            self.decision = decision
            self.reason_code = reason_code
            self.est_tokens_after = 0

    lines = build_item_lines(
        included=kept,
        pre_items=pre,
        allocation_records=[
            Rec("keep", "included", "included"),
            Rec("gone", "omitted", "omitted:pool_cap:PROJECT_CONTEXT:99>10"),
        ],
        dedupe_dropped=[],
        scope_denied=[],
    )
    by_id = {ln.item_id: ln for ln in lines}
    assert by_id["keep"].decision == "included"
    assert by_id["gone"].decision == "omitted"
    assert "pool_cap" in by_id["gone"].reason_code


@pytest.mark.asyncio
async def test_governor_plan_settle_estimate_actual_consistency():
    planned = 137
    plan = await plan_turn_context(
        session_id="gov-sess-1", turn_id="turn-1", planned_tokens=planned
    )
    assert plan.planned_tokens == planned
    # governor exists in-process; observe mode must produce a decision or an
    # honest skip — never an exception.
    assert plan.decision or plan.skipped_reason
    actual_prompt = "x" * planned
    from app.services.chat.context.history_compression import _estimate_tokens

    reconcile = await settle_turn_context(
        plan, session_id="gov-sess-1", turn_id="turn-1",
        actual_prompt=actual_prompt,
    )
    assert reconcile["planned_tokens"] == planned
    assert reconcile["actual_tokens"] == _estimate_tokens(actual_prompt)
    assert reconcile["ledger_recorded"] is True
    # plan handle carries the reconcile for the receipt
    assert plan.extra["reconcile"] == reconcile


@pytest.mark.asyncio
async def test_governor_link_disabled_flag_honest_skip(monkeypatch):
    monkeypatch.setenv("GIS_CONTEXT_GOVERNOR_LINK", "0")
    plan = await plan_turn_context(
        session_id="gov-sess-2", turn_id="t", planned_tokens=50
    )
    assert plan.skipped_reason == "disabled"
    receipt_dict = plan.to_receipt_dict()
    assert receipt_dict["skipped_reason"] == "disabled"


@pytest.mark.asyncio
async def test_session_ledger_records_actual(monkeypatch):
    from app.services.governor.governor import get_governor

    plan = await plan_turn_context(
        session_id="ledger-sess", turn_id="ledger-turn", planned_tokens=80
    )
    prompt = "y" * 40
    from app.services.chat.context.history_compression import _estimate_tokens

    await settle_turn_context(
        plan, session_id="ledger-sess", turn_id="ledger-turn",
        actual_prompt=prompt,
    )
    snap = get_governor().ledger.snapshot("ledger-sess", "ledger-turn")
    session_cum = snap.get("session", {}).get("cumulative", {})
    assert session_cum.get("context_tokens") == _estimate_tokens(prompt)
