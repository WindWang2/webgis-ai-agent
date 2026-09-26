"""RenderApplyAck / perf 探针契约测试（F13，ADR-0214 D3/D5）。

锁定：fail-closed 结构校验、封闭词表收敛、有界截断披露、stale ACK
不产生 findings、DTO 归一门、validate_render_observation 消费链。
"""

from app.lib.cartography.render_apply_ack import (
    ACK_SCHEMA_VERSION,
    ENTRY_APPLIED,
    ENTRY_FAILED,
    ENTRY_SKIPPED,
    MAX_ACK_LAYERS,
    RC_APPLY_ERROR,
    RC_MISSING_AFTER_APPLY,
    RC_UNSUPPORTED_LAYER_TYPE,
    RC_USER_PENDING,
    derive_apply_ack_findings,
    validate_render_apply_ack,
)
from app.lib.cartography.render_perf_probes import (
    PERF_SCHEMA_VERSION,
    normalize_render_perf_block,
)
from app.services.gis_harness.completion.contracts import (
    F_RENDER_APPLY_FAILED,
)


def _ack(**overrides):
    payload = {
        "schema_version": ACK_SCHEMA_VERSION,
        "mapspec_revision": 7,
        "status": "partial",
        "layers": [
            {"layer_id": "l1", "status": ENTRY_APPLIED},
            {"layer_id": "l2", "status": ENTRY_FAILED,
             "reason_code": RC_MISSING_AFTER_APPLY},
            {"layer_id": "l3", "status": ENTRY_SKIPPED,
             "reason_code": RC_UNSUPPORTED_LAYER_TYPE},
        ],
        "components": [
            {"component_id": "c1", "status": ENTRY_APPLIED},
        ],
    }
    payload.update(overrides)
    return payload


class TestValidateRenderApplyAck:
    def test_valid_partial_ack_normalizes(self):
        normalized, errors = validate_render_apply_ack(_ack(), stamped_revision=7)
        assert normalized is not None
        assert errors == []
        assert normalized["stale"] is False
        assert normalized["status"] == "partial"
        assert [e["id"] for e in normalized["layers"]] == ["l1", "l2", "l3"]
        assert normalized["partial_apply"]["discarded"] == 0

    def test_stale_against_stamped_revision(self):
        normalized, _ = validate_render_apply_ack(_ack(), stamped_revision=9)
        assert normalized is not None
        assert normalized["stale"] is True

    def test_failclosed_on_bad_shape(self):
        for bad in (None, "ack", 42, [], {}):
            normalized, errors = validate_render_apply_ack(bad)
            assert normalized is None
            assert errors

    def test_failclosed_on_wrong_version(self):
        normalized, errors = validate_render_apply_ack(
            _ack(schema_version="render_apply_ack.v0"))
        assert normalized is None
        assert "schema_version" in errors[0]

    def test_failclosed_on_missing_revision(self):
        payload = _ack()
        del payload["mapspec_revision"]
        normalized, errors = validate_render_apply_ack(payload)
        assert normalized is None

    def test_failclosed_on_bad_transaction_status(self):
        normalized, errors = validate_render_apply_ack(_ack(status="ok"))
        assert normalized is None

    def test_unknown_reason_code_downgraded_not_rejected(self):
        payload = _ack(layers=[
            {"layer_id": "lx", "status": ENTRY_FAILED,
             "reason_code": "totally_new_code"},
        ])
        normalized, errors = validate_render_apply_ack(payload)
        assert normalized is not None
        assert normalized["layers"][0]["reason_code"] == RC_APPLY_ERROR
        assert any("unknown reason_code" in e for e in errors)

    def test_entry_missing_id_skipped_with_audit(self):
        payload = _ack(layers=[
            {"status": ENTRY_APPLIED},
            {"layer_id": "ok", "status": ENTRY_APPLIED},
        ])
        normalized, errors = validate_render_apply_ack(payload)
        assert normalized is not None
        assert [e["id"] for e in normalized["layers"]] == ["ok"]
        assert any("missing id" in e for e in errors)

    def test_entries_truncated_with_disclosure(self):
        payload = _ack(layers=[
            {"layer_id": f"l{i}", "status": ENTRY_APPLIED}
            for i in range(MAX_ACK_LAYERS + 5)
        ])
        normalized, _ = validate_render_apply_ack(payload)
        assert len(normalized["layers"]) == MAX_ACK_LAYERS
        assert normalized["partial_apply"]["discarded"] == 5

    def test_user_pending_reason_in_vocabulary(self):
        payload = _ack(layers=[
            {"layer_id": "lhud", "status": ENTRY_SKIPPED,
             "reason_code": RC_USER_PENDING},
        ])
        normalized, errors = validate_render_apply_ack(payload, stamped_revision=7)
        assert errors == []
        assert normalized["layers"][0]["reason_code"] == RC_USER_PENDING
        # user_pending 弃权项不进失败 findings（builder 侧本就排除；
        # 这里验证 skipped+user_pending 即使在场也不误报失败语义 ——
        # findings 只归因 missing/unsupported 类硬失败）
        findings = derive_apply_ack_findings({"apply_ack": normalized})
        assert findings == []


