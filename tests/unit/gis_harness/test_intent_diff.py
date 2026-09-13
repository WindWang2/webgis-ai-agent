"""intent_diff 单测 —— 最小失效/携带裁决的确定性契约（方向 5 E5-E6）。

覆盖任务书场景 1-3/8 的计划侧语义：
- 同 subject 换 category（小学→高中）→ boundary 携带、subject 链失效；
- scope 收缩（只看主城区）→ 数据行失效、依赖闭包随之失效；
- global 重塑维（task/recipe/measure/…）→ 零携带（等价现状全量失效）。
"""
from __future__ import annotations

from app.services.gis_harness.intent_diff import (
    ChapterIntentDiff,
    diff_chapters,
    intent_dimension_diff,
    semantic_row_signature,
)


def _req(cap, *, status="pending", ref="", alg="a1", tool="t1", params=None,
         deps=None, purpose=""):
    return {
        "capability": cap, "purpose": purpose, "status": status,
        "bound_ref": ref, "resolved_tool": tool, "resolved_algorithm": alg,
        "params": params or {}, "depends_on": deps or [], "optional": False,
    }


def _step(cap, *, status="pending", ref="", alg="a1", tool="t1", params=None,
          deps=None, purpose=""):
    return {
        "capability": cap, "purpose": purpose, "status": status,
        "bound_ref": ref, "resolved_tool": tool, "resolved_algorithm": alg,
        "params": params or {}, "depends_on": deps or [], "optional": False,
    }


def _chapter(intent=None, reqs=(), steps=(), *, recipe="poi_stats",
             query="成都小学分布"):
    return {
        "plan_id": "p1", "query": query, "recipe_id": recipe,
        "intent": intent or {},
        "data_requirements": list(reqs), "analysis_steps": list(steps),
    }


def _intent(scope="成都市", subject="小学", task="distribution_overview",
            measure="count"):
    return {
        "scope": {"name": scope, "level": "city"},
        "subject": {"type": "poi", "category": subject},
        "task": task, "measure": measure, "time": "",
    }


_BASE_REQS = [
    _req("fetch_adm_boundary", status="available", ref="ref:boundary-1",
         params={"scope": "成都市"}, purpose="成都市行政边界"),
    _req("fetch_poi_primary", status="available", ref="ref:poi-1",
         params={"category": "小学"}, purpose="小学POI"),
]
_BASE_STEPS = [
    _step("aggregate_by_district", status="done", ref="ref:agg-1",
          deps=["fetch_poi_primary", "fetch_adm_boundary"]),
    _step("density_heatmap", status="done", ref="ref:heat-1",
          deps=["fetch_poi_primary"]),
]


def test_identical_chapters_carry_all_completions():
    """同语义 replace（如误触发 intent 重提）：完成事实全部携带。"""
    old = _chapter(intent=_intent(), reqs=_BASE_REQS, steps=_BASE_STEPS)
    new = _chapter(intent=_intent(), reqs=_BASE_REQS, steps=_BASE_STEPS)
    diff = diff_chapters(old, new)
    assert isinstance(diff, ChapterIntentDiff)
    assert diff.fine_dims == []
    assert diff.global_reshape is False
    assert set(diff.carried) == {
        "fetch_adm_boundary", "fetch_poi_primary",
        "aggregate_by_district", "density_heatmap"}
    assert diff.carried["fetch_adm_boundary"] == "ref:boundary-1"
    assert diff.lost == []


