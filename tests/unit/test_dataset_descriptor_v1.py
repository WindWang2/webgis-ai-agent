"""GISDatasetDescriptor v1 契约测试（ADR-0215 V1/V2/V3/V9 验收矩阵）。

- V1 契约：有界性 / 确定性 / 序列化 roundtrip / fail-closed / 指针类字段
  （dataset_key/source_refs/provenance/derived_at）不入指纹；
- V2 投影等价：descriptor 重建的 DatasetProfile 的 resolver 投影与源
  profile 逐键相等；measurement/语义视图/D1 视图重建；
- V3 比较矩阵：CRS/schema/unit/kind/role/temporal 各维变化 → 精确
  reason codes + verdict；
- V9 性能探针：10 万要素合成 FC 只触达有界采样窗口，载荷有硬上限。
"""
import pytest

from app.lib.data.fingerprints import ChangeClass, StalenessVerdict
from app.lib.gis.dataset_descriptor import (
    CODE_CRS_CHANGED,
    CODE_FIELD_KIND_CHANGED,
    CODE_FIELD_REMOVED,
    CODE_FIELD_RETYPE,
    CODE_FIELD_ROLE_CHANGED,
    CODE_FIELD_UNIT_CHANGED,
    CODE_GEOMETRY_CHANGED,
    CODE_SCHEMA_CHANGED,
    CODE_TEMPORAL_CHANGED,
    CODE_UNCHANGED,
    CODE_UNCOMPARABLE,
    CODE_VERSION_UNSUPPORTED,
    DESCRIPTOR_VERSION,
    GISDatasetDescriptor,
    SourceRef,
    compare_descriptors,
    qualification_stale_reason,
)
from app.lib.gis.dataset_profile import DatasetProfile
from app.services.dataset_semantics import (
    MAX_DESCRIPTOR_BYTES,
    SAMPLING_FEATURE_CAP,
    bounded_value_samples,
    build_descriptor,
    derive_descriptor,
    descriptor_payload_bytes,
    descriptor_resolver_profile,
    descriptor_semantic_view,
    descriptor_to_d1_kwargs,
    descriptor_to_dataset_profile,
    descriptor_to_measurement_profile,
)


def _profile(**overrides):
    base = dict(
        source="ref_descriptor",
        feature_count=10,
        geometry_types=["Polygon"],
        crs="EPSG:4326",
        fields={"population": "number", "name": "string", "year": "number"},
        numeric_fields=["population"],
        categorical_fields=["name"],
        null_ratios={"population": 0.1},
        fields_status="explicit",
    )
    base.update(overrides)
    return DatasetProfile(**base)


def _features(n=50):
    return [
        {"properties": {"population": i * 100, "name": f"x{i % 3}", "year": 2020 + i % 4}}
        for i in range(n)
    ]


# ── V1 契约 ────────────────────────────────────────────────────────────────


