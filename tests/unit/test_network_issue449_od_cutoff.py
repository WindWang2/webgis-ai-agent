"""Regression tests for issue #449 (P2 perf): OD matrix ran one FULL-TREE
``nx.single_source_dijkstra`` per unique origin — materializing the complete
path list for every reachable node, O(sum|path|) ≈ O(V²) worst case — and then
re-walked every path in Python to re-sum distance/time that the Dijkstra
distances already imply. The ``cutoff_s`` argument accepted by the tool schema
and the engine was never forwarded to any Dijkstra call.

Fix: cost-only OD queries run a single accumulating Dijkstra (cost + length +
time carried per node, no path lists) with genuine cutoff pruning; the
path-returning variant forwards the cutoff to networkx; ``engine.solve_od_matrix``
plumbs ``cutoff_s`` through.
"""
import asyncio

import pytest

import networkx as nx

from app.services.network.engine import NetworkGraphEngine
from app.services.network.graph_builder import NetworkGraphBuilder
from app.services.network.models import TravelProfile
from app.services.network.od_matrix import NetworkODMatrixService


def _grid(k=12):
    """k x k node grid (~2*k*(k-1) two-way edge pairs), 60 km/h."""
    g = nx.DiGraph()
    for r in range(k):
        for c in range(k):
            g.add_node(f"n{r * k + c}", x=116.0 + c * 0.001, y=39.0 + r * 0.001)
    for r in range(k):
        for c in range(k - 1):
            a, b = f"n{r * k + c}", f"n{r * k + c + 1}"
            for u, v in ((a, b), (b, a)):
                g.add_edge(u, v, length_m=85.0, travel_time_s=5.1, id=f"e_{u}_{v}")
    for c in range(k):
        for r in range(k - 1):
            a, b = f"n{r * k + c}", f"n{(r + 1) * k + c}"
            for u, v in ((a, b), (b, a)):
                g.add_edge(u, v, length_m=111.0, travel_time_s=6.66, id=f"e_{u}_{v}")
    return g


def _dataset(g):
    from app.services.network.models import NetworkDataset, Node, Edge
    nodes = [Node(id=n, x=d["x"], y=d["y"]) for n, d in g.nodes(data=True)]
    edges = [Edge(id=str(i), u=u, v=v, length_m=d["length_m"]) for i, (u, v, d) in enumerate(g.edges(data=True))]
    return NetworkDataset(dataset_id="grid", nodes=nodes, edges=edges)


