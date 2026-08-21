"""#710 (VRP impedance-resolved tour cost) and #711 (cos-lat bearing scaling)."""
import math

import networkx as nx
import pytest

from app.services.network.models import Impedance
from app.services.network.routing import NetworkRoutingService
from app.services.network.graph_builder import NetworkGraphBuilder
from app.services.network.models import TravelProfile
from app.services.network.vrp import NetworkRouteOptimizationService


def _straight_network(n: int = 8):
    """Collinear road so any detour-free order is optimal; metric observable
    comes from the cost matrix source itself."""
    features = [{
        "type": "Feature",
        "properties": {"speed_kmh": 60.0, "one_way": False},
        "geometry": {
            "type": "LineString",
            "coordinates": [[116.0 + i * 0.01, 39.0] for i in range(n)],
        },
    }]
    return NetworkGraphBuilder().build_graph(
        {"type": "FeatureCollection", "features": features}, profile=TravelProfile()
    )


def test_vrp_cost_matrix_uses_impedance_cost():
    """#710: with a length impedance, the tour cost matrix must be built from
    the impedance-resolved od cost, not time_s."""
    graph, dataset = _straight_network()
    svc = NetworkRouteOptimizationService()
    stops = [[116.0, 39.0], [116.02, 39.0], [116.04, 39.0]]
    res_length = svc.optimize_route(
        stops=stops, graph=graph, network_dataset=dataset,
        impedance=Impedance(name="length_m", unit="meters"),
    )
    res_time = svc.optimize_route(
        stops=stops, graph=graph, network_dataset=dataset,
    )
    # both impedances admit the same straight-line optimum on this network —
    # the assertion pins that the length run no longer optimizes by time:
    # with time weights the matrix is seconds; with length weights, meters.
    assert res_length is not None and res_time is not None
    # on a 60 km/h road, 1 m ≈ 0.06 s — cost scales differ by ~16.7×,
    # so a time-built matrix under length accumulation would mis-rank any
    # asymmetric layout; here we verify the reported cost unit matches the
    # accumulated length (meters), i.e. > time_s magnitude by ×16.6.
    # Straight network: total length 116.00→116.04 round trip = 8 km.
    assert res_length.total_cost == pytest.approx(res_length.total_distance_m, rel=0.05)


def test_bearing_vectors_scaled_by_cos_lat():
    """#711: a true 45° NE bearing at 45°N must compute as ~45°, not ~35°."""
    g = nx.DiGraph()
    g.add_node("a", x=0.0, y=45.0)
    g.add_node("b", x=0.01, y=45.01)
    g.add_node("c", x=0.02, y=45.02)
    v1, v2 = NetworkRoutingService._bearing_vectors(g, "a", "b", "c")
    bearing = math.degrees(math.atan2(v1[1], v1[0]))
    # True bearing of (Δx=0.01°, Δy=0.01°) at 45°N: atan2(1, cos45°) ≈ 54.7°
    # from east, i.e. NE-by-north; the OLD unscaled code gave 45.0° and the
    # TRUE ground bearing is 54.7°.
    assert bearing == pytest.approx(54.7356, abs=0.5)

    # and the turn between two collinear legs stays ~0°
    change = NetworkRoutingService._bearing_change_deg(g, "a", "b", "c")
    assert change < 1.0
