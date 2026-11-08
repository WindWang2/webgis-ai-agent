"""S3 — Selection History：多轮指代的选择史投影（ADR-0285 D3）。

「刚才那组学校」＝previous 句柄；史由 ledger 环纯函数重建（零 IO、序号
驱动）；不可解析句柄 → None（绝不随机挑数据）。
"""
from __future__ import annotations

from app.services.gis_situation.selection_history import (
    project_selection_history,
    resolve_selection_ref,
)


def _ev(seq: int, kind: str, payload: dict) -> dict:
    return {
        "event_id": f"c-{seq:08d}", "client_instance_id": "t-aaaa0001",
        "client_seq": seq, "server_seq": seq, "kind": kind, "payload": payload,
    }


def test_history_two_selection_epochs():
    events = [
        _ev(1, "feature_selected", {
            "layer_id": "schools", "dataset_ref": "ref:geojson-aaa",
            "selected_ids": ["sch-1", "sch-2"]}),
        _ev(2, "viewport_settled", {"center": [104, 30], "zoom": 11}),
        _ev(3, "feature_selected", {
            "layer_id": "schools", "dataset_ref": "ref:geojson-aaa",
            "feature_id": "sch-9"}),
    ]
    history = project_selection_history(events)
    assert len(history) == 2
    assert history[0]["feature_ids"] == ["sch-9"]  # 当前代在前
    assert history[0]["superseded"] is False
    assert history[1]["feature_ids"] == ["sch-1", "sch-2"]
    assert history[1]["superseded"] is True


def test_resolve_previous_handle():
    events = [
        _ev(1, "feature_selected", {
            "layer_id": "schools", "dataset_ref": "ref:geojson-aaa",
            "selected_ids": ["sch-1", "sch-2"]}),
        _ev(2, "feature_selected", {
            "layer_id": "schools", "dataset_ref": "ref:geojson-aaa",
            "selected_ids": ["sch-8"]}),
    ]
    claimed = resolve_selection_ref(events, "previous")
    assert claimed is not None
    assert claimed["feature_ids"] == ["sch-1", "sch-2"]
    assert claimed["sources"] == ["selection_history"]
    assert claimed["claimed_at_seq"] == 1


def test_resolve_epoch_seq_handle_and_miss():
    events = [
        _ev(5, "feature_selected", {
            "layer_id": "schools", "dataset_ref": "ref:geojson-aaa",
            "selected_ids": ["sch-3"]}),
        _ev(6, "feature_selected", {
            "layer_id": "schools", "dataset_ref": "ref:geojson-aaa",
            "selected_ids": ["sch-4"]}),
    ]
    claimed = resolve_selection_ref(events, "5")
    assert claimed is not None
    assert claimed["feature_ids"] == ["sch-3"]
    assert resolve_selection_ref(events, "999") is None  # 越界不猜
    assert resolve_selection_ref(events, "bogus") is None  # 非法词表拒绝


def test_deselect_clears_current_epoch():
    events = [
        _ev(1, "feature_selected", {
            "layer_id": "schools", "dataset_ref": "ref:geojson-aaa",
            "selected_ids": ["sch-1"]}),
        _ev(2, "feature_selected", {
            "layer_id": "schools", "dataset_ref": "ref:geojson-aaa",
            "selected_ids": ["sch-2", "sch-3"]}),
        _ev(3, "feature_deselected", {}),
    ]
    history = project_selection_history(events)
    # deselect 终结当前代（sch-2/3 标记 cleared，史保留 ids）。
    cleared = [e for e in history if e["cleared"]]
    assert len(cleared) == 1
    assert cleared[0]["feature_ids"] == ["sch-2", "sch-3"]
    # 无活跃当前代时「刚才那组」= 最近被清除代（有据可解，验证面再裁决）。
    claimed = resolve_selection_ref(events, "previous")
    assert claimed is not None
    assert claimed["feature_ids"] == ["sch-2", "sch-3"]


