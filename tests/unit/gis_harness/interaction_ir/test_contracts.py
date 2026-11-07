"""Interaction IR V1 契约测试（ADR-0227）：词表封闭性 / 白名单有界化 /
指纹确定性 / 双端 manifest 漂移闸。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.services.gis_harness.interaction_ir.contracts import (
    ALLOWED_KINDS,
    COALESCE_POLICIES,
    INTERACTION_KINDS,
    MAX_PAYLOAD_BYTES,
    MUTATION_LINKED_KINDS,
    SCHEMA_VERSION,
    interaction_event_fingerprint,
    normalize_payload,
)

MANIFEST_PATH = Path(__file__).resolve().parent / "contract_manifest.json"


def _manifest() -> dict:
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def test_manifest_schema_version_matches():
    assert _manifest()["schema_version"] == SCHEMA_VERSION


def test_manifest_kinds_match_registry():
    assert _manifest()["kinds"] == sorted(ALLOWED_KINDS)


def test_manifest_kind_specs_match_registry():
    specs = _manifest()["kind_specs"]
    assert set(specs) == set(INTERACTION_KINDS)
    for kind, spec in INTERACTION_KINDS.items():
        assert specs[kind]["payload_keys"] == sorted(spec.payload_keys)
        assert specs[kind]["coalesce"] == spec.coalesce
        assert specs[kind]["sensitivity"] == spec.sensitivity
        assert specs[kind]["authority"] == spec.authority
        assert specs[kind]["state_changing"] == spec.state_changing
        assert specs[kind]["allow_empty_payload"] == spec.allow_empty_payload


def test_manifest_bounds_match_constants():
    from app.services.gis_harness.interaction_ir.contracts import (
        MAX_EVENTS_PER_BATCH, MAX_LEDGER_BYTES, MAX_LEDGER_EVENTS,
        MAX_PAYLOAD_BYTES_SELECTION, MAX_SELECTED_IDS_PAYLOAD,
        MAX_TRACKED_CLIENTS,
    )
    assert _manifest()["bounds"] == {
        "max_events_per_batch": MAX_EVENTS_PER_BATCH,
        "max_ledger_events": MAX_LEDGER_EVENTS,
        "max_ledger_bytes": MAX_LEDGER_BYTES,
        "max_tracked_clients": MAX_TRACKED_CLIENTS,
        "max_payload_bytes": MAX_PAYLOAD_BYTES,
        "max_payload_bytes_selection": MAX_PAYLOAD_BYTES_SELECTION,
        "max_selected_ids_payload": MAX_SELECTED_IDS_PAYLOAD,
    }


def test_kind_metadata_invariants():
    """词表元数据不变量：合并策略合法 / mutation 链接型必 state_changing /
    词表内 kind 的 payload 白名单非空（信号型显式 allow_empty）。"""
    for kind, spec in INTERACTION_KINDS.items():
        assert spec.coalesce in COALESCE_POLICIES
        if spec.authority == "mapspec":
            assert kind in MUTATION_LINKED_KINDS
            assert spec.state_changing is True
        if not spec.allow_empty_payload:
            assert len(spec.payload_keys) > 0, kind


def test_normalize_payload_drops_unknown_keys():
    out = normalize_payload(
        "feature_selected",
        {"layer_id": "roads", "feature_id": "f1", "properties": {"secret": 1}},
    )
    assert out is not None
    assert "properties" not in out  # 隐私白名单：原始属性永不入 ledger
    assert out["layer_id"] == "roads"


def test_normalize_payload_empty_rules():
    # 信号型 kind 允许空 payload；其它 kind 空 payload → 拒收。
    assert normalize_payload("aoi_deleted", {}) == {}
    assert normalize_payload("feature_selected", {}) is None
    # 未知 kind → 拒收。
    assert normalize_payload("no_such_kind", {"x": 1}) is None
    # 非 dict → 拒收。
    assert normalize_payload("aoi_deleted", "x") is None


def test_normalize_payload_byte_budget():
    from app.services.gis_harness.interaction_ir.contracts import (
        MAX_PAYLOAD_BYTES_SELECTION,
    )
    # 单值超长先截断（128 字符）再过预算 —— 通过。
    clipped = normalize_payload("feature_selected", {"feature_id": "x" * 600})
    assert clipped is not None and len(clipped["feature_id"]) == 128
    # SW-02（#2004）：feature_selected/table_selection_changed 是 selection
    # kind —— 字节预算放宽到 MAX_PAYLOAD_BYTES_SELECTION（50 ids 集），
    # 但仍有界。旧 512B 纪律用非选择 kind（viewport_settled）继续钉扎。
    fat_feature = {
        "layer_id": "L" * 128,
        "feature_id": "F" * 128,
        "geometry_digest": "G" * 128,
        "dataset_ref": "D" * 128,
    }
    out = normalize_payload("feature_selected", fat_feature)
    assert out is not None  # ~600B ≤ 4096B（选择 kind 宽预算内）
    blob = json.dumps(out, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    assert len(blob.encode()) <= MAX_PAYLOAD_BYTES_SELECTION
    # 非 selection kind 的 fat 载荷超预算仍整事件拒收（纪律不放松）。
    # chart_selection_changed 不在 SELECTION_ID_KINDS：categories 8×128
    # 字符 ≈1.1KB > 512B 通用预算 → 整事件拒收。
    fat_chart = {
        "layer_id": "C" * 128,
        "field": "F" * 64,
        "categories": [str(i) + "c" * 126 for i in range(8)],
    }
    assert normalize_payload("chart_selection_changed", fat_chart) is None
    # 边界内通过。
    ok = normalize_payload("feature_selected", {"feature_id": "x" * 100})
    assert ok is not None
    blob = json.dumps(ok, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    assert len(blob.encode()) <= MAX_PAYLOAD_BYTES_SELECTION


def test_normalize_payload_clips_long_strings_and_lists():
    out = normalize_payload(
        "feature_selected",
        {
            "layer_id": "L" * 300,
            "field_names": [f"f{i}" for i in range(20)],
        },
    )
    assert len(out["layer_id"]) == 128
    assert len(out["field_names"]) == 8


def test_fingerprint_deterministic_and_time_insensitive():
    a = interaction_event_fingerprint(
        "viewport_settled", "user", "tab-1", 3, {"zoom": 10.5},
    )
    b = interaction_event_fingerprint(
        "viewport_settled", "user", "tab-1", 3, {"zoom": 10.5},
        client_mutation_id=None,
    )
    assert a == b
    # 不同 seq / 不同实例 → 不同指纹。
    assert a != interaction_event_fingerprint(
        "viewport_settled", "user", "tab-1", 4, {"zoom": 10.5},
    )
    assert a != interaction_event_fingerprint(
        "viewport_settled", "user", "tab-2", 3, {"zoom": 10.5},
    )


@pytest.mark.parametrize("kind", sorted(ALLOWED_KINDS))
def test_every_kind_has_spec_roundtrip(kind):
    spec = INTERACTION_KINDS[kind]
    assert spec.kind == kind
