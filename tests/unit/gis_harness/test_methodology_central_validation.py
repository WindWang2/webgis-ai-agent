"""Workflow V4 —— 中央校验收编断言（Epic workflow-v4）。

methodology registry 已并入 validate_gis_library（与 ontology 同级）：
本文件锁定该收编不可回退 —— 悬空引用必须让全库校验变红。
"""
from __future__ import annotations

from app.services.gis_harness.registry_validation import validate_gis_library
from app.services.gis_harness.workflow_v4.methodology import (
    METHODOLOGY_FAMILIES,
    get_methodology_registry,
)


def test_methodology_registry_in_central_validation() -> None:
    import app.services.gis_harness.workflow_v4.methodology as m
    m.reset_methodology_registry()
    issues = validate_gis_library()
    methodology_issues = [i for i in issues if i.startswith("methodology:")]
    assert methodology_issues == []


def test_methodology_fingerprint_in_dependency_chain() -> None:
    """方法族注册表指纹稳定且非空（进 package 兼容性链）。"""
    import app.services.gis_harness.workflow_v4.methodology as m
    m.reset_methodology_registry()
    reg = get_methodology_registry()
    assert len(reg.fingerprint) == 64
    assert reg.family_count() == len(METHODOLOGY_FAMILIES)
