"""G07 M3：框选超上限的诚实披露（Issue #2004 基线事实）。

normalize_payload 对 selected_ids 只取前 8 个且此前无 omitted 披露 →
validate_analysis_scope 可能按前 8 个返回 usable，造成静默部分分析。

本文件钉死 additive 契约（向后兼容）：
- feature_selected / table_selection_changed 的 selected_ids 超 8 时，
  规范化输出保留前 8 个并新增**服务端计算**的 selected_ids_omitted=N；
  ≤8 时输出与既有逐字节一致（无新键）；
- ClaimedScope/ValidatedScope 暴露 omitted 计数；
- validate 对 omitted>0 不得返回 usable：status=stale +
  reason=features_missing + checks.features="truncated"；missing_ids
  不猜未知 ID；既有 ref 所有权/跨会话 fail-closed 校验照常执行。
"""
from __future__ import annotations

from typing import Any, Dict

from app.services.gis_harness.interaction_ir.contracts import normalize_payload
from app.services.gis_situation.analysis_scope import derive_claimed_scope
from tests.data.situation_fakes import FakeMapspecStore
from tests.test_gis_situation_analysis_scope import (
    ScopeStore,
    _geojson_ref_payload,
    _spec_with_schools_layer,
    run_validate,
)

_IDS_10 = [f"sch-{i}" for i in range(1, 11)]


def _payload_with_8() -> Dict[str, Any]:
    return {"geojson": {"type": "FeatureCollection", "features": [
        {"type": "Feature", "id": fid, "properties": {}}
        for fid in _IDS_10[:8]
    ]}}


def _happy_store() -> ScopeStore:
    return ScopeStore(
        payloads={"ref:geojson-aaa": _payload_with_8()},
        descriptors={"ref:geojson-aaa": {
            "content_revision": 2, "feature_count": 8,
        }},
    )


def _truncated_claim(omitted: int = 2) -> Dict[str, Any]:
    return {
        "kind": "feature_ids", "layer_id": "schools",
        "dataset_ref": "ref:geojson-aaa",
        "feature_ids": _IDS_10[:8],
        "selected_ids_omitted": omitted,
        "claimed_at_seq": 3, "sources": ["ir_ledger"],
    }


# ── normalize_payload：服务端计算的 omitted 披露 ────────────────────────


def test_normalize_over_cap_discloses_omitted_both_kinds():
    for kind in ("feature_selected", "table_selection_changed"):
        out = normalize_payload(kind, {
            "layer_id": "schools", "dataset_ref": "ref:geojson-aaa",
            "selected_ids": list(_IDS_10),
        })
        assert out is not None
        assert out["selected_ids"] == _IDS_10[:8]
        assert out["selected_ids_omitted"] == 2


def test_normalize_at_cap_output_unchanged():
    payload = {
        "layer_id": "schools", "dataset_ref": "ref:geojson-aaa",
        "selected_ids": _IDS_10[:8],
    }
    out = normalize_payload("feature_selected", payload)
    assert out is not None
    assert "selected_ids_omitted" not in out
    assert out["selected_ids"] == _IDS_10[:8]


def test_normalize_omitted_not_accepted_from_client():
    """selected_ids_omitted 是服务端计算面 —— 客户端伪造不得进入输出，
    只能由截断事实推出。"""
    out = normalize_payload("feature_selected", {
        "layer_id": "schools", "selected_ids": ["a", "b", "c"],
        "selected_ids_omitted": 99,
    })
    assert out is not None
    assert "selected_ids_omitted" not in out
    assert out["selected_ids"] == ["a", "b", "c"]


def test_normalize_other_kinds_gain_no_omitted_key():
    out = normalize_payload("chart_selection_changed", {
        "layer_id": "schools", "field": "kind",
        "categories": list(range(12)),
    })
    assert out is not None
    assert "selected_ids_omitted" not in out
    assert out["categories"] == list(range(8))


# ── derive_claimed_scope：omitted 计数透传 ──────────────────────────────


