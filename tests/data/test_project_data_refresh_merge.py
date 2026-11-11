"""G10 UPD-04 / O06：user-wins 三方合并纯单元测试（PU 层）。

真实 MapSpec 形态（app/lib/cartography/mapspec_schema.py）：
layers[].id / source / paint / layout / visible / legend_spec / label；
workbench.lockedLayerIds / lockedComponentIds；legend.position；components[]。
合并必须按**稳定 entity id**，禁止数组下标；禁止以「更新数据」为由重置人工编辑。
"""
from __future__ import annotations

import copy

from app.services.project_runtime.refresh.merge import three_way_merge


def _baseline() -> dict:
    return {
        "title": "学校分布 2024",
        "legend": {"title": "学校数", "position": "bottom-left", "visible": True},
        "layers": [
            {
                "id": "L1", "source": "ref://schools-v1", "type": "circle",
                "paint": {"circle-color": "#3388ff", "circle-opacity": 0.8},
                "layout": {}, "visible": True,
            },
            {
                "id": "L2", "source": "ref://heat-v1", "type": "heatmap",
                "paint": {"heatmap-radius": 12}, "visible": True,
            },
            {
                "id": "L3", "source": "ref://labels-v1", "type": "symbol",
                "label": {"field": "name"}, "visible": True,
            },
        ],
        "components": [
            {"id": "C1", "type": "text", "text": "制图：遂宁校区", "position": {"x": 10, "y": 20}},
        ],
        "workbench": {"lockedLayerIds": [], "lockedComponentIds": []},
    }


def _current() -> dict:
    """用户在 baseline 之后做的人工调整。"""
    doc = copy.deepcopy(_baseline())
    doc["title"] = "学校分布 2024（人工修订）"
    doc["legend"]["position"] = "top-right"
    doc["layers"][0]["paint"]["circle-color"] = "#e11d48"   # 颜色
    doc["layers"][0]["paint"]["circle-opacity"] = 0.35      # 透明度
    doc["layers"][1]["visible"] = False                     # 隐藏
    doc["layers"] = [doc["layers"][1], doc["layers"][0], doc["layers"][2]]  # 层序
    doc["components"][0]["text"] = "制图：遂宁校区 · 已复核"   # 文本
    doc["components"][0]["position"] = {"x": 88, "y": 44}   # 组件位置
    doc["workbench"]["lockedLayerIds"] = ["L1"]             # 族锁
    doc["workbench"]["lockedComponentIds"] = ["C1"]
    return doc


def _candidate() -> dict:
    """新数据候选：数据语义更新，但表现层被重生成（默认值）。"""
    doc = copy.deepcopy(_baseline())
    doc["layers"][0]["source"] = "ref://schools-v2"
    doc["layers"][0]["paint"] = {"circle-color": "#3388ff", "circle-opacity": 1.0}
    doc["layers"][1]["source"] = "ref://heat-v2"
    doc["layers"][1]["visible"] = True
    doc["legend"]["position"] = "bottom-left"
    doc["title"] = "学校分布 2025"
    doc["components"][0]["text"] = "自动生成的标题"
    doc["workbench"]["lockedLayerIds"] = []
    return doc


def test_o06a_user_style_order_visibility_locks_text_are_preserved():
    result = three_way_merge(
        baseline=_baseline(), current=_current(), candidate=_candidate(),
    )
    merged = result.merged_mapspec
    layers = {layer["id"]: layer for layer in merged["layers"]}

    # 数据语义来自候选
    assert layers["L1"]["source"] == "ref://schools-v2"
    assert layers["L2"]["source"] == "ref://heat-v2"
    # 人工表现层全部保留（user-wins）
    assert layers["L1"]["paint"]["circle-color"] == "#e11d48"
    assert layers["L1"]["paint"]["circle-opacity"] == 0.35
    assert layers["L2"]["visible"] is False
    assert [layer["id"] for layer in merged["layers"]] == ["L2", "L1", "L3"]
    assert merged["legend"]["position"] == "top-right"
    assert merged["title"] == "学校分布 2024（人工修订）"
    assert merged["components"][0]["text"] == "制图：遂宁校区 · 已复核"
    assert merged["components"][0]["position"] == {"x": 88, "y": 44}
    assert "L1" in merged["workbench"]["lockedLayerIds"]
    assert "C1" in merged["workbench"]["lockedComponentIds"]

    assert result.conflicts == []
    assert any("color" in p or "paint" in p for p in result.preserved)
    assert result.user_wins is True


def test_o06b_deleted_annotation_target_and_removed_field_are_explicit_conflicts():
    cand = _candidate()
    # 候选删除了 L3（用户在 baseline/current 里给它挂了标注）
    cand["layers"] = [layer for layer in cand["layers"] if layer["id"] != "L3"]
    # 候选移除了 current 图表/标签引用的字段
    result = three_way_merge(
        baseline=_baseline(), current=_current(), candidate=cand,
        removed_fields={"L1": ["students"], "L3": ["name"]},
        annotation_targets={"L3": "name"},
    )
    kinds = {c.kind for c in result.conflicts}
    assert "deleted_annotation_target" in kinds
    assert "removed_field" in kinds
    # 不把冲突静默迁移到别的对象
    assert not any(c.kind == "auto_rebind" for c in result.conflicts)
    assert all(c.entity_id for c in result.conflicts)


def test_o06c_hard_semantic_error_is_not_legitimized_by_user_wins():
    result = three_way_merge(
        baseline=_baseline(), current=_current(), candidate=_candidate(),
        hard_semantic_errors=["unit_mismatch:students(person vs thousand_person)"],
    )
    assert result.hard_semantic_errors
    assert result.user_wins is True           # 表现层仍保留
    assert result.adoptable is False          # 但不得被采纳为合法科学结论


def test_o06d_no_user_edits_means_candidate_is_used_as_is():
    result = three_way_merge(
        baseline=_baseline(), current=_baseline(), candidate=_candidate(),
    )
    assert result.merged_mapspec["layers"][0]["paint"]["circle-color"] == "#3388ff"
    assert result.merged_mapspec["legend"]["position"] == "bottom-left"
    assert result.conflicts == []


def test_o06e_array_index_is_never_used_as_identity():
    """候选把层顺序打乱，合并仍按 id 对齐，不把颜色贴到错的层。"""
    cand = _candidate()
    cand["layers"] = list(reversed(cand["layers"]))
    result = three_way_merge(
        baseline=_baseline(), current=_current(), candidate=cand,
    )
    layers = {layer["id"]: layer for layer in result.merged_mapspec["layers"]}
    assert layers["L1"]["paint"]["circle-color"] == "#e11d48"
    assert layers["L2"]["visible"] is False
