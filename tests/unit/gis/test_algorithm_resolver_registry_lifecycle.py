"""Resolver lifecycle follows the currently authoritative registries."""
import pytest

from app.lib.gis import algorithm_registry, algorithm_resolver, capability_registry


@pytest.fixture(autouse=True)
def isolated_registries(monkeypatch):
    monkeypatch.setattr(algorithm_registry, "_registry", None)
    monkeypatch.setattr(capability_registry, "_registry", None)
    monkeypatch.setattr(algorithm_resolver, "_resolver", None)


def _register_temporary_point_provider(resolver):
    resolver.algorithms.register(algorithm_registry.AlgorithmDescriptor(
        id="test.sdk.temporary", name="Temporary test provider",
        capabilities=["point_profile"], tool_candidates=["spatial_stats"],
    ))


def test_algorithm_reset_does_not_leave_temporary_geometry_bypass():
    resolver = algorithm_resolver.get_algorithm_resolver()
    _register_temporary_point_provider(resolver)
    profile = {"geometryTypes": ["LineString"], "featureCount": 200}
    assert resolver.resolve("point_profile", profile=profile).algorithm == "test.sdk.temporary"

    algorithm_registry.reset_algorithm_registry()
    authoritative = algorithm_registry.get_algorithm_registry()
    assert authoritative.get("test.sdk.temporary") is None
    refreshed = algorithm_resolver.get_algorithm_resolver()
    assert refreshed.algorithms is authoritative
    assert refreshed.resolve("point_profile", profile=profile).status == "unavailable"


def test_compiler_geometry_fallback_survives_temporary_provider_lifecycle():
    from app.evaluation.quality_corpus import build_quality_scenario_corpus
    from app.services.gis_harness.workflow_compiler import compile_workflow

    case = next(case for case in build_quality_scenario_corpus()
                if case.id == "QS-categorical-composition-geometry_line")
    _register_temporary_point_provider(algorithm_resolver.get_algorithm_resolver())
    algorithm_registry.reset_algorithm_registry()
    result = compile_workflow(case.query, profile=case.qualification_profile)
    assert result.fallback_resolution["tier"] == case.expected_fallback_tier == "degraded"
    assert result.fallback_resolution["evidence"]["uses_planned_capability"] is True


def test_capability_reset_refreshes_the_resolver_authority():
    previous = algorithm_resolver.get_algorithm_resolver()
    capability_registry.reset_capability_registry()
    authoritative = capability_registry.get_capability_registry()
    refreshed = algorithm_resolver.get_algorithm_resolver()
    assert refreshed is not previous
    assert refreshed.capabilities is authoritative
