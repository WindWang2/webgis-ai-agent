"""V6 自适应执行契约测试（ADR-0118 W8）：观测/受护栏重排/确定性 fallback。"""
from app.services.data_fabric.query.federated.adaptive import (
    AdaptiveController,
    evaluate_tail_orders,
    pick_tail_order,
)


# ── 控制器 ─────────────────────────────────────────────────────────────────


def test_observe_flags_significant_deviation():
    c = AdaptiveController()
    obs = c.observe(hop=0, estimated_rows=10, actual_rows=80)
    assert obs is not None and obs["ratio"] == 8.0
    assert c.notes


def test_observe_ignores_small_deviation():
    c = AdaptiveController()
    assert c.observe(hop=0, estimated_rows=100, actual_rows=120) is None
    assert len(c.observations) == 1  # 仍记录观测
    assert not c.notes


def test_observe_none_estimate_never_flags():
    c = AdaptiveController()
    assert c.observe(hop=0, estimated_rows=None, actual_rows=10**9) is None


def test_replan_budget_is_one():
    c = AdaptiveController()
    c.mark_replan(adopted=True, reason="t1")
    assert not c.can_replan()
    c2 = AdaptiveController(enabled=False)
    assert not c2.can_replan()


# ── 尾序评估 ───────────────────────────────────────────────────────────────


def _attr_edge():
    return {
        "kind": "attribute_join",
        "join_field_left": "k",
        "join_field_right": "k",
    }


def test_tail_orders_connectivity_and_ranking():
    edges = {("s0", "s2"): _attr_edge(), ("s2", "s3"): _attr_edge()}
    ranked = evaluate_tail_orders(
        accumulated_card=10_000,
        tail_sources=[("s2", 100), ("s3", 200)],
        tail_edges=edges,
        ndv_by_source={"s2": {"k": 10}, "s3": {"k": 5}},
    )
    assert [o for _, o in ranked] == [["s2", "s3"]], "只有连通序出现"


def test_tail_orders_multiple_candidates_ranked():
    edges = {
        ("s0", "s1"): _attr_edge(),
        ("s0", "s2"): _attr_edge(),
        ("s1", "s2"): _attr_edge(),
        ("s2", "s1"): _attr_edge(),
    }
    ranked = evaluate_tail_orders(
        accumulated_card=100_000,
        tail_sources=[("s1", 50), ("s2", 100)],
        tail_edges=edges,
        ndv_by_source={"s1": {"k": 50}, "s2": {"k": 1_000_000}},
    )
    assert len(ranked) == 2
    assert ranked[0][0] <= ranked[1][0]
    # s2 的 NDV 极大 → 以 s2 为右（build+除数）的序基数骤减、更便宜
    assert ranked[0][1] == ["s1", "s2"]


def test_pick_tail_order_adopts_only_better():
    edges = {
        ("s0", "s1"): _attr_edge(),
        ("s0", "s2"): _attr_edge(),
        ("s1", "s2"): _attr_edge(),
        ("s2", "s1"): _attr_edge(),
    }
    c = AdaptiveController()
    new_tail, adopted = pick_tail_order(
        controller=c,
        original_tail=["s2", "s1"],  # 故意给次优序（s2 高 NDV 应作右除数）
        observed_first_card=100_000,
        tail_sources=[("s1", 50), ("s2", 100)],
        tail_edges=edges,
        ndv_by_source={"s1": {"k": 50}, "s2": {"k": 1_000_000}},
    )
    assert adopted is True
    assert new_tail == ["s1", "s2"]
    assert c.replans_used == 1
    # 预算耗尽 → 不再重排
    again, adopted2 = pick_tail_order(
        controller=c,
        original_tail=["s2", "s1"],
        observed_first_card=100_000,
        tail_sources=[("s1", 50), ("s2", 100)],
        tail_edges=edges,
        ndv_by_source={},
    )
    assert adopted2 is False and again == ["s2", "s1"]


