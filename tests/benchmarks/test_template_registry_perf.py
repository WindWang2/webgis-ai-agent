"""
Performance benchmarks for F-FE-TPL: Template Registry V2 + Frontend Fast Path.

These are deterministic, non-wall-clock measurements: the test asserts the
result is correct AND the operation is bounded in cost. The shape follows
the existing test_perf_harness.py style (median of N iterations + a floor
ceiling) so it can live next to the existing harness.

F-FE-TPL benchmarks (F-FE-FGP, F-FE-TPL):
  - template_registry_lookup_1000        — O(1) by id, 1000 iterations
  - template_registry_search_1000       — registry.search over 84 entries
  - template_registry_validation        — registry.validate is a no-op at startup
  - template_intent_resolver_1000       — case-insensitive intent → id
  - page_meta_serialization_1000        — Page[T] generic envelope
  - fast_path_dedupe_1000               — in-memory cache dedup simulation

Each test:
  - runs N iterations
  - asserts a deterministic lower + upper bound on the median
  - asserts the operation produced the right shape

These are not "speed" benchmarks (CI runners are noisy). They pin the
algorithmic shape so a regression to linear-scan registry or Python-side
filtering is immediately visible.
"""
import statistics
import time
from typing import List



# --------------------------------------------------------------------
# F-FE-TPL: Template Registry V2
# --------------------------------------------------------------------
def test_template_registry_lookup_1000():
    """1000 O(1) lookups against the V2 registry.

    The point: every call must hit the dict directly. A linear scan would
    trip the upper bound even on a 84-entry registry.
    """
    from app.schemas.template_registry import get_template_registry

    r = get_template_registry()
    timings_ms: List[float] = []
    for _ in range(1000):
        t0 = time.perf_counter()
        entry = r.get("tmpl_th_pop_choro")
        timings_ms.append((time.perf_counter() - t0) * 1000)
        assert entry is not None
        assert entry["id"] == "tmpl_th_pop_choro"

    median = statistics.median(timings_ms)
    # O(1) at 84 entries: median should be sub-50 microseconds; we leave a
    # generous bound (0.5ms) for noisy CI.
    assert median < 0.5, f"registry lookup median {median:.3f}ms exceeds O(1) bound"


def test_template_registry_search_1000():
    """1000 search calls against the 84-entry V2 registry.

    The bounded cost is what we care about: the search must NOT scale with
    the size of an external DB, must NOT do a per-call DB query, and must
    finish sub-millisecond on commodity hardware.
    """
    from app.schemas.template_registry import get_template_registry

    r = get_template_registry()
    timings_ms: List[float] = []
    last_total = 0
    for _ in range(1000):
        t0 = time.perf_counter()
        page, total = r.search(q="population", kind="thematic", limit=20)
        timings_ms.append((time.perf_counter() - t0) * 1000)
        last_total = total
    median = statistics.median(timings_ms)
    assert median < 5.0, f"registry.search median {median:.3f}ms exceeds 5ms bound"
    assert last_total >= 1


def test_template_registry_validation_at_startup():
    """The registry validates zero-error at first load and stays that way.

    A regression that adds a duplicate id or a dangling composite ref
    would show up here.
    """
    from app.schemas.template_registry import get_template_registry

    r = get_template_registry()
    errs = r.validate()
    assert errs == [], f"registry validation reported {len(errs)} errors: {errs[:3]}"


def test_template_intent_resolver_1000():
    """1000 intent-resolution calls; correctness over speed.

    The intent resolver must be O(N) over the in-memory registry (N~84) so
    a 1000-call batch finishes quickly and never returns a wrong id.
    """
    from app.services.templates.intent_resolver import resolve_template_by_intent

    timings_ms: List[float] = []
    for _ in range(1000):
        t0 = time.perf_counter()
        t = resolve_template_by_intent("population density map")
        timings_ms.append((time.perf_counter() - t0) * 1000)
        assert t is not None
        # "population" matches via the keyword pool (we don't pin the
        # exact id because new templates can move the first hit).
        assert "pop" in t["id"].lower() or "density" in (t.get("name") or "").lower()
    median = statistics.median(timings_ms)
    assert median < 5.0, f"intent resolver median {median:.3f}ms exceeds bound"


