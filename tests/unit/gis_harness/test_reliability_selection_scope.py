"""G07 M3：框选超上限的诚实披露（Issue #2004 基线事实）。

normalize_payload 对 selected_ids 只取前 8 个且此前无 omitted 披露 →
validate_analysis_scope 可能按前 8 个返回 usable，造成静默部分分析。

合并仲裁（review-fix-rereview-20261108 执行合并时记录）：携带上限采用
SW-02（PR #2014，ADR-0287）的 per-key cap —— selected_ids=50（三方对齐：
前端 selection-store 与 analysis_scope.MAX_SCOPE_FEATURE_IDS）、
categories=20、宽字节预算 4096；本 PR（#2017）的诚实计数语义保留并按
实际携带上限推导：
- selected_ids 超上限时输出携带前 N 个 + **服务端计算**的
  selected_ids_omitted=原始数-携带数（与 ids_truncated 布尔并存）；
  未超上限不加键；
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
        ids_60 = [f"sch-{i:02d}" for i in range(60)]
        out = normalize_payload(kind, {
            "layer_id": "schools", "dataset_ref": "ref:geojson-aaa",
            "selected_ids": ids_60,
        })
        assert out is not None
        assert out["selected_ids"] == ids_60[:50]
        assert out["selected_ids_omitted"] == 10
        # SW-02 布尔披露与 G07 计数披露并存（同一截断事实的两个面）。
        assert out["ids_truncated"] is True


def test_normalize_at_cap_output_unchanged():
    ids_50 = [f"sch-{i:02d}" for i in range(50)]
    payload = {
        "layer_id": "schools", "dataset_ref": "ref:geojson-aaa",
        "selected_ids": ids_50,
    }
    out = normalize_payload("feature_selected", payload)
    assert out is not None
    assert "selected_ids_omitted" not in out
    assert out["selected_ids"] == ids_50
    assert out["ids_truncated"] is False


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
        "categories": list(range(25)),
    })
    assert out is not None
    assert "selected_ids_omitted" not in out
    # 仲裁 cap：categories=20（SW-02 三方对齐）；omitted 键仅属 id 集 kind。
    assert out["categories"] == list(range(20))


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

    ids_60 = [f"sch-{i:02d}" for i in range(60)]
    normalized = normalize_payload("feature_selected", {
        "layer_id": "schools", "dataset_ref": "ref:geojson-aaa",
        "selected_ids": ids_60,
    })
    assert normalized is not None
    snapshot = project_interaction_snapshot([{
        "kind": "feature_selected", "payload": normalized,
        "client_instance_id": "browser-1", "client_seq": 1, "server_seq": 1,
    }])
    claimed = derive_claimed_scope(snapshot, None)
    assert claimed["selected_ids_omitted"] == 10
    assert claimed["ids_truncated"] is True


def test_omitted_metadata_is_accounted_in_payload_byte_budget():
    """宽预算（4096，SW-02）语义：服务端派生的 omitted 计数计入载荷字节
    预算 —— 附加后越界必须 fail-closed 拒收（None）。控制组：同形不越界
    载荷放行，证明拒收源于新增元数据（22B）而非基底超限。"""
    import json

    def _ids(n_len, count):
        return [f"{i:03d}" + "x" * (n_len - 3) for i in range(count)]

    def _size(id_len, digest_len, extra_pairs):
        return len(json.dumps({
            "geometry_digest": "g" * digest_len,
            "layer_id": "schools",
            **dict(extra_pairs),
            "selected_ids": _ids(id_len, 50),
        }, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"))

    # 双旋钮逼近预算上界：id 长度粗调（50B/步）、digest 长度细调（1B/步），
    # 使 50-id 基底（含 ids_truncated:false）落在 (4074, 4096] —— 附加
    # "selected_ids_omitted":10,（22B）后必越界。
    # 联合搜索 (id 长度粗调, digest 细调) 使基底落 (4074, 4096]
    n, target = next(
        (nn, L)
        for nn in range(48, 110)
        for L in range(0, 129)
        if 4074 < _size(nn, L, [("ids_truncated", False)]) <= 4096
    )
    base = _size(n, target, [("ids_truncated", False)])
    crossed = _size(n, target, [("ids_truncated", True), ("selected_ids_omitted", 10)])
    assert base <= 4096 < crossed

    payload = {
        "layer_id": "schools",
        "geometry_digest": "g" * target,
        "selected_ids": _ids(n, 60),
    }
    # 控制组：不越界的同形（原始 50，无 omitted 派生）放行。
    ok = dict(payload, selected_ids=_ids(n, 50))
    assert normalize_payload("feature_selected", ok) is not None
    # 拒收组：原始 60 → 携带 50 + omitted:10 附加后越界 → fail-closed。
    assert normalize_payload("feature_selected", payload) is None
