"""resource-aware 候选选择测试（ADR-0204 D3：排序消费统一估算）。

覆盖 direction 7 必测面：
- exact vs approximate：资源压力下 heavy 候选排序下沉（live registry +
  合成图两路）
- memory pressure 翻转 + semantic-changing disclosure
- 确定性 + kill-switch 回退 latency-only
- capability_resolution._COST_RANK 兑现（原空挂）
"""
import pytest

from app.services.gis_harness import candidate_planner_v8 as cp
from app.services.gis_harness.capability_graph import (
    KIND_CAPABILITY,
    KIND_TOOL,
    GraphNode,
)
from app.services.gis_harness.capability_resolution import _provider_candidates
from app.services.gis_harness.qualification_v8 import (
    QualificationContext,
    QualificationResult,
    QualificationStatus,
)


class _StubGraph:
    """plan_candidates_v8 / _provider_candidates 所需的最小图协议。"""

    def __init__(self, cap_id, tools):
        self._cap_key = f"{KIND_CAPABILITY}:{cap_id}"
        self._tools = tools
        self._by_key = {f"{n.kind}:{n.id}": n for n in tools}

    def node(self, key):
        if key == self._cap_key:
            return GraphNode("cap", KIND_CAPABILITY, "stub")
        return self._by_key.get(key)

    def has(self, kind, id_):
        return f"{kind}:{id_}" in self._by_key

    def tools_for_capability(self, _cap):
        return [n.id for n in self._tools if n.kind == KIND_TOOL]

    def models_for_capability(self, _cap):
        return [n for n in self._tools if n.kind != KIND_TOOL]


def _tool(name, latency, memory, cost="light") -> GraphNode:
    return GraphNode(name, KIND_TOOL, "stub", extras={
        "latency_class": latency, "memory_class": memory, "cost": cost,
    })


CTX = QualificationContext()


# ── live registry：真实多 provider 能力 ──────────────────────────────────


def _live_plan(cap, pressure):
    return cp.plan_candidates_v8(
        cap, QualificationContext(), resource_pressure=pressure)


def _require_live_capability(cap: str) -> None:
    """live registry 缺该能力（CI 瘦环境）→ skip 而非空候选空洞通过。"""
    g = cp.get_capability_graph()
    if g.node(f"{KIND_CAPABILITY}:{cap}") is None:
        pytest.skip(f"capability {cap} absent in live registry")


def test_admin_boundary_query_pressure_sinks_heavy_provider():
    """admin_boundary_query：压力 0→1，slow/heavy 的 query_osm_boundary
    排序下沉；轻内存 get_district 稳居前列。"""
    _require_live_capability("admin_boundary_query")
    plan0 = _live_plan("admin_boundary_query", 0.0)
    plan1 = _live_plan("admin_boundary_query", 1.0)
    by_id0 = {c.id: c for c in plan0.candidates}
    by_id1 = {c.id: c for c in plan1.candidates}
    if "query_osm_boundary" not in by_id0:
        pytest.skip("query_osm_boundary not eligible in live registry")
    # 压力增量按内存档分化：heavy(slow provider) 罚 2.0，medium 罚 1.0
    heavy_delta = by_id1["query_osm_boundary"].score - \
        by_id0["query_osm_boundary"].score
    light_delta = by_id1["get_district"].score - \
        by_id0["get_district"].score
    assert heavy_delta == pytest.approx(2.0 * by_id1["query_osm_boundary"].memory_rank)
    assert light_delta < heavy_delta
    # 排序不因压力改善任何候选的相对位置（heavy 只会下沉或不动）
    ids0 = [c.id for c in plan0.candidates]
    ids1 = [c.id for c in plan1.candidates]
    assert ids1.index("query_osm_boundary") >= ids0.index("query_osm_boundary")
    # 排序因子披露：pressure_term + estimate 摘要
    heavy = by_id1["query_osm_boundary"]
    assert heavy.factors.get("pressure_term", 0) > 0
    assert heavy.estimate and heavy.estimate["memory_class"] in (
        "medium", "heavy")


def test_dataset_ingest_materialize_vs_ingest_pressure_flip():
    """dataset_ingest：materialize(medium) vs ingest(heavy) —— 压力下
    heavy 下沉（exact vs approximate 的资源面选择披露）。"""
    _require_live_capability("dataset_ingest")
    plan0 = _live_plan("dataset_ingest", 0.0)
    plan1 = _live_plan("dataset_ingest", 1.0)
    by_id0 = {c.id: c for c in plan0.candidates}
    by_id1 = {c.id: c for c in plan1.candidates}
    if {"materialize_dataset", "ingest_dataset"} - set(by_id0):
        pytest.skip("dataset_ingest providers not eligible in live registry")
    heavy0, heavy1 = by_id0["ingest_dataset"], by_id1["ingest_dataset"]
    light0, light1 = by_id0["materialize_dataset"], by_id1["materialize_dataset"]
    # 压力罚分随内存档分化（heavy 增量严格大于 medium）
    assert (heavy1.score - heavy0.score) > (light1.score - light0.score)
    assert heavy1.memory_rank > light1.memory_rank
    # 压力下 heavy 相对位次不下沉到 medium 之后才反直觉 —— 至少不改善
    ids0 = [c.id for c in plan0.candidates]
    ids1 = [c.id for c in plan1.candidates]
    assert ids1.index("ingest_dataset") >= ids0.index("ingest_dataset")