def test_subject_swap_keeps_boundary_drops_subject_chain():
    """场景3：成都小学→成都高中 —— boundary 携带，subject 链最小失效。"""
    old = _chapter(intent=_intent(), reqs=_BASE_REQS, steps=_BASE_STEPS)
    new_reqs = [
        _BASE_REQS[0],  # boundary 行完全同签名
        _req("fetch_poi_high", status="available", ref="ref:poi-h1",
             params={"category": "高中"}),
    ]
    new_steps = [
        _step("aggregate_by_district", status="done", ref="ref:agg-1",
              deps=["fetch_poi_high", "fetch_adm_boundary"]),
        _step("density_heatmap", status="done", ref="ref:heat-1",
              deps=["fetch_poi_high"]),
    ]
    new = _chapter(intent=_intent(subject="高中"),
                   reqs=new_reqs, steps=new_steps)
    diff = diff_chapters(old, new)
    assert "subject" in diff.fine_dims
    assert diff.carried == {"fetch_adm_boundary": "ref:boundary-1"}
    lost_caps = {item["capability"] for item in diff.lost}
    # fetch_poi_primary 被新计划丢弃（fetch_poi_high 顶替）→ 不披露；
    # 依赖它的下游行同签名面变化 → 失效披露。
    assert lost_caps == {"aggregate_by_district", "density_heatmap"}
    assert all(item["dimension"] == "data" for item in diff.lost)


def test_subject_swap_same_capability_params_change_is_parameter_dim():
    """同 capability 换参（category 小学→高中）→ parameter 维披露。"""
    old = _chapter(intent=_intent(), reqs=_BASE_REQS, steps=_BASE_STEPS)
    new_reqs = [
        _BASE_REQS[0],
        _req("fetch_poi_primary", status="available", ref="ref:poi-2",
             params={"category": "高中"}),
    ]
    new_steps = [_BASE_STEPS[0], _BASE_STEPS[1]]
    new = _chapter(intent=_intent(subject="高中"),
                   reqs=new_reqs, steps=new_steps)
    diff = diff_chapters(old, new)
    assert diff.carried == {"fetch_adm_boundary": "ref:boundary-1"}
    by_cap = {item["capability"]: item for item in diff.lost}
    assert by_cap["fetch_poi_primary"]["dimension"] == "parameter"
    # 下游依赖未携带 → 一并失效（闭包保守）
    assert "aggregate_by_district" in by_cap
    assert "density_heatmap" in by_cap


def test_scope_change_invalidates_data_rows_and_closure():
    """场景2：只看主城区 —— 数据行失效（scope 维门），下游随之失效。"""
    old = _chapter(intent=_intent(), reqs=_BASE_REQS, steps=_BASE_STEPS)
    new_reqs = [
        _req("fetch_adm_boundary", status="available", ref="",
             params={"scope": "成都市主城区"}),
        _req("fetch_poi_primary", status="available", ref="",
             params={"category": "小学"}),
    ]
    new = _chapter(intent=_intent(scope="成都市主城区"),
                   reqs=new_reqs, steps=_BASE_STEPS)
    diff = diff_chapters(old, new)
    assert "scope" in diff.fine_dims
    assert diff.carried == {}
    lost_caps = {item["capability"] for item in diff.lost}
    assert lost_caps == {
        "fetch_adm_boundary", "fetch_poi_primary",
        "aggregate_by_district", "density_heatmap"}


def test_global_reshape_dims_carry_nothing():
    """task/recipe/measure 等 global 维变化 → 等价现状全量失效。"""
    old = _chapter(intent=_intent(), reqs=_BASE_REQS, steps=_BASE_STEPS)
    variants = [
        _chapter(intent=_intent(task="accessibility_analysis"),
                 reqs=_BASE_REQS, steps=_BASE_STEPS),
        _chapter(intent=_intent(measure="density"),
                 reqs=_BASE_REQS, steps=_BASE_STEPS),
        _chapter(intent=_intent(), recipe="accessibility_v2",
                 reqs=_BASE_REQS, steps=_BASE_STEPS),
    ]
    for new in variants:
        diff = diff_chapters(old, new)
        assert diff.global_reshape is True
        assert diff.carried == {}
        assert {item["capability"] for item in diff.lost} == {
            "fetch_adm_boundary", "fetch_poi_primary",
            "aggregate_by_district", "density_heatmap"}


