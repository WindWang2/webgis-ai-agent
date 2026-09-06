"""Versioning V3 —— 来源修订比较与版本链测试。"""
from datetime import datetime, timezone

from app.lib.data.fingerprints import ChangeClass
from app.lib.data.versioning import (
    ArtifactVersion,
    SourceRevision,
    build_version_chain,
    compare_revisions,
    revision_token_from_stat,
)


class _Stat:
    def __init__(self, mtime_ns: int, size: int) -> None:
        self.st_mtime_ns = mtime_ns
        self.st_size = size


class TestSourceRevision:
    def test_token_from_stat(self):
        assert revision_token_from_stat(_Stat(111, 42)) == "111:42"
        assert revision_token_from_stat(None) == ""

    def test_roundtrip_dict(self):
        rev = SourceRevision(
            source_type="upload",
            source_ref="up_1",
            revision_token="111:42",
            content_fingerprint="cf",
            crs="EPSG:4326",
            captured_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        rev2 = SourceRevision.from_dict(rev.to_dict())
        assert rev2 == rev
        assert rev2.captured_at.tzinfo is not None

    def test_missing_evidence_is_unknown(self):
        rev = SourceRevision(revision_token="1:1")
        assert compare_revisions(None, rev) is ChangeClass.UNKNOWN
        assert compare_revisions(rev, None) is ChangeClass.UNKNOWN
        assert compare_revisions(SourceRevision(), SourceRevision()) is ChangeClass.UNKNOWN

    def test_token_only_comparison(self):
        a = SourceRevision(revision_token="1:100")
        same = SourceRevision(revision_token="1:100")
        changed = SourceRevision(revision_token="2:100")
        assert compare_revisions(a, same) is ChangeClass.NONE
        assert compare_revisions(a, changed) is ChangeClass.CONTENT  # 保守方向

    def test_fingerprint_evidence_dominates(self):
        old = SourceRevision(
            revision_token="1:100",
            content_fingerprint="c1",
            schema_fingerprint="s1",
            metadata_fingerprint="m1",
            crs="EPSG:4326",
        )
        new = SourceRevision(
            revision_token="2:200",  # token 变了
            content_fingerprint="c1",  # 内容没变
            schema_fingerprint="s1",
            metadata_fingerprint="m1",
            crs="EPSG:4326",
        )
        # 指纹齐 → none（token 噪声不触发重算）
        assert compare_revisions(old, new) is ChangeClass.NONE
        crs_only = SourceRevision(
            revision_token="1:100",
            content_fingerprint="c1",
            schema_fingerprint="s1",
            metadata_fingerprint="m1",
            crs="EPSG:3857",
        )
        assert compare_revisions(old, crs_only) is ChangeClass.CRS


class TestVersionChain:
    def _records(self):
        return [
            dict(artifact_id="v1", replaces=None, revision=0, created_at=1700000000.0,
                 metadata={"content_fingerprint": "f1"}),
            dict(artifact_id="v2", replaces="v1", revision=1, created_at=1700000100.0,
                 metadata={"content_fingerprint": "f2"}),
            dict(artifact_id="v3", replaces="v2", revision=2, created_at=1700000200.0,
                 metadata={"content_fingerprint": "f3"}),
        ]

    def test_chain_order_old_to_new(self):
        chain = build_version_chain(self._records(), head_artifact_id="v3")
        assert [v.artifact_id for v in chain.chain] == ["v1", "v2", "v3"]
        assert chain.latest.artifact_id == "v3"
        assert chain.chain[0].content_fingerprint == "f1"
        assert chain.chain[-1].replaced_by is None
        assert chain.chain[0].replaced_by == "v2"
        assert not chain.truncated

    def test_cycle_safe(self):
        records = [
            dict(artifact_id="a", replaces="b", revision=1, created_at=1.0, metadata={}),
            dict(artifact_id="b", replaces="a", revision=2, created_at=2.0, metadata={}),
        ]
        chain = build_version_chain(records, head_artifact_id="a")
        assert len(chain.chain) == 2

    def test_truncation_declared(self):
        records = [
            dict(artifact_id=f"v{i}", replaces=(f"v{i-1}" if i else None), revision=i,
                 created_at=float(i), metadata={})
            for i in range(40)
        ]
        chain = build_version_chain(records, head_artifact_id="v39", max_length=10)
        assert len(chain.chain) == 10
        assert chain.truncated

    def test_missing_head_empty_chain(self):
        chain = build_version_chain(self._records(), head_artifact_id="nope")
        assert chain.chain == []
        assert chain.latest is None

    def test_artifact_version_dict(self):
        v = ArtifactVersion(artifact_id="x", version=3, content_fingerprint="f")
        assert v.to_dict()["version"] == 3
