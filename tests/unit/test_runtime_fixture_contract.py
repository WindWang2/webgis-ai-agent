"""Runtime fixture contract — no browser, pure structural assertions.

Every dir under tests/fixtures/runtime/ must contain mapspec.json + probes.json;
structural validity and probe DSL invariants are asserted here.
"""
import json
import re
from pathlib import Path

FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "runtime"

KNOWN_PROBE_TYPES = {"layer-exists", "feature-count", "pixel-color"}

HEX_RE = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")


def _hex_to_rgb(hex_str: str):
    h = hex_str.lstrip("#")
    if len(h) == 3:
        return tuple(int(c * 2, 16) for c in h)
    if len(h) == 6:
        return tuple(int(h[i : i + 2], 16) for i in (0, 2, 4))
    raise ValueError(f"invalid hex {hex_str}")


def _fixture_dirs():
    if not FIXTURE_ROOT.exists():
        return []
    return sorted([p for p in FIXTURE_ROOT.iterdir() if p.is_dir()])


def test_fixture_root_exists():
    assert FIXTURE_ROOT.exists(), f"fixture root missing: {FIXTURE_ROOT}"
    dirs = _fixture_dirs()
    assert len(dirs) >= 1, "no fixture dirs under tests/fixtures/runtime/"


def test_every_fixture_has_required_files():
    for d in _fixture_dirs():
        assert (d / "mapspec.json").is_file(), f"{d.name}: missing mapspec.json"
        assert (d / "probes.json").is_file(), f"{d.name}: missing probes.json"


def test_mapspec_json_valid_and_has_sources_layers():
    for d in _fixture_dirs():
        p = d / "mapspec.json"
        if not p.is_file():
            continue
        data = json.loads(p.read_text(encoding="utf-8"))
        assert isinstance(data, dict), f"{d.name}: mapspec.json not an object"
        sources = data.get("sources")
        assert isinstance(sources, dict) and len(sources) > 0, f"{d.name}: mapspec.sources must be non-empty dict"
        layers = data.get("layers")
        assert isinstance(layers, list) and len(layers) > 0, f"{d.name}: mapspec.layers must be non-empty list"


def test_probes_json_valid():
    for d in _fixture_dirs():
        p = d / "probes.json"
        if not p.is_file():
            continue
        data = json.loads(p.read_text(encoding="utf-8"))
        assert isinstance(data, dict), f"{d.name}: probes.json not an object"
        expect = data.get("expect", "pass")
        assert expect in ("pass", "fail"), f"{d.name}: expect must be 'pass' or 'fail', got {expect!r}"
        probes = data.get("probes")
        assert isinstance(probes, list), f"{d.name}: probes must be a list"
        if expect == "fail":
            assert len(probes) >= 1, f"{d.name}: expect:fail fixtures must contain ≥1 probe"
        for idx, probe in enumerate(probes):
            assert isinstance(probe, dict), f"{d.name} probe[{idx}]: not an object"
            t = probe.get("type")
            assert t in KNOWN_PROBE_TYPES, f"{d.name} probe[{idx}]: unknown type {t!r}"
            # layer required for all v1 types
            assert isinstance(probe.get("layer"), str) and probe["layer"], f"{d.name} probe[{idx}]: missing 'layer'"
            if t == "feature-count":
                has_equals = "equals" in probe
                has_min = "min" in probe
                assert has_equals or has_min, f"{d.name} probe[{idx}]: feature-count requires 'equals' and/or 'min'"
                if has_equals:
                    assert isinstance(probe["equals"], int), f"{d.name} probe[{idx}]: equals must be int"
                if has_min:
                    assert isinstance(probe["min"], int), f"{d.name} probe[{idx}]: min must be int"
                if "filter" in probe:
                    assert probe["filter"] is not None, f"{d.name} probe[{idx}]: filter null not allowed"
            elif t == "pixel-color":
                at = probe.get("at")
                assert isinstance(at, (list, tuple)) and len(at) == 2, f"{d.name} probe[{idx}]: 'at' must be [lng, lat]"
                assert all(isinstance(v, (int, float)) for v in at), f"{d.name} probe[{idx}]: 'at' values must be numbers"
                exp = probe.get("expect")
                assert isinstance(exp, str) and HEX_RE.match(exp), f"{d.name} probe[{idx}]: 'expect' must be hex color, got {exp!r}"


def test_pixel_color_pairwise_distinguishable():
    for d in _fixture_dirs():
        p = d / "probes.json"
        if not p.is_file():
            continue
        data = json.loads(p.read_text(encoding="utf-8"))
        probes = [pr for pr in data.get("probes", []) if pr.get("type") == "pixel-color"]
        if len(probes) < 2:
            continue
        for i in range(len(probes)):
            for j in range(i + 1, len(probes)):
                c1 = _hex_to_rgb(probes[i]["expect"])
                c2 = _hex_to_rgb(probes[j]["expect"])
                dist = sum(abs(a - b) for a, b in zip(c1, c2))
                assert dist > 48, (
                    f"{d.name}: pixel-color probes {i} and {j} expected colors {probes[i]['expect']} vs {probes[j]['expect']} "
                    f"must be pairwise distinguishable (total per-channel distance {dist} <= 48)"
                )


