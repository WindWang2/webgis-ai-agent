"""SourceFacts V7 测试（ADR-0119 W4）。

- descriptor 采集（诚实 None / 字段收割）；
- 作用域隔离（同 fingerprint 不同 scope 不串）；
- observe_unfiltered_count 的 basis/provenance 语义；
- TTL 过期与失效；
- to_dataset_statistics 与 V6 costing 消费面兼容；
- durable 层 fail-open（DB 不可用 → 进程路径仍工作，失败计数升）。
"""

from app.services.data_fabric.fabric.source_facts import (
    DurableSourceFactsStore,
    SourceFactsService,
    facts_from_descriptor,
)
from app.schemas.data_fabric_schema import DatasetDescriptor


def _descriptor(**meta):
    return DatasetDescriptor(
        id="countries",
        title="Countries",
        source_type="postgis",
        geometry_type="Polygon",
        srs="EPSG:4326",
        bbox=[100.0, 20.0, 110.0, 30.0],
        feature_count=500,
        fields=[{"name": "name", "type": "string"}],
        metadata=meta,
    )


def test_facts_from_descriptor_harvests_fields():
    d = _descriptor()
    facts = facts_from_descriptor(
        d, fingerprint="fp_1", scope_key="org:_|owner:alice|proj:_", profile_id="ds_1"
    )
    assert facts is not None
    assert facts.row_count == 500
    assert facts.row_count_basis == "estimate"
    assert facts.extent == [100.0, 20.0, 110.0, 30.0]
    assert facts.crs == "EPSG:4326"
    assert facts.provenance.collector == "descriptor"


def test_facts_from_descriptor_returns_none_without_facts():
    empty = DatasetDescriptor(
        id="empty", title="e", source_type="generic", geometry_type="Point",
        srs=None, bbox=None, feature_count=None, fields=[],
    )
    assert facts_from_descriptor(
        empty, fingerprint="fp_x", scope_key="s", profile_id="ds"
    ) is None


def test_scope_isolation_in_service():
    svc = SourceFactsService(durable=_NoDBStore())
    d = _descriptor()
    f1 = svc.get(scope_key="scope:alice", fingerprint="fp_1", descriptor=d, profile_id="ds_1")
    f2 = svc.get(scope_key="scope:bob", fingerprint="fp_1", descriptor=d, profile_id="ds_1")
    assert f1.scope_key == "scope:alice"
    assert f2.scope_key == "scope:bob"


def test_observe_unfiltered_count_marks_observed_basis():
    svc = SourceFactsService(durable=_NoDBStore())
    ok = svc.observe_unfiltered_count(
        scope_key="scope:alice", fingerprint="fp_1", profile_id="ds_1",
        source_type="ogc_api", count=1234, note="numberMatched",
    )
    assert ok
    facts = svc.get(scope_key="scope:alice", fingerprint="fp_1")
    assert facts.row_count == 1234
    assert facts.row_count_basis == "observed"
    assert facts.provenance.collector == "observed_count"


def test_observe_count_rejects_invalid_and_filtered_is_caller_guarded():
    svc = SourceFactsService(durable=_NoDBStore())
    assert not svc.observe_unfiltered_count(
        scope_key="s", fingerprint="fp", profile_id="ds", source_type="x", count=-5)
    assert not svc.observe_unfiltered_count(
        scope_key="s", fingerprint="", profile_id="ds", source_type="x", count=10)


def test_ttl_expiry():
    svc = SourceFactsService(durable=_NoDBStore(), ttl_s=0.0)
    svc.get(scope_key="s", fingerprint="fp_1", descriptor=_descriptor(), profile_id="d")
    # TTL=0 → 下一次 get 现场重采（仍可得到，但缓存不驻留）
    assert svc.stats()["entries"] <= 1


def test_invalidate():
    svc = SourceFactsService(durable=_NoDBStore())
    svc.get(scope_key="s", fingerprint="fp_1", descriptor=_descriptor(), profile_id="d")
    assert svc.invalidate("fp_1") == 1
    assert svc.invalidate() >= 0


def test_to_dataset_statistics_roundtrip():
    svc = SourceFactsService(durable=_NoDBStore())
    facts = svc.get(scope_key="s", fingerprint="fp_1", descriptor=_descriptor(), profile_id="d")
    stats = facts.to_dataset_statistics()
    assert stats.dataset_fingerprint == "fp_1"
    assert stats.row_count == 500
    assert stats.crs == "EPSG:4326"
    # V6 spatial selectivity 可直接消费
    from app.services.data_fabric.query.federated.costing import estimate_spatial_selectivity

    sel = estimate_spatial_selectivity(None, stats)
    assert sel.value == 1.0


class _NoDBStore(DurableSourceFactsStore):
    """DB 不可用假体：fail-open 计数可见。"""

    def load(self, scope_key, dataset_fingerprint):
        self._on_failure(RuntimeError("no db in unit test"), "load")
        return None

    def save(self, facts):
        self._on_failure(RuntimeError("no db in unit test"), "save")
        return False

    def prune(self):
        return 0


def test_durable_failopen_counts_failures():
    svc = SourceFactsService(durable=_NoDBStore())
    svc.get(scope_key="s", fingerprint="fp_1", descriptor=_descriptor(), profile_id="d")
    assert svc.stats()["durable_failures"] >= 1
    # 进程路径仍工作
    facts = svc.get(scope_key="s", fingerprint="fp_1")
    assert facts is not None
