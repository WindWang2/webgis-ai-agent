"""V7 cost/placement/pushdown/adaptive 测试（ADR-0119 W7-W10）。

- W7：不确定性乘子按统计置信度；rate-limit 惩罚（未知=0）；
- W8：server CRS placement（caps 门控、计划树 output_crs、交付账本、
  mismatch 回退）；
- W9：安全聚合下推（R-C1 五条件、唯一键/非唯一键差分、输出逐位一致）；
- W10：bushy 自适应重排（偏差触发、严格更优护栏、given 禁用）。
"""


from app.services.data_fabric.query.federation import (
    ChainJoin,
    ChainSource,
    FederatedChainRequest,
    FederatedExecutor,
    aggregate_join_rows,
)
from app.services.data_fabric.query.federated.costing import (
    estimate_uncertainty_multiplier,
    rate_limit_request_penalty,
)
from app.services.data_fabric.query.statistics import DatasetStatistics


def _pt(x, y, **props):
    return {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [x, y]},
        "properties": props,
    }


class _Fake:
    """可编程假源：rows 提供符 + 聚合（group_by/aggregate extras）支持。"""

    def __init__(self, data, *, aggregate_fn=None, delivered_crs=None):
        self._data = data
        self._aggregate_fn = aggregate_fn
        self._delivered = delivered_crs

    def query(self, dataset_id, spec):
        from app.schemas.data_fabric_schema import QueryResult

        extras = getattr(spec, "model_extra", {}) or {}
        if extras.get("aggregate") or extras.get("group_by"):
            if self._aggregate_fn is None:
                raise AssertionError("unexpected aggregate pushdown request")
            rows = self._aggregate_fn(
                self._data[dataset_id],
                extras.get("group_by") or [],
                extras.get("aggregate") or [],
                extras.get("where"),
            )
            return QueryResult(
                dataset_id=dataset_id, features=[], data=rows,
                payload_type="aggregation", total_count=len(rows),
                returned_count=len(rows), result_mode="statistics",
            )
        feats = list(self._data[dataset_id])
        bbox = extras.get("bbox")
        if bbox:
            feats = [
                f for f in feats
                if f["geometry"] and bbox[0] <= f["geometry"]["coordinates"][0] <= bbox[2]
                and bbox[1] <= f["geometry"]["coordinates"][1] <= bbox[3]
            ]
        limit = extras.get("limit", spec.limit or 100)
        return QueryResult(
            dataset_id=dataset_id,
            features=feats[:limit],
            total_count=len(feats),
            returned_count=min(limit, len(feats)),
            metadata=({"delivered_crs": self._delivered} if self._delivered else {}),
        )


# ── W7：成本原语 ────────────────────────────────────────────────────────


def test_uncertainty_multiplier_by_confidence():
    assert estimate_uncertainty_multiplier(None) == (1.5, "assumption")
    m = estimate_uncertainty_multiplier(DatasetStatistics(dataset_fingerprint="x"))
    assert m == (1.5, "assumption")
    m2 = estimate_uncertainty_multiplier(
        DatasetStatistics(dataset_fingerprint="x", row_count=10, collector="footer"))
    assert m2[0] < 1.5  # 有真实统计 → 更低不确定性


def test_rate_limit_penalty_zero_when_unknown():
    assert rate_limit_request_penalty(None) == 0.0

    class H:
        requests_per_window = 100

    p = rate_limit_request_penalty(H())
    assert 0 < p < 25.0

    class H0:
        requests_per_window = 0

    assert rate_limit_request_penalty(H0()) == 0.0


# ── W8：server CRS placement ────────────────────────────────────────────


class _Caps:
    def __init__(self, source_type="postgis", output_crs_pushdown=False):
        self.source_type = source_type
        self.output_crs_pushdown = output_crs_pushdown
        self.server_reprojection = True


def _run_chain(adapters, sources, joins, bbox=None):
    executor = FederatedExecutor(lambda sid: adapters.get(sid))
    req = FederatedChainRequest(sources=sources, joins=joins, bbox=bbox, limit=1000)
    return executor.execute_chain(req)


