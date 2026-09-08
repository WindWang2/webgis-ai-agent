"""Workflow V4 —— 义务继承 + 工作流包/版本 单测（Wave 5+6）。"""
from __future__ import annotations


from app.services.gis_harness.workflow_schema import ScientificObligation
from app.services.gis_harness.workflow_v4.obligations import (
    ObligationSource,
    inherit_obligations,
    profile_to_source,
)
from app.services.gis_harness.workflow_v4.package import (
    check_compatibility,
    emit_workflow_package,
    next_version,
    parse_semver,
)


def _obl(oid: str, kind: str = "disclosure", on: str = "warn",
         pre: str = "") -> ScientificObligation:
    return ScientificObligation(
        obligation_id=oid, kind=kind, on_violation=on,
        precondition_id=pre, warning_code=f"W_{oid}")


# ── Wave 5：义务继承 ─────────────────────────────────────────────────────

def test_inherit_dedupes_by_id_and_keeps_all_provenance() -> None:
    base = ObligationSource(
        recipe_id="r.base", obligations=(_obl("denominator_ok", "denominator", "warn"),))
    support = ObligationSource(
        recipe_id="r.support", via_composite="comp.x",
        obligations=(_obl("denominator_ok", "denominator", "block_method"),))
    chain = inherit_obligations([base, support])
    assert chain.sources_count == 2
    assert len(chain.obligations) == 1
    o = chain.obligations[0]
    assert o.effective_on_violation == "block_method"  # 最强语义
    assert o.conflict is True
    assert {p.source_recipe_id for p in o.provenance} == {"r.base", "r.support"}
    assert any(p.via_composite == "comp.x" for p in o.provenance)


def test_inherit_scientific_kind_wins_on_kind_conflict() -> None:
    a = ObligationSource(recipe_id="r.a", obligations=(
        _obl("shared", "disclosure", "warn"),))
    b = ObligationSource(recipe_id="r.b", obligations=(
        _obl("shared", "uncertainty", "warn"),))
    chain = inherit_obligations([a, b])
    assert chain.obligations[0].kind == "uncertainty"


def test_inherit_is_idempotent() -> None:
    sources = [
        ObligationSource(recipe_id="r.a", obligations=(
            _obl("o1", "temporal", "degrade_with_disclosure"),
            _obl("o2", "precondition", "warn", pre="numeric_field_required"),
        )),
        ObligationSource(recipe_id="r.b", via_subworkflow="pkg.sub",
                         depth=2, obligations=(
            _obl("o1", "temporal", "warn"),
        )),
    ]
    once = inherit_obligations(sources)
    twice = inherit_obligations([  # 把产物再喂回去（模拟嵌套继承）
        ObligationSource(recipe_id="r.merged", depth=1, obligations=tuple(
            ScientificObligation(
                obligation_id=o.obligation_id, kind=o.kind,
                on_violation=o.effective_on_violation,
                precondition_id=o.precondition_id,
                warning_code=o.warning_code,
            ) for o in once.obligations)),
    ])
    assert {(o.obligation_id, o.kind, o.effective_on_violation)
            for o in once.obligations} == \
        {(o.obligation_id, o.kind, o.effective_on_violation)
         for o in twice.obligations}


def test_inherit_deterministic_fingerprint_and_empty_source() -> None:
    src = profile_to_source("r.x", None)
    assert src.obligations == ()  # 无 profile = 空义务来源（诚实保留）
    a = inherit_obligations([src])
    b = inherit_obligations([src])
    assert a.to_bounded_dict() == b.to_bounded_dict()
    assert a.fingerprint == b.fingerprint


# ── Wave 6：包与版本 ─────────────────────────────────────────────────────

def _compile_and_emit(query: str = "成都小学的分布情况"):
    from app.services.gis_harness.workflow_v4.compiler_v4 import (
        compile_workflow_v4,
    )
    c = compile_workflow_v4(query, profile={
        "featureCount": 120, "geometryTypes": ["Point"]})
    return c, emit_workflow_package(c)


def test_package_emit_reproducible_fingerprint() -> None:
    c1, p1 = _compile_and_emit()
    c2, p2 = _compile_and_emit()
    assert p1.fingerprint == p2.fingerprint
    assert p1.package_id == c1.base.recipe_id
    assert p1.compiler_version == "4.0.0"
    assert p1.recipe_fingerprint  # recipe 内容指纹（workflow_schema 单一事实源）
    parsed = parse_semver(p1.version)
    assert parsed == (1, 0, 0)


def test_package_different_input_different_fingerprint() -> None:
    _, p1 = _compile_and_emit("成都小学的分布情况")
    _, p2 = _compile_and_emit("成都的医院分布如何")
    assert p1.fingerprint != p2.fingerprint


def test_compatibility_matrix() -> None:
    _, pkg = _compile_and_emit()
    assert check_compatibility(pkg).compatible is True
    newer_minor = pkg.model_copy(update={"schema_version": "2.0.0"})
    verdict = check_compatibility(newer_minor)
    assert verdict.reason_code == "SCHEMA_NEWER" and verdict.migration_required
    foreign = pkg.model_copy(update={"compiler_version": "5.0.0"})
    verdict = check_compatibility(foreign)
    assert verdict.reason_code == "COMPILER_MAJOR_MISMATCH"
    assert verdict.migration_required and "recompile" in verdict.migration_hint
    malformed = pkg.model_copy(update={"compiler_version": "four"})
    assert check_compatibility(malformed).reason_code == "MALFORMED_VERSION"


def test_next_version_semver() -> None:
    assert next_version("1.2.3", change="major") == "2.0.0"
    assert next_version("1.2.3", change="minor") == "1.3.0"
    assert next_version("1.2.3", change="patch") == "1.2.4"
    assert next_version(None, change="minor") == "1.1.0"
    assert parse_semver("not-a-version") is None
