"""Anti-Claim / Workflow-Contract Corpus（Goal C / C10 + C4/C6/C7 契约层）。

两个互补的负例/契约层：

1. **Anti-claim plan-tier 案例**（NL 级）：自然语言查询在无数据画像时，
   plan 的 methodology_warnings 必须携带稳定机器可读警告码 ——
   「没有分母不得称公平」「视觉热力不得当显著性」「选址不得静默编准则」。
2. **Workflow-contract 案例**（编译级）：显式 recipe + 数据画像 →
   12 阶段编译 + workflow 契约评估 + verdict V2 —— 断言义务状态、
   语义降级回退、阻断项与最终产品裁决（BLOCKED_BY_METHOD / DATA）。

全部离线、确定、零 LLM；失败语义 = 产品语义回归。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from app.evaluation.case import GISBenchmarkCase

# ── 1. Anti-claim plan-tier 案例（NL 级反声明）──────────────────────────


def build_anti_claim_plan_cases() -> List[GISBenchmarkCase]:
    """无数据画像的规划期反声明契约（稳定警告码必须随 plan 下行）。"""
    return [
        GISBenchmarkCase(
            id="AC-denominator-equity", name="无分母不得称公平",
            group="anti-claim", query="成都各区小学数量是否均衡",
            description="spatial_equity 规划期必须披露分母缺失（禁止静默公平性结论）",
            plan_only=True, expected_task="spatial_equity",
            expected_warning_codes=["EQUITY_MISSING_DENOMINATOR"],
        ),
        GISBenchmarkCase(
            id="AC-denominator-percapita", name="人均语义必须先有分母",
            group="anti-claim", query="各区医院人均拥有量是否达标",
            description="「人均」查询在无分母事实时必须带公平/分母披露",
            plan_only=True, expected_task="spatial_equity",
            expected_warning_codes=["EQUITY_MISSING_DENOMINATOR"],
        ),
        GISBenchmarkCase(
            id="AC-criteria-site-selection", name="选址不得静默编造准则",
            group="anti-claim", query="成都新学校选址推荐",
            description="选址规划期必须披露准则未声明（禁止静默编造权重）",
            plan_only=True, expected_task="site_selection",
            expected_warning_codes=["SITE_SELECTION_CRITERIA_UNDECLARED"],
        ),
        GISBenchmarkCase(
            id="AC-weights-suitability", name="适宜性权重来源必须披露",
            group="anti-claim", query="成都适建区适宜性评价",
            description="适宜性规划期必须披露权重来源义务",
            plan_only=True, expected_task="suitability_assessment",
            expected_warning_codes=["SUITABILITY_WEIGHT_PROVENANCE"],
        ),
        GISBenchmarkCase(
            id="AC-receptors-risk", name="缺受体不得称风险",
            group="anti-claim", query="成都洪水风险区域分析",
            description="risk 规划期必须披露受体未确认（hazard ≠ risk）",
            plan_only=True, expected_task="risk_exposure",
            expected_warning_codes=["RISK_RECEPTORS_UNCONFIRMED"],
        ),
        GISBenchmarkCase(
            id="AC-visual-not-significance", name="视觉热力不得当显著性",
            group="anti-claim", query="成都餐饮店的疏密态势",
            description="纯视觉密度查询不得触发热点显著性/公平性噪声警告",
            plan_only=True, expected_task="distribution_overview",
            forbidden_warning_codes=[
                "HOTSPOT_SCREENING_NOT_SIGNIFICANCE",
                "EQUITY_MISSING_DENOMINATOR",
                "SITE_SELECTION_CRITERIA_UNDECLARED",
            ],
        ),
        GISBenchmarkCase(
            id="AC-no-noise-simple-view", name="简单查看无方法论噪声",
            group="anti-claim", query="show me the cafes in Chengdu",
            description="轻量查看不得携带任何决策族义务噪声",
            plan_only=True, expected_task="simple_view",
            forbidden_warning_codes=[
                "EQUITY_MISSING_DENOMINATOR",
                "RISK_RECEPTORS_UNCONFIRMED",
                "SUITABILITY_WEIGHT_PROVENANCE",
            ],
        ),
    ]


# ── 2. Workflow-contract 案例（编译级义务/回退/裁决）────────────────────

@dataclass(frozen=True)
class WorkflowContractCase:
    """编译级契约案例：显式 recipe + profile → 编译产物断言（C4/C6/C7）。"""
    case_id: str
    recipe_id: str
    query: str
    profile: Optional[Dict[str, Any]] = None
    expect_roles: Dict[str, str] = field(default_factory=dict)       # role → status
    expect_obligation_status: Dict[str, str] = field(default_factory=dict)  # id → status
    expect_method_blockers: Tuple[str, ...] = ()
    expect_data_blockers: Tuple[str, ...] = ()
    expect_warning_codes: Tuple[str, ...] = ()
    expect_fallback_codes: Tuple[str, ...] = ()
    forbid_warning_codes: Tuple[str, ...] = ()
    expect_verdict: Optional[str] = None  # 完成管线 verdict（合成 complete 面）


_PROFILE_EMPTY = {"featureCount": 0, "geometryTypes": [], "fields": {}}
_PROFILE_POINTS = {"featureCount": 60, "geometryTypes": ["Point"],
                   "fields": {"name": {"type": "string"}}}
_PROFILE_NUMERIC_FEW = {"featureCount": 5, "geometryTypes": ["Point"],
                        "fields": {"value": {"type": "number"}},
                        "numericFields": ["value"], "numericSampleCount": 5,
                        "crs": "EPSG:4326"}
_PROFILE_NUMERIC_OK = {"featureCount": 120, "geometryTypes": ["Point"],
                       "fields": {"value": {"type": "number"}},
                       "numericFields": ["value"], "numericSampleCount": 120,
                       "crs": "EPSG:32648"}
_PROFILE_DENOMINATOR = {"featureCount": 60, "geometryTypes": ["Point"],
                        "fields": {"population": {"type": "number"},
                                   "name": {"type": "string"}}}


def build_workflow_contract_cases() -> List[WorkflowContractCase]:
    """代表性专业工作流的契约面案例（义务联动/降级/阻断/裁决）。"""
    return [
        # ── 公平性：分母义务（degrade_with_disclosure，不阻断产品）──────
        WorkflowContractCase(
            "WC-equity-no-denominator", "education_equity_per_capita",
            "成都各区教育设施人均公平性", profile=_PROFILE_POINTS,
            expect_roles={"subject": "bound", "boundary": "bound",
                          "denominator": "external"},
            expect_obligation_status={"equity_denominator_required": "warning"},
            expect_warning_codes=["EQUITY_MISSING_DENOMINATOR"],
            expect_verdict="READY_WITH_WARNINGS",
        ),
        WorkflowContractCase(
            "WC-equity-with-denominator", "education_equity_per_capita",
            "成都各区教育设施人均公平性", profile=_PROFILE_DENOMINATOR,
            expect_roles={"denominator": "bound"},
            expect_obligation_status={"equity_denominator_required": "satisfied"},
            forbid_warning_codes=["EQUITY_MISSING_DENOMINATOR"],
        ),
        # ── 克里金：数值字段缺失 → block_method；角度 CRS → 降级声明 ──
        WorkflowContractCase(
            "WC-kriging-blocked-no-field", "kriging_interpolation_workflow",
            "用克里金插值生成浓度表面", profile=_PROFILE_POINTS,
            expect_obligation_status={"kriging_numeric_field": "blocked"},
            expect_method_blockers=("kriging_numeric_field",),
            expect_verdict="BLOCKED_BY_METHOD",
        ),
        WorkflowContractCase(
            "WC-kriging-angle-crs", "kriging_interpolation_workflow",
            "用克里金插值生成浓度表面", profile=_PROFILE_NUMERIC_FEW,
            # R1-A9：block_method 义务遇到 REQUIRES_TRANSFORM（角度坐标需先
            # 投影）升级为 blocked —— 变换前方法不成立，声明的阻断可达。
            expect_obligation_status={"kriging_projected_crs": "blocked"},
            expect_method_blockers=("kriging_projected_crs",),
            expect_warning_codes=["KRIGING_PROJECTED_CRS_REQUIRED"],
            expect_verdict="BLOCKED_BY_METHOD",
        ),
        WorkflowContractCase(
            "WC-kriging-ok", "kriging_interpolation_workflow",
            "用克里金插值生成浓度表面", profile=_PROFILE_NUMERIC_OK,
            forbid_warning_codes=[
                "KRIGING_NUMERIC_FIELD_REQUIRED",
                "KRIGING_PROJECTED_CRS_REQUIRED",
                "KRIGING_INSUFFICIENT_SAMPLES",
            ],
        ),
        # ── 趋势：观测期数不足 → 降级声明（两期不得称趋势）─────────────
        WorkflowContractCase(
            "WC-trend-short-series", "linear_trend_analysis",
            "站点观测变化趋势", profile={
                "featureCount": 30, "geometryTypes": ["Point"],
                "fields": {"value": {"type": "number"}, "date": {"type": "string"}},
                "hasTimeField": True, "temporalObservationCount": 2,
            },
            expect_obligation_status={"trend_min_observations": "warning"},
            expect_warning_codes=["TREND_INSUFFICIENT_OBSERVATIONS"],
        ),
        WorkflowContractCase(
            "WC-trend-sufficient", "linear_trend_analysis",
            "站点观测变化趋势", profile={
                "featureCount": 30, "geometryTypes": ["Point"],
                "fields": {"value": {"type": "number"}, "date": {"type": "string"}},
                "hasTimeField": True, "temporalObservationCount": 12,
            },
            expect_obligation_status={"trend_min_observations": "satisfied"},
            forbid_warning_codes=["TREND_INSUFFICIENT_OBSERVATIONS"],
        ),
        # ── SAR：定标证据义务（planned 前置的诚实呈现）──────────────────
        WorkflowContractCase(
            "WC-sar-calibration-disclosure", "sar_calibrated_comparison",
            "SAR 定标对比分析", profile=_PROFILE_POINTS,
            expect_warning_codes=["SAR_CALIBRATION_EVIDENCE_REQUIRED"],
        ),
        # ── 地形：米制 CRS 义务（角度坐标降级 + 精度披露）───────────────
        WorkflowContractCase(
            "WC-slope-angle-crs", "slope_analysis_workflow",
            "成都周边的坡度分析", profile={
                "featureCount": 1, "geometryTypes": [], "fields": {},
                "crs": "EPSG:4326",
            },
            expect_obligation_status={"terrain_crs_metric": "degraded"},
            expect_warning_codes=["TERRAIN_METRIC_CRS_REQUIRED"],
        ),
        # ── 风险：缺受体降级为 hazard 图（hazard ≠ risk）───────────────
        WorkflowContractCase(
            "WC-risk-hazard-only", "hazard_exposure_overlay",
            "危险源影响范围叠加暴露评价", profile=_PROFILE_POINTS,
            expect_warning_codes=["RISK_RECEPTORS_UNCONFIRMED"],
        ),
        # ── 选址：准则未声明 → 约束筛选降级（不排序）───────────────────
        WorkflowContractCase(
            "WC-site-criteria-undeclared", "generic_mcda_site_ranking",
            "通用多准则选址排序", profile=_PROFILE_POINTS,
            expect_warning_codes=["SITE_SELECTION_CRITERIA_UNDECLARED"],
        ),
    ]


@dataclass
class ContractCaseResult:
    case_id: str
    passed: bool
    failures: List[str] = field(default_factory=list)


def run_workflow_contract_case(case: WorkflowContractCase) -> ContractCaseResult:
    """确定性执行一个 workflow 契约案例（编译 + 契约评估 + verdict V2）。"""
    from app.services.gis_harness.workflow_compiler import compile_workflow
    from app.services.gis_harness.completion.contracts import (
        MapCompletionResult,
        derive_product_verdict,
    )

    failures: List[str] = []
    compilation = compile_workflow(
        case.query, recipe_id=case.recipe_id, profile=case.profile,
    )
    if len(compilation.stages) != 12:
        failures.append(f"stages: expected 12, got {len(compilation.stages)}")

    plan = compilation.plan
    wc = plan.get("workflow_contract") or {}
    warnings = plan.get("methodology_warnings") or []
    got_codes = set()
    for w in warnings:
        for code in (w.get("warning_codes") or []):
            got_codes.add(str(code))
        if w.get("code"):
            got_codes.add(str(w.get("code")))

    roles = {r.get("role"): r.get("status") for r in wc.get("roles") or []}
    for role_name, expected_status in case.expect_roles.items():
        got = roles.get(role_name)
        if got != expected_status:
            failures.append(
                f"role {role_name}: expected {expected_status}, got {got}"
            )

    obligations = {o.get("obligation_id"): o.get("status")
                   for o in wc.get("obligations") or []}
    for obl_id, expected_status in case.expect_obligation_status.items():
        got = obligations.get(obl_id)
        if got != expected_status:
            failures.append(
                f"obligation {obl_id}: expected {expected_status}, got {got}"
            )

    method_blockers = set(wc.get("method_blockers") or [])
    missing_mb = set(case.expect_method_blockers) - method_blockers
    if missing_mb:
        failures.append(f"method blockers missing: {sorted(missing_mb)}")
    data_blockers = set(wc.get("data_blockers") or [])
    missing_db = set(case.expect_data_blockers) - data_blockers
    if missing_db:
        failures.append(f"data blockers missing: {sorted(missing_db)}")

    missing_codes = set(case.expect_warning_codes) - got_codes
    if missing_codes:
        failures.append(f"warning codes missing: {sorted(missing_codes)}")
    noise_codes = set(case.forbid_warning_codes) & got_codes
    if noise_codes:
        failures.append(f"forbidden warning codes present: {sorted(noise_codes)}")

    fallback_codes = {
        f.get("reason_code") for f in plan.get("fallbacks") or []
    }
    missing_fb = set(case.expect_fallback_codes) - fallback_codes
    if missing_fb:
        failures.append(f"fallback codes missing: {sorted(missing_fb)}")

    if case.expect_verdict is not None:
        # 合成「渲染完美」的完成面：verdict 的降档只允许来自 workflow 契约
        # —— 正是「漂亮地图掩盖不了方法不成立」的可断言形态。
        synthetic = MapCompletionResult(
            status="complete",
            layer_status="valid",
            component_status="valid",
            render_status="not_applicable",
        )
        verdict = derive_product_verdict(synthetic, warnings, chapter=plan)
        if verdict["verdict"] != case.expect_verdict:
            failures.append(
                f"verdict: expected {case.expect_verdict}, "
                f"got {verdict['verdict']} ({verdict['reasons']})"
            )

    return ContractCaseResult(
        case_id=case.case_id, passed=not failures, failures=failures,
    )


def build_v2_recipe_coverage_sweep() -> List[str]:
    """全部 V2 recipe 的编译烟测清单（registry 覆盖：147 个都要能编译）。"""
    from app.services.gis_harness.recipe_packs import all_pack_recipes

    return sorted(r.id for r in all_pack_recipes() if r.workflow is not None)
