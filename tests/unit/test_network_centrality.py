"""Network centrality tests — Foundation V2 A4.

Covers ``NetworkCentralityService`` (app/services/network/centrality.py):

- hand-computed goldens on a bidirectional star (degree 8/2, closeness
  1 vs 4/7, betweenness 1 vs 0) and a directed path (betweenness 1/3 vs 1/6,
  closeness 1/2 → 0 with explicit disconnected semantics);
- edge weights (travel_time_s / length_m) genuinely used as distances —
  weighted closeness closed form 3/24 = 0.125;
- backend variants genuinely switch: exact Brandes ≤ threshold, k-sample
  (seed 42) beyond it — deterministic across runs, disclosed via
  betweenness_mode / sample_k;
- scale guards BEFORE compute: node cap and the honest edge-betweenness
  refusal (ResourceScaleMismatch, no fake sampling);
- output row cap trims with explicit disclosure;
- UnsupportedMethod for invalid metrics/weight enums;
- descriptor backend-selection decisions match the real switch scale, and
  the tool attaches evidence with the backend diagnostic.
"""
import asyncio

import networkx as nx
import pytest

from app.lib.gis.backend_selection import ScaleProfile, select_backend
from app.lib.gis.scientific_errors import ResourceScaleMismatch, UnsupportedMethod
from app.services.network import centrality as centrality_mod
from app.services.network.centrality import NetworkCentralityService
from app.services.network.engine import NetworkGraphEngine

pytestmark = pytest.mark.unit


def _star_graph() -> nx.DiGraph:
    """Center 'c' + 4 leaves, all edges bidirectional, unit weights."""
    g = nx.DiGraph()
    for i in range(4):
        g.add_edge("c", f"l{i}", travel_time_s=1.0, length_m=10.0)
        g.add_edge(f"l{i}", "c", travel_time_s=1.0, length_m=10.0)
    return g


def _path_graph(n: int = 4, times=None) -> nx.DiGraph:
    """Directed chain 0→1→…→n-1 with travel_time_s=1 unless overridden."""
    g = nx.DiGraph()
    for i in range(n - 1):
        t = 1.0 if times is None else times[i]
        g.add_edge(i, i + 1, travel_time_s=t, length_m=t * 10.0)
    return g


def _cycle_graph(n: int = 10) -> nx.DiGraph:
    g = nx.cycle_graph(n, create_using=nx.DiGraph)
    for _, _, d in g.edges(data=True):
        d["travel_time_s"] = 1.0
        d["length_m"] = 10.0
    return g


class TestHandComputedGoldens:
    def test_star_graph_degree_closeness_betweenness_golden(self):
        res = NetworkCentralityService().network_centrality(_star_graph(), metrics="all")
        assert res.node_count == 5 and res.edge_count == 8
        by_node = {r["node_id"]: r for r in res.node_records}
        # degree = in + out (DiGraph semantics): center 8, leaves 2
        assert by_node["c"]["degree"] == 8
        assert all(by_node[f"l{i}"]["degree"] == 2 for i in range(4))
        # closeness: center reaches all at distance 1 → 4/4 = 1.0;
        # leaf: distances 1 + 2·3 = 7 → 4/7 (distance-corrected)
        assert by_node["c"]["closeness"] == pytest.approx(1.0, abs=1e-6)
        for i in range(4):
            assert by_node[f"l{i}"]["closeness"] == pytest.approx(4.0 / 7.0, abs=1e-6)
        # betweenness: center sits on every leaf↔leaf path → 1.0 normalized
        assert by_node["c"]["betweenness"] == pytest.approx(1.0, abs=1e-9)
        assert all(abs(by_node[f"l{i}"]["betweenness"]) < 1e-9 for i in range(4))
        assert res.betweenness_mode == "exact"

    def test_directed_path_betweenness_and_closeness_golden(self):
        """0→1→2→3 unit weights: pairs (0,2),(0,3) pass node 1; pairs (0,3),
        (1,3) pass node 2 — both 2/6 = 1/3 normalized. Closeness uses
        networkx IN-reach semantics on DiGraphs (reverse): node0 is reached
        by nobody → 0; being reached by 1/2/3 others gives (k/k_reach
        scaled) 1/3, 4/9, 1/2 for nodes 1/2/3. Metrics are stored rounded to
        6 decimals, hence the 1e-6 tolerance."""
        res = NetworkCentralityService().network_centrality(_path_graph(4), metrics="all")
        by_node = {r["node_id"]: r for r in res.node_records}
        assert by_node[1]["betweenness"] == pytest.approx(1.0 / 3.0, abs=2e-6)
        assert by_node[2]["betweenness"] == pytest.approx(1.0 / 3.0, abs=2e-6)
        assert by_node[0]["betweenness"] == pytest.approx(0.0, abs=1e-9)
        assert by_node[0]["closeness"] == 0.0  # nobody reaches the source
        assert by_node[1]["closeness"] == pytest.approx(1.0 / 3.0, abs=2e-6)
        assert by_node[2]["closeness"] == pytest.approx(4.0 / 9.0, abs=2e-6)
        assert by_node[3]["closeness"] == pytest.approx(1.0 / 2.0, abs=2e-6)  # the sink

    def test_closeness_uses_weight_not_topology_hops(self):
        """Weights are distances: node 3 is reached over times [1,10,1]
        (sum 3+11+12 = 24) → in-closeness(3) = 3/24 = 0.125 — and switching
        the weight field to length (10×) rescales to 3/120 = 0.025."""
        res = NetworkCentralityService().network_centrality(
            _path_graph(4, times=[1.0, 10.0, 1.0]), metrics="closeness")
        node3 = next(r for r in res.node_records if r["node_id"] == 3)
        assert node3["closeness"] == pytest.approx(3.0 / 24.0, abs=1e-6)

        g = _path_graph(4, times=[1.0, 10.0, 1.0])
        for _, _, d in g.edges(data=True):
            d["length_m"] = d["travel_time_s"] * 10.0  # 10, 100, 10 m
        res_len = NetworkCentralityService().network_centrality(g, metrics="closeness",
                                                                weight="length")
        node3_len = next(r for r in res_len.node_records if r["node_id"] == 3)
        assert node3_len["closeness"] == pytest.approx(3.0 / 240.0, abs=1e-6)  # 10/110/120
        assert res_len.weight_field == "length_m"

    def test_degree_metric_only_has_no_weight_keys(self):
        res = NetworkCentralityService().network_centrality(_star_graph(), metrics="degree")
        assert res.metrics == ["degree"]
        for rec in res.node_records:
            assert set(rec.keys()) == {"node_id", "degree"}