class TestDescriptorContract:
    def test_deterministic_fingerprint(self):
        d1 = derive_descriptor(_profile(), dataset_key="ref:a", features=_features())
        d2 = derive_descriptor(_profile(), dataset_key="ref:b", features=_features())
        # dataset_key 是指针身份，不入语义指纹：同一语义 → 同一指纹。
        assert d1.descriptor_fingerprint == d2.descriptor_fingerprint
        assert d1.schema_fingerprint == d2.schema_fingerprint
        assert d1.descriptor_fingerprint.startswith("dsd-v1:")
        assert d1.schema_fingerprint.startswith("dsd-schema-v1:")

    def test_pointer_fields_excluded_from_fingerprint(self):
        d = derive_descriptor(_profile(), dataset_key="ref:a", features=_features())
        shifted = d.model_copy(update={
            "dataset_key": "ref:other",
            "derived_at": "2099-01-01T00:00:00",
            "source_refs": [SourceRef(type="artifact", ref="artifact:xyz")],
            "provenance": [{"producer": "other", "method": "other"}],
        })
        assert shifted.descriptor_fingerprint == d.descriptor_fingerprint

    def test_semantic_change_changes_fingerprint(self):
        d = derive_descriptor(_profile(), dataset_key="ref:a", features=_features())
        bumped = d.model_copy(update={"feature_count": 11})
        assert bumped.with_fingerprints().descriptor_fingerprint != d.descriptor_fingerprint

    def test_roundtrip_preserves_fingerprint(self):
        d = derive_descriptor(_profile(), dataset_key="ref:a", features=_features())
        clone = GISDatasetDescriptor.from_dict(d.to_dict())
        assert clone.descriptor_fingerprint == d.descriptor_fingerprint
        assert clone.fields == d.fields

    def test_from_dict_fail_closed_on_unknown_version(self):
        d = derive_descriptor(_profile(), dataset_key="ref:a", features=_features())
        payload = d.to_dict()
        payload["descriptor_version"] = DESCRIPTOR_VERSION + 99
        with pytest.raises(ValueError, match="DESCRIPTOR_VERSION_UNSUPPORTED"):
            GISDatasetDescriptor.from_dict(payload)
        with pytest.raises(ValueError):
            GISDatasetDescriptor.from_dict("not-a-dict")

    def test_bounded_fields_cap(self):
        fields = {f"f{i}": "number" for i in range(200)}
        p = _profile(fields=fields, numeric_fields=list(fields))
        d = derive_descriptor(p, dataset_key="ref:a", features=_features())
        assert len(d.fields) == 64  # MAX_FIELDS 同 DatasetProfile.MAX_PROFILE_FIELDS
        assert len(d.numeric_fields) == 64
        assert d.sampling.fields_capped is True

    def test_bounded_evidence_and_refs(self):
        d = derive_descriptor(_profile(), dataset_key="ref:a", features=_features())
        for f in d.fields:
            assert len(f.evidence) <= 6
            assert len(f.checks) <= 4
            assert len(f.roles) <= 6
        assert len(d.source_refs) <= 8
        assert len(d.quality_signals) <= 16

    def test_descriptor_payload_byte_cap(self):
        fields = {f"f{i}": "number" for i in range(64)}
        p = _profile(fields=fields, numeric_fields=list(fields))
        d = derive_descriptor(p, dataset_key="ref:a", features=_features())
        assert descriptor_payload_bytes(d) <= MAX_DESCRIPTOR_BYTES

    def test_honest_defaults_not_fabricated(self):
        p = DatasetProfile(source="ref_descriptor")
        d = derive_descriptor(p, dataset_key="ref:a")
        assert d.crs == ""
        assert d.kind == "unknown"
        assert d.temporal.has_time_field is None
        assert d.descriptor_fingerprint.startswith("dsd-v1:")


# ── V2 投影等价 ────────────────────────────────────────────────────────────


class TestProjections:
    def test_resolver_projection_equivalence(self):
        p = _profile()
        d = derive_descriptor(p, dataset_key="ref:a", features=_features())
        assert descriptor_resolver_profile(d) == p.to_resolver_profile()

    def test_measurement_profile_reconstruction(self):
        p = _profile()
        d = derive_descriptor(p, dataset_key="ref:a", features=_features())
        mp = descriptor_to_measurement_profile(d)
        entry = mp.by_field("population")
        assert entry is not None
        assert entry.measurement_kind == "absolute_quantity"
        assert entry.unit_dimension == "population"

    def test_semantic_view_carries_roles(self):
        d = derive_descriptor(_profile(), dataset_key="ref:a", features=_features())
        view = descriptor_semantic_view(d)
        by_name = {f["field"]: f for f in view["fields"]}
        assert by_name["year"]["roles"] == ["temporal_dimension"]
        assert view["descriptor_fingerprint"] == d.descriptor_fingerprint

    def test_d1_kwargs_shape(self):
        d = derive_descriptor(_profile(), dataset_key="ref:a", features=_features())
        kwargs = descriptor_to_d1_kwargs(d)
        assert kwargs["geometry_type"] == "Polygon"
        assert kwargs["feature_type"] == "vector"
        assert kwargs["crs"] == "EPSG:4326"
        assert kwargs["schema_fields"]["population"] == "number"
        # 时间证据缺席 → temporal_coverage 缺席（不虚构 declared_only=False）
        assert "temporal_coverage" not in kwargs

    def test_projection_does_not_amplify_evidence(self):
        p = _profile(fields_status="unknown")
        d = derive_descriptor(p, dataset_key="ref:a", features=_features())
        rebuilt = descriptor_to_dataset_profile(d)
        # 源 profile 非权威 schema → 投影不得洗成 explicit。
        assert rebuilt.fields_status == "unknown"


