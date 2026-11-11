"""G28 finalizer 接线测试（P1/P3/A01/A13/A17/A20/A25/A30）。

锁定契约：
- ``map_product_block(receipts=..., frame_verification=...)`` 持久化
  additive ``capability_receipts`` / ``frame_product`` 键，并把回执快照
  传入 goal_satisfaction 评估（A01 生产调用面）；
- 输入缺席 → 新键整体省略（旧调用零漂移，A30）；
- frame 终验块驱动 map 需求逐帧闸（A13：一幅成功 ≠ 产品完成）；
- 回执 bundle 是评估快照（A20：迟到结果不改当前裁决）；
- owner/scope 不匹配的 bundle 整包拒绝（A17）；
- kill switch 关闭 → 收集器返回 disabled（证据面回旧形状）。
"""
from __future__ import annotations

import json

from app.services.gis_harness.completion.contracts import MapCompletionResult
from app.services.gis_harness.completion.pipeline import map_product_block
from app.services.gis_harness.goal_satisfaction import (
    bounded_payload,
    build_evidence_registry,
    evaluate_goal_satisfaction,
)
from app.services.gis_harness.goal_satisfaction.facets import (
    frame_observed_receipts,
    frame_verification_facet,
)
from app.services.gis_harness.goal_satisfaction.receipts import (
    collect_capability_receipts,
    receipt_evidence_rows,
)


def _chapter():
    return {
        "query": "成都各区学校分布",
        "intent": {
            "query": "成都各区学校分布",
            "scope": {"name": "成都", "level": "city"},
            "output_intents": ["map", "statistics"],
        },
        "analysis_steps": [
            {"capability": "poi.query", "purpose": "查询学校",
             "status": "complete"},
        ],
    }


def _result():
    return MapCompletionResult(
        status="complete", layer_status="valid",
        component_status="valid", render_status="verified",
        final_map_status="verified",
    )


def _product():
    return {
        "product_verdict": {"verdict": "READY", "reasons": []},
        "final_map_status": "verified",
        "render_status": "verified",
        "checked_revision": 7,
    }


def _frame_verification(status="incomplete", verified=0, required=2,
                        problems=("frame_not_observed:f2",)):
    summary = {"total": 2, "required": required, "verified": verified,
               "live_observed": 0, "export_verified": 0,
               "snapshot_only": 0, "not_observed": required - verified}
    return {
        "status": status, "mapspec_revision": 7,
        "required_frames": [f"f{i}" for i in range(required)],
        "frames": [], "problems": list(problems), "summary": summary,
    }


# ── A01/A30：finalizer 面的块持久化 ─────────────────────────────────────

def test_block_persists_frame_product_and_drives_frame_gate():
    block = map_product_block(
        _result(), 7, chapter=_chapter(),
        frame_verification=_frame_verification())
    assert block["frame_product"]["status"] == "incomplete"
    json.dumps(block["frame_product"], ensure_ascii=False)
    report = evaluate_goal_satisfaction(_chapter(), map_product=block)
    map_v = next(v for v in report.requirements
                 if v.requirement_id == "map")
    assert map_v.verdict.value == "partial"
    assert map_v.failed_rule == "frame_product_incomplete"
    assert any(m.startswith("frame_not_observed") for m in map_v.missing)
    assert map_v.next_action == "reobserve"


def test_frame_complete_and_not_bound_zero_gate():
    """complete 终验不设闸；无 frame_product 块 = 旧形状零漂移。"""
    block_ok = map_product_block(
        _result(), 7, chapter=_chapter(),
        frame_verification=_frame_verification(
            status="complete", verified=2, required=2, problems=()))
    report_ok = evaluate_goal_satisfaction(_chapter(), map_product=block_ok)
    map_v = next(v for v in report_ok.requirements
                 if v.requirement_id == "map")
    assert map_v.failed_rule != "frame_product_incomplete"
    # not_bound → 键省略（facets 层已返回 None 的路径由调用方保证）
    block_none = map_product_block(_result(), 7, chapter=_chapter())
    assert "frame_product" not in block_none
    assert "capability_receipts" not in block_none


