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