# ── V3 比较矩阵 ────────────────────────────────────────────────────────────


class TestCompare:
    def test_self_compare_unchanged(self):
        d = derive_descriptor(_profile(), dataset_key="ref:a", features=_features())
        delta = compare_descriptors(d, d)
        assert delta.change_class == ChangeClass.NONE.value
        assert delta.verdict == StalenessVerdict.VALID.value
        assert CODE_UNCHANGED in delta.reason_codes
        assert delta.is_unchanged

    def test_missing_side_uncomparable(self):
        d = derive_descriptor(_profile(), dataset_key="ref:a", features=_features())
        for delta in (compare_descriptors(None, d), compare_descriptors(d, None)):
            assert delta.reason_codes == [CODE_UNCOMPARABLE]
            assert delta.verdict == StalenessVerdict.RECOMPUTE.value

    def test_crs_change(self):
        d1 = derive_descriptor(_profile(), dataset_key="ref:a", features=_features())
        d2 = derive_descriptor(
            _profile(crs="EPSG:3857"), dataset_key="ref:a", features=_features())
        delta = compare_descriptors(d1, d2)
        assert delta.change_class == ChangeClass.CRS.value
        assert CODE_CRS_CHANGED in delta.reason_codes
        assert delta.verdict == StalenessVerdict.RECOMPUTE.value
        assert qualification_stale_reason(delta.change_class) == "DESCRIPTOR_STALE_CRS"

    def test_schema_change_field_retype_and_removal(self):
        d1 = derive_descriptor(_profile(), dataset_key="ref:a", features=_features())
        d2 = derive_descriptor(
            _profile(fields={"population": "string", "name": "string"},
                     numeric_fields=[]),
            dataset_key="ref:a", features=_features(),
        )
        delta = compare_descriptors(d1, d2)
        assert delta.change_class == ChangeClass.SCHEMA.value
        assert CODE_SCHEMA_CHANGED in delta.reason_codes
        codes = {d.code for d in delta.field_diffs}
        assert CODE_FIELD_RETYPE in codes
        assert CODE_FIELD_REMOVED in codes  # year 字段消失

    def test_unit_change_same_field(self):
        p = _profile(fields={"v": "number"}, numeric_fields=["v"])
        d1 = derive_descriptor(p, dataset_key="a",
                               features=[{"properties": {"v": 5}}])
        d2 = derive_descriptor(p, dataset_key="a",
                               features=[{"properties": {"v": 5}}],
                               unit_overrides={"v": "persons"})
        delta = compare_descriptors(d1, d2)
        codes = {d.code for d in delta.field_diffs}
        assert CODE_FIELD_UNIT_CHANGED in codes

    def test_kind_change_same_field(self):
        p = _profile(fields={"人口变化": "number"}, numeric_fields=["人口变化"])
        d1 = derive_descriptor(p, dataset_key="a",
                               features=[{"properties": {"人口变化": 1}},
                                         {"properties": {"人口变化": 2}}])
        d2 = derive_descriptor(p, dataset_key="a",
                               features=[{"properties": {"人口变化": -1}},
                                         {"properties": {"人口变化": 1}}])
        delta = compare_descriptors(d1, d2)
        codes = {d.code for d in delta.field_diffs}
        assert CODE_FIELD_KIND_CHANGED in codes
        assert delta.verdict == StalenessVerdict.RECOMPUTE.value

    def test_role_change_detected(self):
        p = _profile(fields={"v": "number"}, numeric_fields=["v"])
        d1 = derive_descriptor(p, dataset_key="a",
                               features=[{"properties": {"v": 1}}])
        d2 = derive_descriptor(p, dataset_key="a",
                               features=[{"properties": {"v": 1}}],
                               unit_overrides={"v": "persons"})
        delta = compare_descriptors(d1, d2)
        codes = {d.code for d in delta.field_diffs}
        assert CODE_FIELD_UNIT_CHANGED in codes

    def test_temporal_change(self):
        d1 = derive_descriptor(_profile(), dataset_key="ref:a", features=_features())
        p2 = _profile()
        d2 = derive_descriptor(p2, dataset_key="ref:a", features=_features(),
                               coverage_start="2000", coverage_end="2024")
        delta = compare_descriptors(d1, d2)
        assert CODE_TEMPORAL_CHANGED in delta.reason_codes

    def test_geometry_change(self):
        d1 = derive_descriptor(_profile(), dataset_key="ref:a", features=_features())
        d2 = derive_descriptor(
            _profile(geometry_types=["Point"]), dataset_key="ref:a",
            features=_features())
        delta = compare_descriptors(d1, d2)
        assert CODE_GEOMETRY_CHANGED in delta.reason_codes

    def test_version_bump_wins(self):
        d1 = derive_descriptor(_profile(), dataset_key="ref:a", features=_features())
        d2 = derive_descriptor(_profile(), dataset_key="ref:a", features=_features())
        d2 = d2.model_copy(update={"descriptor_version": DESCRIPTOR_VERSION + 1})
        delta = compare_descriptors(d1, d2)
        assert "DESCRIPTOR_VERSION_BUMPED" in delta.reason_codes
        assert delta.verdict == StalenessVerdict.RECOMPUTE.value