class TestCutoffPlumbing:
    def test_service_accepts_cutoff_and_prunes(self):
        """Cells whose cost exceeds the cutoff must come back unreachable;
        cells within the cutoff must match the uncutoff run exactly."""
        g = _grid(12)
        ds = _dataset(g)
        svc = NetworkODMatrixService()
        origins = ["n0", "n5"]
        dests = ["n143", "n60", "n13", "n0"]

        full = svc.network_od_matrix(origins, dests, graph=g, network_dataset=ds)
        cut = svc.network_od_matrix(origins, dests, graph=g, network_dataset=ds, cutoff_s=60.0)

        assert len(full) == len(cut) == 8
        for f, c in zip(full, cut):
            assert (f.origin_id, f.destination_id) == (c.origin_id, c.destination_id)
            if f.travel_time_s > 60.0:
                assert not c.reachable, (
                    f"{c.origin_id}->{c.destination_id}: time {f.travel_time_s:.1f} s "
                    f"exceeds cutoff 60 s but was returned reachable"
                )
            else:
                assert c.reachable
                assert c.travel_time_s == pytest.approx(f.travel_time_s, abs=0.02)
                assert c.distance_m == pytest.approx(f.distance_m, abs=0.1)

    def test_cutoff_never_returns_cells_beyond_it(self):
        g = _grid(10)
        ds = _dataset(g)
        svc = NetworkODMatrixService()
        pairs = svc.network_od_matrix(
            ["n0"], [f"n{i}" for i in range(0, 100, 7)], graph=g, network_dataset=ds,
            cutoff_s=30.0,
        )
        assert pairs
        for p in pairs:
            assert not p.reachable or p.travel_time_s <= 30.0

    def test_engine_solve_od_matrix_forwards_cutoff_s(self):
        """The tool-level cutoff_s must reach the Dijkstra calls (previously
        accepted and silently ignored)."""
        k = 10
        g = _grid(k)
        ds = _dataset(g)
        engine = NetworkGraphEngine()

        full = engine.od_matrix(
            origins=["n0", "n17"], destinations=["n99", "n45"],
            network_dataset=ds, graph=g,
        )
        cut = engine.od_matrix(
            origins=["n0", "n17"], destinations=["n99", "n45"],
            network_dataset=ds, graph=g, cutoff_s=40.0,
        )
        assert len(full) == len(cut) == 4
        pruned = [c for c in cut if not c.reachable]
        assert pruned, "cutoff_s=40 pruned nothing on a 10x10 grid — not forwarded"
        for f, c in zip(full, cut):
            if c.reachable:
                assert f.reachable
                assert c.travel_time_s == pytest.approx(f.travel_time_s, abs=0.02)

        # Async tool seam: solve_od_matrix(network=NetworkDataset...) forwards too.
        async def _run():
            return await engine.solve_od_matrix(
                network=ds, origins=[[116.0, 39.0]], destinations=[[116.009, 39.009]],
                cutoff_s=1.0,
            )
        res = asyncio.run(_run())
        assert res.od_matrix and not res.od_matrix[0].reachable

        async def _run_full():
            return await engine.solve_od_matrix(
                network=ds, origins=[[116.0, 39.0]], destinations=[[116.009, 39.009]],
            )
        res_full = asyncio.run(_run_full())
        assert res_full.od_matrix and res_full.od_matrix[0].reachable

    def test_cutoff_cost_units_follow_impedance(self):
        """cutoff_s bounds the active impedance cost: under length impedance
        it prunes by meters."""
        from app.services.network.models import Impedance
        g = _grid(10)
        ds = _dataset(g)
        svc = NetworkODMatrixService()
        full = svc.network_od_matrix(
            ["n0"], ["n99"], graph=g, network_dataset=ds,
            impedance=Impedance(name="length_m"),
        )
        cut = svc.network_od_matrix(
            ["n0"], ["n99"], graph=g, network_dataset=ds,
            impedance=Impedance(name="length_m"), cutoff_s=500.0,
        )
        assert full[0].reachable
        assert full[0].distance_m > 500.0
        assert not cut[0].reachable


