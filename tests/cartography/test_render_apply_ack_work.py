"""render_apply_ack v1 的 work echo / sources[] / stale 条目扩展测试
（Goal 03，ADR-0224 —— additive 演进的向后兼容 + 新语义）。"""
from __future__ import annotations

import pytest

from app.lib.cartography.render_apply_ack import (
    ACK_SCHEMA_VERSION,
    ENTRY_STALE,
    ENTRY_STATUSES,
    MAX_ACK_SOURCES,
    REASON_CODES,
    RC_STALE_DATA_REVISION,
    derive_apply_ack_findings,
    validate_render_apply_ack,
)


def _base_ack(**over):
    payload = {
        "schema_version": ACK_SCHEMA_VERSION,
        "mapspec_revision": 5,
        "status": "applied",
        "layers": [{"layer_id": "l1", "status": "applied"}],
        "components": [],
        "partial_apply": {"discarded": 0},
        "reconcile_error": "",
    }
    payload.update(over)
    return payload


class TestWorkEchoValidation:
    def test_absent_work_still_valid(self):
        normalized, errors = validate_render_apply_ack(_base_ack(), stamped_revision=5)
        assert normalized is not None and errors == []
        assert "work" not in normalized
        assert "sources" not in normalized

    def test_valid_work_echo(self):
        normalized, errors = validate_render_apply_ack(
            _base_ack(work={
                "work_id": "rw-5-fpabc", "work_fingerprint": "wf" * 32, "style_epoch": 3,
            }),
            stamped_revision=5,
        )
        assert normalized is not None and errors == []
        assert normalized["work"] == {
            "work_id": "rw-5-fpabc", "work_fingerprint": "wf" * 32, "style_epoch": 3,
        }

    def test_malformed_work_dropped_not_fatal(self):
        normalized, errors = validate_render_apply_ack(
            _base_ack(work={"work_id": ""}), stamped_revision=5,
        )
        assert normalized is not None
        assert "work" not in normalized
        assert errors

    def test_work_not_object_dropped(self):
        normalized, errors = validate_render_apply_ack(
            _base_ack(work=42), stamped_revision=5,
        )
        assert normalized is not None
        assert "work" not in normalized and errors

    def test_bad_style_epoch_dropped(self):
        normalized, errors = validate_render_apply_ack(
            _base_ack(work={
                "work_id": "w", "work_fingerprint": "f", "style_epoch": -1,
            }),
            stamped_revision=5,
        )
        assert normalized is not None
        assert "style_epoch" not in normalized["work"]
        assert errors


class TestSourceEntries:
    def test_source_entries_normalized(self):
        normalized, errors = validate_render_apply_ack(
            _base_ack(sources=[
                {"source_id": "s1", "status": "applied", "data_revision": "3"},
                {"source_id": "s2", "status": "stale",
                 "reason_code": "stale_data_revision", "data_revision": "1"},
                {"source_id": "s3", "status": "pending", "cache": "miss"},
            ]),
            stamped_revision=5,
        )
        assert normalized is not None and errors == []
        assert normalized["sources"][0]["data_revision"] == "3"
        assert normalized["sources"][1]["status"] == ENTRY_STALE
        assert normalized["sources"][2]["cache"] == "miss"

    def test_invalid_source_status_skipped(self):
        normalized, errors = validate_render_apply_ack(
            _base_ack(sources=[
                {"source_id": "s1", "status": "vaporized"},
                {"source_id": "s2", "status": "applied"},
            ]),
            stamped_revision=5,
        )
        assert normalized is not None
        assert [s["source_id"] for s in normalized["sources"]] == ["s2"]
        assert errors

    def test_invalid_cache_value_dropped(self):
        normalized, errors = validate_render_apply_ack(
            _base_ack(sources=[
                {"source_id": "s1", "status": "applied", "cache": "teleport"},
            ]),
            stamped_revision=5,
        )
        assert normalized is not None
        assert "cache" not in normalized["sources"][0]
        assert errors

    def test_source_bound_disclosed(self):
        normalized, _ = validate_render_apply_ack(
            _base_ack(sources=[
                {"source_id": f"s{i}", "status": "applied"} for i in range(MAX_ACK_SOURCES + 4)
            ]),
            stamped_revision=5,
        )
        assert len(normalized["sources"]) == MAX_ACK_SOURCES
        assert normalized["partial_apply"]["discarded"] >= 4

    def test_sources_not_array_rejected_entry_level(self):
        normalized, errors = validate_render_apply_ack(
            _base_ack(sources="nope"), stamped_revision=5,
        )
        assert normalized is not None
        assert "sources" not in normalized
        assert errors


class TestStaleVocabulary:
    def test_stale_entry_status_known(self):
        assert ENTRY_STALE in ENTRY_STATUSES
        assert RC_STALE_DATA_REVISION in REASON_CODES

    def test_stale_layer_entry_produces_finding(self):
        normalized, _ = validate_render_apply_ack(
            _base_ack(
                status="partial",
                layers=[{"layer_id": "l1", "status": "stale",
                         "reason_code": RC_STALE_DATA_REVISION}],
            ),
            stamped_revision=5,
        )
        findings = derive_apply_ack_findings({"apply_ack": normalized})
        assert len(findings) == 1
        assert "stale" in findings[0].detail

    def test_stale_source_entry_produces_finding(self):
        normalized, _ = validate_render_apply_ack(
            _base_ack(sources=[
                {"source_id": "s1", "status": "stale",
                 "reason_code": RC_STALE_DATA_REVISION, "data_revision": "1"},
            ]),
            stamped_revision=5,
        )
        findings = derive_apply_ack_findings({"apply_ack": normalized})
        assert len(findings) == 1
        assert findings[0].target == "s1"

    def test_transaction_stale_skips_all_findings(self):
        """事务级 stale（旧 ACK）：层/源条目一律不进 findings（只进诊断）。"""
        normalized, _ = validate_render_apply_ack(
            _base_ack(
                status="partial",
                layers=[{"layer_id": "l1", "status": "failed",
                         "reason_code": "missing_after_apply"}],
                sources=[{"source_id": "s1", "status": "stale"}],
            ),
            stamped_revision=9,  # ACK revision=5 ≠ stamped 9 → stale
        )
        assert normalized["stale"] is True
        assert derive_apply_ack_findings({"apply_ack": normalized}) == []

    def test_findings_bounded(self):
        normalized, _ = validate_render_apply_ack(
            _base_ack(layers=[
                {"layer_id": f"l{i}", "status": "failed",
                 "reason_code": "missing_after_apply"} for i in range(30)
            ]),
            stamped_revision=5,
        )
        assert len(derive_apply_ack_findings({"apply_ack": normalized})) <= 8


if __name__ == "__main__":
    pytest.main([__file__])
