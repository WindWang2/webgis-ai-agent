"""WP5 · 真旅程测试：自然语言任务 → 角色绑定 → 覆盖/缺口 → 供给计划。

GOAL-20261126-02 E3 验收：≥4 个场景从任务意图到可信供给。所有外部服务
走 fixture 模拟协议（会话种子资产 = 本地已配置通道的真实生产路径；
OSM/远端 provider 不可用场景显式标注 fixture，不伪装真实数据）。

旅程（每条 = 发现 → 覆盖 → 计划 → 决策 → 执行 → 产物 ref → 资格闭环）：
1. 成都小学分布：行政边界 + POI 主体 + 人口分母；POI 完备性未知必须披露；
   率结论无分母 → 前置缺口；有分母后可达率资格；
2. 两时相土地利用：target_time+baseline 双角色；时间窗不相交 → 期次不匹配；
   nodata 折减有效覆盖 → partial；CRS/分辨率可比性缺口；
3. 公共服务可达性：facility + network；展示级底图不进数值统计；
   网络分辨率粗于需求 → 粒度缺口；
4. 统计表 + 行政边界：边界年份与统计口径不匹配 → temporal 缺口；
   边界 footprint 有洞 → partial（非 bbox 宣称的全覆盖）；
5. 诚实分流：空会话 = not_searched（≠ not_available）；远端失败 = failed
   类型化披露；跨会话计划不可见；取消保留 partial 产物。
"""
import pytest

from app.services.artifact_registry import register_artifact
from app.services.data_catalog.catalog import reset_data_catalog
from app.services.data_lifecycle.service import reset_lifecycle_service
from app.tools.registry import ToolRegistry
from app.tools.task_data_supply_tools import register_task_data_supply_tools


@pytest.fixture(autouse=True)
def _reset_env():
    reset_data_catalog()
    reset_lifecycle_service()
    yield
    reset_data_catalog()
    reset_lifecycle_service()


@pytest.fixture()
def registry():
    reg = ToolRegistry()
    register_task_data_supply_tools(reg)
    return reg


async def _seed(sid: str, **kw) -> None:
    await register_artifact(
        sid, artifact_id=kw["id"], producer_tool="fixture_seed",
        artifact_type=kw.get("type", "poi_feature_set"),
        descriptor={
            "bbox": kw["bbox"], "feature_count": kw.get("count"),
            "crs": kw.get("crs", "EPSG:4326")},
        metadata={
            "field_schema": kw.get("fields", {"name": {"type": "string"}}),
            "logical_role": kw["role"], "tags": ["fixture"],
            "display_name": kw.get("name", kw["id"])})


