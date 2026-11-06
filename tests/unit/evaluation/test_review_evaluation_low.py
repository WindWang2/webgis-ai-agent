"""Evaluation isolation and measurement honesty regressions."""
import os
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from app.evaluation import index, retrieval_corpus
from app.evaluation.runner import _reduce, _resolve_fixture_arguments
from app.evaluation.runtime_metrics import context_metrics, execution_metrics, routing_metrics, gis_correctness_metrics


def test_corpus_cache_returns_independent_nested_cases_and_builder_changes_refresh():
    original = SimpleNamespace(id="X-one", nested={"value": [1]})
    registration = index.CorpusRegistration("review-isolation", "X-", lambda: [original])
    one = index.corpus_cases(registration)
    one.clear()
    two = index.corpus_cases(registration)
    two[0].nested["value"].append(2)
    assert index.corpus_cases(registration)[0].nested == {"value": [1]}
    other = index.CorpusRegistration("review-isolation", "X-", lambda: [SimpleNamespace(id="X-two")])
    assert index.corpus_cases(other)[0].id == "X-two"
    index._BUILDER_CACHE.pop("review-isolation", None)


def test_manifest_rejects_wrong_prefix_and_empty_prefix_collisions(monkeypatch):
    registrations = [index.CorpusRegistration("review-prefix", "X-", lambda: [SimpleNamespace(id="Y-one")])]
    monkeypatch.setattr(index, "_all_registrations", lambda: registrations)
    with pytest.raises(ValueError, match="outside prefix"):
        index.corpus_manifest()
    registrations[:] = [index.CorpusRegistration("a", "Z-", lambda: []), index.CorpusRegistration("b", "Z-", lambda: [])]
    with pytest.raises(ValueError, match="claimed by both"):
        index.corpus_manifest()
    for name in ("review-prefix", "a", "b"):
        index._BUILDER_CACHE.pop(name, None)


def test_even_sampling_returns_exact_requested_count(monkeypatch):
    monkeypatch.setattr(retrieval_corpus, "get_retrieval_corpus", lambda: list(range(801)))
    sample = retrieval_corpus.get_retrieval_sample(800)
    assert len(sample) == len(set(sample)) == 800
    assert sample == retrieval_corpus.get_retrieval_sample(800)
    assert retrieval_corpus.get_retrieval_sample(0) == []


def test_sum_rejects_ambiguous_numeric_rows_and_resolves_nested_fixtures():
    assert _reduce([{"count": 2, "area": 100}], "sum") is None
    assert _reduce([{"name": "a", "count": 2}], "sum") == 2
    args = {"nested": [{"ref": "fixture:districts"}], "ordinary": "districts"}
    assert _resolve_fixture_arguments(args) == {"nested": [{"ref": "districts"}], "ordinary": "districts"}
    assert args["nested"][0]["ref"] == "fixture:districts"


def test_unmeasured_metrics_are_none_and_overflow_does_not_imply_truncation():
    for metric in (routing_metrics([]), execution_metrics([]), gis_correctness_metrics([]), context_metrics([])):
        data = metric.as_dict()
        assert all(value is None for name, value in data.items() if name not in {"calls", "turns", "cases", "reports"})
    assert context_metrics([{"over_budget": True, "violations": ["over_budget:total"]}]).truncation_incidence == 0
    assert context_metrics([{"truncated": ["old-turn"], "violations": []}]).truncation_incidence == 1
    assert context_metrics([{}]).schema_token_ratio is None


@pytest.mark.asyncio
async def test_simulation_blocks_destructive_tool_before_dispatch_and_clears_repairs():
    from app.evaluation.replay import simulate_agent_loop, ScriptedCall
    from app.tools.argument_normalization import normalization_report_var
    class Registry:
        calls = []
        def descriptor(self, name):
            return SimpleNamespace(destructive_level=3 if name == "destroy" else 0, effective_security_tier=1, side_effect="pure")
        async def dispatch(self, name, args, sid):
            self.calls.append(name)
            return {"success": True}
    registry = Registry()
    normalization_report_var.set([SimpleNamespace(as_dict=lambda: {"old": True})])
    report = await simulate_agent_loop(registry, "review", [ScriptedCall("destroy", {}, expect_error_code="TIER3_CONFIRMATION_REQUIRED", expect_outcome="error"), ScriptedCall("read", {})])
    assert report.invariants_held, report.violations
    assert registry.calls == ["read"]
    assert all(step["repairs"] == [] for step in report.steps)


def test_hermetic_missions_have_independent_clocks_and_do_not_change_process_env():
    from app.evaluation.mission_driver import hermetic_mission_runtime
    from app.services.mission_runtime import store

    original_clock = store._utcnow
    original_env = dict(os.environ)
    def run(seed):
        with hermetic_mission_runtime() as (runtime, clock):
            before = runtime.store._now()
            clock.advance(seed)
            assert (runtime.store._now() - before).total_seconds() == seed
            return runtime.store._clock
    with ThreadPoolExecutor(max_workers=2) as executor:
        clocks = list(executor.map(run, (10, 100)))
    assert clocks[0] != clocks[1]
    assert store._utcnow is original_clock
    assert dict(os.environ) == original_env


def test_corpus_guards_survive_python_optimization():
    import subprocess
    import sys
    code = '''from app.evaluation import hard_negative_corpus as corpus
original = corpus.GISBenchmarkCase
def duplicate(**kwargs):
    kwargs['id'] = 'HN-duplicate'
    return original(**kwargs)
corpus.GISBenchmarkCase = duplicate
try:
    corpus.build_hard_negative_corpus()
except ValueError:
    pass
else:
    raise RuntimeError('duplicate guard disabled')
'''
    result = subprocess.run([sys.executable, "-O", "-c", code], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr


@pytest.mark.asyncio
async def test_execute_assertions_use_full_error_and_nested_fixture_arguments():
    from app.evaluation.case import GISBenchmarkCase, ScriptStep
    from app.evaluation.runner import GISBenchmarkRunner
    class Registry:
        async def dispatch(self, name, args, *, session_id):
            assert args["nested"][0]["data"] == "schools"
            return {"success": False, "error": "x" * 400 + "EXPECTED_SUFFIX"}
        def list_tools(self):
            return ["read"]
    result = await GISBenchmarkRunner(Registry())._run_execute_tier(
        GISBenchmarkCase(id="review-error", name="full error", group="form", query="q",
            script=[ScriptStep(tool="read", args={"nested": [{"data": "fixture:schools"}]}, expect_error_contains="EXPECTED_SUFFIX")]), "review")
    assert result["failures"] == []
