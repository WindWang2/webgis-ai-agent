"""WP1 契约测试 —— publication_snapshot.v1 构造、指纹确定性、红线扫描。

诚实声明：纯单元（无 DB/网络/LLM）；合成 spec。
"""
from __future__ import annotations

import pytest

from app.services.publication.contract import (
    DataMode,
    PublicationBlocker,
    PublicationBlockerReason,
    PublicationLayerInfo,
    PublicationMeta,
    PublicationQaSummary,
    PublicationScope,
    PublicationSourceInfo,
    build_snapshot_document,
    scan_forbidden_content,
)

pytestmark = pytest.mark.unit


def _meta(**kw) -> PublicationMeta:
    base = dict(
        title="区县学校分布专题图",
        purpose="展示合成区县学校分布",
        attribution="合成数据（simulated）",
        license="CC0-SIMULATED",
    )
    base.update(kw)
    return PublicationMeta(**base)


def _spec(**overrides) -> dict:
    spec = {
        "version": "2.2",
        "view": {"center": [116.4, 39.9], "zoom": 9, "projection": "EPSG:3857"},
        "sources": {
            "schools": {
                "type": "geojson",
                "name": "学校点",
                "attribution": "合成数据",
                "data": {"ref_id": "ref:schools-1"},
            }
        },
        "layers": [
            {
                "id": "lyr-schools",
                "source": "schools",
                "type": "circle",
                "legend": {"title": "学校"},
            }
        ],
        "layout": {"title": "区县学校分布专题图"},
    }
    spec.update(overrides)
    return spec


def _layers() -> list:
    return [
        PublicationLayerInfo(
            layer_id="lyr-schools",
            title="学校",
            source_id="schools",
            field_whitelist=("school_name", "level"),
            feature_count=42,
        )
    ]


def _sources() -> list:
    return [
        PublicationSourceInfo(
            source_id="schools",
            name="学校点",
            kind="geojson",
            attribution="合成数据",
            license="CC0-SIMULATED",
            data_mode=DataMode.materialized,
        )
    ]


def test_snapshot_build_and_fingerprint_deterministic():
    doc, fp = build_snapshot_document(
        product_id="p-1",
        version=1,
        meta=_meta(),
        spec=_spec(),
        layers=_layers(),
        sources=_sources(),
        scope=PublicationScope(crs="EPSG:3857", bbox=(115.0, 39.0, 117.0, 41.0)),
        accessibility={"altText": "学校分布"},
        qa=PublicationQaSummary(spec_validity="COMPILE_VALID"),
        blockers=[],
    )
    assert doc.spec_version == "publication_snapshot.v1"
    assert fp.startswith("pub-snapshot-sha256:")
    assert doc.snapshot_fingerprint == fp
    # 同输入 → 同指纹（幂等发布的事实源）
    doc2, fp2 = build_snapshot_document(
        product_id="p-1",
        version=1,
        meta=_meta(),
        spec=_spec(),
        layers=_layers(),
        sources=_sources(),
        scope=PublicationScope(crs="EPSG:3857", bbox=(115.0, 39.0, 117.0, 41.0)),
        accessibility={"altText": "学校分布"},
        qa=PublicationQaSummary(spec_validity="COMPILE_VALID"),
        blockers=[],
    )
    assert fp2 == fp and doc2.snapshot_fingerprint == fp


def test_fingerprint_changes_with_spec_edit():
    _, fp1 = build_snapshot_document(
        product_id="p-1", version=1, meta=_meta(), spec=_spec(),
        layers=_layers(), sources=_sources(), scope=PublicationScope(),
        accessibility={}, qa=PublicationQaSummary(), blockers=[],
    )
    edited = _spec()
    edited["view"]["zoom"] = 10
    _, fp2 = build_snapshot_document(
        product_id="p-1", version=1, meta=_meta(), spec=edited,
        layers=_layers(), sources=_sources(), scope=PublicationScope(),
        accessibility={}, qa=PublicationQaSummary(), blockers=[],
    )
    assert fp1 != fp2


def test_field_whitelist_lookup():
    doc, _ = build_snapshot_document(
        product_id="p-1", version=1, meta=_meta(), spec=_spec(),
        layers=_layers(), sources=_sources(), scope=PublicationScope(),
        accessibility={}, qa=PublicationQaSummary(), blockers=[],
    )
    assert doc.field_allowed("lyr-schools", "school_name") is True
    assert doc.field_allowed("lyr-schools", "internal_budget") is False
    assert doc.field_allowed("lyr-missing", "school_name") is False
    assert doc.layer_by_id("lyr-schools") is not None
    assert doc.reader_allowed_layers() == list(doc.layers)


def test_blockers_typed_roundtrip():
    blocker = PublicationBlocker(
        reason=PublicationBlockerReason.SOURCE_NOT_AUTHORIZABLE,
        message="瓦片源需会话凭证，无法安全投影",
        layer_id="lyr-tiles",
    )
    doc, _ = build_snapshot_document(
        product_id="p-1", version=1, meta=_meta(), spec=_spec(),
        layers=_layers(), sources=_sources(), scope=PublicationScope(),
        accessibility={}, qa=PublicationQaSummary(), blockers=[blocker],
    )
    assert doc.blockers[0].reason == PublicationBlockerReason.SOURCE_NOT_AUTHORIZABLE


# ── 红线扫描 ─────────────────────────────────────────────────────────


def test_scan_forbidden_keys():
    assert scan_forbidden_content({"owner_token": "x"}) == ["owner_token"]
    assert scan_forbidden_content({"a": {"api_key": "k"}}) == ["a.api_key"]
    assert scan_forbidden_content({"messages": [...]}) == ["messages"]
    assert scan_forbidden_content({"nested": {"deep": {"x_session_token": "t"}}}) == [
        "nested.deep.x_session_token"
    ]
    assert scan_forbidden_content({"clean": "value"}) == []


def test_scan_forbidden_absolute_paths():
    assert scan_forbidden_content({"p": "/home/kevin/projects/webgis/data/x"}) == ["p"]
    assert scan_forbidden_content({"ok": "EPSG:3857"}) == []
    assert scan_forbidden_content(["/var/lib/secret"]) == ["[0]"]


def test_build_rejects_snapshot_with_owner_token_in_spec():
    poisoned = _spec()
    poisoned["layout"]["notes"] = {"owner_token": "leak"}
    with pytest.raises(ValueError, match="forbidden_content"):
        build_snapshot_document(
            product_id="p-1", version=1, meta=_meta(), spec=poisoned,
            layers=_layers(), sources=_sources(), scope=PublicationScope(),
            accessibility={}, qa=PublicationQaSummary(), blockers=[],
        )


def test_build_rejects_absolute_path_in_spec():
    poisoned = _spec()
    poisoned["sources"]["schools"]["dataPath"] = "/home/kevin/secret.geojson"
    with pytest.raises(ValueError, match="forbidden_content"):
        build_snapshot_document(
            product_id="p-1", version=1, meta=_meta(), spec=poisoned,
            layers=_layers(), sources=_sources(), scope=PublicationScope(),
            accessibility={}, qa=PublicationQaSummary(), blockers=[],
        )


def test_meta_validation_bounds():
    with pytest.raises(Exception):
        _meta(title="")  # title 必填非空
    with pytest.raises(Exception):
        PublicationLayerInfo(layer_id="")  # layer_id 必填