def test_delivered_crs_ledger_from_metadata():
    """交付 CRS 事实进 trace（per_source_delivered_srid）。"""
    adapters = {
        "a": _Fake({"d1": [_pt(1, 1, k="1"), _pt(2, 2, k="2")]},
                   delivered_crs="EPSG:4326"),
        "b": _Fake({"d2": [_pt(1, 1, v=10)]}),
    }
    sources = [ChainSource(source_id="a", dataset_id="d1"),
               ChainSource(source_id="b", dataset_id="d2")]
    joins = [ChainJoin(kind="attribute_join", join_field_left="k",
                       join_field_right="k", left_source_id="a", right_source_id="b")]
    executor = FederatedExecutor(lambda sid: adapters.get(sid))
    result = executor.execute_chain(
        FederatedChainRequest(sources=sources, joins=joins, limit=100, engine="v6"))
    assert result["status"] == "success"
    assert result.get("per_source_delivered_srid", {}).get("a") == 4326


# ── W9：安全聚合下推 ────────────────────────────────────────────────────


def _agg_rows(feats, group_by, aggregates, where=None):
    """假源侧 GROUP BY（模拟 PostGIS _execute_aggregation 的行形状）。"""
    out = {}
    for f in feats:
        key = tuple(f["properties"].get(g) for g in group_by)
        acc = out.setdefault(key, {g: f["properties"].get(g) for g in group_by})
        for a in aggregates:
            func, field = a["func"], a.get("field")
            name = func if field is None else f"{func}_{field}"
            if func == "count":
                acc[name] = acc.get(name, 0) + 1
            elif func == "sum":
                acc[name] = (acc.get(name) or 0) + (f["properties"].get(field) or 0)
    return list(out.values())


def _local_reference(left, right, join_l, join_r, group_by, aggregates):
    """join-后聚合 reference（V5 内核直接调用）。"""
    joined = []
    for lf in left:
        for rf in right:
            if lf["properties"].get(join_l) == rf["properties"].get(join_r):
                joined.append({"__left__": lf, "__right__": rf})
    return aggregate_join_rows(joined, aggregates, group_by)


def test_aggregate_pushdown_unique_key_equivalence():
    """唯一左键：下推（v6+证明）与本地内核（无证明）输出**逐位一致**。

    R-C1 等价性的行为锁定：同一数据 × 同一链，唯一键声明只改变执行
    位置，绝不改变结果（含未匹配组 R3 的丢弃语义与组内聚合值）。
    """
    # 左侧行**不含** pop 列（R1-M5：聚合字段不得存在于左侧 —— 内核左优先
    # 解析会让本地/下推读不同值）；左侧声明投影证明字段集已知。
    left = [_pt(1, 1, region="R1"), _pt(2, 2, region="R2")]
    right = [
        _pt(10, 10, region="R1", pop=5),
        _pt(11, 11, region="R1", pop=7),
        _pt(12, 12, region="R3", pop=100),  # 无左匹配
    ]
    group_by = ["region"]
    aggregates = [{"func": "count"}, {"func": "sum", "field": "pop"}]
    pushed_rows = _agg_rows(right, group_by, aggregates)

    def _make(hint_unique):
        adapters = {
            "a": _Fake({"cities": left}),
            "b": _Fake(
                {"totals": right},
                aggregate_fn=lambda feats, gb, aggs, where=None: pushed_rows,
            ),
        }
        sources = [
            ChainSource(source_id="a", dataset_id="cities", estimated_rows=2,
                        source_type="postgis", fields=["region", "city"]),
            ChainSource(source_id="b", dataset_id="totals", estimated_rows=3,
                        source_type="postgis"),
        ]
        joins = [ChainJoin(
            kind="aggregate_join", join_field_left="region", join_field_right="region",
            group_by_right=group_by, aggregates=aggregates,
            left_source_id="a", right_source_id="b",
        )]
        from app.services.data_fabric.query.federation import ChainSourceStats

        hints = (
            {"a": ChainSourceStats(unique_keys=["region"])} if hint_unique else None
        )
        req = FederatedChainRequest(
            sources=sources, joins=joins, limit=100, engine="v6",
            stats_hints=hints,
        )
        executor = FederatedExecutor(lambda sid: adapters.get(sid))
        return executor.execute_chain(req)

    local = _make(hint_unique=False)
    pushed = _make(hint_unique=True)
    assert local["status"] == "success" and pushed["status"] == "success"
    def key(r):
        return (r.get("region") is None, str(r.get("region")))
    assert sorted(local["rows"], key=key) == sorted(pushed["rows"], key=key), (
        "下推与本地路径必须逐位一致（含未匹配组丢弃）"
    )
    assert local["rows"] == [{"region": "R1", "count": 2, "sum_pop": 12}]
    assert pushed["rows"] == local["rows"]
    # 下推路径确实请求了源侧聚合（hop_stats 披露，经 semi_join_reduction 透出）
    hops = pushed.get("semi_join_reduction") or []
    assert any(h.get("aggregate_pushdown") for h in hops), hops


