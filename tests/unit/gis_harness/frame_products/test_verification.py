"""G15 WP11 —— 逐画幅终验契约测试（观察对账/空帧/快照不冒充/单帧不掩盖）。"""
from __future__ import annotations

import pytest

from app.services.gis_harness.frame_products.verification import (
    frame_evidence_rows,
    verify_frame_product,
)

pytestmark = pytest.mark.unit


def _doc(empty_2024: bool = False) -> dict:
    feats = [
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [104, 30]},
         "properties": {"year": 2022, "v": 1.0}},
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [104, 30]},
         "properties": {"year": 2024, "v": 2.0 if not empty_2024 else None}},
    ]
    if empty_2024:
        feats = [f for f in feats if f["properties"]["year"] == 2022] + [
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [104, 30]},
             "properties": {"year": 2023, "v": 1.5}}]
    return {
        "version": "2.0",
        "sources": {"s": {"type": "geojson",
                          "inlineData": {"type": "FeatureCollection", "features": feats}}},
        "layers": [{"id": "l", "type": "circle", "source": "s"}],
        "layout": {"frames": [
            {"id": "f1", "title": "A"}, {"id": "f2", "title": "B"}]},
        "frame_product": {
            "schema_version": "frame_product.v1", "enabled": True,
            "product_kind": "time_series", "grid": {"columns": 2},
            "frames": [
                {"frame_id": "f1", "data": {"ref": "r", "layer_id": "l",
                    "field": "v",
                    "where": {"op": "eq", "field": "year", "value": 2022}}},
                {"frame_id": "f2", "data": {"ref": "r", "layer_id": "l",
                    "field": "v",
                    "where": {"op": "eq", "field": "year", "value": 2024}}},
            ],
        },
    }


def test_not_bound_when_no_product():
    out = verify_frame_product({"version": "2.0"})
    assert out["status"] == "not_bound"


def test_no_observations_is_incomplete_not_failed():
    out = verify_frame_product(_doc(), mapspec_revision=9)
    assert out["status"] == "incomplete"
    assert out["summary"]["not_observed"] == 2
    assert "frame_not_observed:f1" in out["problems"]


def test_single_frame_ack_does_not_complete_product():
    """只有一帧成功观察 —— 产品仍未完成（逐幅汇总不掩盖）。"""
    out = verify_frame_product(
        _doc(), observed={"f1": {"revision": 9, "mode": "live_observed"}},
        mapspec_revision=9)
    assert out["status"] == "incomplete"
    assert out["summary"]["live_observed"] == 1
    assert out["summary"]["verified"] == 1
    assert out["summary"]["required"] == 2


def test_all_frames_observed_completes():
    out = verify_frame_product(
        _doc(),
        observed={"f1": {"revision": 9, "mode": "live_observed"},
                  "f2": {"revision": 9, "mode": "export_verified"}},
        mapspec_revision=9)
    assert out["status"] == "complete"
    assert out["summary"]["verified"] == 2


def test_snapshot_does_not_impersonate_live():
    out = verify_frame_product(
        _doc(), observed={"f1": {"revision": 9, "mode": "snapshot"},
                          "f2": {"revision": 9, "mode": "live_observed"}},
        mapspec_revision=9)
    assert out["status"] == "incomplete"
    assert out["summary"]["snapshot_only"] == 1
    assert out["summary"]["live_observed"] == 1


def test_stale_observation_rejected():
    out = verify_frame_product(
        _doc(), observed={"f1": {"revision": 3, "mode": "live_observed"},
                          "f2": {"revision": 9, "mode": "live_observed"}},
        mapspec_revision=9)
    assert out["status"] == "incomplete"
    assert "stale_observation:f1" in out["problems"]


def test_empty_frame_flagged_not_hidden():
    doc = _doc(empty_2024=True)
    # f2 的 where（year=2024）在数据中无匹配 → 空帧。
    out = verify_frame_product(
        doc, observed={"f1": {"revision": 9, "mode": "live_observed"},
                       "f2": {"revision": 9, "mode": "live_observed"}},
        mapspec_revision=9)
    row2 = next(r for r in out["frames"] if r["frame_id"] == "f2")
    assert row2["binding"] == "empty_data"
    assert "frame_no_data:f2" in out["problems"]
    # 空帧按绑定面不通过 —— 完成状态如实。
    assert out["status"] == "incomplete"


def test_required_subset_scopes_completion():
    """按当前目标收窄 required 集合 —— 非必需画幅不阻塞完成。"""
    out = verify_frame_product(
        _doc(), observed={"f1": {"revision": 9, "mode": "live_observed"}},
        mapspec_revision=9, required_frame_ids=["f1"])
    assert out["status"] == "complete"
    assert out["summary"]["required"] == 1


def test_evidence_rows_bounded_refs_only():
    out = verify_frame_product(
        _doc(), observed={"f1": {"revision": 9, "mode": "live_observed"}},
        mapspec_revision=9)
    rows = frame_evidence_rows(out)
    assert len(rows) == 2
    assert rows[0]["id"] == "frame:f1" and rows[0]["status"] == "verified"
    assert rows[1]["status"] == "not_observed"
