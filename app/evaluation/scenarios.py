"""GIS Scenario Benchmarks（Goal C / C11）—— 代表性端到端确定性场景。

每个场景 = 一个专业 GIS 任务族的全链路离线走查：
plan-tier 语义案例（NL → recipe → capability 契约）+ workflow 契约案例
（义务/降级/裁决）+ execute-tier 脚本（native capability 的真实工具分派，
fixture 数据，零 LLM / 零网络）。

场景是「专业工作流知识库」的验收面：7 个场景覆盖规格 §16 的代表性
任务族；全部确定性、可离线重放。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List

from app.evaluation.anti_claim import (
    ContractCaseResult,
    WorkflowContractCase,
    run_workflow_contract_case,
)
from app.evaluation.case import GISBenchmarkCase, NumericAssertion, ScriptStep
from app.evaluation.runner import CaseResult, GISBenchmarkRunner


@dataclass
class GISScenario:
    scenario_id: str
    title: str
    description: str
    plan_cases: List[GISBenchmarkCase] = field(default_factory=list)
    execute_cases: List[GISBenchmarkCase] = field(default_factory=list)
    contract_cases: List[WorkflowContractCase] = field(default_factory=list)


@dataclass
class ScenarioResult:
    scenario_id: str
    passed: bool
    case_results: List[CaseResult] = field(default_factory=list)
    contract_results: List[ContractCaseResult] = field(default_factory=list)
    failures: List[str] = field(default_factory=list)


def _execute_case(**kwargs) -> GISBenchmarkCase:
    kwargs.setdefault("plan_only", False)
    return GISBenchmarkCase(**kwargs)


def build_scenarios() -> List[GISScenario]:
    """规格 §16 的代表性场景（确定性、离线）。"""
    from app.evaluation.anti_claim import build_workflow_contract_cases

    contract_by_id = {c.case_id: c for c in build_workflow_contract_cases()}

    scenarios: List[GISScenario] = []

    # ── S1 成都学校分布（全链路分布产品：intent→POI→聚合→产品→完成）──
    scenarios.append(GISScenario(
        scenario_id="S-schools-distribution",
        title="成都学校分布：intent → POI → 行政聚合 → 产品 → 完成",
        description="分布产品族全链路：真实 fixture 数据 + webgis_map_product 组装 + 数值断言。",
        plan_cases=[GISBenchmarkCase(
            id="SC-sd-plan", name="学校分布计划契约",
            group="conformance-distribution", query="分析成都各区小学的空间分布情况",
            plan_only=True, expected_task="distribution_overview",
            expected_capabilities=["poi_query", "admin_aggregation"],
            expected_recipe="poi_distribution_overview",
        )],
        execute_cases=[_execute_case(
            id="SC-sd-execute", name="学校分布产品组装",
            group="poi", query="分析成都各区小学的空间分布情况",
            expected_capabilities=["poi_query", "admin_aggregation"],
            fixture_aliases=["chengdu_schools", "admin_boundaries_chengdu"],
            script=[ScriptStep(tool="webgis_map_product", args={
                "primary_ref": "fixture:chengdu_schools",
                "query": "分析成都各区小学的空间分布情况",
            })],
            component_assertions=["title", "north_arrow", "scale_bar", "attribution"],
            numeric_assertions=[NumericAssertion(
                source="fixture", path="chengdu_schools.features",
                agg="len", op="==", value=60, label="fixture size")],
        )],
    ))

    # ── S2 成都学校公平性（分母披露 → 诚实降档）────────────────────────
    scenarios.append(GISScenario(
        scenario_id="S-schools-equity",
        title="成都学校公平性：无分母时诚实降档",
        description="公平性工作流的反声明链：规划期披露 + 契约层 READY_WITH_WARNINGS。",
        plan_cases=[GISBenchmarkCase(
            id="SC-se-plan", name="公平性计划披露",
            group="conformance-equity", query="成都各区小学数量是否均衡",
            plan_only=True, expected_task="spatial_equity",
            expected_warning_codes=["EQUITY_MISSING_DENOMINATOR"],
        )],
        contract_cases=[contract_by_id["WC-equity-no-denominator"]],
    ))

    # ── S3 插值（站点→字段→表面：样本不足诚实降级）────────────────────
    scenarios.append(GISScenario(
        scenario_id="S-interpolation",
        title="PM2.5 站点插值：样本守卫 + 克里金义务",
        description="插值产品族：点数据 fixture 组装 + 克里金工作流义务联动。",
        plan_cases=[GISBenchmarkCase(
            id="SC-in-plan", name="插值计划契约",
            group="conformance-interpolation", query="用克里金插值生成 PM2.5 浓度表面",
            plan_only=True, expected_task="raster_distribution",
            expected_capabilities=["spatial_interpolation"],
            expected_recipe="raster_distribution",
        )],
        execute_cases=[_execute_case(
            id="SC-in-execute", name="站点数据产品组装",
            group="interpolation", query="成都 PM2.5 监测站插值分析",
            expected_capabilities=["spatial_interpolation"],
            optional_capabilities=["poi_query", "point_profile"],
            fixture_aliases=["pm25_stations"],
            script=[ScriptStep(tool="webgis_map_product", args={
                "primary_ref": "fixture:pm25_stations",
                "query": "成都 PM2.5 监测站插值分析",
            })],
        )],
        contract_cases=[
            contract_by_id["WC-kriging-blocked-no-field"],
            contract_by_id["WC-kriging-angle-crs"],
            contract_by_id["WC-kriging-ok"],
        ],
    ))

    # ── S4 选址（准则义务 → 不静默编造）────────────────────────────────
    scenarios.append(GISScenario(
        scenario_id="S-site-selection",
        title="设施选址：准则未声明不排序",
        description="MCDA 选址链：准则/权重来源义务 + 约束筛选降级语义。",
        plan_cases=[GISBenchmarkCase(
            id="SC-ss-plan", name="选址计划义务",
            group="conformance-decision", query="成都新学校选址推荐",
            plan_only=True, expected_task="site_selection",
            expected_capabilities=["mcda_evaluation"],
            expected_warning_codes=["SITE_SELECTION_CRITERIA_UNDECLARED"],
        )],
        contract_cases=[contract_by_id["WC-site-criteria-undeclared"]],
    ))

    # ── S5 风险（hazard ≠ risk：受体义务）─────────────────────────────
    scenarios.append(GISScenario(
        scenario_id="S-risk-exposure",
        title="洪水风险：hazard 与 risk 语义分界",
        description="风险链：受体未确认时产品必须自称 hazard，不得称 risk。",
        plan_cases=[GISBenchmarkCase(
            id="SC-rk-plan", name="风险计划义务",
            group="conformance-decision", query="成都洪水风险区域分析",
            plan_only=True, expected_task="risk_exposure",
            expected_warning_codes=["RISK_RECEPTORS_UNCONFIRMED"],
        )],
        contract_cases=[contract_by_id["WC-risk-hazard-only"]],
    ))

    # ── SAR 变化（planned 前置的诚实呈现 + 定标证据义务）───────────────
    scenarios.append(GISScenario(
        scenario_id="S-sar-change",
        title="SAR 变化检测：planned 能力诚实呈现 + 定标义务",
        description="SAR 链：speckle/定标 planned 前置不可用时禁止伪定量结论。",
        plan_cases=[GISBenchmarkCase(
            id="SC-sar-plan", name="SAR 计划契约",
            group="conformance-sar", query="双时相 SAR 变化检测分析",
            plan_only=True, expected_task="sar_analysis",
            expected_capabilities=["sar_analysis"],
        )],
        contract_cases=[contract_by_id["WC-sar-calibration-disclosure"]],
    ))

    # ── 地形水文（DEM 衍生链：CRS/填洼义务）────────────────────────────
    scenarios.append(GISScenario(
        scenario_id="S-terrain-hydrology",
        title="坡度/流域：DEM 衍生义务链",
        description="地形水文链：米制 CRS 与 DEM 填洼义务的规划期披露。",
        plan_cases=[GISBenchmarkCase(
            id="SC-th-plan", name="坡度计划契约",
            group="conformance-terrain", query="成都周边的坡度分析",
            plan_only=True, expected_task="terrain_analysis",
            expected_capabilities=["terrain_slope"],
            expected_recipe="slope_analysis_workflow",
        ), GISBenchmarkCase(
            id="SC-th-plan-hydro", name="流域计划契约",
            group="conformance-hydrology", query="成都周边流域划分",
            plan_only=True, expected_task="watershed_analysis",
            expected_capabilities=["terrain_hydrology"],
            expected_recipe="watershed_delineation_workflow",
        )],
        contract_cases=[contract_by_id["WC-slope-angle-crs"]],
    ))

    return scenarios


async def run_scenario(scenario: GISScenario) -> ScenarioResult:
    """确定性执行一个场景（plan/execute/contract 三层全部通过才算过）。"""
    runner = GISBenchmarkRunner()
    result = ScenarioResult(scenario_id=scenario.scenario_id, passed=True)

    all_cases = [*scenario.plan_cases, *scenario.execute_cases]
    if all_cases:
        results = await runner.run(all_cases)
        result.case_results = list(results)
        for r in results:
            if not r.passed:
                result.passed = False
                result.failures.append(f"{r.case_id}: {r.failures}")

    for case in scenario.contract_cases:
        r = run_workflow_contract_case(case)
        result.contract_results.append(r)
        if not r.passed:
            result.passed = False
            result.failures.append(f"{r.case_id}: {r.failures}")

    return result


async def run_all_scenarios() -> List[ScenarioResult]:
    return [await run_scenario(s) for s in build_scenarios()]
