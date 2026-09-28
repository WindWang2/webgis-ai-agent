"""Descriptor license/attribution 契约测试（H08，元数据类不入指纹）。

- 指纹兼容（核心红线）：license/attribution 缺席（旧载荷）与在场但为空，
  指纹逐字节一致 —— store 读回重算复核不因新字段误判 corrupt；
- license 补全不改语义身份：同数据 + 不同 license → 同 descriptor 指纹
  （复用裁决 valid，许可补全绝不误判 stale）；
- 变更对账：license/attribution 变化 → 信息级 DESCRIPTOR_LICENSE_CHANGED
  code（change_class/verdict 不变）；
- fabric 侧声明透传：license/attribution 从 fabric descriptor 就地投影；
- 投影出口：d1 kwargs / semantic_view 在场才发射，缺席诚实缺席。
"""
import pytest

from app.lib.gis.dataset_descriptor import (
    CODE_LICENSE_CHANGED,
    GISDatasetDescriptor,
)
from app.lib.gis.dataset_profile import DatasetProfile


def _profile(value: int = 1) -> DatasetProfile:
    return DatasetProfile(
        source="ref_descriptor", feature_count=10 + value,
        geometry_types=["Polygon"], crs="EPSG:4326",
        fields={"v": "number"}, numeric_fields=["v"],
        fields_status="explicit",
    )


def _descriptor(**kwargs) -> GISDatasetDescriptor:
    from app.services.dataset_semantics import derive_descriptor

    return derive_descriptor(
        _profile(kwargs.pop("value", 1)), dataset_key="ref:t",
        features=[{"properties": {"v": i}} for i in range(5)], **kwargs)


def test_license_absent_equals_empty_fingerprint():
    """旧载荷（无 license 键）与新构建（license=""）→ 同一指纹。"""
    without = _descriptor()
    payload = without.to_dict()
    assert payload["license"] == ""  # to_dict 恒发射；旧持久化载荷则没有该键
    legacy = dict(payload)
    legacy.pop("license")
    legacy.pop("attribution")
    from_legacy = GISDatasetDescriptor.from_dict(legacy)
    rebuilt = from_legacy.with_fingerprints()
    assert rebuilt.descriptor_fingerprint == without.descriptor_fingerprint
    assert rebuilt.schema_fingerprint == without.schema_fingerprint


def test_license_enrichment_keeps_semantic_identity():
    """同数据 + 补全 license → descriptor 指纹不变（语义身份 ≠ 权利元数据）。"""
    bare = _descriptor()
    licensed = _descriptor(license="CC-BY-4.0", attribution="© OpenStreetMap contributors")
    assert licensed.license == "CC-BY-4.0"
    assert licensed.attribution.startswith("© OpenStreetMap")
    assert licensed.descriptor_fingerprint == bare.descriptor_fingerprint
    assert licensed.schema_fingerprint == bare.schema_fingerprint


def test_license_change_is_informational_not_stale():
    """license 变化 → 信息级 code；change_class/verdict 不升级为 stale。"""
    from app.lib.gis.dataset_descriptor import compare_descriptors

    bare = _descriptor()
    licensed = _descriptor(license="ODbL-1.0")
    delta = compare_descriptors(bare, licensed)
    assert CODE_LICENSE_CHANGED in delta.reason_codes
    assert delta.change_class == "none"
    assert delta.verdict == "valid"


def test_license_change_after_content_change_reports_both():
    from app.lib.gis.dataset_descriptor import compare_descriptors

    old = _descriptor(value=1)
    new = _descriptor(value=2, license="ODbL-1.0")
    delta = compare_descriptors(old, new)
    assert delta.change_class != "none"
    assert CODE_LICENSE_CHANGED in delta.reason_codes


def test_license_bounded():
    d = _descriptor(license="L" * 500, attribution="A" * 900)
    assert len(d.license) <= 128
    assert len(d.attribution) <= 256


def test_fabric_descriptor_license_passthrough():
    from app.services.dataset_semantics import (
        build_descriptor_from_fabric_descriptor,
    )

    d = build_descriptor_from_fabric_descriptor(
        {
            "feature_count": 42,
            "geometry_type": "Polygon",
            "srs": "EPSG:4326",
            "fields": [{"name": "v", "type": "number"}],
            "license": "CC-BY-4.0",
            "attribution": "© test",
        },
        dataset_key="ref:f",
    )
    assert d.license == "CC-BY-4.0"
    assert d.attribution == "© test"
    # fabric 侧无 license 声明 → 诚实缺席（不虚构 "unknown"）
    d2 = build_descriptor_from_fabric_descriptor(
        {"feature_count": 42, "geometry_type": "Polygon"}, dataset_key="ref:f")
    assert d2.license == ""


def test_projection_surfaces_license_only_when_present():
    from app.services.dataset_semantics import (
        descriptor_semantic_view,
        descriptor_to_d1_kwargs,
    )

    bare = _descriptor()
    assert "license" not in descriptor_to_d1_kwargs(bare)
    assert "license" not in descriptor_semantic_view(bare)
    licensed = _descriptor(license="CC-BY-4.0", attribution="© x")
    d1 = descriptor_to_d1_kwargs(licensed)
    view = descriptor_semantic_view(licensed)
    assert d1.get("license") == "CC-BY-4.0"
    assert view.get("license") == "CC-BY-4.0"
    assert view.get("attribution") == "© x"


@pytest.mark.asyncio
async def test_store_roundtrip_preserves_license(tmp_path):
    """store 写读 roundtrip：license 在场持久化 + 读回复核指纹一致。"""
    from app.services.dataset_semantics.store import (
        DatasetSemanticStore,
        dataset_key_hash,
    )

    store = DatasetSemanticStore(base_dir=tmp_path)
    d = _descriptor(license="CC-BY-4.0")
    put = await store.put("s1", "ref:t", d)
    assert put.ok and put.created
    rec = await store.get("s1", "ref:t")
    assert rec.status == "ok"
    assert rec.descriptor.license == "CC-BY-4.0"
    assert rec.descriptor.descriptor_fingerprint == d.descriptor_fingerprint
    payload_path = (
        tmp_path / "s1" / "dataset_semantics"
        / dataset_key_hash("ref:t") / f"{d.descriptor_fingerprint}.json"
    )
    assert payload_path.exists()


@pytest.mark.asyncio
async def test_legacy_payload_without_license_reads_back_ok(tmp_path):
    """迁移兼容：手工落一个旧 schema 载荷（无 license 键）→ store 读回
    ok 且指纹复核通过（不误判 corrupt）。"""
    import json

    from app.services.dataset_semantics.store import (
        DatasetSemanticStore,
        dataset_key_hash,
    )

    store = DatasetSemanticStore(base_dir=tmp_path)
    d = _descriptor()
    put = await store.put("s1", "ref:t", d)
    assert put.ok
    ppath = (tmp_path / "s1" / "dataset_semantics"
             / dataset_key_hash("ref:t") / f"{d.descriptor_fingerprint}.json")
    payload = json.loads(ppath.read_text())
    payload.pop("license", None)
    payload.pop("attribution", None)
    ppath.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True,
                                separators=(",", ":")))
    rec = await store.get("s1", "ref:t")
    assert rec.status == "ok"
    assert rec.descriptor.license == ""
    assert rec.descriptor.descriptor_fingerprint == d.descriptor_fingerprint
