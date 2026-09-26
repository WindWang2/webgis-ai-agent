"""F04 allocator tests — pools, floors, honest omission, observe/enforce."""
from app.services.context_assembly.allocator import (
    REASON_OMITTED_GLOBAL_CAP,
    REASON_OMITTED_POOL_CAP,
    REASON_TRUNCATED_FLOOR,
    REASON_WINDOW_UNKNOWN,
    allocate,
)
from app.services.context_assembly.contract import ContextDomain, bounded_item


def _items(*sizes):
    domains = [
        ContextDomain.ENVIRONMENT,
        ContextDomain.V6_BLOCKS,
        ContextDomain.EVICTED_REFS,
        ContextDomain.GIS_MEMORY,
        ContextDomain.CARTOGRAPHY_MEMORY,
    ]
    return [
        bounded_item(
            item_id=f"i{idx}", provider_id="p",
            domain=domains[idx % len(domains)],
            content="x" * (size * 4),  # ~size tokens ascii
        )
        for idx, size in enumerate(sizes)
    ]


def test_observe_mode_never_drops_even_over_cap():
    items = [
        bounded_item(item_id="e0", provider_id="p",
                     domain=ContextDomain.ENVIRONMENT, content="x" * 3600),
        bounded_item(item_id="e1", provider_id="p",
                     domain=ContextDomain.V6_BLOCKS, content="x" * 3600),
    ]
    result = allocate(items, context_window=None, max_output_tokens=0)
    assert [i.item_id for i in result.items] == ["e0", "e1"]
    assert result.over_budget is False
    assert any(r.reason_code.startswith("pool_over:") for r in result.records)


def test_enforce_mode_omits_over_cap_items_with_reasons():
    items = _items(900, 900)  # same-pool overflow (PROJECT_CONTEXT)
    result = allocate(items, context_window=4096, max_output_tokens=512)
    decisions = {r.item_id: (r.decision, r.reason_code) for r in result.records}
    dropped = [k for k, (d, _) in decisions.items() if d == "omitted"]
    assert dropped, "enforce mode must drop over-cap items"
    assert all(
        code.startswith(REASON_OMITTED_POOL_CAP) for k, (d, code) in decisions.items()
        if d == "omitted"
    )
    # deterministic: same inputs → same outcome
    again = allocate(items, context_window=4096, max_output_tokens=512)
    assert [i.item_id for i in again.items] == [i.item_id for i in result.items]


def test_floor_item_truncated_not_dropped():
    env = bounded_item(
        item_id="env", provider_id="p",
        domain=ContextDomain.ENVIRONMENT,
        content="v" * 3600,  # ~900 tokens, over the MAP_STATE pool cap
    )
    result = allocate([env], context_window=4096, max_output_tokens=512)
    assert any(i.item_id == "env" for i in result.items), \
        "floor-protected environment block must survive pool pressure"
    assert any(i.item_id == "env" and len(i.content) < 400 for i in result.items)
    assert any(
        r.decision == "truncated" and r.reason_code == REASON_TRUNCATED_FLOOR
        for r in result.records
    )


def test_user_message_and_marker_untouchable_under_enforce():
    user = bounded_item(item_id="u", provider_id="p",
                        domain=ContextDomain.USER_MESSAGE, content="用户请求" * 500)
    marker = bounded_item(item_id="m", provider_id="p",
                          domain=ContextDomain.TURN_MARKER,
                          content="[WEBGIS_TURN_CONTEXT:t]", control_plane=True)
    tools = bounded_item(item_id="t", provider_id="p",
                         domain=ContextDomain.ACTIVE_TOOLS,
                         content="[WEBGIS_ACTIVE_TOOLS:[\"a\"]]",
                         control_plane=True)
    flood = [
        bounded_item(item_id=f"f{i}", provider_id="p",
                     domain=ContextDomain.GIS_MEMORY, content="z" * 300)
        for i in range(60)
    ]
    result = allocate(
        [user, marker, tools, *flood],
        context_window=2048, max_output_tokens=256,
    )
    ids = {i.item_id for i in result.items}
    assert {"u", "m", "t"} <= ids
    assert next(i for i in result.items if i.item_id == "u").content == user.content


def test_global_cap_omits_in_yield_order():
    floods = [
        bounded_item(item_id=f"low{i}", provider_id="p",
                     domain=ContextDomain.GIS_MEMORY,
                     content="e" * 2000, priority=999)
        for i in range(8)
    ]
    small_high = bounded_item(item_id="high", provider_id="p",
                              domain=ContextDomain.CARTOGRAPHY_VERDICT,
                              content="v" * 40, priority=1)
    result = allocate(
        [*floods, small_high],
        context_window=4096, max_output_tokens=512,
    )
    ids = [i.item_id for i in result.items]
    assert "high" in ids, "high-priority item must survive global pressure"
    kept_low = [i for i in ids if i.startswith("low")]
    assert len(kept_low) < 8, "global cap must omit enough lows to fit"
    assert result.planned_tokens <= result.usable_tokens
    assert any(
        r.reason_code.startswith(REASON_OMITTED_GLOBAL_CAP) for r in result.records
    )


def test_window_unknown_records_honest_flag():
    items = _items(10, 10)
    result = allocate(items, context_window=None, max_output_tokens=0)
    assert result.window_known is False
    assert any(r.reason_code == REASON_WINDOW_UNKNOWN for r in result.records)
    # configured window → no honesty downgrade
    ok = allocate(items, context_window=8192, max_output_tokens=512)
    assert ok.window_known is True
    assert not any(r.reason_code == REASON_WINDOW_UNKNOWN for r in ok.records)


def test_pool_usage_accounted():
    items = _items(50, 30)
    result = allocate(items, context_window=None, max_output_tokens=0)
    assert result.pool_usage.get("MAP_STATE", 0) > 0
    assert result.planned_tokens == sum(i.est_tokens for i in result.items)