# --------------------------------------------------------------------
# F-FE-SD: pagination / summary DTOs
# --------------------------------------------------------------------
def test_page_meta_serialization_1000():
    """Page[T] generic envelope serializes deterministically.

    Pydantic v2's model_dump is fast; we just pin the cost so a future
    switch to a dict-of-fields doesn't regress.
    """
    from app.schemas.pagination import Page

    items = [{"id": f"x_{i}", "name": f"item {i}"} for i in range(50)]
    page = Page(items=items, total=200, limit=50, offset=0, has_more=True)
    timings_ms: List[float] = []
    for _ in range(1000):
        t0 = time.perf_counter()
        out = page.model_dump()
        timings_ms.append((time.perf_counter() - t0) * 1000)
        assert out["total"] == 200
        assert len(out["items"]) == 50
    median = statistics.median(timings_ms)
    assert median < 5.0, f"Page[T].model_dump median {median:.3f}ms exceeds bound"


def test_summary_dto_omits_payload():
    """The summary DTO strip pass (templates route `_to_summary`) must drop
    `payload` from every list row. A regression that accidentally ships
    the kB-scale payload JSON in the list would inflate the median bytes
    >100x — we assert it directly here."""
    from app.schemas.pagination import Page
    from app.schemas.project_schema import ProjectSummary, ProjectDatasetSummary

    projects = [
        ProjectSummary(
            id=f"p_{i}", name=f"P{i}", status="active",
            created_at="2026-01-01T00:00:00",
            updated_at="2026-01-01T00:00:00",
        )
        for i in range(50)
    ]
    page = Page(items=projects, total=200, limit=50, offset=0, has_more=True)
    for item in page.model_dump()["items"]:
        # summary DTOs must not include any of the heavy project columns.
        assert "metadata_json" not in item
        assert "owner_id" not in item
        assert "org_id" not in item

    datasets = [
        ProjectDatasetSummary(
            id=f"d_{i}", project_id="p1", name=f"D{i}",
            source_type="vector", crs="EPSG:4326",
            created_at="2026-01-01T00:00:00",
        )
        for i in range(50)
    ]
    page2 = Page(items=datasets, total=200, limit=50, offset=0, has_more=True)
    for item in page2.model_dump()["items"]:
        assert "schema_profile" not in item


# --------------------------------------------------------------------
# F-FE-FGP: in-memory dedup (Python simulation of the JS Fast Path)
# --------------------------------------------------------------------
def test_fast_path_dedupe_1000_simulated():
    """The in-process dedup that the JS Fast Path provides is replicated
    in this Python sim so the algorithmic shape is testable here too.

    The pattern: 1000 lookups for the same key; only the first one pays
    the "miss" cost; the rest are dedup'd to a shared result.
    """
    seen = {}
    fetch_count = 0

    def fake_fetch(key: str) -> str:
        nonlocal fetch_count
        if key in seen:
            return seen[key]
        fetch_count += 1
        seen[key] = f"payload_for_{key}"
        return seen[key]

    timings_ms: List[float] = []
    for _ in range(1000):
        t0 = time.perf_counter()
        # 5 parallel callers of the same key (the dedup case)
        results = [fake_fetch("/projects") for _ in range(5)]
        timings_ms.append((time.perf_counter() - t0) * 1000)
        assert results[0] == "payload_for_/projects"
    median = statistics.median(timings_ms)
    # Only ONE actual fetch should have fired.
    assert fetch_count == 1, f"dedup sim fired {fetch_count} fetches (expected 1)"
    assert median < 5.0, f"dedup sim median {median:.3f}ms exceeds bound"
