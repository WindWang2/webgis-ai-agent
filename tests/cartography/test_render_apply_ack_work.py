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
    reconcile_work_echo,
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
                {"source_id": "s3", "status": "pending"},
            ]),
            stamped_revision=5,
        )
        assert normalized is not None and errors == []
        assert normalized["sources"][0]["data_revision"] == "3"
        assert normalized["sources"][1]["status"] == ENTRY_STALE
        assert normalized["sources"][2] == {"source_id": "s3", "status": "pending"}

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


class TestReconcileWorkEcho:
    """服务端 work echo 对账（delta review N3 —— 路由块逻辑的纯函数化测试）。"""

    _SPEC = {
        "version": "1.5",
        "sources": {"s1": {"type": "geojson", "content_revision": 2}},
        "layers": [{"id": "l1", "type": "circle", "source": "s1"}],
    }

    def _validated(self, work, *, stamped=5):
        payload = _base_ack(work=work)
        normalized, errors = validate_render_apply_ack(payload, stamped_revision=stamped)
        assert normalized is not None and errors == []
        return normalized

    def test_matching_echo_stamps_server_match_true(self):
        from app.lib.cartography.render_work_envelope import (
            project_render_work_envelope,
        )

        env = project_render_work_envelope(
            self._SPEC, session_id="sess-R", revision=5, fingerprint="fp-r",
            observation_seq=77,  # seq 不进指纹 —— 与服务端重算无关
        )
        ack = self._validated({
            "work_id": env.work_id, "work_fingerprint": env.work_fingerprint,
        })
        out = reconcile_work_echo(
            ack, mapspec=self._SPEC, session_id="sess-R",
            revision=5, fingerprint="fp-r",
        )
        assert out["work"]["server_match"] is True
        assert "errors" not in out

    def test_mismatched_echo_stamps_false_and_discloses(self):
        ack = self._validated({
            "work_id": "rw-5-someoneelse", "work_fingerprint": "wf-forged",
        })
        out = reconcile_work_echo(
            ack, mapspec=self._SPEC, session_id="sess-R",
            revision=5, fingerprint="fp-r",
        )
        assert out["work"]["server_match"] is False
        assert out["errors"] == ["work_fingerprint mismatch"]
        # 输入不可变：原 ack 未被就地改写。
        assert "server_match" not in ack["work"]

    def test_stale_ack_untouched(self):
        ack = self._validated(
            {"work_id": "w", "work_fingerprint": "f"}, stamped=9,
        )
        assert ack["stale"] is True
        out = reconcile_work_echo(
            ack, mapspec=self._SPEC, session_id="sess-R",
            revision=9, fingerprint="fp-r",
        )
        assert out is ack  # 旧代证据只进诊断，不对账
        assert "server_match" not in out["work"]

    def test_absent_work_or_bad_authority_untouched(self):
        no_work = self._validated(None)
        assert reconcile_work_echo(
            no_work, mapspec=self._SPEC, session_id="s",
            revision=5, fingerprint="f",
        ) is no_work
        ack = self._validated({"work_id": "w", "work_fingerprint": "f"})
        out = reconcile_work_echo(
            ack, mapspec=None, session_id="s", revision=5, fingerprint="",
        )
        assert out is ack
        assert "server_match" not in out["work"]

    def test_error_cap_respected(self):
        # 已有 8 条 errors（上界）→ mismatch 不再追加（有界纪律）。
        payload = _base_ack(work={"work_id": "w", "work_fingerprint": "bad"})
        payload["layers"] = [
            {"layer_id": f"x{i}", "status": "failed", "reason_code": "bogus{i}"}
            for i in range(8)
        ]
        normalized, _ = validate_render_apply_ack(payload, stamped_revision=5)
        out = reconcile_work_echo(
            normalized, mapspec=self._SPEC, session_id="sess-R",
            revision=5, fingerprint="fp-r",
        )
        assert out["work"]["server_match"] is False
        assert len(out["errors"]) == 8