# ── 旅程 1：成都小学分布 ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_journey_chengdu_schools(registry):
    sid = "j1-chengdu"
    # fixture 种子：本地已治理通道（边界/POI/人口栅格分母）
    await _seed(sid, id="ref:cd-boundary", role="boundary",
                type="boundary_polygon", bbox=[103.9, 30.5, 104.6, 31.0],
                fields={"adcode": {"type": "string"},
                        "name": {"type": "string"}})
    await _seed(sid, id="ref:cd-schools", role="observation",
                bbox=[104.0, 30.6, 104.3, 30.9], count=820)
    await _seed(sid, id="ref:cd-pop", role="source", type="raster_cog",
                bbox=[103.9, 30.5, 104.6, 31.0])

    # ① 发现：subject + boundary + denominator
    disc = await registry.dispatch(
        "discover_task_data",
        {"place": "成都", "roles_csv": "subject,boundary,denominator"},
        session_id=sid)
    assert disc["success"] is True
    ids = {c["candidate_id"] for c in disc["candidates"]}
    assert {"ref:cd-boundary", "ref:cd-schools", "ref:cd-pop"} <= ids
    # population 角色未声明 candidate → 不冒充
    # ② 覆盖：POI 完备性未知必须显式披露（place 讲用户故事 + bbox 供计算）
    cov = await registry.dispatch(
        "compare_task_coverage",
        {"place": "成都", "bbox": "103.9,30.5,104.6,31.0",
         "roles_csv": "subject,boundary,denominator"},
        session_id=sid)
    assert cov["success"] is True
    by_role = {r["role"]: r for r in cov["coverage"]["roles"]}
    subj = by_role["subject"]["evaluations"][0]
    assert subj["completeness"] == "unknown"
    assert any("完备" in d for d in subj["disclosures"])
    # 边界候选（声明 bbox 相交、无 footprint）→ declared_only
    bnd = by_role["boundary"]["evaluations"][0]
    assert bnd["verdict"] == "declared_only"

    # ③ 率需求无分母绑定 → 前置缺口码
    from app.services.task_data_supply.requirement import (
        TaskScope, task_requirement)
    from app.services.task_data_supply.coverage import evaluate_task_coverage
    from app.services.task_data_supply.discovery import (
        discover_task_candidates)

    req = task_requirement(
        TaskScope(place="成都"),
        [{"role": "subject", "consumption_methods": ["rate"],
          "needs_denominator": True}],
        provenance=[{"source": "journey"}])
    cands, _meta = await discover_task_candidates(
        session_id=sid, task_scope=req.task_scope, roles=("subject",),
        limit=24)
    matrix = evaluate_task_coverage(req, cands)
    ev = matrix.roles[0].evaluations[0]
    assert any(g["dimension"] == "denominator" for g in ev.gaps)

    # ④ 计划 + 显式决策（两个角色的替代方案都经用户选择）+ 执行（local 零获取）
    plan_out = await registry.dispatch(
        "plan_task_supply",
        {"place": "成都", "roles_csv": "subject,boundary",
         "choices_json": '{"subject": "keep_gap", "boundary": "keep_gap"}'},
        session_id=sid)
    plan = plan_out["plan"]
    assert plan["state"] in ("ready", "requires_confirmation")
    run = await registry.dispatch(
        "execute_supply_plan",
        {"plan_id": plan["plan_id"], "approved": True},
        session_id=sid)
    assert run["success"] is True
    outcomes = {r["role"]: r["outcome"] for r in run["results"]}
    assert outcomes["subject"] == "completed"
    assert run["plan"]["steps"][0]["receipt_ref"].startswith("ref:cd-")


# ── 旅程 2：两时相土地利用（nodata + 期次口径） ──────────────────────────

@pytest.mark.asyncio
async def test_journey_two_temporal_landuse(registry):

    from app.services.task_data_supply.coverage import (
        CoverageCandidate, evaluate_task_coverage)
    from app.services.task_data_supply.requirement import (
        TaskScope, task_requirement)

    req = task_requirement(
        TaskScope(bbox=[104.0, 30.0, 105.0, 31.0],
                  time_range=["2024-01-01", "2024-12-31"]),
        [
            {"role": "target_time", "geometry_kind": "raster",
             "consumption_methods": ["temporal_comparison",
                                     "numeric_stats"],
             "resolution_meters": 30},
            {"role": "baseline", "geometry_kind": "raster",
             "consumption_methods": ["temporal_comparison"]},
        ],
        provenance=[{"source": "journey"}])
    cands = [
        CoverageCandidate(
            candidate_id="landuse/2024", channel="data_fabric_catalog",
            serves_roles=("target_time",), declared_bbox=[
                104.0, 30.0, 105.0, 31.0],
            temporal_coverage=["2024-01-01", "2024-06-30"],
            geometry_kind="raster", role_class="numeric_raster",
            resolution_meters=30, nodata_fraction=0.4,
            footprint_geometry={"type": "Polygon", "coordinates": [[
                [104.0, 30.0], [105.0, 30.0], [105.0, 31.0],
                [104.0, 31.0], [104.0, 30.0]]]}),
        CoverageCandidate(
            candidate_id="landuse/2010", channel="data_fabric_catalog",
            serves_roles=("baseline",), declared_bbox=[
                104.0, 30.0, 105.0, 31.0],
            temporal_coverage=["2010-01-01", "2010-12-31"],
            geometry_kind="raster", role_class="numeric_raster",
            resolution_meters=100),
    ]
    matrix = evaluate_task_coverage(req, cands)
    by_role = {r.role: r.evaluations[0] for r in matrix.roles if r.evaluations}
    # nodata 40% → 有效覆盖 0.6 → partial（不虚报全覆盖）
    tgt = by_role["target_time"]
    assert tgt.verdict == "partial"
    assert tgt.spatial_ratio is not None
    assert abs(tgt.spatial_ratio - 0.6) < 0.01
    # baseline 2010 与任务窗 2024 不相交 → 期次不匹配（比较口径缺一期）
    base = by_role["baseline"]
    assert base.temporal_verdict == "none"
    assert any(g.get("code") == "COVERAGE_TEMPORAL_DISJOINT"
               for g in base.gaps)
    # 基线分辨率 100m 粗于任务 target 需求 30m → 粒度缺口（对 baseline 无
    # resolution 需求时不误报——only target demand has resolution_meters）
    assert not any(g["dimension"] == "granularity" for g in base.gaps)


