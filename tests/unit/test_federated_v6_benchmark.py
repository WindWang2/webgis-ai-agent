"""V6 联邦基准（ADR-0118 W12）：结构性断言，不设整机 wall-clock 门。

度量维度（全部可解释、可复现）：
- **transferred rows**（Σ per_source_rows）：下推 on/off、semi-join/bloom on/off；
- **remote requests**（adapter 调用数 = 分页数）：N 源缩放；
- **build 侧行数**：硬界 MAX_JOIN_CANDIDATES 消费；
- **基数估计质量**：估计 vs 实际（adaptive 观测输入）。

wall-clock 只记录在结果里供人工参考，绝不作为断言门（与仓库 perf 红线一致）。
"""
import threading
import time

from app.schemas.data_fabric_schema import QueryResult
from app.services.data_fabric.query.federated.bloom import (
    BloomFilter,
    semi_join_plan,
)
from app.services.data_fabric.query.federated.enumerator import (
    EnumerationContext,
    JoinEdge,
    SourceFacts,
    enumerate_federation,
)
from app.services.data_fabric.query.federation import (
    ChainJoin,
    ChainSource,
    FederatedChainRequest,
    FederatedExecutor,
    MAX_JOIN_CANDIDATES,
)
from app.services.data_fabric.query.federated.planner import plan_federation_v6
from app.services.data_fabric.query.federated.executor import PhysicalExecutor


class _CountingAdapter:
    """计数型假适配器：记录调用次数（= 远端请求数）与返回行数。"""

    def __init__(self, datasets, latency_s: float = 0.0):
        self._datasets = datasets
        self.calls = 0
        self.rows_served = 0
        self._latency_s = latency_s
        self._lock = threading.Lock()

    def query(self, dataset_id, spec):
        with self._lock:
            self.calls += 1
        feats = self._datasets[dataset_id]
        limit = spec.limit or 100
        offset = spec.offset or 0
        page = feats[offset : offset + limit]
        self.rows_served += len(page)
        if self._latency_s:
            time.sleep(self._latency_s)  # 仅记录用；断言不消费 wall-clock
        return QueryResult(dataset_id=dataset_id, features=page)


def _mk_pts(n, regions=8):
    return [
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [100.0 + (i % 40) * 0.1, 20.0 + (i % 20) * 0.1]},
            "properties": {"region": f"R{i % regions}", "k": i % 50, "v": i},
        }
        for i in range(n)
    ]


def _run_v6(adapters, sources, joins, **kw):
    req = FederatedChainRequest(
        sources=sources, joins=joins, limit=10_000, engine="v6", **kw)
    plan = plan_federation_v6(req)
    px = PhysicalExecutor(
        adapter_factory=lambda sid: adapters.get(sid),
        budget=req.budget, limit=req.limit, adaptive=True,
        order_strategy=req.order_strategy)
    out = px.execute(plan.tree)
    return plan, out


# ── 1. N 源请求缩放（请求次数结构性有界）────────────────────────────────────


def test_n_source_request_scaling():
    ds = {"pts": _mk_pts(600), "dims": [
        {"properties": {"region": f"R{k}", "label": f"L{k}"}} for k in range(8)]}
    a = _CountingAdapter(ds)
    adapters = {"sA": a, "sC": a}
    sources = [
        ChainSource(source_id="sA", dataset_id="pts", estimated_rows=600),
        ChainSource(source_id="sC", dataset_id="dims", estimated_rows=8),
    ]
    joins = [ChainJoin(kind="attribute_join", join_field_left="region",
                       join_field_right="region",
                       left_source_id="sA", right_source_id="sC")]
    plan, out = _run_v6(adapters, sources, joins)
    assert out["row_count"] == 600
    # 分页请求有界：页大小 2000 → 600 行 = 每源 1 页
    assert a.calls <= 4, f"两源 600 行不应超过 4 次请求（实际 {a.calls}）"
    assert out["pages_fetched"] == a.calls


# ── 2. Bloom 盈利路径：右侧行显著约减 ──────────────────────────────────────


def test_bloom_profitability_and_reduction():
    # 左 100k 键 / 右 10k 行：约减 50% 的节省字节（9MB）≥ 构建成本（3.2MB）×2
    plan = semi_join_plan(left_card=100_000, right_card=10_000,
                          ndv_left=5_000, ndv_right=10_000)
    assert plan.enabled and plan.estimated_reduction_ratio > 0.4
    # 1M 键的构建成本（32B/键 ≈ 32MB）超过节省 → 诚实拒绝（不亏本预滤）
    huge = semi_join_plan(left_card=1_000_000, right_card=10_000,
                          ndv_left=5_000, ndv_right=10_000)
    assert not huge.enabled
    bloom = BloomFilter.with_capacity(expected_keys=100_000)
    for i in range(0, 100_000, 100):  # 采样 1000 键
        bloom.add(i)
    assert bloom.num_bits <= 1 << 20, "位图结构性上界（≤128Kb）"
    # 边际约减不启用（构建成本不划算）
    marginal = semi_join_plan(left_card=10, right_card=10,
                              ndv_left=9, ndv_right=10)
    assert not marginal.enabled


# ── 3. 选择率下推收益：过滤让传输行下降 ────────────────────────────────────


