"""ContextLayers（V7 ADR-0130 D3）回归锁：九域投影 / 预算 / checkpoint。

不变式：
1. 九域封闭词表；每域 = 权威事实的确定性投影（同输入同投影，重建等价）；
2. 预算：域上限压缩为摘要 + 总预算按 DOMAIN_PRUNE_ORDER 淘汰；违规留痕
   不静默；
3. freshness：每域 source_fingerprint —— 输入事实变化即变；
4. checkpoint：revision 单调、单键原子写；载荷与权威状态重建等价；
5. 锚点桥：只带指纹/压缩态，载荷永不复制（rebuildable 纪律）。
"""
from __future__ import annotations

from typing import Any, Dict

from app.services.gis_harness.context_layers import (
    CONTEXT_DOMAINS,
    CONTEXT_LAYERS_KEY,
    DOMAIN_BYTE_CAPS,
    TOTAL_BYTE_BUDGET,
    ContextLayersState,
    apply_context_budget,
    context_digest_for_anchor,
    derive_context_layers,
)


def _chapter() -> Dict[str, Any]:
    return {
        "plan_id": "planC",
        "recipe_id": "education_analysis",
        "query": "成都学校分布分析，出专业地图",
        "data_requirements": [
            {"capability": "fetch_schools", "status": "complete",
             "bound_ref": "ref:geojson-schools"},
        ],
        "analysis_steps": [
            {"capability": "spatial_join", "status": "complete",
             "bound_ref": "ref:geojson-join"},
            {"capability": "admin_aggregation", "status": "pending"},
        ],
        "workflow_instance": {
            "state_revision": 4,
            "stages": [
                {"capability": "fetch_schools", "state": "satisfied"},
                {"capability": "spatial_join", "state": "satisfied"},
            ],
        },
    }


def _recovery() -> Dict[str, Any]:
    return {
        "loops": {"repair": 1, "deepen": 0, "requalify": 0, "replan": 0},
        "position": {"step": "cartography"},
        "source_fingerprints": [
            {"ref": "ref:geojson-schools", "fingerprint": "ab" * 8},
        ],
    }


def _map_digest() -> Dict[str, Any]:
    return {"mapspec_revision": 7, "render_observation_seq": 3,
            "layer_count": 2, "component_count": 5}


def _runtime_state() -> Dict[str, Any]:
    return {"phase": "executing", "phase_revision": 9,
            "suspended": False, "last_trigger": "execution_progressed"}


# ── 九域投影 ─────────────────────────────────────────────────────────────


def test_domains_closed_and_all_present():
    assert len(CONTEXT_DOMAINS) == 9
    state = derive_context_layers(
        _chapter(), map_digest=_map_digest(), recovery=_recovery(),
        runtime_state=_runtime_state())
    assert set(state.domains.keys()) == set(CONTEXT_DOMAINS)


def test_projection_deterministic_and_rebuildable():
    args = dict(map_digest=_map_digest(), recovery=_recovery(),
                runtime_state=_runtime_state())
    a = derive_context_layers(_chapter(), **args)
    b = derive_context_layers(_chapter(), **args)
    assert a.digest() == b.digest()
    # stored 在场 → revision 单调推进；内容指纹与 revision 无关
    c = derive_context_layers(
        _chapter(), stored={"schema": "context_layers.v1", "revision": 3},
        **args)
    assert c.revision == 4
    assert c.digest() == a.digest()


def test_domain_payloads_reflect_authority():
    state = derive_context_layers(
        _chapter(), map_digest=_map_digest(), recovery=_recovery(),
        runtime_state=_runtime_state())
    assert state.domains["turn"].payload["phase"] == "executing"
    assert state.domains["map"].payload["mapspec_revision"] == 7
    assert state.domains["workflow"].payload["revision"] == 4
    assert "fetch_schools" in state.domains["capability"].payload["capabilities"]
    assert state.domains["artifact"].payload["bound_refs"]
    assert state.domains["data"].payload["refs"][0]["ref"] == "ref:geojson-schools"
    assert state.domains["session"].payload["loops_remaining"]["repair"] == 1


def test_freshness_fingerprint_changes_with_input():
    args = dict(map_digest=_map_digest(), recovery=_recovery(),
                runtime_state=_runtime_state())
    base = derive_context_layers(_chapter(), **args)
    # workflow 域的输入事实变化（stage 漂移）→ 该域指纹 + 整体摘要变
    changed = _chapter()
    changed["workflow_instance"]["stages"][0]["state"] = "stale"
    diff = derive_context_layers(changed, **args)
    assert diff.domains["workflow"].source_fingerprint != (
        base.domains["workflow"].source_fingerprint)
    assert diff.digest() != base.digest()
    # 无关域（map）不受影响
    assert diff.domains["map"].source_fingerprint == (
        base.domains["map"].source_fingerprint)


