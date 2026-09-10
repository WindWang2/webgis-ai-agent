"""Epic 11 —— 中央校验收编断言（知识层不得逃逸对账）。"""
from __future__ import annotations

from app.services.gis_harness.registry_validation import validate_gis_library


def test_methodology_intel_in_central_validation() -> None:
    """taxonomy/descriptors/provenance/graph 全部并入 validate_gis_library。"""
    from app.lib.gis.methodology.graph import reset_knowledge_graph
    from app.lib.gis.methodology.taxonomy import reset_task_taxonomy
    reset_task_taxonomy()
    reset_knowledge_graph()
    issues = validate_gis_library()
    intel_issues = [i for i in issues if i.startswith("methodology_intel:")
                    or i.startswith("taxonomy:")
                    or i.startswith("method_descriptors:")
                    or i.startswith("knowledge_graph:")
                    or i.startswith("provenance")]
    assert intel_issues == []