def test_selective_filter_reduces_transferred_rows():
    from app.services.data_fabric.query.predicates import predicate_from_dict

    # 有过滤提示 vs 无提示（成本面差异：扫描行估计下降）
    def _ctx(where):
        return EnumerationContext(
            sources=[SourceFacts(source_id="sA", dataset_id="pts",
                                 estimated_rows=2000, where=where),
                     SourceFacts(source_id="sX", dataset_id="dims",
                                 estimated_rows=40)],
            joins=[JoinEdge(left_source_id="sA", right_source_id="sX",
                            kind="attribute_join", join_field_left="region",
                            join_field_right="region")],
            limit=10_000,
        )

    p1 = enumerate_federation(_ctx(None))
    p2 = enumerate_federation(_ctx(
        predicate_from_dict({"op": "eq", "field": "region", "value": "R1"})))
    # 有过滤提示的计划成本应严格更低（扫描行 = 行数 × 选择率）
    assert p2.cost < p1.cost


# ── 4. build 侧硬界消费 ────────────────────────────────────────────────────


def test_build_side_cap_respected():
    ds = {"pts": _mk_pts(30), "big": _mk_pts(5000, regions=50)}
    a = _CountingAdapter(ds)
    adapters = {"sA": a, "sB": a}
    sources = [
        ChainSource(source_id="sA", dataset_id="pts", estimated_rows=30),
        ChainSource(source_id="sB", dataset_id="big", estimated_rows=5000),
    ]
    joins = [ChainJoin(kind="attribute_join", join_field_left="k",
                       join_field_right="k",
                       left_source_id="sA", right_source_id="sB")]
    plan, out = _run_v6(adapters, sources, joins)
    # build 侧（sB）fetch 窗口 = req.limit（V5 奇偶），行数被硬界约束
    scan_b = _find_scan(plan.tree, "sB")
    assert scan_b is not None
    assert scan_b.fetch_limit <= 10_000
    assert out["per_source_rows"]["sB"] <= MAX_JOIN_CANDIDATES


def _find_scan(node, source_id):
    from app.services.data_fabric.query.federated.logical import (
        LogicalJoin,
        LogicalReproject,
        LogicalScan,
    )

    if isinstance(node, LogicalScan):
        return node if node.source_id == source_id else None
    if isinstance(node, LogicalJoin):
        return _find_scan(node.left, source_id) or _find_scan(node.right, source_id)
    if isinstance(node, LogicalReproject):
        return _find_scan(node.input, source_id)
    return None


# ── 5. 延迟模拟：请求数不因自适应而放大 ────────────────────────────────────


def test_adaptive_does_not_amplify_remote_requests():
    ds = {"pts": _mk_pts(400), "dims": [
        {"properties": {"region": f"R{k % 8}", "label": f"L{k % 4}"}} for k in range(16)]}
    a = _CountingAdapter(ds, latency_s=0.0)  # 模拟延迟不进断言
    adapters = {"sA": a, "sC": a, "sB": a}
    # 提示升序 == given 序（V5 cost 排序保序；V6 DP 沿方向边同序）
    sources = [
        ChainSource(source_id="sA", dataset_id="pts", estimated_rows=400),
        ChainSource(source_id="sC", dataset_id="dims", estimated_rows=800),
        ChainSource(source_id="sB", dataset_id="dims", estimated_rows=1600),
    ]
    joins = [
        ChainJoin(kind="attribute_join", join_field_left="region",
                  join_field_right="region",
                  left_source_id="sA", right_source_id="sC"),
        ChainJoin(kind="attribute_join", join_field_left="label",
                  join_field_right="label",
                  left_source_id="sC", right_source_id="sB"),
    ]
    plan, out = _run_v6(adapters, sources, joins)
    # 每源至多 1 页 + V5 对照：请求数同量级（自适应绝不放大网络扇出）
    v5 = FederatedExecutor(lambda sid: adapters.get(sid)).execute_chain(
        FederatedChainRequest(sources=sources, joins=joins, limit=10_000))
    assert out["row_count"] == v5["row_count"]
    assert a.calls <= 8, f"3 源 400 行请求有界（实际 {a.calls}）"
    assert out.get("replans_used", 0) <= 1


# ── 6. 基数估计质量：估计与实际同数量级（诚实提示）─────────────────────────


def test_cardinality_estimates_in_right_order_of_magnitude():
    ds = {"pts": _mk_pts(1000), "dims": [
        {"properties": {"region": f"R{k}"}} for k in range(10)]}
    a = _CountingAdapter(ds)
    adapters = {"sA": a, "sC": a}
    sources = [
        ChainSource(source_id="sA", dataset_id="pts", estimated_rows=1000),
        ChainSource(source_id="sC", dataset_id="dims", estimated_rows=10),
    ]
    joins = [ChainJoin(kind="attribute_join", join_field_left="region",
                       join_field_right="region",
                       left_source_id="sA", right_source_id="sC")]
    plan, out = _run_v6(adapters, sources, joins)
    est = plan.components.get("join", {}).get("card")
    actual = out["row_count"]
    # NDV 模型：est = 1000×10/10 = 1000 vs actual 1000 —— 同数量级（≤16×）
    assert est is not None and est > 0
    ratio = max(est, actual) / max(1, min(est, actual))
    assert ratio <= 16, f"基数估计偏差 x{ratio:.1f} 超出诚实范围（est={est}, actual={actual}）"
