"""Shared fixtures for #669 CoW proofs (unit + perf).

Centralizes the 100k-scale synthetic payloads and fake doubles that were
duplicated between `tests/unit/test_mapspec_cow_regression_669.py` and
`tests/benchmarks/test_perf_mapspec_mutation_cost.py`. Import from here
instead of re-defining.

Keep lazy construction (no 160k-feature cost at import) for perf-suite skip
behaviour (#664).
"""
import pathlib
import tempfile


# ── 100k/50k/10k deterministic generators (extend test_performance_100k) ─────
def _point_fc_100k():
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [116.0 + (i % 1000) * 0.001, 39.9 + (i // 1000) * 0.001]},
                "properties": {"id": i, "cat": f"cat{i % 10}", "v": float(i % 100)},
            }
            for i in range(100000)
        ],
    }


def _linestring_fc_50k():
    feats = []
    for i in range(50000):
        lon0 = 116.0 + (i % 500) * 0.002
        lat0 = 39.0 + (i // 500) * 0.002
        feats.append({
            "type": "Feature",
            "geometry": {"type": "LineString", "coordinates": [[lon0, lat0], [lon0 + 0.001, lat0 + 0.001], [lon0 + 0.002, lat0]]},
            "properties": {"id": i},
        })
    return {"type": "FeatureCollection", "features": feats}


def _polygon_fc_10k():
    feats = []
    for i in range(10000):
        lon = 116.0 + (i % 100) * 0.01
        lat = 39.0 + (i // 100) * 0.01
        ring = [[lon, lat], [lon + 0.005, lat], [lon + 0.005, lat + 0.005], [lon, lat + 0.005], [lon, lat]]
        feats.append({
            "type": "Feature",
            "geometry": {"type": "Polygon", "coordinates": [ring]},
            "properties": {"id": i},
        })
    return {"type": "FeatureCollection", "features": feats}


_CACHED: dict = {}


def get_point_100k():
    if "point" not in _CACHED:
        _CACHED["point"] = _point_fc_100k()
    return _CACHED["point"]


def get_line_50k():
    if "line" not in _CACHED:
        _CACHED["line"] = _linestring_fc_50k()
    return _CACHED["line"]


def get_poly_10k():
    if "poly" not in _CACHED:
        _CACHED["poly"] = _polygon_fc_10k()
    return _CACHED["poly"]


# ── large inline payload (50MB-class: 100k points ≈12MB JSON) ───────────────
# Keep identity stable for sharing assertions; build lazily.
_LARGE_INLINE_FC_CACHE = None


def get_large_inline_fc():
    global _LARGE_INLINE_FC_CACHE
    if _LARGE_INLINE_FC_CACHE is None:
        _LARGE_INLINE_FC_CACHE = get_point_100k()
    return _LARGE_INLINE_FC_CACHE


def get_large_source():
    # shallow wrapper each call so tests get same payload object identity
    return {
        "type": "geojson",
        "inlineData": get_large_inline_fc(),
        "profile": {"bbox": [116, 39, 117, 40], "featureCount": 100000},
    }


def get_large_ref_spec(ref_id: str = "ref:big-100k"):
    return {
        "version": "1.0",
        "view": {"center": [116.0, 39.0], "zoom": 10},
        "sources": {"big": {"type": "geojson", "ref": ref_id, "profile": {"bbox": [116, 39, 117, 40]}}},
        "layers": [{"id": "l1", "type": "circle", "source": "big"}],
        "layout": {"legend": {"visible": True}, "controls": []},
    }


def spec_with_large_inline():
    return {
        "version": "1.0",
        "view": {"center": [116.0, 39.0], "zoom": 10},
        "sources": {"big": get_large_source()},
        "layers": [{"id": "l1", "type": "circle", "source": "big"}],
        "layout": {"legend": {"visible": True}, "controls": []},
        "thresholds": {"maxFeatures": 50000, "timeoutMs": 30000},
    }


# ── fakes (mirrors tests/unit/test_mapspec_cow.py doubles) ──────────────────
class FakeStore:
    def __init__(self, spec):
        self.spec = spec
        self.saved = []

    async def get_mapspec(self, sid):
        return self.spec

    async def save_mapspec(self, sid, mapspec):
        self.saved.append(mapspec)
        self.spec = mapspec
        return {"mapspec": mapspec}

    def get_session_dir(self, sid):
        return pathlib.Path(tempfile.mkdtemp())


class FakeSDM:
    def __init__(self, layers=None):
        self._layers = layers if layers is not None else [{"id": "l1", "type": "circle", "source": "big", "paint": {"circle-color": "#f00"}}]

    async def get_map_state(self, sid):
        return {"layers": list(self._layers)}

    async def set_map_state(self, sid, key, value, seq=None):
        if key == "layers":
            self._layers = value
        return True

    async def update_layer_in_state(self, sid, layer_id, updates):
        return None

    async def remove_layer_from_state(self, sid, layer_id):
        return None

    async def get(self, sid, ref):
        return None


class FakeLockCtx:
    async def __aenter__(self):
        return None

    async def __aexit__(self, *exc):
        return False


class FakeLockReg:
    def lock(self, sid):
        return FakeLockCtx()


async def fake_checkpoint(mapspec, session_dir, sdm, checkpoint_id=None):
    return {"checkpoint_id": "ckpt-test", "ref_count": 0}