def test_algorithm_change_reports_algorithm_dim():
    old = _chapter(intent=_intent(), reqs=_BASE_REQS, steps=_BASE_STEPS)
    new_reqs = [
        _BASE_REQS[0],
        _req("fetch_poi_primary", status="available", ref="ref:poi-1",
             alg="a2"),
    ]
    new = _chapter(intent=_intent(), reqs=new_reqs, steps=_BASE_STEPS)
    diff = diff_chapters(old, new)
    by_cap = {item["capability"]: item for item in diff.lost}
    assert by_cap["fetch_poi_primary"]["dimension"] == "algorithm"


def test_capability_dropped_from_new_plan_not_disclosed():
    """新计划不再需要的行：既不携带也不披露（无需重算也无需失效）。"""
    old = _chapter(intent=_intent(), reqs=_BASE_REQS, steps=_BASE_STEPS)
    new = _chapter(intent=_intent(), reqs=[_BASE_REQS[0]],
                   steps=[_step("chart_only", deps=["fetch_adm_boundary"])])
    diff = diff_chapters(old, new)
    assert diff.carried == {"fetch_adm_boundary": "ref:boundary-1"}
    lost_caps = {item["capability"] for item in diff.lost}
    assert "density_heatmap" not in lost_caps
    assert "fetch_poi_primary" not in lost_caps


def test_new_capability_is_pending_not_carried():
    old = _chapter(intent=_intent(), reqs=_BASE_REQS, steps=_BASE_STEPS)
    new_reqs = _BASE_REQS + [_req("fetch_network_roads")]
    new = _chapter(intent=_intent(), reqs=new_reqs, steps=_BASE_STEPS)
    diff = diff_chapters(old, new)
    assert "fetch_network_roads" not in diff.carried
    assert all(item["capability"] != "fetch_network_roads"
               for item in diff.lost)


def test_optional_failed_old_rows_are_not_completions():
    """failed/unavailable 旧行不是完成事实 —— 不携带也不算 lost。"""
    old = _chapter(intent=_intent(), reqs=[
        _BASE_REQS[0],
        _req("fetch_poi_primary", status="failed"),
    ], steps=[])
    new = _chapter(intent=_intent(), reqs=_BASE_REQS, steps=[])
    diff = diff_chapters(old, new)
    assert diff.carried == {"fetch_adm_boundary": "ref:boundary-1"}
    assert all(item["capability"] != "fetch_poi_primary"
               for item in diff.lost)


def test_empty_or_missing_chapters_degrade_honestly():
    assert diff_chapters(None, _chapter()) == ChapterIntentDiff()
    assert diff_chapters(_chapter(), None).carried == {}
    empty = diff_chapters(_chapter(), _chapter(reqs=[], steps=[]))
    assert empty.carried == {}


def test_intent_dimension_diff_is_deterministic_and_scoped():
    a = _chapter(intent=_intent())
    b = _chapter(intent=_intent(subject="高中", scope="主城区"))
    dims = intent_dimension_diff(a, b)
    assert dims == ["scope", "subject"]  # _INTENT_FACTS 声明序
    assert intent_dimension_diff(a, a) == []


def test_semantic_row_signature_ignores_completion_state():
    """签名只含语义面：status/bound_ref 变化不影响签名（完成事实可携带）。"""
    row = _req("x", status="available", ref="ref:1", params={"k": 1})
    done = _req("x", status="pending", ref="", params={"k": 1})
    assert semantic_row_signature(row) == semantic_row_signature(done)
    changed = _req("x", status="pending", ref="", params={"k": 2})
    assert semantic_row_signature(row) != semantic_row_signature(changed)


def test_output_is_bounded():
    old_reqs = [_req(f"cap_{i:02d}", status="available", ref=f"ref:{i}")
                for i in range(80)]
    old = _chapter(reqs=old_reqs)
    new = _chapter(reqs=[_req(f"cap_{i:02d}", params={"v": i})
                         for i in range(80)])
    diff = diff_chapters(old, new)
    assert len(diff.lost) <= 32
    bounded = diff.to_bounded_dict()
    assert len(bounded["lost"]) <= 32