class TestBackendVariantSwitch:
    def test_betweenness_switches_to_sampled_beyond_threshold(self, monkeypatch):
        """Implementation genuinely switches: shrink the exact threshold so a
        10-node graph takes the sampled path (k monkeypatched to 3 ≤ n)."""
        monkeypatch.setattr(centrality_mod, "_EXACT_BETWEENNESS_NODES", 4)
        monkeypatch.setattr(centrality_mod, "_SAMPLE_K", 3)
        svc = NetworkCentralityService()
        g = _cycle_graph(10)
        r1 = svc.network_centrality(g, metrics="betweenness")
        assert r1.betweenness_mode == "sampled"
        assert r1.sample_k == 3
        assert r1.summary["betweenness_mode"] == "sampled"
        # fixed seed ⇒ two runs identical
        r2 = svc.network_centrality(g, metrics="betweenness")
        assert r1.node_records == r2.node_records

    def test_sampled_betweenness_deterministic_and_real_scale(self, monkeypatch):
        """At the declared boundary (n>2000) the real k=500/seed=42 sampled
        path runs and is reproducible; monkeypatching the threshold up
        switches the same graph to exact."""
        n = 2100
        g = nx.path_graph(n, create_using=nx.DiGraph)
        for _, _, d in g.edges(data=True):
            d["travel_time_s"] = 1.0
            d["length_m"] = 1.0
        svc = NetworkCentralityService()
        res = svc.network_centrality(g, metrics="betweenness")
        assert res.betweenness_mode == "sampled"
        assert res.sample_k == 500
        res2 = svc.network_centrality(g, metrics="betweenness")
        assert res.node_records == res2.node_records

        from app.services.network import centrality as cm
        monkeypatch.setattr(cm, "_EXACT_BETWEENNESS_NODES", n + 1)
        res_exact = svc.network_centrality(g, metrics="betweenness")
        assert res_exact.betweenness_mode == "exact"
        assert res_exact.sample_k is None

    def test_descriptor_backend_variants_genuinely_switch(self):
        """The descriptor's declared windows match the implementation's switch:
        selection at n≤2000 picks exact_brandes, at n≥2001 sampled_brandes."""
        d_small = select_backend("network.centrality", ScaleProfile(feature_count=2000))
        d_big = select_backend("network.centrality", ScaleProfile(feature_count=2001))
        assert d_small.variant_id == "exact_brandes"
        assert d_small.matched is True
        assert d_big.variant_id == "sampled_brandes"
        assert d_big.matched is True
        # and the implementation switches at the same node count
        assert centrality_mod._EXACT_BETWEENNESS_NODES == 2000