def test_fixture_asset_declarations():
    """Asset convention: points.geojson → vector tiles, raster.json → raster PNG via __ORIGIN__.

    - If a fixture contains points.geojson it must be a valid FeatureCollection.
    - If a fixture contains raster.json it must declare array/palette/bounds and be parseable.
    - The mapspec must contain a matching source whose tiles/imageRef use __ORIGIN__.
    - Every __ORIGIN__ path in mapspec.json must correspond to a declared asset
      (__ORIGIN__/tiles/... ↔ points.geojson; __ORIGIN__/raster/... ↔ raster.json).
    """
    origin_re = re.compile(r"__ORIGIN__/([^\s\"']+)")
    for d in _fixture_dirs():
        mapspec_path = d / "mapspec.json"
        if not mapspec_path.is_file():
            continue
        raw = mapspec_path.read_text(encoding="utf-8")
        origin_paths = origin_re.findall(raw)
        points_path = d / "points.geojson"
        has_points = points_path.is_file()
        raster_path = d / "raster.json"
        has_raster = raster_path.is_file()

        if has_points:
            # GeoJSON must parse as FeatureCollection
            data = json.loads(points_path.read_text(encoding="utf-8"))
            assert isinstance(data, dict), f"{d.name}: points.geojson not an object"
            assert data.get("type") == "FeatureCollection", f"{d.name}: points.geojson must be a FeatureCollection"
            feats = data.get("features")
            assert isinstance(feats, list) and len(feats) > 0, f"{d.name}: points.geojson must have non-empty features"
            for idx, feat in enumerate(feats):
                assert isinstance(feat, dict), f"{d.name}: points.geojson feature[{idx}] not an object"
                geom = feat.get("geometry")
                assert isinstance(geom, dict), f"{d.name}: feature[{idx}] missing geometry"
                assert "type" in geom and "coordinates" in geom, f"{d.name}: feature[{idx}] geometry missing type/coordinates"

            # mapspec must have a vector source with __ORIGIN__ tiles
            spec = json.loads(raw)
            sources = spec.get("sources", {})
            vector_sources = [v for v in sources.values() if isinstance(v, dict) and v.get("type") == "vector"]
            assert len(vector_sources) >= 1, f"{d.name}: contains points.geojson but mapspec has no vector source"
            has_origin_tile = False
            for src in vector_sources:
                tiles = src.get("tiles")
                if isinstance(tiles, list):
                    for t in tiles:
                        if isinstance(t, str) and t.startswith("__ORIGIN__/"):
                            has_origin_tile = True
            assert has_origin_tile, f"{d.name}: vector source tiles must use __ORIGIN__ prefix when points.geojson is present"

        if has_raster:
            # raster.json must be parseable and declare array/palette/bounds
            rdata = json.loads(raster_path.read_text(encoding="utf-8"))
            assert isinstance(rdata, dict), f"{d.name}: raster.json not an object"
            assert "array" in rdata, f"{d.name}: raster.json must contain 'array'"
            assert "palette" in rdata, f"{d.name}: raster.json must contain 'palette'"
            assert "bounds" in rdata, f"{d.name}: raster.json must contain 'bounds'"
            arr = rdata["array"]
            assert isinstance(arr, list) and len(arr) > 0, f"{d.name}: raster.json array must be non-empty 2D list"
            for r_idx, row in enumerate(arr):
                assert isinstance(row, list) and len(row) > 0, f"{d.name}: raster.json array row {r_idx} must be non-empty list"
                for v in row:
                    assert isinstance(v, (int, float)), f"{d.name}: raster.json array[{r_idx}] values must be numeric"
            # rectangular check
            row_lens = [len(r) for r in arr if isinstance(r, list)]
            assert len(set(row_lens)) == 1, f"{d.name}: raster.json array rows must have equal length"
            palette = rdata["palette"]
            assert isinstance(palette, str) and palette, f"{d.name}: raster.json palette must be non-empty string"
            bounds = rdata["bounds"]
            assert isinstance(bounds, (list, tuple)) and len(bounds) == 4, f"{d.name}: raster.json bounds must be [w,s,e,n]"
            assert all(isinstance(v, (int, float)) for v in bounds), f"{d.name}: raster.json bounds values must be numbers"

            # mapspec must have a raster source with __ORIGIN__ imageRef
            spec = json.loads(raw)
            sources = spec.get("sources", {})
            raster_sources = [v for v in sources.values() if isinstance(v, dict) and v.get("type") == "raster"]
            assert len(raster_sources) >= 1, f"{d.name}: contains raster.json but mapspec has no raster source"
            has_origin_raster = False
            for src in raster_sources:
                ref = src.get("imageRef")
                if isinstance(ref, str) and ref.startswith("__ORIGIN__/raster/"):
                    has_origin_raster = True
                # raster bounds must also be present in mapspec source
                assert isinstance(src.get("bounds"), (list, tuple)) and len(src.get("bounds", [])) == 4, (
                    f"{d.name}: raster source must declare bounds [w,s,e,n]"
                )
            assert has_origin_raster, f"{d.name}: raster source imageRef must use __ORIGIN__/raster/ prefix when raster.json is present"

        # Every __ORIGIN__ path must map to a declared asset
        for rel in origin_paths:
            if rel.startswith("tiles/"):
                assert has_points, (
                    f"{d.name}: mapspec references __ORIGIN__/{rel} but no points.geojson found in fixture dir"
                )
            elif rel.startswith("raster/"):
                assert has_raster, (
                    f"{d.name}: mapspec references __ORIGIN__/{rel} but no raster.json found in fixture dir"
                )
                # raster.json content already validated above when has_raster is True;
                # when referenced but file was just checked, also validate parseability
                if has_raster:
                    rdata = json.loads(raster_path.read_text(encoding="utf-8"))
                    assert isinstance(rdata.get("array"), list), f"{d.name}: raster.json array must be readable"
                    assert isinstance(rdata.get("palette"), str), f"{d.name}: raster.json palette must be readable"
                    assert isinstance(rdata.get("bounds"), (list, tuple)), f"{d.name}: raster.json bounds must be readable"
            else:
                assert False, f"{d.name}: __ORIGIN__/{rel} does not correspond to any declared asset source type (tiles/ or raster/)"
