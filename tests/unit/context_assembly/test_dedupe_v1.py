"""F04 dedupe tests — deterministic winner selection, stale-authority rule."""
from app.services.context_assembly.contract import ContextDomain, bounded_item
from app.services.context_assembly.dedupe import (
    REASON_EXACT_DUP,
    REASON_STALE_AUTHORITY,
    dedupe_items,
)
from app.services.gis_context.scope import ScopeTier


def _verdict_item(item_id, content, *, provider="cartography_verdict",
                  priority=None, scope=ScopeTier.TURN, freshness="", ref=""):
    return bounded_item(
        item_id=item_id, provider_id=provider,
        domain=ContextDomain.CARTOGRAPHY_VERDICT, content=content,
        scope=scope, scope_id="s1", freshness=freshness,
        evidence_ref=ref,
        **({"priority": priority} if priority is not None else {}),
    )


def test_exact_duplicate_keeps_higher_authority():
    a = _verdict_item("a", "same text", priority=10)
    b = _verdict_item("b", "same text", priority=99)
    result = dedupe_items([b, a])  # input order must not matter
    assert [i.item_id for i in result.kept] == ["a"]
    assert result.dropped[0][0].item_id == "b"
    assert result.dropped[0][1] == REASON_EXACT_DUP


def test_exact_duplicate_tiebreak_by_scope_then_id():
    a = _verdict_item("a", "same", scope=ScopeTier.PROJECT)
    b = _verdict_item("b", "same", scope=ScopeTier.TURN)
    result = dedupe_items([a, b])
    assert [i.item_id for i in result.kept] == ["b"]  # turn scope wins
    # same scope+priority → stable id wins
    c = _verdict_item("x", "tie")
    d = _verdict_item("x2", "tie")
    assert [i.item_id for i in dedupe_items([d, c]).kept] == ["x"]


def test_authority_stale_rendering_is_dropped_not_merged():
    newer = _verdict_item("new", "verdict v2", freshness="fp2", ref="fingerprint")
    older = _verdict_item("old", "verdict v1", freshness="fp1", ref="fingerprint")
    result = dedupe_items([newer, older])
    kept_ids = {i.item_id for i in result.kept}
    assert len(kept_ids) == 1
    dropped = {item.item_id: reason for item, reason in result.dropped}
    assert dropped["old"] == REASON_STALE_AUTHORITY
    assert kept_ids == {"new"}


def test_distinct_authorities_never_merge():
    a = _verdict_item("a", "alpha", ref="authority-1")
    b = _verdict_item("b", "beta", ref="authority-2")
    result = dedupe_items([a, b])
    assert {i.item_id for i in result.kept} == {"a", "b"}


def test_different_content_without_authority_is_kept():
    a = bounded_item(item_id="a", provider_id="p",
                     domain=ContextDomain.SESSION_PLAN, content="plan A")
    b = bounded_item(item_id="b", provider_id="p",
                     domain=ContextDomain.SESSION_PLAN, content="plan B")
    result = dedupe_items([a, b])
    assert {i.item_id for i in result.kept} == {"a", "b"}
    assert not result.dropped


def test_deterministic_and_order_preserving():
    items = [
        _verdict_item("a", "one"),
        bounded_item(item_id="b", provider_id="p",
                     domain=ContextDomain.ENVIRONMENT, content="env"),
        _verdict_item("c", "one"),  # dup of a
        bounded_item(item_id="d", provider_id="p",
                     domain=ContextDomain.V6_BLOCKS, content="v6"),
    ]
    first = dedupe_items(items)
    assert [i.item_id for i in first.kept] == ["a", "b", "d"]
    # winner selection is input-order independent (sets match), while the
    # kept list preserves each run's own input order
    second = dedupe_items(list(reversed(items)))
    assert {i.item_id for i in second.kept} == {"a", "b", "d"}
    assert [i.item_id for i in second.kept] == ["d", "b", "a"]


def test_dedupe_never_edits_content():
    a = _verdict_item("a", "text A")
    b = _verdict_item("b", "text A")
    result = dedupe_items([a, b])
    assert all(i.content == "text A" for i in result.kept + [x for x, _ in result.dropped])
