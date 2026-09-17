"""Generate the deterministic standards QA fixture matrix (ADR-0200).

Run from the worktree root:
    ./.venv/Scripts/python tests/cartography/fixtures/standards_cases/_generate.py [out_dir]

Each case dir: ``mapspec.json`` + ``profile.json`` + ``expected.json``.
The expectation table below is HAND-AUTHORED from the core pack semantics and
the engine single-sources (context_matrix verdicts measured 2026-09-17:
YlOrRd@5 fails cvd_deuteranopia/protanopia via name AND raw path; Pastel1@5
fails all four contexts). The regression test asserts the engine reproduces
every expectation — drift in either direction is red.

Deterministic: no randomness, no clocks, stable ordering.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, Tuple

HERE = Path(__file__).parent
REPO_ROOT = HERE.resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

PUB = "publication_print"
ANA_P = "analysis_print"
ANA_S = "analysis_screen"
EXP_M = "exploration_mobile"
BRF_S = "briefing_screen"
INF = "inferred"

PROFILES: Dict[str, Any] = {
    PUB: {"purpose": "publication", "audience": "public", "medium": "print"},
    ANA_P: {"purpose": "analysis", "audience": "public", "medium": "print"},
    ANA_S: {"purpose": "analysis", "audience": "public", "medium": "screen"},
    EXP_M: {"purpose": "exploration", "audience": "public", "medium": "mobile"},
    BRF_S: {"purpose": "briefing", "audience": "public", "medium": "screen"},
    INF: None,  # inferred from mapspec (non-strict)
}

R_REQ = "CORE.REQUIRED_COMPONENTS"
R_SRC = "CORE.SOURCE_DISCLOSURE"
R_LEG = "CORE.LEGEND_PRESENT"
R_UNIT = "CORE.LEGEND_UNIT_DISCLOSURE"
R_CNT = "CORE.COUNT_VS_RATE"
R_CVD = "CORE.CVD_SAFE_PALETTE"
R_PRT = "CORE.PRINT_LEGIBLE_PALETTE"
R_CLS = "CORE.CLASSIFICATION_DECLARED"
R_LBL = "CORE.LABEL_DENSITY_DECLARED"
R_TIME = "CORE.TIME_DISCLOSURE"
R_UNC = "CORE.UNCERTAINTY_DISCLOSURE"
R_PROF = "CORE.THEMATIC_PROFILE_DECLARED"

YLORRD_RAW = ["#ffffb2", "#fed976", "#fd8d3c", "#f03b20", "#bd0026"]

# ── mapspec templates ──────────────────────────────────────────────────────


def _profile_block(**fields):
    base = {
        "crs": "EPSG:4326", "crs_status": "explicit",
        "bbox": [-125.0, 24.0, -66.0, 50.0],
        "featureCount": 3100,
        "geometryTypes": ["Polygon", "MultiPolygon"],
        "fields": {"median_income": {"type": "number"},
                   "state_name": {"type": "string"}},
    }
    base.update(fields)
    return base


def _components(attribution_text="数据来源：美国人口普查局 ACS 2022"):
    return [
        {"id": "c-title", "type": "title", "enabled": True},
        {"id": "c-scale", "type": "scale_bar", "enabled": True},
        {"id": "c-north", "type": "north_arrow", "enabled": True},
        {"id": "c-attr", "type": "attribution", "enabled": True,
         "options": {"text": attribution_text}},
        {"id": "c-legend", "type": "legend", "enabled": True},
        {"id": "c-grat", "type": "graticule", "enabled": True},
    ]


def _choro_legend_spec(field="median_income", palette="Viridis", **over):
    spec = {
        "type": "graduated", "field": field,
        "palette": palette, "method": "quantiles",
        "title": "家庭收入中位数", "unit": "美元",
        "min": 20000.0, "max": 120000.0,
        "breaks": [40000.0, 60000.0, 80000.0, 100000.0],
    }
    spec.update(over)
    return spec


def _choro(**over):
    spec = {
        "version": "1.0",
        "cartographic_profile": "thematic_map",
        "sources": {"s1": {"type": "geojson", "ref": "ref:dataset-1",
                           "profile": _profile_block()}},
        "layers": [{"id": "l1", "source": "s1", "type": "fill",
                    "legend_spec": _choro_legend_spec()}],
        "layout": {"legend": {"visible": True, "title": "家庭收入中位数"},
                   "components": _components()},
    }
    spec.update(over)
    return spec


def _points():
    return {
        "version": "1.0",
        "cartographic_profile": "general_analysis",
        "sources": {"s1": {"type": "geojson", "ref": "ref:dataset-2",
                           "profile": _profile_block(
                               featureCount=5000,
                               geometryTypes=["Point"],
                               fields={"city_name": {"type": "string"},
                                       "median_income": {"type": "number"}})}},
        "layers": [{"id": "l-pts", "source": "s1", "type": "circle",
                    "paint": {"circle-radius": 4, "circle-color": "#1f77b4"}}],
        "layout": {"components": _components()},
    }


def _temporal():
    spec = _choro()
    spec["sources"]["s1"]["profile"] = _profile_block(hasTimeField=True)
    return spec


def _uncertainty():
    spec = _choro()
    spec["sources"]["s1"]["profile"]["fields"]["margin_of_error"] = {"type": "number"}
    return spec


def _basemap():
    return {
        "version": "1.0",
        "cartographic_profile": "general_analysis",
        "sources": {"s1": {"type": "geojson", "ref": "ref:dataset-3",
                           "profile": _profile_block()}},
        "layers": [
            {"id": "l-roads", "source": "s1", "type": "line",
             "paint": {"line-color": "#888888", "line-width": 1}},
            {"id": "l-boundaries", "source": "s1", "type": "line",
             "paint": {"line-color": "#444444", "line-width": 2}},
        ],
        "layout": {"components": _components()},
    }


def _legacy():
    return {
        "version": "1.0",
        "sources": {"s1": {"type": "geojson",
                           "url": "https://example.test/legacy.geojson"}},
        "layers": [{"id": "l1", "source": "s1", "type": "fill",
                    "paint": {"fill-color": "#334455", "fill-opacity": 0.8}}],
    }


TEMPLATES = {
    "choro": _choro,
    "points": _points,
    "temporal": _temporal,
    "uncertainty": _uncertainty,
    "basemap": _basemap,
    "legacy": _legacy,
}

# ── base expectations (violations before mutations) ────────────────────────

BASE_EXPECT: Dict[str, Dict[str, tuple]] = {
    "choro": {PUB: (), ANA_P: (), ANA_S: (), EXP_M: (), BRF_S: (), INF: ()},
    "points": {
        PUB: (), ANA_P: (),
        ANA_S: (R_LBL,), EXP_M: (R_LBL,), BRF_S: (R_LBL,), INF: (R_LBL,),
    },
    "temporal": {
        PUB: (R_TIME,), ANA_P: (R_TIME,), ANA_S: (R_TIME,),
        EXP_M: (), BRF_S: (R_TIME,), INF: (R_TIME,),
    },
    "uncertainty": {
        PUB: (R_UNC,), ANA_P: (R_UNC,), ANA_S: (R_UNC,),
        EXP_M: (), BRF_S: (), INF: (R_UNC,),
    },
    "basemap": {PUB: (), ANA_P: (), ANA_S: (), EXP_M: (), BRF_S: (), INF: ()},
    "legacy": {
        PUB: (R_REQ, R_SRC), ANA_P: (R_REQ, R_SRC), ANA_S: (R_REQ, R_SRC),
        EXP_M: (R_REQ,), BRF_S: (R_REQ,), INF: (R_REQ,),
    },
}

# ── mutations ──────────────────────────────────────────────────────────────
# Each mutation: (fn, delta) where delta maps profile class → (add, remove).
# ``ALL`` is expanded to every class at generation time.


def _strip_components(spec):
    spec["layout"]["components"] = []


def _placeholder(spec):
    for c in spec["layout"]["components"]:
        if c["type"] == "attribution":
            c["options"]["text"] = "数据来源：—（待补充）"


def _no_legend(spec):
    del spec["layers"][0]["legend_spec"]
    spec["layers"][0]["paint"] = {
        "fill-color": {
            "property": "median_income", "method": "step",
            "stops": [[40000.0, "#ffeda0"], [80000.0, "#f03b20"]],
            "default": "#ffffcc",
        },
    }
    spec["layout"]["legend"] = {"visible": False}
    spec["layout"]["components"] = [
        c for c in spec["layout"]["components"] if c["type"] != "legend"]


def _panel_only(spec):
    spec["layout"]["components"] = [
        c for c in spec["layout"]["components"] if c["type"] != "legend"]


def _missing_unit(spec):
    del spec["layers"][0]["legend_spec"]["unit"]


def _missing_title(spec):
    del spec["layers"][0]["legend_spec"]["title"]
    del spec["layout"]["legend"]["title"]


def _missing_unit_and_title(spec):
    _missing_unit(spec)
    _missing_title(spec)


def _count_field(spec):
    spec["layers"][0]["legend_spec"]["field"] = "population"
    spec["sources"]["s1"]["profile"]["fields"]["population"] = {"type": "number"}


def _rate_field(spec):
    spec["layers"][0]["legend_spec"]["field"] = "population_density"
    spec["sources"]["s1"]["profile"]["fields"]["population_density"] = {"type": "number"}


def _density_field(spec):
    spec["layers"][0]["legend_spec"]["field"] = "household_density"
    spec["sources"]["s1"]["profile"]["fields"]["household_density"] = {"type": "number"}


def _cvd_fail(spec):
    spec["layers"][0]["legend_spec"]["palette"] = "YlOrRd"


def _cvd_fail_raw(spec):
    legend = spec["layers"][0]["legend_spec"]
    del legend["palette"]
    legend["palette_colors"] = list(YLORRD_RAW)


def _print_fail(spec):
    spec["layers"][0]["legend_spec"]["palette"] = "Pastel1"


def _no_method(spec):
    del spec["layers"][0]["legend_spec"]["method"]


def _profile_undeclared(spec):
    del spec["cartographic_profile"]


def _with_time(spec):
    spec["time"] = {"start": "2022-01-01", "end": "2022-12-31"}


def _with_panel(spec):
    spec["layout"]["components"].append(
        {"id": "c-unc", "type": "uncertainty_panel", "enabled": True})


def _dense_budgeted(spec):
    spec["layers"][0]["label"] = {"field": "city_name", "mode": "top_n", "topN": 40}


def _sparse(spec):
    spec["sources"]["s1"]["profile"]["featureCount"] = 50


def _hover_labeled(spec):
    spec["layers"][0]["label"] = {"field": "city_name", "mode": "hover_only"}


def _legacy_partial(spec):
    spec["cartographic_profile"] = "thematic_map"
    spec["layout"] = {"components": _components(
        attribution_text="数据来源：—（待补充）")}  # 占位署名


def _legacy_partial_real(spec):
    _legacy_partial(spec)
    for c in spec["layout"]["components"]:
        if c["type"] == "attribution":
            c["options"]["text"] = "数据来源：1990 年纸质地图数字化"


def _legacy_remediated(spec):
    _legacy_partial_real(spec)
    spec["layers"][0]["legend_spec"] = _choro_legend_spec()


_ADD_ALL = lambda rules: {c: (set(rules), set()) for c in PROFILES}  # noqa: E731


def _delta(**per_class):
    """delta(publication_print=("+R_SRC",), inferred=("-R_LEG",)) → dict form."""
    out = {}
    for cls, adds_removed in per_class.items():
        adds = {r[1:] for r in adds_removed if r.startswith("+")}
        removes = {r[1:] for r in adds_removed if r.startswith("-")}
        out[cls] = (adds, removes)
    return out


_SRC_THREE = {
    PUB: ("+" + R_SRC,), ANA_P: ("+" + R_SRC,), ANA_S: ("+" + R_SRC,),
}
_D_SRC_PUB_ANA = _delta(**_SRC_THREE)
_D_SRC_PUB_ANA_INF = _delta(**{**_SRC_THREE, INF: ("+" + R_SRC,)})
_D_UNIT = _delta(**{
    PUB: ("+" + R_UNIT,), ANA_P: ("+" + R_UNIT,), ANA_S: ("+" + R_UNIT,),
    BRF_S: ("+" + R_UNIT,), INF: ("+" + R_UNIT,),
})
_D_COUNT = _delta(**{
    PUB: ("+" + R_CNT,), ANA_P: ("+" + R_CNT,), ANA_S: ("+" + R_CNT,),
    INF: ("+" + R_CNT,),
})
_D_CLASS = _delta(**{
    PUB: ("+" + R_CLS,), ANA_P: ("+" + R_CLS,), ANA_S: ("+" + R_CLS,),
    INF: ("+" + R_CLS,),
})

CHORO_MUTATIONS = {
    "clean": (lambda s: None, {}),
    "strip_components": (_strip_components, _delta(**{
        **{c: ("+" + R_REQ, "+" + R_SRC) for c in (PUB, ANA_P, ANA_S)},
        EXP_M: ("+" + R_REQ,), BRF_S: ("+" + R_REQ,),
        INF: ("+" + R_REQ, "+" + R_SRC),
    })),
    "placeholder_attribution": (_placeholder, _D_SRC_PUB_ANA_INF),
    "no_legend": (_no_legend, _ADD_ALL([R_LEG, R_REQ])),
    "panel_only_legend": (_panel_only, _ADD_ALL([R_REQ])),
    "missing_unit": (_missing_unit, _D_UNIT),
    "missing_title": (_missing_title, _D_UNIT),
    "missing_unit_and_title": (_missing_unit_and_title, _D_UNIT),
    "count_field": (_count_field, _D_COUNT),
    "rate_field": (_rate_field, {}),
    "density_field": (_density_field, {}),
    "cvd_fail": (_cvd_fail, _ADD_ALL([R_CVD])),
    "cvd_fail_raw_colors": (_cvd_fail_raw, _ADD_ALL([R_CVD])),
    "print_fail_palette": (_print_fail, _delta(**{
        **{c: ("+" + R_CVD, "+" + R_PRT) for c in (PUB, ANA_P)},
        **{c: ("+" + R_CVD,) for c in (ANA_S, EXP_M, BRF_S, INF)},
    })),
    "no_method": (_no_method, _D_CLASS),
    "profile_undeclared": (_profile_undeclared, _ADD_ALL([R_PROF])),
}

POINTS_MUTATIONS = {
    "clean": (lambda s: None, {}),
    "strip_components": (_strip_components, _delta(**{
        **{c: ("+" + R_REQ, "+" + R_SRC) for c in (PUB, ANA_P, ANA_S)},
        EXP_M: ("+" + R_REQ,), BRF_S: ("+" + R_REQ,), INF: ("+" + R_REQ,),
    })),
    "placeholder_attribution": (_placeholder, _D_SRC_PUB_ANA),
    "dense_budgeted": (_dense_budgeted, _delta(**{
        ANA_S: ("-" + R_LBL,), EXP_M: ("-" + R_LBL,),
        BRF_S: ("-" + R_LBL,), INF: ("-" + R_LBL,),
    })),
    "sparse": (_sparse, _delta(**{
        ANA_S: ("-" + R_LBL,), EXP_M: ("-" + R_LBL,),
        BRF_S: ("-" + R_LBL,), INF: ("-" + R_LBL,),
    })),
    "hover_labeled": (_hover_labeled, _delta(**{
        ANA_S: ("-" + R_LBL,), EXP_M: ("-" + R_LBL,),
        BRF_S: ("-" + R_LBL,), INF: ("-" + R_LBL,),
    })),
}

TEMPORAL_MUTATIONS = {
    "clean": (lambda s: None, {}),
    "strip_components": (_strip_components, _delta(**{
        **{c: ("+" + R_REQ, "+" + R_SRC) for c in (PUB, ANA_P, ANA_S)},
        EXP_M: ("+" + R_REQ,), BRF_S: ("+" + R_REQ,),
        INF: ("+" + R_REQ, "+" + R_SRC),
    })),
    "placeholder_attribution": (_placeholder, _D_SRC_PUB_ANA_INF),
    "with_time": (_with_time, _delta(**{
        PUB: ("-" + R_TIME,), ANA_P: ("-" + R_TIME,), ANA_S: ("-" + R_TIME,),
        BRF_S: ("-" + R_TIME,), INF: ("-" + R_TIME,),
    })),
    "no_legend": (_no_legend, _ADD_ALL([R_LEG, R_REQ])),
}

UNCERTAINTY_MUTATIONS = {
    "clean": (lambda s: None, {}),
    "strip_components": (_strip_components, _delta(**{
        **{c: ("+" + R_REQ, "+" + R_SRC) for c in (PUB, ANA_P, ANA_S)},
        EXP_M: ("+" + R_REQ,), BRF_S: ("+" + R_REQ,),
        INF: ("+" + R_REQ, "+" + R_SRC),
    })),
    "placeholder_attribution": (_placeholder, _D_SRC_PUB_ANA_INF),
    "with_panel": (_with_panel, _delta(**{
        PUB: ("-" + R_UNC,), ANA_P: ("-" + R_UNC,), ANA_S: ("-" + R_UNC,),
        INF: ("-" + R_UNC,),
    })),
}

BASEMAP_MUTATIONS = {
    "clean": (lambda s: None, {}),
    "strip_components": (_strip_components, _delta(**{
        **{c: ("+" + R_REQ, "+" + R_SRC) for c in (PUB, ANA_P, ANA_S)},
        EXP_M: ("+" + R_REQ,), BRF_S: ("+" + R_REQ,), INF: ("+" + R_REQ,),
    })),
    "placeholder_attribution": (_placeholder, _D_SRC_PUB_ANA),
}

_PARTIAL_REM = {c: ("-" + R_REQ, "-" + R_SRC) for c in PROFILES}
LEGACY_MUTATIONS = {
    "clean": (lambda s: None, {}),
    "partial": (_legacy_partial, _delta(**{
        **_PARTIAL_REM,
        PUB: ("-" + R_REQ, "+" + R_SRC),
        ANA_P: ("-" + R_REQ, "+" + R_SRC),
        ANA_S: ("-" + R_REQ, "+" + R_SRC),
        INF: ("-" + R_REQ, "+" + R_SRC),
    })),
    "partial_real_source": (_legacy_partial_real, _delta(**_PARTIAL_REM)),
    "remediated": (_legacy_remediated, _delta(**_PARTIAL_REM)),
}

MUTATIONS = {
    "choro": CHORO_MUTATIONS,
    "points": POINTS_MUTATIONS,
    "temporal": TEMPORAL_MUTATIONS,
    "uncertainty": UNCERTAINTY_MUTATIONS,
    "basemap": BASEMAP_MUTATIONS,
    "legacy": LEGACY_MUTATIONS,
}

# ── generation ─────────────────────────────────────────────────────────────


def _error_rule_ids() -> set:
    from app.lib.cartography.standards.packs import get_core_pack

    return {
        r.rule_id for r in get_core_pack().rules if r.severity == "error"
    }


def build_cases() -> Dict[Tuple[str, str, str], Dict[str, Any]]:
    """(template, mutation, profile_class) → case payloads."""
    cases: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
    for template_name, builder in TEMPLATES.items():
        for mutation_name, (fn, delta) in MUTATIONS[template_name].items():
            spec = builder()
            fn(spec)
            for profile_class, axes in PROFILES.items():
                adds, removes = delta.get(profile_class, (set(), set()))
                violations = sorted(
                    (set(BASE_EXPECT[template_name][profile_class]) | adds)
                    - removes
                )
                case_id = f"{template_name}__{mutation_name}__{profile_class}"
                cases[(template_name, mutation_name, profile_class)] = {
                    "case_id": case_id,
                    "mapspec": spec,
                    "profile": axes,
                    "expected": {
                        "violations": violations,
                        "status": "violations" if violations else "pass",
                    },
                }
    return cases


def generate(out_dir: Path = HERE) -> int:
    error_rules = _error_rule_ids()
    cases = build_cases()
    if len(cases) < 200:
        raise SystemExit(f"fixture matrix shrunk to {len(cases)} (<200) — investigate")
    if out_dir != HERE:
        out_dir.mkdir(parents=True, exist_ok=True)
    for (template, mutation, profile_class), payload in sorted(cases.items()):
        case_dir = out_dir / f"{template}__{mutation}__{profile_class}"
        case_dir.mkdir(exist_ok=True)
        expected = dict(payload["expected"])
        axes = payload["profile"]
        strict = axes is not None
        blocking = bool(set(expected["violations"]) & error_rules) and strict
        expected["gate"] = "block" if blocking else "allow"
        (case_dir / "mapspec.json").write_text(
            json.dumps(payload["mapspec"], ensure_ascii=False, indent=1, sort_keys=True) + "\n",
            encoding="utf-8")
        (case_dir / "profile.json").write_text(
            json.dumps({"profile": axes}, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
            encoding="utf-8")
        (case_dir / "expected.json").write_text(
            json.dumps(expected, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
            encoding="utf-8")
    return len(cases)


if __name__ == "__main__":
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE
    count = generate(out)
    print(f"generated {count} standards cases under {out}")