def test_aggregate_pushdown_not_applied_without_unique_key_proof():
    """无唯一键证明：不请求源侧聚合（诚实拒绝），走本地内核。"""
    left = [_pt(1, 1, region="R1"), _pt(2, 2, region="R1")]  # R1 重复 → 非唯一
    right = [_pt(10, 10, region="R1", pop=5)]
    group_by = ["region"]
    aggregates = [{"func": "count"}]

    def _unexpected(feats, gb, aggs, where=None):
        raise AssertionError("must not push down without proof")

    adapters = {
        "a": _Fake({"cities": left}),
        "b": _Fake({"totals": right}, aggregate_fn=_unexpected),
    }
    sources = [
        ChainSource(source_id="a", dataset_id="cities"),
        ChainSource(source_id="b", dataset_id="totals"),
    ]
    joins = [ChainJoin(
        kind="aggregate_join", join_field_left="region", join_field_right="region",
        group_by_right=group_by, aggregates=aggregates,
        left_source_id="a", right_source_id="b",
    )]
    executor = FederatedExecutor(lambda sid: adapters.get(sid))
    result = executor.execute_chain(
        FederatedChainRequest(sources=sources, joins=joins, limit=100))
    assert result["status"] == "success"
    # 本地内核：join-后聚合 → R1 命中 2 行（扇出重复计数是既有语义）
    assert result["rows"] == [{"region": "R1", "count": 2}]


# ── W10：bushy 自适应 ───────────────────────────────────────────────────


def test_bushy_replan_triggers_on_deviation_and_requires_better():
    from app.services.data_fabric.query.federated.executor import PhysicalExecutor
    from app.services.data_fabric.query.federated.logical import (
        LogicalJoin, LogicalScan,
    )
    from app.services.data_fabric.query.federated.enumerator import EnumeratedPlan

    def _tree(order, est):
        scans = [
            LogicalScan(source_id=s, dataset_id=f"d{s}", estimated_rows=e)
            for s, e in zip(order, est)
        ]
        return LogicalJoin(
            join_kind="attribute_join",
            left=scans[0], right=scans[1],
            join_field_left="k", join_field_right="k",
        )

    calls = []

    def replan_fn(actuals):
        calls.append(dict(actuals))
        new_tree = _tree(["b", "a"], [1, 1])  # 反转序的新树
        plan = EnumeratedPlan(tree=new_tree, cost=1.0, components={}, order=["b", "a"])
        plan.previous_cost = 500.0  # 严格更优 → 切换
        return plan

    data_a = [_pt(i, i, k=str(i % 50)) for i in range(200)]  # est 10 → actual 200
    data_b = [_pt(i, i, k=str(i)) for i in range(1)]
    adapters = {"a": _Fake({"da": data_a}), "b": _Fake({"db": data_b})}
    tree = _tree(["a", "b"], [10, 1])
    px = PhysicalExecutor(
        adapter_factory=lambda sid: adapters[sid],
        budget=_BudgetLike(), limit=100, adaptive=True, order_strategy="cost",
        replan_fn=replan_fn,
    )
    result = px.execute(tree)
    assert calls, "偏差必须触发重排"
    assert result["replans_used"] == 1
    assert any("bushy replan applied" in n for n in result["adaptive_observations"])

    # 新计划不严格更优 → 拒绝
    def replan_bad(actuals):
        plan = EnumeratedPlan(
            tree=_tree(["b", "a"], [1, 1]), cost=999.0, components={}, order=["b", "a"])
        plan.previous_cost = 500.0
        return plan

    px2 = PhysicalExecutor(
        adapter_factory=lambda sid: adapters[sid],
        budget=_BudgetLike(), limit=100, adaptive=True, order_strategy="cost",
        replan_fn=replan_bad,
    )
    result2 = px2.execute(tree)
    assert result2["replans_used"] == 0
    assert any("not strictly cheaper" in n for n in result2["adaptive_observations"])