# ── V9 性能探针 ────────────────────────────────────────────────────────────


class TestSamplingBounds:
    def test_bounded_sampling_window(self):
        samples = bounded_value_samples(
            _features(100_000), numeric_fields=["population"])
        assert len(samples["population"]) <= 200  # MAX_VALUE_SAMPLES
        # 只触达采样窗口：100k 输入 → 样本封顶，不随行数增长。
        samples2 = bounded_value_samples(
            _features(300), numeric_fields=["population"])
        assert len(samples["population"]) == len(samples2["population"])

    def test_sampling_field_cap(self):
        wide = [{"properties": {f"f{i}": i for i in range(100)}} for _ in range(10)]
        samples = bounded_value_samples(wide)
        assert len(samples) <= 16  # SAMPLING_FIELD_CAP

    def test_large_synthetic_feature_count_descriptor(self):
        fields = {f"m{i}": "number" for i in range(20)}
        p = _profile(
            feature_count=100_000,
            fields=fields,
            numeric_fields=list(fields),
        )
        d = derive_descriptor(
            p, dataset_key="ref:big",
            features=[{"properties": {f"m{i}": i for i in range(20)}}
                      for _ in range(100_000)],
        )
        # 10 万要素输入 → descriptor 载荷仍 O(fields) 有界。
        assert descriptor_payload_bytes(d) <= MAX_DESCRIPTOR_BYTES
        assert d.sampling.strategy == "first_n_features"
        assert d.sampling.feature_cap == SAMPLING_FEATURE_CAP

    def test_none_and_nan_do_not_consume_budget(self):
        feats = [{"properties": {"v": None}} for _ in range(50)] + [
            {"properties": {"v": float("nan")}} for _ in range(50)
        ] + [{"properties": {"v": i}} for i in range(10)]
        samples = bounded_value_samples(feats, numeric_fields=["v"])
        assert samples["v"] == [float(i) for i in range(10)]