class TestDeriveApplyAckFindings:
    def test_failed_entries_produce_warnings(self):
        normalized, _ = validate_render_apply_ack(_ack(), stamped_revision=7)
        findings = derive_apply_ack_findings({"apply_ack": normalized})
        codes = [f.code for f in findings]
        assert codes == [F_RENDER_APPLY_FAILED, F_RENDER_APPLY_FAILED]
        assert all(f.severity == "warning" for f in findings)
        assert findings[0].target == "l2"
        assert RC_MISSING_AFTER_APPLY in findings[0].detail

    def test_stale_ack_produces_no_findings(self):
        normalized, _ = validate_render_apply_ack(_ack(), stamped_revision=99)
        assert normalized["stale"] is True
        assert derive_apply_ack_findings({"apply_ack": normalized}) == []

    def test_absent_ack_produces_no_findings(self):
        assert derive_apply_ack_findings({}) == []
        assert derive_apply_ack_findings({"apply_ack": None}) == []
        assert derive_apply_ack_findings(None) == []


class TestPerfBlockNormalization:
    def test_valid_block_normalized(self):
        block = normalize_render_perf_block({
            "schema_version": PERF_SCHEMA_VERSION,
            "ttfr_ms": 812.4,
            "patch_latency_ms": 120,
            "map_idle": True,
            "render_failures": 1,
            "data_plane": {
                "requests": 10, "cacheHits": 4, "etag304": 2,
                "fetchOk": 5, "fetchFailed": 1, "cancelled": 1,
                "deduped": 2, "evictions": 0,
            },
            "cache_bytes": 123456,
        })
        assert block is not None
        assert block["ttfr_ms"] == 812.4
        assert block["data_plane"]["cacheHits"] == 4
        assert block["cache_bytes"] == 123456

    def test_invalid_and_foreign_version_rejected(self):
        assert normalize_render_perf_block(None) is None
        assert normalize_render_perf_block("perf") is None
        assert normalize_render_perf_block({"schema_version": "v0"}) is None

    def test_adversarial_values_bounded(self):
        block = normalize_render_perf_block({
            "schema_version": PERF_SCHEMA_VERSION,
            "ttfr_ms": -5,                      # 负值 → 缺席
            "patch_latency_ms": float("1e999") if False else 10**12,  # 超界 → 缺席
            "render_failures": -99,             # 负计数 → 0
            "data_plane": "junk",               # 非法子块 → 全零
            "cache_bytes": 10**18,              # 超界 → 上限
        })
        assert block is not None
        assert "ttfr_ms" not in block
        assert "patch_latency_ms" not in block
        assert block["render_failures"] == 0
        assert set(block["data_plane"].values()) == {0}
        assert 0 <= block["cache_bytes"] <= 16 * 1024**3


class TestObservationIngestContract:
    def test_dto_normalizes_perf_and_gates_ack(self):
        from app.schemas.chat_schema import CartographicRuntimeObservationRequest

        req = CartographicRuntimeObservationRequest(
            client_generation=1,
            mapspec_fingerprint="sha256:0123456789abcdef",
            style_loaded=True,
            apply_ack=_ack(),
            perf={"schema_version": PERF_SCHEMA_VERSION, "ttfr_ms": 900},
        )
        assert req.apply_ack["status"] == "partial"
        assert req.perf["ttfr_ms"] == 900.0

    def test_dto_rejects_garbage_blocks_gracefully(self):
        from app.schemas.chat_schema import CartographicRuntimeObservationRequest

        req = CartographicRuntimeObservationRequest(
            client_generation=1,
            mapspec_fingerprint="sha256:0123456789abcdef",
            style_loaded=True,
            apply_ack="not-a-dict",
            perf={"schema_version": "wrong"},
        )
        assert req.apply_ack is None
        assert req.perf is None

    def test_dto_oversized_ack_gated(self):
        from app.schemas.chat_schema import CartographicRuntimeObservationRequest

        huge = _ack(layers=[
            {"layer_id": "x" * 64 + "y" * 100, "status": ENTRY_APPLIED,
             "note": "p" * 4000}
            for _ in range(200)
        ])
        req = CartographicRuntimeObservationRequest(
            client_generation=1,
            mapspec_fingerprint="sha256:0123456789abcdef",
            style_loaded=True,
            apply_ack=huge,
        )
        assert req.apply_ack is None

    def test_validate_render_observation_consumes_ack_findings(self):
        from app.services.gis_harness.render_observation import (
            validate_render_observation,
        )

        normalized, _ = validate_render_apply_ack(_ack(), stamped_revision=7)
        chapter = {"map_layers": []}
        mapspec = {"layers": []}
        observation = {
            "mapspec_revision": 7,
            "apply_ack": normalized,
            "layers": [],
        }
        status, findings = validate_render_observation(
            chapter, mapspec, observation, current_revision=7,
            required_slots=[["title"]],
        )
        codes = [f.code for f in findings]
        assert F_RENDER_APPLY_FAILED in codes
        # warning 级不推翻 status（transient 语义）
        assert status == "verified"

    def test_validate_render_observation_stale_ack_no_findings(self):
        from app.services.gis_harness.render_observation import (
            validate_render_observation,
        )

        normalized, _ = validate_render_apply_ack(_ack(), stamped_revision=99)
        assert normalized["stale"] is True
        observation = {"mapspec_revision": 7, "apply_ack": normalized}
        _, findings = validate_render_observation(
            {"map_layers": []}, {"layers": []}, observation, current_revision=7
        )
        assert F_RENDER_APPLY_FAILED not in [f.code for f in findings]