def test_partial_deselect_removes_id_from_epoch():
    events = [
        _ev(1, "feature_selected", {
            "layer_id": "schools", "dataset_ref": "ref:geojson-aaa",
            "selected_ids": ["sch-1", "sch-2", "sch-3"]}),
        _ev(2, "feature_deselected", {"layer_id": "schools", "feature_id": "sch-2"}),
    ]
    history = project_selection_history(events)
    assert history[0]["feature_ids"] == ["sch-1", "sch-3"]
    assert history[0]["cleared"] is False


def test_history_bounded_entries():
    events = [
        _ev(i, "feature_selected", {
            "layer_id": "schools", "dataset_ref": "ref:geojson-aaa",
            "selected_ids": [f"sch-{i}"]})
        for i in range(1, 12)
    ]
    history = project_selection_history(events)
    assert len(history) <= 6  # 有界


def test_history_truncated_disclosure():
    """SW-02/#2004：史代 ids 与 IR 摄入同界（≤50）；>50 才截断披露。

    12 个 id 在新界内完整保留（旧行为静默截到 8 是 #2004 断点）。
    """
    raw_ids = [f"sch-{i}" for i in range(1, 13)]
    events = [_ev(1, "feature_selected", {
        "layer_id": "schools", "dataset_ref": "ref:geojson-aaa",
        "selected_ids": raw_ids,
    })]
    history = project_selection_history(events)
    assert history[0]["truncated"] is False
    assert history[0]["feature_ids"] == raw_ids
    assert history[0]["matched_count"] == 12

    overflow_ids = [f"sch-{i}" for i in range(1, 53)]  # 52 > 50
    events = [_ev(2, "feature_selected", {
        "layer_id": "schools", "dataset_ref": "ref:geojson-aaa",
        "selected_ids": overflow_ids, "matched_count": 52, "ids_truncated": True,
    })]
    history = project_selection_history(events)
    assert history[0]["truncated"] is True
    assert len(history[0]["feature_ids"]) == 50
    assert history[0]["matched_count"] == 52
    assert history[0]["ids_truncated"] is True


def test_review_table_clear_closes_current_epoch():
    """review P2-2：table clear 终结当前代——previous 不再错代。"""
    events = [
        _ev(1, "feature_selected", {
            "layer_id": "schools", "dataset_ref": "ref:geojson-aaa",
            "selected_ids": ["sch-1"]}),
        _ev(2, "table_selection_changed", {
            "layer_id": "schools", "feature_id": "sch-9", "action": "select"}),
        _ev(3, "table_selection_changed", {
            "layer_id": "schools", "action": "clear"}),
    ]
    history = project_selection_history(events)
    cleared = [e for e in history if e["cleared"]]
    assert cleared and cleared[0]["feature_ids"] == ["sch-9"]
    # 「刚才」= 最近被清除的 sch-9（不再是 sch-1）。
    claimed = resolve_selection_ref(events, "previous")
    assert claimed is not None
    assert claimed["feature_ids"] == ["sch-9"]


def test_review_chart_select_ends_current_epoch():
    """review P3：chart 类别选择后历史与投影两面一致（当前代终结）。"""
    events = [
        _ev(1, "feature_selected", {
            "layer_id": "schools", "dataset_ref": "ref:geojson-aaa",
            "selected_ids": ["sch-1"]}),
        _ev(2, "chart_selection_changed", {
            "layer_id": "districts", "field": "type",
            "categories": ["A"], "action": "select"}),
    ]
    history = project_selection_history(events)
    assert history[0]["superseded"] is True  # 要素选择代被 chart 选择替代


def test_previous_after_chart_replacement_is_the_last_feature_epoch():
    events = [{"server_seq": 1, "kind": "feature_selected", "payload": {"layer_id": "schools", "feature_id": "1"}},
              {"server_seq": 2, "kind": "chart_selection_changed", "payload": {"layer_id": "schools", "categories": ["school"]}}]
    assert resolve_selection_ref(events, "previous")["feature_ids"] == ["1"]
    assert resolve_selection_ref(events, "²") is None
    assert resolve_selection_ref(events, "9" * 5000) is None
