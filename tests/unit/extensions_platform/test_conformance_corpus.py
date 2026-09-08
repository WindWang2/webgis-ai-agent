"""扩展平台一致性语料库执行（ADR-0104 / Wave 15）。

≥2000 个确定性 case；manifest 层 case 纯内存，policy/lifecycle 层 case
物化临时目录 + 独立 ToolRegistry。整库不触网络、不用 LLM、无随机源。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.extensions_platform.conformance import (
    ConformanceCase,
    build_conformance_cases,
    execute_case,
)
from app.tools.registry import ToolRegistry

CASES: list[ConformanceCase] = build_conformance_cases()


def test_corpus_is_large_and_deterministic():
    assert len(CASES) >= 2000, f"conformance corpus has only {len(CASES)} cases"
    ids = [c.case_id for c in CASES]
    assert len(set(ids)) == len(ids), "case ids must be unique"
    # 同进程重建必须逐项一致（确定性红线）。
    rebuilt = build_conformance_cases()
    assert [c.case_id for c in rebuilt] == ids
    assert [c.expectation for c in rebuilt] == [c.expectation for c in CASES]


def test_corpus_covers_required_scenario_families():
    categories = {c.category for c in CASES}
    required = {
        "manifest_valid",
        "manifest_invalid",
        "compatibility",
        "lifecycle_activate",
        "lifecycle_rollback",
        "lifecycle_dependency",
        "lifecycle_trust",
        "lifecycle_disabled",
        "lifecycle_degraded",
        "permission_matrix",
        "tool_sdk_matrix",
        "provider_sdk",
        "cartography_honesty",
        "policy_matrix",
    }
    missing = required - categories
    assert not missing, f"corpus missing scenario families: {sorted(missing)}"


@pytest.mark.parametrize("case", CASES, ids=[c.case_id for c in CASES])
def test_conformance_case(case: ConformanceCase, tmp_path: Path):
    def host_factory(policy):
        from app.extensions_platform.host import HostPolicy as _P
        from app.extensions_platform.host import ExtensionHost as _H

        assert isinstance(policy, _P)
        return _H(tool_registry=ToolRegistry(), policy=policy)

    outcome = execute_case(case, tmp_path, host_factory)
    assert outcome.passed, f"{case.case_id}: {outcome.detail}"