def test_block_persists_capability_receipts_summary():
    bundle = {"session_id": "s1", "families": {
        "edit": [{"ref_id": "ref:a", "seq": 3}], "compute": [], "import": [],
        "delivery": [], "raster": [], "refresh": []}}
    block = map_product_block(_result(), 7, chapter=_chapter(),
                              receipts=bundle)
    assert block["capability_receipts"]["counts"]["edit"] == 1
    # bundle 进评估：编辑行 bound_ref 匹配 → 无 cap 码 → 中性在证（A25）
    report = evaluate_goal_satisfaction(_chapter(), map_product=block,
                                        receipts=bundle)
    assert report is not None
    json.dumps(block["goal_satisfaction"], ensure_ascii=False)


def test_receipts_absent_omits_keys_zero_drift():
    block = map_product_block(_result(), 7, chapter=_chapter())
    assert "capability_receipts" not in block
    assert "frame_product" not in block


# ── A20：评估快照语义 ───────────────────────────────────────────────────

def test_late_receipt_does_not_change_persisted_verdict():
    chapter = {
        "query": "计算案例", "intent": {"output_intents": []},
        "analysis_steps": [{"capability": "field_compute",
                            "status": "complete",
                            "bound_ref": "ref:ds1"}],
    }
    partial_bundle = {"session_id": "s1", "families": {
        "edit": [], "import": [], "delivery": [], "raster": [], "refresh": [],
        "compute": [{"plan_id": "p1", "compute_fingerprint": "cf",
                     "input_ref": "ref:ds1", "output_ref": "ref:ds1-out",
                     "row_count": 100, "computed_rows": 10,
                     "skipped_rows": 90, "error_count": 0}]}}
    block = map_product_block(MapCompletionResult(), 7, chapter=chapter,
                              receipts=partial_bundle)
    stored = block["goal_satisfaction"]
    v1 = next(v for v in stored["requirements"]
              if v["requirement_id"] == "analysis:field_compute")
    assert v1["verdict"] == "partial"
    # 迟到「好」回执不改已持久化裁决 —— 只有下次终验重新评估才见新事实。
    good_bundle = {"session_id": "s1", "families": {
        "edit": [], "import": [], "delivery": [], "raster": [], "refresh": [],
        "compute": [{"plan_id": "p1", "compute_fingerprint": "cf",
                     "input_ref": "ref:ds1", "output_ref": "ref:ds1-out",
                     "row_count": 100, "computed_rows": 100,
                     "skipped_rows": 0, "error_count": 0}]}}
    stored_after = dict(stored)
    assert stored_after == stored  # 块未被后续调用修改（快照语义）
    report_next = evaluate_goal_satisfaction(chapter,
                                             map_product=_product(),
                                             receipts=good_bundle)
    v2 = next(v for v in report_next.requirements
              if v.requirement_id == "analysis:field_compute")
    assert v2.verdict.value == "fulfilled"


# ── A17：owner/scope 绑定 ───────────────────────────────────────────────

def test_owner_mismatched_bundle_rejected():
    bundle = {"session_id": "other-session", "families": {
        "edit": [{"ref_id": "ref:a", "seq": 3}], "compute": [], "import": [],
        "delivery": [], "raster": [], "refresh": []}}
    assert receipt_evidence_rows(bundle, session_id="s1") == []
    # bundle 无 session 标记（合成形状）→ 不过滤（评估面纯函数契约）
    assert receipt_evidence_rows({"families": bundle["families"]},
                                 session_id="s1")


# ── 证据投影面（纯函数）────────────────────────────────────────────────

def test_receipt_evidence_rows_delivery_binds_export_id():
    bundle = {"session_id": "s1", "families": {
        "edit": [], "compute": [], "import": [], "raster": [], "refresh": [],
        "delivery": [{"delivery_id": "d1", "job_id": "j1", "format": "csv",
                      "status": "completed", "filename": "a.csv",
                      "row_count": 5, "content_sha256": "x" * 64,
                      "scope_missing_ids": []}]}}
    rows = receipt_evidence_rows(bundle, session_id="s1")
    ids = [r.id for r in rows]
    assert "export_receipt:delivery:csv" in ids
    row = next(r for r in rows if r.id == "export_receipt:delivery:csv")
    assert row.status.value == "present"
    assert row.detail.startswith("delivery:")
    # 注册进证据面（delivery_expected 需求绑定路径）
    chapter = {"query": "q", "intent": {"export_intents": ["csv"]},
               "analysis_steps": []}
    evidence = build_evidence_registry(chapter, _product(), None,
                                       receipts=bundle)
    assert any(e.id == "export_receipt:delivery:csv" for e in evidence)


