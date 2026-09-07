"""Fingerprints V3 —— canonical 指纹、变更分类与复用键测试。"""
import pytest

from app.lib.data.fingerprints import (
    ChangeClass,
    FingerprintFormatError,
    FingerprintSet,
    canonical_dumps,
    canonical_fingerprint,
    classify_change,
    compute_reuse_fingerprint,
    fingerprint_sequence,
    safe_canonical_fingerprint,
    staleness_verdict,
)


class TestCanonicalFingerprint:
    def test_deterministic_across_key_order(self):
        a = canonical_fingerprint({"x": 1, "y": [1, 2, 3], "z": {"k": "v"}})
        b = canonical_fingerprint({"y": [1, 2, 3], "z": {"k": "v"}, "x": 1})
        assert a == b

    def test_set_normalization(self):
        assert canonical_fingerprint({"s": {3, 1, 2}}) == canonical_fingerprint({"s": {1, 2, 3}})

    def test_unicode_stable(self):
        assert canonical_fingerprint({"n": "北京市"}) == canonical_fingerprint({"n": "北京市"})

    def test_non_finite_rejected(self):
        with pytest.raises(ValueError):
            canonical_fingerprint({"x": float("nan")})
        with pytest.raises(ValueError):
            canonical_fingerprint({"x": float("inf")})

    def test_unserializable_safe_variant(self):
        assert safe_canonical_fingerprint({"x": object()}) is None
        assert safe_canonical_fingerprint({"ok": 1}) is not None

    def test_canonical_dumps_sorted_keys(self):
        assert canonical_dumps({"b": 1, "a": 2}) == '{"a":2,"b":1}'


class TestChangeClassification:
    def test_no_change(self):
        fp = FingerprintSet(content="c1", schema="s1", metadata="m1", crs="EPSG:4326")
        assert classify_change(fp, fp) is ChangeClass.NONE

    def test_content_change(self):
        old = FingerprintSet(content="c1", schema="s1", metadata="m1", crs="EPSG:4326")
        new = FingerprintSet(content="c2", schema="s1", metadata="m1", crs="EPSG:4326")
        assert classify_change(old, new) is ChangeClass.CONTENT

    def test_schema_change_dominates_content(self):
        old = FingerprintSet(content="c1", schema="s1", metadata="m1")
        new = FingerprintSet(content="c2", schema="s2", metadata="m1")
        assert classify_change(old, new) is ChangeClass.SCHEMA

    def test_crs_change(self):
        old = FingerprintSet(content="c1", crs="EPSG:4326")
        new = FingerprintSet(content="c1", crs="EPSG:3857")
        assert classify_change(old, new) is ChangeClass.CRS

    def test_metadata_only(self):
        old = FingerprintSet(content="c1", schema="s1", metadata="m1")
        new = FingerprintSet(content="c1", schema="s1", metadata="m2")
        assert classify_change(old, new) is ChangeClass.METADATA_ONLY

    def test_missing_evidence_is_unknown_not_none(self):
        # 旧指纹缺 content 维 → 不能下「没变」的结论
        old = FingerprintSet(schema="s1")
        new = FingerprintSet(schema="s1", content="c2")
        assert classify_change(old, new) is ChangeClass.UNKNOWN

    def test_empty_fingerprints_unknown(self):
        assert classify_change(FingerprintSet(), FingerprintSet()) is ChangeClass.UNKNOWN

    def test_verdict_mapping(self):
        assert staleness_verdict(ChangeClass.NONE) == "valid"
        assert staleness_verdict(ChangeClass.METADATA_ONLY) == "stale"
        assert staleness_verdict(ChangeClass.CONTENT) == "recompute"
        assert staleness_verdict(ChangeClass.SCHEMA) == "recompute"
        assert staleness_verdict(ChangeClass.CRS) == "recompute"
        assert staleness_verdict(ChangeClass.UNKNOWN) == "recompute"
        # 上游死亡压倒一切
        assert staleness_verdict(ChangeClass.NONE, upstream_alive=False) == "invalid"


class TestReuseFingerprint:
    def test_deterministic(self):
        kwargs = dict(
            operation="kde",
            operation_version="1.2",
            input_fingerprints={"points": "fp-a", "boundary": "fp-b"},
            normalized_args={"bandwidth": 300, "grid": 256},
            crs="EPSG:4326",
            runtime_semantic_version="3.4.0",
        )
        assert compute_reuse_fingerprint(**kwargs) == compute_reuse_fingerprint(**kwargs)

    def test_input_order_irrelevant(self):
        base = dict(
            operation="kde",
            operation_version="1",
            normalized_args={},
            crs="",
            runtime_semantic_version="",
        )
        a = compute_reuse_fingerprint(input_fingerprints={"a": "1", "b": "2"}, **base)
        b = compute_reuse_fingerprint(input_fingerprints={"b": "2", "a": "1"}, **base)
        assert a == b

    def test_any_component_change_changes_key(self):
        base = dict(
            input_fingerprints={"a": "1"},
            normalized_args={"x": 1},
            crs="EPSG:4326",
            runtime_semantic_version="3.4.0",
        )
        variants = [
            dict(base, operation="kde2", operation_version="1"),
            dict(base, operation="kde", operation_version="2"),
            dict(base, operation="kde", operation_version="1", normalized_args={"x": 2}),
            dict(base, operation="kde", operation_version="1", crs="EPSG:3857"),
            dict(base, operation="kde", operation_version="1", runtime_semantic_version="3.5.0"),
        ]
        keys = {compute_reuse_fingerprint(**v) for v in variants}
        assert len(keys) == len(variants)  # 每个变体都是独立键

    def test_operation_required(self):
        with pytest.raises(FingerprintFormatError):
            compute_reuse_fingerprint(
                operation="", operation_version="1", input_fingerprints={}
            )

    def test_prefix_versioned(self):
        key = compute_reuse_fingerprint(
            operation="op", operation_version="1", input_fingerprints={}
        )
        assert key.startswith("reuse:v3:")


def test_fingerprint_sequence_order_sensitive():
    a = fingerprint_sequence(["x", "y"])
    b = fingerprint_sequence(["y", "x"])
    assert a != b
    assert a == fingerprint_sequence(["x", "y"])