def test_planner_selection_is_deterministic_and_eligible_only():
    _require_live_capability("crs_transformation")
    plan_a = _live_plan("crs_transformation", 0.5)
    plan_b = _live_plan("crs_transformation", 0.5)
    assert [(c.kind, c.id, c.score) for c in plan_a.candidates] == \
        [(c.kind, c.id, c.score) for c in plan_b.candidates]
    assert all(c.qualification.status != QualificationStatus.INELIGIBLE
               for c in plan_a.candidates)


def test_synthetic_pressure_flip_backstops_live_registry():
    """P2-10 兜底：合成图压力翻转（不依赖 live registry；CI 环境保底）。"""
    graph = _StubGraph("flip_cap", [
        _tool("lean_path", "fast", "light"),
        _tool("heavy_path", "fast", "heavy"),
    ])
    plan0 = cp.plan_candidates_v8("flip_cap", CTX, graph=graph,
                                  resource_pressure=0.0)
    plan1 = cp.plan_candidates_v8("flip_cap", CTX, graph=graph,
                                  resource_pressure=1.0)
    assert plan0.candidates[0].id == "lean_path"
    assert plan1.candidates[0].id == "lean_path"
    # 压力把 heavy 候选压到 worse score + 显式 pressure_term 因子
    heavy0 = next(c for c in plan0.candidates if c.id == "heavy_path")
    heavy1 = next(c for c in plan1.candidates if c.id == "heavy_path")
    assert heavy1.score - heavy0.score == pytest.approx(2.0)  # 2×1×rank(1.0)
    assert heavy1.factors["pressure_term"] == pytest.approx(2.0)
    # 无压力时排序因子不含 pressure_term（恒定项仍披露）
    assert "pressure_term" not in heavy0.factors
    assert heavy0.estimate is not None and heavy0.estimate["memory_class"] == "heavy"


def test_kill_switch_falls_back_to_latency_only(monkeypatch):
    _require_live_capability("admin_boundary_query")
    monkeypatch.setenv(cp.RESOURCE_RANK_ENV, "0")
    plan = _live_plan("admin_boundary_query", 1.0)
    assert plan.resource_pressure is None
    assert all(not c.factors.get("pressure_term") for c in plan.candidates)
    assert all(c.estimate is None for c in plan.candidates)


def test_auto_pressure_defaults_neutral(monkeypatch):
    """resource_pressure 缺省 → governor live 内存推导（空账本 → 0）。"""
    _require_live_capability("crs_transformation")
    from app.services.governor.governor import reset_governor_for_tests
    reset_governor_for_tests()
    plan = _live_plan("crs_transformation", None)
    assert plan.resource_pressure == 0.0


# ── 合成图：语义披露与解析面 cost rank ───────────────────────────────────


def test_degraded_candidate_discloses_approximate_semantics(monkeypatch):
    """degraded 候选入选 → candidate/plan 显式 approximate 披露。"""
    graph = _StubGraph("demo_cap", [
        _tool("exact_path", "fast", "light"),
        _tool("fallback_path", "fast", "light"),
    ])

    def fake_qualify(node, ctx, g=None):
        if node.id == "fallback_path":
            return QualificationResult(QualificationStatus.DEGRADED, [])
        return QualificationResult(QualificationStatus.ELIGIBLE, [])

    monkeypatch.setattr(cp, "qualify_node", fake_qualify)
    plan = cp.plan_candidates_v8("demo_cap", CTX, graph=graph,
                                 resource_pressure=0.0)
    assert plan.worst_semantics == "approximate"
    by_id = {c.id: c for c in plan.candidates}
    assert by_id["fallback_path"].semantics == "approximate"
    assert by_id["exact_path"].semantics is None


def test_provider_candidates_cost_rank_wired(monkeypatch):
    """capability_resolution：等 latency 下 heavy 内存 provider 下沉 +
    factors 披露 cost_rank（_COST_RANK 空挂修复）。"""
    graph = _StubGraph("cap", [
        _tool("light_one", "fast", "light"),
        _tool("heavy_one", "fast", "heavy"),
    ])
    ranked, rejected = _provider_candidates("cap", CTX, graph)
    assert [c.id for c in ranked][0] == "light_one"
    factors = {c.id: c.factors for c in ranked}
    assert factors["heavy_one"]["cost_rank"] > factors["light_one"]["cost_rank"]
    assert factors["light_one"]["cost_rank"] == pytest.approx(0.0)
    monkeypatch.setenv(cp.RESOURCE_RANK_ENV, "0")
    ranked_off, _ = _provider_candidates("cap", CTX, graph)
    assert all("cost_rank" not in c.factors for c in ranked_off)