# ── 预算 ─────────────────────────────────────────────────────────────────


def test_budget_compacts_over_cap_domain():
    big_recovery = _recovery()
    big_recovery["source_fingerprints"] = [
        {"ref": f"ref:long-{i}" + "x" * 300, "fingerprint": "ab" * 8}
        for i in range(40)
    ]
    state = derive_context_layers(
        _chapter(), map_digest=_map_digest(), recovery=big_recovery,
        runtime_state=_runtime_state())
    # data 域超上限 → 压缩为摘要
    assert state.domains["data"].compacted is True
    assert state.domains["data"].payload.get("compacted") is True
    assert state.violations
    assert any(v.startswith("domain_over_cap:data") for v in state.violations)


def test_budget_total_prune_order_deterministic():
    # 各域都压在自身上限内，但总和超 TOTAL → 触发 prune（非 compact）。
    from app.services.gis_harness.context_layers import (
        DOMAIN_PRUNE_ORDER,
        DomainBlock,
    )

    domains = {}
    for name in CONTEXT_DOMAINS:
        cap = DOMAIN_BYTE_CAPS[name]
        domains[name] = DomainBlock(payload={"blob": "x" * (cap - 42)})
    state = ContextLayersState(domains=domains)
    apply_context_budget(state)
    pruned = [n for n in CONTEXT_DOMAINS if state.domains[n].pruned]
    compacted = [n for n in CONTEXT_DOMAINS if state.domains[n].compacted]
    assert compacted == []  # 单域合规 → 无压缩
    # 淘汰集合 = DOMAIN_PRUNE_ORDER 的前缀（集合断言 + 顺序断言分离：
    # CONTEXT_DOMAINS 迭代序 ≠ 裁剪序）
    assert set(pruned) == set(list(DOMAIN_PRUNE_ORDER)[:len(pruned)])
    pruned_in_prune_order = [
        n for n in DOMAIN_PRUNE_ORDER if state.domains[n].pruned]
    assert pruned_in_prune_order == list(DOMAIN_PRUNE_ORDER)[:len(pruned)]
    assert pruned, "至少淘汰一域"


def test_total_budget_bound():
    state = derive_context_layers(
        _chapter(), map_digest=_map_digest(), recovery=_recovery(),
        runtime_state=_runtime_state())
    import json

    total = sum(len(json.dumps(d.payload, ensure_ascii=False, default=str))
                for d in state.domains.values())
    assert total <= TOTAL_BYTE_BUDGET


# ── 锚点桥 ───────────────────────────────────────────────────────────────


def test_anchor_digest_carries_no_payload():
    state = derive_context_layers(
        _chapter(), map_digest=_map_digest(), recovery=_recovery(),
        runtime_state=_runtime_state())
    digest = context_digest_for_anchor(state)
    assert digest["digest"] == state.digest()
    dom = digest["domains"]["map"]
    assert set(dom.keys()) == {"fp", "compacted", "pruned"}
    # 载荷不进锚点（rebuildable 纪律）
    assert "mapspec_revision" not in str(digest)
    # 空态
    assert context_digest_for_anchor(None) == {}


# ── checkpoint 服务入口（真实 session store）────────────────────────────


async def test_checkpoint_roundtrip():
    import uuid

    sid = f"ctx-svc-{uuid.uuid4().hex[:8]}"
    from app.services.session_data import session_data_manager
    from app.services.session_plan import SessionPlan, save_session_plan

    try:
        chapter = _chapter()
        chapter["runtime_state"] = _runtime_state()
        await save_session_plan(SessionPlan(
            envelope_id="env-planC", session_id=sid,
            user_goal=chapter["query"], gis_chapter=chapter))
        from app.services.gis_harness.context_layers import (
            checkpoint_context_layers,
        )

        block = await checkpoint_context_layers(sid)
        assert block is not None
        assert block["schema"] == "context_layers.v1"
        # 读回 map_state 一致
        state = await session_data_manager.get_map_state(sid)
        stored = (state or {}).get(CONTEXT_LAYERS_KEY)
        assert stored is not None
        assert stored["content_fingerprint"] == block["content_fingerprint"]
        # 再跑一次：revision 单调 +1，内容指纹稳定
        block2 = await checkpoint_context_layers(sid)
        assert block2["revision"] == block["revision"] + 1
        assert block2["content_fingerprint"] == block["content_fingerprint"]
    finally:
        await session_data_manager.clear_session(sid)
