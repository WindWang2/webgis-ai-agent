"""SpatialGoalGraph（V4 Wave 3）回归锁。

不变式：
- 确定性展开：同章节同图（fingerprint 稳定），零 LLM；
- 成都小学教育公平目标：确定性展开覆盖任务书要求的 13 个方法学侧面
  （学校数据 / 行政边界 / 数据检视 / CRS 验证 / 点分布 / 密度聚合 /
  分母获取 / 人均归一化 / 公平性统计 / 统计图表 / 制图 / 观察 / 验证）；
- candidate 校验：LLM 建议图由确定性 validator 收敛（词表/规模/环/能力
  对账），绝不由语言直接造节点；
- 有界（≤48 节点）；diff 可比较；状态是章节行只读投影。
"""
from __future__ import annotations


from app.services.gis_harness.goal_graph import (
  DependencyKind,
  GoalNodeKind,
  build_goal_graph,
  validate_candidate_graph,
)


def _equity_chapter() -> dict:
    """成都小学教育公平章节：真实确定性 planner 输出（零 LLM）。"""
    from app.services.gis_harness.intent import resolve_map_request_intent
    from app.services.gis_harness.planner import MapProductPlanner

    intent = resolve_map_request_intent("分析成都市小学分布和各区教育公平情况")
    plan = MapProductPlanner().plan_from_intent(intent, use_memo=False)
    return plan.model_dump()


def _ids(g):
    return {n.id for n in g.nodes}


def test_deterministic_fingerprint():
    ch = _equity_chapter()
    a = build_goal_graph(ch)
    b = build_goal_graph(ch)
    assert a.fingerprint() == b.fingerprint()
    assert a.to_bounded_dict() == b.to_bounded_dict()


def test_chengdu_schools_equity_expansion_covers_required_aspects():
    ch = _equity_chapter()
    g = build_goal_graph(ch)
    ids = _ids(g)
    kinds = {n.kind for n in g.nodes}
    # 节点种类骨架齐备
    for kind in (
        GoalNodeKind.GOAL, GoalNodeKind.ACQUIRE, GoalNodeKind.INSPECT,
        GoalNodeKind.VALIDATE, GoalNodeKind.ANALYZE, GoalNodeKind.CARTOGRAPHY,
        GoalNodeKind.OBSERVE, GoalNodeKind.VERIFY, GoalNodeKind.DELIVER,
    ):
        assert kind.value in kinds, f"missing node kind {kind}"
    # 任务书 13 侧面（按节点 id / kind 对账）
    assert "acquire:poi_query" in ids                       # 学校数据集
    assert "acquire:admin_boundary_query" in ids            # 行政边界
    assert any(n.kind == GoalNodeKind.INSPECT.value for n in g.nodes)   # 数据检视
    assert any(n.kind == GoalNodeKind.VALIDATE.value for n in g.nodes)  # CRS/几何验证
    assert "acquire:denominator" in ids                     # 分母获取（红线）
    assert "analyze:normalization" in ids                   # 人均/率归一化
    assert any(n.capability == "admin_aggregation" for n in g.nodes)    # 密度/聚合
    assert any(n.capability in ("global_morans_i", "local_morans_i")
               for n in g.nodes)                            # 公平性/可达统计
    assert any(n.kind == GoalNodeKind.DISCLOSE.value for n in g.nodes)  # 披露
    # 分母欠账在图上可见：acquire:denominator BLOCKED，deliver 依赖披露
    denom = g.node("acquire:denominator")
    assert denom.status == "blocked"
    deliver = g.node("deliver")
    assert "verify" in deliver.depends_on and "observe" in deliver.depends_on
    assert any(d.startswith("disclose:") for d in deliver.depends_on)
    # 科学红线传播：归一化依赖分母获取（science 边）
    norm = g.node("analyze:normalization")
    assert "acquire:denominator" in norm.depends_on
    assert norm.dep_kinds[norm.depends_on.index("acquire:denominator")] == (
        DependencyKind.SCIENCE.value)


