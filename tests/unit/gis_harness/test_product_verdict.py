"""VNext §14 产品裁决（Product Verdict）回归锁。

词表冻结：READY / READY_WITH_WARNINGS / NEEDS_REPAIR / BLOCKED_BY_DATA /
BLOCKED_BY_METHOD。推导是纯函数；方法论警告永远压低裁决档位 ——
「带分母缺失披露的 READY」不允许存在。
"""
from __future__ import annotations

from app.services.gis_harness.completion.contracts import (
    STATUS_COMPLETE,
    STATUS_FAILED,
    STATUS_NEEDS_REPAIR,
    F_ARTIFACT_EXPIRED,
    F_EMPTY_RESULT,
    F_LAYER_MISSING,
    F_SEMANTIC_LEGEND_MISMATCH,
    MapCompletionFinding,
    MapCompletionResult,
    derive_product_verdict,
)


def _result(status=STATUS_COMPLETE, findings=None):
    r = MapCompletionResult(status=status)
    r.findings = list(findings or [])
    return r


class TestVerdictVocabulary:
    def test_complete_clean_is_ready(self):
        v = derive_product_verdict(_result())
        assert v["verdict"] == "READY"
        assert v["reasons"] == []

    def test_complete_with_warning_findings_is_ready_with_warnings(self):
        r = _result(findings=[MapCompletionFinding(
            code=F_LAYER_MISSING, severity="warning", target="ly-1")])
        v = derive_product_verdict(r)
        assert v["verdict"] == "READY_WITH_WARNINGS"
        assert v["reasons"] == [F_LAYER_MISSING]

    def test_methodology_warning_always_downgrades(self):
        """方法论披露压低档位：complete + 方法论警告 ≠ READY。"""
        r = _result()
        v = derive_product_verdict(r, methodology_warnings=[
            {"pattern": "spatial_equity", "code": "EQUITY_MISSING_DENOMINATOR"}])
        assert v["verdict"] == "READY_WITH_WARNINGS"
        assert v["methodology_warning_count"] == 1
        assert v["methodology_warning_codes"] == ["EQUITY_MISSING_DENOMINATOR"]

    def test_needs_repair_status(self):
        r = _result(status=STATUS_NEEDS_REPAIR, findings=[
            MapCompletionFinding(code=F_LAYER_MISSING, severity="error",
                                 target="ly-1", repair="add_layer")])
        v = derive_product_verdict(r)
        assert v["verdict"] == "NEEDS_REPAIR"

    def test_failed_data_family_blocks_by_data(self):
        r = _result(status=STATUS_FAILED, findings=[
            MapCompletionFinding(code=F_ARTIFACT_EXPIRED, severity="error"),
            MapCompletionFinding(code=F_EMPTY_RESULT, severity="error"),
        ])
        v = derive_product_verdict(r)
        assert v["verdict"] == "BLOCKED_BY_DATA"
        assert sorted(v["reasons"]) == sorted([F_ARTIFACT_EXPIRED, F_EMPTY_RESULT])

    def test_failed_method_family_blocks_by_method(self):
        r = _result(status=STATUS_FAILED, findings=[
            MapCompletionFinding(code=F_SEMANTIC_LEGEND_MISMATCH, severity="error"),
        ])
        v = derive_product_verdict(r)
        assert v["verdict"] == "BLOCKED_BY_METHOD"

    def test_failed_mixed_family_data_first(self):
        """数据族 + 方法族并存 → 数据先行（上游因），method 仍随行披露。"""
        r = _result(status=STATUS_FAILED, findings=[
            MapCompletionFinding(code=F_ARTIFACT_EXPIRED, severity="error"),
            MapCompletionFinding(code=F_SEMANTIC_LEGEND_MISMATCH, severity="error"),
        ])
        v = derive_product_verdict(r)
        assert v["verdict"] == "BLOCKED_BY_DATA"
        assert F_SEMANTIC_LEGEND_MISMATCH in v["reasons"]


class TestVerdictInChapterBlock:
    def test_map_product_block_carries_verdict(self):
        from app.services.gis_harness.completion.pipeline import map_product_block

        r = _result(findings=[MapCompletionFinding(
            code=F_LAYER_MISSING, severity="warning", target="ly-1")])
        block = map_product_block(
            r, 7, methodology_warnings=[
                {"pattern": "spatial_equity",
                 "code": "EQUITY_MISSING_DENOMINATOR"}])
        assert block["product_verdict"]["verdict"] == "READY_WITH_WARNINGS"
        assert block["product_verdict"]["methodology_warning_count"] == 1

    def test_block_without_warnings_still_has_verdict(self):
        from app.services.gis_harness.completion.pipeline import map_product_block

        block = map_product_block(_result(), 3)
        assert block["product_verdict"]["verdict"] == "READY"
