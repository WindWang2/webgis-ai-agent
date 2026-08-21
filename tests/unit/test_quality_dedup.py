"""Issue #693 item 10: NEAR_DUPLICATE vs TOPOLOGY_GAP dedup; plus allocation/graph/centroid checks."""

from app.services.spatial_quality_service import SpatialQualityEngine


def test_gap_and_near_duplicate_not_duplicated():
    # Two polygons separated by 5e-7 deg (< both thresholds 1e-5 gap, 1e-6 near-dup)
    def poly(x):
        return {"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [[[x, 0], [x + 0.001, 0], [x + 0.001, 0.001], [x, 0.001], [x, 0]]]}, "properties": {}}
    fc = {"type": "FeatureCollection", "features": [poly(0), poly(0.001 + 5e-7)]}
    r = SpatialQualityEngine.audit_dataset(fc, crs="EPSG:4326")
    codes = [i.code for i in r.issues if i.dimension == "topology"]
    assert "TOPOLOGY_GAP" in codes
    assert codes.count("NEAR_DUPLICATE_VERTICES") == 0  # gap already covers the pair


def test_allocation_unassigned(tmp_path=None):
    # Direct test of the inf-cost unassigned path: two facilities, three demands
    # where demand d_far has inf cost to every facility (disconnected component
    # simulated by mocking OD). The new code puts d_far in summary unassigned
    # instead of silently assigning to the first facility via min(inf, inf) == 0.
    from app.services.network.allocation import NetworkLocationAllocationService
    from app.services.network.models import Facility, DemandPoint, NetworkDataset, Node, Edge
    import networkx as nx
    from unittest.mock import patch

    g = nx.DiGraph()
    for i, lon in enumerate([0, 0.001]):
        g.add_node(f"n{i}", x=lon, y=0)
    g.add_edge("n0", "n1", length_m=111, travel_time_s=10, highway_type="residential", geometry=None)
    g.add_edge("n1", "n0", length_m=111, travel_time_s=10, highway_type="residential", geometry=None)
    nodes = [Node(id=f"n{i}", x=lon, y=0) for i, lon in enumerate([0, 0.001])]
    edges = [Edge(id="e0", u="n0", v="n1", length_m=111, travel_time_s=10), Edge(id="e1", u="n1", v="n0", length_m=111, travel_time_s=10)]
    ds = NetworkDataset(dataset_id="test", nodes=nodes, edges=edges, bounding_box=[-0.1, -0.1, 0.002, 0.1])
    facs = [Facility(facility_id="f0", geometry={"type": "Point", "coordinates": [0, 0]}),
            Facility(facility_id="f1", geometry={"type": "Point", "coordinates": [0.001, 0]})]
    demands = [
        DemandPoint(demand_id="d0", geometry={"type": "Point", "coordinates": [0.0005, 0]}, weight=1),
        DemandPoint(demand_id="d1", geometry={"type": "Point", "coordinates": [0.0006, 0]}, weight=1),
        DemandPoint(demand_id="d_far", geometry={"type": "Point", "coordinates": [5, 0]}, weight=1),
    ]
    svc = NetworkLocationAllocationService()
    # Mock OD to force d_far unreachable to both facilities
    from app.services.network.models import ODPair
    fake_pairs = [
        ODPair(origin_id="d0", destination_id="f0", distance_m=10, travel_time_s=5, reachable=True),
        ODPair(origin_id="d0", destination_id="f1", distance_m=10, travel_time_s=5, reachable=True),
        ODPair(origin_id="d1", destination_id="f0", distance_m=10, travel_time_s=5, reachable=True),
        ODPair(origin_id="d1", destination_id="f1", distance_m=10, travel_time_s=5, reachable=True),
        ODPair(origin_id="d_far", destination_id="f0", distance_m=0, travel_time_s=0, reachable=False),
        ODPair(origin_id="d_far", destination_id="f1", distance_m=0, travel_time_s=0, reachable=False),
    ]
    with patch.object(svc.od_service, "network_od_matrix", return_value=fake_pairs):
        res = svc.location_allocation(facs, demands, p_count=1, graph=g, network_dataset=ds)
    assert "unassigned_ids" in res.summary
    assert "d_far" in res.summary["unassigned_ids"]
    # d_far must not be counted in any facility's assigned list
    all_assigned = [did for fac in res.allocated_facilities for did in fac["assigned_demand_ids"]]
    assert "d_far" not in all_assigned


def test_graph_builder_uses_dict_not_scan():
    import inspect
    from app.services.network.graph_builder import NetworkGraphBuilder
    src = inspect.getsource(NetworkGraphBuilder._extract_line_items)
    # Must use dict lookup, not linear scan over nodes
    assert "node_by_id" in src
    assert "for n in data.nodes if n.id == edge.u" not in src


def test_change_centroid_doc_mentions_holes():
    import inspect
    from app.services.temporal.change import compute_centroid
    src = inspect.getsource(compute_centroid)
    assert "hole" in src.lower() or "holes" in src.lower()