def test_pick_tail_order_rejects_when_already_optimal():
    edges = {("s0", "s1"): {"kind": "attribute_join"}}
    c = AdaptiveController()
    tail, adopted = pick_tail_order(
        controller=c,
        original_tail=["s1"],
        observed_first_card=100,
        tail_sources=[("s1", 10)],
        tail_edges=edges,
        ndv_by_source={},
    )
    assert adopted is False and tail == ["s1"]
    assert c.replans_used == 0, "平凡尾（len<2）早退，不消耗一次性预算"


def test_single_tail_never_replans():
    c = AdaptiveController()
    tail, adopted = pick_tail_order(
        controller=c,
        original_tail=["only"],
        observed_first_card=10,
        tail_sources=[("only", 5)],
        tail_edges={},
        ndv_by_source={},
    )
    assert adopted is False and tail == ["only"]
    assert c.replans_used == 0


def test_entry_sources_gate_first_tail_hop():
    """M3（评审 R1）：首尾跳源必须从累积侧有向可达。"""
    edges = {("s3", "s2"): _attr_edge()}  # 尾内部邻接：s3→s2
    ranked = evaluate_tail_orders(
        accumulated_card=1000,
        tail_sources=[("s2", 100), ("s3", 100)],
        tail_edges=edges,
        ndv_by_source={},
        entry_sources={"s3"},  # 只有 s3 从累积侧可达
    )
    assert [o for _, o in ranked] == [["s3", "s2"]]


# ── 执行器集成（护栏路径）─────────────────────────────────────────────────


def test_executor_adaptive_observes_and_keeps_parity():
    """3 源链 + 离谱估计 → 触发观测；结果仍与 V5 逐位一致。"""
    from app.schemas.data_fabric_schema import QueryResult
    from app.services.data_fabric.query.federated.enumerator import (
        enumerate_federation,
    )
    from app.services.data_fabric.query.federated.planner import (
        build_enumeration_context,
    )
    from app.services.data_fabric.query.federated.executor import PhysicalExecutor
    from app.services.data_fabric.query.federation import (
        ChainJoin,
        ChainSource,
        FederatedChainRequest,
        FederatedExecutor,
    )
    from tests.unit.test_federated_physical_executor import _canon

    pts = [
        {"type": "Feature", "geometry": None,
         "properties": {"region": f"r{i}", "v": i}}
        for i in range(40)
    ]
    dims = [{"properties": {"region": f"r{i}", "label": "L"}} for i in range(40)]
    labels = [{"properties": {"label": "L", "tag": "t1"}}]

    class _A:
        def query(self, dataset_id, spec):
            data = {"pts": pts, "dims": dims, "labels": labels}
            feats = data[dataset_id]
            limit = spec.limit or 100
            offset = spec.offset or 0
            return QueryResult(dataset_id=dataset_id, features=feats[offset : offset + limit])

    adapters = {"sA": _A(), "sC": _A(), "sB": _A()}
    req = FederatedChainRequest(
        sources=[ChainSource(source_id="sA", dataset_id="pts"),
                 ChainSource(source_id="sC", dataset_id="dims"),
                 ChainSource(source_id="sB", dataset_id="labels")],
        joins=[ChainJoin(kind="attribute_join", join_field_left="region",
                         join_field_right="region",
                         left_source_id="sA", right_source_id="sC"),
               ChainJoin(kind="attribute_join", join_field_left="label",
                         join_field_right="label",
                         left_source_id="sC", right_source_id="sB")],
        limit=10_000,
    )
    v5 = FederatedExecutor(lambda sid: adapters.get(sid)).execute_chain(req)
    plan = enumerate_federation(build_enumeration_context(req))
    px = PhysicalExecutor(
        adapter_factory=lambda sid: adapters.get(sid),
        budget=req.budget, limit=req.limit, adaptive=True,
    )
    out = px.execute(plan.tree, hop_estimates=[1, 1])
    assert out["row_count"] == v5["row_count"] == 40
    assert _canon(out["rows"]) == _canon(v5["rows"])
    assert px.adaptive.replans_used <= 1, "护栏：最多一次 replan"
    assert any("deviated" in n for n in out["adaptive_observations"])