# ── 旅程 3：公共服务可达性（facility+network，展示级底图） ────────────────

@pytest.mark.asyncio
async def test_journey_accessibility(registry):
    sid = "j3-access"
    await _seed(sid, id="ref:facilities", role="observation",
                bbox=[104.0, 30.6, 104.4, 30.9], count=300)
    await _seed(sid, id="ref:roads", role="constraint", type="road_network",
                bbox=[103.9, 30.5, 104.6, 31.0])

    cov = await registry.dispatch(
        "compare_task_coverage",
        {"place": "成都", "roles_csv": "subject"},
        session_id=sid)
    assert cov["success"] is True
    # roads（logical_role=constraint）不服务 subject → 无冒充评估行
    subj = cov["coverage"]["roles"][0]
    cand_ids = {e["candidate_id"] for e in subj["evaluations"]}
    assert "ref:facilities" in cand_ids
    assert "ref:roads" not in cand_ids

    # 展示级底图（fixture：WMS-only 源）× numeric_stats → 不得入数值资格
    from app.services.task_data_supply.coverage import (
        CoverageCandidate, evaluate_task_coverage)
    from app.services.task_data_supply.requirement import (
        TaskScope, task_requirement)

    req = task_requirement(
        TaskScope(place="成都"),
        [{"role": "reference", "consumption_methods": ["map_display",
                                                       "numeric_stats"]}],
        provenance=[{"source": "journey"}])
    basemap = CoverageCandidate(
        candidate_id="wms/basemap", channel="provider_service",
        serves_roles=("reference",), declared_bbox=[103.9, 30.5, 104.6, 31.0],
        role_class="display_only")
    matrix = evaluate_task_coverage(req, [basemap])
    ev = matrix.roles[0].evaluations[0]
    assert ev.fit_for_methods.get("map_display") is True
    assert ev.fit_for_methods.get("numeric_stats") is False
    assert ev.numeric_stats_qualified is False
    assert any("展示" in d for d in ev.disclosures)

    # 网络分辨率粗于可达性需求 → 粒度缺口
    req2 = task_requirement(
        TaskScope(place="成都"),
        [{"role": "network", "consumption_methods": ["accessibility"],
          "resolution_meters": 10}],
        provenance=[{"source": "journey"}])
    coarse_net = CoverageCandidate(
        candidate_id="osm/roads-coarse", channel="data_fabric_catalog",
        serves_roles=("network",), declared_bbox=[103.9, 30.5, 104.6, 31.0],
        geometry_kind="network", role_class="vector_features",
        resolution_meters=50)
    matrix2 = evaluate_task_coverage(req2, [coarse_net])
    ev2 = matrix2.roles[0].evaluations[0]
    assert any(g["dimension"] == "granularity" for g in ev2.gaps)


# ── 旅程 4：官方统计表 + 行政边界（版本口径 + 边界空洞） ─────────────────

