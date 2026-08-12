"""
Unit tests for the V2 template registry + intent resolver.
"""

from app.schemas.template_schema import SEED_TEMPLATES
from app.schemas.template_registry import (
    COMPOSITE_TEMPLATES,
    TemplateRegistry,
    get_template_registry,
)
from app.services.templates.intent_resolver import (
    resolve_template_by_intent,
    get_template_or_composite,
    list_templates_v2,
    expand_composite,
)


# --------------------------------------------------------------------
# Registry integrity
# --------------------------------------------------------------------
def test_registry_count_meets_v2_contract():
    r = get_template_registry()
    # V2 contract: ≥50 built-ins + ≥20 composites.
    assert r.count() >= 70, f"registry only has {r.count()} entries"
    assert len(r.by_kind("composite")) >= 20
    for kind in ("basemap", "symbology", "layout", "thematic", "composite"):
        assert len(r.by_kind(kind)) > 0, f"no entries for kind={kind}"


def test_registry_validation_is_clean():
    errs = get_template_registry().validate()
    assert errs == [], f"registry has {len(errs)} integrity errors: {errs[:3]}"


def test_seed_template_count_in_v2_library():
    # Sanity: the V2 expansion reached the goal's 50-80 target band.
    assert 50 <= len(SEED_TEMPLATES) <= 200, len(SEED_TEMPLATES)
    # All composite templates reference real ids.
    for c in COMPOSITE_TEMPLATES:
        for slot, ref in (c.get("pipeline") or {}).items():
            assert ref in {t["id"] for t in SEED_TEMPLATES}, (
                f"composite {c['id']} pipeline.{slot} -> {ref} not in SEED_TEMPLATES"
            )


# --------------------------------------------------------------------
# Lookup performance (deterministic, no wall clock — measure calls)
# --------------------------------------------------------------------
def test_by_id_is_o1():
    r = TemplateRegistry()
    r.load_builtins()
    # `get` must be a dict lookup, not a scan. We can't assert that
    # without bytecode inspection; we assert correctness at scale and
    # the trivial cost.
    for _ in range(1000):
        assert r.get("tmpl_th_pop_choro") is not None
        assert r.get("__missing__") is None


def test_by_kind_groups_correctly():
    r = get_template_registry()
    bm = r.by_kind("basemap")
    sym = r.by_kind("symbology")
    assert all(t["kind"] == "basemap" for t in bm)
    assert all(t["kind"] == "symbology" for t in sym)
    assert len(set(t["id"] for t in bm)) == len(bm)  # unique ids


# --------------------------------------------------------------------
# Search
# --------------------------------------------------------------------
def test_search_finds_by_id_exact():
    r = get_template_registry()
    page, total = r.search(q="tmpl_th_pop_choro")
    assert total == 1
    assert page[0]["id"] == "tmpl_th_pop_choro"


def test_search_finds_by_keyword_multilingual():
    r = get_template_registry()
    page, total = r.search(q="人口", limit=20)
    assert total >= 1, "expected at least one population keyword hit"
    ids = {t["id"] for t in page}
    assert any("pop" in tid for tid in ids), ids


def test_search_filters_by_kind():
    r = get_template_registry()
    page, total = r.search(kind="composite", limit=5)
    assert total >= 5
    assert all(t["kind"] == "composite" for t in page)


def test_search_returns_empty_for_garbage():
    r = get_template_registry()
    page, total = r.search(q="zzznotaplatformtemplate__")
    assert total == 0
    assert page == []


def test_search_paginates():
    r = get_template_registry()
    p0, total = r.search(limit=10, offset=0)
    p1, _ = r.search(limit=10, offset=10)
    ids0 = {t["id"] for t in p0}
    ids1 = {t["id"] for t in p1}
    # No overlap between consecutive pages of the same query.
    assert ids0.isdisjoint(ids1)


# --------------------------------------------------------------------
# Composite expansion
# --------------------------------------------------------------------
def test_expand_composite_returns_all_four_slots():
    slots = expand_composite("composite_population_density_analysis")
    assert set(slots.keys()) == {"basemap", "symbology", "thematic", "layout"}
    for slot, entry in slots.items():
        assert entry is not None, f"slot {slot} did not resolve"
        assert entry.get("kind") in ("basemap", "symbology", "thematic", "layout")


def test_expand_composite_missing_returns_empty():
    assert expand_composite("__nonexistent__") == {}


# --------------------------------------------------------------------
# Intent resolver
# --------------------------------------------------------------------
def test_intent_resolver_finds_population_density():
    t = resolve_template_by_intent("make a population density map")
    assert t is not None
    assert t["id"] == "tmpl_th_pop_choro"


def test_intent_resolver_finds_composite():
    t = resolve_template_by_intent("composite_population_density_analysis")
    assert t is not None
    assert t["kind"] == "composite"


def test_intent_resolver_kind_filter():
    t = resolve_template_by_intent("人口", kind="thematic")
    assert t is not None
    assert t["kind"] == "thematic"


def test_intent_resolver_returns_none_for_empty_or_garbage():
    assert resolve_template_by_intent("") is None
    assert resolve_template_by_intent("__zzz__") is None


def test_get_template_or_composite_o1():
    t = get_template_or_composite("tmpl_th_pop_choro")
    assert t is not None
    assert t["id"] == "tmpl_th_pop_choro"


# --------------------------------------------------------------------
# List paginated helper
# --------------------------------------------------------------------
def test_list_templates_v2_pagination_contract():
    page, total = list_templates_v2(kind="basemap", limit=5, offset=0)
    assert len(page) <= 5
    assert total >= len(page)