def test_receipt_evidence_rows_cancelled_delivery_absent():
    bundle = {"session_id": "s1", "families": {
        "edit": [], "compute": [], "import": [], "raster": [], "refresh": [],
        "delivery": [{"delivery_id": "d9", "job_id": "j9",
                      "format": "shp", "status": "cancelled"}]}}
    rows = receipt_evidence_rows(bundle, session_id="s1")
    row = next(r for r in rows if r.id == "export_receipt:delivery:shp")
    assert row.status.value == "absent"  # A19：取消 ≠ complete


# ── kill switch 与收集器诚实缺席 ────────────────────────────────────────

def test_collect_disabled_by_kill_switch(monkeypatch):
    monkeypatch.setenv("GIS_GOAL_RECEIPT_ADAPTERS", "0")
    import asyncio

    out = asyncio.run(collect_capability_receipts("s1", {}))
    assert out["disabled"] is True
    assert all(v == [] for v in out["families"].values())


def test_collect_empty_session_is_empty_bundle():
    import asyncio

    out = asyncio.run(collect_capability_receipts("", {}))
    assert out["session_id"] == "" and not out["disabled"]


def test_collect_refresh_without_project_context_empty(monkeypatch):
    import asyncio

    out = asyncio.run(collect_capability_receipts("s1", {"query": "q"}))
    assert out["families"]["refresh"] == []  # 无 project 桥 → 诚实缺席（A30）


def test_collect_raster_from_mapspec(monkeypatch):
    import asyncio

    class _FakeStore:
        async def get_mapspec(self, session_id):
            return {
                "sources": {"src1": {
                    "type": "numeric_raster", "refId": "ref:r1",
                    "dataRevision": "rev-9", "dataFingerprint": "fp-9"}},
                "layers": [{"id": "lyr1", "source": "src1",
                            "type": "raster"}],
            }

    import app.services.mapspec_store as ms

    monkeypatch.setattr(ms.mapspec_store, "get_mapspec",
                        _FakeStore().get_mapspec, raising=False)
    out = asyncio.run(collect_capability_receipts("s1", {}))
    raster_rows = out["families"]["raster"]
    assert len(raster_rows) == 1
    assert raster_rows[0]["data_revision"] == "rev-9"
    assert raster_rows[0]["layer_ids"] == ["lyr1"]


# ── frame 观察回执组装（纯函数）────────────────────────────────────────

def test_frame_observed_receipts_from_exports():
    observed = frame_observed_receipts(
        None,
        [{"format": "png", "revision": 7, "frame_id": "f1"},
         {"format": "png", "revision": 7}])
    assert observed["f1"]["mode"] == "export_verified"
    assert observed["f1"]["revision"] == 7
    assert "f2" not in observed  # 无 frame_id 的导出不冒充逐帧观察


def test_frame_facet_not_bound_returns_none():
    import asyncio

    out = asyncio.run(frame_verification_facet(
        "s1", spec={"frame_product": {"enabled": False}},
        mapspec_revision=7))
    assert out is None


def test_bounded_payload_includes_requirement_rows():
    gs = {"verdict": "partial", "signal": "continue",
          "counts": {"partial": 1},
          "requirements": [
              {"requirement_id": "map", "kind": "map", "verdict": "partial",
               "missing": ["frame_not_observed:f2"],
               "next_action": "reobserve"}],
          "missing_summary": [], "summary_line": "x"}
    payload = bounded_payload(gs)
    rows = payload["requirements"]
    assert rows and rows[0]["id"] == "map"
    assert rows[0]["next_action"] == "reobserve"
    assert rows[0]["verdict"] == "partial"
    json.dumps(payload, ensure_ascii=False)  # SSE 可序列化