@pytest.mark.asyncio
async def test_journey_stats_with_boundary_version(registry):

    from app.services.task_data_supply.coverage import (
        CoverageCandidate, evaluate_task_coverage)
    from app.services.task_data_supply.requirement import (
        TaskScope, task_requirement)

    req = task_requirement(
        TaskScope(bbox=[104.0, 30.0, 105.0, 31.0],
                  time_range=["2024-01-01", "2024-12-31"]),
        [
            {"role": "boundary", "geometry_kind": "polygon",
             "consumption_methods": ["geometry_skeleton"],
             "admin_level": "district"},
            {"role": "measure", "consumption_methods": ["numeric_stats"],
             "required_fields": ("population_2024",)},
        ],
        provenance=[{"source": "journey"}])
    ring = [[104.0, 30.0], [105.0, 30.0], [105.0, 31.0], [104.0, 31.0],
            [104.0, 30.0]]
    hole = [[104.4, 30.4], [104.8, 30.4], [104.8, 30.8], [104.4, 30.8],
            [104.4, 30.4]]  # 缺一个区（手算 16% 洞）
    boundary = CoverageCandidate(
        candidate_id="admin/2010-bounds", channel="local",
        serves_roles=("boundary",), declared_bbox=[104.0, 30.0, 105.0, 31.0],
        temporal_coverage=["2010-01-01", "2010-12-31"],
        version="2010", geometry_kind="polygon",
        role_class="vector_features",
        footprint_geometry={"type": "Polygon",
                            "coordinates": [ring, hole]})
    stats = CoverageCandidate(
        candidate_id="yearbook/2024", channel="local",
        serves_roles=("measure",), declared_bbox=[104.0, 30.0, 105.0, 31.0],
        temporal_coverage=["2024-01-01", "2024-12-31"],
        fields=("population_2024", "gdp"),
        geometry_kind="table", role_class="vector_features")
    matrix = evaluate_task_coverage(req, [boundary, stats])
    by_role = {r.role: r.evaluations[0] for r in matrix.roles if r.evaluations}
    # 边界 2010 版本 vs 任务 2024 口径 → temporal 缺口（换边界年须显式确认）
    bnd = by_role["boundary"]
    assert bnd.temporal_verdict == "none"
    assert any(g.get("code") == "COVERAGE_TEMPORAL_DISJOINT" for g in bnd.gaps)
    # 实际 footprint 有洞（手算 0.84 覆盖比）→ partial，绝不按 bbox 宣称全覆盖
    assert bnd.verdict == "partial"
    assert bnd.spatial_ratio is not None
    assert abs(bnd.spatial_ratio - 0.84) < 0.01
    # 统计表字段齐 → 无属性缺口
    assert not any(g["dimension"] == "attribute"
                   for g in by_role["measure"].gaps)


# ── 旅程 5：诚实分流（空/失败/跨租户/取消） ──────────────────────────────

@pytest.mark.asyncio
async def test_journey_honest_routing(registry):
    # ① 空会话：not_searched（未知 ≠ 不存在），unknown_reasons 可解释
    empty = await registry.dispatch(
        "compare_task_coverage",
        {"place": "成都", "roles_csv": "subject"},
        session_id="j5-empty")
    assert empty["success"] is True
    role0 = empty["coverage"]["roles"][0]
    assert not role0["evaluations"]
    assert role0["unknown_reasons"]
    assert empty["missing_obligations"] and \
        empty["missing_obligations"][0]["state"] == "not_searched"

    # ② 跨会话计划不可见
    sid = "j5-owner"
    await _seed(sid, id="ref:x", role="observation",
                bbox=[104.0, 30.0, 105.0, 31.0])
    built = await registry.dispatch(
        "plan_task_supply",
        {"place": "成都", "roles_csv": "subject"}, session_id=sid)
    pid = built["plan"]["plan_id"]
    other = await registry.dispatch(
        "get_supply_plan_status", {"plan_id": pid}, session_id="j5-intruder")
    assert other["success"] is False
    assert other["code"] == "TASK_SUPPLY_PLAN_NOT_FOUND"

    # ③ 取消保留 partial 产物（record_cancel 语义；服务失败路径）
    from app.services.task_data_supply.supply_plan import (
        SupplyPlanner, build_supply_plan)
    from app.services.task_data_supply.coverage import (
        CoverageCandidate, evaluate_task_coverage)
    from app.services.task_data_supply.requirement import (
        TaskScope, task_requirement)

    req = task_requirement(
        TaskScope(bbox=[104.0, 30.0, 105.0, 31.0]),
        [{"role": "subject", "consumption_methods": ["numeric_stats"]}],
        provenance=[{"source": "journey"}])
    cands = [CoverageCandidate(
        candidate_id="src/a", channel="data_fabric_catalog",
        serves_roles=("subject",), declared_bbox=[104.0, 30.0, 105.0, 31.0],
        role_class="vector_features")]
    plan = build_supply_plan(req, evaluate_task_coverage(req, cands), cands)
    planner = SupplyPlanner()
    planner.record_outcome(plan, "subject", outcome="partial",
                           receipt_ref="ref:fabric-parquet/partial-1",
                           rows_written=400, disclosure="南郊瓦片超时")
    planner.record_cancel(plan, reason="用户取消")
    assert plan.state == "cancelled"
    step = plan.step("subject")
    assert step.outcome == "partial"
    assert step.receipt_ref == "ref:fabric-parquet/partial-1"
    assert any("超时" in d for d in step.disclosures)
    assert any("取消" in d for d in step.disclosures)
