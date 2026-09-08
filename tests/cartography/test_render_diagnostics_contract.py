"""Render Diagnostics Contract V5（ADR-0118 D1）契约测试。

锁定三件事：
1. 权威词表自身一致性（severity 封闭、码唯一、message 非空中文）；
2. catalog 导出段与词表逐字一致（防生成物漂移）；
3. 外部载荷规整语义（未知码拒绝、超限截断、detail 截断）。
"""
from __future__ import annotations

import pytest

from app.lib.cartography.export_component_catalog import build_catalog
from app.lib.cartography.render_diagnostics import (
    MAX_DETAIL_CHARS,
    MAX_DIAGNOSTICS_PER_EXPORT,
    RENDER_DIAGNOSTICS,
    catalog_section,
    diagnostic,
    normalize_render_diagnostics,
)

pytestmark = [pytest.mark.unit, pytest.mark.cartography]


class TestVocabulary:
    def test_codes_unique_and_severity_closed(self):
        codes = list(RENDER_DIAGNOSTICS)
        assert len(codes) == len(set(codes))
        for spec in RENDER_DIAGNOSTICS.values():
            assert spec.severity in ("info", "warning", "error"), spec.code
            assert spec.message.strip(), spec.code

    def test_frontend_legacy_codes_are_authoritative_subset(self):
        # 前端 ExportDegradation 既有 4 码必须全部在权威词表内
        for code in (
            "chart_ref_unavailable",
            "table_ref_unavailable",
            "chart_kind_unsupported_export",
            "component_skipped_invalid",
        ):
            assert code in RENDER_DIAGNOSTICS

    def test_diagnostic_unknown_code_returns_none(self):
        assert diagnostic("totally_made_up_code") is None

    def test_diagnostic_message_interpolates_detail(self):
        d = diagnostic("label_truncated", detail="layer=a len=240")
        assert d is not None
        assert "layer=a len=240" in d.message
        assert d.severity == "warning"
        assert d.to_dict()["code"] == "label_truncated"

    def test_diagnostic_detail_capped(self):
        d = diagnostic("label_truncated", detail="x" * 500)
        assert d is not None
        assert len(d.detail) == MAX_DETAIL_CHARS


class TestCatalogSection:
    def test_catalog_section_matches_vocabulary(self):
        section = catalog_section()
        by_code = {e["code"]: e for e in section}
        assert set(by_code) == set(RENDER_DIAGNOSTICS)
        for code, spec in RENDER_DIAGNOSTICS.items():
            assert by_code[code]["severity"] == spec.severity
            assert by_code[code]["message"] == spec.message

    def test_build_catalog_carries_render_diagnostics(self):
        catalog = build_catalog()
        assert catalog["schemaVersion"] == 5
        assert catalog["renderDiagnostics"] == catalog_section()


class TestNormalize:
    def test_accepts_valid_payload(self):
        accepted, rejected = normalize_render_diagnostics(
            [
                {"code": "label_truncated", "detail": "len=240",
                 "layer_id": "lyr1"},
                {"code": "chart_ref_unavailable"},
            ]
        )
        assert rejected == []
        assert [a["code"] for a in accepted] == [
            "label_truncated", "chart_ref_unavailable",
        ]
        assert accepted[0]["layer_id"] == "lyr1"

    def test_rejects_unknown_code_and_non_object(self):
        accepted, rejected = normalize_render_diagnostics(
            [{"code": "nope"}, "junk", {"detail": "no code"}]
        )
        assert accepted == []
        assert len(rejected) == 3

    def test_rejects_over_limit(self):
        payload = [{"code": "label_truncated"}] * (MAX_DIAGNOSTICS_PER_EXPORT + 5)
        accepted, rejected = normalize_render_diagnostics(payload)
        assert len(accepted) == MAX_DIAGNOSTICS_PER_EXPORT
        assert any("exceeds max" in r for r in rejected)

    def test_rejects_non_list(self):
        accepted, rejected = normalize_render_diagnostics({"code": "x"})
        assert accepted == []
        assert rejected == ["payload must be a list"]