class TestScaleGuardsAndErrors:
    def test_node_cap_guard_fires_before_compute(self, monkeypatch):
        monkeypatch.setattr(centrality_mod, "_NODE_CAP", 3)
        with pytest.raises(ResourceScaleMismatch) as ei:
            NetworkCentralityService().network_centrality(_cycle_graph(5), metrics="degree")
        assert "nodes=5" in (ei.value.estimated or "")
        assert ei.value.scientific_code == "RESOURCE_SCALE_MISMATCH"

    def test_edge_betweenness_scale_guard_refuses(self, monkeypatch):
        """edge_betweenness beyond the exact edge budget is honestly refused —
        never 'approximated' with fake sampling."""
        monkeypatch.setattr(centrality_mod, "_EXACT_EDGE_BETWEENNESS_EDGES", 5)
        g = _cycle_graph(6)  # 6 edges > 5
        with pytest.raises(ResourceScaleMismatch) as ei:
            NetworkCentralityService().network_centrality(g, metrics="edge_betweenness")
        assert "edges=6" in (ei.value.estimated or "")
        # "all" includes edge_betweenness ⇒ same refusal
        with pytest.raises(ResourceScaleMismatch):
            NetworkCentralityService().network_centrality(g, metrics="all")

    def test_small_edge_betweenness_exact_values(self):
        """Within budget, node-level edge betweenness = sum of incident edges'
        exact (networkx-normalized) betweenness. Unit path 0→1→2→3: raw edge
        loads are 3/4/3; networkx's directed rescaling gives (0,1)=0.25,
        (1,2)=1/3, (2,3)=0.25 → node sums {0: 0.25, 1: 7/12, 2: 7/12, 3:
        0.25}; cross-checked against the networkx estimator aggregated."""
        g = _path_graph(4)
        res = NetworkCentralityService().network_centrality(g, metrics="edge_betweenness")
        expected_edges = nx.edge_betweenness_centrality(g, weight="travel_time_s", normalized=True)
        expected_nodes: dict = {}
        for (u, v), val in expected_edges.items():
            expected_nodes[u] = expected_nodes.get(u, 0.0) + val
            expected_nodes[v] = expected_nodes.get(v, 0.0) + val
        by_node = {r["node_id"]: r for r in res.node_records}
        assert by_node[0]["edge_betweenness"] == pytest.approx(0.25, abs=2e-6)
        assert by_node[1]["edge_betweenness"] == pytest.approx(7.0 / 12.0, abs=2e-6)
        assert by_node[2]["edge_betweenness"] == pytest.approx(7.0 / 12.0, abs=2e-6)
        assert by_node[3]["edge_betweenness"] == pytest.approx(0.25, abs=2e-6)
        for node, expected in expected_nodes.items():
            assert by_node[node]["edge_betweenness"] == pytest.approx(expected, abs=2e-6)

    def test_output_row_cap_trims_with_disclosure(self, monkeypatch):
        monkeypatch.setattr(centrality_mod, "_OUTPUT_ROW_CAP", 3)
        res = NetworkCentralityService().network_centrality(_cycle_graph(10), metrics="degree")
        assert len(res.node_records) == 3
        assert res.output_rows_total == 10
        assert res.output_row_cap == 3
        assert res.summary["output_rows_trimmed"] is True

    def test_invalid_metric_or_weight_raises_unsupported(self):
        svc = NetworkCentralityService()
        with pytest.raises(UnsupportedMethod):
            svc.network_centrality(_star_graph(), metrics="harmonic")
        with pytest.raises(UnsupportedMethod):
            svc.network_centrality(_star_graph(), weight="degrees")


class TestEngineAndToolSurface:
    def test_engine_solve_centrality_and_tool_evidence(self):
        from app.tools import network_tools as nt
        from app.tools.registry import ToolRegistry

        engine = NetworkGraphEngine()
        res = asyncio.run(engine.solve_centrality(
            network={"type": "FeatureCollection", "features": [
                {"type": "Feature", "properties": {"id": "s0", "speed_kmh": 40.0, "one_way": False},
                 "geometry": {"type": "LineString", "coordinates": [[116.0, 0.0], [116.001, 0.0]]}},
                {"type": "Feature", "properties": {"id": "s1", "speed_kmh": 40.0, "one_way": False},
                 "geometry": {"type": "LineString", "coordinates": [[116.001, 0.0], [116.002, 0.0]]}},
            ]},
            metrics="betweenness",
            weight="travel_time",
        ))
        assert res.analysis_type == "network_centrality"
        assert res.metrics == ["betweenness"]
        assert res.betweenness_mode == "exact"

        reg = ToolRegistry()
        nt.register_network_tools(reg)
        out = asyncio.run(reg._tools["network_centrality"](
            network={"type": "FeatureCollection", "features": [
                {"type": "Feature", "properties": {"id": "s0", "speed_kmh": 40.0, "one_way": False},
                 "geometry": {"type": "LineString", "coordinates": [[116.0, 0.0], [116.001, 0.0]]}},
                {"type": "Feature", "properties": {"id": "s1", "speed_kmh": 40.0, "one_way": False},
                 "geometry": {"type": "LineString", "coordinates": [[116.001, 0.0], [116.002, 0.0]]}},
            ]},
            metrics="all",
            weight="travel_time",
        ))
        assert out.get("type") != "error", out
        ev = out.get("scientific_evidence")
        assert ev, "centrality tool must carry scientific_evidence"
        assert ev["algorithm"] == "network.centrality"
        assert "brandes2001" in ev["method_references"]
        names = [d["name"] for d in ev["diagnostics"]]
        assert "backend_selection" in names
        assert "betweenness_mode" in names
        backend = next(d for d in ev["diagnostics"] if d["name"] == "backend_selection")
        assert "variant=" in backend["text"]