def test_executor_replan_adopts_cheaper_tail_with_edge_graph():
    """M3（评审 R1）：edge_specs 提供完整 join graph 时，尾重排真实可达，
    且替换后的跳序列执行正确。"""
    from app.schemas.data_fabric_schema import QueryResult
    from app.services.data_fabric.query.federated.executor import PhysicalExecutor
    from app.services.data_fabric.query.federated.logical import (
        LogicalJoin,
        LogicalScan,
    )
    from app.services.data_fabric.query.federation import (
        ChainJoin,
        ChainSource,
        FederatedChainRequest,
    )

    s0 = [{"properties": {"k": i, "v0": i}} for i in range(40)]
    s1 = [{"properties": {"k": i, "v1": i * 2}} for i in range(40)]
    s2 = [{"properties": {"k": 0, "v2": "small"}}]
    s3 = [{"properties": {"k": i, "v3": i * 3}} for i in range(500)]

    class _A:
        def query(self, dataset_id, spec):
            data = {"s0": s0, "s1": s1, "s2": s2, "s3": s3}
            feats = data[dataset_id]
            limit = spec.limit or 100
            offset = spec.offset or 0
            return QueryResult(dataset_id=dataset_id, features=feats[offset : offset + limit])

    adapters = {sid: _A() for sid in ("s0", "s1", "s2", "s3")}
    req = FederatedChainRequest(
        sources=[
            ChainSource(source_id="s0", dataset_id="s0", estimated_rows=40),
            ChainSource(source_id="s1", dataset_id="s1", estimated_rows=41),
            ChainSource(source_id="s2", dataset_id="s2", estimated_rows=42),
            ChainSource(source_id="s3", dataset_id="s3", estimated_rows=43),
        ],
        joins=[
            ChainJoin(kind="attribute_join", join_field_left="k", join_field_right="k",
                      left_source_id="s0", right_source_id="s1"),
            ChainJoin(kind="attribute_join", join_field_left="k", join_field_right="k",
                      left_source_id="s1", right_source_id="s2"),
            ChainJoin(kind="attribute_join", join_field_left="k", join_field_right="k",
                      left_source_id="s2", right_source_id="s3"),
        ],
        limit=10_000,
    )
    # 手工构造纯链树（无 limit 包装）：自适应只对链形计划生效
    est_by_sid = {"s0": 40, "s1": 40, "s2": 2, "s3": 500}

    def _scan(sid):
        return LogicalScan(
            source_id=sid, dataset_id=sid,
            fetch_limit=req.limit, estimated_rows=est_by_sid[sid],
        )

    tree = LogicalJoin(
        join_kind="attribute_join",
        join_field_left="k", join_field_right="k",
        left=LogicalJoin(
            join_kind="attribute_join",
            join_field_left="k", join_field_right="k",
            left=LogicalJoin(
                join_kind="attribute_join",
                join_field_left="k", join_field_right="k",
                left=_scan("s0"), right=_scan("s1"),
            ),
            right=_scan("s2"),
        ),
        right=_scan("s3"),
    )
    px = PhysicalExecutor(
        adapter_factory=lambda sid: adapters.get(sid),
        budget=req.budget, limit=req.limit,
        adaptive=True, order_strategy="cost",
        ndv_hints={"s2": {"k": 1}, "s3": {"k": 500}},
    )
    edge_specs = {(j.left_source_id, j.right_source_id): j for j in req.joins}
    edge_specs[("s1", "s3")] = req.joins[2]   # 额外可达边（join graph 超集）
    edge_specs[("s3", "s2")] = req.joins[1]
    out = px.execute(tree, hop_estimates=[1, 1, 1], edge_specs=edge_specs)
    assert px.adaptive.replans_used == 1
    assert any("adopted" in n for n in px.adaptive.notes)
    assert out["row_count"] == 1  # s2 只有 k=0 一行 → 最终 1 行
    row = out["rows"][0]
    # V5 行语义：每跳覆盖 __right__（末跳右源 = s2），中间右表属性不展平
    assert row["v0"] == 0
    assert row["__right__"] == {"k": 0, "v2": "small"}