class TestNoPathMaterialization:
    def test_cost_only_od_never_materializes_paths(self, monkeypatch):
        """Structural guard (#449): the cost-only matrix variant must not call
        networkx's path-materializing single_source_dijkstra at all."""
        g = _grid(12)
        ds = _dataset(g)

        def _boom(*args, **kwargs):
            raise AssertionError(
                "network_od_matrix (cost-only) must not call "
                "nx.single_source_dijkstra — paths are not needed (#449)"
            )

        monkeypatch.setattr(nx, "single_source_dijkstra", _boom)
        monkeypatch.setattr(nx, "single_source_dijkstra_path", _boom)
        svc = NetworkODMatrixService()
        pairs = svc.network_od_matrix(
            ["n0", "n40"], ["n143", "n77", "n5"], graph=g, network_dataset=ds,
        )
        assert len(pairs) == 6
        assert sum(1 for p in pairs if p.reachable) == 6

    def test_cutoff_reduces_explored_nodes(self):
        """Direct check on the accumulating Dijkstra: a cutoff must settle
        strictly fewer nodes than the full run on a large graph, and the
        settled set must equal the full run's nodes within the cutoff radius."""
        from app.services.network.od_matrix import _single_source_costs
        from app.services.network.routing import build_weight_func

        g = _grid(60)  # 3600 nodes
        wf = build_weight_func("travel_time_s")
        full_d, full_len, _full_t = _single_source_costs(g, "n1800", wf, cutoff=None)
        cut_d, cut_len, _cut_t = _single_source_costs(g, "n1800", wf, cutoff=60.0)

        assert len(cut_d) < len(full_d), (
            f"cutoff settled {len(cut_d)} nodes vs full {len(full_d)} — no pruning"
        )
        # Equality with the full run filtered to the cutoff radius.
        within = {n: c for n, c in full_d.items() if c <= 60.0}
        assert set(cut_d) == set(within)
        for n, c in cut_d.items():
            assert c == pytest.approx(within[n], rel=1e-12)
            assert cut_len[n] == pytest.approx(full_len[n], rel=1e-9)

    def test_cost_tree_matches_reference_dijkstra(self):
        """The accumulating Dijkstra's costs/distances equal nx reference runs
        on a random graph (unique optima)."""
        import random
        from app.services.network.od_matrix import _single_source_costs
        from app.services.network.routing import build_weight_func

        rng = random.Random(449)
        g = nx.DiGraph()
        for i in range(150):
            g.add_node(f"n{i}", x=116.0 + rng.uniform(0, 0.05), y=39.0 + rng.uniform(0, 0.05))
        ids = list(g.nodes)
        for _ in range(600):
            a, b = rng.sample(ids, 2)
            if g.has_edge(a, b):
                continue
            import math
            dx = g.nodes[a]["x"] - g.nodes[b]["x"]
            dy = g.nodes[a]["y"] - g.nodes[b]["y"]
            length_m = math.hypot(dx * 85_000, dy * 111_000)
            for u, v in ((a, b), (b, a)):
                g.add_edge(u, v, length_m=length_m,
                           travel_time_s=length_m / rng.uniform(8.0, 25.0),
                           id=f"e_{u}_{v}")

        wf = build_weight_func("travel_time_s")
        dists, lens, times = _single_source_costs(g, "n0", wf)
        ref_cost = nx.single_source_dijkstra_path_length(g, "n0", weight=wf)
        for n in ref_cost:
            assert dists[n] == pytest.approx(ref_cost[n], rel=1e-9)
        # len/time accumulate along the SAME tree with per-edge speeds in
        # [8, 25] m/s, so every node's accumulated speed ratio stays in range.
        for n, t in times.items():
            if t > 0 and lens[n] > 0:
                speed = lens[n] / t
                assert 7.9 <= speed <= 25.1, f"node {n}: implied speed {speed:.2f} m/s"


class TestAdversarial:
    def test_disconnected_graph_unreachable(self):
        g = _grid(4)
        g.add_node("iso", x=117.0, y=40.0)
        ds = _dataset(g)
        svc = NetworkODMatrixService()
        pairs = svc.network_od_matrix(
            ["n0", "iso"], ["n15", "iso"], graph=g, network_dataset=ds,
        )
        assert len(pairs) == 4
        by_od = {(p.origin_id, p.destination_id): p for p in pairs}
        assert not by_od[("iso", "n15")].reachable
        assert by_od[("iso", "iso")].reachable
        assert by_od[("iso", "iso")].distance_m == 0.0

    def test_zero_length_edges_do_not_break_accumulation(self):
        g = nx.DiGraph()
        g.add_node("a", x=116.0, y=39.0)
        g.add_node("b", x=116.0, y=39.0)
        g.add_node("c", x=116.001, y=39.0)
        g.add_edge("a", "b", length_m=0.0, travel_time_s=0.0, id="e0")
        g.add_edge("b", "c", length_m=85.0, travel_time_s=5.1, id="e1")
        ds = _dataset(g)
        svc = NetworkODMatrixService()
        pairs = svc.network_od_matrix(["a"], ["b", "c"], graph=g, network_dataset=ds)
        assert pairs[0].reachable and pairs[0].distance_m == 0.0
        assert pairs[1].reachable and pairs[1].distance_m == pytest.approx(85.0)

    def test_one_way_edges_respected(self):
        g = nx.DiGraph()
        for i, x in enumerate((116.0, 116.001, 116.002)):
            g.add_node(f"n{i}", x=x, y=39.0)
        g.add_edge("n0", "n1", length_m=85.0, travel_time_s=5.1, id="e0")
        g.add_edge("n1", "n2", length_m=85.0, travel_time_s=5.1, id="e1")
        # no reverse edges — strictly one-way chain
        ds = _dataset(g)
        svc = NetworkODMatrixService()
        fwd = svc.network_od_matrix(["n0"], ["n2"], graph=g, network_dataset=ds)
        rev = svc.network_od_matrix(["n2"], ["n0"], graph=g, network_dataset=ds)
        assert fwd[0].reachable
        assert not rev[0].reachable