def test_claimed_scope_carries_omitted_count():
    snapshot = {
        "event_count": 1,
        "selection": {
            "layer_id": "schools", "dataset_ref": "ref:geojson-aaa",
            "selected_ids": _IDS_10[:8], "selected_ids_omitted": 2,
            "source": {"server_seq": 1},
        },
    }
    claimed = derive_claimed_scope(snapshot, None)
    assert claimed["kind"] == "feature_ids"
    assert claimed["feature_ids"] == _IDS_10[:8]
    assert claimed["selected_ids_omitted"] == 2


def test_claimed_scope_omitted_absent_by_default():
    snapshot = {
        "event_count": 1,
        "selection": {
            "layer_id": "schools", "dataset_ref": "ref:geojson-aaa",
            "selected_ids": ["sch-1", "sch-2"],
            "source": {"server_seq": 1},
        },
    }
    claimed = derive_claimed_scope(snapshot, None)
    assert "selected_ids_omitted" not in claimed


# ── validate_analysis_scope：truncated 不得 usable ─────────────────────


def test_validate_truncated_selection_is_stale_not_usable():
    result = run_validate(
        "s1", _truncated_claim(omitted=2), store=_happy_store(),
        mapspec_store=FakeMapspecStore(_spec_with_schools_layer()))
    assert result["status"] == "stale"
    assert result["reason"] == "features_missing"
    assert result["checks"]["features"] == "truncated"
    # 部分真实要素仍如实返回（诚实部分披露，不伪造完整）。
    assert result["feature_ids"] == _IDS_10[:8]
    # 被截断的未知 ID 不得猜进 missing_ids。
    assert result["missing_ids"] == []
    assert result["selected_ids_omitted"] == 2


def test_validate_truncated_cannot_bypass_ownership_fail_closed():
    """omitted>0 不得绕过既有 ref 所有权校验：store 无 payload →
    仍 fail-closed unusable（跨会话/未授权语义不变）。"""
    result = run_validate(
        "s1", _truncated_claim(omitted=2), store=ScopeStore(),
        mapspec_store=FakeMapspecStore(_spec_with_schools_layer()))
    assert result["status"] == "unusable"
    assert result["reason"] == "ref_unresolvable"


def test_validate_truncated_large_dataset_also_stale():
    store = ScopeStore(
        payloads={"ref:geojson-aaa": _geojson_ref_payload()},
        descriptors={"ref:geojson-aaa": {
            "content_revision": 2, "feature_count": 20_001,
        }},
    )
    result = run_validate(
        "s1", _truncated_claim(omitted=2), store=store,
        mapspec_store=FakeMapspecStore(_spec_with_schools_layer()))
    assert result["status"] == "stale"
    assert result["checks"]["features"] == "truncated"


def test_validate_full_selection_semantics_unchanged():
    """成功对照：≤8（无 omitted 键）→ 既有 usable 语义逐字段不变。"""
    claimed = {
        "kind": "feature_ids", "layer_id": "schools",
        "dataset_ref": "ref:geojson-aaa",
        "feature_ids": ["sch-1", "sch-2"],
        "claimed_at_seq": 3, "sources": ["ir_ledger"],
    }
    result = run_validate(
        "s1", claimed, store=_happy_store(),
        mapspec_store=FakeMapspecStore(_spec_with_schools_layer()))
    assert result["status"] == "usable"
    assert result["reason"] == "ok"
    assert result["checks"]["features"] == "ok"
    assert "selected_ids_omitted" not in result


def test_projection_to_derive_preserves_omitted_across_production_chain():
    from app.services.gis_harness.interaction_ir.projection import (
        project_interaction_snapshot,
    )

    normalized = normalize_payload("feature_selected", {
        "layer_id": "schools", "dataset_ref": "ref:geojson-aaa",
        "selected_ids": list(_IDS_10),
    })
    assert normalized is not None
    snapshot = project_interaction_snapshot([{
        "kind": "feature_selected", "payload": normalized,
        "client_instance_id": "browser-1", "client_seq": 1, "server_seq": 1,
    }])
    claimed = derive_claimed_scope(snapshot, None)
    assert claimed["selected_ids_omitted"] == 2
