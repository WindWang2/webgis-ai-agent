"""Regression tests for issue #580 (P1): the point-snapping STRtree cache was
keyed by ``id(dataset)`` but its entries held NO strong reference to the
dataset. CPython recycles the ``id()`` of a freed object, so a NEW dataset
could reuse an old network's identity and ``snap_point`` would silently hit
the freed network's STRtree — matching points onto the wrong network's edges.

Fix: each cache entry pins the dataset (first tuple element) so its id cannot
be recycled while the entry is live, and the lookup re-checks identity so a
stale key hit rebuilds instead of returning the old network's STRtree.
"""
import gc

from app.services.network.graph_builder import NetworkGraphBuilder
from app.services.network.models import TravelProfile
from app.services.network.snapping import PointSnappingService


def _line_geojson(start_lng):
    """A single ~850 m eastward road edge starting at ``start_lng``."""
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {"id": "e1", "speed_kmh": 60.0, "one_way": False},
                "geometry": {
                    "type": "LineString",
                    "coordinates": [[start_lng, 39.0], [start_lng + 0.01, 39.0]],
                },
            }
        ],
    }


def test_snap_cache_pins_dataset_and_never_returns_stale_network():
    """#580: after snapping network A, dropping it and building new networks,
    a snap on B must land on B's geometry — never on the freed A's STRtree."""
    snapper = PointSnappingService()
    builder = NetworkGraphBuilder()
    profile = TravelProfile()

    graph_a, ds_a = builder.build_graph(_line_geojson(116.0), profile=profile)
    res_a = snapper.snap_point((116.005, 39.0), ds_a)
    assert res_a.nearest_edge_id is not None

    stale_key = id(ds_a)
    entry = snapper._index_cache[stale_key]  # noqa: SLF001
    # Direct regression guard: the entry MUST pin the dataset. The old code
    # stored only the STRtree here, so this fails deterministically on the
    # unfixed implementation.
    assert entry[0] is ds_a, "#580: the cache entry must pin the dataset"

    del graph_a, ds_a, res_a
    gc.collect()

    # Behavioral repro from the audit (A → del → construct B until id reuse):
    # without the pin, the freed dataset's id gets recycled and a snap on B
    # returns A's geometry; the assertions below catch that. With the pin the
    # id can never be recycled while the entry is live, so no collision ever
    # occurs and the loop exhausts its cap.
    collision = False
    for _ in range(400):
        graph_b, ds_b = builder.build_graph(_line_geojson(116.2), profile=profile)
        if id(ds_b) == stale_key:
            collision = True
            res_b = snapper.snap_point((116.205, 39.0), ds_b)
            # B's snapped point must sit on B's segment (~116.205), NOT on A's
            # (~116.01, ~17 km away): a stale STRtree hit returns A's edges.
            assert abs(res_b.snapped_point[0] - 116.205) < 0.002, (
                f"#580 regression: B's snap hit freed network A at "
                f"{res_b.snapped_point} — id(ds_b) == id(ds_a) after GC"
            )
            assert res_b.distance_to_network_m < 500.0
            break
    if not collision:
        # The pin held: the entry still maps the original key to the ORIGINAL
        # dataset object (its id could not be recycled while it lives).
        assert snapper._index_cache[stale_key][0] is entry[0]  # noqa: SLF001