def test_data_access_rows_do_not_duplicate_analysis_nodes():
    ch = _equity_chapter()
    g = build_goal_graph(ch)
    # poi_query 是 data_access 行：有 acquire 链，无 analyze 节点
    assert "acquire:poi_query" in _ids(g)
    assert "analyze:poi_query" not in _ids(g)


def test_rows_bound_updates_status_projection():
    ch = _equity_chapter()
    for row in ch["data_requirements"]:
        if row["capability"] == "poi_query":
            row["status"] = "available"
            row["bound_ref"] = "ref:geojson-x"
    g = build_goal_graph(ch)
    assert g.node("acquire:poi_query").status == "satisfied"


def test_bounded_and_serializable():
    ch = _equity_chapter()
    g = build_goal_graph(ch)
    assert len(g.nodes) <= 48
    d = g.to_bounded_dict()
    assert d["schema"] == "goal_graph.v1"
    assert d["fingerprint"] == g.fingerprint()
    assert len(g.summary_line()) <= 400


def test_diff_added_removed_changed():
    ch = _equity_chapter()
    a = build_goal_graph(ch)
    ch2 = _equity_chapter()
    ch2["analysis_steps"].append({
        "capability": "spatial_interpolation", "purpose": "插值",
        "status": "pending", "bound_ref": "", "depends_on": [],
    })
    b = build_goal_graph(ch2)
    d = a.diff(b)
    assert d["added"] and not d["removed"]
    assert a.diff(a) == {"added": [], "removed": [], "changed": []}


# ── candidate 校验（LLM 建议 → 确定性收敛）──────────────────────────────

def test_candidate_validator_accepts_wellformed():
    ch = _equity_chapter()
    candidate = {
        "goal": ch["query"],
        "nodes": [
            {"id": "goal", "kind": "goal", "depends_on": []},
            {"id": "a", "kind": "acquire", "capability": "poi_query",
             "depends_on": ["goal"]},
            {"id": "b", "kind": "analyze", "capability": "admin_aggregation",
             "depends_on": ["a"]},
        ],
    }
    result = validate_candidate_graph(candidate, ch)
    assert result["valid"], result["errors"]
    assert len(result["normalized"]["nodes"]) == 3


def test_candidate_validator_rejects_fabricated_capability():
    ch = _equity_chapter()
    candidate = {"nodes": [
        {"id": "x", "kind": "model", "capability": "teleportation", "depends_on": []},
    ]}
    result = validate_candidate_graph(candidate, ch)
    assert not result["valid"]
    assert any("unknown capability" in e for e in result["errors"])


def test_candidate_validator_rejects_unknown_kind_and_cycle():
    ch = _equity_chapter()
    bad = {"nodes": [
        {"id": "a", "kind": "vibes", "depends_on": []},
        {"id": "b", "kind": "analyze", "capability": "poi_query", "depends_on": ["c"]},
        {"id": "c", "kind": "verify", "depends_on": ["b"]},
    ]}
    result = validate_candidate_graph(bad, ch)
    assert not result["valid"]
    assert any("invalid node" in e for e in result["errors"])
    cycle = {"nodes": [
        {"id": "b", "kind": "analyze", "capability": "poi_query", "depends_on": ["c"]},
        {"id": "c", "kind": "verify", "depends_on": ["b"]},
    ]}
    result = validate_candidate_graph(cycle, ch)
    assert not result["valid"]
    assert any("cycle" in e for e in result["errors"])


def test_candidate_validator_rejects_oversize_and_drops_dangling_deps():
    ch = _equity_chapter()
    big = {"nodes": [
        {"id": f"n{i}", "kind": "analyze", "depends_on": []} for i in range(60)
    ]}
    result = validate_candidate_graph(big, ch)
    assert not result["valid"]
    dangling = {"nodes": [
        {"id": "a", "kind": "acquire", "capability": "poi_query",
         "depends_on": ["ghost", "goal"]},
        {"id": "goal", "kind": "goal", "depends_on": []},
    ]}
    result = validate_candidate_graph(dangling, ch)
    assert result["valid"], result["errors"]
    node = [n for n in result["normalized"]["nodes"] if n["id"] == "a"][0]
    assert node["depends_on"] == ["goal"]