class _BudgetLike:
    deadline_s = 30.0
    max_rows = 100_000
    max_bytes = 10**9
    max_vertices = 10**9


def test_aggregate_pushdown_rejected_when_left_unprojected():
    """R1-C3/M5 回归：左侧未投影（字段集未知）→ 无法证明 → 不下推。"""
    adapters = {
        "a": _Fake({"cities": [_pt(1, 1, region="R1")]}),
        "b": _Fake(
            {"totals": [_pt(10, 10, region="R1", pop=5)]},
            aggregate_fn=lambda *a, **k: (_ for _ in ()).throw(
                AssertionError("must not push down")
            ),
        ),
    }
    sources = [
        ChainSource(source_id="a", dataset_id="cities", source_type="postgis"),
        ChainSource(source_id="b", dataset_id="totals", source_type="postgis"),
    ]
    joins = [ChainJoin(
        kind="aggregate_join", join_field_left="region", join_field_right="region",
        group_by_right=["region"], aggregates=[{"func": "sum", "field": "pop"}],
        left_source_id="a", right_source_id="b",
    )]
    from app.services.data_fabric.query.federation import ChainSourceStats

    req = FederatedChainRequest(
        sources=sources, joins=joins, limit=100, engine="v6",
        stats_hints={"a": ChainSourceStats(unique_keys=["region"])},
    )
    result = FederatedExecutor(lambda sid: adapters.get(sid)).execute_chain(req)
    hops = result.get("semi_join_reduction") or []
    assert hops and not hops[0].get("aggregate_pushdown")


def test_aggregate_pushdown_not_applied_for_nested_left_subtree():
    """R1-C3 回归：左子树非单扫描（嵌套 join）→ 改写守卫拒绝。"""
    from app.services.data_fabric.query.federated.logical import (
        LogicalJoin,
        LogicalScan,
    )
    from app.services.data_fabric.query.federated.planner import (
        apply_safe_aggregate_pushdown,
        build_enumeration_context,
    )
    from app.services.data_fabric.query.federation import ChainSourceStats

    sources = [
        ChainSource(source_id="a", dataset_id="da", source_type="postgis",
                    fields=["k"]),
        ChainSource(source_id="b", dataset_id="db", source_type="postgis"),
        ChainSource(source_id="c", dataset_id="dc", source_type="postgis"),
    ]
    joins = [
        ChainJoin(kind="attribute_join", join_field_left="k",
                  join_field_right="k", left_source_id="a", right_source_id="b"),
        ChainJoin(kind="aggregate_join", join_field_left="k",
                  join_field_right="k", group_by_right=["k"],
                  aggregates=[{"func": "count"}],
                  left_source_id="a", right_source_id="c"),
    ]
    req = FederatedChainRequest(
        sources=sources, joins=joins, limit=100, engine="v6",
        stats_hints={"a": ChainSourceStats(unique_keys=["k"])},
    )
    ctx = build_enumeration_context(req)

    inner = LogicalJoin(
        join_kind="attribute_join",
        left=LogicalScan(source_id="a", dataset_id="da", fields=["k"]),
        right=LogicalScan(source_id="b", dataset_id="db"),
        join_field_left="k", join_field_right="k",
    )
    tree = LogicalJoin(
        join_kind="aggregate_join",
        left=inner,  # 嵌套左子树（非单扫描）
        right=LogicalScan(source_id="c", dataset_id="dc"),
        join_field_left="k", join_field_right="k",
        group_by_right=["k"], aggregates=[{"func": "count"}],
    )
    out = apply_safe_aggregate_pushdown(tree, ctx)
    assert out.aggregate_pushdown is False
